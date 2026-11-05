package background

// Evaluation jobs (F00B/T026).
//
// An `application_evaluate` background job executes one `platform.evaluation_jobs`
// row. The job and the evaluation are two identities for the same work:
//
//   - the job owns delivery (claim, lease, retries, failure classification);
//   - the evaluation owns the business state (queued/running/succeeded/failed/
//     cancelled) and the immutable report it produced.
//
// The handler runs in the work pool and may not write either row: it reads its
// binding through a job-scoped internal endpoint, waits for the dataset through
// the job-scoped artifact-status endpoint, downloads/upload report bytes through
// signed links, and reports an outcome. This file is the server half:
//
//   - Claim marks the evaluation running in the same transaction that leases the
//     job (one attempt owns the work).
//   - Complete, for a claimed success, re-reads the sealed report artifact and
//     accepts it only when it parses, its manifest_sha256 equals the evaluation's
//     bound candidate/snapshot manifest, its dataset_sha256 equals the dataset
//     artifact's recorded sha256, and its passed/failed/not_run verdict agrees
//     with its own cases. Only then does it insert the immutable
//     evaluation_reports row, set evaluation_jobs.state=succeeded + report_id and
//     finish the job.
//   - A malformed or mismatched report is a *job* failure (report_invalid /
//     report_binding_mismatch) with no report row; a business evaluation verdict
//     is a report with status=failed while the job succeeds. An infrastructure
//     failure is a terminal job failure that also fails the evaluation and
//     creates no report.
//
// Read models here are owner-or-admin only and project the contract's fields;
// report bodies and artifact paths never leave the server.

import (
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"strings"
	"time"

	eval "orchara/platform/internal/evaluation"
	"orchara/platform/internal/objectstore"
	"orchara/platform/internal/uuid"
)

// MaxEvaluationReportBytes caps the report artifact the guard will read. It is
// deliberately far below the 20 MiB store limit: a report is metadata plus case
// results, not a data dump, and the guard must never parse an unbounded body.
const MaxEvaluationReportBytes int64 = 1 << 20

// EvaluationJob is one row of platform.evaluation_jobs (the contract's
// EvaluationJob plus the internal ids the server needs). Only the contract
// projection is returned over HTTP.
type EvaluationJob struct {
	ID                    string
	Kind                  string
	OwnerID               string
	BackgroundJobID       string
	SnapshotID            *string
	ModelProfileVersionID *string
	DatasetArtifactID     string
	ReportID              *string
	State                 string
	ErrorCode             *string
	CreatedAt             time.Time
	FinishedAt            *time.Time
}

// EvaluationReport is one row of platform.evaluation_reports. It is immutable
// and carries only the contract's Report fields plus its owner-lookup join.
type EvaluationReport struct {
	ID               string
	ManifestSHA256   string
	DatasetSHA256    string
	ResultArtifactID string
	Status           string
	CreatedAt        time.Time
}

// EvaluationBinding is what a handler needs to know about its evaluation:
// which evaluation it is, which kind, which dataset and which manifest hash the
// report must bind to. It is resolved server-side from the job's own target;
// the handler never supplies any of these ids.
type EvaluationBinding struct {
	EvaluationID      string
	Kind              string
	DatasetArtifactID string
	ManifestSHA256    string
	// F13A/T052: the frozen identity behind the hash. The runner copies these
	// into the report's provenance block so a result is traceable to an exact
	// snapshot, compiler build and worker image rather than to a hash alone.
	SnapshotID         string
	ApplicationID      string
	DraftRevision      int64
	CompilerVersion    string
	WorkerImageDigest  string
	StateSchemaVersion string
}

// Evaluation errors. The internal endpoint maps them to stable contract codes;
// handlers map those codes to job outcomes.
var (
	// ErrEvaluationNotFound is "no evaluation for this job, or not this caller's".
	ErrEvaluationNotFound = errors.New("evaluation not found")
	// ErrEvaluationNotApplicable is a job of another kind asking for evaluation data.
	ErrEvaluationNotApplicable = errors.New("this background job is not an evaluation")
	// ErrEvaluationBindingUnavailable means the evaluation exists but its bound
	// candidate/snapshot manifest cannot be resolved in this deployment (for
	// example kind=application before T027 creates execution_snapshots).
	ErrEvaluationBindingUnavailable = errors.New("the evaluation binding is not resolvable")
)

