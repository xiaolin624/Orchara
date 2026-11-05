package catalog

import (
	"encoding/json"
	"testing"
)

// T075: fixed sub-application delegation. These are the pure rules the validate
// endpoint applies after dependency readiness and cycle detection: the I/O
// mapping against the child version's frozen definition and the child
// depth/count limits over the real version_dependencies closure.

const (
	testChildVersion = "55555555-5555-4555-8555-555555555555"
	testOtherVersion = "66666666-6666-4666-8666-666666666666"
)

// childRow builds the scan shape of one published version: the frozen manifest's
// definition object (input/output schemas), as application_versions.manifest ->
// 'definition' returns it.
func childRow(inputSchema, outputSchema map[string]any) subappDependency {
	definition := map[string]any{
		"dsl_version":   "1.0",
		"nodes":         []any{},
		"edges":         []any{},
		"input_schema":  inputSchema,
		"output_schema": outputSchema,
	}
	raw, err := json.Marshal(definition)
	if err != nil {
		panic(err)
	}
	return subappDependency{
		applicationID: "11111111-1111-4111-8111-111111111111",
		ownerID:       "22222222-2222-4222-8222-222222222222",
		definition:    raw,
	}
}

func objectSchema(properties map[string]any, required ...string) map[string]any {
	schema := map[string]any{"type": "object", "properties": properties}
	if len(required) > 0 {
		schema["required"] = stringsOf(required)
	}
	return schema
}

func stringsOf(values []string) []any {
	items := make([]any, 0, len(values))
	for _, value := range values {
		items = append(items, value)
	}
	return items
}

func literal(value any) map[string]any { return map[string]any{"literal": value} }
func ref(reference string) map[string]any {
	return map[string]any{"ref": reference}
}

// callAppDocument is one start -> call_app -> end definition. The parent input
// schema declares `source` as a string so a ref binding has a known type.
func callAppDocument(inputs map[string]any, outputSchema map[string]any) map[string]any {
	return map[string]any{
		"dsl_version": "1.0",
		"input_schema": objectSchema(map[string]any{
			"source": map[string]any{"type": "string"},
		}, "source"),
		"output_schema": map[string]any{"type": "object"},
		"nodes": []any{
			map[string]any{"id": "start", "type": "start", "inputs": map[string]any{},
				"output_schema": map[string]any{"type": "object"}, "config": map[string]any{}},
			map[string]any{"id": "child", "type": "call_app", "inputs": inputs,
				"output_schema": outputSchema,
				"config":        map[string]any{"application_version": testChildVersion}},
			map[string]any{"id": "end", "type": "end", "inputs": map[string]any{},
				"output_schema": map[string]any{"type": "object"}, "config": map[string]any{}},
		},
		"edges": []any{
			map[string]any{"source": "start", "target": "child"},
			map[string]any{"source": "child", "target": "end"},
		},
		"policy": map[string]any{
			"budget_limit": "1.00", "currency": "USD", "max_active_seconds": 300,
			"max_child_calls": 12, "max_child_depth": 3,
		},
	}
}

func childSchemas() (map[string]any, map[string]any) {
	input := objectSchema(map[string]any{
		"source": map[string]any{"type": "string"},
		"mode":   map[string]any{"type": "string"},
	}, "source")
	output := objectSchema(map[string]any{
		"report":  map[string]any{"type": "string"},
		"summary": map[string]any{"type": "object"},
	}, "report", "summary")
	return input, output
}

func TestCallAppMappingAcceptsACompatibleMapping(t *testing.T) {
	input, output := childSchemas()
	document := callAppDocument(
		map[string]any{"source": ref("input#/source")},
		map[string]any{"type": "object", "properties": map[string]any{
			"report": map[string]any{"type": "string"},
		}, "required": []any{"report"}},
	)
	located := checkCallAppMappings(document, map[string]subappDependency{
		testChildVersion: childRow(input, output),
	})
	if len(located) != 0 {
		t.Fatalf("a compatible mapping was refused: %+v", located)
	}
}

