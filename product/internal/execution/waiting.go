package execution

// F03/T035: waiting conditions (approval/children) and the single resume.
//
// A worker's `waiting_approval` / `waiting_children` result is a *proposal to
// stop*, not an answer. This file owns the hand-over that makes it durable and
// resumable:
//
//	SubmitResult (waiting_*)  store the proposal, settle active time, move the Run
//	                          to waiting_*, phase waiting, hold the job and write
//	                          the run_waitings row (checkpoint).
//	ConfirmExit (waiting_*)   record the supervisor exit fact; only now may a
//	                          resume exist.
//	ResolveWaiting            store the synthetic resolution once (F06/F07 own the
//	                          real approvals/children) and try to resume.
//	maybeEnqueueResumeTx      the one idempotent transaction: decision AND exit
//	                          AND checkpoint AND permission/budget/deadline, then
//	                          one sealed resume artifact, one resume job, complete
//	                          the old job, revoke the old token, release the slot
//	                          once and queue the Run.
//
// Ordering is deliberately free: the decision may arrive before or after the exit
// evidence, and either order converges to exactly one resume job. A second call
// is a no-op because the waiting row's UNIQUE resume_job_id is already set (and
// jobs.one_live_job_per_run refuses a second live job even if Go raced).
//
// Boundary (recorded in the T035 report): approvals and child Runs are F06/F07
// features. The internal `waiting-resolution` endpoint is a documented
// non-contract bridge that only accepts the closed, safe decision shapes below —
// never a Command payload, thread id or user identifier.

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"

	"orchara/platform/internal/jcs"
	"orchara/platform/internal/uuid"
)

// Waiting kinds, also the closed `platform.run_waitings.kind` domain.
const (
	// WaitingKindApproval awaits a human approval decision.
	WaitingKindApproval = "approval"
	// WaitingKindChildren awaits a child Run's terminal result.
	WaitingKindChildren = "children"
)

// Resolution outcomes, closed per kind (the migration CHECK mirrors this).
var (
	approvalOutcomes = map[string]bool{"approved": true, "rejected": true, "expired": true}
	childrenOutcomes = map[string]bool{"succeeded": true, "failed": true}
)

// Waiting error codes added by T035. Everything else reuses the execution
// vocabulary (`run_state_conflict`, `run_not_found`, `validation_failed`).
const (
	// CodeDecisionConflict is the 409 when a different resolution is already
	// stored for this wait. The first stored decision is authoritative.
	CodeDecisionConflict = "decision_conflict"
)

// ErrDecisionConflict is 409 decision_conflict.
var ErrDecisionConflict = errors.New("a different waiting resolution is already stored")

// WaitingChildError is a refused synthetic children resolution: the referenced
// Run is not this parent's child, does not exist, or is not terminal yet. It is a
// 409 run_state_conflict (details.reason), never a terminal Run outcome: a bogus
// fixture must not be able to resume or fail the parent.
type WaitingChildError struct {
	Reason string
}

func (e *WaitingChildError) Error() string {
	return "the referenced child run cannot resume this wait: " + e.Reason
}

// Waiting child refusal reasons (details.reason).
const (
	// ChildReasonNotFound: no Run row for the id.
	ChildReasonNotFound = "child_not_found"
	// ChildReasonNotChild: the Run exists but another parent owns it.
	ChildReasonNotChild = "child_not_owned"
	// ChildReasonNotTerminal: the child is still non-terminal.
	ChildReasonNotTerminal = "child_not_terminal"
)

// WaitingResolution is the normalised synthetic-resolution body. Exactly one of
// Reference (approval) or ChildRunID (children) is set; the shape is closed.
type WaitingResolution struct {
	Kind       string
	Outcome    string
	Reference  string
	ChildRunID string
}

// WaitingResolutionOutcome is what the bridge returns after a resolution.
type WaitingResolutionOutcome struct {
	RunID       string
	Kind        string
	Outcome     string
	DecisionAt  time.Time
	ResumeJobID *string
}

// WaitingFacts is the owner-facing projection of one run_waitings row plus the
// resume job state. It carries the safe outcome, never a decision reference or
// any credential; it is exposed through non-contract Run-detail headers.
type WaitingFacts struct {
	Kind             string
	CheckpointID     string
	Outcome          string
	DecisionAt       *time.Time
	ExitConfirmedAt  *time.Time
	ResumeJobID      *string
	ResumeJobState   string
	ResumeArtifactID *string
}

