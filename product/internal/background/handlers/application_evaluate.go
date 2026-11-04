// application_evaluate: the maintenance-side half of the evaluation pipeline.
//
// Since F13A/T052 this handler is the real PlatformTaskRunner for an application
// evaluation. It never touches an evaluation/jobs/artifacts table. It asks the
// server for its job-scoped evaluation binding, waits for the dataset artifact
// to become ready (heartbeating, without holding a system_io slot), downloads
// the sealed dataset through a signed link, and then, for every case:
//
//  1. asks the server to create the case's platform run *as the evaluation's
//     owner* from the evaluation's frozen snapshot (the same run path a user
//     submission uses: sealed input, queued run, ready start job);
//  2. performs the case's predefined interactions that exist in this build
//     (cancel) through the real owner-facing transition;
//  3. waits, with a bounded timeout that lands in not_run, for the run to reach
//     a rest state, executed by the real `cmd/supervisor exec` path;
//  4. reads independent truth back: the run state, its sealed output artifact,
//     the real platform.operations ledger counts and the citation sources that
//     output declares;
//  5. runs the deterministic checker (json_equals/decimal_equals/run_state/
//     effect_count/citation_sources) against that truth.
//
// It uploads a sealed report document with per-case evidence through the
// job-scoped upload/complete/status flow and submits the report artifact id.
//
// The distinction the ticket exists for is deliberate:
//
//   - a business evaluation verdict (a case failed, a run timed out, a target
//     this build cannot execute) is a report whose status is computed by the
//     checker; the job still succeeds, and the server records that report;
//   - an infrastructure problem (cannot read the dataset, cannot upload bytes,
//     the report never becomes ready) fails the job with a retryable code, and
//     the server fails the evaluation with no report row.
//
// The handler cannot declare `passed` on its own: the server re-reads the sealed
// report in the completion transaction, requires per-case assertion evidence for
// every passed case, recomputes the verdict from the cases and applies the
// binding rules.
//
// The legacy `orchara.evaluation-dataset/1` document (T026's synthetic
// placeholder) is still recognised and keeps its exact old behaviour so the
// T026 tests describe the same pipeline; it is never used for an F13A dataset.
package handlers

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"

	"orchara/platform/internal/background"
	"orchara/platform/internal/evaluation"
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
	// defaultEvaluationCaseTimeout bounds one case's run to a rest state. A
	// timeout is recorded as not_run; it can never become a pass.
	defaultEvaluationCaseTimeout = 2 * time.Minute

	// evaluationDatasetSchema is the T026 synthetic dataset schema. It is kept
	// only for backwards compatibility with the placeholder evaluator.
	evaluationDatasetSchema = "orchara.evaluation-dataset/1"
	// evaluationEvaluatorName identifies the synthetic placeholder verdict in
	// the report, so nobody can mistake it for a real F13A evaluation.
	evaluationEvaluatorName = evaluation.SyntheticEvaluatorID
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

	if evaluation.IsLegacySyntheticDataset(dataset) {
		// T026's deterministic synthetic placeholder. It proves the pipeline and
		// is never produced for a dataset a curator wrote for F13A.
		document := synthesizeEvaluationReport(binding, datasetSHA, dataset)
		return submitEvaluationReport(ctx, work, document)
	}

	parsed, parseErr := evaluation.ParseDataset(dataset)
	if parseErr != nil {
		// The dataset is not a valid evaluation dataset. That is a business
		// verdict about the input, expressed as a failed report (job succeeds).
		return submitEvaluationReport(ctx, work, datasetFailureReport(binding, datasetSHA, parseErr))
	}

	runner := &evaluationRunner{work: work, binding: binding}
	document := runner.run(ctx, parsed, datasetSHA)
	return submitEvaluationReport(ctx, work, document)
}

// evaluationRunner adapts the job scope to the collector's RunClient seam. It is
// the only place the handler talks to the platform about runs; every call
// re-proves the lease and the server re-proves the owner's access.
type evaluationRunner struct {
	work    background.HandlerWork
	binding background.EvaluationBinding
}