func TestCallAppMappingLocatesAnUnmappedRequiredChildInput(t *testing.T) {
	input, output := childSchemas()
	document := callAppDocument(map[string]any{}, map[string]any{"type": "object"})
	located := checkCallAppMappings(document, map[string]subappDependency{
		testChildVersion: childRow(input, output),
	})
	if len(located) != 1 {
		t.Fatalf("located = %+v, want exactly the missing required field", located)
	}
	error := located[0]
	if error.Code != CodeIOMappingMissingField || error.Pointer != "/nodes/1/inputs/source" ||
		error.NodeID != "child" {
		t.Fatalf("missing required input = %+v", error)
	}
}

func TestCallAppMappingLocatesAnUnknownMappedField(t *testing.T) {
	input, output := childSchemas()
	document := callAppDocument(map[string]any{
		"source": ref("input#/source"),
		"extra":  literal("not declared"),
	}, map[string]any{"type": "object"})
	located := checkCallAppMappings(document, map[string]subappDependency{
		testChildVersion: childRow(input, output),
	})
	if len(located) != 1 || located[0].Code != CodeIOMappingUnknownField ||
		located[0].Pointer != "/nodes/1/inputs/extra" || located[0].NodeID != "child" {
		t.Fatalf("unknown mapped field = %+v", located)
	}
}

func TestCallAppMappingLocatesLiteralAndRefTypeMismatches(t *testing.T) {
	input, output := childSchemas()
	cases := map[string]any{"source": literal(float64(42))}
	located := checkCallAppMappings(callAppDocument(cases, map[string]any{"type": "object"}),
		map[string]subappDependency{testChildVersion: childRow(input, output)})
	if len(located) != 1 || located[0].Code != CodeIOMappingTypeMismatch ||
		located[0].Pointer != "/nodes/1/inputs/source" {
		t.Fatalf("literal type mismatch = %+v", located)
	}

	// The parent input schema declares `source` as integer: the ref is a
	// statically impossible mapping even though both sides are well formed.
	document := callAppDocument(map[string]any{"source": ref("input#/source")},
		map[string]any{"type": "object"})
	document["input_schema"] = objectSchema(map[string]any{
		"source": map[string]any{"type": "integer"},
	}, "source")
	located = checkCallAppMappings(document,
		map[string]subappDependency{testChildVersion: childRow(input, output)})
	if len(located) != 1 || located[0].Code != CodeIOMappingTypeMismatch ||
		located[0].Pointer != "/nodes/1/inputs/source" {
		t.Fatalf("ref type mismatch = %+v", located)
	}
}

func TestCallAppMappingAcceptsAnExplicitOpenChildInputSchema(t *testing.T) {
	input, output := childSchemas()
	input["additionalProperties"] = true
	document := callAppDocument(map[string]any{
		"source": ref("input#/source"),
		"extra":  literal("explicitly accepted"),
	}, map[string]any{"type": "object"})
	located := checkCallAppMappings(document, map[string]subappDependency{
		testChildVersion: childRow(input, output),
	})
	if len(located) != 0 {
		t.Fatalf("an explicitly open child input schema refused an extra field: %+v", located)
	}
}

