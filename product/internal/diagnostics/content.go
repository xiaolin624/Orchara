package diagnostics

// F11/T073: the grantee-only diagnostic content door.
//
// ReadContent is the only code path through which an administrator may read
// another user's run content. It re-checks every grant dimension against live
// rows in one transaction — grantee, revocation, expiry, run tombstone, purpose,
// object snapshot, run membership, artifact state — and commits the
// `diagnostic.content_read` audit row *before* the object is opened and before a
// single byte can be served. A refused or failed attempt is audited too, with its
// own stable code; an unknown/foreign grant is the located 404 and is not audited
// because there is no grant to attribute it to.
//
// The object is re-read from `platform.artifacts` (deleted_at/state) and its run
// membership from `run_artifacts`/`run_artifact_uploads`, so a stale grant never
// resurrects a tombstoned or purged body.

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"strings"

	"orchara/platform/internal/audit"
	"orchara/platform/internal/objectstore"
	"orchara/platform/internal/uuid"
)

// ReadRequest is one diagnostic content read after HTTP parsing.
type ReadRequest struct {
	GrantID string
	Object  string
	Purpose string
}

// ContentRead is one opened body plus the correlation the audit row records.
// The caller owns Reader and must close it.
type ContentRead struct {
	GrantID      string
	ArtifactID   string
	OperationID  string
	AttemptToken string
	Reader       io.ReadCloser
	Size         int64
}

// grantGate is the live grant row as read inside the read transaction.
type grantGate struct {
	id          string
	runID       string
	granteeID   string
	purposes    []string
	objectScope []string
	expired     bool
	revoked     sql.NullTime
}

// ReadContent performs one audited diagnostic content read for callerID.
func (s *Service) ReadContent(
	ctx context.Context, callerID string, request ReadRequest, requestID string,
) (*ContentRead, error) {
	caller := strings.TrimSpace(callerID)
	if !uuid.Valid(caller) {
		return nil, ErrNotFound
	}
	grantID := strings.TrimSpace(request.GrantID)
	if !uuid.Valid(grantID) {
		return nil, ErrNotFound
	}
	purpose := strings.TrimSpace(request.Purpose)
	if !purposePattern.MatchString(purpose) {
		return nil, &ValidationError{
			Field:   "purpose",
			Message: "purpose must match [a-z][a-z0-9_-]{0,63}",
		}
	}
	kind, objectID, ok := ParseObject(request.Object)
	if !ok {
		return nil, &ValidationError{
			Field:   "object",
			Message: "object must be artifact:<uuid>",
		}
	}

	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return nil, fmt.Errorf("starting the diagnostic read transaction: %w", err)
	}
	defer func() { _ = tx.Rollback() }()

	gate, err := loadGrantGate(ctx, tx, grantID)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrNotFound
	}
	if err != nil {
		return nil, fmt.Errorf("reading the diagnostic grant: %w", err)
	}
	if !strings.EqualFold(gate.granteeID, caller) {
		// Only the named administrator may use the grant; everyone else gets the
		// same 404 as a missing grant (no enumeration, no attribution guess).
		return nil, ErrNotFound
	}

	attemptToken := ""
	operationID := ""

	record := func(outcome, code string) error {
		read := audit.ContentRead{
			ActorID:      &caller,
			GrantID:      gate.id,
			Purpose:      purpose,
			RunID:        gate.runID,
			ResourceType: kind,
			ResourceID:   objectID,
			OperationID:  operationID,
			AttemptToken: attemptToken,
			RequestID:    requestID,
			Outcome:      outcome,
			ErrorCode:    code,
		}
		if err := audit.RecordContentRead(ctx, tx, read); err != nil {
			return err
		}
		return tx.Commit()
	}
	refuse := func(code string, cause error) (*ContentRead, error) {
		if err := record(audit.OutcomeRefused, code); err != nil {
			return nil, err
		}
		return nil, cause
	}

	// Order is deliberate and documented in ADR 0028: a revoked grant is terminal,
	// an expired grant is refused before any row is touched, and a tombstoned run
	// outlives every other check because a grant must never be a resurrection path.
	if gate.revoked.Valid {
		return refuse("diagnostic_grant_revoked", ErrGrantRevoked)
	}
	if gate.expired {
		return refuse("diagnostic_grant_expired", ErrGrantExpired)
	}
	var runDeleted sql.NullTime
	err = tx.QueryRowContext(ctx, `
		SELECT deleted_at FROM platform.runs WHERE id = $1::uuid`, gate.runID).
		Scan(&runDeleted)
	if errors.Is(err, sql.ErrNoRows) {
		return refuse("diagnostic_run_deleted", ErrRunDeleted)
	}
	if err != nil {
		return nil, fmt.Errorf("reading the diagnostic run: %w", err)
	}
	if runDeleted.Valid {
		return refuse("diagnostic_run_deleted", ErrRunDeleted)
	}
	if !containsString(gate.purposes, purpose) {
		return refuse("diagnostic_purpose_denied", ErrPurposeDenied)
	}
	if !containsString(gate.objectScope, kind+":"+objectID) {
		return refuse("diagnostic_scope_denied", ErrScopeDenied)
	}

	// The grant snapshot is not sufficient: the object must still belong to this
	// run, still exist, and still be a ready artifact. The tombstone is re-read in
	// the same statement that supplies the object key.
	var (
		objectKey string
		state     string
		deleted   sql.NullTime
		size      sql.NullInt64
		attempt   sql.NullString
		member    bool
	)
	err = tx.QueryRowContext(ctx, `
		SELECT a.object_key, a.state, a.deleted_at, a.size_bytes,
		       COALESCE(
		           (SELECT ra.attempt_token::text FROM platform.run_artifacts AS ra
		             WHERE ra.run_id = $2::uuid AND ra.artifact_id = a.id
		               AND ra.attempt_token IS NOT NULL
		             ORDER BY ra.relation, ra.attempt_token LIMIT 1),
		           (SELECT ru.attempt_token::text FROM platform.run_artifact_uploads AS ru
		             WHERE ru.run_id = $2::uuid AND ru.artifact_id = a.id
		             ORDER BY ru.created_at DESC LIMIT 1)
		       ) AS attempt_token,
		       (EXISTS (SELECT 1 FROM platform.run_artifacts AS ra
		                 WHERE ra.run_id = $2::uuid AND ra.artifact_id = a.id)
		        OR EXISTS (SELECT 1 FROM platform.run_artifact_uploads AS ru
		                    WHERE ru.run_id = $2::uuid AND ru.artifact_id = a.id)) AS member
		  FROM platform.artifacts AS a
		 WHERE a.id = $1::uuid`, objectID, gate.runID).
		Scan(&objectKey, &state, &deleted, &size, &attempt, &member)
	if errors.Is(err, sql.ErrNoRows) {
		return refuse("diagnostic_content_unavailable", ErrContentUnavailable)
	}
	if err != nil {
		return nil, fmt.Errorf("reading the diagnostic artifact: %w", err)
	}
	if !member {
		return refuse("diagnostic_content_unavailable", ErrContentUnavailable)
	}
	if deleted.Valid || state == "deleted" || state == "quarantined" {
		return refuse("diagnostic_content_unavailable", ErrContentUnavailable)
	}
	if state != "ready" {
		return refuse("diagnostic_content_not_ready", ErrContentNotReady)
	}
	if attempt.Valid && uuid.Valid(attempt.String) {
		attemptToken = strings.ToLower(attempt.String)
		operationID = s.operationForAttempt(ctx, tx, attemptToken)
	}

	if s.Objects == nil {
		if err := record(audit.OutcomeFailed, "object_store_unavailable"); err != nil {
			return nil, err
		}
		return nil, ErrObjectUnavailable
	}
	reader, openedSize, err := s.Objects.Open(objectKey)
	if err != nil {
		code := "diagnostic_storage_io"
		if errors.Is(err, objectstore.ErrNotExist) || errors.Is(err, objectstore.ErrInvalidKey) {
			code = "diagnostic_content_missing"
		}
		if recordErr := record(audit.OutcomeFailed, code); recordErr != nil {
			return nil, recordErr
		}
		return nil, ErrContentUnavailable
	}

	// The audit row commits before the handler can write the first byte. If this
	// fails the body is closed unread: an unrecorded read never happens.
	if err := record(audit.OutcomeServed, ""); err != nil {
		_ = reader.Close()
		return nil, err
	}
	sizeBytes := size.Int64
	if openedSize >= 0 {
		sizeBytes = openedSize
	}
	return &ContentRead{
		GrantID:      gate.id,
		ArtifactID:   objectID,
		OperationID:  operationID,
		AttemptToken: attemptToken,
		Reader:       reader,
		Size:         sizeBytes,
	}, nil
}

