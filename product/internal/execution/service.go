package execution

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"

	"orchara/platform/internal/compiler"
	"orchara/platform/internal/objectstore"
	"orchara/platform/internal/uuid"
)

// This file is the decision half of the package: request normalisation, the
// claim transaction's ordering, manifest/input verification and signed links,
// heartbeat control facts and the exit-evidence classification. It issues no
// SQL of its own except through store.go primitives.

// Claim answers the contract's claimJob.
//
// One transaction does all of the following, or none of it:
//
//  1. pick one ready job whose run is queued and whose snapshot compiler version
//     is supported (FOR UPDATE SKIP LOCKED);
//  2. take one global execution slot atomically (used < capacity);
//  3. take/re-arm the holder allocation, insert the fresh attempt row;
//  4. lease the job and name the token; move the run queued->running exactly once;
//  5. re-verify the sealed manifest bytes and mint real sealed GET links.
//
// A nil lease with a nil error is "no compatible job or no capacity": the HTTP
// layer answers 204 and the database is unchanged. Every error path rolls back,
// so a failed verification/signing never leaves a lease behind.
func (s *Service) Claim(ctx context.Context, request ClaimRequest) (*Lease, error) {
	normalized, err := validateClaimRequest(request)
	if err != nil {
		return nil, err
	}

	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return nil, fmt.Errorf("starting the claim transaction: %w", err)
	}
	defer func() { _ = tx.Rollback() }()

	candidate, err := selectClaimCandidateTx(ctx, tx, normalized.SupportedCompilers)
	if err != nil {
		return nil, err
	}
	if candidate == nil {
		return nil, nil
	}

	used, capacity, err := lockQuotaCounterTx(ctx, tx)
	if err != nil {
		return nil, err
	}
	if used >= capacity {
		// Nothing has been written yet; the row locks are simply released.
		return nil, nil
	}
	if err := takeQuotaSlotTx(ctx, tx); err != nil {
		if errors.Is(err, errNotEnoughCapacity) {
			return nil, nil
		}
		return nil, err
	}
	holderKey := "job:" + candidate.JobID
	if err := takeAllocationTx(ctx, tx, holderKey); err != nil {
		return nil, err
	}

	token := uuid.NewString()
	leaseDeadline, _, _, err := insertAttemptTx(
		ctx, tx, candidate, normalized.WorkerID, normalized.ExecutionUnitID, token,
		s.LeaseTTL.Seconds(),
	)
	if err != nil {
		return nil, err
	}
	if err := leaseJobTx(ctx, tx, candidate.JobID, token); err != nil {
		return nil, err
	}
	if _, err := startRunTx(ctx, tx, candidate.RunID); err != nil {
		return nil, err
	}

	lease, err := s.buildLease(ctx, tx, candidate, token, leaseDeadline)
	if err != nil {
		return nil, err
	}
	if err := tx.Commit(); err != nil {
		return nil, fmt.Errorf("committing the claim transaction: %w", err)
	}
	return lease, nil
}