func TestCallAppOutputMustBeARefinementOfTheChildOutput(t *testing.T) {
	input, output := childSchemas()

	unknown := map[string]any{"type": "object", "properties": map[string]any{
		"bogus": map[string]any{"type": "string"},
	}}
	located := checkCallAppMappings(
		callAppDocument(map[string]any{"source": ref("input#/source")}, unknown),
		map[string]subappDependency{testChildVersion: childRow(input, output)})
	if len(located) != 1 || located[0].Code != CodeIOOutputUnknownField ||
		located[0].Pointer != "/nodes/1/output_schema/properties/bogus" {
		t.Fatalf("unknown declared output = %+v", located)
	}

	mismatch := map[string]any{"type": "object", "properties": map[string]any{
		"report": map[string]any{"type": "integer"},
	}}
	located = checkCallAppMappings(
		callAppDocument(map[string]any{"source": ref("input#/source")}, mismatch),
		map[string]subappDependency{testChildVersion: childRow(input, output)})
	if len(located) != 1 || located[0].Code != CodeIOOutputTypeMismatch ||
		located[0].Pointer != "/nodes/1/output_schema/properties/report" {
		t.Fatalf("output type mismatch = %+v", located)
	}
}

func TestCallAppMappingRefusesAVersionWithoutAFrozenDefinition(t *testing.T) {
	document := callAppDocument(map[string]any{}, map[string]any{"type": "object"})
	located := checkCallAppMappings(document, map[string]subappDependency{
		testChildVersion: {applicationID: "app"},
	})
	if len(located) != 1 || located[0].Code != CodeDependencyNotReady ||
		located[0].Pointer != "/nodes/1/config/application_version" {
		t.Fatalf("no frozen definition = %+v", located)
	}
}

func TestChildDepthIsMeasuredOverTheRealClosure(t *testing.T) {
	document := callAppDocument(map[string]any{}, map[string]any{"type": "object"})
	refs := collectDependencyRefs(document)
	edges := map[string][]string{testChildVersion: {testOtherVersion}}

	// The direct child plus one published generation: depth 2.
	document["policy"].(map[string]any)["max_child_depth"] = float64(1)
	located := checkChildLimits(document, refs, edges)
	if len(located) != 1 || located[0].Code != CodeChildDepthExceeded ||
		located[0].Pointer != "/nodes/1/config/application_version" || located[0].NodeID != "child" {
		t.Fatalf("depth 2 against max_child_depth=1 = %+v", located)
	}

	document["policy"].(map[string]any)["max_child_depth"] = float64(2)
	if located := checkChildLimits(document, refs, edges); len(located) != 0 {
		t.Fatalf("depth 2 against max_child_depth=2 = %+v", located)
	}
}

func TestChildCallCountIsTheDeclaredFixedReferenceCount(t *testing.T) {
	document := callAppDocument(map[string]any{}, map[string]any{"type": "object"})
	refs := collectDependencyRefs(document)
	document["policy"].(map[string]any)["max_child_calls"] = float64(0)
	located := checkChildLimits(document, refs, nil)
	if len(located) != 1 || located[0].Code != CodeChildCallsExceeded ||
		located[0].Pointer != "/policy/max_child_calls" {
		t.Fatalf("one child reference against max_child_calls=0 = %+v", located)
	}

	// An agent's allowed set counts each distinct version once, not once per
	// duplicate entry: the set is a declaration, not a call.
	agentDocument := map[string]any{
		"nodes": []any{
			map[string]any{"id": "agent", "type": "agent", "config": map[string]any{
				"subapp_versions": []any{testChildVersion, testChildVersion},
			}},
		},
		"policy": map[string]any{"max_child_calls": float64(1), "max_child_depth": float64(3)},
	}
	if located := checkChildLimits(agentDocument, collectDependencyRefs(agentDocument), nil); len(located) != 0 {
		t.Fatalf("a duplicated allowed-set entry was counted twice: %+v", located)
	}
}

func TestChildDepthTerminatesOnACyclicClosure(t *testing.T) {
	// checkSubappCycles rejects the cycle first, but the depth walk must not hang
	// or recurse forever if it is ever reached with cyclic edges.
	edges := map[string][]string{testChildVersion: {testOtherVersion}, testOtherVersion: {testChildVersion}}
	if depth := childClosureDepth(testChildVersion, edges); depth != 2 {
		t.Fatalf("cyclic closure depth = %d, want 2", depth)
	}
}
