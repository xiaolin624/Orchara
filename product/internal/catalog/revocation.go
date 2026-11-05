package catalog

// F02B/T070: revoking a published application version.
//
// Revocation is a state change, never a rewrite: `application_versions` keeps
// `manifest`, `manifest_sha256`, `manifest_artifact_id`, `version_no` and
// `review_id` forever (the row is the historical reference `runs` point at), and
// exactly three columns move together — `revoked_at`, `revoked_by`,
// `revoke_reason` — under the database CHECK T068's 0042 added. There is no
// DELETE path: the composite active-version foreign key is ON DELETE RESTRICT.
//
// The contract's Accepted body (`{id, state}`) has no version state field, so
// `state` is derived as "revoked"; the read model hides a revoked version
// (`GET /versions/{id}` is 404 and the list omits it) instead of inventing a
// field. `revokeVersion` has **no** Idempotency-Key in the contract: the natural
// `revoked_at IS NULL` CAS is the idempotency, and a second call replays the same
// 200 body without a second audit/outbox row.
//
// The active pointer is deliberately set to NULL, never repointed at another
// version. `Application.active_version_id` is the only availability signal the
// contract has, and repointing would silently substitute a version the
// administrator did not choose (the ticket's "不会自动替换为最新版"). The previous
// pointer is recorded in the outbox payload and the audit metadata so a consumer
// (T077) and an auditor can reconstruct the transition.
//
// `stop_existing:true` records a cancellation intent for every non-terminal Run
// of the version through the same control facts the T036 cancel machinery uses
// (`cancel_requested_at` + the stable `version_revoked` reason code) and appends
// one `run.status` event per Run with the existing same-transaction primitive.
// The revoke transaction never writes a terminal Run state: the worker/exit path
// and the one finalizer own that transition. `stop_existing:false` records the
// revocation only.

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"strings"
	"unicode/utf8"

	"orchara/platform/internal/audit"
	"orchara/platform/internal/execution"
	"orchara/platform/internal/uuid"
)

const (
	// VersionRevokedState is the contract-visible Accepted.state of a revoked
	// version (the Version schema itself has no state field).
	VersionRevokedState = "revoked"

	// VersionRevokedRunCode is the stable, non-free-text control reason recorded
	// on a Run whose version was revoked. It travels as the Run's error_code (the
	// contract's run.status payload is `{state, error_code}` with no room for a
	// free-text reason); the cancelled finalizer clears it when the worker exits.
	VersionRevokedRunCode = "version_revoked"

	// MaxRevokeReasonRunes bounds the recorded reason. The contract says only
	// `string`; a 2000-character cap keeps one row from storing an essay without
	// inventing a contract rule (a located 422 explains it).
	MaxRevokeReasonRunes = 2000
)

// RevokeVersionRequest keeps the two body members raw so a wrong type is a
// located 422 and unknown top-level keys are reported by the service.
type RevokeVersionRequest struct {
	Reason        json.RawMessage
	StopExisting  json.RawMessage
	UnknownFields []string
}

// RevokeVersionPlan is a structurally valid revoke body.
type RevokeVersionPlan struct {
	Reason       string
	StopExisting bool
}

// ValidateRevokeVersionRequest checks the contract body's shape and normalises
// the reason. It touches no database.
func ValidateRevokeVersionRequest(request RevokeVersionRequest) (*RevokeVersionPlan, error) {
	if len(request.UnknownFields) > 0 {
		unknown := append([]string(nil), request.UnknownFields...)
		sort.Strings(unknown)
		return nil, &ValidationError{
			Field:   unknown[0],
			Message: "revokeVersion accepts exactly reason and stop_existing",
		}
	}
	var reason string
	if err := json.Unmarshal(request.Reason, &reason); err != nil {
		return nil, &ValidationError{Field: "reason", Message: "reason must be a string"}
	}
	reason = strings.TrimSpace(reason)
	if reason == "" {
		return nil, &ValidationError{Field: "reason", Message: "a revocation reason is required"}
	}
	if utf8.RuneCountInString(reason) > MaxRevokeReasonRunes {
		return nil, &ValidationError{
			Field:   "reason",
			Message: fmt.Sprintf("reason must be at most %d characters", MaxRevokeReasonRunes),
		}
	}
	var stopExisting bool
	if len(request.StopExisting) == 0 {
		return nil, &ValidationError{Field: "stop_existing", Message: "stop_existing is required"}
	}
	if err := json.Unmarshal(request.StopExisting, &stopExisting); err != nil {
		return nil, &ValidationError{
			Field:   "stop_existing",
			Message: "stop_existing must be a boolean",
		}
	}
	return &RevokeVersionPlan{Reason: reason, StopExisting: stopExisting}, nil
}