// errBindingUnavailable is the transaction-level form of
// ErrEvaluationBindingUnavailable; the guard turns it into a business code.
var errBindingUnavailable = errors.New("evaluation binding unavailable")

const selectEvaluationColumns = `
	id::text, kind, owner_id::text, background_job_id::text, snapshot_id::text,
	model_profile_version_id::text, dataset_artifact_id::text, report_id::text,
	state, error_code, created_at, finished_at`

func scanEvaluation(row interface{ Scan(dest ...any) error }) (*EvaluationJob, error) {
	var (
		evaluation EvaluationJob
		snapshot   sql.NullString
		model      sql.NullString
		report     sql.NullString
		errorCode  sql.NullString
		finished   sql.NullTime
	)
	if err := row.Scan(
		&evaluation.ID, &evaluation.Kind, &evaluation.OwnerID, &evaluation.BackgroundJobID,
		&snapshot, &model, &evaluation.DatasetArtifactID, &report, &evaluation.State,
		&errorCode, &evaluation.CreatedAt, &finished,
	); err != nil {
		return nil, err
	}
	evaluation.SnapshotID = nullString(snapshot)
	evaluation.ModelProfileVersionID = nullString(model)
	evaluation.ReportID = nullString(report)
	evaluation.ErrorCode = nullString(errorCode)
	if finished.Valid {
		value := finished.Time
		evaluation.FinishedAt = &value
	}
	return &evaluation, nil
}

// GetEvaluation returns one evaluation to its owner or an administrator.
//
// Everyone else gets ErrEvaluationNotFound, the same answer as a missing row, so
// the endpoint cannot enumerate ids. The caller's own id is compared in SQL.
func (c *Controller) GetEvaluation(
	ctx context.Context,
	evaluationID, principalID string,
	admin bool,
) (*EvaluationJob, error) {
	if !uuid.Valid(evaluationID) || !uuid.Valid(principalID) {
		return nil, ErrEvaluationNotFound
	}
	evaluation, err := scanEvaluation(c.DB.QueryRowContext(ctx, `
		SELECT `+selectEvaluationColumns+`
		  FROM platform.evaluation_jobs
		 WHERE id = $1::uuid
		   AND (owner_id = $2::uuid OR $3::boolean)`, evaluationID, principalID, admin))
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrEvaluationNotFound
	}
	if err != nil {
		return nil, fmt.Errorf("reading the evaluation: %w", err)
	}
	return evaluation, nil
}

// GetEvaluationReport returns one report to the owner of the evaluation that
// produced it, or to an administrator.
//
// evaluation_reports has no owner column: ownership is the evaluation row's, and
// the join is part of the authorization predicate. A report that no evaluation
// references answers exactly like a missing one.
func (c *Controller) GetEvaluationReport(
	ctx context.Context,
	reportID, principalID string,
	admin bool,
) (*EvaluationReport, error) {
	if !uuid.Valid(reportID) || !uuid.Valid(principalID) {
		return nil, ErrEvaluationNotFound
	}
	var (
		report EvaluationReport
	)
	err := c.DB.QueryRowContext(ctx, `
		SELECT r.id::text, r.manifest_sha256, r.dataset_sha256,
		       r.result_artifact_id::text, r.status, r.created_at
		  FROM platform.evaluation_reports AS r
		 WHERE r.id = $1::uuid
		   AND EXISTS (
		        SELECT 1 FROM platform.evaluation_jobs AS e
		         WHERE e.report_id = r.id
		           AND (e.owner_id = $2::uuid OR $3::boolean)
		   )`, reportID, principalID, admin,
	).Scan(
		&report.ID, &report.ManifestSHA256, &report.DatasetSHA256,
		&report.ResultArtifactID, &report.Status, &report.CreatedAt,
	)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrEvaluationNotFound
	}
	if err != nil {
		return nil, fmt.Errorf("reading the evaluation report: %w", err)
	}
	return &report, nil
}

