package catalog

// F07/T078: the spawn-time delegation re-check and the child I/O schema
// identity. These are the pure rules the children service and the result
// validator share; the real-stack behavioural proof lives in
// tests/f07/test_t078_child_runs.py.

import (
	"encoding/json"
	"testing"
)

const (
	t078ChildVersion = "55555555-5555-4555-8555-555555555555"
	t078OtherVersion = "66666666-6666-4666-8666-666666666666"
)

func t078Definition(t *testing.T, raw string) map[string]any {
	t.Helper()
	var definition map[string]any
	if err := json.Unmarshal([]byte(raw), &definition); err != nil {
		t.Fatalf("parse definition: %v", err)
	}
	return definition
}

const t078ParentDefinition = `{
  "dsl_version": "1.0",
  "input_schema": {"type":"object","properties":{"source":{"type":"string"}},"required":["source"]},
  "output_schema": {"type":"object","properties":{"echo":{"type":"string"}},"required":["echo"]},
  "nodes": [
    {"id":"start","type":"start","inputs":{},"output_schema":{"type":"object"},"config":{}},
    {"id":"child","type":"call_app",
     "inputs":{"source":{"ref":"input#/source"}},
     "output_schema":{"type":"object","properties":{"echo":{"type":"string"}},"required":["echo"]},
     "config":{"application_version":"` + t078ChildVersion + `"}},
    {"id":"end","type":"end","inputs":{"echo":{"ref":"node:child#/echo"}},
     "output_schema":{},"config":{}}
  ],
  "edges": [{"source":"start","target":"child"},{"source":"child","target":"end"}],
  "policy": {"max_child_calls":12,"max_child_depth":3,"budget_limit":"1.00","currency":"USD",
             "max_active_seconds":300}
}`

const t078ChildDefinition = `{
  "dsl_version": "1.0",
  "input_schema": {"type":"object","properties":{"source":{"type":"string"}},
                   "required":["source"],"additionalProperties":false},
  "output_schema": {"type":"object","properties":{"echo":{"type":"string"}},
                    "required":["echo"],"additionalProperties":false},
  "nodes": [
    {"id":"start","type":"start","inputs":{},"output_schema":{"type":"object"},"config":{}},
    {"id":"end","type":"end","inputs":{"echo":{"ref":"input#/source"}},"output_schema":{},"config":{}}
  ],
  "edges": [{"source":"start","target":"end"}],
  "policy": {"max_child_calls":12,"max_child_depth":3,"budget_limit":"1.00","currency":"USD",
             "max_active_seconds":300}
}`

func TestCheckChildSpawnReusesTheT075Gate(t *testing.T) {
	parent := t078Definition(t, t078ParentDefinition)
	child := t078Definition(t, t078ChildDefinition)

	mappings, located := CheckChildSpawn(parent, t078ChildVersion, child)
	if len(located) != 0 {
		t.Fatalf("a declared, compatible child was refused: %+v", located)
	}
	if len(mappings) != 1 || mappings[0].NodeID != "child" ||
		len(mappings[0].Fields) != 1 || mappings[0].Fields[0] != "source" {
		t.Fatalf("unexpected mappings: %+v", mappings)
	}

	// A version the frozen definition does not name is not callable.
	if _, located := CheckChildSpawn(parent, t078OtherVersion, child); len(located) == 0 ||
		located[0].Code != CodeChildNotInClosure {
		t.Fatalf("a version outside the frozen closure was accepted: %+v", located)
	}

	// T075's mapping rule still applies at spawn time: a field the child does
	// not declare is located at its exact pointer.
	undeclared := t078Definition(t, `{
	  "nodes":[
	    {"id":"start","type":"start","config":{}},
	    {"id":"child","type":"call_app",
	     "inputs":{"source":{"ref":"input#/source"},"region":{"literal":"eu"}},
	     "output_schema":{"type":"object"},
	     "config":{"application_version":"`+t078ChildVersion+`"}},
	    {"id":"end","type":"end","config":{}}],
	  "edges":[{"source":"start","target":"child"},{"source":"child","target":"end"}],
	  "policy":{"max_child_calls":12,"max_child_depth":3}}`)
	_, located = CheckChildSpawn(undeclared, t078ChildVersion, child)
	if len(located) == 0 || located[0].Code != CodeIOMappingUnknownField ||
		located[0].Pointer != "/nodes/1/inputs/region" {
		t.Fatalf("an undeclared mapped field was accepted: %+v", located)
	}
}

