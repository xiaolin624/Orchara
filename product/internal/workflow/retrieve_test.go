package workflow

// F04B/T088: the publish validator accepts the retrieve definitions the canvas
// authors and the graph executor runs. The retrieve config has been in
// rules.json since the contract freeze; this test pins the surviving shape
// (stable KB ids + top_k, a bound string query) so the T088 canvas fixtures are
// not accidentally rejected before the executor can see them.

import (
	"encoding/json"
	"testing"

	"orchara/platform/internal/jcs"
)

func retrieveTestDefinition(t *testing.T, nodes, edges []map[string]any) map[string]any {
	t.Helper()
	document := map[string]any{
		"dsl_version": "1.0",
		"input_schema": map[string]any{
			"type": "object",
			"properties": map[string]any{
				"question": map[string]any{"type": "string"},
			},
			"required": []any{"question"},
		},
		"output_schema": map[string]any{"type": "object"},
		"nodes":         nodes,
		"edges":         edges,
		"policy": map[string]any{
			"max_active_seconds": 900, "max_child_depth": 0, "max_child_calls": 0,
			"budget_limit": "1.00", "currency": "USD",
		},
	}
	return document
}

func retrieveTestNode() map[string]any {
	return map[string]any{
		"id":   "search",
		"type": "retrieve",
		"inputs": map[string]any{
			"query": map[string]any{"ref": "input#/question"},
		},
		"config": map[string]any{
			"knowledge_base_ids": []any{"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"},
			"top_k":              3,
		},
		"output_schema": map[string]any{"type": "object"},
	}
}

// jsonRoundTrip decodes a hand-built fixture the way the HTTP layer does: JSON
// numbers become float64, which is the shape the validator speaks.
func jsonRoundTrip(t *testing.T, value any) map[string]any {
	t.Helper()
	raw, err := json.Marshal(value)
	if err != nil {
		t.Fatalf("encode the fixture: %v", err)
	}
	var decoded map[string]any
	if err := json.Unmarshal(raw, &decoded); err != nil {
		t.Fatalf("decode the fixture: %v", err)
	}
	return decoded
}

func TestValidateAcceptsTheRetrieveNode(t *testing.T) {
	document := jsonRoundTrip(t, retrieveTestDefinition(t,
		[]map[string]any{
			{"id": "start", "type": "start", "inputs": map[string]any{}, "config": map[string]any{},
				"output_schema": map[string]any{"type": "object"}},
			retrieveTestNode(),
			{"id": "end", "type": "end",
				"inputs": map[string]any{
					"citations": map[string]any{"ref": "node:search#/citations"},
					"count":     map[string]any{"ref": "node:search#/count"},
				},
				"config": map[string]any{}, "output_schema": map[string]any{"type": "object"}},
		},
		[]map[string]any{
			{"source": "start", "target": "search"},
			{"source": "search", "target": "end"},
		},
	))
	rules, err := DefaultRules()
	if err != nil {
		t.Fatalf("load the embedded rules: %v", err)
	}
	canonical, err := jcs.Canonicalize(document)
	if err != nil {
		t.Fatalf("canonicalize the retrieve definition: %v", err)
	}
	result, err := Validate(document, rules, ValidationOptions{
		InputSHA256: jcs.SHA256Hex([]byte(canonical)),
	})
	if err != nil {
		t.Fatalf("validate the retrieve definition: %v", err)
	}
	if result.Verdict() != "valid" {
		t.Fatalf("the retrieve definition is invalid: %+v", result.Problems)
	}
	node := findNode(t, result.Data, "search")
	config, _ := node["config"].(map[string]any)
	if config == nil || len(config) != 2 {
		t.Fatalf("the normal form lost the retrieve config: %v", node)
	}
}

func TestValidateRejectsABadRetrieveConfig(t *testing.T) {
	broken := retrieveTestNode()
	broken["config"] = map[string]any{
		"knowledge_base_ids": []any{}, // min_items: 1
		"top_k":              3,
	}
	document := jsonRoundTrip(t, retrieveTestDefinition(t,
		[]map[string]any{
			{"id": "start", "type": "start", "inputs": map[string]any{}, "config": map[string]any{},
				"output_schema": map[string]any{"type": "object"}},
			broken,
			{"id": "end", "type": "end", "inputs": map[string]any{}, "config": map[string]any{},
				"output_schema": map[string]any{"type": "object"}},
		},
		[]map[string]any{
			{"source": "start", "target": "search"},
			{"source": "search", "target": "end"},
		},
	))
	rules, err := DefaultRules()
	if err != nil {
		t.Fatalf("load the embedded rules: %v", err)
	}
	result, err := Validate(document, rules, ValidationOptions{})
	if err != nil {
		t.Fatalf("validate the broken definition: %v", err)
	}
	if result.Verdict() != "invalid" {
		t.Fatal("an empty knowledge_base_ids list must be refused before execution")
	}
	found := false
	for _, problem := range result.Problems {
		if problem.Code == "bad_config" {
			found = true
			break
		}
	}
	if !found {
		t.Fatalf("expected a bad_config problem, got %+v", result.Problems)
	}
}

// findNode returns the normalised node with the given id.
func findNode(t *testing.T, normal *NormalizedWorkflow, id string) map[string]any {
	t.Helper()
	if normal == nil {
		t.Fatal("the validator returned no normal form")
	}
	encoded, err := json.Marshal(normal.Document)
	if err != nil {
		t.Fatalf("encode the normal form: %v", err)
	}
	var document map[string]any
	if err := json.Unmarshal(encoded, &document); err != nil {
		t.Fatalf("decode the normal form: %v", err)
	}
	nodes, _ := document["nodes"].([]any)
	for _, item := range nodes {
		node, _ := item.(map[string]any)
		if node["id"] == id {
			return node
		}
	}
	t.Fatalf("the normal form has no node %q", id)
	return nil
}
