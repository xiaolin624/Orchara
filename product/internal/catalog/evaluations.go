package catalog

// F13A/T052: the evaluation submission (contract operation evaluateDraft).
//
//	POST /api/v1/applications/{application_id}/evaluations
//	  body {draft_revision, dataset_artifact_id}   Idempotency-Key required
//	  -> 202 EvaluationJob
//
// The submission freezes one draft revision into a private execution snapshot
// (the same freeze debugDraft performs: compile, seal the canonical manifest,
// record the compiler/worker/state identities), and creates exactly one
// `application_evaluate` background job plus one `evaluation_jobs` row. It
// creates no run: the trusted runner creates the per-case runs later, as the
// evaluation's owner, through the job-scoped internal API (ADR 0011).
//
// Rules that shape this file:
//
//   - **Owner only.** A read grant is sufficient to read a draft and never to
//     compile or evaluate it; every non-owner gets the same 404 as an unknown
//     application.
//   - **The dataset is validated at submit time.** The uploaded, sealed dataset
//     artifact is read through the object store, its recorded SHA-256 is
//     verified against the bytes, and the document is parsed strictly against
//     contracts/evaluation-case.schema.json. Every problem is a located 422, so
//     a curator learns which case and which member is wrong. A case with zero
//     assertions is refused here and can therefore never reach a passed report.
//   - **Idempotency is atomic with creation.** The receipt row is inserted in
//     the same transaction as the snapshot/evaluation/job; a replay returns the
//     stored 202 body and creates no row.
//   - **Nothing here claims execution.** The job is queued; the report is the
//     runner's, and the completion guard is still the only writer of a report
//     row.

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"sort"
	"strings"

	"orchara/platform/internal/background"
	"orchara/platform/internal/compiler"
	"orchara/platform/internal/evaluation"
	"orchara/platform/internal/jcs"
	"orchara/platform/internal/objectstore"
	"orchara/platform/internal/uuid"
)

const (
	// EvaluationDatasetPurpose is the only artifact purpose an evaluation
	// dataset may have. T028's upload admission already accepts it.
	EvaluationDatasetPurpose = "evaluation_dataset"
	// EvaluationDatasetMediaType is the only dataset media type this build
	// parses.
	EvaluationDatasetMediaType = "application/json"
	// evaluationAcceptedStatus is the contract's evaluateDraft success status.
	evaluationAcceptedStatus = 202
)

// EvaluationRequest is one decoded evaluateDraft body: the two contract fields
// as raw JSON (so a wrong type is a located 422) plus the unknown top-level
// members the contract's additionalProperties:false forbids.
type EvaluationRequest struct {
	DraftRevision     json.RawMessage
	DatasetArtifactID json.RawMessage
	UnknownFields     []string
}

// EvaluationPlan is a structurally valid submission plus its canonical identity.
type EvaluationPlan struct {
	DraftRevision     int64
	DatasetArtifactID string
	RequestSHA256     string
}

// EvaluationJobView is exactly the contract's EvaluationJob: six fields,
// additionalProperties:false. Nothing internal (snapshot id, dataset id,
// application id, progress) is ever serialised here.
type EvaluationJobView struct {
	ID              string  `json:"id"`
	Kind            string  `json:"kind"`
	State           string  `json:"state"`
	BackgroundJobID string  `json:"background_job_id"`
	ReportID        *string `json:"report_id"`
	ErrorCode       *string `json:"error_code"`
}

// EvaluationSubmission is the result of one evaluateDraft call.
type EvaluationSubmission struct {
	Evaluation *EvaluationJobView
	Replayed   bool
}

// DatasetRefusedError carries every located dataset problem. It is the
// evaluation counterpart of *ExecutionRefusedError: the client gets a 422 with
// the list of pointers, never a generic "invalid dataset".
type DatasetRefusedError struct {
	Errors []evaluation.Error
}

func (e *DatasetRefusedError) Error() string {
	if len(e.Errors) == 0 {
		return "the evaluation dataset is not valid"
	}
	return "the evaluation dataset is not valid: " + e.Errors[0].Error()
}

