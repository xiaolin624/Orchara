package capabilities

// F06/T051: reconcileOperation for an unknown external write.
//
// A connector write that was sent but whose response was lost is recorded as
// operation=unknown (see dispatchConnector). It is never presented as failed or
// succeeded, and it is never resent automatically: only the authorized->dispatching
// CAS winner of the first call ever made the physical request.
//
// This file is the owner-facing settlement:
//
//	POST /api/v1/operations/{operation_id}/reconcile
//	body {conclusion, evidence_artifact_id}, Idempotency-Key required
//
//   - only the Run owner or the operation's recorded approver may reconcile;
//   - the evidence artifact must be ready, owned by the owner or linked to the
//     Run, not deleted, and its JSON body must bind to *this* operation (the
//     operation id, or the logical key + params hash). Free text is never trusted
//     as a provider receipt;
//   - confirmed_success marks the operation succeeded and uses the original
//     receipt when it exists, else the evidence artifact as the receipt;
//   - confirmed_not_sent / confirmed_failure mark it failed. A same-operation
//     retry is only allowed when the policy explicitly supports one; the F06/T048
//     connector policies declare no idempotency/query capability, so this ticket
//     refuses the retry and records the limitation instead of inventing one. No
//     second operation id is ever created for the same logical call;
//   - the operation row and the run-side T036 resolution commit in one
//     transaction, so the Run leaves needs_reconciliation only through the one
//     finalizer (internal/execution).
//
// Idempotency: the conclusion/evidence/key tuple is persisted on the operation.
// The same tuple replays the recorded answer; a different one after the committed
// reconciliation is 409.

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"strings"

	"orchara/platform/internal/execution"
	"orchara/platform/internal/uuid"
)

// Reconciliation conclusion vocabulary (contract `Reconciliation.conclusion`).
const (
	ReconcileConfirmedSuccess = execution.ReconciliationConfirmedSuccess
	ReconcileConfirmedNotSent = execution.ReconciliationConfirmedNotSent
	ReconcileConfirmedFailure = execution.ReconciliationConfirmedFailure
)

// Reconciliation error codes (stable, owner-facing).
const (
	CodeOperationNotReconcilable  = "operation_not_reconcilable"
	CodeReconciliationConflict    = "reconciliation_conflict"
	CodeEvidenceInvalid           = "evidence_artifact_invalid"
	CodeReconciliationUnavailable = "reconciliation_unavailable"
)

// ReconcileOperationRequest is the contract's Reconciliation body, normalised.
type ReconcileOperationRequest struct {
	Conclusion         string
	EvidenceArtifactID string
}

// ReconcileResult is the service answer. The HTTP layer returns only the
// contract's Accepted {id, state}; the owner ledger carries the rest.
type ReconcileResult struct {
	OperationID            string
	State                  string
	Conclusion             string
	EvidenceArtifactID     string
	ReceiptArtifactID      *string
	RunState               string
	AlreadyReconciled      bool
	ProviderIdempotencyKey *string
	Limitation             *string
	WriteCount             int
}

// reconciledOperation is one locked row plus its Run facts.
type reconciledOperation struct {
	OperationID       string
	RunID             string
	OwnerID           string
	LogicalKey        string
	ParamsSHA256      string
	RiskClass         string
	State             string
	ReceiptArtifactID sql.NullString
	IdempotencyKey    sql.NullString
	ReconciledBy      sql.NullString
	EvidenceID        sql.NullString
	Conclusion        sql.NullString
	ReconcileKey      sql.NullString
	Limitation        sql.NullString
	LastErrorCode     sql.NullString
}

