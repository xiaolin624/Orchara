package background

// F06/T039: the model_validate completion decision.
//
// The handler proves a candidate handled the platform protocol and uploads a
// compatibility report. This guard is the server's acceptance decision: it
// re-reads the sealed report, verifies every binding (candidate, fixture,
// model-call ledger row and verdict), and only then records the immutable
// evaluation report and moves the model from running to passed/failed.
//
// A failed *verdict* (the model produced unusable structured output) is a
// succeeded job with a failed report. A report the server cannot trust is a
// failed job: no report row, the evaluation fails, and the model is marked
// failed so it can never be selected by a shared application until it is
// validated again.

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"strings"

	"orchara/platform/internal/objectstore"
	"orchara/platform/internal/uuid"
)

// ModelCompatibilityCheck is one probe or aggregate verdict. Stub reports
// retain their T039 synthetic check; real reports require the probe set below.
type ModelCompatibilityCheck struct {
	Name   string `json:"name"`
	Passed bool   `json:"passed"`
	Reason string `json:"reason"`
}

// RealModelCompatibilityChecks are mandatory for the sole supported real
// protocol. Stub validation retains its explicitly labelled synthetic check.
func RealModelCompatibilityChecks() []string {
	return []string{"structured_output", "tool_call", "streaming", "cancellation"}
}

// ModelCompatibilityProbe binds one verdict to the server's immutable call result.
type ModelCompatibilityProbe struct {
	CallID           string                  `json:"call_id"`
	ResultArtifactID string                  `json:"result_artifact_id"`
	Check            ModelCompatibilityCheck `json:"check"`
}

// ModelCompatibilityReport is the exact report document the model_validate
// handler registers (internal/background/handlers/model_validate.go). Unknown
// fields are refused: the handler and this guard must share one schema.
type ModelCompatibilityReport struct {
	ReportVersion         int                       `json:"report_version"`
	Kind                  string                    `json:"kind"`
	ModelProfileVersionID string                    `json:"model_profile_version_id"`
	CallID                string                    `json:"call_id"`
	FixtureArtifactID     string                    `json:"fixture_artifact_id"`
	FixtureSHA256         string                    `json:"fixture_sha256"`
	Check                 ModelCompatibilityCheck   `json:"check"`
	Compatible            bool                      `json:"compatible"`
	Provider              string                    `json:"provider"`
	Stub                  bool                      `json:"stub"`
	ModelResultArtifactID *string                   `json:"model_result_artifact_id,omitempty"`
	Probes                []ModelCompatibilityProbe `json:"probes,omitempty"`
}

// decodeModelCompatibilityReport strictly decodes one report document.
func decodeModelCompatibilityReport(content []byte, document *ModelCompatibilityReport) error {
	decoder := json.NewDecoder(strings.NewReader(string(content)))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(document); err != nil {
		return err
	}
	var trailing any
	if err := decoder.Decode(&trailing); !errors.Is(err, io.EOF) {
		return errors.New("the compatibility report has trailing data")
	}
	return nil
}

// modelValidationEvaluationBound reports whether this model_validate job was
// created by the F06 flow with a bound evaluation row. A directly enqueued T024
// job has none, and its completion keeps the original report-only contract.
func modelValidationEvaluationBound(ctx context.Context, tx *sql.Tx, jobID string) (bool, error) {
	var one int
	err := tx.QueryRowContext(ctx, `
		SELECT 1
		  FROM platform.evaluation_jobs
		 WHERE background_job_id = $1::uuid`, jobID,
	).Scan(&one)
	if errors.Is(err, sql.ErrNoRows) {
		return false, nil
	}
	if err != nil {
		return false, fmt.Errorf("checking the model evaluation binding: %w", err)
	}
	return true, nil
}