// ValidateEvaluationRequest checks the contract body's shape and produces the
// canonical request identity. It touches no database.
func ValidateEvaluationRequest(request EvaluationRequest) (*EvaluationPlan, error) {
	if len(request.UnknownFields) > 0 {
		unknown := append([]string(nil), request.UnknownFields...)
		sort.Strings(unknown)
		return nil, &ValidationError{
			Field:   unknown[0],
			Message: "evaluateDraft accepts exactly draft_revision and dataset_artifact_id",
		}
	}
	plan := &EvaluationPlan{}
	if len(bytes.TrimSpace(request.DraftRevision)) == 0 {
		return nil, &ValidationError{Field: "draft_revision", Message: "draft_revision is required"}
	}
	if err := json.Unmarshal(request.DraftRevision, &plan.DraftRevision); err != nil {
		return nil, &ValidationError{Field: "draft_revision", Message: "draft_revision must be an integer"}
	}
	if plan.DraftRevision < 1 {
		return nil, &ValidationError{Field: "draft_revision", Message: "draft_revision must be at least 1"}
	}
	if len(bytes.TrimSpace(request.DatasetArtifactID)) == 0 {
		return nil, &ValidationError{Field: "dataset_artifact_id", Message: "dataset_artifact_id is required"}
	}
	var datasetID string
	if err := json.Unmarshal(request.DatasetArtifactID, &datasetID); err != nil {
		return nil, &ValidationError{Field: "dataset_artifact_id", Message: "dataset_artifact_id must be a UUID"}
	}
	if !uuid.Valid(datasetID) {
		return nil, &ValidationError{Field: "dataset_artifact_id", Message: "dataset_artifact_id must be a UUID"}
	}
	plan.DatasetArtifactID = strings.ToLower(datasetID)
	return plan, nil
}

// evaluationRequestDigest is the canonical request fingerprint stored in the
// idempotency receipt: SHA-256 of the JCS bytes of
// {application_id, dataset_artifact_id, draft_revision}.
func evaluationRequestDigest(applicationID string, plan *EvaluationPlan) (string, error) {
	document := struct {
		ApplicationID     string `json:"application_id"`
		DatasetArtifactID string `json:"dataset_artifact_id"`
		DraftRevision     int64  `json:"draft_revision"`
	}{
		ApplicationID:     applicationID,
		DatasetArtifactID: plan.DatasetArtifactID,
		DraftRevision:     plan.DraftRevision,
	}
	encoded, err := json.Marshal(document)
	if err != nil {
		return "", fmt.Errorf("encoding the idempotency request digest: %w", err)
	}
	canonical, err := jcs.CanonicalizeBytes(encoded)
	if err != nil {
		return "", fmt.Errorf("canonicalising the idempotency request digest: %w", err)
	}
	return jcs.SHA256Hex(canonical), nil
}

// evaluationDatasetFact is what the submit path verified about the dataset
// artifact before it read the bytes.
type evaluationDatasetFact struct {
	ObjectKey string
	SHA256    string
	SizeBytes int64
}

