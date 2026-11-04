package evaluation

// F13A/T052: the PlatformTaskRunner's collection loop.
//
// For every case the collector drives the platform *as the evaluation's owner*
// through the same run path a user submission uses (the platform creates the
// run, seals its input, queues its start job and executes it), waits for a rest
// state with a bounded timeout, and then reads independent truth: the run state,
// the run's sealed output artifact, the platform's real operation ledger and the
// citation sources that output declares.
//
// What it never does:
//
//   - treat HTTP 202, a stream end or a model that claims success as proof. Only
//     a terminal run row and its sealed output count.
//   - count model tool-call text as side effects. The count comes from
//     platform.operations.
//   - pass a case whose fact could not be collected.
//   - fake a target it cannot execute: published_version has no publish path in
//     this build and is recorded as blocked/unsupported_target, never passed.

import (
	"context"
	"errors"
	"fmt"
	"sort"
	"strings"
	"time"
)

// RunClient is the collector's seam to the platform. The handler implements it
// on top of the job-scoped internal API, so the collector itself has no database
// credential and no private shortcut: every call re-proves the job's lease and
// the server re-checks the evaluation owner's current authorization.
type RunClient interface {
	// CreateCaseRun creates (or returns the already-created) run for one case.
	// The server owns the run identity, the sealed input and the run owner. A
	// non-nil taskInput asks the server to bind the evaluation owner's own
	// content-addressed fixture to the run as its task_input artifact (T056).
	CreateCaseRun(ctx context.Context, caseID string, input []byte, taskInput *TaskInputBinding) (string, error)
	// CaseRunFacts reads the independent truth of that run.
	CaseRunFacts(ctx context.Context, runID string) (Facts, error)
	// Interact performs one predefined platform interaction on the run.
	Interact(ctx context.Context, runID string, interaction Interaction) error
}

// CollectOptions bounds one evaluation.
type CollectOptions struct {
	// CaseTimeout bounds how long one case's run may take to reach a rest
	// state. A timeout is a recorded not_run, never a pass.
	CaseTimeout time.Duration
	// PollInterval is the facts-poll cadence.
	PollInterval time.Duration
	// Provenance is the frozen identity every case's run must have executed
	// under; it is the report-level binding, and the running case's own
	// provenance is recorded per case.
	Provenance RunProvenance
}

// Case rest-state vocabulary. These are the states the contract's run_state
// assertion can name, and the collector stops waiting at any of them.
var restStates = map[string]bool{
	"succeeded": true, "failed": true, "cancelled": true,
	"needs_reconciliation": true, "recovery_pending": true,
}

// Case refusal reason codes. They are stable, safe and never provider text.
const (
	ReasonUnsupportedTarget    = "unsupported_target"
	ReasonRunTimeout           = "run_timeout"
	ReasonAssertionFailed      = "assertion_failed"
	ReasonRunUnavailable       = "run_unavailable"
	ReasonCollectionRefused    = "collection_refused"
	ReasonNoAssertions         = "no_assertions"
	ReasonProvenanceMismatch   = "provenance_mismatch"
	ReasonTaskInputUnavailable = "task_input_unavailable"
)

// Check failure_reason is recorded when a case declares the business failure it
// expects (T056). It is not a contract assertion kind; it is evaluated by the
// same checker against the run's own state and error code.
const CheckFailureReason = KindFailureReason

// Checks the collector reports having performed. The report's `checks` array
// must never be empty, so a dataset-only or target-only refusal still names the
// check that decided it.
const (
	CheckDatasetSchema = "dataset_schema"
	CheckTargetSupport = "target_supported"
	CheckRunFacts      = "run_facts"
)

// Collect evaluates every case and returns the per-case reports plus the list of
// checks that were actually performed.
func Collect(
	ctx context.Context,
	client RunClient,
	dataset *Dataset,
	options CollectOptions,
) ([]CaseReport, []string) {
	checks := make(map[string]bool)
	results := make([]CaseReport, 0, len(dataset.Cases))
	for _, item := range dataset.Cases {
		result := collectCase(ctx, client, item, options, checks)
		results = append(results, result)
	}
	names := make([]string, 0, len(checks)+2)
	for name := range checks {
		names = append(names, name)
	}
	sort.Strings(names)
	return results, names
}

