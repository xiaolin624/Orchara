package policy

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/jackc/pgx/v5/pgconn"

	"orchara/platform/internal/audit"
	"orchara/platform/internal/uuid"
)

// Grant vocabulary. F01 fixes one resource type and one action; the maps are
// the extension point for later tickets, and an unknown value is a validation
// error rather than an unreadable row.
const (
	// ResourceApplication is the only resource_type F01 grants.
	ResourceApplication = "application"
	// ActionRead is the only action F01 grants.
	ActionRead = "read"
)

var (
	ResourceTypes = map[string]bool{ResourceApplication: true}
	Actions       = map[string]bool{ActionRead: true}
)

// Errors the API layer maps to contract codes. They are sentinels so handlers
// never inspect driver messages.
var (
	ErrValidation        = errors.New("validation failed")
	ErrGrantExists       = errors.New("grant already exists")
	ErrGrantNotFound     = errors.New("grant not found")
	ErrPrincipalNotFound = errors.New("principal not found")
	ErrPrincipalDisabled = errors.New("principal is disabled")
	ErrResourceNotFound  = errors.New("resource not found")
)

// ValidationError names the request field that failed semantic validation.
type ValidationError struct {
	Field   string
	Message string
}

func (e *ValidationError) Error() string { return fmt.Sprintf("%s: %s", e.Field, e.Message) }

// Unwrap lets callers use errors.Is(err, ErrValidation).
func (e *ValidationError) Unwrap() error { return ErrValidation }

// GrantParams is one grant request, already parsed from HTTP.
type GrantParams struct {
	PrincipalID  string
	ResourceType string
	ResourceID   string
	Action       string
	ExpiresAt    *time.Time
}

// GrantResult is the write receipt: the new grant id and the policy revision it
// committed. The revision is used for audit metadata and the response.
type GrantResult struct {
	ID       string
	Revision int64
}

// ValidateGrant is the pure request validation. It is called by the handler
// (for an early 422) and again by Grant inside the transaction, so a caller
// cannot bypass it by reaching the service layer directly.
func ValidateGrant(params GrantParams, now time.Time) error {
	if !uuid.Valid(params.PrincipalID) {
		return &ValidationError{Field: "principal_id", Message: "principal_id must be a UUID"}
	}
	if !ResourceTypes[params.ResourceType] {
		return &ValidationError{
			Field:   "resource_type",
			Message: "resource_type must be one of the known types (" + ResourceApplication + ")",
		}
	}
	if !uuid.Valid(params.ResourceID) {
		return &ValidationError{Field: "resource_id", Message: "resource_id must be a UUID"}
	}
	if !Actions[params.Action] {
		return &ValidationError{
			Field:   "action",
			Message: "action must be one of the known actions (" + ActionRead + ")",
		}
	}
	if params.ExpiresAt != nil && !params.ExpiresAt.After(now) {
		return &ValidationError{Field: "expires_at", Message: "expires_at must be in the future"}
	}
	return nil
}

// validateSelfGrant refuses a grant whose principal is the acting
// administrator. An administrator is not an automatic content reader (T019
// acceptance 3), and the ACL write path must not be usable to manufacture that
// access: the documented path is a second administrator granting the read.
//
// UUID text is case-insensitive, so the comparison is case-insensitive too; an
// upper-case spelling of the caller's id must not bypass the rule and become a
// grant row that PostgreSQL resolves back to the same principal.
func validateSelfGrant(actorID string, params GrantParams) error {
	if params.PrincipalID != "" && strings.EqualFold(params.PrincipalID, actorID) {
		return &ValidationError{
			Field:   "principal_id",
			Message: "an administrator cannot grant themselves access; another administrator must grant it",
		}
	}
	return nil
}