// SubmitEvaluation validates and creates one evaluation.
//
// Order (shared with the API's error mapping):
//
//  0. body shape (422)
//  1. stored receipt (replay or 409)
//  2. owner-only draft read (404)
//  3. current draft revision (409)
//  4. dataset artifact: exists, owned, ready, evaluation_dataset, JSON (422)
//  5. dataset document: strict parse against the case schema (422 located)
//  6. ValidateExecution (422/503) — definition, card, dependencies
//  7. one transaction: receipt + snapshot + evaluation + queued job
func (r *Reader) SubmitEvaluation(
	ctx context.Context,
	callerID, applicationID string,
	scope IdempotencyScope,
	request EvaluationRequest,
	objects *objectstore.Store,
	inputs compiler.Inputs,
) (*EvaluationSubmission, error) {
	plan, err := ValidateEvaluationRequest(request)
	if err != nil {
		return nil, err
	}
	if !uuid.Valid(applicationID) {
		return nil, ErrNotVisible
	}
	if strings.TrimSpace(scope.PrincipalID) == "" || !strings.EqualFold(scope.PrincipalID, callerID) ||
		strings.TrimSpace(scope.Key) == "" || scope.Method != debugIdempotencyMethod {
		return nil, &ValidationError{Field: "idempotency_key", Message: "a complete idempotency scope is required"}
	}
	digest, err := evaluationRequestDigest(applicationID, plan)
	if err != nil {
		return nil, err
	}
	plan.RequestSHA256 = digest

	// 1. A committed receipt answers immediately.
	existing, err := r.lookupIdempotency(ctx, scope)
	if err != nil {
		return nil, err
	}
	if existing != nil {
		return r.replayEvaluationIdempotency(ctx, existing, digest)
	}

	// 2. Owner only.
	draft, ownerID, err := r.loadDraft(ctx, applicationID)
	if err != nil {
		return nil, err
	}
	if !strings.EqualFold(callerID, ownerID) {
		return nil, ErrNotVisible
	}
	if draft.Revision != plan.DraftRevision {
		return nil, &RevisionConflictError{
			RequestedRevision: plan.DraftRevision,
			CurrentRevision:   draft.Revision,
		}
	}

	// 4. The dataset artifact must be the caller's own, ready and a dataset.
	fact, err := r.checkEvaluationDataset(ctx, callerID, plan.DatasetArtifactID)
	if err != nil {
		return nil, err
	}

	// 5. Read the sealed bytes and parse them strictly.
	if objects == nil {
		return nil, objectstore.ErrNotConfigured
	}
	dataset, err := readSealedDataset(objects, fact)
	if err != nil {
		return nil, err
	}
	parsed, err := evaluation.ParseDataset(dataset)
	if err != nil {
		var located *evaluation.Error
		if errors.As(err, &located) {
			return nil, &DatasetRefusedError{Errors: []evaluation.Error{*located}}
		}
		return nil, err
	}

	// 6. Compile with the author-private dependency rule (an evaluation is the
	// author's own private execution, exactly like a debug run).
	execution, located, err := r.ValidateExecution(
		ctx, callerID, applicationID, objects, inputs, ExecutionOptions{PrivateDebug: true},
	)
	if err != nil {
		return nil, err
	}
	if len(located) > 0 {
		return nil, &ExecutionRefusedError{Errors: located}
	}
	if execution == nil || execution.Manifest == nil {
		return nil, errors.New("evaluation validation produced no manifest")
	}
	if _, _, _, problem := debugPolicy(execution.Manifest.Definition); problem != nil {
		return nil, &ExecutionRefusedError{Errors: []ExecutableError{*problem}}
	}

	submission, err := r.createEvaluation(ctx, createEvaluationParams{
		CallerID:      callerID,
		ApplicationID: applicationID,
		Scope:         scope,
		Plan:          plan,
		Execution:     execution,
		Dataset:       fact,
		CaseCount:     len(parsed.Cases),
	})
	if err != nil {
		return nil, err
	}
	return submission, nil
}

// checkEvaluationDataset verifies the dataset artifact row before any bytes are
// read. Every refusal is located at the contract field that named it.
func (r *Reader) checkEvaluationDataset(
	ctx context.Context, callerID, datasetArtifactID string,
) (*evaluationDatasetFact, error) {
	var (
		ownerID   string
		objectKey string
		state     string
		deleted   sql.NullTime
		purpose   string
		mediaType string
		sha256    sql.NullString
		sizeBytes sql.NullInt64
	)
	err := r.DB.QueryRowContext(ctx, `
		SELECT owner_id::text, object_key, state, deleted_at, purpose, media_type,
		       sha256, size_bytes
		  FROM platform.artifacts
		 WHERE id = $1::uuid`, datasetArtifactID,
	).Scan(&ownerID, &objectKey, &state, &deleted, &purpose, &mediaType, &sha256, &sizeBytes)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, &ValidationError{
			Field:   "dataset_artifact_id",
			Message: "the referenced dataset artifact does not exist",
		}
	}
	if err != nil {
		return nil, fmt.Errorf("reading the evaluation dataset: %w", err)
	}
	switch {
	case !strings.EqualFold(ownerID, callerID):
		return nil, &ValidationError{
			Field: "dataset_artifact_id",
			Message: "the referenced dataset artifact belongs to another principal; " +
				"an evaluation may only use the caller's own dataset",
		}
	case purpose != EvaluationDatasetPurpose:
		return nil, &ValidationError{
			Field: "dataset_artifact_id",
			Message: "the referenced artifact is not an evaluation dataset " +
				"(purpose must be " + EvaluationDatasetPurpose + ")",
		}
	case mediaType != EvaluationDatasetMediaType:
		return nil, &ValidationError{
			Field:   "dataset_artifact_id",
			Message: "an evaluation dataset must be " + EvaluationDatasetMediaType,
		}
	case deleted.Valid || state == "deleted" || state == "quarantined":
		return nil, &ValidationError{
			Field:   "dataset_artifact_id",
			Message: "the referenced dataset artifact is deleted or quarantined",
		}
	case state != "ready" || !sha256.Valid || !sizeBytes.Valid:
		return nil, &ValidationError{
			Field:   "dataset_artifact_id",
			Message: "the referenced dataset artifact is not ready (state=" + state + ")",
		}
	case sizeBytes.Int64 <= 0 || sizeBytes.Int64 > evaluation.MaxDatasetBytes:
		return nil, &ValidationError{
			Field: "dataset_artifact_id",
			Message: fmt.Sprintf("an evaluation dataset must be between 1 and %d bytes",
				evaluation.MaxDatasetBytes),
		}
	}
	return &evaluationDatasetFact{
		ObjectKey: objectKey,
		SHA256:    strings.ToLower(sha256.String),
		SizeBytes: sizeBytes.Int64,
	}, nil
}

