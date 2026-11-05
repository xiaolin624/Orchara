// Package diagnostics owns F11/T073's user-scoped diagnostic grants.
//
// A grant is the only door through which an administrator may read another
// user's run content. It names, at creation time:
//
//   - the run owner (the caller) and the administrator grantee (`grantee_id`);
//   - the run (`run_id`, pre-existing and not tombstoned);
//   - the explicit object snapshot (`object_scope`, `artifact:<uuid>` entries) the
//     owner selected via the documented non-contract `X-Diagnostic-Object-Scope`
//     header — an absent header derives the run's current objects once, never
//     "everything now and in the future";
//   - the purposes (`purposes`, a bounded token vocabulary), and the hard expiry
//     (`expires_at`).
//
// The grant table is diagnostic consent only: it is not training consent, and
// creating one writes exactly one grant row plus one audit row. The content door
// (content.go) re-checks every dimension on every read, in one transaction, and
// appends an audit.event row before any byte is served. An administrator without
// a matching live grant sees metadata only.
package diagnostics

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"regexp"
	"sort"
	"strings"
	"time"

	"orchara/platform/internal/audit"
	"orchara/platform/internal/objectstore"
	"orchara/platform/internal/uuid"
)

// Audit actions. They are the closed vocabulary of this package; a later ticket
// extends it only together with ADR 0028 and this report.
const (
	ActionGrantCreated = "diagnostic.grant_created"
	ActionGrantRevoked = "diagnostic.grant_revoked"
)

// ObjectArtifact is the only object kind in the T073 scope vocabulary. The
// canonical entry shape is "artifact:<uuid>".
const ObjectArtifact = "artifact"

// Relaxed states are derived from the stored timestamps, never stored themselves.
const (
	StateActive  = "active"
	StateExpired = "expired"
	StateRevoked = "revoked"
)

// Direction of a listed grant from the caller's point of view.
const (
	DirectionOwner   = "owner"
	DirectionGrantee = "grantee"
)

// Bounds keep the object snapshot and the purpose vocabulary typed and small
// enough to audit. They are documented non-contract limits (ADR 0028).
const (
	MaxPurposes = 8
	MaxObjects  = 100
)

var purposePattern = regexp.MustCompile(`^[a-z][a-z0-9_-]{0,63}$`)

// Errors the API maps one-to-one onto contract codes. They are sentinels so no
// handler inspects driver messages and no message ever carries content.
var (
	ErrValidation         = errors.New("validation failed")
	ErrNotFound           = errors.New("diagnostic grant does not exist")
	ErrGranteeNotFound    = errors.New("grantee does not exist")
	ErrGranteeDisabled    = errors.New("grantee is disabled")
	ErrGranteeNotAdmin    = errors.New("grantee is not an administrator")
	ErrCallerNotAdmin     = errors.New("the content reader no longer holds the administrator role")
	ErrRunNotGrantable    = errors.New("run is not grantable")
	ErrGrantRevoked       = errors.New("diagnostic grant was revoked")
	ErrGrantExpired       = errors.New("diagnostic grant has expired")
	ErrRunDeleted         = errors.New("the run has been deleted")
	ErrPurposeDenied      = errors.New("the purpose is not in the grant")
	ErrScopeDenied        = errors.New("the object is outside the grant scope")
	ErrContentUnavailable = errors.New("diagnostic content is unavailable")
	ErrContentNotReady    = errors.New("diagnostic content is not ready")
	ErrObjectUnavailable  = errors.New("the object store is not configured")
)

// ValidationError names the request field that failed semantic validation. The
// message never echoes the submitted value: an error must not carry request
// content (SECURITY/data-policy).
type ValidationError struct {
	Field   string
	Message string
}

func (e *ValidationError) Error() string { return e.Field + ": " + e.Message }

// Unwrap lets callers use errors.Is(err, ErrValidation).
func (e *ValidationError) Unwrap() error { return ErrValidation }

// AsValidation reports the typed field failure behind err.
func AsValidation(err error) (*ValidationError, bool) {
	var typed *ValidationError
	if errors.As(err, &typed) {
		return typed, true
	}
	return nil, false
}

// CreateParams is one grant request already parsed from HTTP.
type CreateParams struct {
	RunID       string
	GranteeID   string
	Purposes    []string
	ExpiresAt   time.Time
	ObjectScope []string
}