// collectCase runs one case and returns its verdict. It never panics on a
// platform refusal: a refusal is a recorded, located case result.
func collectCase(
	ctx context.Context,
	client RunClient,
	item Case,
	options CollectOptions,
	checks map[string]bool,
) CaseReport {
	result := CaseReport{ID: item.CaseID, Verdict: VerdictNotRun, Status: VerdictNotRun}
	for _, assertion := range item.Assertions {
		checks[assertion.Kind] = true
	}
	if item.ExpectedFailure != nil {
		checks[CheckFailureReason] = true
	}
	checks[CheckRunFacts] = true

	if len(item.Assertions) == 0 {
		// Defence in depth: the submit path refuses this dataset, and a case
		// that still arrives here can never be passed.
		result.ReasonCode = ReasonNoAssertions
		result.Detail = "the case declares no assertions; an empty checker is not a pass"
		return result
	}

	switch item.Target.Kind {
	case "published_version":
		// There is no publish path in this build (F02B #69 / F07). The case is
		// recorded as an honest refusal, never executed and never passed.
		checks[CheckTargetSupport] = true
		result.Status = VerdictBlocked
		result.Verdict = VerdictBlocked
		result.ReasonCode = ReasonUnsupportedTarget
		result.Detail = "published_version targets cannot be executed in this build: " +
			"there is no publication path (F02B #69) and this runner does not fake one"
		return result
	case "debug_snapshot":
		// supported below
	default:
		checks[CheckTargetSupport] = true
		result.Status = VerdictBlocked
		result.Verdict = VerdictBlocked
		result.ReasonCode = ReasonUnsupportedTarget
		result.Detail = "target kind " + item.Target.Kind + " is not executable"
		return result
	}

	input, err := item.RunInput()
	if err != nil {
		result.Status = VerdictBlocked
		result.Verdict = VerdictBlocked
		result.ReasonCode = ReasonCollectionRefused
		result.Detail = "the case input is not canonicalisable JSON"
		return result
	}

	timeout := options.CaseTimeout
	if timeout <= 0 {
		timeout = 2 * time.Minute
	}
	poll := options.PollInterval
	if poll <= 0 {
		poll = 200 * time.Millisecond
	}
	caseCtx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()

	started := time.Now()
	runID, err := client.CreateCaseRun(caseCtx, item.CaseID, input, item.TaskInput)
	if err != nil {
		// The platform refused to create the run (a withdrawn application, a
		// missing fixture, a stale lease, a missing snapshot). That is never a
		// pass, and a missing fixture gets its own reason so the curator learns
		// which input bytes to upload.
		reason := safeReason(err)
		result.Status = VerdictBlocked
		result.Verdict = VerdictBlocked
		result.ReasonCode = ReasonCollectionRefused
		result.Detail = "the platform refused to create this case's run: " + reason
		if reason == ReasonTaskInputUnavailable {
			result.ReasonCode = ReasonTaskInputUnavailable
			result.Detail = "the case's task_input fixture is not among the evaluation owner's ready " +
				"task_input artifacts; upload the exact fixture bytes the case names and retry"
		}
		return result
	}
	result.Facts = &CaseFacts{RunID: runID}

	interactions, err := item.Interactions()
	if err != nil {
		result.Status = VerdictBlocked
		result.Verdict = VerdictBlocked
		result.ReasonCode = ReasonCollectionRefused
		result.Detail = "the case declares an invalid predefined interaction"
		return result
	}

	facts, timedOut := waitForRestState(caseCtx, client, runID, poll, interactions)
	result.Timings = &CaseTimings{
		RunStartedAt:      FormatTime(facts.StartedAt),
		RunFinishedAt:     FormatTime(facts.FinishedAt),
		CollectDurationMS: time.Since(started).Milliseconds(),
	}
	if facts.StartedAt != nil && facts.FinishedAt != nil {
		result.Timings.RunDurationMS = facts.FinishedAt.Sub(*facts.StartedAt).Milliseconds()
	}
	applyFacts(result.Facts, facts)

	if timedOut {
		result.Status = VerdictNotRun
		result.Verdict = VerdictNotRun
		result.ReasonCode = ReasonRunTimeout
		result.Detail = fmt.Sprintf(
			"the run did not reach a rest state within %s (observed state %q); a timeout is recorded as not_run, never passed",
			timeout, facts.RunState)
		result.Assertions = caseOutcomes(item, facts)
		return result
	}
	if facts.RunState == "" {
		result.Status = VerdictBlocked
		result.Verdict = VerdictBlocked
		result.ReasonCode = ReasonRunUnavailable
		result.Detail = "the run's state facts could not be collected"
		return result
	}
	// Traceability check: the run must have executed the frozen snapshot this
	// evaluation was submitted with. A run against another manifest is not this
	// case's truth, so it can never satisfy the case.
	if expected := options.Provenance.ManifestSHA256; expected != "" &&
		facts.Provenance.ManifestSHA256 != "" &&
		!strings.EqualFold(expected, facts.Provenance.ManifestSHA256) {
		result.Status = VerdictBlocked
		result.Verdict = VerdictBlocked
		result.ReasonCode = ReasonProvenanceMismatch
		result.Detail = "the run executed a different manifest than the evaluation's frozen snapshot"
		return result
	}

	outcomes := caseOutcomes(item, facts)
	result.Assertions = outcomes
	failed := 0
	for _, outcome := range outcomes {
		if !outcome.Passed {
			failed++
		}
	}
	if failed == 0 {
		result.Status = VerdictPassed
		result.Verdict = VerdictPassed
		return result
	}
	result.Status = VerdictFailed
	result.Verdict = VerdictFailed
	result.ReasonCode = ReasonAssertionFailed
	result.Detail = fmt.Sprintf("%d of %d assertions failed against the run's collected truth", failed, len(outcomes))
	return result
}