// ReconcileOperation settles one unknown external write from verified evidence.
func (s *Service) ReconcileOperation(
	ctx context.Context, callerID, operationID string, request ReconcileOperationRequest,
	idempotencyKey string,
) (*ReconcileResult, *Error) {
	operationID = strings.ToLower(strings.TrimSpace(operationID))
	if !uuid.Valid(operationID) {
		return nil, capFailure(404, CodeOperationNotFound, "that operation does not exist")
	}
	if !uuid.Valid(callerID) {
		return nil, capFailure(403, "forbidden", "a principal is required to reconcile an operation")
	}
	conclusion := strings.TrimSpace(request.Conclusion)
	switch conclusion {
	case ReconcileConfirmedSuccess, ReconcileConfirmedNotSent, ReconcileConfirmedFailure:
	default:
		return nil, validationFailure("conclusion",
			"conclusion must be confirmed_success, confirmed_not_sent or confirmed_failure")
	}
	evidenceID := strings.ToLower(strings.TrimSpace(request.EvidenceArtifactID))
	if !uuid.Valid(evidenceID) {
		return nil, &Error{
			Status: 422, Code: CodeEvidenceInvalid,
			Message: "evidence_artifact_id must name an artifact of this run",
			Field:   "evidence_artifact_id",
		}
	}
	idempotencyKey = strings.TrimSpace(idempotencyKey)
	if idempotencyKey == "" || len(idempotencyKey) > 200 {
		return nil, &Error{
			Status: 400, Code: "idempotency_key_required",
			Message: "the Idempotency-Key header is required and must be at most 200 characters",
			Field:   "Idempotency-Key",
		}
	}

	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return nil, capFailure(500, CodeInternal, "the reconciliation transaction could not start")
	}
	defer func() { _ = tx.Rollback() }()

	op, capErr := s.lockReconcilableOperationTx(ctx, tx, operationID)
	if capErr != nil {
		return nil, capErr
	}
	if capErr := s.requireReconcileAuthority(ctx, tx, op, callerID); capErr != nil {
		return nil, capErr
	}
	if op.ReconciledBy.Valid {
		return s.replayReconciliation(ctx, tx, op, conclusion, evidenceID, idempotencyKey)
	}
	if op.State != "unknown" {
		return nil, &Error{
			Status: 409, Code: CodeOperationNotReconcilable,
			Message: "only an unknown external write can be reconciled; this operation is already settled",
		}
	}
	if s.Store == nil {
		return nil, capFailure(503, CodeReconciliationUnavailable,
			"the object store is not configured; the evidence cannot be verified")
	}
	if capErr := s.verifyReconciliationEvidence(ctx, tx, op, evidenceID); capErr != nil {
		return nil, capErr
	}

	newState := "failed"
	errorCode := sql.NullString{String: execution.ReconciliationFailedCode, Valid: true}
	if op.LastErrorCode.Valid {
		errorCode = op.LastErrorCode
	}
	switch conclusion {
	case ReconcileConfirmedSuccess:
		newState = "succeeded"
		errorCode = sql.NullString{}
	case ReconcileConfirmedNotSent:
		errorCode = sql.NullString{String: execution.ReconciliationNotSentCode, Valid: true}
	}
	// A safe same-operation retry is only possible when the policy explicitly
	// supports it. The F06/T048 connector policy shape declares no provider
	// idempotency key and no query/receipt lookup, so no retry is authorised:
	// confirmed_not_sent fails the operation the same way confirmed_failure does.
	update, execErr := tx.ExecContext(ctx, `
		UPDATE platform.operations
		   SET state = $2,
		       receipt_artifact_id = CASE WHEN $2 = 'succeeded'
		                                  THEN COALESCE(receipt_artifact_id, $3::uuid)
		                                  ELSE receipt_artifact_id END,
		       last_error_code = CASE WHEN $2 = 'succeeded' THEN NULL ELSE $4 END,
		       reconciled_by = $5::uuid,
		       reconciliation_evidence_id = $3::uuid,
		       reconciliation_conclusion = $6,
		       reconciliation_key = $7,
		       updated_at = now()
		 WHERE id = $1::uuid AND state = 'unknown'`,
		op.OperationID, newState, evidenceID, errorCode.String, callerID,
		conclusion, idempotencyKey,
	)
	if execErr != nil {
		return nil, capFailure(500, CodeInternal, "the reconciled operation could not be recorded")
	}
	if affected, affectedErr := update.RowsAffected(); affectedErr == nil && affected == 0 {
		return nil, &Error{
			Status: 409, Code: CodeReconciliationConflict,
			Message: "the operation changed while it was being reconciled",
		}
	}
	if s.Reconciler == nil {
		return nil, capFailure(503, CodeReconciliationUnavailable,
			"the run reconciliation bridge is not configured")
	}
	runState, bridgeErr := s.Reconciler.ReconcileUnknownOperationTx(
		ctx, tx, execution.ReconcileResolution{
			RunID:       op.RunID,
			OperationID: op.OperationID,
			CallerID:    callerID,
			Conclusion:  conclusion,
			FailureCode: errorCode.String,
		})
	if bridgeErr != nil {
		return nil, reconcileBridgeFailure(bridgeErr)
	}
	if err := tx.Commit(); err != nil {
		return nil, capFailure(500, CodeInternal, "the reconciliation could not be committed")
	}
	settled := &ReconcileResult{
		OperationID:        op.OperationID,
		State:              newState,
		Conclusion:         conclusion,
		EvidenceArtifactID: evidenceID,
		RunState:           runState,
		WriteCount:         s.physicalWriteCount(ctx, op.OperationID),
	}
	if receiptID, ok := s.operationReceipt(ctx, op.OperationID); ok {
		settled.ReceiptArtifactID = &receiptID
	}
	if op.IdempotencyKey.Valid {
		value := op.IdempotencyKey.String
		settled.ProviderIdempotencyKey = &value
	}
	if op.Limitation.Valid {
		value := op.Limitation.String
		settled.Limitation = &value
	}
	return settled, nil
}