// Grant is the stored row projected for the API. It contains ids, purposes,
// object ids and timestamps only: never a content body.
type Grant struct {
	ID          string
	OwnerID     string
	GranteeID   string
	RunID       string
	Purposes    []string
	ObjectScope []string
	ExpiresAt   time.Time
	RevokedAt   *time.Time
	CreatedAt   time.Time
	State       string
	Direction   string
}

// Service owns the grant table. It depends on database/sql, the pure audit
// helper and the object store seam (bytes only; no other service).
type Service struct {
	DB *sql.DB
	// Objects opens the sealed bytes after every grant/predicate check passed. A
	// nil store fails closed with ErrObjectUnavailable; it never falls back.
	Objects *objectstore.Store
	// Clock is the request-validation clock. Read-time expiry is decided by the
	// database (`now()`), so a skewed process clock can never resurrect a grant.
	Clock func() time.Time
}

// New builds the service over one database handle and object store.
func New(db *sql.DB, objects *objectstore.Store) *Service {
	return &Service{DB: db, Objects: objects, Clock: time.Now}
}

func (s *Service) clock() time.Time {
	if s.Clock != nil {
		return s.Clock()
	}
	return time.Now()
}

// NormalizeCreate validates the request shape and returns the canonical params.
// It is called by the handler (for an early located 422) and again by Create
// inside the transaction, so a direct service caller cannot bypass it.
func NormalizeCreate(params CreateParams, now time.Time) (CreateParams, error) {
	params.RunID = strings.TrimSpace(params.RunID)
	params.GranteeID = strings.TrimSpace(params.GranteeID)
	if !uuid.Valid(params.RunID) {
		return params, &ValidationError{Field: "run_id", Message: "run_id must be a UUID"}
	}
	if !uuid.Valid(params.GranteeID) {
		return params, &ValidationError{Field: "grantee_id", Message: "grantee_id must be a UUID"}
	}
	purposes, err := normalizePurposes(params.Purposes)
	if err != nil {
		return params, err
	}
	params.Purposes = purposes
	if params.ExpiresAt.IsZero() || !params.ExpiresAt.After(now) {
		return params, &ValidationError{Field: "expires_at", Message: "expires_at must be in the future"}
	}
	params.ExpiresAt = params.ExpiresAt.UTC()
	scope, err := normalizeObjectScope(params.ObjectScope)
	if err != nil {
		return params, err
	}
	params.ObjectScope = scope
	return params, nil
}

func normalizePurposes(raw []string) ([]string, error) {
	if len(raw) == 0 {
		return nil, &ValidationError{Field: "purposes", Message: "at least one purpose is required"}
	}
	if len(raw) > MaxPurposes {
		return nil, &ValidationError{
			Field:   "purposes",
			Message: fmt.Sprintf("at most %d purposes are allowed", MaxPurposes),
		}
	}
	seen := make(map[string]bool, len(raw))
	out := make([]string, 0, len(raw))
	for _, value := range raw {
		purpose := strings.TrimSpace(value)
		if !purposePattern.MatchString(purpose) {
			return nil, &ValidationError{
				Field:   "purposes",
				Message: "each purpose must match [a-z][a-z0-9_-]{0,63}",
			}
		}
		if seen[purpose] {
			continue
		}
		seen[purpose] = true
		out = append(out, purpose)
	}
	return out, nil
}

func normalizeObjectScope(raw []string) ([]string, error) {
	if len(raw) == 0 {
		return nil, nil
	}
	if len(raw) > MaxObjects {
		return nil, &ValidationError{
			Field:   "object_scope",
			Message: fmt.Sprintf("at most %d objects are allowed", MaxObjects),
		}
	}
	seen := make(map[string]bool, len(raw))
	out := make([]string, 0, len(raw))
	for _, value := range raw {
		entry := strings.TrimSpace(value)
		if entry == "" {
			return nil, &ValidationError{Field: "object_scope", Message: "object_scope entries must not be empty"}
		}
		kind, objectID, ok := ParseObject(entry)
		if !ok {
			return nil, &ValidationError{
				Field:   "object_scope",
				Message: "each object_scope entry must be artifact:<uuid>",
			}
		}
		canonical := kind + ":" + objectID
		if seen[canonical] {
			continue
		}
		seen[canonical] = true
		out = append(out, canonical)
	}
	sort.Strings(out)
	return out, nil
}

// ParseObject parses and validates one object entry ("artifact:<uuid>").
func ParseObject(value string) (kind, objectID string, ok bool) {
	kind, objectID, found := strings.Cut(strings.TrimSpace(value), ":")
	if !found || kind != ObjectArtifact || !uuid.Valid(objectID) {
		return "", "", false
	}
	return kind, strings.ToLower(objectID), true
}

