package catalog

import (
	"encoding/json"
	"errors"
	"os"
	"strings"
	"testing"
)

func TestETagRoundTripsAndRejectsNonContractTokens(t *testing.T) {
	if got := FormatETag(7); got != `"7"` {
		t.Fatalf("FormatETag(7) = %q", got)
	}
	for _, raw := range []string{`"7"`, ` "7" `} {
		revision, ok := ParseETag(raw)
		if !ok || revision != 7 {
			t.Fatalf("ParseETag(%q) = %d, %v", raw, revision, ok)
		}
	}
	for _, raw := range []string{"", "7", `W/"7"`, `"0"`, `"-1"`, `"abc"`, `"7", "8"`, `*`, `"7`} {
		if _, ok := ParseETag(raw); ok {
			t.Fatalf("ParseETag(%q) accepted a non-contract token", raw)
		}
	}
}

func TestValidateApplicationNameTrimsAndBounds(t *testing.T) {
	name, err := ValidateApplicationName("  sales report  ")
	if err != nil || name != "sales report" {
		t.Fatalf("trim = %q, %v", name, err)
	}
	for _, value := range []string{"", "   ", strings.Repeat("x", MaxApplicationNameRunes+1)} {
		var validation *ValidationError
		if err := func() error { _, err := ValidateApplicationName(value); return err }(); !errors.As(err, &validation) {
			t.Fatalf("ValidateApplicationName(%q) did not return a ValidationError: %v", value, err)
		}
	}
	if _, err := ValidateApplicationName(strings.Repeat("x", MaxApplicationNameRunes)); err != nil {
		t.Fatalf("the limit itself must pass: %v", err)
	}
}

func draftInput(definition, layout, card string) DraftInput {
	return DraftInput{
		Definition:     json.RawMessage(definition),
		Layout:         json.RawMessage(layout),
		CapabilityCard: json.RawMessage(card),
	}
}

func requireValidation(t *testing.T, input DraftInput, field string) {
	t.Helper()
	var validation *ValidationError
	if err := ValidateDraftInput(input); !errors.As(err, &validation) {
		t.Fatalf("expected a ValidationError, got %v", err)
	}
	if validation.Field != field {
		t.Fatalf("field = %q, want %q", validation.Field, field)
	}
}

func TestValidateDraftInputAcceptsTheEmptyCardAndTheFullCard(t *testing.T) {
	if err := ValidateDraftInput(draftInput(`{}`, `{}`, `{}`)); err != nil {
		t.Fatalf("an empty draft must be valid: %v", err)
	}
	full := `{"purpose":"summarise","includes":[],"excludes":[],"required_inputs":[],"expected_outputs":[],"examples":["a","b"]}`
	if err := ValidateDraftInput(draftInput(`{"dsl_version":"1.0"}`, `{"nodes":[]}`, full)); err != nil {
		t.Fatalf("a full card must be valid: %v", err)
	}
}

func TestValidateDraftInputRefusesPartialOrPaddedCards(t *testing.T) {
	requireValidation(t, draftInput(`{}`, `{}`, `{"purpose":"only a purpose"}`), "capability_card.includes")
	requireValidation(t, draftInput(`{}`, `{}`, `{"purpose":"p","includes":[],"excludes":[],"required_inputs":[],"expected_outputs":[],"examples":["one"]}`), "capability_card.examples")
	requireValidation(t, draftInput(`{}`, `{}`, `{"purpose":"p","includes":[],"excludes":[],"required_inputs":[],"expected_outputs":[],"examples":["a","b"],"extra":1}`), "capability_card.extra")
	requireValidation(t, draftInput(`{}`, `{}`, `{"purpose":null,"includes":[],"excludes":[],"required_inputs":[],"expected_outputs":[],"examples":["a","b"]}`), "capability_card.purpose")
}

func TestValidateDraftInputRefusesNonObjectsAndUnknownTopLevelKeys(t *testing.T) {
	requireValidation(t, draftInput(`[]`, `{}`, `{}`), "definition")
	requireValidation(t, draftInput(`null`, `{}`, `{}`), "definition")
	requireValidation(t, draftInput(`{}`, `null`, `{}`), "layout")
	requireValidation(t, draftInput(`{}`, `{}`, `null`), "capability_card")
	input := draftInput(`{}`, `{}`, `{}`)
	input.UnknownFields = []string{"visibility"}
	requireValidation(t, input, "visibility")
}