// loadGrantGate reads one grant row FOR SHARE. The expiry is evaluated by the
// database clock, not the process clock.
func loadGrantGate(ctx context.Context, tx *sql.Tx, grantID string) (*grantGate, error) {
	var (
		gate         grantGate
		purposesJSON string
		scopeJSON    string
	)
	err := tx.QueryRowContext(ctx, `
		SELECT id::text, run_id::text, grantee_id::text,
		       to_jsonb(purposes)::text, object_scope::text,
		       (expires_at <= now()) AS expired, revoked_at
		  FROM platform.diagnostic_grants
		 WHERE id = $1::uuid
		   FOR SHARE`, grantID).
		Scan(&gate.id, &gate.runID, &gate.granteeID, &purposesJSON, &scopeJSON,
			&gate.expired, &gate.revoked)
	if err != nil {
		return nil, err
	}
	if err := jsonUnmarshalStrings(purposesJSON, &gate.purposes); err != nil {
		return nil, fmt.Errorf("decoding the grant purposes: %w", err)
	}
	if err := jsonUnmarshalStrings(scopeJSON, &gate.objectScope); err != nil {
		return nil, fmt.Errorf("decoding the grant object scope: %w", err)
	}
	return &gate, nil
}

// operationForAttempt resolves the one operation id bound to a physical attempt
// token. If the token was used by several operations (a documented possibility
// for retried calls), the audit records the attempt only: an arbitrary operation
// would be a lie. The attempt token itself is always the real join key.
func (s *Service) operationForAttempt(
	ctx context.Context, tx *sql.Tx, attemptToken string,
) string {
	rows, err := tx.QueryContext(ctx, `
		SELECT DISTINCT operation_id::text
		  FROM platform.operation_attempts
		 WHERE attempt_token = $1::uuid`, attemptToken)
	if err != nil {
		return ""
	}
	defer func() { _ = rows.Close() }()
	var ids []string
	for rows.Next() {
		var id string
		if err := rows.Scan(&id); err != nil {
			return ""
		}
		ids = append(ids, id)
	}
	if rows.Err() != nil || len(ids) != 1 {
		return ""
	}
	return ids[0]
}

func containsString(values []string, wanted string) bool {
	for _, value := range values {
		if value == wanted {
			return true
		}
	}
	return false
}

func jsonUnmarshalStrings(raw string, target *[]string) error {
	if raw == "" || raw == "null" {
		*target = []string{}
		return nil
	}
	var values []string
	if err := json.Unmarshal([]byte(raw), &values); err != nil {
		return err
	}
	if values == nil {
		values = []string{}
	}
	*target = values
	return nil
}