// Create writes one grant and its audit event in one transaction. Only the run
// owner may grant; the grantee must be a different, enabled human administrator.
func (s *Service) Create(ctx context.Context, ownerID string, params CreateParams) (*Grant, error) {
	caller := strings.TrimSpace(ownerID)
	if !uuid.Valid(caller) {
		return nil, ErrNotFound
	}
	normalized, err := NormalizeCreate(params, s.clock())
	if err != nil {
		return nil, err
	}

	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return nil, fmt.Errorf("starting the diagnostic grant transaction: %w", err)
	}
	defer func() { _ = tx.Rollback() }()

	var (
		runOwner   string
		runDeleted sql.NullTime
	)
	err = tx.QueryRowContext(ctx, `
		SELECT owner_id::text, deleted_at
		  FROM platform.runs
		 WHERE id = $1::uuid
		   FOR SHARE`, normalized.RunID).Scan(&runOwner, &runDeleted)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrNotFound
	}
	if err != nil {
		return nil, fmt.Errorf("looking up the run: %w", err)
	}
	if !strings.EqualFold(runOwner, caller) {
		// A foreign run is indistinguishable from a missing one: no enumeration.
		return nil, ErrNotFound
	}
	if runDeleted.Valid {
		return nil, ErrRunNotGrantable
	}
	if strings.EqualFold(normalized.GranteeID, caller) {
		return nil, &ValidationError{
			Field:   "grantee_id",
			Message: "the grantee must be a different administrator from the run owner",
		}
	}

	var (
		granteeDisabled sql.NullTime
		granteeIsAdmin  bool
	)
	err = tx.QueryRowContext(ctx, `
		SELECT p.disabled_at,
		       EXISTS (SELECT 1 FROM platform.role_bindings AS rb
		                WHERE rb.principal_id = p.id AND rb.role = 'admin')
		  FROM platform.principals AS p
		 WHERE p.id = $1::uuid`, normalized.GranteeID).
		Scan(&granteeDisabled, &granteeIsAdmin)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrGranteeNotFound
	}
	if err != nil {
		return nil, fmt.Errorf("looking up the grantee: %w", err)
	}
	if granteeDisabled.Valid {
		return nil, ErrGranteeDisabled
	}
	if !granteeIsAdmin {
		return nil, ErrGranteeNotAdmin
	}

	scope, err := s.resolveObjectScope(ctx, tx, normalized.RunID, normalized.ObjectScope)
	if err != nil {
		return nil, err
	}

	encodedScope, err := json.Marshal(scope)
	if err != nil {
		return nil, fmt.Errorf("encoding the diagnostic object scope: %w", err)
	}
	grant := &Grant{
		ID:          uuid.NewString(),
		OwnerID:     caller,
		GranteeID:   normalized.GranteeID,
		RunID:       normalized.RunID,
		Purposes:    normalized.Purposes,
		ObjectScope: scope,
		ExpiresAt:   normalized.ExpiresAt,
		State:       StateActive,
		Direction:   DirectionOwner,
	}
	err = tx.QueryRowContext(ctx, `
		INSERT INTO platform.diagnostic_grants
		       (id, owner_id, grantee_id, run_id, purposes, object_scope, expires_at, created_at)
		VALUES ($1::uuid, $2::uuid, $3::uuid, $4::uuid, $5::text[], $6::jsonb, $7, now())
		RETURNING created_at`,
		grant.ID, caller, normalized.GranteeID, normalized.RunID,
		normalized.Purposes, string(encodedScope), normalized.ExpiresAt,
	).Scan(&grant.CreatedAt)
	if err != nil {
		return nil, fmt.Errorf("creating the diagnostic grant: %w", err)
	}

	metadata := map[string]any{
		"grant_id":     grant.ID,
		"run_id":       grant.RunID,
		"grantee_id":   grant.GranteeID,
		"purposes":     grant.Purposes,
		"expires_at":   grant.ExpiresAt.UTC().Format(time.RFC3339),
		"object_count": len(grant.ObjectScope),
	}
	if err := audit.Record(
		ctx, tx, &caller, ActionGrantCreated, "diagnostic_grant", &grant.ID, metadata,
	); err != nil {
		return nil, err
	}
	if err := tx.Commit(); err != nil {
		return nil, fmt.Errorf("committing the diagnostic grant: %w", err)
	}
	return grant, nil
}

