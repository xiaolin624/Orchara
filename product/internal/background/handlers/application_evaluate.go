// application_evaluate: the maintenance-side half of F00B/T026.
//
// The handler never touches an evaluation/jobs/artifacts table. It asks the
// server for its job-scoped evaluation binding, waits for the dataset artifact
// to become ready (heartbeating, without holding a system_io slot), downloads
// the sealed dataset through a signed link, runs the deterministic *synthetic*
// evaluator documented in ADR 0004 §31, uploads the report through the
// job-scoped upload/complete/status flow, and submits the report artifact id.
//
// The distinction the ticket exists for is deliberate:
//
//   - a business evaluation verdict (a case failed, or the dataset itself is not
//     a valid evaluation dataset) is a report with status=failed; the job still
//     succeeds, and the server records the failed report;
//   - an infrastructure problem (cannot read the dataset, cannot upload bytes,
//     the report never becomes ready) fails the job with a retryable code where
//     retrying can help, and the server fails the evaluation with no report row.
//
// The handler cannot declare `passed` on its own: the server re-reads the sealed
// report in the completion transaction and applies the binding/verdict rules.
package handlers

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"

	"orchara/platform/internal/background"
	"orchara/platform/internal/jcs"
	"orchara/platform/internal/objectstore"
)

const (
	// defaultDatasetWaitTimeout bounds how long one attempt waits for an
	// uploading/scanning dataset. It is a retryable condition: the job returns
	// to ready with backoff while the dataset may still finish.
	defaultDatasetWaitTimeout = 60 * time.Second
	// defaultEvaluationPollInterval is the status-poll/heartbeat cadence while
	// waiting. Short enough for tests, cheap against PostgreSQL.
	defaultEvaluationPollInterval = 100 * time.Millisecond
	// defaultReportWaitTimeout bounds the wait for the report artifact's own
	// finalize (the system_io pool must be able to make progress while all work
	// slots are occupied by waiting evaluations; that is the T026 pool split).
	defaultReportWaitTimeout = 60 * time.Second

	// evaluationDatasetSchema is the only dataset document shape this synthetic
	// evaluator understands. F13A owns the real dataset schema.
	evaluationDatasetSchema = "orchara.evaluation-dataset/1"
	// evaluationEvaluatorName identifies the synthetic placeholder verdict in
	// the report, so nobody can mistake it for a real F13A evaluation.
	evaluationEvaluatorName = "synthetic-jcs-case/1"
)

// ApplicationEvaluate runs one evaluation over one sealed dataset.
func ApplicationEvaluate(ctx context.Context, work background.HandlerWork) background.Outcome {
	if work.Scope == nil || work.Job == nil {
		return background.Failed(background.CodeTransientInternal)
	}
	job := work.Job

	binding, err := work.Scope.Evaluation(ctx, job.ID, work.AttemptToken)
	if err != nil {
		return evaluationOutcome(err)
	}
	if job.InputArtifactID != nil && *job.InputArtifactID != "" &&
		!strings.EqualFold(*job.InputArtifactID, binding.DatasetArtifactID) {
		// The lease and the evaluation disagree about the dataset: that is a
		// server-side binding defect, never something a retry can fix.
		return background.Failed(background.CodeEvaluationBindingMismatch)
	}
	if binding.Kind != "model" && binding.Kind != "application" {
		return background.Failed(background.CodeEvaluationBindingMissing)
	}
	if !isSHA256(binding.ManifestSHA256) {
		return background.Failed(background.CodeEvaluationBindingMissing)
	}

	if outcome, ok := waitForDataset(ctx, work, binding); !ok {
		return outcome
	}

	link, err := work.Scope.Download(ctx, job.ID, work.AttemptToken, binding.DatasetArtifactID)
	if err != nil {
		return evaluationOutcome(err)
	}
	dataset, err := work.Scope.FetchURL(ctx, link, objectstore.MaxObjectBytes)
	if err != nil {
		// The link can expire or the store can be momentarily unreadable; a fresh
		// attempt mints a fresh link and re-reads the same sealed bytes.
		return background.Failed(background.CodeTransientStorageIO)
	}
	datasetSHA := sha256Hex(dataset)

	document := synthesizeEvaluationReport(binding, datasetSHA, dataset)
	reportBytes, err := canonicalJSON(document)
	if err != nil {
		// The report is built from server-controlled values; a canonicalisation
		// failure is an internal defect.
		return background.Failed(background.CodeTransientInternal)
	}
	reportSHA := sha256Hex(reportBytes)

	ticket, err := work.Scope.Upload(ctx, job.ID, work.AttemptToken, background.UploadRequest{
		MediaType: "application/json",
		SizeBytes: int64(len(reportBytes)),
		Purpose:   "report",
	})
	if err != nil {
		return evaluationOutcome(err)
	}
	if err := work.Scope.PutURL(ctx, ticket.URL, reportBytes); err != nil {
		return background.Failed(background.CodeTransientStorageIO)
	}
	view, err := work.Scope.Complete(ctx, job.ID, work.AttemptToken, ticket.ArtifactID, reportSHA)
	if err != nil {
		return evaluationOutcome(err)
	}
	if view.State != "ready" {
		if outcome, ready := waitForEvaluationReport(ctx, work, ticket.ArtifactID); !ready {
			return outcome
		}
	}
	return background.SucceededWith(ticket.ArtifactID)
}

