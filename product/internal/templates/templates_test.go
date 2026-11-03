package templates

import (
	"encoding/json"
	"strings"
	"testing"
)

// The sales definition is the one thing T050 and the seed command both depend
// on, so its shape is pinned here as well as by the real-stack test.
func TestSalesDefinitionPinsTheActualIDsAndLinearChain(t *testing.T) {
	const (
		tool  = "11111111-1111-4111-8111-111111111111"
		model = "33333333-3333-4333-8333-333333333333"
	)
	definition := SalesDefinition(tool, model)

	nodes := make(map[string]map[string]any, 4)
	rawNodes, ok := definition["nodes"].([]any)
	if !ok || len(rawNodes) != 4 {
		t.Fatalf("nodes = %#v", definition["nodes"])
	}
	order := make([]string, 0, 4)
	for _, raw := range rawNodes {
		node := raw.(map[string]any)
		id := node["id"].(string)
		order = append(order, id)
		nodes[id] = node
	}
	if strings.Join(order, ">") != "start>stats>explain>end" {
		t.Fatalf("node order = %v", order)
	}

	stats := nodes["stats"]
	config := stats["config"].(map[string]any)
	if config["tool_version"] != tool {
		t.Fatalf("stats tool_version = %v", config["tool_version"])
	}
	binding := stats["inputs"].(map[string]any)["file"].(map[string]any)
	if binding["ref"] != "node:start#/artifact" {
		t.Fatalf("stats file binding = %v", binding)
	}

	explainConfig := nodes["explain"]["config"].(map[string]any)
	if explainConfig["model_profile_version"] != model {
		t.Fatalf("explain model = %v", explainConfig["model_profile_version"])
	}
	if tools := explainConfig["tool_versions"].([]any); len(tools) != 0 {
		t.Fatalf("explain tools = %v (the report may only explain, never re-call the tool)", tools)
	}
	if explainConfig["max_turns"] != 4 {
		t.Fatalf("explain max_turns = %v", explainConfig["max_turns"])
	}

	endInputs := nodes["end"]["inputs"].(map[string]any)
	for name, want := range map[string]string{
		"summary": "node:stats#",
		"report":  "node:explain#/report",
		"totals":  "node:explain#/totals",
	} {
		ref := endInputs[name].(map[string]any)["ref"]
		if ref != want {
			t.Fatalf("end input %s = %v, want %v", name, ref, want)
		}
	}

	// The stored bytes must never carry the fixture placeholder model id.
	encoded, err := json.Marshal(definition)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(encoded), "22222222-2222-4222-8222-222222222222") {
		t.Fatal("the definition still contains the fixture model placeholder")
	}
}

func TestCapabilityCardHasTheValidatedMinimum(t *testing.T) {
	card := CapabilityCard()
	examples, ok := card["examples"].([]any)
	if !ok || len(examples) < 2 {
		t.Fatalf("capability_card.examples = %#v; the compiler requires at least 2", card["examples"])
	}
	if purpose, _ := card["purpose"].(string); strings.TrimSpace(purpose) == "" {
		t.Fatal("capability_card.purpose is empty")
	}
}