// resolveObjectScope turns the owner's selection into a stored snapshot.
//
// An explicit selection must name objects that really belong to this run and are
// not tombstoned; an absent selection is resolved once, now, from the run's real
// links. Either way the stored value is a fixed list of "artifact:<uuid>"
// entries: a later object is not silently in scope.
func (s *Service) resolveObjectScope(
	ctx context.Context, tx *sql.Tx, runID string, requested []string,
) ([]string, error) {
	if len(requested) == 0 {
		rows, err := tx.QueryContext(ctx, `
			SELECT DISTINCT a.id::text
			  FROM platform.artifacts AS a
			  JOIN (
			        SELECT ra.artifact_id FROM platform.run_artifacts AS ra
			         WHERE ra.run_id = $1::uuid
			        UNION
			        SELECT ru.artifact_id FROM platform.run_artifact_uploads AS ru
			         WHERE ru.run_id = $1::uuid
			       ) AS linked ON linked.artifact_id = a.id
			 WHERE a.deleted_at IS NULL
			 ORDER BY 1`, runID)
		if err != nil {
			return nil, fmt.Errorf("reading the run's diagnostic objects: %w", err)
		}
		defer func() { _ = rows.Close() }()
		scope := []string{}
		for rows.Next() {
			var id string
			if err := rows.Scan(&id); err != nil {
				return nil, fmt.Errorf("scanning the run's diagnostic objects: %w", err)
			}
			scope = append(scope, ObjectArtifact+":"+strings.ToLower(id))
		}
		if err := rows.Err(); err != nil {
			return nil, fmt.Errorf("reading the run's diagnostic objects: %w", err)
		}
		return scope, nil
	}

	ids := make([]string, 0, len(requested))
	for _, entry := range requested {
		_, id, _ := ParseObject(entry)
		ids = append(ids, id)
	}
	rows, err := tx.QueryContext(ctx, `
		SELECT DISTINCT a.id::text
		  FROM platform.artifacts AS a
		 WHERE a.id::text = ANY($2::text[])
		   AND a.deleted_at IS NULL
		   AND (EXISTS (SELECT 1 FROM platform.run_artifacts AS ra
		                 WHERE ra.run_id = $1::uuid AND ra.artifact_id = a.id)
		     OR EXISTS (SELECT 1 FROM platform.run_artifact_uploads AS ru
		                 WHERE ru.run_id = $1::uuid AND ru.artifact_id = a.id))`,
		runID, ids)
	if err != nil {
		return nil, fmt.Errorf("checking the diagnostic object scope: %w", err)
	}
	defer func() { _ = rows.Close() }()
	member := make(map[string]bool, len(requested))
	for rows.Next() {
		var id string
		if err := rows.Scan(&id); err != nil {
			return nil, fmt.Errorf("scanning the diagnostic object scope: %w", err)
		}
		member[strings.ToLower(id)] = true
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("checking the diagnostic object scope: %w", err)
	}
	scope := make([]string, 0, len(requested))
	for _, entry := range requested {
		_, id, _ := ParseObject(entry)
		if !member[id] {
			return nil, &ValidationError{
				Field:   "object_scope",
				Message: "each object must belong to this run and must not be deleted",
			}
		}
		scope = append(scope, ObjectArtifact+":"+id)
	}
	sort.Strings(scope)
	return scope, nil
}

// Revoke sets revoked_at once. It is owner-only and idempotent: a second DELETE
// answers 204 without a second audit row and without moving the timestamp.
func (s *Service) Revoke(ctx context.Context, ownerID, grantID string) (*Grant, error) {
	caller := strings.TrimSpace(ownerID)
	if !uuid.Valid(caller) {
		return nil, ErrNotFound
	}
	id := strings.TrimSpace(grantID)
	if !uuid.Valid(id) {
		return nil, ErrNotFound
	}
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return nil, fmt.Errorf("starting the revoke transaction: %w", err)
	}
	defer func() { _ = tx.Rollback() }()

	grant, err := scanGrant(tx.QueryRowContext(ctx, `
		SELECT `+grantColumns+`,
		       CASE WHEN revoked_at IS NOT NULL THEN 'revoked'
		            WHEN expires_at <= now() THEN 'expired' ELSE 'active' END AS state
		  FROM platform.diagnostic_grants
		 WHERE id = $1::uuid AND owner_id = $2::uuid
		   FOR UPDATE`, id, caller))
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrNotFound
	}
	if err != nil {
		return nil, fmt.Errorf("reading the diagnostic grant: %w", err)
	}
	if grant.RevokedAt != nil {
		return grant, nil
	}
	var revokedAt time.Time
	if err := tx.QueryRowContext(ctx, `
		UPDATE platform.diagnostic_grants
		   SET revoked_at = now()
		 WHERE id = $1::uuid AND revoked_at IS NULL
		RETURNING revoked_at`, id).Scan(&revokedAt); err != nil {
		return nil, fmt.Errorf("revoking the diagnostic grant: %w", err)
	}
	grant.RevokedAt = &revokedAt
	grant.State = StateRevoked

	metadata := map[string]any{
		"grant_id":   grant.ID,
		"run_id":     grant.RunID,
		"grantee_id": grant.GranteeID,
	}
	if err := audit.Record(
		ctx, tx, &caller, ActionGrantRevoked, "diagnostic_grant", &grant.ID, metadata,
	); err != nil {
		return nil, err
	}
	if err := tx.Commit(); err != nil {
		return nil, fmt.Errorf("committing the revoke: %w", err)
	}
	return grant, nil
}