// ResolveEvaluationBinding is the job-scoped read the handler uses to learn its
// evaluation facts. It proves the evaluation belongs to this exact job and
// resolves the bound manifest hash:
//
//	kind=model       -> the candidate model_profile_versions.manifest_sha256
//	kind=application -> the execution_snapshots.manifest_sha256 (T027+)
//
// A kind=application evaluation created in this batch is structurally valid but
// unresolvable: execution_snapshots does not exist yet, so the answer is
// ErrEvaluationBindingUnavailable and the handler fails the job honestly instead
// of inventing a manifest. T027 replaces that branch with the real table.
func (c *Controller) ResolveEvaluationBinding(ctx context.Context, job *Job) (*EvaluationBinding, error) {
	if job == nil || job.Kind != KindApplicationEvaluate {
		return nil, ErrEvaluationNotApplicable
	}
	evaluation, err := scanEvaluation(c.DB.QueryRowContext(ctx, `
		SELECT `+selectEvaluationColumns+`
		  FROM platform.evaluation_jobs
		 WHERE id = $1::uuid`, job.TargetID))
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrEvaluationNotFound
	}
	if err != nil {
		return nil, fmt.Errorf("reading the evaluation: %w", err)
	}
	if evaluation.BackgroundJobID != job.ID || !strings.EqualFold(evaluation.OwnerID, job.OwnerID) {
		// The job does not own this evaluation: answer as if it did not exist.
		return nil, ErrEvaluationNotFound
	}
	if job.InputArtifactID != nil && *job.InputArtifactID != "" &&
		*job.InputArtifactID != evaluation.DatasetArtifactID {
		return nil, ErrEvaluationBindingUnavailable
	}
	manifestSHA, err := c.resolveBoundManifest(ctx, c.DB, evaluation)
	if errors.Is(err, errBindingUnavailable) {
		return nil, ErrEvaluationBindingUnavailable
	}
	if err != nil {
		return nil, err
	}
	binding := &EvaluationBinding{
		EvaluationID:      evaluation.ID,
		Kind:              evaluation.Kind,
		DatasetArtifactID: evaluation.DatasetArtifactID,
		ManifestSHA256:    manifestSHA,
	}
	if evaluation.Kind == "application" && evaluation.SnapshotID != nil {
		if err := c.DB.QueryRowContext(ctx, `
			SELECT s.id::text, s.application_id::text, COALESCE(s.draft_revision, 0),
			       s.compiler_version, s.worker_image_digest, s.state_schema_version
			  FROM platform.execution_snapshots AS s
			 WHERE s.id = $1::uuid`, *evaluation.SnapshotID,
		).Scan(
			&binding.SnapshotID, &binding.ApplicationID, &binding.DraftRevision,
			&binding.CompilerVersion, &binding.WorkerImageDigest, &binding.StateSchemaVersion,
		); errors.Is(err, sql.ErrNoRows) {
			return nil, ErrEvaluationBindingUnavailable
		} else if err != nil {
			return nil, fmt.Errorf("reading the evaluation snapshot identity: %w", err)
		}
	}
	return binding, nil
}

// queryer is the small subset of *sql.DB / *sql.Tx the manifest resolution
// needs, so the handler read and the completion guard share one implementation.
type queryer interface {
	QueryRowContext(ctx context.Context, query string, args ...any) *sql.Row
}

// resolveBoundManifest returns the manifest hash the report must declare.
//
// kind=model reads the candidate's immutable manifest_sha256. kind=application
// reads the snapshot's manifest_sha256 when platform.execution_snapshots exists
// (T027); before that the binding is unavailable rather than fabricated.
func (c *Controller) resolveBoundManifest(
	ctx context.Context,
	q queryer,
	evaluation *EvaluationJob,
) (string, error) {
	switch evaluation.Kind {
	case "model":
		if evaluation.ModelProfileVersionID == nil {
			return "", errBindingUnavailable
		}
		var manifest string
		err := q.QueryRowContext(ctx, `
			SELECT manifest_sha256 FROM platform.model_profile_versions WHERE id = $1::uuid`,
			*evaluation.ModelProfileVersionID,
		).Scan(&manifest)
		if errors.Is(err, sql.ErrNoRows) {
			return "", errBindingUnavailable
		}
		if err != nil {
			return "", fmt.Errorf("reading the candidate manifest: %w", err)
		}
		if !isSHA256Hex(manifest) {
			return "", errBindingUnavailable
		}
		return manifest, nil

	case "application":
		if evaluation.SnapshotID == nil {
			return "", errBindingUnavailable
		}
		var exists bool
		if err := q.QueryRowContext(ctx,
			`SELECT to_regclass('platform.execution_snapshots') IS NOT NULL`,
		).Scan(&exists); err != nil {
			return "", fmt.Errorf("inspecting the snapshot table: %w", err)
		}
		if !exists {
			// T027 creates the table; until then an application evaluation has
			// no binding and must fail rather than invent one.
			return "", errBindingUnavailable
		}
		var (
			manifest string
			ownerID  string
		)
		err := q.QueryRowContext(ctx, `
			SELECT manifest_sha256, owner_id::text
			  FROM platform.execution_snapshots WHERE id = $1::uuid`,
			*evaluation.SnapshotID,
		).Scan(&manifest, &ownerID)
		if errors.Is(err, sql.ErrNoRows) {
			return "", errBindingUnavailable
		}
		if err != nil {
			return "", fmt.Errorf("reading the snapshot manifest: %w", err)
		}
		if !strings.EqualFold(ownerID, evaluation.OwnerID) {
			// Evaluation snapshots are the owner's own frozen execution; a
			// snapshot owned by someone else is not a valid binding.
			return "", errBindingUnavailable
		}
		if !isSHA256Hex(manifest) {
			return "", errBindingUnavailable
		}
		return manifest, nil

	default:
		return "", errBindingUnavailable
	}
}