func TestChildSchemaIdentityAndValidation(t *testing.T) {
	child := t078Definition(t, t078ChildDefinition)
	inputHash, outputHash, ok := ChildSchemaHashes(child)
	if !ok || len(inputHash) != 64 || len(outputHash) != 64 || inputHash == outputHash {
		t.Fatalf("unexpected schema hashes: %q %q %v", inputHash, outputHash, ok)
	}
	againInput, againOutput, _ := ChildSchemaHashes(child)
	if inputHash != againInput || outputHash != againOutput {
		t.Fatal("the schema identity is not deterministic")
	}

	if located := ValidateChildInput(child, map[string]any{"source": "ok"}); len(located) != 0 {
		t.Fatalf("a valid mapped input was refused: %+v", located)
	}
	located := ValidateChildInput(child, map[string]any{"source": float64(42)})
	if len(located) != 1 || located[0].Code != CodeChildInputInvalid ||
		located[0].Pointer != "/input/source" {
		t.Fatalf("a wrong-typed input was accepted: %+v", located)
	}

	if located := ValidateChildResult(child, map[string]any{"echo": "ok"}); len(located) != 0 {
		t.Fatalf("a conforming child result was refused: %+v", located)
	}
	located = ValidateChildResult(child, map[string]any{"bogus": "x"})
	if len(located) == 0 || located[0].Code != CodeChildOutputInvalid {
		t.Fatalf("a non-conforming child result was accepted: %+v", located)
	}
}

func TestMatchChildInputMappingRequiresExactlyTheDeclaredFields(t *testing.T) {
	mappings := []ChildCallMapping{{NodeID: "child", Fields: []string{"source"}}}
	mapping, fields, ok := MatchChildInputMapping(mappings, map[string]any{"source": "x"})
	if !ok || mapping.NodeID != "child" || len(fields) != 1 {
		t.Fatalf("an exact mapping did not match: %+v %v %v", mapping, fields, ok)
	}
	if _, fields, ok := MatchChildInputMapping(mappings, map[string]any{
		"source": "x", "history": "must not cross",
	}); ok || len(fields) != 2 {
		t.Fatalf("an extra input field matched a declared mapping: %v %v", fields, ok)
	}
	if _, _, ok := MatchChildInputMapping(mappings, map[string]any{}); ok {
		t.Fatal("an empty input matched a mapping that requires source")
	}
}

// TestCheckChildSpawnAcceptsAnAgentSubappSubset pins the agent declaration path:
// an agent's allowed set is a subset mapping (the model chooses declared inputs),
// not an exact call_app mapping.
func TestCheckChildSpawnAcceptsAnAgentSubappSubset(t *testing.T) {
	parent := t078Definition(t, `{
	  "nodes":[
	    {"id":"start","type":"start","config":{}},
	    {"id":"analysis","type":"agent",
	     "output_schema":{"type":"object","properties":{"text":{"type":"string"}}},
	     "config":{"model_profile_version":"22222222-2222-4222-8222-222222222222",
	               "instructions":"go","max_turns":2,
	               "subapp_versions":["`+t078ChildVersion+`"]}},
	    {"id":"end","type":"end","config":{}}],
	  "edges":[{"source":"start","target":"analysis"},{"source":"analysis","target":"end"}],
	  "policy":{"max_child_calls":12,"max_child_depth":3}}`)
	child := t078Definition(t, t078ChildDefinition)
	mappings, located := CheckChildSpawn(parent, t078ChildVersion, child)
	if len(located) != 0 {
		t.Fatalf("an allowed agent sub-app was refused: %+v", located)
	}
	if len(mappings) != 1 || !mappings[0].Subset || mappings[0].NodeID != "analysis" ||
		len(mappings[0].Fields) != 1 || mappings[0].Fields[0] != "source" {
		t.Fatalf("unexpected agent mapping: %+v", mappings)
	}
	if _, _, ok := MatchChildInputMapping(mappings, map[string]any{"source": "x"}); !ok {
		t.Fatal("a declared agent input was not accepted")
	}
	if _, _, ok := MatchChildInputMapping(mappings, map[string]any{"history": "x"}); ok {
		t.Fatal("an undeclared agent input was accepted")
	}
}
