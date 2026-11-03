package presetexec

import (
	"encoding/json"
	"errors"
	"strings"
	"testing"
)

// testDefinition decodes one definition JSON document into the sealed shape the
// executor reads. Agent plan tests need the manifest's fixed sets, so the helper
// supplies the preset tool and one model profile version.
func testDefinition(t *testing.T, raw string) *manifestDocument {
	t.Helper()
	var definition manifestDefinition
	if err := json.Unmarshal([]byte(raw), &definition); err != nil {
		t.Fatalf("parse the test definition: %v", err)
	}
	return &manifestDocument{
		Definition: definition,
		ToolArtifacts: []manifestToolArtifact{{
			ID: PresetToolID, ImageDigest: "sha256:" + strings.Repeat("aa", 32),
		}},
		ModelProfileVersions: []string{testModelProfileID},
	}
}

const testModelProfileID = "22222222-2222-4222-8222-222222222222"

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

func TestBuildLinearPlanAcceptsAStartPythonAgentEndChain(t *testing.T) {
	plan, err := buildLinearPlan(testDefinition(t, agentDefinitionJSON))
	if err != nil {
		t.Fatalf("a legal agent chain was refused: %v", err)
	}
	if plan.agent == nil || plan.agent.ID != "analysis" {
		t.Fatalf("agent node missing from the plan: %+v", plan)
	}
	if len(plan.order) != 4 || plan.order[2] != plan.agent || plan.order[3] != plan.end {
		t.Fatalf("unexpected order: %+v", plan.order)
	}
	config := plan.agentConfig
	if config == nil || config.ModelProfileVersion != testModelProfileID ||
		config.MaxTurns != 3 || len(config.ToolVersions) != 1 {
		t.Fatalf("agent config was not parsed: %+v", config)
	}
}

const agentDefinitionJSON = `{
  "dsl_version": "1.0",
  "input_schema": {"type":"object","properties":{"source":{"type":"string"}},
                   "required":["source"],"additionalProperties":false},
  "output_schema": {"type":"object","properties":{"text":{"type":"string"}},
                    "required":["text"],"additionalProperties":false},
  "nodes": [
    {"id":"start","type":"start","inputs":{},"output_schema":{"type":"object"},"config":{}},
    {"id":"stats","type":"python",
     "inputs":{"file":{"ref":"node:start#/artifact"}},
     "output_schema":{"type":"object","properties":{"total":{"type":"string"}},
                      "required":["total"]},
     "config":{"tool_version":"` + PresetToolID + `"}},
    {"id":"analysis","type":"agent",
     "inputs":{"stats":{"ref":"node:stats#"}},
     "output_schema":{"type":"object","properties":{"text":{"type":"string"}},
                      "required":["text"],"additionalProperties":false},
     "config":{"model_profile_version":"` + testModelProfileID + `",
               "instructions":"Summarise the statistics.",
               "max_turns":3,
               "tool_versions":["` + PresetToolID + `"],
               "subapp_versions":[]}},
    {"id":"end","type":"end",
     "inputs":{"text":{"ref":"node:analysis#/text"}},
     "output_schema":{},"config":{}}
  ],
  "edges": [{"source":"start","target":"stats"},{"source":"stats","target":"analysis"},
            {"source":"analysis","target":"end"}]
}`

