package evaluation

import (
	"encoding/json"
	"testing"
	"time"
)

func factsFrom(t *testing.T, output string) Facts {
	t.Helper()
	facts := Facts{
		RunID:              "run-1",
		RunState:           "succeeded",
		Output:             json.RawMessage(output),
		OutputCollected:    true,
		Effects:            map[string]int64{},
		EffectsCollected:   true,
		Citations:          []string{},
		CitationsCollected: true,
	}
	return facts
}

func TestJSONEqualsResolvesNestedPointersAndArrayIndexes(t *testing.T) {
	facts := factsFrom(t, `{"summary":{"total":"250.00","order_count":3},
		"regions":[{"id":"east","total":"200.00"}]}`)
	assertions := []Assertion{
		{Kind: "json_equals", Pointer: "/summary/order_count", ExpectedJSON: json.RawMessage(`3`)},
		{Kind: "json_equals", Pointer: "/regions/0/id", ExpectedJSON: json.RawMessage(`"east"`)},
		{Kind: "json_equals", Pointer: "/regions/1/id", ExpectedJSON: json.RawMessage(`"east"`)},
		{Kind: "json_equals", Pointer: "/summary/order_count", ExpectedJSON: json.RawMessage(`"3"`)},
	}
	outcomes := EvaluateAssertions(assertions, facts)
	if !outcomes[0].Passed || !outcomes[1].Passed {
		t.Fatalf("reachable pointers must pass: %+v", outcomes[:2])
	}
	if outcomes[2].Passed {
		t.Fatal("an out-of-range array index must not pass")
	}
	if outcomes[3].Passed {
		t.Fatal("3 and \"3\" are different JSON values")
	}
}

func TestJSONEqualsWithoutCollectedOutputNeverPasses(t *testing.T) {
	facts := Facts{RunState: "succeeded"}
	outcomes := EvaluateAssertions([]Assertion{
		{Kind: "json_equals", Pointer: "/summary/order_count", ExpectedJSON: json.RawMessage(`0`)},
	}, facts)
	if outcomes[0].Passed {
		t.Fatal("a missing output must not satisfy json_equals")
	}
}

func TestDecimalEqualsComparesExactDecimalsNotBinaryFloats(t *testing.T) {
	facts := factsFrom(t, `{"summary":{"total":"250.00","zero":"0","tiny":"0.1"},
		"numeric_total":250}`)
	assertions := []Assertion{
		{Kind: "decimal_equals", Pointer: "/summary/total", ExpectedDecimal: "250"},
		{Kind: "decimal_equals", Pointer: "/summary/zero", ExpectedDecimal: "0"},
		{Kind: "decimal_equals", Pointer: "/numeric_total", ExpectedDecimal: "250.00"},
		{Kind: "decimal_equals", Pointer: "/summary/tiny", ExpectedDecimal: "0.1"},
		{Kind: "decimal_equals", Pointer: "/summary/tiny", ExpectedDecimal: "0.100000000000000005"},
		{Kind: "decimal_equals", Pointer: "/summary/missing", ExpectedDecimal: "0"},
	}
	outcomes := EvaluateAssertions(assertions, facts)
	for index := 0; index < 4; index++ {
		if !outcomes[index].Passed {
			t.Fatalf("outcome %d must pass, got %+v", index, outcomes[index])
		}
	}
	if outcomes[4].Passed {
		t.Fatal("0.1 and 0.100000000000000005 are not the same decimal; a binary float would wrongly pass")
	}
	if outcomes[5].Passed {
		t.Fatal("a missing pointer must not satisfy decimal_equals")
	}
}

func TestDecimalEqualsRefusesNonDecimalActual(t *testing.T) {
	facts := factsFrom(t, `{"summary":{"total":"not a number"}}`)
	outcomes := EvaluateAssertions([]Assertion{
		{Kind: "decimal_equals", Pointer: "/summary/total", ExpectedDecimal: "0"},
	}, facts)
	if outcomes[0].Passed {
		t.Fatal("a non-decimal actual must not satisfy decimal_equals")
	}
}

func TestRunStateComparesTheCollectedState(t *testing.T) {
	facts := factsFrom(t, `{}`)
	facts.RunState = "cancelled"
	outcomes := EvaluateAssertions([]Assertion{
		{Kind: "run_state", ExpectedState: "cancelled"},
		{Kind: "run_state", ExpectedState: "succeeded"},
	}, facts)
	if !outcomes[0].Passed || outcomes[1].Passed {
		t.Fatalf("unexpected run_state outcomes: %+v", outcomes)
	}
}