// guardModelValidateSuccess accepts a claimed model-compatibility success.
//
// It returns a non-empty failure code when the report cannot be accepted (the
// job then fails with that code and no report exists) and errGuardTransient
// when the sealed bytes could not be read for an infrastructure reason. A nil
// code means the report row, the succeeded evaluation and the model state were
// all written in this transaction.
func (c *Controller) guardModelValidateSuccess(
	ctx context.Context,
	tx *sql.Tx,
	job *Job,
	resultArtifactID string,
) (string, error) {
	if job.InputArtifactID == nil || !uuid.Valid(*job.InputArtifactID) {
		return CodeReportBindingMismatch, nil
	}

	// Lock the model row first: BeginValidation takes the same lock FOR UPDATE,
	// so a validate request can never observe (or overwrite) a half-finished
	// completion.
	var (
		modelManifest string
		modelState    string
		modelProvider string
		modelRevoked  sql.NullTime
	)
	err := tx.QueryRowContext(ctx, `
		SELECT manifest_sha256, validation_state, revoked_at, provider
		  FROM platform.model_profile_versions
		 WHERE id = $1::uuid
		 FOR UPDATE`, job.TargetID,
	).Scan(&modelManifest, &modelState, &modelRevoked, &modelProvider)
	if errors.Is(err, sql.ErrNoRows) {
		return CodeEvaluationMissing, nil
	}
	if err != nil {
		return "", fmt.Errorf("reading the model profile: %w", err)
	}
	if modelRevoked.Valid || modelState != "running" {
		return CodeEvaluationStateInvalid, nil
	}

	var (
		evaluationID      string
		evaluationKind    string
		evaluationOwner   string
		evaluationModel   sql.NullString
		evaluationDataset sql.NullString
		evaluationState   string
	)
	err = tx.QueryRowContext(ctx, `
		SELECT id::text, kind, owner_id::text, model_profile_version_id::text,
		       dataset_artifact_id::text, state
		  FROM platform.evaluation_jobs
		 WHERE background_job_id = $1::uuid
		 FOR UPDATE`, job.ID,
	).Scan(&evaluationID, &evaluationKind, &evaluationOwner, &evaluationModel,
		&evaluationDataset, &evaluationState)
	if errors.Is(err, sql.ErrNoRows) {
		return CodeEvaluationMissing, nil
	}
	if err != nil {
		return "", fmt.Errorf("reading the model evaluation: %w", err)
	}
	if evaluationKind != "model" || !evaluationModel.Valid || evaluationModel.String != job.TargetID ||
		!strings.EqualFold(evaluationOwner, job.OwnerID) ||
		!evaluationDataset.Valid || evaluationDataset.String != *job.InputArtifactID {
		return CodeEvaluationBindingMismatch, nil
	}
	if evaluationState != "queued" && evaluationState != "running" {
		return CodeEvaluationStateInvalid, nil
	}

	// The fixture identity is a database fact, not a report claim.
	var (
		fixtureSHA     string
		fixtureState   string
		fixtureDeleted sql.NullTime
	)
	err = tx.QueryRowContext(ctx, `
		SELECT sha256, state, deleted_at
		  FROM platform.artifacts
		 WHERE id = $1::uuid`, *job.InputArtifactID,
	).Scan(&fixtureSHA, &fixtureState, &fixtureDeleted)
	if errors.Is(err, sql.ErrNoRows) {
		return CodeReportBindingMismatch, nil
	}
	if err != nil {
		return "", fmt.Errorf("reading the compatibility fixture: %w", err)
	}
	if fixtureDeleted.Valid || fixtureState != "ready" || !isSHA256Hex(fixtureSHA) {
		return CodeReportBindingMismatch, nil
	}

	// Read the sealed report. verifyJobResultArtifact has already checked that
	// it is this job's own ready artifact.
	var (
		reportOwner string
		reportKey   string
		reportState string
		reportSHA   sql.NullString
		reportSize  sql.NullInt64
		reportGone  sql.NullTime
	)
	err = tx.QueryRowContext(ctx, `
		SELECT owner_id::text, object_key, state, sha256, size_bytes, deleted_at
		  FROM platform.artifacts
		 WHERE id = $1::uuid`, resultArtifactID,
	).Scan(&reportOwner, &reportKey, &reportState, &reportSHA, &reportSize, &reportGone)
	if errors.Is(err, sql.ErrNoRows) {
		return CodeReportInvalid, nil
	}
	if err != nil {
		return "", fmt.Errorf("reading the compatibility report artifact: %w", err)
	}
	if reportGone.Valid || reportState != "ready" || !reportSHA.Valid ||
		!isSHA256Hex(reportSHA.String) || !strings.EqualFold(reportOwner, job.OwnerID) {
		return CodeReportInvalid, nil
	}
	if reportSize.Valid && reportSize.Int64 > MaxEvaluationReportBytes {
		return CodeReportInvalid, nil
	}
	if c.Store == nil {
		return "", ErrObjectStoreUnavailable
	}
	content, digest, err := readCappedObject(c.Store, reportKey, MaxEvaluationReportBytes)
	switch {
	case errors.Is(err, objectstore.ErrNotExist), errors.Is(err, objectstore.ErrInvalidKey),
		errors.Is(err, objectstore.ErrTooLarge):
		return CodeReportInvalid, nil
	case err != nil:
		return "", fmt.Errorf("%w: %v", errGuardTransient, err)
	}
	if !strings.EqualFold(digest, reportSHA.String) {
		return CodeReportInvalid, nil
	}

	var report ModelCompatibilityReport
	if err := decodeModelCompatibilityReport(content, &report); err != nil {
		return CodeReportInvalid, nil
	}
	switch {
	case report.ReportVersion != 1 || report.Kind != "model_compatibility":
		return CodeReportInvalid, nil
	case report.ModelProfileVersionID != job.TargetID,
		report.FixtureArtifactID != *job.InputArtifactID,
		!strings.EqualFold(report.FixtureSHA256, fixtureSHA):
		return CodeReportBindingMismatch, nil
	case strings.TrimSpace(report.Check.Name) == "":
		return CodeReportInvalid, nil
	case report.Compatible != report.Check.Passed:
		return CodeReportInvalid, nil
	}

	// The ledger row proves the verdict came from a real model call this job
	// made against this candidate.
	var (
		callModel string
		callJob   sql.NullString
		callState string
	)
	err = tx.QueryRowContext(ctx, `
		SELECT model_profile_version_id::text, background_job_id::text, state
		  FROM platform.platform_model_calls
		 WHERE id = $1::uuid`, report.CallID,
	).Scan(&callModel, &callJob, &callState)
	if errors.Is(err, sql.ErrNoRows) {
		return CodeReportBindingMismatch, nil
	}
	if err != nil {
		return "", fmt.Errorf("reading the model call ledger row: %w", err)
	}
	if callModel != job.TargetID || !callJob.Valid || callJob.String != job.ID || callState != "succeeded" {
		return CodeReportBindingMismatch, nil
	}
	if modelProvider != "stub" {
		if report.Stub || report.Provider != modelProvider {
			return CodeReportBindingMismatch, nil
		}
		if code, err := c.verifyRealModelProbes(ctx, tx, job, report); code != "" || err != nil {
			return code, err
		}
	}

	status := "failed"
	if report.Compatible {
		status = "passed"
	}
	reportID := uuid.NewString()
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO platform.evaluation_reports
		       (id, manifest_sha256, dataset_sha256, result_artifact_id, status)
		VALUES ($1::uuid, $2, $3, $4::uuid, $5)`,
		reportID, modelManifest, fixtureSHA, resultArtifactID, status,
	); err != nil {
		return "", fmt.Errorf("recording the model compatibility report: %w", err)
	}

	updated, err := tx.ExecContext(ctx, `
		UPDATE platform.evaluation_jobs
		   SET state = 'succeeded', report_id = $2::uuid, error_code = NULL, finished_at = now()
		 WHERE id = $1::uuid AND state IN ('queued', 'running')`, evaluationID, reportID)
	if err != nil {
		return "", fmt.Errorf("completing the model evaluation: %w", err)
	}
	affected, err := updated.RowsAffected()
	if err != nil {
		return "", fmt.Errorf("completing the model evaluation: %w", err)
	}
	if affected != 1 {
		if _, err := tx.ExecContext(ctx,
			`DELETE FROM platform.evaluation_reports WHERE id = $1::uuid`, reportID); err != nil {
			return "", fmt.Errorf("rolling back the unbound model report: %w", err)
		}
		return CodeEvaluationStateInvalid, nil
	}

	modelUpdated, err := tx.ExecContext(ctx, `
		UPDATE platform.model_profile_versions
		   SET validation_state = $2, evaluation_report_id = $3::uuid
		 WHERE id = $1::uuid AND validation_state = 'running'`,
		job.TargetID, status, reportID)
	if err != nil {
		return "", fmt.Errorf("recording the model validation verdict: %w", err)
	}
	affected, err = modelUpdated.RowsAffected()
	if err != nil {
		return "", fmt.Errorf("recording the model validation verdict: %w", err)
	}
	if affected != 1 {
		if _, err := tx.ExecContext(ctx,
			`DELETE FROM platform.evaluation_reports WHERE id = $1::uuid`, reportID); err != nil {
			return "", fmt.Errorf("rolling back the unbound model report: %w", err)
		}
		return CodeEvaluationStateInvalid, nil
	}
	return "", nil
}

// failModelValidationForJobTx records the terminal failure of a model
// validation. It is shared by the completion guard and the expired-lease sweep:
// a terminal job may never leave its evaluation running or its model running.
func failModelValidationForJobTx(
	ctx context.Context,
	tx *sql.Tx,
	modelID, errorCode string,
) error {
	if _, err := tx.ExecContext(ctx, `
		UPDATE platform.evaluation_jobs
		   SET state = 'failed', error_code = $2, finished_at = now()
		 WHERE kind = 'model' AND model_profile_version_id = $1::uuid
		   AND state IN ('queued', 'running')`, modelID, errorCode); err != nil {
		return fmt.Errorf("failing the model evaluation: %w", err)
	}
	if _, err := tx.ExecContext(ctx, `
		UPDATE platform.model_profile_versions
		   SET validation_state = 'failed'
		 WHERE id = $1::uuid AND validation_state = 'running'`, modelID); err != nil {
		return fmt.Errorf("failing the model validation state: %w", err)
	}
	return nil
}

// cancelModelValidationForJobTx records a cancelled model validation. The model
// is marked failed (it is not usable) and the evaluation cancelled.
func cancelModelValidationForJobTx(ctx context.Context, tx *sql.Tx, modelID string) error {
	if _, err := tx.ExecContext(ctx, `
		UPDATE platform.evaluation_jobs
		   SET state = 'cancelled', finished_at = now()
		 WHERE kind = 'model' AND model_profile_version_id = $1::uuid
		   AND state IN ('queued', 'running')`, modelID); err != nil {
		return fmt.Errorf("cancelling the model evaluation: %w", err)
	}
	if _, err := tx.ExecContext(ctx, `
		UPDATE platform.model_profile_versions
		   SET validation_state = 'failed'
		 WHERE id = $1::uuid AND validation_state = 'running'`, modelID); err != nil {
		return fmt.Errorf("failing the cancelled model validation: %w", err)
	}
	return nil
}