// resumeOutcome is the internal result of maybeEnqueueResumeTx. Exactly one field
// group is set: Queued (one resume job), PendingReason (evidence/decision not
// ready or cancel intent recorded) or FailedCode (the Run was terminalised).
type resumeOutcome struct {
	Queued         bool
	ResumeJobID    string
	PendingReason  string
	FailedCode     string
	ExistingResume bool
}

// waitingKindForState maps a Run state to its await kind.
func waitingKindForState(state string) (string, bool) {
	switch state {
	case "waiting_approval":
		return WaitingKindApproval, true
	case "waiting_children":
		return WaitingKindChildren, true
	default:
		return "", false
	}
}

// waitingStateForKind is the inverse mapping (only the two waiting states).
func waitingStateForKind(kind string) string {
	if kind == WaitingKindChildren {
		return "waiting_children"
	}
	return "waiting_approval"
}

// waitingResolutionError is a located 422 for a malformed synthetic resolution.
func waitingResolutionError(field, message string) error {
	return &ValidationError{Field: field, Message: message}
}

// validateWaitingResolution checks the closed union and its per-kind fields
// before any row is touched (422 validation_failed with details.field).
func validateWaitingResolution(request WaitingResolution) (WaitingResolution, error) {
	normalized := WaitingResolution{
		Kind:       strings.TrimSpace(request.Kind),
		Outcome:    strings.TrimSpace(request.Outcome),
		Reference:  strings.TrimSpace(request.Reference),
		ChildRunID: strings.ToLower(strings.TrimSpace(request.ChildRunID)),
	}
	switch normalized.Kind {
	case WaitingKindApproval:
		if !approvalOutcomes[normalized.Outcome] {
			return WaitingResolution{}, waitingResolutionError(
				"outcome",
				"an approval resolution must be approved, rejected or expired",
			)
		}
		if normalized.ChildRunID != "" {
			return WaitingResolution{}, waitingResolutionError(
				"child_run_id", "an approval resolution must not carry a child_run_id",
			)
		}
		if normalized.Reference == "" || len(normalized.Reference) > maxCheckpointID ||
			!isPrintable(normalized.Reference) {
			return WaitingResolution{}, waitingResolutionError(
				"reference",
				fmt.Sprintf("reference must be printable and 1..%d characters", maxCheckpointID),
			)
		}
	case WaitingKindChildren:
		if !childrenOutcomes[normalized.Outcome] {
			return WaitingResolution{}, waitingResolutionError(
				"outcome", "a children resolution must be succeeded or failed",
			)
		}
		if normalized.Reference != "" {
			return WaitingResolution{}, waitingResolutionError(
				"reference", "a children resolution must not carry a reference",
			)
		}
		if !uuid.Valid(normalized.ChildRunID) {
			return WaitingResolution{}, waitingResolutionError(
				"child_run_id", "child_run_id must be a UUID",
			)
		}
	default:
		return WaitingResolution{}, waitingResolutionError(
			"kind", "kind must be approval or children",
		)
	}
	return normalized, nil
}

// waitingDecisionDocument is the exact safe JSON document stored for a
// resolution. It has a closed shape whose CHECK the migration mirrors.
func waitingDecisionDocument(resolution WaitingResolution) map[string]any {
	document := map[string]any{
		"kind":    resolution.Kind,
		"outcome": resolution.Outcome,
	}
	if resolution.Kind == WaitingKindApproval {
		document["reference"] = resolution.Reference
	} else {
		document["child_run_id"] = resolution.ChildRunID
	}
	return document
}

// canonicalWaitingDecision renders the small decision document as RFC 8785 JCS
// bytes. The value is all strings, so this is the canonical UTF-8 object form.
func canonicalWaitingDecision(resolution WaitingResolution) ([]byte, error) {
	raw, err := json.Marshal(waitingDecisionDocument(resolution))
	if err != nil {
		return nil, fmt.Errorf("encoding the waiting resolution: %w", err)
	}
	canonical, err := jcs.CanonicalizeBytes(raw)
	if err != nil {
		return nil, fmt.Errorf("canonicalising the waiting resolution: %w", err)
	}
	return canonical, nil
}

