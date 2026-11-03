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
// layer answers 204 and no execution state changes. T034 adds one deliberate
// exception: a queued Run whose accumulated active time already reached its frozen
// limit is committed failed(deadline_exceeded) (its never-run job becomes void)
// and the same 204 tells the caller there is no lease. Every error path rolls
// back, so a failed verification/signing never leaves a lease behind.
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
	// T034: an already-over queued Run is never leased. Its accumulated active
	// time (settled by its previous attempt) already reached the frozen limit, so
	// a new attempt would only burn more time without a chance to finish. The Run
	// is committed failed(deadline_exceeded), the never-run job is voided and the
	// claim answers 204 (no lease, nothing to execute).
	if deadlineExceeded(candidate.MaxActiveMS, candidate.ActiveElapsedMS) {
		if err := failQueuedRunOnDeadlineTx(
			ctx, tx, candidate.RunID, candidate.Revision,
		); err != nil {
			return nil, err
		}
		if err := voidJobTx(ctx, tx, candidate.JobID); err != nil {
			return nil, err
		}
		if _, err := AppendRunStatusTx(ctx, tx, candidate.RunID); err != nil {
			return nil, err
		}
		if err := tx.Commit(); err != nil {
			return nil, fmt.Errorf("committing the overdue claim refusal: %w", err)
		}
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
	// T032: the queued->running transition and its run.status event are one
	// transaction. A failure to record the fact rolls the claim back.
	if _, err := AppendRunStatusTx(ctx, tx, candidate.RunID); err != nil {
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

	// Lock the Run first: the heartbeat may record the deadline intent on the Run
	// row, and taking the lock in the same order as the result/finalizer paths
	// (run -> attempt) avoids a lock-order inversion under a concurrent result.
	runFacts, err := lockRunActiveFactsTx(ctx, tx, normalized.RunID)
	if err != nil {
		if errors.Is(err, ErrRunNotFound) {
			// The heartbeat protocol keeps one answer for every stale/unknown
			// credential: a nonexistent Run is not an enumeration oracle.
			return nil, ErrStaleAttempt
		}
		return nil, err
	}
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
	deadlineState := deadlineStateOf(runFacts, facts.LiveActiveMS, !facts.ActiveSettled)
	if deadlineState.Over {
		// T034 pre-cancel deadline path: record the intent once, keep renewing the
		// lease so the supervisor can still confirm the exit that lets the
		// finalizer commit failed(deadline_exceeded). T036 refines cancel priority.
		if err := markDeadlineIntentTx(ctx, tx, normalized.RunID); err != nil {
			return nil, err
		}
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
		// the cancel endpoint (T036) owns the confirmed transition. A deadline sets
		// the same intent so the worker sees one control flag.
		CancelRequested: runFacts.CancelRequestedAt.Valid || deadlineState.Over,
		PolicyRevision:  facts.PolicyRevision.Int64,
		// The usage ledger is a later ticket. Until then the honest remaining
		// budget is the run's whole limit, not a fabricated spend figure.
		BudgetRemaining: facts.BudgetLimit,
	}, nil
}

// ConfirmExit records supervisor exit evidence and runs the unified finalizer
// when a result proposal is already stored.
//
// The started=false case is the "startup failed, provably never started" path:
// the attempt is exited and revoked, the slot is released, the job returns to
// ready and the run returns to queued without inventing a terminal result.
//
// Any started=true exit has already released the slot (the physical unit is
// gone). If a result proposal is present, finalizeRunTx commits succeeded/
// failed/needs_reconciliation or leaves a waiting proposal for T035. With no
// result the run is parked in recovery_pending, the job is held and nothing is
// requeued: T038 owns proving the old unit drained, and a late result completes
// the finalizer.
//
// The evidence must echo the claimed identity (protocol version, manifest hash,
// image digest); a mismatch is ErrIdentityMismatch (409
// execution_identity_mismatch) before any write, so a supervisor that ran the
// wrong image cannot certify this attempt. The call is idempotent for identical
// evidence (JSONB equality) and answers ErrEvidenceConflict for different
// evidence on an already-exited attempt.
func (s *Service) ConfirmExit(ctx context.Context, request ExitRequest) (*ExitResult, error) {
	normalized, evidence, err := validateExitRequest(request)
	if err != nil {
		return nil, err
	}
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return nil, fmt.Errorf("starting the attempt-exit transaction: %w", err)
	}
	defer func() { _ = tx.Rollback() }()

	// T035: lock order is Run -> attempt/job everywhere, so a concurrent waiting
	// resolution cannot deadlock with this exit.
	if err := lockExitRunTx(ctx, tx, normalized.AttemptID); err != nil {
		return nil, err
	}
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
	_, waitingExit := waitingKindForState(facts.RunState)
	switch {
	case waitingExit:
		// F03/T035: a waiting attempt was held (not requeued), so its exit evidence
		// is accepted while the job is held. The run keeps its waiting state until
		// the decision arrives; this call only records the supervisor's fact.
		if facts.JobState != "held" && facts.JobState != "leased" {
			return nil, ErrStaleAttempt
		}
	case facts.RunState == "cancelling":
		// F03/T036: a cancel-requested Run keeps its live attempt while the
		// supervisor stops it; the exit is exactly what lets the finalizer commit
		// cancelled (or park for unknown side effects).
		if facts.JobState != "held" && facts.JobState != "leased" {
			return nil, ErrStaleAttempt
		}
	case facts.JobState != "leased" || facts.RunState != "running":
		// Cancel/recovery races are T038's model; this handler refuses rather
		// than overwrite a state it does not own.
		return nil, ErrStaleAttempt
	}
	// The claimed identity check comes before any write: a wrong protocol,
	// manifest or image is a refusal, not an exit that later parks the run.
	if evidence.ProtocolVersion != LeaseProtocolVersion ||
		!strings.EqualFold(evidence.ManifestSHA256, facts.RunManifestSHA256) ||
		evidence.ImageDigest != facts.ImageDigest {
		return nil, ErrIdentityMismatch
	}

	if err := recordExitTx(ctx, tx, normalized.AttemptID, normalized.Evidence); err != nil {
		return nil, err
	}
	// The physical execution unit is gone, so its slot is free regardless of
	// what the result/finalizer does next. released_at IS NULL keeps it
	// exactly-once; the finalizer repeats the statement idempotently.
	if err := releaseAllocationOnceTx(ctx, tx, "job:"+facts.JobID); err != nil {
		return nil, err
	}
	// T034: the exit is the transition out of executing, so the attempt's active
	// interval is settled once here (idempotent if a result proposal already
	// settled it). The deadline decision is then made against the settled total.
	if _, err := settleAttemptActiveTx(ctx, tx, facts.RunID); err != nil {
		return nil, err
	}
	deadlineState, err := checkDeadlineTx(ctx, tx, facts.RunID)
	if err != nil {
		return nil, err
	}
	// T036: the cancel intent is a Run fact read under the lock. The recorded
	// deadline error_code is the durable "the limit was already observed" fact, so
	// it keeps priority even if a test/operator later relaxed max_active_ms.
	cancelIntent := facts.RunCancelRequestedAt.Valid || facts.RunState == "cancelling"
	deadlineRecorded := facts.RunErrorCode.Valid &&
		facts.RunErrorCode.String == DeadlineErrorCode
	switch {
	case waitingExit:
		// F03/T035: the supervisor confirmed the waiting attempt exited. Record the
		// fact and let the one resume transaction decide. Without a stored decision
		// this is a valid pending state and no event is emitted (the timeline
		// already carries the waiting transition).
		if err := clearWaitingJobTokenTx(ctx, tx, facts.JobID); err != nil {
			return nil, err
		}
		if err := confirmWaitingExitTx(ctx, tx, facts.RunID); err != nil {
			return nil, err
		}
		if _, err := s.maybeEnqueueResumeTx(ctx, tx, facts.RunID); err != nil {
			return nil, err
		}
	case deadlineState.Over || deadlineRecorded:
		// T034's pre-cancel deadline path keeps its failed(deadline_exceeded)
		// outcome and now goes through the same cancel/unknown guard: live children
		// park the Run (as before), unknown operations park it in
		// needs_reconciliation instead of faking a failure.
		finalized, err := s.finalizeRunTx(ctx, tx, finalizeInput{
			RunID:            facts.RunID,
			RunOwnerID:       facts.RunOwnerID,
			RunState:         facts.RunState,
			RunRevision:      facts.RunRevision,
			JobID:            facts.JobID,
			AttemptToken:     facts.Token,
			ReportedResult:   facts.ReportedResult,
			Exited:           true,
			DeadlineExceeded: true,
			NeverStarted:     !evidence.Started,
		})
		if err != nil {
			return nil, err
		}
		if !finalized {
			if err := parkRunForRecoveryTx(ctx, tx, facts.RunID, RecoveryReasonExitWithoutResult); err != nil {
				return nil, err
			}
			if err := holdJobTx(ctx, tx, facts.JobID); err != nil {
				return nil, err
			}
			if _, err := AppendRunStatusTx(ctx, tx, facts.RunID); err != nil {
				return nil, err
			}
		}
	case cancelIntent:
		// T036: cancel wins over an uncommitted result. A never-started unit is
		// voided (it never ran); a started unit completes. A guard that blocks the
		// terminal transition leaves the Run in cancelling with the job held.
		finalized, err := s.finalizeRunTx(ctx, tx, finalizeInput{
			RunID:          facts.RunID,
			RunOwnerID:     facts.RunOwnerID,
			RunState:       facts.RunState,
			RunRevision:    facts.RunRevision,
			JobID:          facts.JobID,
			AttemptToken:   facts.Token,
			ReportedResult: facts.ReportedResult,
			Exited:         true,
			NeverStarted:   !evidence.Started,
		})
		if err != nil {
			return nil, err
		}
		if !finalized {
			if err := holdJobTx(ctx, tx, facts.JobID); err != nil {
				return nil, err
			}
		}
	case !evidence.Started:
		if err := returnJobToQueueTx(ctx, tx, facts.JobID, facts.RunID); err != nil {
			return nil, err
		}
		// T032: running -> queued is a committed aggregate transition.
		if _, err := AppendRunStatusTx(ctx, tx, facts.RunID); err != nil {
			return nil, err
		}
	case len(facts.ReportedResult) > 0:
		if _, err := s.finalizeRunTx(ctx, tx, finalizeInput{
			RunID:          facts.RunID,
			RunOwnerID:     facts.RunOwnerID,
			RunState:       facts.RunState,
			RunRevision:    facts.RunRevision,
			JobID:          facts.JobID,
			AttemptToken:   facts.Token,
			ReportedResult: facts.ReportedResult,
			Exited:         true,
		}); err != nil {
			return nil, err
		}
	default:
		// Exit with no result: park for recovery, hold the job, never requeue.
		if err := parkRunForRecoveryTx(ctx, tx, facts.RunID, RecoveryReasonExitWithoutResult); err != nil {
			return nil, err
		}
		if err := holdJobTx(ctx, tx, facts.JobID); err != nil {
			return nil, err
		}
		if _, err := AppendRunStatusTx(ctx, tx, facts.RunID); err != nil {
			return nil, err
		}
	}
	if err := tx.Commit(); err != nil {
		return nil, fmt.Errorf("committing the attempt-exit transaction: %w", err)
	}
	return &ExitResult{ID: normalized.AttemptID, State: "exited"}, nil
}

