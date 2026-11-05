package catalog

import (
	"testing"

	"orchara/platform/internal/evaluation"
)

// passedCase builds one passed report case with the evidence the gate reads.
func passedCase(id, runState string, assertions ...evaluation.Outcome) evaluation.CaseReport {
	return evaluation.CaseReport{
		ID:         id,
		Status:     evaluation.VerdictPassed,
		Verdict:    evaluation.VerdictPassed,
		Assertions: assertions,
		Facts:      &evaluation.CaseFacts{RunID: "run-" + id, RunState: runState},
	}
}

func passedOutcome(kind string) evaluation.Outcome {
	return evaluation.Outcome{Kind: kind, Passed: true}
}

func failureReason(code string) evaluation.Outcome {
	return evaluation.Outcome{Kind: evaluation.KindFailureReason, Actual: code, Passed: true}
}

func fiveCategoryReport() *evaluation.Report {
	return &evaluation.Report{
		Status: evaluation.StatusPassed,
		Cases: []evaluation.CaseReport{
			passedCase("normal", "succeeded", passedOutcome("run_state"), passedOutcome("decimal_equals")),
			passedCase("bad", "failed", passedOutcome("run_state"), failureReason("missing_column")),
			passedCase("bounds", "failed", passedOutcome("run_state"), failureReason("row_limit_exceeded")),
			passedCase("tool", "failed", passedOutcome("run_state"), failureReason("sandbox_unavailable")),
			passedCase("normal-2", "succeeded", passedOutcome("run_state")),
		},
	}
}

func TestEvaluateAuthorCaseCoverageSatisfiesAllFive(t *testing.T) {
	coverage := EvaluateAuthorCaseCoverage(fiveCategoryReport())
	if !coverage.Satisfied {
		t.Fatalf("coverage = %+v, want satisfied", coverage)
	}
	if len(coverage.Missing) != 0 {
		t.Fatalf("missing = %v, want none", coverage.Missing)
	}
	want := []string{
		AuthorCaseNormal, AuthorCaseBadInput, AuthorCaseOutOfBounds,
		AuthorCaseToolFailure, AuthorCaseOutputCheck,
	}
	if len(coverage.Categories) != len(want) {
		t.Fatalf("categories = %v, want %v", coverage.Categories, want)
	}
	for index := range want {
		if coverage.Categories[index] != want[index] {
			t.Fatalf("categories = %v, want %v", coverage.Categories, want)
		}
	}
	if coverage.CaseCount != 5 || coverage.MinimumCases != MinAuthorCases {
		t.Fatalf("counts = %+v", coverage)
	}
}

func TestEvaluateAuthorCaseCoverageRefusesMissingCategories(t *testing.T) {
	document := fiveCategoryReport()
	// Drop the tool-failure case: every other category stays covered.
	document.Cases[3] = passedCase("normal-3", "succeeded", passedOutcome("run_state"))
	coverage := EvaluateAuthorCaseCoverage(document)
	if coverage.Satisfied {
		t.Fatalf("satisfied without a tool-failure case: %+v", coverage)
	}
	if len(coverage.Missing) != 1 || coverage.Missing[0] != AuthorCaseToolFailure {
		t.Fatalf("missing = %v, want [tool_failure]", coverage.Missing)
	}
}

func TestEvaluateAuthorCaseCoverageRequiresFiveCases(t *testing.T) {
	document := fiveCategoryReport()
	document.Cases = document.Cases[:4]
	coverage := EvaluateAuthorCaseCoverage(document)
	if coverage.Satisfied {
		t.Fatalf("four cases satisfied the gate: %+v", coverage)
	}
	if coverage.CaseCount != 4 {
		t.Fatalf("case count = %d", coverage.CaseCount)
	}
}

func TestEvaluateAuthorCaseCoverageRefusesNilAndNonPassedReports(t *testing.T) {
	for name, document := range map[string]*evaluation.Report{
		"nil":     nil,
		"failed":  {Status: evaluation.StatusFailed},
		"not_run": {Status: evaluation.StatusNotRun},
	} {
		if coverage := EvaluateAuthorCaseCoverage(document); coverage.Satisfied {
			t.Fatalf("%s report satisfied the gate: %+v", name, coverage)
		}
	}
}

func TestClassifyFailureReasonIsClosedAndDocumented(t *testing.T) {
	cases := map[string]string{
		"missing_column":      AuthorCaseBadInput,
		"duplicate_order_id":  AuthorCaseBadInput,
		"tool_timeout":        AuthorCaseToolFailure,
		"sandbox_unavailable": AuthorCaseToolFailure,
		"row_limit_exceeded":  AuthorCaseOutOfBounds,
		"ambiguous_request":   AuthorCaseOutOfBounds,
		"out_of_scope":        AuthorCaseOutOfBounds,
		"unsupported_input":   AuthorCaseOutOfBounds,
	}
	for code, want := range cases {
		if got := classifyFailureReason(code); got != want {
			t.Fatalf("classifyFailureReason(%q) = %q, want %q", code, got, want)
		}
	}
}

func TestASingleCaseCanSatisfySeveralPredicatesWithoutSatisfyingTheGate(t *testing.T) {
	// One normal case with an output assertion covers normal + output_check only.
	coverage := EvaluateAuthorCaseCoverage(&evaluation.Report{
		Status: evaluation.StatusPassed,
		Cases: []evaluation.CaseReport{
			passedCase("only", "succeeded", passedOutcome("run_state"), passedOutcome("json_equals")),
		},
	})
	if coverage.Satisfied {
		t.Fatalf("one case satisfied the gate: %+v", coverage)
	}
	if len(coverage.Missing) != 3 {
		t.Fatalf("missing = %v", coverage.Missing)
	}
}