// sameWaitingDecision compares two stored decision documents by JSON value.
// JSONB equality is the database authority; JCS is the same value in one byte
// form, so key order/whitespace never makes a replay look conflicting.
func sameWaitingDecision(stored, proposed []byte) bool {
	storedCanonical, storedErr := jcs.CanonicalizeBytes(stored)
	proposedCanonical, proposedErr := jcs.CanonicalizeBytes(proposed)
	if storedErr != nil || proposedErr != nil {
		return false
	}
	return string(storedCanonical) == string(proposedCanonical)
}

// waitingDecisionOutcome extracts the safe outcome for the owner-facing facts.
// An unknown/malformed document yields "" instead of leaking the document.
func waitingDecisionOutcome(decision []byte) string {
	if len(decision) == 0 {
		return ""
	}
	var document struct {
		Outcome string `json:"outcome"`
	}
	if err := json.Unmarshal(decision, &document); err != nil {
		return ""
	}
	return document.Outcome
}

// applyWaitingTransitionTx commits the waiting hand-over for one stored waiting
// proposal: the Run moves to waiting_*/phase waiting, the fixed checkpoint is
// recorded, the job is held and one run.status event is appended. The caller
// already holds the Run and attempt locks in this transaction.
func (s *Service) applyWaitingTransitionTx(
	ctx context.Context, tx *sql.Tx, run *runFacts, attempt *resultAttemptFacts, result WorkerResult,
) error {
	kind := WaitingKindApproval
	if result.SuggestedState == "waiting_children" {
		kind = WaitingKindChildren
	}
	checkpointID := ""
	if result.CheckpointID != nil {
		checkpointID = strings.TrimSpace(*result.CheckpointID)
	}
	if checkpointID == "" {
		// validateWorkerResult already requires it; this is a defensive invariant.
		return &ValidationError{
			Field:   "checkpoint_id",
			Message: "a waiting proposal must name the checkpoint it stopped at",
		}
	}
	// T034: waiting does not accrue active time. The proposal path already settled
	// this attempt; repeating the helper is the idempotent no-op that guarantees
	// the transition out of executing is always accompanied by a settlement.
	if _, err := settleAttemptActiveTx(ctx, tx, run.ID); err != nil {
		return err
	}
	if err := insertWaitingConditionTx(ctx, tx, run.ID, kind, checkpointID); err != nil {
		return err
	}
	if kind == WaitingKindApproval {
		// F06/T050: a decision may already be durable (the human decided while
		// the attempt was still exiting). Adopt it into the fresh waiting row so
		// the single resume transaction below sees the same decision.
		if err := adoptApprovalDecisionTx(ctx, tx, run.ID); err != nil {
			return err
		}
	}
	if err := moveRunToWaitingTx(
		ctx, tx, run.ID, run.State, run.Revision, kind,
	); err != nil {
		return err
	}
	if err := holdJobForWaitingTx(ctx, tx, attempt.JobID); err != nil {
		return err
	}
	if _, err := AppendRunStatusTx(ctx, tx, run.ID); err != nil {
		return err
	}
	return nil
}

// WaitingFacts returns the owner-facing waiting projection for one Run, or nil
// when the Run never waited. It never returns the decision reference or a token.
func (s *Service) WaitingFacts(ctx context.Context, runID string) (*WaitingFacts, error) {
	if !uuid.Valid(strings.TrimSpace(runID)) {
		return nil, nil
	}
	return waitingFactsRead(ctx, s.DB, runID)
}