// List returns every grant the caller owns or has been granted, metadata only.
// It is the documented non-contract read (ADR 0028): it exists so the owner's page
// can show/revoke grants and so the grantee can learn the grant id and object
// snapshot, without opening a single content byte.
func (s *Service) List(ctx context.Context, callerID string) ([]Grant, error) {
	caller := strings.TrimSpace(callerID)
	if !uuid.Valid(caller) {
		return nil, ErrNotFound
	}
	rows, err := s.DB.QueryContext(ctx, `
		SELECT `+grantColumns+`,
		       CASE WHEN revoked_at IS NOT NULL THEN 'revoked'
		            WHEN expires_at <= now() THEN 'expired' ELSE 'active' END AS state,
		       CASE WHEN owner_id = $1::uuid THEN 'owner'
		            ELSE 'grantee' END AS direction
		  FROM platform.diagnostic_grants
		 WHERE owner_id = $1::uuid OR grantee_id = $1::uuid
		 ORDER BY created_at DESC, id`, caller)
	if err != nil {
		return nil, fmt.Errorf("listing the diagnostic grants: %w", err)
	}
	defer func() { _ = rows.Close() }()
	grants := []Grant{}
	for rows.Next() {
		grant, err := scanGrantRow(rows)
		if err != nil {
			return nil, fmt.Errorf("scanning the diagnostic grants: %w", err)
		}
		grants = append(grants, grant)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("listing the diagnostic grants: %w", err)
	}
	return grants, nil
}

const grantColumns = `
		id::text, owner_id::text, grantee_id::text, run_id::text,
		to_jsonb(purposes)::text, object_scope::text,
		expires_at, revoked_at, created_at`

type rowScanner interface {
	Scan(dest ...any) error
}

func scanGrant(row rowScanner) (*Grant, error) {
	return scanGrantColumns(row, false)
}

func scanGrantRow(rows *sql.Rows) (Grant, error) {
	grant, err := scanGrantColumns(rows, true)
	if err != nil {
		return Grant{}, err
	}
	return *grant, nil
}

func scanGrantColumns(row rowScanner, withState bool) (*Grant, error) {
	var (
		grant        Grant
		purposesJSON string
		scopeJSON    string
		revoked      sql.NullTime
	)
	dest := []any{
		&grant.ID, &grant.OwnerID, &grant.GranteeID, &grant.RunID,
		&purposesJSON, &scopeJSON, &grant.ExpiresAt, &revoked, &grant.CreatedAt,
	}
	var state, direction sql.NullString
	if withState {
		dest = append(dest, &state, &direction)
	} else {
		dest = append(dest, &state)
	}
	if err := row.Scan(dest...); err != nil {
		return nil, err
	}
	if err := json.Unmarshal([]byte(purposesJSON), &grant.Purposes); err != nil {
		return nil, fmt.Errorf("decoding the grant purposes: %w", err)
	}
	if err := json.Unmarshal([]byte(scopeJSON), &grant.ObjectScope); err != nil {
		return nil, fmt.Errorf("decoding the grant object scope: %w", err)
	}
	if grant.Purposes == nil {
		grant.Purposes = []string{}
	}
	if grant.ObjectScope == nil {
		grant.ObjectScope = []string{}
	}
	if revoked.Valid {
		when := revoked.Time
		grant.RevokedAt = &when
	}
	grant.State = state.String
	if withState {
		grant.Direction = direction.String
	}
	return &grant, nil
}