// markEvaluationRunning is called inside the claim transaction. A job attempt
// that owns the work is what makes the evaluation running; a retry keeps it
// running (there is still an attempt in flight).
//
// application_evaluate targets the evaluation row directly; model_validate
// targets the model profile, so its evaluation is found by background_job_id.
func markEvaluationRunning(ctx context.Context, tx *sql.Tx, job *Job) error {
	switch job.Kind {
	case KindApplicationEvaluate:
		if _, err := tx.ExecContext(ctx, `
			UPDATE platform.evaluation_jobs
			   SET state = 'running'
			 WHERE id = $1::uuid AND state = 'queued'`, job.TargetID); err != nil {
			return fmt.Errorf("marking the evaluation running: %w", err)
		}
	case KindModelValidate:
		if _, err := tx.ExecContext(ctx, `
			UPDATE platform.evaluation_jobs
			   SET state = 'running'
			 WHERE kind = 'model' AND background_job_id = $1::uuid AND state = 'queued'`,
			job.ID); err != nil {
			return fmt.Errorf("marking the model evaluation running: %w", err)
		}
	}
	return nil
}

// guardReportProvenanceTx is the F13A/T052 binding extension. A report without a
// provenance block keeps the T026 rules. A report that declares one must match
// the frozen snapshot row exactly: the snapshot id, the compiler version, the
// worker image digest and the state-schema version are read from the database,
// never trusted from the document. That is what makes "运行产物可追溯到
// manifest/dataset" verifiable and what stops a report from another snapshot,
// application or build from being accepted.
func guardReportProvenanceTx(
	ctx context.Context, tx *sql.Tx, evaluation *EvaluationJob, document *eval.Report,
) string {
	if document.Provenance == nil {
		return ""
	}
	if document.Provenance.DatasetArtifactID != "" &&
		document.Provenance.DatasetArtifactID != evaluation.DatasetArtifactID {
		return CodeReportBindingMismatch
	}
	if evaluation.SnapshotID == nil {
		// A model evaluation has no snapshot; a provenance block claiming one is
		// not a document this pipeline produced.
		if document.Provenance.SnapshotID != "" {
			return CodeReportBindingMismatch
		}
		return ""
	}
	var (
		snapshotID      string
		manifestSHA256  string
		compilerVersion string
		workerDigest    string
		stateSchema     string
		ownerID         string
		applicationID   sql.NullString
	)
	err := tx.QueryRowContext(ctx, `
		SELECT s.id::text, s.manifest_sha256, s.compiler_version, s.worker_image_digest,
		       s.state_schema_version, s.owner_id::text, e.application_id::text
		  FROM platform.execution_snapshots AS s
		  JOIN platform.evaluation_jobs AS e ON e.snapshot_id = s.id
		 WHERE s.id = $1::uuid
		   AND e.id = $2::uuid`, *evaluation.SnapshotID, evaluation.ID,
	).Scan(&snapshotID, &manifestSHA256, &compilerVersion, &workerDigest, &stateSchema,
		&ownerID, &applicationID)
	if errors.Is(err, sql.ErrNoRows) {
		return CodeEvaluationBindingMissing
	}
	if err != nil {
		return ""
	}
	if document.Provenance.SnapshotID != snapshotID {
		return CodeReportBindingMismatch
	}
	// The optional identity fields are exact when they are present: a document
	// that names a compiler/worker/state-schema/application identity must name
	// the frozen one.
	if value := document.Provenance.CompilerVersion; value != "" && value != compilerVersion {
		return CodeReportBindingMismatch
	}
	if value := document.Provenance.WorkerImageDigest; value != "" && value != workerDigest {
		return CodeReportBindingMismatch
	}
	if value := document.Provenance.StateSchemaVersion; value != "" && value != stateSchema {
		return CodeReportBindingMismatch
	}
	if value := document.Provenance.ApplicationID; value != "" {
		if !applicationID.Valid || !strings.EqualFold(value, applicationID.String) {
			return CodeReportBindingMismatch
		}
	}
	return ""
}