// buildLease verifies the sealed manifest bytes and mints the two real sealed
// GET links. It runs inside the claim transaction on purpose: if the bytes or
// signer are unavailable the transaction rolls back and no lease exists.
func (s *Service) buildLease(
	ctx context.Context,
	tx *sql.Tx,
	candidate *claimCandidate,
	token, leaseDeadline string,
) (*Lease, error) {
	if s.Objects == nil {
		return nil, fmt.Errorf("%w: the object store is not configured", ErrObjectStoreUnavailable)
	}
	if !strings.EqualFold(candidate.ManifestSHA256, candidate.RunManifestSHA256) {
		return nil, fmt.Errorf(
			"%w: the snapshot and run manifest hashes disagree", ErrObjectStoreUnavailable,
		)
	}
	manifestFacts, err := loadArtifactTx(ctx, tx, candidate.ManifestArtifactID)
	if err != nil {
		return nil, err
	}
	if err := requireReadySealed(manifestFacts); err != nil {
		return nil, err
	}
	if !strings.EqualFold(manifestFacts.SHA256.String, candidate.ManifestSHA256) {
		return nil, fmt.Errorf(
			"%w: the manifest artifact hash does not match the snapshot", ErrObjectStoreUnavailable,
		)
	}
	// The authority is the sealed byte sequence, never the JSONB projection.
	if _, _, err := compiler.VerifyManifestBytes(s.Objects, compiler.ArtifactRef{
		ObjectKey: manifestFacts.ObjectKey,
		SHA256:    candidate.ManifestSHA256,
		SizeBytes: manifestFacts.SizeBytes.Int64,
	}); err != nil {
		return nil, fmt.Errorf("%w: %v", ErrObjectStoreUnavailable, err)
	}
	manifestURL, err := s.signSealed(ctx, tx, candidate.ManifestArtifactID, candidate.ManifestSHA256)
	if err != nil {
		return nil, err
	}
	inputURL, err := s.signSealed(ctx, tx, candidate.InputArtifactID, "")
	if err != nil {
		return nil, err
	}

	lease := &Lease{
		JobID:               candidate.JobID,
		RunID:               candidate.RunID,
		AttemptToken:        token,
		LeaseDeadline:       leaseDeadline,
		Manifest:            json.RawMessage(candidate.Manifest),
		ProtocolVersion:     "1.0",
		JobKind:             candidate.JobKind,
		ExecutionSnapshotID: candidate.SnapshotID,
		ManifestSHA256:      candidate.ManifestSHA256,
		ManifestURL:         manifestURL,
		InputArtifactID:     candidate.InputArtifactID,
		InputURL:            inputURL,
	}
	if candidate.ResumeArtifactID.Valid {
		// A resume job's payload is a real sealed artifact. T029 never creates a
		// resume job; T031 does, and this branch is already the contract shape.
		resumeID := candidate.ResumeArtifactID.String
		resumeURL, err := s.signSealed(ctx, tx, resumeID, "")
		if err != nil {
			return nil, err
		}
		lease.ResumeArtifactID = &resumeID
		lease.ResumeURL = &resumeURL
	}
	return lease, nil
}

// signSealed mints one short-lived GET link for a ready sealed artifact.
func (s *Service) signSealed(
	ctx context.Context,
	tx *sql.Tx,
	artifactID, expectedSHA256 string,
) (string, error) {
	if s.Signer == nil || s.BaseURL == "" {
		return "", ErrObjectSigningUnavailable
	}
	facts, err := loadArtifactTx(ctx, tx, artifactID)
	if err != nil {
		return "", err
	}
	if err := requireReadySealed(facts); err != nil {
		return "", err
	}
	if expectedSHA256 != "" && !strings.EqualFold(facts.SHA256.String, expectedSHA256) {
		return "", fmt.Errorf("%w: artifact bytes do not match the frozen hash", ErrObjectStoreUnavailable)
	}
	token, _, err := s.Signer.Sign(
		objectstore.OpGet, facts.ObjectKey, facts.ID, facts.SizeBytes.Int64,
	)
	if err != nil {
		if errors.Is(err, objectstore.ErrSignerNotConfigured) {
			return "", ErrObjectSigningUnavailable
		}
		return "", fmt.Errorf("%w: %v", ErrObjectStoreUnavailable, err)
	}
	return s.linkURL(token), nil
}

// requireReadySealed rejects any artifact that cannot be served as sealed bytes.
func requireReadySealed(facts artifactFacts) error {
	switch {
	case facts.State != "ready":
		return fmt.Errorf("%w: artifact state is %q", ErrObjectStoreUnavailable, facts.State)
	case facts.Deleted.Valid:
		return fmt.Errorf("%w: artifact is deleted", ErrObjectStoreUnavailable)
	case !facts.SHA256.Valid || len(facts.SHA256.String) != 64:
		return fmt.Errorf("%w: artifact has no recorded hash", ErrObjectStoreUnavailable)
	case !facts.SizeBytes.Valid || facts.SizeBytes.Int64 < 0:
		return fmt.Errorf("%w: artifact has no recorded size", ErrObjectStoreUnavailable)
	case !strings.HasPrefix(facts.ObjectKey, "sealed/"):
		return fmt.Errorf("%w: artifact is not sealed", ErrObjectStoreUnavailable)
	}
	return nil
}