// waitForDataset polls the job-scoped artifact status and renews the lease while
// the dataset is still uploading/scanning. The handler runs in the work pool, so
// this wait cannot consume a system_io slot: artifact_finalize keeps running in
// its own pool and is what eventually makes the dataset ready.
func waitForDataset(
	ctx context.Context,
	work background.HandlerWork,
	binding background.EvaluationBinding,
) (background.Outcome, bool) {
	timeout := work.Settings.DatasetWaitTimeout
	if timeout <= 0 {
		timeout = defaultDatasetWaitTimeout
	}
	poll := work.Settings.EvaluationPollInterval
	if poll <= 0 {
		poll = defaultEvaluationPollInterval
	}
	deadline := time.Now().Add(timeout)
	for {
		if ctx.Err() != nil {
			return background.Failed(background.CodeTransientInternal), false
		}
		view, err := work.Scope.Status(ctx, work.Job.ID, work.AttemptToken, binding.DatasetArtifactID)
		if err != nil {
			return evaluationOutcome(err), false
		}
		switch view.State {
		case "ready":
			return background.Succeeded(), true
		case "quarantined", "deleted":
			// The dataset can never be read; retrying the same facts cannot help.
			return background.Failed(background.CodeSourceDeleted), false
		}
		if time.Now().After(deadline) {
			return background.Failed(background.CodeDatasetWaitTimeout), false
		}
		if work.Heartbeat != nil {
			_ = work.Heartbeat(ctx)
		}
		select {
		case <-ctx.Done():
			return background.Failed(background.CodeTransientInternal), false
		case <-time.After(poll):
		}
	}
}

// waitForEvaluationReport waits for the report artifact's finalize decision.
// It never reads the artifact table: the job-scoped status endpoint is the only
// view a handler has of the finalize guard's decision.
func waitForEvaluationReport(
	ctx context.Context,
	work background.HandlerWork,
	artifactID string,
) (background.Outcome, bool) {
	timeout := work.Settings.ReportWaitTimeout
	if timeout <= 0 {
		timeout = defaultReportWaitTimeout
	}
	poll := work.Settings.EvaluationPollInterval
	if poll <= 0 {
		poll = defaultEvaluationPollInterval
	}
	deadline := time.Now().Add(timeout)
	for {
		if ctx.Err() != nil {
			return background.Failed(background.CodeTransientInternal), false
		}
		view, err := work.Scope.Status(ctx, work.Job.ID, work.AttemptToken, artifactID)
		if err != nil {
			return evaluationOutcome(err), false
		}
		switch view.State {
		case "ready":
			return background.Succeeded(), true
		case "quarantined", "deleted":
			return background.Failed(background.CodeReportNotReady), false
		}
		if time.Now().After(deadline) {
			// The system_io pool did not finalize the report in time. Retrying
			// is honest: the next attempt waits again rather than claiming a
			// success the server could not accept.
			return background.Failed(background.CodeTransientStorageIO), false
		}
		if work.Heartbeat != nil {
			_ = work.Heartbeat(ctx)
		}
		select {
		case <-ctx.Done():
			return background.Failed(background.CodeTransientInternal), false
		case <-time.After(poll):
		}
	}
}

// synthesizeEvaluationReport turns one dataset document into the report the
// guard will verify. The verdict is fully determined by the dataset bytes:
//
//   - the document must be a JSON object with a `cases` array (schema, when
//     present, must be "orchara.evaluation-dataset/1");
//   - each case declares an id and the SHA-256 of the RFC 8785 JCS bytes of its
//     `input`; the synthetic evaluator recomputes that digest and passes/fails
//     the case;
//   - zero cases is not_run (never passed), a malformed dataset is one failed
//     `dataset` case (a business failure, never a job failure).
func synthesizeEvaluationReport(
	binding background.EvaluationBinding,
	datasetSHA string,
	dataset []byte,
) background.EvaluationReportDocument {
	document := background.EvaluationReportDocument{
		ReportVersion:     1,
		Kind:              "evaluation",
		EvaluationKind:    binding.Kind,
		EvaluationID:      binding.EvaluationID,
		DatasetArtifactID: binding.DatasetArtifactID,
		DatasetSHA256:     datasetSHA,
		ManifestSHA256:    binding.ManifestSHA256,
		Synthetic:         true,
		Evaluator:         evaluationEvaluatorName,
	}

	cases, checks, err := evaluateDatasetCases(dataset)
	if err != nil {
		// The dataset is not a valid evaluation dataset. That is a business
		// verdict about the input, expressed as a failed report (job succeeds).
		document.Status = "failed"
		document.Checks = []string{"dataset_schema"}
		document.CaseCount = 1
		document.Cases = []background.EvaluationReportCase{{
			ID:     "dataset",
			Status: "failed",
			Detail: err.Error(),
		}}
		return document
	}
	document.Checks = checks
	document.Cases = cases
	document.CaseCount = len(cases)
	switch {
	case len(cases) == 0:
		document.Status = "not_run"
	case countFailed(cases) > 0:
		document.Status = "failed"
	default:
		document.Status = "passed"
	}
	return document
}