// guardReportCaseRunsTx is the F13A/T062 report-to-run binding. A sealed report is
// a claim; the platform rows are the truth. For an application evaluation (the
// only kind the real runner produces), a passed case must be backed by *this*
// evaluation's case run, owned by the evaluation's owner, executed against the
// bound snapshot and manifest, and finished (succeeded or failed).
//
// This is what makes "a forged, borrowed, cancelled or incomplete report cannot
// pass acceptance" true even when the document is internally consistent: a
// crafted passed case with fabricated assertions, or a real run that is still
// queued/cancelled, is refused with report_binding_mismatch. A model evaluation
// has no case runs and keeps its pre-existing synthetic rules.
func guardReportCaseRunsTx(
	ctx context.Context,
	tx *sql.Tx,
	evaluation *EvaluationJob,
	boundManifest string,
	document *eval.Report,
) (string, error) {
	if evaluation.Kind != "application" {
		return "", nil
	}
	// F13A/T062: an application evaluation is executed by the real runner. The
	// legacy synthetic evaluator is a model-only placeholder, so a document that
	// declares any other evaluator is not a report this pipeline produced; this
	// closes the "declare the placeholder to skip the evidence rules" bypass.
	if document.Evaluator != eval.RunnerEvaluatorID {
		return CodeReportInvalid, nil
	}
	for _, item := range document.Cases {
		facts := item.Facts
		if facts == nil || strings.TrimSpace(facts.RunID) == "" {
			if item.Status == eval.VerdictPassed {
				// A pass with no run at all is an unproven claim.
				return CodeReportBindingMismatch, nil
			}
			continue
		}
		var (
			runID      string
			runState   string
			runOwner   string
			snapshotID sql.NullString
			manifest   sql.NullString
		)
		err := tx.QueryRowContext(ctx, `
			SELECT r.id::text, r.state, r.owner_id::text,
			       r.execution_snapshot_id::text, r.manifest_sha256
			  FROM platform.evaluation_case_runs AS cr
			  JOIN platform.runs AS r ON r.id = cr.run_id
			 WHERE cr.evaluation_id = $1::uuid AND cr.case_id = $2`,
			evaluation.ID, item.ID,
		).Scan(&runID, &runState, &runOwner, &snapshotID, &manifest)
		if errors.Is(err, sql.ErrNoRows) {
			// The report names a run that is not this evaluation's case run.
			return CodeReportBindingMismatch, nil
		}
		if err != nil {
			return "", fmt.Errorf("reading the report's case run: %w", err)
		}
		if runID != facts.RunID || !strings.EqualFold(runOwner, evaluation.OwnerID) {
			return CodeReportBindingMismatch, nil
		}
		if evaluation.SnapshotID != nil &&
			(!snapshotID.Valid || !strings.EqualFold(snapshotID.String, *evaluation.SnapshotID)) {
			return CodeReportBindingMismatch, nil
		}
		if !manifest.Valid || !strings.EqualFold(manifest.String, boundManifest) {
			return CodeReportBindingMismatch, nil
		}
		if item.Status == eval.VerdictPassed {
			// A pass is only acceptable over a finished run, and the report's
			// recorded state must agree with the platform row. Failed/not_run
			// cases keep their evidence but no further state assertion, because a
			// reconciliation state may legitimately move after collection.
			if !eval.CompletedRunState(runState) {
				return CodeReportBindingMismatch, nil
			}
			if facts.RunState != "" && facts.RunState != runState {
				return CodeReportBindingMismatch, nil
			}
		}
	}
	return "", nil
}