// Heartbeat renews one live attempt's lease and returns the contract's control
// facts.
//
// Staleness is one answer: an unknown/wrong-run token, a revoked or exited
// attempt, an expired lease and a different execution unit all surface as
// ErrStaleAttempt (409), so the worker re-claims instead of guessing.
func (s *Service) Heartbeat(ctx context.Context, request HeartbeatRequest) (*HeartbeatReply, error) {
	normalized, err := validateHeartbeatRequest(request)
	if err != nil {
		return nil, err
	}
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return nil, fmt.Errorf("starting the heartbeat transaction: %w", err)
	}
	defer func() { _ = tx.Rollback() }()

	facts, err := loadHeartbeatAttemptTx(ctx, tx, normalized.RunID, normalized.AttemptToken)
	if err != nil {
		return nil, err
	}
	if facts.ExitedAt.Valid || facts.RevokedAt.Valid || !facts.LeaseLive {
		return nil, ErrStaleAttempt
	}
	if facts.ExecutionUnitID != normalized.ExecutionUnitID {
		return nil, ErrStaleAttempt
	}
	deadline, err := renewLeaseTx(
		ctx, tx, normalized.AttemptToken, s.LeaseTTL.Seconds(), normalized.CheckpointConnections,
	)
	if err != nil {
		return nil, err
	}
	if err := tx.Commit(); err != nil {
		return nil, fmt.Errorf("committing the heartbeat transaction: %w", err)
	}
	return &HeartbeatReply{
		LeaseDeadline: deadline,
		// Cancel is intent, not a terminal state: the worker stops scheduling and
		// the cancel endpoint (T038) owns the confirmed transition.
		CancelRequested: facts.CancelRequestedAt.Valid,
		PolicyRevision:  facts.PolicyRevision.Int64,
		// The usage ledger is a later ticket. Until then the honest remaining
		// budget is the run's whole limit, not a fabricated spend figure.
		BudgetRemaining: facts.BudgetLimit,
	}, nil
}

// ConfirmExit records supervisor exit evidence.
//
// The started=false case is this ticket's "startup failed, provably never
// started" path: the attempt is exited and revoked, the slot is released, the
// job returns to ready and the run returns to queued without inventing a
// terminal result. Any other evidence holds the job: T031's finalizer (result
// proposal, sandbox/drain proofs, terminal state, resume) decides next.
//
// The call is idempotent for identical evidence (JSONB equality) and answers
// ErrEvidenceConflict for different evidence on an already-exited attempt.
func (s *Service) ConfirmExit(ctx context.Context, request ExitRequest) (*ExitResult, error) {
	normalized, started, err := validateExitRequest(request)
	if err != nil {
		return nil, err
	}
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return nil, fmt.Errorf("starting the attempt-exit transaction: %w", err)
	}
	defer func() { _ = tx.Rollback() }()

	facts, err := loadAttemptForExitTx(ctx, tx, normalized.AttemptID, normalized.Evidence)
	if err != nil {
		return nil, err
	}
	if facts.ExecutionUnitID != normalized.ExecutionUnitID {
		return nil, ErrStaleAttempt
	}
	if facts.ExitedAt.Valid {
		if facts.EvidenceSame {
			// Identical replay: nothing changes and nothing is released twice.
			return &ExitResult{ID: normalized.AttemptID, State: "exited"}, nil
		}
		return nil, ErrEvidenceConflict
	}
	if facts.RevokedAt.Valid || !facts.LeaseLive {
		return nil, ErrStaleAttempt
	}
	if facts.JobState != "leased" || facts.RunState != "running" {
		// Cancel/recovery races are T031/T038's model; this ticket refuses rather
		// than overwrite a state it does not own.
		return nil, ErrStaleAttempt
	}

	if err := recordExitTx(ctx, tx, normalized.AttemptID, normalized.Evidence); err != nil {
		return nil, err
	}
	released, err := releaseAllocationTx(ctx, tx, "job:"+facts.JobID)
	if err != nil {
		return nil, err
	}
	if !released {
		return nil, ErrAllocationConflict
	}
	if started {
		if err := holdJobTx(ctx, tx, facts.JobID); err != nil {
			return nil, err
		}
	} else {
		if err := returnJobToQueueTx(ctx, tx, facts.JobID, facts.RunID); err != nil {
			return nil, err
		}
	}
	if err := tx.Commit(); err != nil {
		return nil, fmt.Errorf("committing the attempt-exit transaction: %w", err)
	}
	return &ExitResult{ID: normalized.AttemptID, State: "exited"}, nil
}

