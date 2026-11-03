package presetexec

import (
	"encoding/json"
	"errors"
	"strings"
	"testing"
)

// testDefinition decodes one definition JSON document into the sealed shape the
// executor reads.
func testDefinition(t *testing.T, raw string) *manifestDefinition {
	t.Helper()
	var definition manifestDefinition
	if err := json.Unmarshal([]byte(raw), &definition); err != nil {
		t.Fatalf("parse the test definition: %v", err)
	}
	return &definition
}

const linearDefinitionJSON = `{
  "dsl_version": "1.0",
  "input_schema": {"type":"object","properties":{"source":{"type":"string"}},
                   "required":["source"],"additionalProperties":false},
  "output_schema": {"type":"object","properties":{"report":{"type":"string"},"summary":{"type":"object"}},
                    "required":["report","summary"],"additionalProperties":false},
  "nodes": [
    {"id":"start","type":"start","inputs":{},"output_schema":{"type":"object"},"config":{}},
    {"id":"stats","type":"python",
     "inputs":{"file":{"ref":"node:start#/artifact"}},
     "output_schema":{"type":"object",
       "properties":{"total":{"type":"string"},"regions":{"type":"object","additionalProperties":{"type":"string"}},
                     "order_count":{"type":"integer"}},
       "required":["total","regions","order_count"],"additionalProperties":false},
     "config":{"tool_version":"` + PresetToolID + `"}},
    {"id":"end","type":"end",
     "inputs":{"summary":{"ref":"node:stats#"},"report":{"literal":"sales summary"}},
     "output_schema":{},"config":{}}
  ],
  "edges": [{"source":"start","target":"stats"},{"source":"stats","target":"end"}],
  "policy": {"max_active_seconds":900,"max_child_depth":3,"max_child_calls":12,
             "budget_limit":"1.00","currency":"USD"}
}`

func TestBuildLinearPlanAcceptsAStartPythonEndChain(t *testing.T) {
	plan, err := buildLinearPlan(testDefinition(t, linearDefinitionJSON))
	if err != nil {
		t.Fatalf("a legal chain was refused: %v", err)
	}
	if plan.start.ID != "start" || plan.python.ID != "stats" || plan.end.ID != "end" {
		t.Fatalf("unexpected plan: %+v", plan)
	}
	if len(plan.order) != 3 || plan.order[1] != plan.python {
		t.Fatalf("unexpected order: %+v", plan.order)
	}
}

// TestBuildLinearPlanRefusesNonLinearGraphs proves the closed graph vocabulary:
// each unsupported shape is a deterministic refusal with its own code (agent has
// its own code until T043).
func TestBuildLinearPlanRefusesNonLinearGraphs(t *testing.T) {
	cases := []struct {
		name string
		raw  string
		code string
	}{
		{
			name: "agent node",
			raw: `{"nodes":[
				{"id":"start","type":"start","config":{}},
				{"id":"agent","type":"agent","config":{}},
				{"id":"end","type":"end","config":{}}],
				"edges":[{"source":"start","target":"agent"},{"source":"agent","target":"end"}]}`,
			code: CodeAgentNodeUnsupported,
		},
		{
			name: "branch",
			raw: `{"nodes":[
				{"id":"start","type":"start","config":{}},
				{"id":"stats","type":"python","config":{"tool_version":"` + PresetToolID + `"}},
				{"id":"extra","type":"python","config":{"tool_version":"` + PresetToolID + `"}},
				{"id":"end","type":"end","config":{}}],
				"edges":[{"source":"start","target":"stats"},{"source":"start","target":"extra"}]}`,
			code: CodeToolUnsupportedGraph,
		},
		{
			name: "model node",
			raw: `{"nodes":[
				{"id":"start","type":"start","config":{}},
				{"id":"m","type":"model","config":{}},
				{"id":"end","type":"end","config":{}}],
				"edges":[{"source":"start","target":"m"},{"source":"m","target":"end"}]}`,
			code: CodeToolUnsupportedGraph,
		},
		{
			name: "two chain edges plus a shortcut",
			raw: `{"nodes":[
				{"id":"start","type":"start","config":{}},
				{"id":"stats","type":"python","config":{"tool_version":"` + PresetToolID + `"}},
				{"id":"end","type":"end","config":{}}],
				"edges":[{"source":"start","target":"stats"},{"source":"stats","target":"end"},{"source":"start","target":"end"}]}`,
			code: CodeToolUnsupportedGraph,
		},
		{
			name: "another tool",
			raw: `{"nodes":[
				{"id":"start","type":"start","config":{}},
				{"id":"stats","type":"python","config":{"tool_version":"99999999-9999-4999-8999-999999999999"}},
				{"id":"end","type":"end","config":{}}],
				"edges":[{"source":"start","target":"stats"},{"source":"stats","target":"end"}]}`,
			code: CodeToolUnsupportedTool,
		},
		{
			name: "no edges",
			raw: `{"nodes":[
				{"id":"start","type":"start","config":{}},
				{"id":"stats","type":"python","config":{"tool_version":"` + PresetToolID + `"}},
				{"id":"end","type":"end","config":{}}]}`,
			code: CodeToolUnsupportedGraph,
		},
	}
	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			_, err := buildLinearPlan(testDefinition(t, testCase.raw))
			if err == nil {
				t.Fatal("a non-linear definition was accepted")
			}
			var planErr *planError
			if !errors.As(err, &planErr) {
				t.Fatalf("want *planError, got %T: %v", err, err)
			}
			if planErr.code != testCase.code {
				t.Fatalf("code = %q, want %q", planErr.code, testCase.code)
			}
		})
	}
}