// ResolveWaiting stores the synthetic resolution once and tries the one resume.
//
// Validation order:
//  1. path UUID -> 404 run_not_found;
//  2. locked Run: a terminal/other state with no matching stored decision is
//     409 run_state_conflict;
//  3. the waiting row and its kind must match the body (409 run_state_conflict);
//  4. an identical stored decision is the idempotent 200 (no write); a different
//     one is 409 decision_conflict;
//  5. only then is the decision stored (now()) and maybeEnqueueResumeTx run in
//     the same transaction: any refusal (bad child reference, object store
//     unavailable, ...) rolls the decision back.
func (s *Service) ResolveWaiting(
	ctx context.Context, runID string, request WaitingResolution,
) (*WaitingResolutionOutcome, error) {
	runID = strings.TrimSpace(runID)
	if !uuid.Valid(runID) {
		return nil, ErrRunNotFound
	}
	resolution, err := validateWaitingResolution(request)
	if err != nil {
		return nil, err
	}
	decision, err := canonicalWaitingDecision(resolution)
	if err != nil {
		return nil, err
	}

	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return nil, fmt.Errorf("starting the waiting-resolution transaction: %w", err)
	}
	defer func() { _ = tx.Rollback() }()

	run, err := lockWaitingRunTx(ctx, tx, runID)
	if err != nil {
		return nil, err
	}
	waiting, err := lockWaitingTx(ctx, tx, runID)
	if err != nil {
		return nil, err
	}
	if waiting == nil {
		// A Run that is not waiting has no stored condition to resolve.
		return nil, ErrRunStateConflict
	}
	if run.State == "cancelling" {
		// F03/T036: while a cancel is being confirmed only heartbeat, exit and
		// receipt/query calls remain. A waiting resolution must not race a cancel
		// into a resume; the waiting evidence stays, but it is inert.
		return nil, ErrRunStateConflict
	}
	if waiting.Kind != resolution.Kind {
		return nil, ErrRunStateConflict
	}
	_, isWaiting := waitingKindForState(run.State)
	if len(waiting.Decision) > 0 {
		// The first stored decision is authoritative in every state: an identical
		// replay is the idempotent 200, anything else is the conflict.
		if !sameWaitingDecision(waiting.Decision, decision) {
			return nil, ErrDecisionConflict
		}
	} else if !isWaiting {
		// A Run that is not waiting (and has no stored decision) cannot gain one:
		// terminal/cancelled/queued all answer the same 409.
		return nil, ErrRunStateConflict
	}
	if !isWaiting {
		return waitingOutcomeOf(run.ID, waiting, resolution, nil), nil
	}

	outcome := &resumeOutcome{}
	if len(waiting.Decision) > 0 {
		outcome.PendingReason = "already_decided"
	} else {
		// A children decision must name a real terminal child of this Run before it is
		// stored: a bogus fixture must not be able to strand or resume the parent.
		// maybeEnqueueResumeTx re-checks the same rule on the stored document.
		if resolution.Kind == WaitingKindChildren {
			if err := checkWaitingChildTx(ctx, tx, run.ID, decision); err != nil {
				return nil, err
			}
		}
		if err := storeWaitingDecisionTx(ctx, tx, runID, waiting.Kind, decision); err != nil {
			return nil, err
		}
	}
	resume, err := s.maybeEnqueueResumeTx(ctx, tx, runID)
	if err != nil {
		return nil, err
	}
	if resume != nil {
		outcome = resume
	}
	decisionAt, err := waitingDecisionAtTx(ctx, tx, runID)
	if err != nil {
		return nil, err
	}
	if err := tx.Commit(); err != nil {
		return nil, fmt.Errorf("committing the waiting-resolution transaction: %w", err)
	}
	result := &WaitingResolutionOutcome{
		RunID:      run.ID,
		Kind:       resolution.Kind,
		Outcome:    resolution.Outcome,
		DecisionAt: decisionAt,
	}
	if outcome.Queued && outcome.ResumeJobID != "" {
		jobID := outcome.ResumeJobID
		result.ResumeJobID = &jobID
	}
	return result, nil
}

// waitingOutcomeOf is the read-only replay answer for a run that already left the
// waiting state (resumed/queued/terminal): it reports the stored facts and the
// existing resume job, and never writes.
func waitingOutcomeOf(
	runID string, waiting *waitingFactsRow, resolution WaitingResolution, resumeJobID *string,
) *WaitingResolutionOutcome {
	result := &WaitingResolutionOutcome{
		RunID:   runID,
		Kind:    resolution.Kind,
		Outcome: resolution.Outcome,
	}
	if waiting.DecisionAt.Valid {
		result.DecisionAt = waiting.DecisionAt.Time
	}
	if resumeJobID != nil {
		result.ResumeJobID = resumeJobID
	} else if waiting.ResumeJobID.Valid {
		jobID := waiting.ResumeJobID.String
		result.ResumeJobID = &jobID
	}
	return result
}