// LiveLease returns the current attempt's non-secret lease facts for one run, or
// nil when no attempt is live. It is the owner-facing run-detail extension.
func (s *Service) LiveLease(ctx context.Context, runID string) (*LiveLease, error) {
	if !uuid.Valid(strings.TrimSpace(runID)) {
		return nil, nil
	}
	return liveLease(ctx, s.DB, runID)
}

// ---------------------------------------------------------------------------
// request validation (422 validation_failed with a located field)
// ---------------------------------------------------------------------------

func validateClaimRequest(request ClaimRequest) (ClaimRequest, error) {
	workerID, err := validateIdentifier("worker_id", request.WorkerID)
	if err != nil {
		return ClaimRequest{}, err
	}
	executionUnitID, err := validateIdentifier("execution_unit_id", request.ExecutionUnitID)
	if err != nil {
		return ClaimRequest{}, err
	}
	if len(request.SupportedCompilers) == 0 {
		return ClaimRequest{}, &ValidationError{
			Field:   "supported_compilers",
			Message: "supported_compilers must name at least one compiler version",
		}
	}
	if len(request.SupportedCompilers) > maxSupportedCompilers {
		return ClaimRequest{}, &ValidationError{
			Field:   "supported_compilers",
			Message: fmt.Sprintf("supported_compilers must contain at most %d entries", maxSupportedCompilers),
		}
	}
	seen := make(map[string]bool, len(request.SupportedCompilers))
	compilers := make([]string, 0, len(request.SupportedCompilers))
	for _, raw := range request.SupportedCompilers {
		value := strings.TrimSpace(raw)
		if value == "" {
			return ClaimRequest{}, &ValidationError{
				Field:   "supported_compilers",
				Message: "supported_compilers entries must not be blank",
			}
		}
		if len(value) > maxIdentifierLength || !isPrintable(value) {
			return ClaimRequest{}, &ValidationError{
				Field:   "supported_compilers",
				Message: "supported_compilers entries must be printable and at most 128 characters",
			}
		}
		if seen[value] {
			return ClaimRequest{}, &ValidationError{
				Field:   "supported_compilers",
				Message: "supported_compilers must not repeat a compiler version",
			}
		}
		seen[value] = true
		compilers = append(compilers, value)
	}
	return ClaimRequest{
		WorkerID:           workerID,
		SupportedCompilers: compilers,
		ExecutionUnitID:    executionUnitID,
	}, nil
}

func validateHeartbeatRequest(request HeartbeatRequest) (HeartbeatRequest, error) {
	runID := strings.TrimSpace(request.RunID)
	if !uuid.Valid(runID) {
		return HeartbeatRequest{}, &ValidationError{Field: "run_id", Message: "run_id must be a UUID"}
	}
	token := strings.TrimSpace(request.AttemptToken)
	if !uuid.Valid(token) {
		return HeartbeatRequest{}, &ValidationError{
			Field: "attempt_token", Message: "attempt_token must be a UUID",
		}
	}
	executionUnitID, err := validateIdentifier("execution_unit_id", request.ExecutionUnitID)
	if err != nil {
		return HeartbeatRequest{}, err
	}
	connections, err := validateCheckpointConnections(request.CheckpointConnections)
	if err != nil {
		return HeartbeatRequest{}, err
	}
	return HeartbeatRequest{
		RunID:                 runID,
		AttemptToken:          token,
		ExecutionUnitID:       executionUnitID,
		CheckpointConnections: connections,
	}, nil
}

// checkpointConnection is the contract's heartbeat connection entry.
type checkpointConnection struct {
	PID             *int64  `json:"pid"`
	BackendStart    *string `json:"backend_start"`
	ApplicationName *string `json:"application_name"`
}