// failEvaluationTx records a terminal evaluation failure. It is idempotent: an
// evaluation that is already terminal is left alone (the job row is the lock
// that makes this unreachable twice), and a retry never calls it.
func failEvaluationTx(ctx context.Context, tx *sql.Tx, evaluationID, errorCode string) error {
	if _, err := tx.ExecContext(ctx, `
		UPDATE platform.evaluation_jobs
		   SET state = 'failed', error_code = $2, finished_at = now()
		 WHERE id = $1::uuid AND state IN ('queued', 'running')`,
		evaluationID, errorCode); err != nil {
		return fmt.Errorf("failing the evaluation: %w", err)
	}
	return nil
}

// failEvaluationForJobTx reconciles the business row of a terminal background
// job with that job's failure. It is the one place both terminal paths (Complete's
// guards and the expired-lease sweep) go through, so a failed job can never leave
// its evaluation "running" and a retryable recovery never fails it.
func failEvaluationForJobTx(
	ctx context.Context,
	tx *sql.Tx,
	kind Kind,
	targetID, errorCode string,
) error {
	switch kind {
	case KindApplicationEvaluate:
		return failEvaluationTx(ctx, tx, targetID, errorCode)
	case KindModelValidate:
		// A model_validate job targets the model profile; the evaluation and
		// the model state both become failed.
		return failModelValidationForJobTx(ctx, tx, targetID, errorCode)
	default:
		// No other kind owns bound business state in this batch.
		return nil
	}
}

// cancelEvaluationTx records a cancelled evaluation.
func cancelEvaluationTx(ctx context.Context, tx *sql.Tx, evaluationID string) error {
	if _, err := tx.ExecContext(ctx, `
		UPDATE platform.evaluation_jobs
		   SET state = 'cancelled', finished_at = now()
		 WHERE id = $1::uuid AND state IN ('queued', 'running')`, evaluationID); err != nil {
		return fmt.Errorf("cancelling the evaluation: %w", err)
	}
	return nil
}