// VersionRevocation is the outcome of one revoke call. AlreadyRevoked is true
// when a previous call had already committed the transition; in that case no
// audit/outbox row and no Run intent was written by this call.
type VersionRevocation struct {
	VersionID               string
	ApplicationID           string
	VersionNo               int64
	State                   string
	AlreadyRevoked          bool
	PreviousActiveVersionID *string
	RunsCancelRequested     []string
	RunsAlreadyCancelling   []string
}

// revokeVersionOutboxPayload is the durable T077 signal; extra keys are additive
// JSONB (the contract only fixes the outbox *table*, not the payload).
type revokeVersionOutboxPayload struct {
	Op                      string  `json:"op"`
	ApplicationID           string  `json:"application_id"`
	VersionID               string  `json:"version_id"`
	VersionNo               int64   `json:"version_no"`
	ManifestSHA256          string  `json:"manifest_sha256"`
	PreviousActiveVersionID *string `json:"previous_active_version_id"`
	ActiveVersionID         *string `json:"active_version_id"`
	Reason                  string  `json:"reason"`
	StopExisting            bool    `json:"stop_existing"`
}

// RevokeVersion validates one revokeVersion call and commits the state change,
// its catalog outbox row, one audit event and (when requested) the cancellation
// intent for every non-terminal Run of the version, in one transaction.
func (r *Reader) RevokeVersion(
	ctx context.Context,
	adminID, versionID string,
	request RevokeVersionRequest,
) (*VersionRevocation, error) {
	plan, err := ValidateRevokeVersionRequest(request)
	if err != nil {
		return nil, err
	}
	if !uuid.Valid(versionID) {
		return nil, ErrNotVisible
	}

	tx, err := r.DB.BeginTx(ctx, nil)
	if err != nil {
		return nil, fmt.Errorf("starting the version revocation transaction: %w", err)
	}
	defer func() { _ = tx.Rollback() }()

	// Lock the version and its application together: the version row is the state
	// change, the application row is the active pointer the same transaction may
	// clear. The version is selected even when already revoked, because a repeated
	// call must replay 200 rather than 404 (the contract has no Idempotency-Key).
	var (
		applicationID string
		versionNo     int64
		manifestSHA   string
		revokedAt     sql.NullTime
		active        sql.NullString
	)
	err = tx.QueryRowContext(ctx, `
		SELECT v.application_id::text, v.version_no, v.manifest_sha256,
		       v.revoked_at, a.active_version_id::text
		  FROM platform.application_versions AS v
		  JOIN platform.applications AS a ON a.id = v.application_id
		 WHERE v.id = $1::uuid
		   FOR UPDATE OF v, a`, versionID,
	).Scan(&applicationID, &versionNo, &manifestSHA, &revokedAt, &active)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrNotVisible
	}
	if err != nil {
		return nil, fmt.Errorf("reading the version to revoke: %w", err)
	}
	if revokedAt.Valid {
		return &VersionRevocation{
			VersionID: versionID, ApplicationID: applicationID, VersionNo: versionNo,
			State: VersionRevokedState, AlreadyRevoked: true,
		}, nil
	}

	result, err := tx.ExecContext(ctx, `
		UPDATE platform.application_versions
		   SET revoked_at = now(), revoked_by = $2::uuid, revoke_reason = $3
		 WHERE id = $1::uuid AND revoked_at IS NULL`, versionID, adminID, plan.Reason)
	if err != nil {
		return nil, fmt.Errorf("revoking the application version: %w", err)
	}
	affected, err := result.RowsAffected()
	if err != nil {
		return nil, fmt.Errorf("revoking the application version: %w", err)
	}
	if affected != 1 {
		// A concurrent revoke won the CAS and committed between our SELECT and
		// UPDATE; this call is the idempotent replay and writes nothing.
		return &VersionRevocation{
			VersionID: versionID, ApplicationID: applicationID, VersionNo: versionNo,
			State: VersionRevokedState, AlreadyRevoked: true,
		}, nil
	}

	// Never silently substitute another version: the pointer becomes NULL and the
	// previous value is recorded for the outbox/audit trail.
	var previousActiveVersionID *string
	if active.Valid && strings.EqualFold(active.String, versionID) {
		result, err = tx.ExecContext(ctx, `
			UPDATE platform.applications
			   SET active_version_id = NULL
			 WHERE id = $1::uuid AND active_version_id = $2::uuid`,
			applicationID, versionID)
		if err != nil {
			return nil, fmt.Errorf("clearing the active version pointer: %w", err)
		}
		affected, err = result.RowsAffected()
		if err != nil {
			return nil, fmt.Errorf("clearing the active version pointer: %w", err)
		}
		if affected != 1 {
			return nil, errors.New("the active version pointer changed during the revocation")
		}
		previous := versionID
		previousActiveVersionID = &previous
	}

	revocation := &VersionRevocation{
		VersionID: versionID, ApplicationID: applicationID, VersionNo: versionNo,
		State: VersionRevokedState, AlreadyRevoked: false,
		PreviousActiveVersionID: previousActiveVersionID,
		RunsCancelRequested:     []string{},
		RunsAlreadyCancelling:   []string{},
	}
	if plan.StopExisting {
		if err := r.requestVersionRunCancellation(ctx, tx, versionID, revocation); err != nil {
			return nil, err
		}
	}

	// Durable catalog signal. There is no dispatcher in this build; T077/T082 own
	// delivery and the catalog index, so delivered_at/attempts stay untouched.
	if _, err := insertOutboxTx(ctx, tx, PublicationOutboxKind, versionID, revokeVersionOutboxPayload{
		Op: "revoke", ApplicationID: applicationID, VersionID: versionID,
		VersionNo: versionNo, ManifestSHA256: manifestSHA,
		PreviousActiveVersionID: previousActiveVersionID, ActiveVersionID: nil,
		Reason: plan.Reason, StopExisting: plan.StopExisting,
	}); err != nil {
		return nil, err
	}

	actor := adminID
	if err := audit.Record(ctx, tx, &actor, "version.revoked", "application_version",
		&versionID, map[string]any{
			"application_id":             applicationID,
			"version_no":                 versionNo,
			"manifest_sha256":            manifestSHA,
			"reason":                     plan.Reason,
			"stop_existing":              plan.StopExisting,
			"previous_active_version_id": previousActiveVersionID,
			"runs_cancel_requested":      revocation.RunsCancelRequested,
			"runs_already_cancelling":    revocation.RunsAlreadyCancelling,
		}); err != nil {
		return nil, err
	}

	if err := tx.Commit(); err != nil {
		return nil, fmt.Errorf("committing the version revocation: %w", err)
	}
	return revocation, nil
}