// readSealedDataset reads the dataset bytes and re-verifies them against the
// artifact row's recorded hash: the bytes the parser validated must be the bytes
// the database says were sealed.
func readSealedDataset(store *objectstore.Store, fact *evaluationDatasetFact) ([]byte, error) {
	reader, _, err := store.Open(fact.ObjectKey)
	if err != nil {
		if errors.Is(err, objectstore.ErrNotExist) || errors.Is(err, objectstore.ErrInvalidKey) {
			return nil, &ValidationError{
				Field:   "dataset_artifact_id",
				Message: "the referenced dataset artifact has no sealed bytes",
			}
		}
		return nil, fmt.Errorf("opening the evaluation dataset: %w", err)
	}
	defer func() { _ = reader.Close() }()
	content, err := io.ReadAll(io.LimitReader(reader, evaluation.MaxDatasetBytes+1))
	if err != nil {
		return nil, fmt.Errorf("reading the evaluation dataset: %w", err)
	}
	if int64(len(content)) > evaluation.MaxDatasetBytes {
		return nil, &ValidationError{
			Field:   "dataset_artifact_id",
			Message: "the evaluation dataset exceeds the size limit",
		}
	}
	if digest := jcs.SHA256Hex(content); digest != fact.SHA256 {
		return nil, &ValidationError{
			Field:   "dataset_artifact_id",
			Message: "the evaluation dataset bytes do not match the sealed hash; the artifact is not trustworthy",
		}
	}
	return content, nil
}

// createEvaluationParams is the validated state createEvaluation needs.
type createEvaluationParams struct {
	CallerID      string
	ApplicationID string
	Scope         IdempotencyScope
	Plan          *EvaluationPlan
	Execution     *ValidatedExecution
	Dataset       *evaluationDatasetFact
	CaseCount     int
}