// finalizeInput is the state the finalizer decides on. The caller has already
// locked the run/job/attempt and checked the evidence.
type finalizeInput struct {
	RunID          string
	RunOwnerID     string
	RunState       string
	RunRevision    int64
	JobID          string
	AttemptToken   string
	ReportedResult []byte
	Exited         bool
	// DeadlineExceeded tells the finalizer that the pre-cancel deadline path is
	// already decided (T034). The finalizer re-checks the settled total anyway, so
	// a caller that forgot the flag cannot make an overdue run succeed.
	DeadlineExceeded bool
	// NeverStarted is the started=false exit of a cancel-requested Run: the unit
	// provably never ran, so the job is void rather than completed.
	NeverStarted bool
}

// finalizeRunTx is the one idempotent finalizer for result+exit, in either
// arrival order. It requires both pieces of evidence; when only one is present
// it does nothing. When both are present it decides in this fixed priority:
//
//  1. **live child Runs** block every terminal transition: the job is held and
//     the Run keeps its non-terminal state (the caller decides whether a deadline
//     parks it for recovery, as T034 does);
//  2. **unresolved unknown operations** take priority over deadline/cancel/result
//     (EXECUTION §5/§7): the Run commits needs_reconciliation with the stable
//     unknown_operations reason and never claims success/failure;
//  3. the **active-time deadline** keeps its failed(deadline_exceeded) outcome,
//     even when a cancel intent was recorded first;
//  4. **cancel_requested_at** makes cancelled win over an uncommitted result
//     proposal (including a waiting proposal); a started=false unit is voided;
//  5. otherwise the stored proposal decides succeeded/failed/needs_reconciliation
//     or leaves a waiting_* proposal to T035.
//
// The run CAS on state+revision is what makes a racing duplicate a conflict
// rather than a second terminal write.
func (s *Service) finalizeRunTx(
	ctx context.Context, tx *sql.Tx, input finalizeInput,
) (bool, error) {
	if !input.Exited {
		return false, nil
	}
	switch input.RunState {
	case "running", "recovery_pending", "cancelling", "needs_reconciliation":
		// cancelling: the T036 exit path. needs_reconciliation: resolving the last
		// unknown re-invokes this same finalizer so a cancelled Run is committed
		// exactly once. Any other state (terminal/waiting/queued) is a no-op.
	default:
		return false, nil
	}
	// T034: every finalizer entry has left executing, so the attempt interval is
	// settled once here (idempotent when the caller already settled it). The
	// deadline decision is then made against the settled total plus the recorded
	// intent: an overdue Run never commits a success, even if a result proposal
	// was accepted earlier.
	if _, err := settleAttemptActiveTx(ctx, tx, input.RunID); err != nil {
		return false, err
	}
	deadlineState, err := checkDeadlineTx(ctx, tx, input.RunID)
	if err != nil {
		return false, err
	}
	cancelAt, err := runCancelIntentTx(ctx, tx, input.RunID)
	if err != nil {
		return false, err
	}
	recordedErrorCode, err := runErrorCodeTx(ctx, tx, input.RunID)
	if err != nil {
		return false, err
	}
	deadline := input.DeadlineExceeded || deadlineState.Over ||
		(recordedErrorCode.Valid && recordedErrorCode.String == DeadlineErrorCode)
	cancelIntent := cancelAt.Valid || input.RunState == "cancelling"
	if !deadline && !cancelIntent && !input.NeverStarted && len(input.ReportedResult) == 0 {
		return false, nil
	}

	var proposal struct {
		SuggestedState   string  `json:"suggested_state"`
		OutputArtifactID *string `json:"output_artifact_id"`
		ErrorCode        *string `json:"error_code"`
	}
	if !deadline && !input.NeverStarted && len(input.ReportedResult) > 0 {
		if err := json.Unmarshal(input.ReportedResult, &proposal); err != nil {
			return false, fmt.Errorf("decoding the stored result proposal: %w", err)
		}
	}

	liveChildren, err := hasLiveChildrenTx(ctx, tx, input.RunID)
	if err != nil {
		return false, err
	}
	unknownOperations, err := hasUnknownOperationsTx(ctx, tx, input.RunID)
	if err != nil {
		return false, err
	}
	if liveChildren {
		// A controlled unit (child Run) is still running: keep every piece of
		// evidence and stop dispatching. A deadline caller parks the Run; a cancel
		// caller leaves it cancelling.
		if err := holdJobTx(ctx, tx, input.JobID); err != nil {
			return false, err
		}
		return false, nil
	}

	outcome := runOutcome{}
	switch {
	case unknownOperations:
		// Unknown external writes are never disguised as success/failure/cancel.
		code := UnknownOperationsErrorCode
		outcome = runOutcome{State: "needs_reconciliation", ErrorCode: &code}
	case deadline:
		// Pre-cancel deadline outcome: failed with the stable code, no output
		// artifact, terminal and finished.
		code := DeadlineErrorCode
		outcome = runOutcome{State: "failed", ErrorCode: &code, Finished: true}
	case cancelIntent || input.NeverStarted:
		// T036: cancelled wins over an uncommitted result. The state is the
		// terminal reason; error_code is cleared so a previous unknown/deadline
		// marker cannot outlive the confirmed cancel.
		outcome = runOutcome{State: "cancelled", Finished: true}
	case len(input.ReportedResult) == 0:
		return false, nil
	case proposal.SuggestedState == "waiting_approval" ||
		proposal.SuggestedState == "waiting_children":
		// Stored, not terminal: T035 turns the waiting proposal into the waiting
		// state and the single resume job. The execution slot is already free.
		if err := holdJobTx(ctx, tx, input.JobID); err != nil {
			return false, err
		}
		return false, nil
	case proposal.SuggestedState == "succeeded":
		if proposal.OutputArtifactID == nil {
			return false, errors.New("the stored succeeded proposal has no output artifact")
		}
		if err := checkOutputArtifactTx(
			ctx, tx, input.RunID, input.RunOwnerID, *proposal.OutputArtifactID,
		); err != nil {
			// The artifact was acceptable when the proposal was stored; if it is
			// no longer owned/ready/linked the platform cannot honestly claim
			// success, so the run parks for reconciliation instead.
			code := "output_artifact_invalid"
			outcome = runOutcome{State: "needs_reconciliation", ErrorCode: &code}
			break
		}
		outcome = runOutcome{
			State:            "succeeded",
			OutputArtifactID: proposal.OutputArtifactID,
			Finished:         true,
		}
	case proposal.SuggestedState == "failed":
		if proposal.ErrorCode == nil {
			return false, errors.New("the stored failed proposal has no error_code")
		}
		outcome = runOutcome{
			State:     "failed",
			ErrorCode: proposal.ErrorCode,
			Finished:  true,
		}
	case proposal.SuggestedState == "needs_reconciliation":
		outcome = runOutcome{
			State:     "needs_reconciliation",
			ErrorCode: proposal.ErrorCode,
		}
	default:
		return false, fmt.Errorf("unknown stored result proposal %q", proposal.SuggestedState)
	}

	// The job transition: a completed execution is `completed`, a unit that
	// provably never started (started=false cancel) is `void`.
	if input.NeverStarted {
		if err := voidJobForCancelTx(ctx, tx, input.JobID); err != nil {
			return false, err
		}
	} else if err := completeJobTx(ctx, tx, input.JobID); err != nil {
		return false, err
	}
	if err := revokeAttemptTx(ctx, tx, input.AttemptToken); err != nil {
		return false, err
	}
	if err := releaseAllocationOnceTx(ctx, tx, "job:"+input.JobID); err != nil {
		return false, err
	}
	if err := updateRunOutcomeTx(
		ctx, tx, input.RunID, input.RunState, input.RunRevision, outcome,
	); err != nil {
		return false, err
	}
	// T032: the committed terminal/non-terminal outcome is projected in the same
	// transaction. Both entry orders (result->exit, exit->result) converge here,
	// so exactly one outcome event exists per actual transition; a repeated
	// finalize returns before this point.
	if _, err := AppendRunStatusTx(ctx, tx, input.RunID); err != nil {
		return false, err
	}
	return true, nil
}