// TestBuildLinearPlanRefusesUnsafeAgentConfigs pins the closed agent vocabulary:
// every unsafe config is refused before the first model call with a stable code.
func TestBuildLinearPlanRefusesUnsafeAgentConfigs(t *testing.T) {
	node := func(config string) string {
		return `{"nodes":[
			{"id":"start","type":"start","config":{}},
			{"id":"stats","type":"python","inputs":{"file":{"ref":"node:start#/artifact"}},
			 "config":{"tool_version":"` + PresetToolID + `"}},
			{"id":"analysis","type":"agent",
			 "inputs":{"stats":{"ref":"node:stats#"}},
			 "output_schema":{"type":"object","properties":{"text":{"type":"string"}}},
			 "config":` + config + `},
			{"id":"end","type":"end","config":{}}],
			"edges":[{"source":"start","target":"stats"},{"source":"stats","target":"analysis"},
			         {"source":"analysis","target":"end"}]}`
	}
	valid := `{"model_profile_version":"` + testModelProfileID + `",
	           "instructions":"go","max_turns":3,
	           "tool_versions":[], "subapp_versions":[]}`
	cases := []struct {
		name   string
		config string
		code   string
	}{
		{"missing model", `{"instructions":"go","max_turns":3}`, CodeAgentConfigInvalid},
		{"missing instructions", `{"model_profile_version":"` + testModelProfileID + `","max_turns":3}`, CodeAgentConfigInvalid},
		{"zero turns", `{"model_profile_version":"` + testModelProfileID + `","instructions":"go","max_turns":0}`, CodeAgentConfigInvalid},
		{"too many turns", `{"model_profile_version":"` + testModelProfileID + `","instructions":"go","max_turns":51}`, CodeAgentConfigInvalid},
		{"subapps", `{"model_profile_version":"` + testModelProfileID + `","instructions":"go","max_turns":1,
		              "subapp_versions":["33333333-3333-4333-8333-333333333333"]}`, CodeAgentSubappUnsupported},
		{"unknown tool", `{"model_profile_version":"` + testModelProfileID + `","instructions":"go","max_turns":1,
		              "tool_versions":["33333333-3333-4333-8333-333333333333"]}`, CodeToolUnsupportedTool},
	}
	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			_, err := buildLinearPlan(testDefinition(t, node(testCase.config)))
			var planErr *planError
			if !errors.As(err, &planErr) {
				t.Fatalf("want *planError, got %T: %v", err, err)
			}
			if planErr.code != testCase.code {
				t.Fatalf("code = %q, want %q", planErr.code, testCase.code)
			}
		})
	}
	// The same node with a legal config is accepted, proving the matrix is not
	// passing because everything is refused.
	if _, err := buildLinearPlan(testDefinition(t, node(valid))); err != nil {
		t.Fatalf("a valid config was refused: %v", err)
	}
}

// TestBuildLinearPlanRefusesAgentChainShapes pins the four-node shape.
func TestBuildLinearPlanRefusesAgentChainShapes(t *testing.T) {
	twoAgents := `{"nodes":[
		{"id":"start","type":"start","config":{}},
		{"id":"stats","type":"python","config":{"tool_version":"` + PresetToolID + `"}},
		{"id":"a","type":"agent","config":{}},
		{"id":"b","type":"agent","config":{}},
		{"id":"end","type":"end","config":{}}],
		"edges":[{"source":"start","target":"stats"},{"source":"stats","target":"a"},
		         {"source":"a","target":"b"},{"source":"b","target":"end"}]}`
	if _, err := buildLinearPlan(testDefinition(t, twoAgents)); err == nil {
		t.Fatal("two agent nodes were accepted")
	}
	badEdges := strings.Replace(agentDefinitionJSON,
		`{"source":"stats","target":"analysis"},`,
		`{"source":"stats","target":"analysis"},{"source":"start","target":"end"},`, 1)
	if _, err := buildLinearPlan(testDefinition(t, badEdges)); err == nil {
		t.Fatal("a shortcut edge was accepted")
	}
}

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
			name: "agent without a python node",
			raw: `{"nodes":[
				{"id":"start","type":"start","config":{}},
				{"id":"agent","type":"agent","config":{}},
				{"id":"end","type":"end","config":{}}],
				"edges":[{"source":"start","target":"agent"},{"source":"agent","target":"end"}]}`,
			code: CodeToolUnsupportedGraph,
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
	if !strings.HasPrefix(planErr.code, "agent_") && !strings.HasPrefix(planErr.code, "tool_") {
		t.Fatalf("unexpected plan code: %q", planErr.code)
	}
}