func TestValidateDraftInputEnforcesSizeCaps(t *testing.T) {
	big := `{"pad":"` + strings.Repeat("x", MaxDefinitionBytes) + `"}`
	requireValidation(t, draftInput(big, `{}`, `{}`), "definition")
}

// p05Case returns the input of one named case in the shared P05 corpus, so the
// CheckExecutable tests use the same documents the validator was pinned against
// instead of hand-written approximations.
func p05Case(t *testing.T, name string) map[string]any {
	t.Helper()
	raw, err := os.ReadFile("../../probes/p05_dsl/corpus.json")
	if err != nil {
		t.Fatalf("read the P05 corpus: %v", err)
	}
	var corpus struct {
		Cases []struct {
			Name  string         `json:"name"`
			Input map[string]any `json:"input"`
		} `json:"cases"`
	}
	if err := json.Unmarshal(raw, &corpus); err != nil {
		t.Fatalf("parse the P05 corpus: %v", err)
	}
	for _, entry := range corpus.Cases {
		if entry.Name == name {
			return entry.Input
		}
	}
	t.Fatalf("the P05 corpus has no case %q", name)
	return nil
}

func TestCheckExecutableLocatesACycleAndAcceptsAValidWorkflow(t *testing.T) {
	cycle := p05Case(t, "cycle")
	encoded, err := json.Marshal(cycle)
	if err != nil {
		t.Fatalf("encode the cycle case: %v", err)
	}
	located, err := CheckExecutable(Draft{Definition: encoded})
	if err != nil {
		t.Fatalf("CheckExecutable: %v", err)
	}
	if len(located) == 0 {
		t.Fatal("the cyclic definition was reported executable")
	}
	if located[0].Code != "cycle" {
		t.Fatalf("first problem = %+v, want code cycle", located[0])
	}
	if located[0].Message == "" {
		t.Fatal("the located error has no message")
	}

	valid := p05Case(t, "anchor-minimal-linear")
	encoded, err = json.Marshal(valid)
	if err != nil {
		t.Fatalf("encode the valid case: %v", err)
	}
	located, err = CheckExecutable(Draft{Definition: encoded})
	if err != nil {
		t.Fatalf("CheckExecutable: %v", err)
	}
	if len(located) != 0 {
		t.Fatalf("the valid workflow was rejected: %+v", located)
	}
}

func TestCheckExecutableResolvesANodePointerToItsID(t *testing.T) {
	document := map[string]any{
		"nodes": []any{
			map[string]any{"id": "start", "type": "start"},
			map[string]any{"id": "stats", "type": "python"},
		},
		"edges": []any{
			map[string]any{"source": "start", "target": "stats"},
		},
	}
	if got := nodeIDAt(document, "/nodes/1/config/tool_version"); got != "stats" {
		t.Fatalf("nodeIDAt = %q, want stats", got)
	}
	// F04B/T083: an edge-level error carries the edge's source node so the canvas
	// can highlight both the edge (pointer /edges/<i>) and a node panel.
	if got := nodeIDAt(document, "/edges/0"); got != "start" {
		t.Fatalf("nodeIDAt(/edges/0) = %q, want the edge source", got)
	}
	if got := nodeIDAt(document, "/edges/0/port"); got != "start" {
		t.Fatalf("nodeIDAt(/edges/0/port) = %q, want the edge source", got)
	}
	for _, pointer := range []string{
		"", "/nodes", "/nodes/9/value", "/policy/x", "/nodes/x/value",
		"/edges/9", "/edges/x", "/edges",
	} {
		if got := nodeIDAt(document, pointer); got != "" {
			t.Fatalf("nodeIDAt(%q) = %q, want empty", pointer, got)
		}
	}
}

func TestCheckExecutableRejectsAnEmptyOrNonObjectDefinition(t *testing.T) {
	for _, definition := range []string{"", "null", "[]", "not json"} {
		located, err := CheckExecutable(Draft{Definition: json.RawMessage(definition)})
		if err != nil {
			t.Fatalf("CheckExecutable(%q): %v", definition, err)
		}
		if len(located) != 1 || located[0].Code != "bad_document" {
			t.Fatalf("CheckExecutable(%q) = %+v, want one bad_document", definition, located)
		}
	}
}
