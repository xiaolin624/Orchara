package execution

// F13A/T052: the evaluation runner's run creation and independent fact reads.
//
// The maintenance handler may not touch platform tables. This service is the
// server half of the two job-scoped internal endpoints the handler uses:
//
//	POST /internal/v1/background/jobs/{job_id}/evaluation-runs
//	POST /internal/v1/background/jobs/{job_id}/evaluation-runs/{run_id}/facts
//
// Creating a case run is the one place an evaluation borrows the platform's
// official run path. It behaves exactly like a debug submission of the frozen
// snapshot:
//
//	sealed case input (owner = evaluation owner)
//	  -> runs(kind=debug, state=queued, frozen snapshot, manifest_sha256)
//	  -> jobs(kind=start, state=ready)          <- claimed by cmd/supervisor exec
//	  -> run_artifacts(input) + run.status#1
//	  -> evaluation_case_runs(evaluation_id, case_id, run_id)
//
// It never compiles, never re-freezes a snapshot and never writes a run on
// behalf of anyone but the evaluation's owner. The ACL is re-proved on every
// call: the application must still belong to the evaluation owner and must not
// be archived, and the snapshot owner must equal the evaluation owner. A
// withdrawn application therefore turns a collection into a recorded refusal,
// not into a cached run.
//
// Reading facts is deliberately a *platform* read, not a handler read: the run
// row, its sealed output artifact, the real platform.operations ledger and the
// citation sources the output declares. No model trace, no tool-call text.

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"strings"
	"time"

	"orchara/platform/internal/background"
	"orchara/platform/internal/evaluation"
	"orchara/platform/internal/jcs"
	"orchara/platform/internal/objectstore"
	"orchara/platform/internal/uuid"
)

const (
	// evaluationRunInputPurpose is the artifact purpose of a case's sealed run
	// input. It reuses the debug submission's purpose because the run is the
	// same shape of private execution.
	evaluationRunInputPurpose = "run_input"
	// MaxEvaluationOutputBytes caps the run output document the collector will
	// read. A run output is a result document, not a data dump.
	MaxEvaluationOutputBytes int64 = 1 << 20
	// maxEvaluationCaseInputBytes caps one case's canonical input.
	maxEvaluationCaseInputBytes int64 = 1 << 20

	// evaluationProgressDoneSQL is the one definition of "a case is decided":
	// the runner recorded a verdict, or the case's run reached a rest state.
	// The creation hook, the facts hook and the runner's progress report all use
	// it, so none of them can lower the counter on a retry. $1 is the
	// evaluation id in every call site.
	evaluationProgressDoneSQL = `(SELECT count(*)
		FROM platform.evaluation_case_runs
		WHERE evaluation_id = $1::uuid
		  AND (verdict IS NOT NULL
		       OR state IN ('succeeded', 'failed', 'cancelled',
		                    'recovery_pending', 'needs_reconciliation')))`
	// evaluationProgressFailedSQL counts the runner's failed case verdicts.
	// $1 is the evaluation id.
	evaluationProgressFailedSQL = `(SELECT count(*)
		FROM platform.evaluation_case_runs
		WHERE evaluation_id = $1::uuid AND verdict = 'failed')`
)

// ErrEvaluationNotVisible means the evaluation cannot be collected for its
// owner any more: the application is archived or belongs to someone else, or
// the snapshot owner no longer matches. It is the same answer as "not found" so
// a caller cannot probe for foreign evaluations.
var ErrEvaluationNotVisible = errors.New("the evaluation is not collectable for its owner")

// ErrEvaluationRunNotFound means the run is not this evaluation's run.
var ErrEvaluationRunNotFound = errors.New("that evaluation run does not exist")

// ErrTaskInputUnavailable means a case named a caller fixture by content that is
// not among the evaluation owner's ready task_input artifacts. It is a located
// refusal (the case cannot be executed and must never be invented), not a
// retryable infrastructure failure: uploading the exact bytes fixes it.
var ErrTaskInputUnavailable = errors.New("the case's task_input fixture is unavailable")

// EvaluationRunRef is one case's created (or already-created) platform run.
type EvaluationRunRef struct {
	RunID   string
	Created bool
}