// caseOutcomes evaluates one case's contract checker plus its optional T056
// failure expectation against the same collected facts. The failure expectation
// is appended to the same assertions array, so a bad-input case's pass can never
// hide an unexpected failure reason.
func caseOutcomes(item Case, facts Facts) []Outcome {
	outcomes := EvaluateAssertions(item.Assertions, facts)
	if outcome := EvaluateFailureExpectation(item.ExpectedFailure, facts); outcome != nil {
		outcomes = append(outcomes, *outcome)
	}
	return outcomes
}

// waitForRestState polls the run's facts, performs the case's predefined
// interactions once the run exists, and returns the last facts plus whether the
// deadline expired. The first facts read is what proves the run exists before an
// interaction is attempted, so a cancel never races the run's creation.
func waitForRestState(
	ctx context.Context,
	client RunClient,
	runID string,
	poll time.Duration,
	interactions []Interaction,
) (Facts, bool) {
	var (
		facts      Facts
		lastErr    error
		interacted = make(map[string]bool, len(interactions))
		firstRead  = false
	)
	for {
		read, err := client.CaseRunFacts(ctx, runID)
		if err != nil {
			lastErr = err
			// A refusal to read facts is not a run verdict; keep the last good
			// facts (possibly zero) and let the caller record the refusal.
			if ctx.Err() != nil {
				return facts, true
			}
		} else {
			facts = read
			firstRead = true
			if lastErr != nil {
				lastErr = nil
			}
			if restStates[facts.RunState] {
				return facts, false
			}
		}
		if firstRead {
			for _, interaction := range interactions {
				if interacted[interaction.Kind] {
					continue
				}
				// One best-effort write: a refused interaction leaves a real
				// effect on the run's own state and is not retried.
				_ = client.Interact(ctx, runID, interaction)
				interacted[interaction.Kind] = true
			}
		}
		select {
		case <-ctx.Done():
			return facts, true
		case <-time.After(poll):
		}
	}
}

// applyFacts copies the collected truth into the report's evidence block.
func applyFacts(target *CaseFacts, facts Facts) {
	target.RunState = facts.RunState
	target.RunErrorCode = facts.RunErrorCode
	target.OutputArtifactID = facts.OutputArtifactID
	target.OutputSHA256 = facts.OutputSHA256
	target.EffectCounts = nil
	if facts.EffectsCollected {
		target.EffectCounts = make(map[string]int64, len(facts.Effects))
		for capability, count := range facts.Effects {
			target.EffectCounts[capability] = count
		}
	}
	target.CitationSources = nil
	if facts.CitationsCollected {
		target.CitationSources = append([]string(nil), facts.Citations...)
	}
	target.TaskInputArtifactID = facts.TaskInputArtifactID
	target.TaskInputSHA256 = facts.TaskInputSHA256
	provenance := facts.Provenance
	if provenance.SnapshotID != "" || provenance.ManifestSHA256 != "" {
		target.Provenance = &provenance
	}
}

// safeReason turns a client error into a short, safe reason. It never copies a
// provider body, a URL or a credential: only the stable error code the client
// carries.
func safeReason(err error) string {
	var coded interface{ ErrorCode() string }
	if errors.As(err, &coded) {
		if code := strings.TrimSpace(coded.ErrorCode()); code != "" {
			return code
		}
	}
	message := strings.TrimSpace(err.Error())
	if len(message) > 200 {
		message = message[:200]
	}
	return message
}