// guardEvaluationSuccess is the server-side completion guard for
// application_evaluate. It runs inside Complete's transaction and holds the
// evaluation row lock.
//
// A non-empty failure code means "this claimed success is refused": Complete
// turns it into a failed job with that code and no report row. errGuardTransient
// means the sealed object could not be read for an infrastructure reason, which
// Complete classifies as the retryable transient_storage_io. A nil code means the
// report row was inserted and the evaluation marked succeeded in this same
// transaction.
func (c *Controller) guardEvaluationSuccess(
	ctx context.Context,
	tx *sql.Tx,
	job *Job,
	resultArtifactID string,
) (string, error) {
	evaluation, err := scanEvaluation(tx.QueryRowContext(ctx, `
		SELECT `+selectEvaluationColumns+`
		  FROM platform.evaluation_jobs
		 WHERE id = $1::uuid
		 FOR UPDATE`, job.TargetID))
	if errors.Is(err, sql.ErrNoRows) {
		return CodeEvaluationMissing, nil
	}
	if err != nil {
		return "", fmt.Errorf("reading the evaluation: %w", err)
	}
	if evaluation.BackgroundJobID != job.ID || !strings.EqualFold(evaluation.OwnerID, job.OwnerID) {
		return CodeEvaluationBindingMismatch, nil
	}
	if evaluation.State != "queued" && evaluation.State != "running" {
		return CodeEvaluationStateInvalid, nil
	}

	// The dataset is the second binding fact. A tombstone or a dataset that is
	// not ready can never produce a valid report, even if the bytes were readable
	// when the handler downloaded them.
	var (
		datasetSHA     sql.NullString
		datasetState   string
		datasetDeleted sql.NullTime
	)
	err = tx.QueryRowContext(ctx, `
		SELECT sha256, state, deleted_at
		  FROM platform.artifacts
		 WHERE id = $1::uuid
		 FOR UPDATE`, evaluation.DatasetArtifactID,
	).Scan(&datasetSHA, &datasetState, &datasetDeleted)
	if errors.Is(err, sql.ErrNoRows) {
		return CodeSourceDeleted, nil
	}
	if err != nil {
		return "", fmt.Errorf("reading the dataset artifact: %w", err)
	}
	if datasetDeleted.Valid || datasetState == "deleted" {
		return CodeSourceDeleted, nil
	}
	if datasetState != "ready" || !datasetSHA.Valid || !isSHA256Hex(datasetSHA.String) {
		return CodeDatasetNotReady, nil
	}
	datasetDigest := strings.ToLower(strings.TrimSpace(datasetSHA.String))

	boundManifest, err := c.resolveBoundManifest(ctx, tx, evaluation)
	switch {
	case errors.Is(err, errBindingUnavailable):
		return CodeEvaluationBindingMissing, nil
	case err != nil:
		return "", err
	}

	// Read the report artifact row and its sealed bytes. verifyJobResultArtifact
	// already refused an unlinked/unready artifact; this second read is what
	// binds the bytes to the recorded hash and caps the size.
	var (
		ownerID   string
		objectKey string
		state     string
		deleted   sql.NullTime
		storedSHA sql.NullString
		sizeBytes sql.NullInt64
	)
	err = tx.QueryRowContext(ctx, `
		SELECT owner_id::text, object_key, state, deleted_at, sha256, size_bytes
		  FROM platform.artifacts
		 WHERE id = $1::uuid`, resultArtifactID,
	).Scan(&ownerID, &objectKey, &state, &deleted, &storedSHA, &sizeBytes)
	if errors.Is(err, sql.ErrNoRows) {
		return CodeReportInvalid, nil
	}
	if err != nil {
		return "", fmt.Errorf("reading the report artifact: %w", err)
	}
	if deleted.Valid || state != "ready" || !strings.EqualFold(ownerID, evaluation.OwnerID) ||
		!storedSHA.Valid || !isSHA256Hex(storedSHA.String) {
		return CodeReportInvalid, nil
	}
	if sizeBytes.Valid && sizeBytes.Int64 > MaxEvaluationReportBytes {
		return CodeReportInvalid, nil
	}
	if c.Store == nil {
		return "", ErrObjectStoreUnavailable
	}
	content, digest, err := readCappedObject(c.Store, objectKey, MaxEvaluationReportBytes)
	switch {
	case errors.Is(err, objectstore.ErrNotExist), errors.Is(err, objectstore.ErrInvalidKey):
		return CodeReportInvalid, nil
	case errors.Is(err, objectstore.ErrTooLarge):
		return CodeReportInvalid, nil
	case err != nil:
		return "", fmt.Errorf("%w: %v", errGuardTransient, err)
	}
	if !strings.EqualFold(digest, storedSHA.String) {
		// The sealed bytes changed after the finalize guard verified them; no
		// report can be accepted from bytes the server cannot reproduce.
		return CodeReportInvalid, nil
	}

	var document EvaluationReportDocument
	if err := decodeEvaluationReport(content, &document); err != nil {
		return CodeReportInvalid, nil
	}
	if code := validateEvaluationReportShape(&document); code != "" {
		return code, nil
	}
	switch {
	case !strings.EqualFold(document.ManifestSHA256, boundManifest),
		!strings.EqualFold(document.DatasetSHA256, datasetDigest),
		document.DatasetArtifactID != evaluation.DatasetArtifactID,
		document.EvaluationID != evaluation.ID:
		return CodeReportBindingMismatch, nil
	case document.EvaluationKind != evaluation.Kind:
		return CodeReportInvalid, nil
	}
	// F13A/T052: a report that declares its provenance must prove that the
	// snapshot/compiler/worker identities it names are the frozen identity the
	// evaluation actually runs under. The identity is never taken from the
	// document; it is re-read from the row the binding already resolved.
	if code := guardReportProvenanceTx(ctx, tx, evaluation, &document); code != "" {
		return code, nil
	}
	// F13A/T062: the document's per-case claims are checked against the
	// evaluation's real case runs. A forged pass, a borrowed run, or a case whose
	// run never finished is refused before any report row exists.
	if code, err := guardReportCaseRunsTx(ctx, tx, evaluation, boundManifest, &document); err != nil {
		return "", err
	} else if code != "" {
		return code, nil
	}

	reportID := uuid.NewString()
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO platform.evaluation_reports
		       (id, manifest_sha256, dataset_sha256, result_artifact_id, status)
		VALUES ($1::uuid, $2, $3, $4::uuid, $5)`,
		reportID, boundManifest, datasetDigest, resultArtifactID, document.Status,
	); err != nil {
		return "", fmt.Errorf("recording the evaluation report: %w", err)
	}
	// F13A/T065: the sealed report is the final progress truth. Every case in it
	// has a decision, and the failed cases are the report's own statuses; the
	// live progress reports may have been interrupted by a retry, so the
	// terminal counters are written here in the same transaction as the report.
	caseCount := int64(len(document.Cases))
	failedCases := int64(0)
	for _, item := range document.Cases {
		if item.Status == eval.VerdictFailed {
			failedCases++
		}
	}
	result, err := tx.ExecContext(ctx, `
		UPDATE platform.evaluation_jobs
		   SET state = 'succeeded', report_id = $2::uuid, error_code = NULL, finished_at = now(),
		       progress_total = GREATEST(progress_total, $3),
		       progress_done = GREATEST(progress_done, $3),
		       progress_failed = $4
		 WHERE id = $1::uuid AND state IN ('queued', 'running')`,
		evaluation.ID, reportID, caseCount, failedCases,
	)
	if err != nil {
		return "", fmt.Errorf("completing the evaluation: %w", err)
	}
	affected, err := result.RowsAffected()
	if err != nil {
		return "", fmt.Errorf("completing the evaluation: %w", err)
	}
	if affected != 1 {
		// Unreachable while the evaluation row lock is held, but a completion
		// that could not mark the evaluation succeeded must not leave a report
		// row behind: remove the row just inserted and fail the job.
		if _, err := tx.ExecContext(ctx,
			`DELETE FROM platform.evaluation_reports WHERE id = $1::uuid`, reportID); err != nil {
			return "", fmt.Errorf("rolling back the unbound report: %w", err)
		}
		return CodeEvaluationStateInvalid, nil
	}
	return "", nil
}

// EvaluationReportDocument is the sealed report document the trusted runner
// writes and the completion guard accepts. It is an alias of the F13A/T052
// document so the handler and the guard share one schema instead of two
// drifting structs.
//
// T026 introduced a *synthetic* evaluator placeholder (synthetic-jcs-case/1)
// that proves the download -> evaluate -> upload -> finalize -> guarded-report
// pipeline on real bytes. F13A/T052 adds the real runner (the runner evaluator
// id) and extends the document additively with per-case evidence, provenance and
// the informational quality slot. The five load-bearing fields
// (report_version/kind/status/checks/cases plus the two binding hashes) keep
// their exact meaning.
type EvaluationReportDocument = eval.Report

// EvaluationReportCase is one evaluated case. It is an alias of the F13A/T052
// case document so the guard, the handler and the synthetic legacy path agree.
type EvaluationReportCase = eval.CaseReport

// decodeEvaluationReport strictly decodes one report document. Unknown fields,
// trailing data or invalid JSON are a malformed report, never an accepted one.
func decodeEvaluationReport(content []byte, document *EvaluationReportDocument) error {
	decoded, err := eval.DecodeReport(content)
	if err != nil {
		return err
	}
	*document = *decoded
	return nil
}

// validateEvaluationReportShape applies the report's structural and verdict
// rules. They live in internal/evaluation so the runner that builds a document
// and the guard that accepts one use exactly the same rules.
//
// The rules are never weaker than T026's:
//
//	passed  -> at least one case, at least one check, every case passed, and for
//	           the real runner every passed case carries at least one passed
//	           assertion (an empty checker is not a pass)
//	failed  -> at least one case, at least one check, at least one case did not
//	           pass or did not run
//	not_run -> nothing was collected (no cases, or every case is not_run/blocked)
//
// Anything else is report_invalid. In particular the declared status must equal
// the status computed from the cases; a quality/judge score is never an input.
func validateEvaluationReportShape(document *EvaluationReportDocument) string {
	if reason := eval.ValidateShape(document); reason != "" {
		return CodeReportInvalid
	}
	return ""
}

// readCappedObject reads at most max+1 bytes of one sealed object and returns
// the bytes and their SHA-256. ErrTooLarge is returned without hashing anything
// past the cap; the caller never parses an over-limit body.
func readCappedObject(store *objectstore.Store, key string, max int64) ([]byte, string, error) {
	reader, _, err := store.Open(key)
	if err != nil {
		return nil, "", err
	}
	defer func() { _ = reader.Close() }()
	content, err := io.ReadAll(io.LimitReader(reader, max+1))
	if err != nil {
		return nil, "", err
	}
	if int64(len(content)) > max {
		return nil, "", objectstore.ErrTooLarge
	}
	sum := sha256.Sum256(content)
	return content, hex.EncodeToString(sum[:]), nil
}