// EvaluationRunFacts is the independent truth the collector decides on.
type EvaluationRunFacts struct {
	RunID              string
	State              string
	ErrorCode          string
	OutputArtifactID   *string
	OutputSHA256       string
	Output             []byte
	OutputCollected    bool
	Effects            map[string]int64
	EffectsCollected   bool
	Citations          []string
	CitationsCollected bool
	// F13A/T056: the caller fixture bound to the run, by artifact id and
	// content hash. Empty means no fixture was bound.
	TaskInputArtifactID string
	TaskInputSHA256     string
	Provenance          evaluation.RunProvenance
	StartedAt           *time.Time
	FinishedAt          *time.Time
}

// EvaluationRuns is the server-side evaluation run service.
type EvaluationRuns struct {
	DB    *sql.DB
	Store *objectstore.Store
	Jobs  *background.Controller
}

// NewEvaluationRuns builds the service.
func NewEvaluationRuns(db *sql.DB, store *objectstore.Store, jobs *background.Controller) *EvaluationRuns {
	return &EvaluationRuns{DB: db, Store: store, Jobs: jobs}
}

// evaluationScope is the authorization-relevant fact set of one evaluation.
type evaluationScope struct {
	OwnerID            string
	SnapshotID         string
	ApplicationID      string
	ApplicationOwnerID string
	ArchivedAt         *time.Time
	SnapshotOwnerID    string
	ManifestSHA256     string
	ManifestJSON       string
	CompilerVersion    string
	WorkerImageDigest  string
	StateSchemaVersion string
	DraftRevision      int64
}

// loadEvaluationScope reads and authorizes one evaluation for its own job.
func (e *EvaluationRuns) loadEvaluationScope(
	ctx context.Context, job *background.Job,
) (*evaluationScope, error) {
	if job == nil {
		return nil, background.ErrEvaluationNotFound
	}
	binding, err := e.Jobs.ResolveEvaluationBinding(ctx, job)
	if err != nil {
		return nil, err
	}
	if binding.Kind != "application" {
		// Only an application evaluation has runs to collect; a model
		// evaluation is the model-validation pipeline's.
		return nil, background.ErrEvaluationNotApplicable
	}
	var (
		scope      evaluationScope
		archivedAt sql.NullTime
	)
	err = e.DB.QueryRowContext(ctx, `
		SELECT ev.owner_id::text, ev.snapshot_id::text, ev.application_id::text,
		       app.owner_id::text, app.archived_at,
		       snap.owner_id::text, snap.manifest_sha256, snap.manifest::text,
		       snap.compiler_version, snap.worker_image_digest, snap.state_schema_version,
		       snap.draft_revision
		  FROM platform.evaluation_jobs AS ev
		  JOIN platform.applications AS app ON app.id = ev.application_id
		  JOIN platform.execution_snapshots AS snap ON snap.id = ev.snapshot_id
		 WHERE ev.id = $1::uuid
		   AND ev.background_job_id = $2::uuid`,
		binding.EvaluationID, job.ID,
	).Scan(
		&scope.OwnerID, &scope.SnapshotID, &scope.ApplicationID,
		&scope.ApplicationOwnerID, &archivedAt,
		&scope.SnapshotOwnerID, &scope.ManifestSHA256, &scope.ManifestJSON,
		&scope.CompilerVersion, &scope.WorkerImageDigest, &scope.StateSchemaVersion,
		&scope.DraftRevision,
	)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrEvaluationNotVisible
	}
	if err != nil {
		return nil, fmt.Errorf("reading the evaluation scope: %w", err)
	}
	if archivedAt.Valid {
		value := archivedAt.Time
		scope.ArchivedAt = &value
	}
	// The ACL the collector must not bypass: the run is created as the
	// evaluation's owner, for the owner's own application, on the owner's own
	// snapshot.
	if !strings.EqualFold(scope.OwnerID, job.OwnerID) ||
		!strings.EqualFold(scope.ApplicationOwnerID, scope.OwnerID) ||
		!strings.EqualFold(scope.SnapshotOwnerID, scope.OwnerID) ||
		scope.ArchivedAt != nil {
		return nil, ErrEvaluationNotVisible
	}
	return &scope, nil
}

