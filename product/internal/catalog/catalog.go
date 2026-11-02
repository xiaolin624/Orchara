// Package catalog is the applications read model and, from F02A/T023, the draft
// aggregate and published-version read model.
//
// Reads apply the resource ACL before deciding what the caller may see:
//
//   - every list puts the visibility predicate in the same SQL WHERE as the
//     cursor and LIMIT, so the page is filtered before it is cut (a grant cannot
//     be "hidden behind" an invisible row and produce an empty page);
//   - every single lookup answers all not-visible cases with one sentinel, so a
//     missing row and somebody else's row are indistinguishable to the caller.
//
// The draft write path adds optimistic concurrency: GetDraft returns a strong
// ETag, SaveDraft requires If-Match and updates with `WHERE revision = <etag>`.
// The two counters on a draft are deliberately different (see draft.go and
// migration 0010): revision is the ETag/CAS counter, execution_revision only
// moves when the executable content moves.
//
// F01 only reads applications; F02A owns createApplication, drafts and versions.
package catalog

import (
	"context"
	"database/sql"
	"encoding/base64"
	"errors"
	"fmt"
	"strconv"
	"strings"
	"time"

	"orchara/platform/internal/policy"
	"orchara/platform/internal/uuid"
)

// Pagination bounds fixed by DESIGN.md §4 and contracts/openapi.json.
const (
	DefaultLimit = 20
	MinLimit     = 1
	MaxLimit     = 100
)

// Errors are typed so the API layer maps them to contract codes without
// inspecting driver messages.
var (
	// ErrInvalidCursor is a malformed opaque cursor: 400 invalid_cursor.
	ErrInvalidCursor = errors.New("invalid cursor")
	// ErrInvalidLimit is a limit outside 1..100: 422 validation_failed.
	ErrInvalidLimit = errors.New("invalid limit")
	// ErrNotVisible is both "unknown" and "not authorized"; callers must answer
	// 404 for either, so the endpoint cannot be used to enumerate ids.
	ErrNotVisible = errors.New("application is not visible")
)

// Application is one row of the contract's Application schema.
type Application struct {
	ID              string
	Name            string
	ActiveVersionID *string
}

// Reader reads applications with the caller's ACL applied.
type Reader struct {
	DB *sql.DB
	// Authorizer decides the single-resource read (owner or an unexpired read
	// grant). It must be non-nil: a reader that cannot authorize fails closed.
	Authorizer *policy.Authorizer
}

// New builds a Reader.
func New(db *sql.DB, authorizer *policy.Authorizer) *Reader {
	return &Reader{DB: db, Authorizer: authorizer}
}

// ParseLimit applies the contract rule: an omitted limit means the default, a
// non-numeric or out-of-range limit is a validation error (422), never a silent
// clamp. A present but blank value is invalid rather than "omitted".
func ParseLimit(raw string) (int, error) {
	if raw == "" {
		return DefaultLimit, nil
	}
	trimmed := strings.TrimSpace(raw)
	if trimmed == "" {
		return 0, ErrInvalidLimit
	}
	limit, err := strconv.Atoi(trimmed)
	if err != nil || limit < MinLimit || limit > MaxLimit {
		return 0, ErrInvalidLimit
	}
	return limit, nil
}

// EncodeCursor serialises one keyset position (created_at, id) as an opaque
// base64url token. The payload is not a secret, but it is encoded so clients
// use it as an opaque continuation instead of querying by column.
func EncodeCursor(createdAt time.Time, id string) string {
	payload := createdAt.UTC().Format(time.RFC3339Nano) + "|" + id
	return base64.RawURLEncoding.EncodeToString([]byte(payload))
}

// DecodeCursor parses a cursor produced by EncodeCursor. Anything else is
// ErrInvalidCursor.
func DecodeCursor(raw string) (time.Time, string, error) {
	decoded, err := base64.RawURLEncoding.DecodeString(strings.TrimSpace(raw))
	if err != nil {
		return time.Time{}, "", ErrInvalidCursor
	}
	parts := strings.Split(string(decoded), "|")
	if len(parts) != 2 {
		return time.Time{}, "", ErrInvalidCursor
	}
	createdAt, err := time.Parse(time.RFC3339Nano, parts[0])
	if err != nil {
		return time.Time{}, "", ErrInvalidCursor
	}
	if !uuid.Valid(parts[1]) {
		return time.Time{}, "", ErrInvalidCursor
	}
	return createdAt.UTC(), parts[1], nil
}

// applicationReadPredicate is the single owner-or-unexpired-grant SQL predicate
// every application list uses. The caller supplies the 1-based placeholder
// numbers for the principal, the read clock, the resource type and the action, so
// the applications list and the version list share one copy of the predicate
// instead of two that can drift. The values are always policy.ResourceApplication
// and policy.ActionRead at the call sites; they are parameters (not literals) so
// the query keeps the existing placeholder order and argument count.
//
// The predicate assumes the application is aliased `a` and is used in the same
// WHERE as the keyset cursor and LIMIT, so filtering happens before pagination.
func applicationReadPredicate(principalArg, nowArg, resourceTypeArg, actionArg int) string {
	return fmt.Sprintf(`(
		        a.owner_id = $%[1]d
		        OR EXISTS (
		             SELECT 1
		               FROM platform.resource_grants AS g
		              WHERE g.principal_id = $%[1]d
		                AND g.resource_type = $%[3]d
		                AND g.resource_id = a.id
		                AND g.action = $%[4]d
		                AND (g.expires_at IS NULL OR g.expires_at > $%[2]d)
		           )
		       )`, principalArg, nowArg, resourceTypeArg, actionArg)
}