// LiveLease returns the current attempt's non-secret lease facts for one run, or
// nil when no attempt is live. It is the owner-facing run-detail extension.
func (s *Service) LiveLease(ctx context.Context, runID string) (*LiveLease, error) {
	if !uuid.Valid(strings.TrimSpace(runID)) {
		return nil, nil
	}
	return liveLease(ctx, s.DB, runID)
}

// CancelRequestedAt returns the recorded cancel intent (nil when none). The
// public run detail uses it to distinguish "stopping" from a terminal state
// without inventing T036's cancel transition.
func (s *Service) CancelRequestedAt(ctx context.Context, runID string) (*time.Time, error) {
	if !uuid.Valid(strings.TrimSpace(runID)) {
		return nil, nil
	}
	return runCancelRequestedAt(ctx, s.DB, runID)
}

// SubmitResult stores one worker result proposal. It is deliberately not a
// completion: the attempt keeps its credential, the job stays leased, the
// allocation stays held and the run only moves to phase=finalizing. The unified
// finalizer (T031) commits the terminal state once the supervisor's exit
// evidence is present, in whichever order the two calls arrive.
//
// Order inside the transaction:
//
//  1. lock the run: terminal is 409 run_terminal (a late result never revives),
//     a non-executing non-terminal state is 409 run_state_conflict;
//  2. lock the attempt *bound to this run*: a foreign run's token, a superseded
//     attempt or a requeued (started=false) job is 409 stale_attempt;
//  3. an identical stored proposal is the idempotent 200 (no write); different
//     content is 409 event_conflict;
//  4. a succeeded proposal's output artifact must be owned/ready/linked;
//  5. store the proposal, set phase=finalizing (never state/revision).
func (s *Service) SubmitResult(
	ctx context.Context, runID string, result WorkerResult,
) (*ResultOutcome, error) {
	runID = strings.TrimSpace(runID)
	if !uuid.Valid(runID) {
		return nil, ErrRunNotFound
	}
	normalized, err := validateWorkerResult(result)
	if err != nil {
		return nil, err
	}
	proposal, err := json.Marshal(normalized)
	if err != nil {
		return nil, fmt.Errorf("encoding the result proposal: %w", err)
	}

	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return nil, fmt.Errorf("starting the result transaction: %w", err)
	}
	defer func() { _ = tx.Rollback() }()

	run, err := lockRunTx(ctx, tx, runID)
	if err != nil {
		return nil, err
	}
	// A Run that already committed the waiting transition only accepts the exact
	// same proposal as an idempotent replay. T035 owns that transition; a second
	// different proposal is never allowed to move the goalposts.
	if run.State == "waiting_approval" || run.State == "waiting_children" {
		attempt, err := lockAttemptForResultTx(ctx, tx, run.ID, normalized.AttemptToken)
		if err != nil {
			return nil, err
		}
		if attempt.Superseded {
			return nil, ErrStaleAttempt
		}
		if len(attempt.ReportedResult) > 0 {
			if sameProposal(attempt.ReportedResult, proposal) {
				return &ResultOutcome{RunID: run.ID, OwnerID: run.OwnerID}, nil
			}
			return nil, ErrEventConflict
		}
		return nil, ErrRunStateConflict
	}
	switch run.State {
	case "succeeded", "failed", "cancelled":
		return nil, ErrRunTerminal
	case "running", "recovery_pending":
		// running: the normal proposal. recovery_pending: the supervisor already
		// proved the exit but the result was in flight; this call can complete
		// the finalizer, it never dispatches a new attempt.
	default:
		return nil, ErrRunStateConflict
	}

	attempt, err := lockAttemptForResultTx(ctx, tx, runID, normalized.AttemptToken)
	if err != nil {
		return nil, err
	}
	if attempt.Superseded {
		return nil, ErrStaleAttempt
	}
	switch attempt.JobState {
	case "leased":
		if !attempt.CurrentAttemptToken.Valid || attempt.CurrentAttemptToken.String != normalized.AttemptToken {
			return nil, ErrStaleAttempt
		}
	case "held":
		// An exit without result parks the job with the token cleared; a held job
		// that names a different token is already serving another attempt.
		if attempt.CurrentAttemptToken.Valid && attempt.CurrentAttemptToken.String != normalized.AttemptToken {
			return nil, ErrStaleAttempt
		}
	default:
		return nil, ErrStaleAttempt
	}

	if len(attempt.ReportedResult) > 0 {
		if sameProposal(attempt.ReportedResult, proposal) {
			return &ResultOutcome{RunID: run.ID, OwnerID: run.OwnerID}, nil
		}
		return nil, ErrEventConflict
	}
	// T034: a result proposal is work. Settle the attempt's interval once and
	// refuse the proposal at/over the frozen limit, so a worker that reaches the
	// deadline cannot publish an outcome or keep working. The intent is recorded
	// in the same transaction; the supervisor's exit then drives the one
	// finalizer to failed(deadline_exceeded).
	if _, err := settleAttemptActiveTx(ctx, tx, run.ID); err != nil {
		return nil, err
	}
	deadlineState, err := checkDeadlineTx(ctx, tx, run.ID)
	if err != nil {
		return nil, err
	}
	if deadlineState.Over {
		if err := tx.Commit(); err != nil {
			return nil, fmt.Errorf("committing the deadline refusal: %w", err)
		}
		return nil, ErrDeadlineExceeded
	}
	if normalized.SuggestedState == "succeeded" {
		if err := checkOutputArtifactTx(
			ctx, tx, run.ID, run.OwnerID, *normalized.OutputArtifactID,
		); err != nil {
			return nil, err
		}
	}
	if err := storeResultTx(
		ctx, tx, normalized.AttemptToken, proposal, nullableString(normalized.CheckpointID),
	); err != nil {
		return nil, err
	}
	if normalized.SuggestedState == "waiting_approval" ||
		normalized.SuggestedState == "waiting_children" {
		// F03/T035: a waiting proposal is not a completion. The Run moves to
		// waiting_*, phase waiting (the T034 settlement already ran above and is
		// repeated idempotently inside), the job is held and the durable waiting
		// condition records the fixed checkpoint. The attempt may still be live;
		// only the supervisor's exit creates a resume.
		if err := s.applyWaitingTransitionTx(ctx, tx, run, attempt, normalized); err != nil {
			return nil, err
		}
		if attempt.ExitedAt.Valid {
			// Exit-first: the hand-over is already proven, so this same call records
			// the exit fact and runs the one resume transaction.
			if err := confirmWaitingExitTx(ctx, tx, run.ID); err != nil {
				return nil, err
			}
			if _, err := s.maybeEnqueueResumeTx(ctx, tx, run.ID); err != nil {
				return nil, err
			}
		}
		if err := tx.Commit(); err != nil {
			return nil, fmt.Errorf("committing the waiting result transaction: %w", err)
		}
		return &ResultOutcome{RunID: run.ID, OwnerID: run.OwnerID}, nil
	}
	if err := setRunFinalizingTx(ctx, tx, run.ID); err != nil {
		return nil, err
	}
	// T032: a first proposal is a committed fact (phase=finalizing, state still
	// running). The contract's run.status payload has no phase field, so this
	// event carries only {state:"running"}; the phase authority remains the
	// owner-only Run read. A proposal on a recovery_pending run changes nothing
	// and therefore emits nothing (the eventual terminal event explains it).
	if run.State == "running" {
		if _, err := AppendRunStatusTx(ctx, tx, run.ID); err != nil {
			return nil, err
		}
	}
	// Exit evidence may already be committed (the exit came first). In that
	// case this proposal completes the same finalizer; the run CAS makes the
	// transition happen exactly once.
	if attempt.ExitedAt.Valid {
		if _, err := s.finalizeRunTx(ctx, tx, finalizeInput{
			RunID:          run.ID,
			RunOwnerID:     run.OwnerID,
			RunState:       run.State,
			RunRevision:    run.Revision,
			JobID:          attempt.JobID,
			AttemptToken:   normalized.AttemptToken,
			ReportedResult: proposal,
			Exited:         true,
		}); err != nil {
			return nil, err
		}
	}
	if err := tx.Commit(); err != nil {
		return nil, fmt.Errorf("committing the result transaction: %w", err)
	}
	return &ResultOutcome{RunID: run.ID, OwnerID: run.OwnerID}, nil
}