// Grant creates one grant, bumps the acl epoch and records `grant.created`, all
// in one transaction. The epoch bump is what makes a previously cached allow
// unreachable, so the new grant takes effect on the very next decision.
func (a *Authorizer) Grant(ctx context.Context, actorID string, params GrantParams) (*GrantResult, error) {
	now := a.clock()
	if err := ValidateGrant(params, now); err != nil {
		return nil, err
	}
	if err := validateSelfGrant(actorID, params); err != nil {
		return nil, err
	}

	tx, err := a.DB.BeginTx(ctx, nil)
	if err != nil {
		return nil, fmt.Errorf("starting the grant transaction: %w", err)
	}
	defer func() { _ = tx.Rollback() }()

	var granteeDisabled sql.NullTime
	err = tx.QueryRowContext(ctx,
		`SELECT disabled_at FROM platform.principals WHERE id = $1`,
		params.PrincipalID,
	).Scan(&granteeDisabled)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrPrincipalNotFound
	}
	if err != nil {
		return nil, fmt.Errorf("looking up the grantee: %w", err)
	}
	if granteeDisabled.Valid {
		return nil, ErrPrincipalDisabled
	}

	// Only `application` is a known resource type in F01, so this is the only
	// resource lookup. A later resource type adds its own existence probe here.
	var archivedAt sql.NullTime
	err = tx.QueryRowContext(ctx,
		`SELECT archived_at FROM platform.applications WHERE id = $1`,
		params.ResourceID,
	).Scan(&archivedAt)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrResourceNotFound
	}
	if err != nil {
		return nil, fmt.Errorf("looking up the resource: %w", err)
	}
	if archivedAt.Valid {
		// A grant on an archived application would be a grant on nothing.
		return nil, ErrResourceNotFound
	}

	var expiresAt any
	if params.ExpiresAt != nil {
		expiresAt = params.ExpiresAt.UTC()
	}
	var grantID string
	err = tx.QueryRowContext(ctx, `
		INSERT INTO platform.resource_grants
		       (id, principal_id, resource_type, resource_id, action, expires_at)
		VALUES (gen_random_uuid(), $1, $2, $3, $4, $5)
		RETURNING id`,
		params.PrincipalID, params.ResourceType, params.ResourceID, params.Action, expiresAt,
	).Scan(&grantID)
	if isGrantTupleViolation(err) {
		return nil, ErrGrantExists
	}
	if err != nil {
		return nil, fmt.Errorf("creating the grant: %w", err)
	}

	metadata := map[string]any{
		"principal_id":  params.PrincipalID,
		"resource_type": params.ResourceType,
		"resource_id":   params.ResourceID,
		"action":        params.Action,
	}
	if params.ExpiresAt != nil {
		metadata["expires_at"] = params.ExpiresAt.UTC().Format(time.RFC3339)
	}
	if err := audit.Record(
		ctx, tx, &actorID, "grant.created", params.ResourceType, &params.ResourceID, metadata,
	); err != nil {
		return nil, err
	}
	revision, err := Bump(ctx, tx)
	if err != nil {
		return nil, err
	}
	if err := tx.Commit(); err != nil {
		return nil, fmt.Errorf("committing the grant: %w", err)
	}
	return &GrantResult{ID: grantID, Revision: revision}, nil
}

// Revoke deletes one grant, bumps the acl epoch and records `grant.revoked`, all
// in one transaction. An unknown grant is a typed not-found, never a silent 204.
func (a *Authorizer) Revoke(ctx context.Context, actorID, grantID string) (int64, error) {
	if !uuid.Valid(grantID) {
		// A malformed id cannot name a row; report the same not-found as an id
		// that does not exist (the endpoint has nothing to enumerate).
		return 0, ErrGrantNotFound
	}
	tx, err := a.DB.BeginTx(ctx, nil)
	if err != nil {
		return 0, fmt.Errorf("starting the revoke transaction: %w", err)
	}
	defer func() { _ = tx.Rollback() }()

	var principalID, resourceType, resourceID, action string
	err = tx.QueryRowContext(ctx, `
		DELETE FROM platform.resource_grants
		 WHERE id = $1
		RETURNING principal_id, resource_type, resource_id, action`,
		grantID,
	).Scan(&principalID, &resourceType, &resourceID, &action)
	if errors.Is(err, sql.ErrNoRows) {
		return 0, ErrGrantNotFound
	}
	if err != nil {
		return 0, fmt.Errorf("revoking the grant: %w", err)
	}

	if err := audit.Record(
		ctx, tx, &actorID, "grant.revoked", resourceType, &resourceID, map[string]any{
			"principal_id":  principalID,
			"resource_type": resourceType,
			"resource_id":   resourceID,
			"action":        action,
		},
	); err != nil {
		return 0, err
	}
	revision, err := Bump(ctx, tx)
	if err != nil {
		return 0, err
	}
	if err := tx.Commit(); err != nil {
		return 0, fmt.Errorf("committing the revoke: %w", err)
	}
	return revision, nil
}

// Bump advances the acl policy revision inside an existing transaction and
// returns the new revision. Every module that changes authorization state
// (grants, role bindings, principal disable in T021) must call it in the same
// transaction as the change, so a cached decision can never outlive it.
func Bump(ctx context.Context, tx *sql.Tx) (int64, error) {
	var revision int64
	err := tx.QueryRowContext(ctx,
		`UPDATE platform.policy_epochs
		    SET revision = revision + 1
		  WHERE scope = $1
		RETURNING revision`,
		ScopeACL,
	).Scan(&revision)
	if errors.Is(err, sql.ErrNoRows) {
		return 0, ErrEpochMissing
	}
	if err != nil {
		return 0, fmt.Errorf("bumping the acl policy epoch: %w", err)
	}
	return revision, nil
}

// isGrantTupleViolation reports whether err is the unique-tuple conflict of
// resource_grants. It checks the constraint name, not the message text.
func isGrantTupleViolation(err error) bool {
	var pgErr *pgconn.PgError
	if !errors.As(err, &pgErr) || pgErr.Code != "23505" {
		return false
	}
	return pgErr.ConstraintName == "resource_grants_tuple"
}