// lockReconcilableOperationTx locks the Run first (the package's fixed lock
// order: Run then operation) and returns the operation facts. A missing operation
// and a foreign Run are both 404 (no enumeration).
func (s *Service) lockReconcilableOperationTx(
	ctx context.Context, tx *sql.Tx, operationID string,
) (*reconciledOperation, *Error) {
	var op reconciledOperation
	if err := tx.QueryRowContext(ctx, `
		SELECT o.run_id::text FROM platform.operations AS o
		 WHERE o.id = $1::uuid`, operationID,
	).Scan(&op.RunID); err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return nil, capFailure(404, CodeOperationNotFound, "that operation does not exist")
		}
		return nil, capFailure(500, CodeInternal, "the operation could not be read")
	}
	if err := tx.QueryRowContext(ctx, `
		SELECT owner_id::text FROM platform.runs
		 WHERE id = $1::uuid AND deleted_at IS NULL
		 FOR UPDATE`, op.RunID,
	).Scan(&op.OwnerID); err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return nil, capFailure(404, CodeOperationNotFound, "that operation does not exist")
		}
		return nil, capFailure(500, CodeInternal, "the run could not be locked")
	}
	if err := tx.QueryRowContext(ctx, `
		SELECT id::text, logical_key, params_sha256, risk_class, state,
		       receipt_artifact_id::text, provider_idempotency_key,
		       reconciled_by::text, reconciliation_evidence_id::text,
		       reconciliation_conclusion, reconciliation_key,
		       reconciliation_limitation, last_error_code
		  FROM platform.operations WHERE id = $1::uuid FOR UPDATE`, operationID,
	).Scan(&op.OperationID, &op.LogicalKey, &op.ParamsSHA256, &op.RiskClass, &op.State,
		&op.ReceiptArtifactID, &op.IdempotencyKey, &op.ReconciledBy, &op.EvidenceID,
		&op.Conclusion, &op.ReconcileKey, &op.Limitation, &op.LastErrorCode,
	); err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return nil, capFailure(404, CodeOperationNotFound, "that operation does not exist")
		}
		return nil, capFailure(500, CodeInternal, "the operation could not be locked")
	}
	return &op, nil
}