// sameProposal reports whether two proposal documents carry the same JSON value.
// JSONB equality is the authority in the database; this comparison only answers
// the idempotent replay before writing.
func sameProposal(stored, proposed []byte) bool {
	var storedValue, proposedValue any
	if json.Unmarshal(stored, &storedValue) != nil || json.Unmarshal(proposed, &proposedValue) != nil {
		return false
	}
	storedCanonical, err := json.Marshal(storedValue)
	if err != nil {
		return false
	}
	proposedCanonical, err := json.Marshal(proposedValue)
	if err != nil {
		return false
	}
	return string(storedCanonical) == string(proposedCanonical)
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

// resultStates is the contract's suggested_state enum. The proposal vocabulary
// is closed: a worker cannot invent a state the platform does not know.
var resultStates = map[string]bool{
	"succeeded":            true,
	"failed":               true,
	"waiting_approval":     true,
	"waiting_children":     true,
	"needs_reconciliation": true,
}

// validateWorkerResult checks the contract body's shape and conditional
// requirements before any row is touched (all 422 validation_failed with a
// located field):
//
//	attempt_token    UUID
//	event_id         UUID (the proposal's idempotency identity)
//	suggested_state  one of the contract's five proposals
//	output_artifact_id  required for succeeded, UUID when present
//	checkpoint_id       required for waiting_approval/waiting_children
//	error_code          required for failed
//
// Artifact ownership/readiness/linkage is checked inside the transaction, where
// the run owner is known.
func validateWorkerResult(result WorkerResult) (WorkerResult, error) {
	normalized := result
	normalized.AttemptToken = strings.TrimSpace(result.AttemptToken)
	if !uuid.Valid(normalized.AttemptToken) {
		return WorkerResult{}, &ValidationError{
			Field: "attempt_token", Message: "attempt_token must be a UUID",
		}
	}
	normalized.EventID = strings.TrimSpace(result.EventID)
	if !uuid.Valid(normalized.EventID) {
		return WorkerResult{}, &ValidationError{
			Field: "event_id", Message: "event_id must be a UUID",
		}
	}
	normalized.SuggestedState = strings.TrimSpace(result.SuggestedState)
	if !resultStates[normalized.SuggestedState] {
		return WorkerResult{}, &ValidationError{
			Field:   "suggested_state",
			Message: "suggested_state must be succeeded, failed, waiting_approval, waiting_children or needs_reconciliation",
		}
	}

	normalized.OutputArtifactID = nil
	if result.OutputArtifactID != nil {
		value := strings.ToLower(strings.TrimSpace(*result.OutputArtifactID))
		if !uuid.Valid(value) {
			return WorkerResult{}, &ValidationError{
				Field: "output_artifact_id", Message: "output_artifact_id must be a UUID",
			}
		}
		normalized.OutputArtifactID = &value
	}

	normalized.CheckpointID = nil
	if result.CheckpointID != nil {
		value := strings.TrimSpace(*result.CheckpointID)
		if value == "" || len(value) > maxCheckpointID || !isPrintable(value) {
			return WorkerResult{}, &ValidationError{
				Field:   "checkpoint_id",
				Message: fmt.Sprintf("checkpoint_id must be printable and at most %d characters", maxCheckpointID),
			}
		}
		normalized.CheckpointID = &value
	}

	normalized.ErrorCode = nil
	if result.ErrorCode != nil {
		value := strings.TrimSpace(*result.ErrorCode)
		if value == "" || len(value) > maxErrorCode || !isPrintable(value) {
			return WorkerResult{}, &ValidationError{
				Field:   "error_code",
				Message: fmt.Sprintf("error_code must be printable and at most %d characters", maxErrorCode),
			}
		}
		normalized.ErrorCode = &value
	}

	switch normalized.SuggestedState {
	case "succeeded":
		if normalized.OutputArtifactID == nil {
			return WorkerResult{}, &ValidationError{
				Field:   "output_artifact_id",
				Message: "a succeeded proposal must name its output artifact",
			}
		}
	case "failed":
		if normalized.ErrorCode == nil {
			return WorkerResult{}, &ValidationError{
				Field: "error_code", Message: "a failed proposal must name its error_code",
			}
		}
	case "waiting_approval", "waiting_children":
		if normalized.CheckpointID == nil {
			return WorkerResult{}, &ValidationError{
				Field:   "checkpoint_id",
				Message: "a waiting proposal must name the checkpoint it stopped at",
			}
		}
	}
	return normalized, nil
}

func validateExitRequest(request ExitRequest) (ExitRequest, *ExitEvidence, error) {
	attemptID := strings.TrimSpace(request.AttemptID)
	if !uuid.Valid(attemptID) {
		return ExitRequest{}, nil, ErrAttemptNotFound
	}
	executionUnitID, err := validateIdentifier("execution_unit_id", request.ExecutionUnitID)
	if err != nil {
		return ExitRequest{}, nil, err
	}
	if len(request.Evidence) == 0 {
		return ExitRequest{}, nil, &ValidationError{
			Field: "evidence", Message: "evidence must be a JSON object",
		}
	}
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(request.Evidence, &fields); err != nil || fields == nil {
		return ExitRequest{}, nil, &ValidationError{
			Field: "evidence", Message: "evidence must be a JSON object",
		}
	}

	// T031: `started` is required. T029 treated an absent key as "possibly
	// started" and held the job; now a supervisor must state which case it
	// proves, so a missing key is a located 422 instead of an ambiguous hold.
	startedField, present := fields["started"]
	if !present {
		return ExitRequest{}, nil, &ValidationError{
			Field: "evidence.started", Message: "evidence.started is required",
		}
	}
	var started bool
	if err := json.Unmarshal(startedField, &started); err != nil {
		return ExitRequest{}, nil, &ValidationError{
			Field: "evidence.started", Message: "evidence.started must be a boolean",
		}
	}
	protocolVersion, err := evidenceString(fields, "protocol_version", maxIdentifierLength)
	if err != nil {
		return ExitRequest{}, nil, err
	}
	manifestSHA256, err := evidenceString(fields, "manifest_sha256", 64)
	if err != nil {
		return ExitRequest{}, nil, err
	}
	if !isHex64(manifestSHA256) {
		return ExitRequest{}, nil, &ValidationError{
			Field:   "evidence.manifest_sha256",
			Message: "evidence.manifest_sha256 must be 64 hexadecimal characters",
		}
	}
	imageDigest, err := evidenceString(fields, "image_digest", maxCheckpointID)
	if err != nil {
		return ExitRequest{}, nil, err
	}
	return ExitRequest{
		AttemptID:       attemptID,
		ExecutionUnitID: executionUnitID,
		Evidence:        request.Evidence,
	}, &ExitEvidence{
		Started:         started,
		ProtocolVersion: protocolVersion,
		ManifestSHA256:  manifestSHA256,
		ImageDigest:     imageDigest,
	}, nil
}

// evidenceString reads one required non-blank printable string from an evidence
// object and locates the error at evidence.<name>.
func evidenceString(fields map[string]json.RawMessage, name string, maxLength int) (string, error) {
	raw, present := fields[name]
	if !present {
		return "", &ValidationError{
			Field: "evidence." + name, Message: "evidence." + name + " is required",
		}
	}
	var value string
	if err := json.Unmarshal(raw, &value); err != nil {
		return "", &ValidationError{
			Field: "evidence." + name, Message: "evidence." + name + " must be a string",
		}
	}
	value = strings.TrimSpace(value)
	if value == "" || len(value) > maxLength || !isPrintable(value) {
		return "", &ValidationError{
			Field: "evidence." + name,
			Message: fmt.Sprintf(
				"evidence.%s must be printable and at most %d characters", name, maxLength,
			),
		}
	}
	return value, nil
}

// isHex64 is the manifest hash shape (case-insensitive hex).
func isHex64(value string) bool {
	if len(value) != 64 {
		return false
	}
	for _, character := range value {
		switch {
		case character >= '0' && character <= '9':
		case character >= 'a' && character <= 'f':
		case character >= 'A' && character <= 'F':
		default:
			return false
		}
	}
	return true
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