func validateCheckpointConnections(raw []byte) ([]byte, error) {
	if len(raw) == 0 {
		return nil, nil
	}
	var entries []json.RawMessage
	if err := json.Unmarshal(raw, &entries); err != nil {
		return nil, &ValidationError{
			Field:   "checkpoint_connections",
			Message: "checkpoint_connections must be an array of objects",
		}
	}
	if entries == nil {
		return nil, &ValidationError{
			Field:   "checkpoint_connections",
			Message: "checkpoint_connections must be an array of objects",
		}
	}
	if len(entries) > 32 {
		return nil, &ValidationError{
			Field:   "checkpoint_connections",
			Message: "checkpoint_connections must contain at most 32 entries",
		}
	}
	normalized := make([]map[string]any, 0, len(entries))
	for _, entry := range entries {
		var connection checkpointConnection
		decoder := json.NewDecoder(strings.NewReader(string(entry)))
		decoder.DisallowUnknownFields()
		if err := decoder.Decode(&connection); err != nil {
			return nil, &ValidationError{
				Field:   "checkpoint_connections",
				Message: "each connection must be {pid, backend_start, application_name}",
			}
		}
		if connection.PID == nil || *connection.PID < 1 {
			return nil, &ValidationError{
				Field:   "checkpoint_connections",
				Message: "connection pid must be a positive integer",
			}
		}
		if connection.BackendStart == nil {
			return nil, &ValidationError{
				Field:   "checkpoint_connections",
				Message: "connection backend_start is required",
			}
		}
		if _, err := time.Parse(time.RFC3339, *connection.BackendStart); err != nil {
			return nil, &ValidationError{
				Field:   "checkpoint_connections",
				Message: "connection backend_start must be an RFC3339 timestamp",
			}
		}
		if connection.ApplicationName == nil {
			return nil, &ValidationError{
				Field:   "checkpoint_connections",
				Message: "connection application_name is required",
			}
		}
		normalized = append(normalized, map[string]any{
			"pid":              *connection.PID,
			"backend_start":    *connection.BackendStart,
			"application_name": *connection.ApplicationName,
		})
	}
	encoded, err := json.Marshal(normalized)
	if err != nil {
		return nil, fmt.Errorf("encoding checkpoint connections: %w", err)
	}
	return encoded, nil
}

func validateExitRequest(request ExitRequest) (ExitRequest, bool, error) {
	attemptID := strings.TrimSpace(request.AttemptID)
	if !uuid.Valid(attemptID) {
		return ExitRequest{}, false, ErrAttemptNotFound
	}
	executionUnitID, err := validateIdentifier("execution_unit_id", request.ExecutionUnitID)
	if err != nil {
		return ExitRequest{}, false, err
	}
	if len(request.Evidence) == 0 {
		return ExitRequest{}, false, &ValidationError{
			Field: "evidence", Message: "evidence must be a JSON object",
		}
	}
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(request.Evidence, &fields); err != nil || fields == nil {
		return ExitRequest{}, false, &ValidationError{
			Field: "evidence", Message: "evidence must be a JSON object",
		}
	}
	// Only an explicit started=false is proof that the execution unit never
	// began work. An absent key proves nothing, so it holds the job: the process
	// may have started and the F03 finalizer must decide.
	started := true
	if raw, present := fields["started"]; present {
		if err := json.Unmarshal(raw, &started); err != nil {
			return ExitRequest{}, false, &ValidationError{
				Field: "evidence.started", Message: "evidence.started must be a boolean",
			}
		}
	}
	return ExitRequest{
		AttemptID:       attemptID,
		ExecutionUnitID: executionUnitID,
		Evidence:        request.Evidence,
	}, started, nil
}

// validateIdentifier trims and bounds one free-form identifier.
func validateIdentifier(field, raw string) (string, error) {
	value := strings.TrimSpace(raw)
	if value == "" {
		return "", &ValidationError{Field: field, Message: field + " is required"}
	}
	if len(value) > maxIdentifierLength || !isPrintable(value) {
		return "", &ValidationError{
			Field:   field,
			Message: field + " must be printable and at most 128 characters",
		}
	}
	return value, nil
}

func isPrintable(value string) bool {
	for _, character := range value {
		if character < 0x20 || character == 0x7f {
			return false
		}
	}
	return true
}