// Create creates (or returns) the platform run for one case of the job's
// evaluation. The input bytes are the case's canonical run input. A non-nil
// taskInput binds one of the evaluation owner's own ready fixtures to the run as
// its task_input artifact (F13A/T056), addressed by content so a committed
// dataset never embeds a volatile artifact id.
func (e *EvaluationRuns) Create(
	ctx context.Context, job *background.Job, caseID string, input []byte,
	taskInput *evaluation.TaskInputBinding,
) (*EvaluationRunRef, error) {
	caseID = strings.TrimSpace(caseID)
	if caseID == "" || len(caseID) > evaluation.MaxCaseIDLength {
		return nil, &background.ValidationError{Field: "case_id", Message: "case_id must be a non-empty string"}
	}
	if int64(len(input)) == 0 || int64(len(input)) > maxEvaluationCaseInputBytes {
		return nil, &background.ValidationError{
			Field:   "input",
			Message: fmt.Sprintf("a case input must be between 1 and %d bytes", maxEvaluationCaseInputBytes),
		}
	}
	var inputObject map[string]json.RawMessage
	if err := json.Unmarshal(input, &inputObject); err != nil || inputObject == nil {
		return nil, &background.ValidationError{Field: "input", Message: "a case input must be a JSON object"}
	}
	canonical, err := jcs.CanonicalizeBytes(input)
	if err != nil {
		return nil, &background.ValidationError{
			Field: "input", Message: "a case input must be canonicalisable JSON (RFC 8785 JCS)",
		}
	}

	scope, err := e.loadEvaluationScope(ctx, job)
	if err != nil {
		return nil, err
	}

	// One run per case: a retried attempt returns the run it already created
	// instead of creating a second one.
	if existing, err := e.existingCaseRun(ctx, scope, job, caseID); err != nil {
		return nil, err
	} else if existing != nil {
		return existing, nil
	}

	// Resolve the fixture before sealing anything: the case cannot be executed
	// without it, and a missing fixture must leave no run behind.
	var taskInputArtifactID string
	if taskInput != nil {
		resolved, err := e.resolveTaskInput(ctx, scope, taskInput)
		if err != nil {
			return nil, err
		}
		taskInputArtifactID = resolved
	}

	if e.Store == nil {
		return nil, background.ErrObjectStoreUnavailable
	}
	policy, problem := evaluationRunPolicy(scope.ManifestJSON)
	if problem != "" {
		return nil, fmt.Errorf("the frozen snapshot manifest has no usable policy: %s", problem)
	}
	sealedKey, err := objectstore.SealedKey()
	if err != nil {
		return nil, err
	}
	digest := jcs.SHA256Hex(canonical)
	object, err := e.Store.PutSealed(sealedKey, bytes.NewReader(canonical), int64(len(canonical)))
	if err != nil {
		return nil, fmt.Errorf("sealing the case input: %w", err)
	}
	if object.SHA256 != digest || object.Size != int64(len(canonical)) {
		_ = e.Store.Delete(sealedKey)
		return nil, errors.New("the sealed case input does not match its canonical bytes")
	}

	ref, err := e.insertCaseRun(ctx, scope, job, caseID, sealedKey, digest, int64(len(canonical)),
		taskInputArtifactID, policy)
	if err != nil {
		// Nothing references the object when the transaction did not commit.
		_ = e.Store.Delete(sealedKey)
		if errors.Is(err, errCaseRunAlreadyExists) {
			if existing, readErr := e.existingCaseRun(ctx, scope, job, caseID); readErr == nil && existing != nil {
				return existing, nil
			}
		}
		return nil, err
	}
	return ref, nil
}

// maxEvaluationTaskInputBytes mirrors the preset tool's input cap. The T028
// admission already bounds a task_input artifact, so this is defence in depth.
const maxEvaluationTaskInputBytes int64 = 20 << 20