func TestResolveNodeBindingsFollowsInputAndNodePointers(t *testing.T) {
	node := &manifestNode{
		ID: "stats",
		Inputs: map[string]planBinding{
			"file":    {Ref: "node:start#/artifact"},
			"source":  {Ref: "input#/source"},
			"count":   {Ref: "node:start#/nested/count"},
			"literal": {Literal: json.RawMessage(`{"fixed":true}`)},
			"escaped": {Ref: "input#/a~1b"},
		},
	}
	available := map[string]any{
		"input": map[string]any{
			"source": "demo",
			"nested": map[string]any{"count": float64(2)},
			"a/b":    "escaped",
			"artifact": map[string]any{
				"artifact_id": "99999999-8888-4777-8666-555555555555",
				"media_type":  "text/csv",
			},
		},
		"start": map[string]any{
			"artifact": map[string]any{"artifact_id": "x"},
			"nested":   map[string]any{"count": float64(2)},
		},
	}
	resolved, err := resolveNodeBindings(node, available)
	if err != nil {
		t.Fatalf("a legal binding set was refused: %v", err)
	}
	if resolved["source"] != "demo" {
		t.Fatalf("source = %v", resolved["source"])
	}
	if resolved["count"] != float64(2) {
		t.Fatalf("count = %v", resolved["count"])
	}
	if resolved["escaped"] != "escaped" {
		t.Fatalf("escaped = %v", resolved["escaped"])
	}
	if resolved["literal"].(map[string]any)["fixed"] != true {
		t.Fatalf("literal = %v", resolved["literal"])
	}
}

func TestResolveNodeBindingsReportsLocatedFailures(t *testing.T) {
	available := map[string]any{
		"input": map[string]any{"source": "demo"},
		"start": map[string]any{"source": "demo"},
	}
	cases := []struct {
		name    string
		input   planBinding
		pointer string
	}{
		{"missing pointer", planBinding{Ref: "input#/missing"}, "stats.file"},
		{"unknown node", planBinding{Ref: "node:later#/x"}, "stats.file"},
		{"both literal and ref", planBinding{Ref: "input#/source", Literal: json.RawMessage(`1`)}, "stats.file"},
		{"invalid pointer escape", planBinding{Ref: "input#/a~2b"}, "stats.file"},
	}
	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			node := &manifestNode{ID: "stats", Inputs: map[string]planBinding{"file": testCase.input}}
			_, err := resolveNodeBindings(node, available)
			var located *locatedError
			if !errors.As(err, &located) {
				t.Fatalf("want *locatedError, got %T: %v", err, err)
			}
			if located.Pointer != testCase.pointer {
				t.Fatalf("pointer = %q, want %q", located.Pointer, testCase.pointer)
			}
		})
	}
}

// TestValidateSchemaEnforcesTheRestrictedSubset covers the structural checks the
// ticket's AC names: a wrong shape must be refused at a located pointer.
func TestValidateSchemaEnforcesTheRestrictedSubset(t *testing.T) {
	schema := json.RawMessage(`{
		"type":"object",
		"properties":{
			"total":{"type":"string"},
			"count":{"type":"integer"},
			"regions":{"type":"object","additionalProperties":{"type":"string"}}
		},
		"required":["total","count","regions"],
		"additionalProperties":false}`)
	valid := map[string]any{
		"total":   "250.00",
		"count":   float64(3),
		"regions": map[string]any{"east": "250.00"},
	}
	if err := validateSchema(schema, valid, "#/data"); err != nil {
		t.Fatalf("a valid document was refused: %v", err)
	}
	cases := []struct {
		name    string
		value   map[string]any
		pointer string
	}{
		{"missing required", map[string]any{"total": "1", "count": float64(1)}, "#/data/regions"},
		{"wrong type", map[string]any{"total": float64(1), "count": float64(1), "regions": map[string]any{}}, "#/data/total"},
		{"fractional integer", map[string]any{"total": "1", "count": float64(1.5), "regions": map[string]any{}}, "#/data/count"},
		{"additional property", map[string]any{"total": "1", "count": float64(1), "regions": map[string]any{}, "x": true}, "#/data/x"},
		{"additional property type", map[string]any{"total": "1", "count": float64(1), "regions": map[string]any{"east": float64(1)}}, "#/data/regions/east"},
	}
	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			err := validateSchema(schema, testCase.value, "#/data")
			if err == nil {
				t.Fatal("an invalid document was accepted")
			}
			var located *locatedError
			if !errors.As(err, &located) || located.Pointer != testCase.pointer {
				t.Fatalf("want located at %s, got %v", testCase.pointer, err)
			}
		})
	}
}

func TestDecodeJSONDocumentRejectsTrailingData(t *testing.T) {
	if _, err := decodeJSONDocument([]byte(`{"a":1} {"b":2}`)); err == nil {
		t.Fatal("trailing data was accepted")
	}
	value, err := decodeJSONDocument([]byte(`{"a":1}`))
	if err != nil {
		t.Fatalf("a single document was refused: %v", err)
	}
	if value.(map[string]any)["a"] != float64(1) {
		t.Fatalf("numbers were not normalised: %v", value)
	}
}

// TestPlanErrorsCarryStableCodes keeps the run's error_code vocabulary closed.
func TestPlanErrorsCarryStableCodes(t *testing.T) {
	_, err := buildLinearPlan(testDefinition(t, `{"nodes":[{"id":"start","type":"start","config":{}}]}`))
	var planErr *planError
	if !errors.As(err, &planErr) || planErr.code == "" {
		t.Fatalf("plan error has no stable code: %v", err)
	}
	if !strings.HasPrefix(planErr.code, "agent_node_") && !strings.HasPrefix(planErr.code, "tool_") {
		t.Fatalf("unexpected plan code: %q", planErr.code)
	}
}