// requireReconcileAuthority accepts the Run owner or the operation's recorded
// approver (T050). Another principal sees 403: the operation exists and is not
// theirs to settle.
func (s *Service) requireReconcileAuthority(
	ctx context.Context, tx *sql.Tx, op *reconciledOperation, callerID string,
) *Error {
	if strings.EqualFold(op.OwnerID, callerID) {
		return nil
	}
	var approver bool
	if err := tx.QueryRowContext(ctx, `
		SELECT EXISTS (
			SELECT 1 FROM platform.approvals
			 WHERE operation_id = $1::uuid AND approver_id = $2::uuid)`,
		op.OperationID, callerID,
	).Scan(&approver); err != nil {
		return capFailure(500, CodeInternal, "the reconcile authority could not be read")
	}
	if !approver {
		return capFailure(403, "forbidden",
			"only the run owner or the operation's approving approver may reconcile it")
	}
	return nil
}

// replayReconciliation returns the recorded answer for the same
// (conclusion, evidence, Idempotency-Key) tuple. A different tuple after the
// terminal write is a conflict, never a second settlement.
func (s *Service) replayReconciliation(
	ctx context.Context, tx *sql.Tx, op *reconciledOperation,
	conclusion, evidenceID, idempotencyKey string,
) (*ReconcileResult, *Error) {
	same := op.Conclusion.Valid && op.Conclusion.String == conclusion &&
		op.EvidenceID.Valid && op.EvidenceID.String == evidenceID &&
		op.ReconcileKey.Valid && op.ReconcileKey.String == idempotencyKey
	if !same {
		return nil, &Error{
			Status: 409, Code: CodeReconciliationConflict,
			Message: "this operation was already reconciled with a different conclusion, evidence or Idempotency-Key",
		}
	}
	var runState string
	if err := tx.QueryRowContext(ctx, `
		SELECT state FROM platform.runs WHERE id = $1::uuid`, op.RunID,
	).Scan(&runState); err != nil {
		return nil, capFailure(500, CodeInternal, "the reconciled run state could not be read")
	}
	replay := &ReconcileResult{
		OperationID:        op.OperationID,
		State:              op.State,
		Conclusion:         conclusion,
		EvidenceArtifactID: evidenceID,
		RunState:           runState,
		AlreadyReconciled:  true,
		WriteCount:         s.physicalWriteCount(ctx, op.OperationID),
	}
	if op.ReceiptArtifactID.Valid {
		value := op.ReceiptArtifactID.String
		replay.ReceiptArtifactID = &value
	}
	if op.IdempotencyKey.Valid {
		value := op.IdempotencyKey.String
		replay.ProviderIdempotencyKey = &value
	}
	if op.Limitation.Valid {
		value := op.Limitation.String
		replay.Limitation = &value
	}
	return replay, nil
}