// resolveTaskInput finds the evaluation owner's own ready fixture by content.
//
// The lookup is content-addressed on purpose: a committed dataset names bytes
// (sha256 + media type), never an object key or a volatile artifact id, and only
// the evaluation owner's own artifacts are considered. When the same bytes were
// uploaded more than once the earliest artifact wins deterministically; the
// chosen id is recorded in the case facts, so the sealed result names exactly
// which platform artifact was bound.
func (e *EvaluationRuns) resolveTaskInput(
	ctx context.Context, scope *evaluationScope, binding *evaluation.TaskInputBinding,
) (string, error) {
	sha := strings.ToLower(strings.TrimSpace(binding.SHA256))
	mediaType := strings.TrimSpace(binding.MediaType)
	if len(sha) != 64 || (mediaType != evaluation.MediaTypeCSV && mediaType != evaluation.MediaTypeXLSX) {
		return "", &background.ValidationError{
			Field:   "task_input",
			Message: "task_input must name a SHA-256 and the CSV or XLSX media type",
		}
	}
	var (
		artifactID string
		sizeBytes  sql.NullInt64
	)
	err := e.DB.QueryRowContext(ctx, `
		SELECT id::text, size_bytes
		  FROM platform.artifacts
		 WHERE owner_id = $1::uuid
		   AND purpose = 'task_input'
		   AND sha256 = $2
		   AND media_type = $3
		   AND state = 'ready'
		   AND deleted_at IS NULL
		 ORDER BY created_at, id
		 LIMIT 1`, scope.OwnerID, sha, mediaType,
	).Scan(&artifactID, &sizeBytes)
	if errors.Is(err, sql.ErrNoRows) {
		return "", ErrTaskInputUnavailable
	}
	if err != nil {
		return "", fmt.Errorf("resolving the case task_input: %w", err)
	}
	if !sizeBytes.Valid || sizeBytes.Int64 <= 0 || sizeBytes.Int64 > maxEvaluationTaskInputBytes {
		return "", fmt.Errorf("the bound task_input artifact size is outside the tool protocol")
	}
	return artifactID, nil
}

var errCaseRunAlreadyExists = errors.New("the case run already exists")

// existingCaseRun returns the run already recorded for this case, or nil.
func (e *EvaluationRuns) existingCaseRun(
	ctx context.Context, scope *evaluationScope, job *background.Job, caseID string,
) (*EvaluationRunRef, error) {
	var runID sql.NullString
	err := e.DB.QueryRowContext(ctx, `
		SELECT run_id::text
		  FROM platform.evaluation_case_runs
		 WHERE evaluation_id = $1::uuid AND case_id = $2`,
		scope.evaluationID(job), caseID,
	).Scan(&runID)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("reading the case run: %w", err)
	}
	if !runID.Valid || runID.String == "" {
		return nil, nil
	}
	return &EvaluationRunRef{RunID: runID.String, Created: false}, nil
}

// evaluationID is the evaluation this scope was loaded for. It is derived from
// the job's own target so a scope value can never name another evaluation.
func (s *evaluationScope) evaluationID(job *background.Job) string { return job.TargetID }

// runPolicy is the frozen run policy of the snapshot.
type runPolicy struct {
	Budget      string
	Currency    string
	MaxActiveMS int64
}

// evaluationRunPolicy reads the policy out of the frozen manifest projection.
func evaluationRunPolicy(manifestJSON string) (runPolicy, string) {
	var manifest struct {
		Definition struct {
			Policy struct {
				BudgetLimit      string `json:"budget_limit"`
				Currency         string `json:"currency"`
				MaxActiveSeconds int64  `json:"max_active_seconds"`
			} `json:"policy"`
		} `json:"definition"`
	}
	if err := json.Unmarshal([]byte(manifestJSON), &manifest); err != nil {
		return runPolicy{}, "the manifest projection is not JSON"
	}
	policy := runPolicy{
		Budget:      manifest.Definition.Policy.BudgetLimit,
		Currency:    manifest.Definition.Policy.Currency,
		MaxActiveMS: manifest.Definition.Policy.MaxActiveSeconds * 1000,
	}
	if policy.Budget == "" || len(policy.Currency) != 3 || policy.MaxActiveMS <= 0 {
		return runPolicy{}, "the definition policy is incomplete"
	}
	return policy, ""
}

