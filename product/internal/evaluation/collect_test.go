package evaluation

// F13A/T062: the collector's verdict rules for a run that did not finish.
//
// The staged RunClient below is a test double for the *platform seam*, not for
// the thing under test: the collector itself runs for real and its decision is
// asserted. The end-to-end suite (tests/f13a/test_t062_runner.py) proves the
// same rule through the real HTTP/mTLS/PG path.

import (
	"context"
	"encoding/json"
	"testing"
	"time"
)

type stagedRunClient struct {
	facts        Facts
	created      int
	interactions []string
	taskInputs   []*TaskInputBinding
	outcomes     []stagedOutcome
}

type stagedOutcome struct {
	caseID     string
	ordinal    int
	verdict    string
	reasonCode string
}

func (c *stagedRunClient) CreateCaseRun(
	_ context.Context, caseID string, _ []byte, taskInput *TaskInputBinding,
) (string, error) {
	c.created++
	c.taskInputs = append(c.taskInputs, taskInput)
	return "run-" + caseID, nil
}

func (c *stagedRunClient) CaseRunFacts(_ context.Context, _ string) (Facts, error) {
	return c.facts, nil
}

func (c *stagedRunClient) Interact(_ context.Context, _ string, interaction Interaction) error {
	c.interactions = append(c.interactions, interaction.Kind)
	return nil
}

func (c *stagedRunClient) RecordCaseOutcome(
	_ context.Context, caseID string, ordinal int, verdict, reasonCode string,
) error {
	c.outcomes = append(c.outcomes, stagedOutcome{
		caseID: caseID, ordinal: ordinal, verdict: verdict, reasonCode: reasonCode,
	})
	return nil
}

func collectOneCase(t *testing.T, facts Facts, assertions ...Assertion) CaseReport {
	t.Helper()
	client := &stagedRunClient{facts: facts}
	dataset := &Dataset{Cases: []Case{{
		CaseID:     "case-1",
		Target:     Target{Kind: "debug_snapshot", ID: "frozen"},
		Input:      json.RawMessage(`{"text":"hello"}`),
		Assertions: assertions,
	}}}
	cases, _ := Collect(context.Background(), client, dataset, CollectOptions{
		CaseTimeout:  time.Second,
		PollInterval: time.Millisecond,
	})
	if len(cases) != 1 {
		t.Fatalf("expected one case result, got %d", len(cases))
	}
	return cases[0]
}

func TestACancelledRunIsNeverPassed(t *testing.T) {
	facts := Facts{
		RunID:              "run-1",
		RunState:           "cancelled",
		Effects:            map[string]int64{},
		EffectsCollected:   true,
		CitationsCollected: true,
	}
	// Both assertions are true of the cancelled run; a checker-only verdict would
	// be passed, which is exactly what must never happen.
	result := collectOneCase(t, facts,
		Assertion{Kind: "run_state", ExpectedState: "cancelled"},
		Assertion{Kind: "effect_count", Capability: "test.send", ExpectedCount: 0},
	)
	if result.Status != VerdictFailed || result.Verdict != VerdictFailed {
		t.Fatalf("a cancelled run must fail, got %+v", result)
	}
	if result.ReasonCode != ReasonRunCancelled {
		t.Fatalf("expected reason %q, got %q", ReasonRunCancelled, result.ReasonCode)
	}
	// The evidence is still recorded honestly: the assertions did pass.
	if len(result.Assertions) != 2 {
		t.Fatalf("expected both assertion outcomes, got %+v", result.Assertions)
	}
	for _, outcome := range result.Assertions {
		if !outcome.Passed {
			t.Fatalf("expected the assertion evidence to be recorded, got %+v", outcome)
		}
	}
}

func TestAnUnfinishedRunIsNeverPassed(t *testing.T) {
	facts := Facts{
		RunID:              "run-2",
		RunState:           "recovery_pending",
		Effects:            map[string]int64{},
		EffectsCollected:   true,
		CitationsCollected: true,
	}
	result := collectOneCase(t, facts,
		Assertion{Kind: "effect_count", Capability: "test.send", ExpectedCount: 0},
	)
	if result.Status != VerdictFailed || result.Verdict != VerdictFailed {
		t.Fatalf("an unfinished run must fail, got %+v", result)
	}
	if result.ReasonCode != ReasonRunUnfinished {
		t.Fatalf("expected reason %q, got %q", ReasonRunUnfinished, result.ReasonCode)
	}
}