// verifyReconciliationEvidence proves the artifact is usable evidence for this
// operation: ready, undeleted, owned by the Run owner or linked to the Run, and
// its JSON object binds to this operation id (or logical key + params hash).
func (s *Service) verifyReconciliationEvidence(
	ctx context.Context, tx *sql.Tx, op *reconciledOperation, artifactID string,
) *Error {
	var (
		ownerID   string
		state     string
		deleted   sql.NullTime
		objectKey string
		digest    string
		runLinked bool
	)
	if err := tx.QueryRowContext(ctx, `
		SELECT a.owner_id::text, a.state, a.deleted_at, a.object_key, COALESCE(a.sha256, ''),
		       EXISTS (SELECT 1 FROM platform.run_artifacts AS ra
		                WHERE ra.run_id = $2::uuid AND ra.artifact_id = a.id)
		  FROM platform.artifacts AS a WHERE a.id = $1::uuid`,
		artifactID, op.RunID,
	).Scan(&ownerID, &state, &deleted, &objectKey, &digest, &runLinked); err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return evidenceFailure("the evidence artifact does not exist")
		}
		return capFailure(500, CodeInternal, "the evidence artifact could not be read")
	}
	if deleted.Valid || state != "ready" {
		// Expired/deleted evidence is refused: a non-ready artifact cannot prove
		// what the external side effect was.
		return evidenceFailure("the evidence artifact is not ready or was deleted")
	}
	if !strings.EqualFold(ownerID, op.OwnerID) && !runLinked {
		return evidenceFailure("the evidence artifact is not owned by this run")
	}
	content, err := s.readSealed(objectKey, digest)
	if err != nil {
		return evidenceFailure("the evidence artifact bytes are not readable")
	}
	var document map[string]any
	decoder := json.NewDecoder(bytes.NewReader(content))
	decoder.UseNumber()
	if err := decoder.Decode(&document); err != nil || document == nil {
		return evidenceFailure("the evidence artifact must be a JSON object")
	}
	if runID, ok := document["run_id"]; ok {
		if value, isString := runID.(string); !isString || value != op.RunID {
			return evidenceFailure("the evidence artifact belongs to another run")
		}
	}
	if raw, ok := document["operation_id"]; ok {
		value, isString := raw.(string)
		if !isString || !strings.EqualFold(value, op.OperationID) {
			return evidenceFailure("the evidence artifact does not name this operation")
		}
		return nil
	}
	rawKey, hasKey := document["logical_key"]
	rawHash, hasHash := document["params_sha256"]
	if !hasKey || !hasHash {
		return evidenceFailure(
			"the evidence artifact must bind to this operation by operation_id or logical_key+params_sha256")
	}
	key, okKey := rawKey.(string)
	hash, okHash := rawHash.(string)
	if !okKey || !okHash || key != op.LogicalKey || !strings.EqualFold(hash, op.ParamsSHA256) {
		return evidenceFailure("the evidence artifact binds to different invocation parameters")
	}
	return nil
}

func evidenceFailure(message string) *Error {
	return &Error{Status: 422, Code: CodeEvidenceInvalid, Message: message, Field: "evidence_artifact_id"}
}

// reconcileBridgeFailure maps the execution package's typed bridge errors.
func reconcileBridgeFailure(err error) *Error {
	switch {
	case errors.Is(err, execution.ErrRunTerminal):
		return &Error{Status: 409, Code: "run_terminal",
			Message: "the run is already terminal; its outcome is the authority"}
	case errors.Is(err, execution.ErrReconciliationForbidden):
		return capFailure(403, "forbidden",
			"only the run owner or the operation's approving approver may reconcile it")
	case errors.Is(err, execution.ErrReconciliationUnknown):
		return &Error{Status: 409, Code: CodeReconciliationUnavailable,
			Message: "the operation has no unresolved unknown record for this run"}
	default:
		return capFailure(500, CodeInternal, "the run reconciliation could not be committed")
	}
}

// physicalWriteCount is the number of real physical dispatch rows. It is proof
// that reconciliation added no resend: it must stay 1 across the reconcile.
func (s *Service) physicalWriteCount(ctx context.Context, operationID string) int {
	var count int
	_ = s.DB.QueryRowContext(ctx, `
		SELECT count(*) FROM platform.operation_attempts WHERE operation_id = $1::uuid`,
		operationID).Scan(&count)
	return count
}

// operationReceipt returns the operation's receipt artifact id, if any.
func (s *Service) operationReceipt(ctx context.Context, operationID string) (string, bool) {
	var receipt sql.NullString
	if err := s.DB.QueryRowContext(ctx, `
		SELECT receipt_artifact_id::text FROM platform.operations WHERE id = $1::uuid`,
		operationID).Scan(&receipt); err != nil || !receipt.Valid {
		return "", false
	}
	return receipt.String, true
}