// maybeEnqueueResumeTx is the one idempotent resume transaction. Callers already
// hold (or re-lock) the Run and the waiting row; this function locks what it
// needs in the fixed Run -> waiting -> job -> attempt order.
//
// It returns nil,nil only when there is nothing to decide (the Run is neither
// waiting nor terminal), a pending outcome when the decision or the exit fact is
// still missing or a cancel intent is recorded, a queued outcome when (and only
// when) one resume job now exists, or a typed error that must roll the caller's
// transaction back.
func (s *Service) maybeEnqueueResumeTx(
	ctx context.Context, tx *sql.Tx, runID string,
) (*resumeOutcome, error) {
	run, err := lockWaitingRunTx(ctx, tx, runID)
	if err != nil {
		return nil, err
	}
	if _, waiting := waitingKindForState(run.State); !waiting {
		// Already resumed (queued) or terminalised: the repeated call is a no-op.
		return nil, nil
	}
	waiting, err := lockWaitingTx(ctx, tx, runID)
	if err != nil {
		return nil, err
	}
	if waiting == nil {
		return nil, fmt.Errorf("the run is waiting but has no waiting condition")
	}
	if waiting.ResumeJobID.Valid {
		return &resumeOutcome{
			Queued:         true,
			ResumeJobID:    waiting.ResumeJobID.String,
			ExistingResume: true,
		}, nil
	}
	if waiting.CancelRequestedAt.Valid {
		// F03/T036: the waiting row is marked cancelled only by the cancel
		// endpoint (never by the T034 deadline intent), so it is the definitive
		// "this wait can never resume" fact. The finalizer commits cancelled once
		// the exit fact is present; this transaction must not create a resume.
		return &resumeOutcome{PendingReason: "cancel_requested"}, nil
	}
	if len(waiting.Decision) == 0 || !waiting.DecisionAt.Valid || !waiting.ExitConfirmedAt.Valid ||
		strings.TrimSpace(waiting.CheckpointID) == "" {
		// Either the decision or the supervisor's exit fact is still missing. The
		// first arrival only leaves its fact; the second completes the resume.
		reason := "awaiting_decision"
		if len(waiting.Decision) > 0 && waiting.DecisionAt.Valid {
			reason = "awaiting_exit"
		}
		return &resumeOutcome{PendingReason: reason}, nil
	}

	attempt, err := lockLatestWaitingAttemptTx(ctx, tx, runID)
	if err != nil {
		return nil, err
	}
	if attempt == nil || !attempt.ExitedAt.Valid {
		// exit_confirmed_at exists, so a missing/exited attempt is an invariant
		// violation rather than a state the resume may paper over.
		return nil, fmt.Errorf("the waiting run has no exited attempt to hand over")
	}

	// A children wait may only resume from a real terminal child of this Run. The
	// check runs before any failure transition, so a bogus synthetic reference is
	// refused (409) instead of terminalising the parent.
	if waiting.Kind == WaitingKindChildren {
		if err := checkWaitingChildTx(ctx, tx, run.ID, waiting.Decision); err != nil {
			return nil, err
		}
	}
	// F06/T050: a human rejection or an expired approval is a refusal, not a
	// resume. The Run fails with the stable code once the exit fact exists; a
	// decision that arrives before the exit is failed by ConfirmExit's call into
	// this same function.
	if waiting.Kind == WaitingKindApproval {
		switch waitingDecisionOutcome(waiting.Decision) {
		case "rejected":
			if err := failWaitingRunTx(ctx, tx, run, attempt, ApprovalRejectedCode); err != nil {
				return nil, err
			}
			return &resumeOutcome{FailedCode: ApprovalRejectedCode}, nil
		case "expired":
			if err := failWaitingRunTx(ctx, tx, run, attempt, ApprovalExpiredCode); err != nil {
				return nil, err
			}
			return &resumeOutcome{FailedCode: ApprovalExpiredCode}, nil
		}
	}

	// Re-check permission/budget/deadline immediately before creating the resume.
	// Failing here (after the exit fact exists) terminalises the Run with the
	// stable reason instead of resuming a run that may not continue.
	if _, err := settleAttemptActiveTx(ctx, tx, runID); err != nil {
		return nil, err
	}
	deadlineState, err := checkDeadlineTx(ctx, tx, runID)
	if err != nil {
		return nil, err
	}
	if deadlineState.Over {
		// T034's pre-cancel deadline path also records cancel_requested_at; the
		// deadline is the honest reason and it must win over the cancel-intent
		// pending branch below.
		if err := failWaitingRunTx(ctx, tx, run, attempt, DeadlineErrorCode); err != nil {
			return nil, err
		}
		return &resumeOutcome{FailedCode: DeadlineErrorCode}, nil
	}
	if run.CancelRequestedAt.Valid {
		// Cancel intent wins over a resume. T036 owns the confirmed cancelled
		// transition, so T035 leaves the Run non-terminal instead of faking it.
		return &resumeOutcome{PendingReason: "cancel_requested"}, nil
	}
	disabled, err := ownerDisabledTx(ctx, tx, run.OwnerID)
	if err != nil {
		return nil, err
	}
	failureCode := ""
	switch {
	case disabled:
		failureCode = "permission_revoked"
	case !run.BudgetPositive:
		failureCode = "budget_exhausted"
	}
	if failureCode != "" {
		if err := failWaitingRunTx(ctx, tx, run, attempt, failureCode); err != nil {
			return nil, err
		}
		return &resumeOutcome{FailedCode: failureCode}, nil
	}

	payload, err := resumePayloadBytes(runID, waiting)
	if err != nil {
		return nil, err
	}
	if s.Objects == nil {
		return nil, ErrObjectStoreUnavailable
	}
	object, err := s.Objects.CreateSealed(bytes.NewReader(payload), int64(len(payload)))
	if err != nil {
		return nil, fmt.Errorf("%w: %v", ErrObjectStoreUnavailable, err)
	}
	artifactID := uuid.NewString()
	if err := insertResumeArtifactTx(
		ctx, tx, artifactID, run.OwnerID, object.Key, object.SHA256, object.Size,
	); err != nil {
		return nil, err
	}
	resumeJobID := uuid.NewString()
	// Order matters for the database invariants: the old job is completed (it is the
	// one live job of the Run) before the single resume job is inserted, so
	// jobs.one_live_job_per_run is never violated. All of it is one transaction.
	if err := completeJobTx(ctx, tx, attempt.JobID); err != nil {
		return nil, err
	}
	if err := revokeAttemptTx(ctx, tx, attempt.Token); err != nil {
		return nil, err
	}
	if err := releaseAllocationOnceTx(ctx, tx, "job:"+attempt.JobID); err != nil {
		return nil, err
	}
	if err := insertResumeJobTx(ctx, tx, resumeJobID, runID, artifactID); err != nil {
		return nil, err
	}
	if err := updateWaitingResumeTx(
		ctx, tx, runID, resumeJobID, artifactID,
	); err != nil {
		return nil, err
	}
	if err := queueWaitingRunTx(ctx, tx, run, waiting.Kind); err != nil {
		return nil, err
	}
	// T032: the resume is a committed Run transition (waiting -> queued) and its
	// run.status event is written in this same transaction.
	if _, err := AppendRunStatusTx(ctx, tx, runID); err != nil {
		return nil, err
	}
	return &resumeOutcome{Queued: true, ResumeJobID: resumeJobID}, nil
}

// resumePayloadBytes is the canonical sealed resume payload the resume job
// carries: run identity, the wait kind, the fixed checkpoint and the stored safe
// decision. It never contains a Command, thread id, user id or credential.
func resumePayloadBytes(runID string, waiting *waitingFactsRow) ([]byte, error) {
	document := map[string]any{
		"run_id":        runID,
		"kind":          waiting.Kind,
		"checkpoint_id": waiting.CheckpointID,
		"decision":      json.RawMessage(waiting.Decision),
	}
	raw, err := json.Marshal(document)
	if err != nil {
		return nil, fmt.Errorf("encoding the resume payload: %w", err)
	}
	canonical, err := jcs.CanonicalizeBytes(raw)
	if err != nil {
		return nil, fmt.Errorf("canonicalising the resume payload: %w", err)
	}
	return canonical, nil
}