// createEvaluation writes the receipt, snapshot, evaluation and queued job in one
// transaction.
func (r *Reader) createEvaluation(
	ctx context.Context, params createEvaluationParams,
) (*EvaluationSubmission, error) {
	tx, err := r.DB.BeginTx(ctx, nil)
	if err != nil {
		return nil, fmt.Errorf("starting the evaluation transaction: %w", err)
	}
	defer func() { _ = tx.Rollback() }()

	result, err := tx.ExecContext(ctx, `
		INSERT INTO platform.idempotency_keys
		       (principal_id, method, path, key, request_sha256, expires_at)
		VALUES ($1::uuid, $2, $3, $4, $5, now() + $6::interval)
		ON CONFLICT (principal_id, method, path, key) DO NOTHING`,
		params.Scope.PrincipalID, params.Scope.Method, params.Scope.Path, params.Scope.Key,
		params.Plan.RequestSHA256, intervalOf(IdempotencyTTL),
	)
	if err != nil {
		return nil, fmt.Errorf("claiming the idempotency receipt: %w", err)
	}
	claimed, err := result.RowsAffected()
	if err != nil {
		return nil, fmt.Errorf("reading the idempotency claim: %w", err)
	}
	if claimed == 0 {
		_ = tx.Rollback()
		winner, lookupErr := r.lookupIdempotency(ctx, params.Scope)
		if lookupErr != nil {
			return nil, lookupErr
		}
		if winner == nil {
			return nil, errors.New("the idempotency claim conflicted but no receipt is visible")
		}
		return r.replayEvaluationIdempotency(ctx, winner, params.Plan.RequestSHA256)
	}

	// Freeze the draft revision under a row lock, exactly like debugDraft.
	var revision int64
	err = tx.QueryRowContext(ctx, `
		SELECT revision FROM platform.application_drafts
		 WHERE application_id = $1::uuid
		 FOR UPDATE`, params.ApplicationID).Scan(&revision)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrNotVisible
	}
	if err != nil {
		return nil, fmt.Errorf("locking the draft revision: %w", err)
	}
	if revision != params.Plan.DraftRevision {
		return nil, &RevisionConflictError{
			RequestedRevision: params.Plan.DraftRevision,
			CurrentRevision:   revision,
		}
	}

	snapshotID := uuid.NewString()
	evaluationID := uuid.NewString()

	manifestJSON, err := json.Marshal(params.Execution.Manifest)
	if err != nil {
		return nil, fmt.Errorf("encoding the snapshot manifest projection: %w", err)
	}
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO platform.execution_snapshots
		       (id, kind, application_id, application_version_id, draft_revision, owner_id,
		        manifest, manifest_sha256, manifest_artifact_id, compiler_version,
		        worker_image_digest, state_schema_version)
		VALUES ($1::uuid, 'debug', $2::uuid, NULL, $3, $4::uuid, $5::jsonb, $6, $7::uuid,
		        $8, $9, $10)`,
		snapshotID, params.ApplicationID, params.Plan.DraftRevision, params.CallerID,
		string(manifestJSON), params.Execution.SHA256, params.Execution.ArtifactID,
		params.Execution.Manifest.CompilerVersion, params.Execution.Manifest.WorkerImageDigest,
		params.Execution.Manifest.StateSchemaVersion,
	); err != nil {
		return nil, fmt.Errorf("creating the evaluation execution snapshot: %w", err)
	}

	job, _, err := background.EnqueueTx(ctx, tx, background.EnqueueParams{
		Kind:            background.KindApplicationEvaluate,
		TargetID:        evaluationID,
		OwnerID:         params.CallerID,
		DedupKey:        evaluationID,
		InputArtifactID: params.Plan.DatasetArtifactID,
	})
	if err != nil {
		return nil, fmt.Errorf("queueing the evaluation job: %w", err)
	}

	if _, err := tx.ExecContext(ctx, `
		INSERT INTO platform.evaluation_jobs
		       (id, kind, owner_id, background_job_id, snapshot_id,
		        model_profile_version_id, dataset_artifact_id, report_id, state,
		        error_code, application_id, progress_total, progress_done)
		VALUES ($1::uuid, 'application', $2::uuid, $3::uuid, $4::uuid,
		        NULL, $5::uuid, NULL, 'queued', NULL, $6::uuid, $7, 0)`,
		evaluationID, params.CallerID, job.ID, snapshotID,
		params.Plan.DatasetArtifactID, params.ApplicationID, params.CaseCount,
	); err != nil {
		return nil, fmt.Errorf("recording the evaluation: %w", err)
	}

	view := &EvaluationJobView{
		ID:              evaluationID,
		Kind:            "application",
		State:           "queued",
		BackgroundJobID: job.ID,
	}
	payload, err := json.Marshal(view)
	if err != nil {
		return nil, fmt.Errorf("encoding the evaluation response: %w", err)
	}
	if _, err := tx.ExecContext(ctx, `
		UPDATE platform.idempotency_keys
		   SET response_code = $5, response_json = $6::jsonb
		 WHERE principal_id = $1::uuid AND method = $2 AND path = $3 AND key = $4`,
		params.Scope.PrincipalID, params.Scope.Method, params.Scope.Path, params.Scope.Key,
		evaluationAcceptedStatus, string(payload),
	); err != nil {
		return nil, fmt.Errorf("storing the idempotent evaluation response: %w", err)
	}

	if err := tx.Commit(); err != nil {
		return nil, fmt.Errorf("committing the evaluation submission: %w", err)
	}
	return &EvaluationSubmission{Evaluation: view, Replayed: false}, nil
}

// replayEvaluationIdempotency turns one receipt into the stored 202 body.
func (r *Reader) replayEvaluationIdempotency(
	ctx context.Context, record *idempotencyRecord, digest string,
) (*EvaluationSubmission, error) {
	if record.RequestSHA256 != digest {
		return nil, &IdempotencyConflictError{}
	}
	if !record.ResponseCode.Valid || record.ResponseCode.Int64 != evaluationAcceptedStatus ||
		!record.ResponseJSON.Valid {
		return nil, errors.New("the idempotency receipt has no stored 202 response")
	}
	var view EvaluationJobView
	if err := json.Unmarshal([]byte(record.ResponseJSON.String), &view); err != nil {
		return nil, fmt.Errorf("decoding the stored idempotent response: %w", err)
	}
	return &EvaluationSubmission{Evaluation: &view, Replayed: true}, nil
}