// readAllowed applies the default-deny ACL used by every single application
// read (application detail, draft, version, dependency visibility). A nil
// authorizer fails closed: a reader that cannot authorize never falls back to
// "visible".
func (r *Reader) readAllowed(
	ctx context.Context, callerID, applicationID, ownerID string,
) (bool, error) {
	if r.Authorizer == nil {
		return false, nil
	}
	return r.Authorizer.Allow(ctx, policy.PrincipalRef{ID: callerID}, policy.ResourceRef{
		Type:    policy.ResourceApplication,
		ID:      applicationID,
		OwnerID: ownerID,
	}, policy.ActionRead)
}

// ListVisible returns the applications principalID may read, newest first, with
// a stable (created_at DESC, id DESC) keyset order.
//
// The ACL predicate is part of the same WHERE as the cursor and LIMIT: filtering
// happens before pagination, so an injected invisible row can never consume a
// page slot or leave an empty page before the real end of the list.
//
// now is the caller's clock: the API passes policy.Authorizer.Clock(), the same
// clock GetVisible goes through, so a grant's expiry is judged by one authority
// for both the list and the detail read instead of mixing the database's now()
// with the process clock.
func (r *Reader) ListVisible(
	ctx context.Context,
	principalID, cursor string,
	limit int,
	now time.Time,
) ([]Application, *string, error) {
	if limit == 0 {
		limit = DefaultLimit
	}
	if limit < MinLimit || limit > MaxLimit {
		return nil, nil, ErrInvalidLimit
	}
	var (
		cursorAt any
		cursorID any
	)
	if strings.TrimSpace(cursor) != "" {
		at, id, err := DecodeCursor(cursor)
		if err != nil {
			return nil, nil, err
		}
		cursorAt, cursorID = at, id
	}

	rows, err := r.DB.QueryContext(ctx, `
		SELECT a.id, a.name, a.active_version_id, a.created_at
		  FROM platform.applications AS a
		 WHERE a.archived_at IS NULL
		   AND `+applicationReadPredicate(1, 5, 6, 7)+`
		   AND (
		        $2::timestamptz IS NULL
		        OR (a.created_at, a.id) < ($2::timestamptz, $3::uuid)
		       )
		 ORDER BY a.created_at DESC, a.id DESC
		 LIMIT $4`,
		principalID, cursorAt, cursorID, limit+1,
		now.UTC(), policy.ResourceApplication, policy.ActionRead,
	)
	if err != nil {
		return nil, nil, fmt.Errorf("listing applications: %w", err)
	}
	defer rows.Close()

	type scored struct {
		application Application
		createdAt   time.Time
	}
	all := make([]scored, 0, limit+1)
	for rows.Next() {
		var (
			entry  scored
			active sql.NullString
		)
		if err := rows.Scan(&entry.application.ID, &entry.application.Name, &active, &entry.createdAt); err != nil {
			return nil, nil, fmt.Errorf("listing applications: %w", err)
		}
		if active.Valid {
			value := active.String
			entry.application.ActiveVersionID = &value
		}
		all = append(all, entry)
	}
	if err := rows.Err(); err != nil {
		return nil, nil, fmt.Errorf("listing applications: %w", err)
	}

	items := make([]Application, 0, len(all))
	for _, entry := range all {
		items = append(items, entry.application)
	}
	var next *string
	if len(all) > limit {
		// One extra row proves another page exists; the cursor names the last
		// returned row so the next page starts strictly after it.
		last := all[limit-1]
		token := EncodeCursor(last.createdAt, last.application.ID)
		next = &token
		items = items[:limit]
	}
	return items, next, nil
}

// GetVisible returns one application, or ErrNotVisible when it does not exist,
// has been archived, or the caller may not read it. The three cases are
// deliberately one error: the HTTP layer answers 404 for all of them.
func (r *Reader) GetVisible(
	ctx context.Context,
	principalID, applicationID string,
) (*Application, error) {
	if !uuid.Valid(applicationID) {
		return nil, ErrNotVisible
	}
	var (
		application Application
		ownerID     string
		active      sql.NullString
	)
	err := r.DB.QueryRowContext(ctx, `
		SELECT id, name, active_version_id, owner_id
		  FROM platform.applications
		 WHERE id = $1
		   AND archived_at IS NULL`,
		applicationID,
	).Scan(&application.ID, &application.Name, &active, &ownerID)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrNotVisible
	}
	if err != nil {
		return nil, fmt.Errorf("reading an application: %w", err)
	}
	if active.Valid {
		value := active.String
		application.ActiveVersionID = &value
	}
	allow, err := r.readAllowed(ctx, principalID, application.ID, ownerID)
	if err != nil {
		return nil, err
	}
	if !allow {
		return nil, ErrNotVisible
	}
	return &application, nil
}
