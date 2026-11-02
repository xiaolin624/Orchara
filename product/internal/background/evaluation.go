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
	"bytes"
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"strings"
	"time"

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
	return &EvaluationBinding{
		EvaluationID:      evaluation.ID,
		Kind:              evaluation.Kind,
		DatasetArtifactID: evaluation.DatasetArtifactID,
		ManifestSHA256:    manifestSHA,
	}, nil
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
func markEvaluationRunning(ctx context.Context, tx *sql.Tx, job *Job) error {
	if job.Kind != KindApplicationEvaluate {
		return nil
	}
	if _, err := tx.ExecContext(ctx, `
		UPDATE platform.evaluation_jobs
		   SET state = 'running'
		 WHERE id = $1::uuid AND state = 'queued'`, job.TargetID); err != nil {
		return fmt.Errorf("marking the evaluation running: %w", err)
	}
	return nil
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
	if kind != KindApplicationEvaluate {
		// No other kind owns bound business state in this batch.
		return nil
	}
	return failEvaluationTx(ctx, tx, targetID, errorCode)
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

	reportID := uuid.NewString()
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO platform.evaluation_reports
		       (id, manifest_sha256, dataset_sha256, result_artifact_id, status)
		VALUES ($1::uuid, $2, $3, $4::uuid, $5)`,
		reportID, boundManifest, datasetDigest, resultArtifactID, document.Status,
	); err != nil {
		return "", fmt.Errorf("recording the evaluation report: %w", err)
	}
	result, err := tx.ExecContext(ctx, `
		UPDATE platform.evaluation_jobs
		   SET state = 'succeeded', report_id = $2::uuid, error_code = NULL, finished_at = now()
		 WHERE id = $1::uuid AND state IN ('queued', 'running')`,
		evaluation.ID, reportID,
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

// EvaluationReportDocument is the exact report document the T026 synthetic
// evaluator writes and the completion guard accepts. It is exported so the
// handler and the guard share one schema instead of two drifting structs.
//
// This is deliberately a *synthetic* evaluator placeholder: it proves the
// download -> evaluate -> upload -> finalize -> guarded-report pipeline on real
// bytes. F13A owns the real evaluation runner/checker suite; it may extend the
// case fields, but must keep report_version/kind/status/checks/cases and the two
// binding hashes, or the guard's binding rules must be extended with it.
type EvaluationReportDocument struct {
	ReportVersion     int                    `json:"report_version"`
	Kind              string                 `json:"kind"`
	EvaluationKind    string                 `json:"evaluation_kind"`
	EvaluationID      string                 `json:"evaluation_id"`
	DatasetArtifactID string                 `json:"dataset_artifact_id"`
	DatasetSHA256     string                 `json:"dataset_sha256"`
	ManifestSHA256    string                 `json:"manifest_sha256"`
	Status            string                 `json:"status"`
	Checks            []string               `json:"checks"`
	CaseCount         int                    `json:"case_count"`
	Cases             []EvaluationReportCase `json:"cases"`
	Synthetic         bool                   `json:"synthetic"`
	Evaluator         string                 `json:"evaluator"`
}

// EvaluationReportCase is one evaluated case. The guard only needs identity and
// verdict; the hash fields make the synthetic evaluator's outcome auditable.
type EvaluationReportCase struct {
	ID             string `json:"id"`
	Status         string `json:"status"`
	ExpectedSHA256 string `json:"expected_sha256,omitempty"`
	ActualSHA256   string `json:"actual_sha256,omitempty"`
	Detail         string `json:"detail,omitempty"`
}

// decodeEvaluationReport strictly decodes one report document. Unknown fields,
// trailing data or invalid JSON are a malformed report, never an accepted one.
func decodeEvaluationReport(content []byte, document *EvaluationReportDocument) error {
	decoder := json.NewDecoder(bytes.NewReader(content))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(document); err != nil {
		return err
	}
	// A second value after the document is trailing garbage.
	var trailing any
	if err := decoder.Decode(&trailing); !errors.Is(err, io.EOF) {
		return errors.New("the report document has trailing data")
	}
	return nil
}

// validateEvaluationReportShape applies the passed/failed/not_run rules:
//
//	passed  -> at least one case, at least one check, every case passed
//	failed  -> at least one case, at least one check, at least one case failed
//	not_run -> no cases at all
//
// Anything else is report_invalid. In particular an empty case set or an empty
// check set can never be passed, and a status that disagrees with the cases is
// refused rather than trusted.
func validateEvaluationReportShape(document *EvaluationReportDocument) string {
	if document.ReportVersion != 1 || document.Kind != "evaluation" {
		return CodeReportInvalid
	}
	if document.EvaluationKind != "model" && document.EvaluationKind != "application" {
		return CodeReportInvalid
	}
	if len(document.Checks) == 0 {
		return CodeReportInvalid
	}
	for _, check := range document.Checks {
		if strings.TrimSpace(check) == "" {
			return CodeReportInvalid
		}
	}
	if document.CaseCount < 0 || document.CaseCount != len(document.Cases) {
		return CodeReportInvalid
	}
	seen := make(map[string]bool, len(document.Cases))
	passed, failed := 0, 0
	for _, item := range document.Cases {
		if strings.TrimSpace(item.ID) == "" || seen[item.ID] {
			return CodeReportInvalid
		}
		seen[item.ID] = true
		switch item.Status {
		case "passed":
			passed++
		case "failed":
			failed++
		default:
			return CodeReportInvalid
		}
	}
	switch document.Status {
	case "passed":
		if len(document.Cases) == 0 || failed != 0 || passed == 0 {
			return CodeReportInvalid
		}
	case "failed":
		if len(document.Cases) == 0 || failed == 0 {
			return CodeReportInvalid
		}
	case "not_run":
		if len(document.Cases) != 0 {
			return CodeReportInvalid
		}
	default:
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