type datasetDocument struct {
	Schema string            `json:"schema"`
	Cases  []datasetCaseJSON `json:"cases"`
}

type datasetCaseJSON struct {
	ID             string          `json:"id"`
	Input          json.RawMessage `json:"input"`
	ExpectedSHA256 string          `json:"expected_sha256"`
}

// evaluateDatasetCases runs the deterministic synthetic check. It returns the
// case results and the checks that were actually performed; an error means the
// dataset itself is malformed (not a case verdict).
func evaluateDatasetCases(dataset []byte) ([]background.EvaluationReportCase, []string, error) {
	var document datasetDocument
	if err := json.Unmarshal(dataset, &document); err != nil {
		return nil, nil, fmt.Errorf("the dataset is not a JSON object")
	}
	if document.Schema != "" && document.Schema != evaluationDatasetSchema {
		return nil, nil, fmt.Errorf("the dataset schema must be %q", evaluationDatasetSchema)
	}
	if document.Cases == nil {
		return nil, nil, fmt.Errorf("the dataset must carry a cases array")
	}
	checks := []string{"dataset_schema"}
	if len(document.Cases) > 0 {
		checks = append(checks, "case_sha256")
	}
	cases := make([]background.EvaluationReportCase, 0, len(document.Cases))
	seen := make(map[string]bool, len(document.Cases))
	for index, raw := range document.Cases {
		item := background.EvaluationReportCase{
			ID:             strings.TrimSpace(raw.ID),
			ExpectedSHA256: strings.ToLower(strings.TrimSpace(raw.ExpectedSHA256)),
		}
		switch {
		case item.ID == "":
			item.ID = fmt.Sprintf("case-%d", index)
			item.Status = "failed"
			item.Detail = "the case has no id"
		case seen[item.ID]:
			item.Status = "failed"
			item.Detail = "the case id is duplicated"
		case !isSHA256(item.ExpectedSHA256):
			item.Status = "failed"
			item.Detail = "expected_sha256 must be 64 lowercase hexadecimal characters"
		case len(raw.Input) == 0:
			item.Status = "failed"
			item.Detail = "the case has no input"
		default:
			canonical, err := jcs.CanonicalizeBytes(raw.Input)
			if err != nil {
				item.Status = "failed"
				item.Detail = "the case input is not canonical JSON"
				break
			}
			item.ActualSHA256 = sha256Hex(canonical)
			if item.ActualSHA256 == item.ExpectedSHA256 {
				item.Status = "passed"
			} else {
				item.Status = "failed"
			}
		}
		seen[item.ID] = true
		cases = append(cases, item)
	}
	return cases, checks, nil
}

func countFailed(cases []background.EvaluationReportCase) int {
	failed := 0
	for _, item := range cases {
		if item.Status == "failed" {
			failed++
		}
	}
	return failed
}

// evaluationOutcome maps a job-scoped API refusal to a job outcome. The
// vocabulary is fixed here, not inferred from strings: only codes in the
// application_evaluate retry table are retryable, every other code fails the
// job immediately (fail safe).
func evaluationOutcome(err error) background.Outcome {
	var coded interface{ ErrorCode() string }
	if errors.As(err, &coded) {
		switch coded.ErrorCode() {
		case "stale_attempt":
			return background.Failed(background.CodeStaleAttempt)
		case "evaluation_not_found":
			return background.Failed(background.CodeEvaluationMissing)
		case "evaluation_not_applicable":
			return background.Failed(background.CodeEvaluationUnsupported)
		case "evaluation_binding_unavailable":
			return background.Failed(background.CodeEvaluationBindingMissing)
		case "artifact_not_found":
			return background.Failed(background.CodeSourceDeleted)
		case "artifact_not_ready", "artifact_conflict":
			return background.Failed(background.CodeReportNotReady)
		case "object_store_unavailable", "object_signing_unavailable":
			return background.Failed(background.CodeObjectStoreUnavailable)
		case "candidate_mismatch", "candidate_unavailable", "model_not_allowed",
			"model_call_refused", "fixture_missing":
			return background.Failed(background.CodeReportNotReady)
		}
	}
	return background.Failed(background.CodeTransientInternal)
}