func TestEffectCountAssertsZeroHonestly(t *testing.T) {
	facts := factsFrom(t, `{}`)
	facts.Effects = map[string]int64{"test.send": 0}
	outcomes := EvaluateAssertions([]Assertion{
		{Kind: "effect_count", Capability: "test.send", ExpectedCount: 0, Source: "stub_ledger"},
		{Kind: "effect_count", Capability: "test.send", ExpectedCount: 1, Source: "stub_ledger"},
	}, facts)
	if !outcomes[0].Passed {
		t.Fatalf("expected=0 against a real empty ledger must pass: %+v", outcomes[0])
	}
	if outcomes[1].Passed {
		t.Fatalf("expected=1 against an empty ledger must fail: %+v", outcomes[1])
	}
	if outcomes[0].Actual != "0" {
		t.Fatalf("the collected count must be reported, got %q", outcomes[0].Actual)
	}
}

func TestEffectCountWithoutACollectedLedgerNeverPasses(t *testing.T) {
	facts := factsFrom(t, `{}`)
	facts.Effects = nil
	facts.EffectsCollected = false
	outcomes := EvaluateAssertions([]Assertion{
		{Kind: "effect_count", Capability: "test.send", ExpectedCount: 0, Source: "stub_ledger"},
	}, facts)
	if outcomes[0].Passed {
		t.Fatal("an uncollected ledger is not an empty ledger; expected=0 must not pass")
	}
	if outcomes[0].Actual != "not_collected" {
		t.Fatalf("the outcome must say the fact was not collected, got %q", outcomes[0].Actual)
	}
}

func TestCitationSourcesChecksTheAllowedSet(t *testing.T) {
	facts := factsFrom(t, `{}`)
	facts.Citations = []string{"doc-a", "doc-b"}
	outcomes := EvaluateAssertions([]Assertion{
		{Kind: "citation_sources", AllowedSourceIDs: []string{"doc-a", "doc-b"}, RequireAtLeastOne: true},
		{Kind: "citation_sources", AllowedSourceIDs: []string{"doc-a"}, RequireAtLeastOne: true},
		{Kind: "citation_sources", AllowedSourceIDs: []string{"doc-a"}, RequireAtLeastOne: false},
	}, facts)
	if !outcomes[0].Passed {
		t.Fatalf("an allowed citation set must pass: %+v", outcomes[0])
	}
	if outcomes[1].Passed {
		t.Fatal("a cited source outside the allowed set must fail")
	}
	if outcomes[2].Passed {
		t.Fatal("an out-of-set citation fails even when a citation is not required")
	}

	facts.Citations = nil
	empty := EvaluateAssertions([]Assertion{
		{Kind: "citation_sources", AllowedSourceIDs: []string{"doc-a"}, RequireAtLeastOne: true},
	}, facts)
	if empty[0].Passed {
		t.Fatal("require_at_least_one with no citation must fail")
	}
	notRequired := EvaluateAssertions([]Assertion{
		{Kind: "citation_sources", AllowedSourceIDs: []string{"doc-a"}, RequireAtLeastOne: false},
	}, facts)
	if !notRequired[0].Passed {
		t.Fatal("no citation is acceptable when one is not required")
	}
}

func TestUnknownAssertionKindNeverPasses(t *testing.T) {
	outcomes := EvaluateAssertions([]Assertion{{Kind: "custom_script"}}, Facts{})
	if outcomes[0].Passed {
		t.Fatal("an unsupported checker must never pass")
	}
}

func TestValidJSONPointer(t *testing.T) {
	for _, pointer := range []string{"", "/a", "/a/b", "/a~0b", "/a~1b", "/items/0/name", "/"} {
		if !ValidJSONPointer(pointer) {
			t.Fatalf("%q must be a valid RFC 6901 pointer", pointer)
		}
	}
	for _, pointer := range []string{"a", "/a~2b", "/a~"} {
		if ValidJSONPointer(pointer) {
			t.Fatalf("%q must not be a valid RFC 6901 pointer", pointer)
		}
	}
}

func TestValidDecimalString(t *testing.T) {
	for _, value := range []string{"0", "-0", "250", "250.00", "-3.5", "0001"} {
		if !ValidDecimalString(value) {
			t.Fatalf("%q must be a valid decimal string", value)
		}
	}
	for _, value := range []string{"", ".5", "5.", "1e3", "0x10", " 1", "+1", "1,5"} {
		if ValidDecimalString(value) {
			t.Fatalf("%q must not be a valid decimal string", value)
		}
	}
}

func TestCaseTimingsFormattingOmitsZeroInstants(t *testing.T) {
	start := time.Date(2026, 1, 2, 3, 4, 5, 0, time.UTC)
	if got := FormatTime(&start); got != "2026-01-02T03:04:05Z" {
		t.Fatalf("unexpected formatting: %s", got)
	}
	if got := FormatTime(nil); got != "" {
		t.Fatalf("a nil time must be omitted, got %q", got)
	}
}