// insertCaseRun writes the case's run, start job, input link, first status
// event and its case-run row in one transaction.
func (e *EvaluationRuns) insertCaseRun(
	ctx context.Context,
	scope *evaluationScope,
	job *background.Job,
	caseID, sealedKey, inputSHA string,
	inputSize int64,
	taskInputArtifactID string,
	policy runPolicy,
) (*EvaluationRunRef, error) {
	tx, err := e.DB.BeginTx(ctx, nil)
	if err != nil {
		return nil, fmt.Errorf("starting the case run transaction: %w", err)
	}
	defer func() { _ = tx.Rollback() }()

	// Claim the case row first; a concurrent create loses here and re-reads the
	// winner's run instead of creating a second one.
	result, err := tx.ExecContext(ctx, `
		INSERT INTO platform.evaluation_case_runs (evaluation_id, case_id, ordinal, state)
		VALUES ($1::uuid, $2, COALESCE((
		            SELECT max(ordinal) + 1 FROM platform.evaluation_case_runs
		             WHERE evaluation_id = $1::uuid), 0), 'pending')
		ON CONFLICT (evaluation_id, case_id) DO NOTHING`,
		job.TargetID, caseID,
	)
	if err != nil {
		return nil, fmt.Errorf("claiming the evaluation case: %w", err)
	}
	claimed, err := result.RowsAffected()
	if err != nil {
		return nil, fmt.Errorf("reading the case claim: %w", err)
	}
	if claimed == 0 {
		// Already claimed: a retry or a concurrent attempt owns it.
		var runID sql.NullString
		if err := tx.QueryRowContext(ctx, `
			SELECT run_id::text FROM platform.evaluation_case_runs
			 WHERE evaluation_id = $1::uuid AND case_id = $2`,
			job.TargetID, caseID,
		).Scan(&runID); err != nil {
			return nil, fmt.Errorf("reading the claimed case: %w", err)
		}
		_ = tx.Rollback()
		if runID.Valid && runID.String != "" {
			return &EvaluationRunRef{RunID: runID.String, Created: false}, nil
		}
		return nil, errCaseRunAlreadyExists
	}

	runID := uuid.NewString()
	jobID := uuid.NewString()
	inputArtifactID := uuid.NewString()
	checkpointThreadID := uuid.NewString()

	if _, err := tx.ExecContext(ctx, `
		INSERT INTO platform.artifacts
		       (id, owner_id, object_key, purpose, sha256, media_type, size_bytes, state, revision)
		VALUES ($1::uuid, $2::uuid, $3, $4, $5, 'application/json', $6, 'ready', 1)`,
		inputArtifactID, scope.OwnerID, sealedKey, evaluationRunInputPurpose,
		inputSHA, inputSize,
	); err != nil {
		return nil, fmt.Errorf("registering the case input artifact: %w", err)
	}

	if _, err := tx.ExecContext(ctx, `
		INSERT INTO platform.runs
		       (id, request_id, conversation_id, parent_run_id, parent_invocation_key,
		        owner_id, kind, execution_snapshot_id, application_version_id,
		        manifest_sha256, input_artifact_id, output_artifact_id,
		        checkpoint_thread_id, state, revision, next_event_seq,
		        budget_limit, currency, phase, active_elapsed_ms, max_active_ms)
		VALUES ($1::uuid, NULL, NULL, NULL, NULL, $2::uuid, 'debug', $3::uuid, NULL,
		        $4, $5::uuid, NULL, $6::uuid, 'queued', 1, 2, $7::numeric, $8, 'idle', 0, $9)`,
		runID, scope.OwnerID, scope.SnapshotID, scope.ManifestSHA256, inputArtifactID,
		checkpointThreadID, policy.Budget, policy.Currency, policy.MaxActiveMS,
	); err != nil {
		return nil, fmt.Errorf("creating the case run: %w", err)
	}

	// The first durable timeline fact (seq 1), in the same transaction as the
	// queued run. The event allocator in this package owns seq >= 2.
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO platform.run_events (run_id, seq, event_id, type, revision, payload)
		VALUES ($1::uuid, 1, $2::uuid, 'run.status', 1, jsonb_build_object('state', 'queued'))`,
		runID, uuid.NewString(),
	); err != nil {
		return nil, fmt.Errorf("recording the queued run event: %w", err)
	}

	if _, err := tx.ExecContext(ctx, `
		INSERT INTO platform.jobs (id, run_id, kind, state, available_at)
		VALUES ($1::uuid, $2::uuid, 'start', 'ready', now())`,
		jobID, runID,
	); err != nil {
		return nil, fmt.Errorf("creating the case start job: %w", err)
	}

	if _, err := tx.ExecContext(ctx, `
		INSERT INTO platform.run_artifacts (run_id, artifact_id, attempt_token, relation)
		VALUES ($1::uuid, $2::uuid, NULL, 'input')`,
		runID, inputArtifactID,
	); err != nil {
		return nil, fmt.Errorf("linking the case input artifact: %w", err)
	}

	// F13A/T056: the caller's fixture, resolved by content above, is linked the
	// same way a debug submission links its artifact_ids: relation=input, so the
	// supervisor discovers it through ListRunInputArtifacts and the run-scoped
	// download link serves its sealed bytes.
	if taskInputArtifactID != "" {
		if _, err := tx.ExecContext(ctx, `
			INSERT INTO platform.run_artifacts (run_id, artifact_id, attempt_token, relation)
			VALUES ($1::uuid, $2::uuid, NULL, 'input')`,
			runID, taskInputArtifactID,
		); err != nil {
			return nil, fmt.Errorf("linking the case task_input artifact: %w", err)
		}
	}

	if _, err := tx.ExecContext(ctx, `
		UPDATE platform.evaluation_case_runs
		   SET run_id = $3::uuid, state = 'queued', updated_at = now()
		 WHERE evaluation_id = $1::uuid AND case_id = $2`,
		job.TargetID, caseID, runID,
	); err != nil {
		return nil, fmt.Errorf("linking the case run: %w", err)
	}
	if _, err := tx.ExecContext(ctx, `
		UPDATE platform.evaluation_jobs
		   SET progress_total = GREATEST(progress_total, $2),
		       progress_done = `+evaluationProgressDoneSQL+`
		 WHERE id = $1::uuid`,
		job.TargetID, 1,
	); err != nil {
		return nil, fmt.Errorf("updating the evaluation progress: %w", err)
	}

	if err := tx.Commit(); err != nil {
		return nil, fmt.Errorf("committing the case run: %w", err)
	}
	return &EvaluationRunRef{RunID: runID, Created: true}, nil
}

// CaseRunOwner proves the run belongs to the job's evaluation and returns the
// owner identity a predefined interaction must be performed as. It is the ACL
// gate of the interaction bridge: the cancel is executed as the evaluation's
// owner, never as the maintenance process.
func (e *EvaluationRuns) CaseRunOwner(
	ctx context.Context, job *background.Job, runID string,
) (string, error) {
	scope, err := e.loadEvaluationScope(ctx, job)
	if err != nil {
		return "", err
	}
	var ownerID string
	err = e.DB.QueryRowContext(ctx, `
		SELECT r.owner_id::text
		  FROM platform.evaluation_case_runs AS cr
		  JOIN platform.runs AS r ON r.id = cr.run_id
		 WHERE cr.evaluation_id = $1::uuid
		   AND cr.run_id = $2::uuid
		   AND r.owner_id = $3::uuid`,
		job.TargetID, runID, scope.OwnerID,
	).Scan(&ownerID)
	if errors.Is(err, sql.ErrNoRows) {
		return "", ErrEvaluationRunNotFound
	}
	if err != nil {
		return "", fmt.Errorf("reading the case run owner: %w", err)
	}
	return ownerID, nil
}

// RunState reads one case run's current state for the interaction reply.
func (e *EvaluationRuns) RunState(
	ctx context.Context, job *background.Job, runID string,
) (string, error) {
	if _, err := e.loadEvaluationScope(ctx, job); err != nil {
		return "", err
	}
	var state string
	err := e.DB.QueryRowContext(ctx, `
		SELECT r.state
		  FROM platform.evaluation_case_runs AS cr
		  JOIN platform.runs AS r ON r.id = cr.run_id
		 WHERE cr.evaluation_id = $1::uuid
		   AND cr.run_id = $2::uuid`,
		job.TargetID, runID,
	).Scan(&state)
	if errors.Is(err, sql.ErrNoRows) {
		return "", ErrEvaluationRunNotFound
	}
	if err != nil {
		return "", fmt.Errorf("reading the case run state: %w", err)
	}
	return state, nil
}

// Facts reads one case run's independent truth. The run must belong to the
// job's evaluation, otherwise it answers exactly like a missing run.
func (e *EvaluationRuns) Facts(
	ctx context.Context, job *background.Job, runID string,
) (*EvaluationRunFacts, error) {
	scope, err := e.loadEvaluationScope(ctx, job)
	if err != nil {
		return nil, err
	}
	var (
		facts      evaluation.Facts
		errorCode  sql.NullString
		outputID   sql.NullString
		startedAt  sql.NullTime
		finishedAt sql.NullTime
	)
	facts.RunID = runID
	facts.Provenance = evaluation.RunProvenance{
		SnapshotID:         scope.SnapshotID,
		ManifestSHA256:     scope.ManifestSHA256,
		ApplicationID:      scope.ApplicationID,
		DraftRevision:      scope.DraftRevision,
		CompilerVersion:    scope.CompilerVersion,
		WorkerImageDigest:  scope.WorkerImageDigest,
		StateSchemaVersion: scope.StateSchemaVersion,
	}
	err = e.DB.QueryRowContext(ctx, `
		SELECT r.state, r.error_code, r.output_artifact_id::text,
		       r.started_at, r.finished_at
		  FROM platform.evaluation_case_runs AS cr
		  JOIN platform.runs AS r ON r.id = cr.run_id
		 WHERE cr.evaluation_id = $1::uuid
		   AND cr.run_id = $2::uuid`,
		job.TargetID, runID,
	).Scan(&facts.RunState, &errorCode, &outputID, &startedAt, &finishedAt)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrEvaluationRunNotFound
	}
	if err != nil {
		return nil, fmt.Errorf("reading the case run: %w", err)
	}
	facts.RunErrorCode = errorCode.String
	if outputID.Valid && outputID.String != "" {
		value := outputID.String
		facts.OutputArtifactID = &value
	}
	if startedAt.Valid {
		value := startedAt.Time
		facts.StartedAt = &value
	}
	if finishedAt.Valid {
		value := finishedAt.Time
		facts.FinishedAt = &value
	}

	// The sealed output document, when the run produced one.
	if facts.OutputArtifactID != nil {
		output, sha, collected := e.readRunOutput(ctx, *facts.OutputArtifactID, scope.OwnerID)
		if collected {
			facts.Output = output
			facts.OutputSHA256 = sha
			facts.OutputCollected = true
			facts.Citations, facts.CitationsCollected = citationsOf(output)
		}
	}

	// F13A/T056: the caller fixture bound to this run, read from the real
	// run_artifacts link. It is evidence for the per-case result; a case without
	// a fixture (a minimal start->end chain) simply has no such link.
	_ = e.DB.QueryRowContext(ctx, `
		SELECT a.id::text, a.sha256
		  FROM platform.run_artifacts AS ra
		  JOIN platform.artifacts AS a ON a.id = ra.artifact_id
		 WHERE ra.run_id = $1::uuid
		   AND ra.relation = 'input'
		   AND a.purpose = 'task_input'
		   AND a.state = 'ready'
		   AND a.deleted_at IS NULL
		 ORDER BY a.created_at, a.id
		 LIMIT 1`, runID,
	).Scan(&facts.TaskInputArtifactID, &facts.TaskInputSHA256)

	// The real side-effect ledger: one row per attempted controlled call.
	effects, collected, err := e.effectCounts(ctx, runID)
	if err != nil {
		return nil, err
	}
	facts.Effects = effects
	facts.EffectsCollected = collected

	// Worker provenance of the latest attempt, if any.
	_ = e.DB.QueryRowContext(ctx, `
		SELECT worker_id, execution_unit_id
		  FROM platform.run_attempts
		 WHERE run_id = $1::uuid
		 ORDER BY started_at DESC, token DESC
		 LIMIT 1`, runID,
	).Scan(&facts.Provenance.WorkerID, &facts.Provenance.ExecutionUnitID)

	// Record the observed state (progress hook for T065) in the same call that
	// reads it, so progress never becomes a second source of truth.
	if _, err := e.DB.ExecContext(ctx, `
		UPDATE platform.evaluation_case_runs
		   SET state = $3, updated_at = now()
		 WHERE evaluation_id = $1::uuid AND run_id = $2::uuid`,
		job.TargetID, runID, facts.RunState,
	); err != nil {
		return nil, fmt.Errorf("recording the case state: %w", err)
	}
	if _, err := e.DB.ExecContext(ctx, `
		UPDATE platform.evaluation_jobs
		   SET progress_done = `+evaluationProgressDoneSQL+`
		 WHERE id = $1::uuid`, job.TargetID,
	); err != nil {
		return nil, fmt.Errorf("updating the evaluation progress: %w", err)
	}

	return &EvaluationRunFacts{
		RunID:               runID,
		State:               facts.RunState,
		ErrorCode:           facts.RunErrorCode,
		OutputArtifactID:    facts.OutputArtifactID,
		OutputSHA256:        facts.OutputSHA256,
		Output:              facts.Output,
		OutputCollected:     facts.OutputCollected,
		Effects:             facts.Effects,
		EffectsCollected:    facts.EffectsCollected,
		Citations:           facts.Citations,
		CitationsCollected:  facts.CitationsCollected,
		TaskInputArtifactID: facts.TaskInputArtifactID,
		TaskInputSHA256:     facts.TaskInputSHA256,
		Provenance:          facts.Provenance,
		StartedAt:           facts.StartedAt,
		FinishedAt:          facts.FinishedAt,
	}, nil
}

// readRunOutput reads one run output artifact's sealed bytes and re-verifies
// them against the recorded hash. A non-collectable artifact is "not collected",
// never an empty document.
func (e *EvaluationRuns) readRunOutput(
	ctx context.Context, artifactID, ownerID string,
) ([]byte, string, bool) {
	var (
		objectKey string
		state     string
		deleted   sql.NullTime
		sha256    sql.NullString
		docOwner  string
		sizeBytes sql.NullInt64
	)
	err := e.DB.QueryRowContext(ctx, `
		SELECT object_key, state, deleted_at, sha256, owner_id::text, size_bytes
		  FROM platform.artifacts
		 WHERE id = $1::uuid`, artifactID,
	).Scan(&objectKey, &state, &deleted, &sha256, &docOwner, &sizeBytes)
	if err != nil || deleted.Valid || state != "ready" || !sha256.Valid ||
		!strings.EqualFold(docOwner, ownerID) || e.Store == nil {
		return nil, "", false
	}
	if sizeBytes.Valid && sizeBytes.Int64 > MaxEvaluationOutputBytes {
		return nil, "", false
	}
	reader, _, err := e.Store.Open(objectKey)
	if err != nil {
		return nil, "", false
	}
	defer func() { _ = reader.Close() }()
	content, err := io.ReadAll(io.LimitReader(reader, MaxEvaluationOutputBytes+1))
	if err != nil || int64(len(content)) > MaxEvaluationOutputBytes {
		return nil, "", false
	}
	if digest := jcs.SHA256Hex(content); digest != strings.ToLower(sha256.String) {
		return nil, "", false
	}
	return content, strings.ToLower(sha256.String), true
}

// effectCounts counts the run's real controlled-capability calls per capability.
func (e *EvaluationRuns) effectCounts(
	ctx context.Context, runID string,
) (map[string]int64, bool, error) {
	rows, err := e.DB.QueryContext(ctx, `
		SELECT capability_version, count(*)
		  FROM platform.operations
		 WHERE run_id = $1::uuid
		 GROUP BY capability_version`, runID)
	if err != nil {
		return nil, false, fmt.Errorf("reading the operation ledger: %w", err)
	}
	defer rows.Close()
	counts := make(map[string]int64)
	for rows.Next() {
		var (
			capability string
			count      int64
		)
		if err := rows.Scan(&capability, &count); err != nil {
			return nil, false, fmt.Errorf("reading the operation ledger: %w", err)
		}
		counts[capability] = count
	}
	if err := rows.Err(); err != nil {
		return nil, false, fmt.Errorf("reading the operation ledger: %w", err)
	}
	return counts, true, nil
}

// citationsOf reads the citation source ids out of a run output document. The
// shape is the RAG output convention: /citations is an array of source ids or
// of objects carrying source_id. A document without the member is "no citation
// declared", which the checker treats as zero citations rather than as an
// unknown fact.
func citationsOf(output []byte) ([]string, bool) {
	var document map[string]json.RawMessage
	if err := json.Unmarshal(output, &document); err != nil {
		return nil, false
	}
	raw, present := document["citations"]
	if !present {
		return []string{}, true
	}
	var items []json.RawMessage
	if err := json.Unmarshal(raw, &items); err != nil {
		return nil, false
	}
	citations := make([]string, 0, len(items))
	for _, item := range items {
		var id string
		if err := json.Unmarshal(item, &id); err == nil {
			citations = append(citations, id)
			continue
		}
		var object struct {
			SourceID string `json:"source_id"`
		}
		if err := json.Unmarshal(item, &object); err == nil && object.SourceID != "" {
			citations = append(citations, object.SourceID)
			continue
		}
		return nil, false
	}
	return citations, true
}