func TestAFinishedFailedRunCanPassATheCaseThatAssertsIt(t *testing.T) {
	// The completed-state rule is not "succeeded only": a case may assert the
	// expected failure of its own run, and that is a legitimate pass.
	facts := Facts{
		RunID:              "run-3",
		RunState:           "failed",
		RunErrorCode:       "tool_unsupported_graph",
		Effects:            map[string]int64{},
		EffectsCollected:   true,
		CitationsCollected: true,
	}
	result := collectOneCase(t, facts,
		Assertion{Kind: "run_state", ExpectedState: "failed"},
		Assertion{Kind: "effect_count", Capability: "test.send", ExpectedCount: 0},
	)
	if result.Status != VerdictPassed || result.Verdict != VerdictPassed {
		t.Fatalf("a completed failed run with passing assertions is a pass, got %+v", result)
	}
}

func TestATimeoutIsNotRunAndNeverPassed(t *testing.T) {
	// The run never reaches a rest state: the bounded wait expires with the last
	// observed (non-terminal) state. The case is not_run, not passed.
	client := &blockingRunClient{}
	dataset := &Dataset{Cases: []Case{{
		CaseID:     "case-1",
		Target:     Target{Kind: "debug_snapshot", ID: "frozen"},
		Input:      json.RawMessage(`{"text":"hello"}`),
		Assertions: []Assertion{{Kind: "run_state", ExpectedState: "succeeded"}},
	}}}
	cases, _ := Collect(context.Background(), client, dataset, CollectOptions{
		CaseTimeout:  20 * time.Millisecond,
		PollInterval: 5 * time.Millisecond,
	})
	if len(cases) != 1 {
		t.Fatalf("expected one case result, got %d", len(cases))
	}
	if cases[0].Status != VerdictNotRun || cases[0].ReasonCode != ReasonRunTimeout {
		t.Fatalf("expected a not_run timeout, got %+v", cases[0])
	}
}

type blockingRunClient struct{ stagedRunClient }

func (c *blockingRunClient) CaseRunFacts(_ context.Context, _ string) (Facts, error) {
	return Facts{RunID: "run-queued", RunState: "queued"}, nil
}

func TestCollectReportsEveryCaseDecisionWithItsOrdinal(t *testing.T) {
	// F13A/T065: the collector is the only place that knows a case's decision,
	// including one that never created a run. Both a normal pass and a blocked
	// target must be reported with the dataset's ordinal so the live progress
	// read can count them without parsing the report.
	client := &stagedRunClient{facts: Facts{
		RunID:              "run-1",
		RunState:           "succeeded",
		Effects:            map[string]int64{},
		EffectsCollected:   true,
		CitationsCollected: true,
	}}
	dataset := &Dataset{Cases: []Case{
		{
			CaseID:     "first",
			Target:     Target{Kind: "debug_snapshot", ID: "frozen"},
			Input:      json.RawMessage(`{"text":"hello"}`),
			Assertions: []Assertion{{Kind: "run_state", ExpectedState: "succeeded"}},
		},
		{
			CaseID:     "second",
			Target:     Target{Kind: "published_version", ID: "published"},
			Input:      json.RawMessage(`{"text":"hello"}`),
			Assertions: []Assertion{{Kind: "run_state", ExpectedState: "succeeded"}},
		},
	}}
	cases, _ := Collect(context.Background(), client, dataset, CollectOptions{
		CaseTimeout:  time.Second,
		PollInterval: time.Millisecond,
	})
	if len(cases) != 2 {
		t.Fatalf("expected two case results, got %d", len(cases))
	}
	if len(client.outcomes) != 2 {
		t.Fatalf("expected two reported outcomes, got %+v", client.outcomes)
	}
	if got := client.outcomes[0]; got.caseID != "first" || got.ordinal != 0 || got.verdict != VerdictPassed {
		t.Fatalf("unexpected first outcome: %+v", got)
	}
	blocked := client.outcomes[1]
	if blocked.caseID != "second" || blocked.ordinal != 1 || blocked.verdict != VerdictBlocked {
		t.Fatalf("unexpected blocked outcome: %+v", blocked)
	}
	if blocked.reasonCode != ReasonUnsupportedTarget {
		t.Fatalf("the blocked outcome must carry its reason, got %+v", blocked)
	}
}
