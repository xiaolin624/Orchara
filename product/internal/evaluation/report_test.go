package evaluation

import (
	"strings"
	"testing"
)

func passedCase(id string) CaseReport {
	return CaseReport{
		ID: id, Status: VerdictPassed, Verdict: VerdictPassed,
		Assertions: []Outcome{{Kind: "run_state", Expected: "succeeded", Actual: "succeeded", Passed: true}},
	}
}

func failedCase(id string) CaseReport {
	return CaseReport{
		ID: id, Status: VerdictFailed, Verdict: VerdictFailed, ReasonCode: ReasonAssertionFailed,
		Assertions: []Outcome{{Kind: "run_state", Expected: "succeeded", Actual: "failed", Passed: false}},
	}
}

func TestComputeStatusNeverPassesAnEmptyChecker(t *testing.T) {
	empty := CaseReport{ID: "c1", Status: VerdictPassed, Verdict: VerdictPassed}
	if status := ComputeStatus([]CaseReport{empty}); status != StatusFailed {
		t.Fatalf("a passed case with no assertions must not make the report passed, got %s", status)
	}
}

func TestComputeStatusPassesOnlyWhenEveryCaseRanAndPassed(t *testing.T) {
	if status := ComputeStatus([]CaseReport{passedCase("a"), passedCase("b")}); status != StatusPassed {
		t.Fatalf("expected passed, got %s", status)
	}
	if status := ComputeStatus([]CaseReport{passedCase("a"), failedCase("b")}); status != StatusFailed {
		t.Fatalf("one failed case must make the report failed, got %s", status)
	}
	mixed := []CaseReport{passedCase("a"), {ID: "b", Status: VerdictNotRun, Verdict: VerdictNotRun}}
	if status := ComputeStatus(mixed); status != StatusFailed {
		t.Fatalf("a case that did not run must not let the report pass, got %s", status)
	}
}

func TestComputeStatusIsNotRunOnlyWhenNothingWasCollected(t *testing.T) {
	if status := ComputeStatus(nil); status != StatusNotRun {
		t.Fatalf("an empty case set is not_run, got %s", status)
	}
	blocked := []CaseReport{
		{ID: "a", Status: VerdictBlocked, Verdict: VerdictBlocked, ReasonCode: ReasonUnsupportedTarget},
		{ID: "b", Status: VerdictNotRun, Verdict: VerdictNotRun, ReasonCode: ReasonRunTimeout},
	}
	if status := ComputeStatus(blocked); status != StatusNotRun {
		t.Fatalf("no collected case is not_run, got %s", status)
	}
}

func TestComputeStatusIgnoresQualityEntirely(t *testing.T) {
	score := 1.0
	document := Report{
		ReportVersion:  ReportVersion,
		Kind:           ReportKind,
		EvaluationKind: "application",
		Status:         StatusFailed,
		Checks:         []string{"run_state"},
		CaseCount:      1,
		Cases:          []CaseReport{failedCase("c1")},
		Synthetic:      false,
		Evaluator:      RunnerEvaluatorID,
		Quality:        &Quality{Informational: true, Adapter: "not_installed", Score: &score},
		Provenance:     &ReportProvenance{SnapshotID: "s1"},
	}
	// The document's status is the computed one even though Quality claims a
	// perfect score: there is no judge adapter, and a score can never gate the
	// hard verdict.
	if reason := ValidateShape(&document); reason != "" {
		t.Fatalf("a failed report with a high informational score must be accepted as failed: %s", reason)
	}
	document.Status = StatusPassed
	if reason := ValidateShape(&document); reason == "" {
		t.Fatal("a high quality score must not let a failed case become passed")
	}
}

func TestValidateShapeRefusesARunnerPassWithoutAssertions(t *testing.T) {
	document := Report{
		ReportVersion:  ReportVersion,
		Kind:           ReportKind,
		EvaluationKind: "application",
		Status:         StatusPassed,
		Checks:         []string{"run_state"},
		CaseCount:      1,
		Cases: []CaseReport{{
			ID: "c1", Status: VerdictPassed, Verdict: VerdictPassed,
		}},
		Synthetic:  false,
		Evaluator:  RunnerEvaluatorID,
		Provenance: &ReportProvenance{SnapshotID: "s1"},
	}
	if reason := ValidateShape(&document); !strings.Contains(reason, "no assertion evidence") {
		t.Fatalf("an empty checker must be refused for the real runner, got %q", reason)
	}
}

func TestValidateShapeRefusesARunnerPassWithAFailedAssertion(t *testing.T) {
	item := passedCase("c1")
	item.Assertions[0].Passed = false
	document := Report{
		ReportVersion: ReportVersion, Kind: ReportKind, EvaluationKind: "application",
		Status: StatusPassed, Checks: []string{"run_state"}, CaseCount: 1,
		Cases: []CaseReport{item}, Evaluator: RunnerEvaluatorID,
		Provenance: &ReportProvenance{SnapshotID: "s1"},
	}
	if reason := ValidateShape(&document); reason == "" {
		t.Fatal("a passed case carrying a failed assertion must be refused")
	}
}

func TestValidateShapeKeepsTheLegacySyntheticDocumentValid(t *testing.T) {
	// This is the exact shape T026's crafted-report tests use: one passed case
	// with no assertion list, a foreign evaluator id, and no provenance. The
	// F13A evidence rules must not tighten that legacy path.
	document := Report{
		ReportVersion: ReportVersion, Kind: ReportKind, EvaluationKind: "model",
		Status: StatusPassed, Checks: []string{"dataset_schema", "case_sha256"},
		CaseCount: 1,
		Cases:     []CaseReport{{ID: "c1", Status: VerdictPassed}},
		Synthetic: true, Evaluator: "test-crafted/1",
	}
	if reason := ValidateShape(&document); reason != "" {
		t.Fatalf("the legacy crafted document must stay shape-valid: %s", reason)
	}
}

func TestValidateShapeRefusesDisagreeingStatusAndDuplicateIDs(t *testing.T) {
	base := func() Report {
		return Report{
			ReportVersion: ReportVersion, Kind: ReportKind, EvaluationKind: "application",
			Status: StatusPassed, Checks: []string{"run_state"}, CaseCount: 1,
			Cases: []CaseReport{passedCase("c1")}, Evaluator: "test-crafted/1",
		}
	}
	emptyCases := base()
	emptyCases.Cases = nil
	emptyCases.CaseCount = 0
	if reason := ValidateShape(&emptyCases); reason == "" {
		t.Fatal("passed with no cases must be refused")
	}

	duplicate := base()
	duplicate.Cases = []CaseReport{passedCase("c1"), passedCase("c1")}
	duplicate.CaseCount = 2
	if reason := ValidateShape(&duplicate); reason == "" {
		t.Fatal("duplicate case ids must be refused")
	}

	mismatch := base()
	mismatch.Status = StatusFailed
	if reason := ValidateShape(&mismatch); reason == "" {
		t.Fatal("a status that disagrees with the cases must be refused")
	}

	noChecks := base()
	noChecks.Checks = nil
	if reason := ValidateShape(&noChecks); reason == "" {
		t.Fatal("a report without checks must be refused")
	}

	badVerdict := base()
	badVerdict.Cases = []CaseReport{{ID: "c1", Status: VerdictPassed, Verdict: VerdictFailed}}
	if reason := ValidateShape(&badVerdict); reason == "" {
		t.Fatal("a case whose verdict disagrees with its status must be refused")
	}
}