// requestVersionRunCancellation records the intent for every non-terminal Run of
// one version. The rows are locked in a stable order, the intent keeps its first
// value (COALESCE) so a user/deadline cancel already in flight is not rewritten,
// and one run.status event is appended through the shared same-transaction
// primitive. It never writes a terminal state.
func (r *Reader) requestVersionRunCancellation(
	ctx context.Context, tx *sql.Tx, versionID string, revocation *VersionRevocation,
) error {
	rows, err := tx.QueryContext(ctx, `
		SELECT id::text, cancel_requested_at
		  FROM platform.runs
		 WHERE application_version_id = $1::uuid
		   AND state NOT IN ('succeeded', 'failed', 'cancelled')
		   AND deleted_at IS NULL
		 ORDER BY id
		   FOR UPDATE`, versionID)
	if err != nil {
		return fmt.Errorf("reading the runs of the revoked version: %w", err)
	}
	type runFacts struct {
		id            string
		cancelRequest sql.NullTime
	}
	runs := make([]runFacts, 0, 4)
	for rows.Next() {
		var facts runFacts
		if err := rows.Scan(&facts.id, &facts.cancelRequest); err != nil {
			_ = rows.Close()
			return fmt.Errorf("reading the runs of the revoked version: %w", err)
		}
		runs = append(runs, facts)
	}
	if err := rows.Err(); err != nil {
		_ = rows.Close()
		return fmt.Errorf("reading the runs of the revoked version: %w", err)
	}
	if err := rows.Close(); err != nil {
		return fmt.Errorf("reading the runs of the revoked version: %w", err)
	}

	for _, run := range runs {
		if run.cancelRequest.Valid {
			// First write wins: a user/deadline cancel already recorded its intent
			// and its own event; revocation must not duplicate either.
			revocation.RunsAlreadyCancelling = append(revocation.RunsAlreadyCancelling, run.id)
			continue
		}
		if _, err := tx.ExecContext(ctx, `
			UPDATE platform.runs
			   SET cancel_requested_at = COALESCE(cancel_requested_at, now()),
			       error_code = CASE WHEN error_code IS NULL THEN $2 ELSE error_code END
			 WHERE id = $1::uuid`, run.id, VersionRevokedRunCode); err != nil {
			return fmt.Errorf("recording the revocation cancel intent: %w", err)
		}
		if _, err := execution.AppendRunStatusTx(ctx, tx, run.id); err != nil {
			return fmt.Errorf("appending the version-revoked run status event: %w", err)
		}
		revocation.RunsCancelRequested = append(revocation.RunsCancelRequested, run.id)
	}
	return nil
}