func (r *evaluationRunner) heartbeat(ctx context.Context) {
	if r.work.Heartbeat != nil {
		_ = r.work.Heartbeat(ctx)
	}
}

// CreateCaseRun asks the platform to create the case's run as the owner. A
// non-nil taskInput binds the evaluation owner's own fixture (F13A/T056).
func (r *evaluationRunner) CreateCaseRun(
	ctx context.Context, caseID string, input []byte, taskInput *evaluation.TaskInputBinding,
) (string, error) {
	r.heartbeat(ctx)
	ref, err := r.work.Scope.EvaluationRun(
		ctx, r.work.Job.ID, r.work.AttemptToken, caseID, input, taskInput,
	)
	if err != nil {
		return "", err
	}
	return ref.RunID, nil
}

// CaseRunFacts reads the run's independent truth.
func (r *evaluationRunner) CaseRunFacts(ctx context.Context, runID string) (evaluation.Facts, error) {
	r.heartbeat(ctx)
	facts, err := r.work.Scope.EvaluationRunFacts(
		ctx, r.work.Job.ID, r.work.AttemptToken, runID,
	)
	if err != nil {
		return evaluation.Facts{}, err
	}
	return evaluation.Facts{
		RunID:               facts.RunID,
		RunState:            facts.State,
		RunErrorCode:        facts.ErrorCode,
		Output:              facts.Output,
		OutputCollected:     facts.OutputCollected,
		OutputArtifactID:    facts.OutputArtifactID,
		OutputSHA256:        facts.OutputSHA256,
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

// Interact performs one predefined interaction through the real platform
// transition, as the evaluation's owner.
func (r *evaluationRunner) Interact(
	ctx context.Context, runID string, interaction evaluation.Interaction,
) error {
	r.heartbeat(ctx)
	return r.work.Scope.EvaluationRunInteraction(
		ctx, r.work.Job.ID, r.work.AttemptToken, runID, interaction.Kind,
	)
}

// RecordCaseOutcome persists one case's decision as it is made (F13A/T065). The
// server owns the write and re-proves the lease; a case that never created a run
// (an unsupported target) is observable to the progress read only through this
// call. The sealed report remains the record of truth.
func (r *evaluationRunner) RecordCaseOutcome(
	ctx context.Context, caseID string, ordinal int, verdict, reasonCode string,
) error {
	r.heartbeat(ctx)
	return r.work.Scope.EvaluationCaseOutcome(
		ctx, r.work.Job.ID, r.work.AttemptToken, caseID, ordinal, verdict, reasonCode,
	)
}

// run collects every case and builds the report document.
func (r *evaluationRunner) run(
	ctx context.Context, dataset *evaluation.Dataset, datasetSHA string,
) background.EvaluationReportDocument {
	started := time.Now()
	cases, checks := evaluation.Collect(ctx, r, dataset, evaluation.CollectOptions{
		CaseTimeout:  r.caseTimeout(),
		PollInterval: r.pollInterval(),
		Provenance: evaluation.RunProvenance{
			SnapshotID:        r.binding.SnapshotID,
			ManifestSHA256:    r.binding.ManifestSHA256,
			DatasetSHA256:     datasetSHA,
			ApplicationID:     r.binding.ApplicationID,
			DraftRevision:     r.binding.DraftRevision,
			CompilerVersion:   r.binding.CompilerVersion,
			WorkerImageDigest: r.binding.WorkerImageDigest,
		},
	})
	finished := time.Now()
	return r.document(cases, checks, datasetSHA, started, finished)
}

// document is the sealed report: the checker's verdict plus the per-case
// evidence, the frozen identity and the recorded limitations.
func (r *evaluationRunner) document(
	cases []evaluation.CaseReport,
	checks []string,
	datasetSHA string,
	started, finished time.Time,
) background.EvaluationReportDocument {
	startedAt := started
	finishedAt := finished
	return background.EvaluationReportDocument{
		ReportVersion:     evaluation.ReportVersion,
		Kind:              evaluation.ReportKind,
		EvaluationKind:    r.binding.Kind,
		EvaluationID:      r.binding.EvaluationID,
		DatasetArtifactID: r.binding.DatasetArtifactID,
		DatasetSHA256:     datasetSHA,
		ManifestSHA256:    r.binding.ManifestSHA256,
		Status:            evaluation.ComputeStatus(cases),
		Checks:            checks,
		CaseCount:         len(cases),
		Cases:             cases,
		Synthetic:         false,
		Evaluator:         evaluation.RunnerEvaluatorID,
		// There is no judge adapter in this build. The field is informational and
		// the completion guard never reads it; a high score can never rescue a
		// failed hard check.
		Quality: &evaluation.Quality{
			Informational: true,
			Adapter:       "not_installed",
			Score:         nil,
			Note:          "no judge adapter is installed in this build; this score is informational and never gates the verdict",
		},
		Provenance: &evaluation.ReportProvenance{
			SnapshotID:         r.binding.SnapshotID,
			ApplicationID:      r.binding.ApplicationID,
			DraftRevision:      r.binding.DraftRevision,
			CompilerVersion:    r.binding.CompilerVersion,
			WorkerImageDigest:  r.binding.WorkerImageDigest,
			StateSchemaVersion: r.binding.StateSchemaVersion,
			DatasetArtifactID:  r.binding.DatasetArtifactID,
		},
		Limits: []string{
			"published_version targets are not executable in this build (no publication path; F02B #69 / F07); " +
				"such a case is recorded as blocked/unsupported_target, never passed",
			"effect_count.source=stub_ledger is served by the platform's real effect ledger " +
				"(platform.operations rows attributable to the case's run); no separate stub ledger exists",
			"no judge adapter is installed: the quality field is informational and never gates the verdict",
			"predefined interactions: only cancel exists in this build (F06 approvals are out of this slice)",
		},
		StartedAt:  evaluation.FormatTime(&startedAt),
		FinishedAt: evaluation.FormatTime(&finishedAt),
	}
}

func (r *evaluationRunner) caseTimeout() time.Duration {
	if value := r.work.Settings.EvaluationCaseTimeout; value > 0 {
		return value
	}
	return defaultEvaluationCaseTimeout
}

func (r *evaluationRunner) pollInterval() time.Duration {
	if value := r.work.Settings.EvaluationPollInterval; value > 0 {
		return value
	}
	return defaultEvaluationPollInterval
}

// datasetFailureReport is the business verdict for a dataset the platform cannot
// evaluate: one failed dataset case, a failed report, and the job still
// succeeds. It can never be passed.
func datasetFailureReport(
	binding background.EvaluationBinding, datasetSHA string, cause error,
) background.EvaluationReportDocument {
	return background.EvaluationReportDocument{
		ReportVersion:     evaluation.ReportVersion,
		Kind:              evaluation.ReportKind,
		EvaluationKind:    binding.Kind,
		EvaluationID:      binding.EvaluationID,
		DatasetArtifactID: binding.DatasetArtifactID,
		DatasetSHA256:     datasetSHA,
		ManifestSHA256:    binding.ManifestSHA256,
		Status:            evaluation.StatusFailed,
		Checks:            []string{evaluation.CheckDatasetSchema},
		CaseCount:         1,
		Synthetic:         false,
		Evaluator:         evaluation.RunnerEvaluatorID,
		Cases: []background.EvaluationReportCase{{
			ID:         "dataset",
			Status:     evaluation.VerdictFailed,
			Verdict:    evaluation.VerdictFailed,
			ReasonCode: "dataset_invalid",
			Detail:     cause.Error(),
		}},
		Quality: &evaluation.Quality{
			Informational: true, Adapter: "not_installed", Score: nil,
		},
		Provenance: &evaluation.ReportProvenance{
			SnapshotID:        binding.SnapshotID,
			ApplicationID:     binding.ApplicationID,
			DraftRevision:     binding.DraftRevision,
			CompilerVersion:   binding.CompilerVersion,
			WorkerImageDigest: binding.WorkerImageDigest,
			DatasetArtifactID: binding.DatasetArtifactID,
		},
		Limits: []string{
			"the dataset was refused before any case ran; the job succeeds and the report is failed",
		},
	}
}

// submitEvaluationReport uploads the sealed report and waits for its finalize.
func submitEvaluationReport(
	ctx context.Context,
	work background.HandlerWork,
	document background.EvaluationReportDocument,
) background.Outcome {
	reportBytes, err := canonicalJSON(document)
	if err != nil {
		// The report is built from server-controlled values; a canonicalisation
		// failure is an internal defect.
		return background.Failed(background.CodeTransientInternal)
	}
	reportSHA := sha256Hex(reportBytes)

	ticket, err := work.Scope.Upload(ctx, work.Job.ID, work.AttemptToken, background.UploadRequest{
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
	view, err := work.Scope.Complete(ctx, work.Job.ID, work.AttemptToken, ticket.ArtifactID, reportSHA)
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

// synthesizeEvaluationReport turns one *legacy synthetic* dataset document into
// the report the guard will verify. The verdict is fully determined by the
// dataset bytes:
//
//   - the document must be a JSON object with a `cases` array (schema, when
//     present, must be "orchara.evaluation-dataset/1");
//   - each case declares an id and the SHA-256 of the RFC 8785 JCS bytes of its
//     `input`; the synthetic evaluator recomputes that digest and passes/fails
//     the case;
//   - zero cases is not_run (never passed), a malformed dataset is one failed
//     `dataset` case (a business failure, never a job failure).
//
// It is kept for the T026 tests and the T026 report, and is never reached for a
// dataset that parses as an F13A evaluation dataset.
func synthesizeEvaluationReport(
	binding background.EvaluationBinding,
	datasetSHA string,
	dataset []byte,
) background.EvaluationReportDocument {
	document := background.EvaluationReportDocument{
		ReportVersion:     evaluation.ReportVersion,
		Kind:              evaluation.ReportKind,
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
		// The dataset is not a valid synthetic dataset. That is a business
		// verdict about the input, expressed as a failed report (job succeeds).
		document.Status = evaluation.StatusFailed
		document.Checks = []string{"dataset_schema"}
		document.CaseCount = 1
		document.Cases = []background.EvaluationReportCase{{
			ID:     "dataset",
			Status: evaluation.VerdictFailed,
			Detail: err.Error(),
		}}
		return document
	}
	document.Checks = checks
	document.Cases = cases
	document.CaseCount = len(cases)
	switch {
	case len(cases) == 0:
		document.Status = evaluation.StatusNotRun
	case countFailed(cases) > 0:
		document.Status = evaluation.StatusFailed
	default:
		document.Status = evaluation.StatusPassed
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
			item.Status = evaluation.VerdictFailed
			item.Detail = "the case has no id"
		case seen[item.ID]:
			item.Status = evaluation.VerdictFailed
			item.Detail = "the case id is duplicated"
		case !isSHA256(item.ExpectedSHA256):
			item.Status = evaluation.VerdictFailed
			item.Detail = "expected_sha256 must be 64 lowercase hexadecimal characters"
		case len(raw.Input) == 0:
			item.Status = evaluation.VerdictFailed
			item.Detail = "the case has no input"
		default:
			canonical, err := jcs.CanonicalizeBytes(raw.Input)
			if err != nil {
				item.Status = evaluation.VerdictFailed
				item.Detail = "the case input is not canonical JSON"
				break
			}
			item.ActualSHA256 = sha256Hex(canonical)
			if item.ActualSHA256 == item.ExpectedSHA256 {
				item.Status = evaluation.VerdictPassed
			} else {
				item.Status = evaluation.VerdictFailed
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
		if item.Status == evaluation.VerdictFailed {
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
		case "evaluation_run_not_found":
			return background.Failed(background.CodeEvaluationBindingMismatch)
		case "evaluation_not_visible":
			// The evaluation's owner can no longer access the application. That
			// is not retryable: the collector must not collect for a principal
			// who lost access, and an infrastructure retry would not restore it.
			return background.Failed(background.CodeEvaluationBindingMismatch)
		case "candidate_mismatch", "candidate_unavailable", "model_not_allowed",
			"model_call_refused", "fixture_missing":
			return background.Failed(background.CodeReportNotReady)
		}
	}
	return background.Failed(background.CodeTransientInternal)
}
