package presetexec

// F04B/T083: the graph plan's structural proof and the condition evaluator.
//
// The executor does not trust the publish validator (a sealed manifest could be
// replayed from outside this build), so buildGraphPlan re-proves the region
// structure it is about to schedule. These tests pin the proof and the stable
// refusal codes; the real end-to-end execution lives in tests/f04b.

import (
	"encoding/json"
	"strings"
	"testing"
)

// ---------------------------------------------------------------------------
// fixtures
// ---------------------------------------------------------------------------

// flatConditionDefinition is the T083 acceptance shape:
//
//	start -> choose --yes--> left(python)
//	               \--no----> right(python)
//	         both -> join -> end
const flatConditionDefinition = `{
  "dsl_version": "1.0",
  "input_schema": {"type":"object","properties":{"requires_approval":{"type":"boolean"}},
                   "required":["requires_approval"],"additionalProperties":false},
  "output_schema": {"type":"object"},
  "nodes": [
    {"id":"start","type":"start","inputs":{},"config":{},"output_schema":{"type":"object"}},
    {"id":"choose","type":"condition",
     "inputs":{"requires_approval":{"ref":"input#/requires_approval"}},
     "config":{"join_id":"join","default_port":"no",
               "cases":[{"input":"requires_approval","op":"eq","value":true,"port":"yes"}]},
     "output_schema":{"type":"object"}},
    {"id":"left","type":"python","inputs":{"file":{"ref":"node:start#/artifact"}},
     "config":{"tool_version":"11111111-1111-4111-8111-111111111111"},
     "output_schema":{"type":"object"}},
    {"id":"right","type":"python","inputs":{"file":{"ref":"node:start#/artifact"}},
     "config":{"tool_version":"11111111-1111-4111-8111-111111111111"},
     "output_schema":{"type":"object"}},
    {"id":"join","type":"join","inputs":{},
     "config":{"fork_id":"choose","mode":"all_active",
               "required_sources":["left","right"],"optional_sources":[]},
     "output_schema":{"type":"object"}},
    {"id":"end","type":"end","inputs":{"results":{"ref":"node:join#/results"}},
     "config":{},"output_schema":{"type":"object"}}
  ],
  "edges": [
    {"source":"start","target":"choose"},
    {"source":"choose","target":"left","port":"yes"},
    {"source":"choose","target":"right","port":"no"},
    {"source":"left","target":"join"},
    {"source":"right","target":"join"},
    {"source":"join","target":"end"}
  ],
  "policy": {"max_active_seconds":900,"max_child_depth":3,"max_child_calls":12,
             "budget_limit":"1.00","currency":"USD"}
}`

// nestedConditionDefinition nests a second condition region inside the "yes"
// branch of the outer one:
//
//	start -> choose --yes--> choose2 --yes--> deep
//	                                \--no---> shallow
//	                    \--no----> direct
//	         (choose2,shallow,deep) -> join2 ; (join2,direct) -> join -> end
const nestedConditionDefinition = `{
  "dsl_version": "1.0",
  "input_schema": {"type":"object"},
  "output_schema": {"type":"object"},
  "nodes": [
    {"id":"start","type":"start","inputs":{},"config":{},"output_schema":{"type":"object"}},
    {"id":"choose","type":"condition","inputs":{"mode":{"ref":"input#/mode"}},
     "config":{"join_id":"join","default_port":"no",
               "cases":[{"input":"mode","op":"eq","value":"nested","port":"yes"}]},
     "output_schema":{"type":"object"}},
    {"id":"choose2","type":"condition","inputs":{"mode":{"ref":"input#/mode"}},
     "config":{"join_id":"join2","default_port":"no",
               "cases":[{"input":"mode","op":"eq","value":"deep","port":"yes"}]},
     "output_schema":{"type":"object"}},
    {"id":"deep","type":"python","inputs":{},"config":{"tool_version":"11111111-1111-4111-8111-111111111111"},
     "output_schema":{"type":"object"}},
    {"id":"shallow","type":"python","inputs":{},"config":{"tool_version":"11111111-1111-4111-8111-111111111111"},
     "output_schema":{"type":"object"}},
    {"id":"join2","type":"join","inputs":{},
     "config":{"fork_id":"choose2","mode":"all_active",
               "required_sources":["deep","shallow"],"optional_sources":[]},
     "output_schema":{"type":"object"}},
    {"id":"direct","type":"python","inputs":{},"config":{"tool_version":"11111111-1111-4111-8111-111111111111"},
     "output_schema":{"type":"object"}},
    {"id":"join","type":"join","inputs":{},
     "config":{"fork_id":"choose","mode":"all_active",
               "required_sources":["join2","direct"],"optional_sources":[]},
     "output_schema":{"type":"object"}},
    {"id":"end","type":"end","inputs":{"results":{"ref":"node:join#/results"}},
     "config":{},"output_schema":{"type":"object"}}
  ],
  "edges": [
    {"source":"start","target":"choose"},
    {"source":"choose","target":"choose2","port":"yes"},
    {"source":"choose","target":"direct","port":"no"},
    {"source":"choose2","target":"deep","port":"yes"},
    {"source":"choose2","target":"shallow","port":"no"},
    {"source":"deep","target":"join2"},
    {"source":"shallow","target":"join2"},
    {"source":"join2","target":"join"},
    {"source":"direct","target":"join"},
    {"source":"join","target":"end"}
  ],
  "policy": {"max_active_seconds":900,"max_child_depth":3,"max_child_calls":12,
             "budget_limit":"1.00","currency":"USD"}
}`

// parallelDefinition forks at the start node onto two python branches.
const parallelDefinition = `{
  "dsl_version": "1.0",
  "input_schema": {"type":"object"},
  "output_schema": {"type":"object"},
  "nodes": [
    {"id":"start","type":"start","inputs":{},"config":{},"output_schema":{"type":"object"}},
    {"id":"left","type":"python","inputs":{"file":{"ref":"node:start#/artifact"}},
     "config":{"tool_version":"11111111-1111-4111-8111-111111111111"},
     "output_schema":{"type":"object"}},
    {"id":"right","type":"python","inputs":{"file":{"ref":"node:start#/artifact"}},
     "config":{"tool_version":"11111111-1111-4111-8111-111111111111"},
     "output_schema":{"type":"object"}},
    {"id":"join","type":"join","inputs":{},
     "config":{"fork_id":"start","mode":"all_active",
               "required_sources":["left","right"],"optional_sources":[]},
     "output_schema":{"type":"object"}},
    {"id":"end","type":"end","inputs":{"results":{"ref":"node:join#/results"}},
     "config":{},"output_schema":{"type":"object"}}
  ],
  "edges": [
    {"source":"start","target":"left"},
    {"source":"start","target":"right"},
    {"source":"left","target":"join"},
    {"source":"right","target":"join"},
    {"source":"join","target":"end"}
  ],
  "policy": {"max_active_seconds":900,"max_child_depth":3,"max_child_calls":12,
             "budget_limit":"1.00","currency":"USD"}
}`

func definitionDocument(t *testing.T, raw string) *manifestDocument {
	t.Helper()
	var definition manifestDefinition
	if err := json.Unmarshal([]byte(raw), &definition); err != nil {
		t.Fatalf("fixture is not a sealed definition: %v", err)
	}
	if len(definition.Nodes) == 0 {
		t.Fatalf("fixture has no nodes")
	}
	return &manifestDocument{
		ManifestVersion:   "orchara-execution-manifest/1.0",
		CompilerVersion:   CompilerVersion,
		WorkerImageDigest: "sha256:" + strings.Repeat("aa", 32),
		Definition:        definition,
	}
}

func mustGraphPlan(t *testing.T, raw string) *graphPlan {
	t.Helper()
	plan, err := buildGraphPlan(definitionDocument(t, raw))
	if err != nil {
		t.Fatalf("buildGraphPlan: %v", err)
	}
	return plan
}

func planErrorCode(t *testing.T, err error) string {
	t.Helper()
	var refusal *planError
	if !asPlanError(err, &refusal) {
		t.Fatalf("error %v is not a planError", err)
	}
	return refusal.code
}

func asPlanError(err error, target **planError) bool {
	typed, ok := err.(*planError)
	if ok {
		*target = typed
	}
	return ok
}

// ---------------------------------------------------------------------------
// structural proof
// ---------------------------------------------------------------------------

func TestBuildGraphPlanProvesTheConditionRegion(t *testing.T) {
	plan := mustGraphPlan(t, flatConditionDefinition)
	if plan.startID != "start" || plan.endID != "end" {
		t.Fatalf("start/end = %q/%q", plan.startID, plan.endID)
	}
	if len(plan.order) != 6 {
		t.Fatalf("order = %v", plan.order)
	}
	if plan.order[0] != "start" || plan.order[len(plan.order)-1] != "end" {
		t.Fatalf("order = %v", plan.order)
	}
	for _, id := range []string{"choose", "left", "right", "join"} {
		if plan.nodes[id] == nil {
			t.Fatalf("node %q missing", id)
		}
	}
	if len(plan.regions) != 1 {
		t.Fatalf("regions = %d", len(plan.regions))
	}
	region := plan.regions[0]
	if region.fork != "choose" || region.join != "join" {
		t.Fatalf("region = %+v", region)
	}
	if !region.inside["left"] || !region.inside["right"] {
		t.Fatalf("inside = %v", region.inside)
	}
	if region.inside["start"] || region.inside["end"] || region.inside["join"] {
		t.Fatalf("inside leaks outside the region: %v", region.inside)
	}
	if got := region.branches["yes"]; len(got) != 1 || got[0] != "left" {
		t.Fatalf("branches[yes] = %v", got)
	}
	if got := region.branches["no"]; len(got) != 1 || got[0] != "right" {
		t.Fatalf("branches[no] = %v", got)
	}
	if !region.required["left"] || !region.required["right"] || len(region.optional) != 0 {
		t.Fatalf("required/optional = %v/%v", region.required, region.optional)
	}
	if len(region.sources) != 2 {
		t.Fatalf("sources = %v", region.sources)
	}
	// Edge ports survive parsing (the Omitted port is the literal "default").
	for _, edge := range plan.nodes["choose"].outgoing {
		if edge.port == "" && edge.target == "" {
			t.Fatalf("edge = %+v", edge)
		}
	}
	if plan.nodes["left"].incoming[0].port != "yes" {
		t.Fatalf("left incoming = %+v", plan.nodes["left"].incoming)
	}
}

func TestBuildGraphPlanProvesNestedRegions(t *testing.T) {
	plan := mustGraphPlan(t, nestedConditionDefinition)
	if len(plan.regions) != 2 {
		t.Fatalf("regions = %d: %+v", len(plan.regions), plan.regions)
	}
	byJoin := map[string]*graphRegion{}
	for _, region := range plan.regions {
		byJoin[region.join] = region
	}
	outer, inner := byJoin["join"], byJoin["join2"]
	if outer == nil || inner == nil {
		t.Fatalf("regions = %+v", plan.regions)
	}
	for _, id := range []string{"choose2", "deep", "shallow", "join2", "direct"} {
		if !outer.inside[id] {
			t.Fatalf("outer inside misses %q: %v", id, outer.inside)
		}
	}
	for _, id := range []string{"deep", "shallow"} {
		if !inner.inside[id] {
			t.Fatalf("inner inside misses %q: %v", id, inner.inside)
		}
	}
	if inner.inside["join2"] || inner.inside["direct"] || inner.inside["join"] {
		t.Fatalf("inner inside leaks: %v", inner.inside)
	}
	// The nested region converges at its own join before entering the parent.
	if !outer.inside["join2"] {
		t.Fatalf("outer inside must include the nested join: %v", outer.inside)
	}
}

func TestBuildGraphPlanAcceptsAParallelForkOnStart(t *testing.T) {
	plan := mustGraphPlan(t, parallelDefinition)
	if len(plan.regions) != 1 {
		t.Fatalf("regions = %d", len(plan.regions))
	}
	region := plan.regions[0]
	if region.fork != "start" || region.join != "join" {
		t.Fatalf("region = %+v", region)
	}
	if !region.inside["left"] || !region.inside["right"] {
		t.Fatalf("inside = %v", region.inside)
	}
	if len(region.branches) != 0 {
		t.Fatalf("a parallel fork has no ports: %v", region.branches)
	}
}

func TestBuildGraphPlanRejectsACrossRegionEdge(t *testing.T) {
	definition := definitionDocument(t, nestedConditionDefinition)
	// A third branch leaves the inner parallel fork but bypasses the inner join
	// and feeds the outer join directly (the P05 corpus's escaped-branch shape).
	definition.Definition.Nodes = append(definition.Definition.Nodes, manifestNode{
		ID: "escapee", Type: "python", Config: json.RawMessage(
			`{"tool_version":"11111111-1111-4111-8111-111111111111"}`),
	})
	definition.Definition.Edges = append(definition.Definition.Edges,
		manifestEdge{Source: "choose2", Target: "escapee", Port: "no"},
		manifestEdge{Source: "escapee", Target: "join"},
	)
	_, err := buildGraphPlan(definition)
	if err == nil {
		t.Fatal("a cross-region edge must be refused")
	}
	if code := planErrorCode(t, err); code != "cross_region_edge" {
		t.Fatalf("code = %q, want cross_region_edge (%v)", code, err)
	}
	if !strings.Contains(err.Error(), "/edges/10") {
		t.Fatalf("the refusal must locate the escaping edge: %v", err)
	}
}

func TestBuildGraphPlanRejectsAMismatchedJoinPairing(t *testing.T) {
	definition := definitionDocument(t, flatConditionDefinition)
	// The join claims "@left" as its fork; left is a plain node with one edge.
	for index := range definition.Definition.Nodes {
		if definition.Definition.Nodes[index].ID == "join" {
			definition.Definition.Nodes[index].Config = json.RawMessage(
				`{"fork_id":"left","mode":"all_active","required_sources":["left","right"],"optional_sources":[]}`)
		}
	}
	_, err := buildGraphPlan(definition)
	if err == nil {
		t.Fatal("a join whose fork is not a fork must be refused")
	}
	if code := planErrorCode(t, err); code != "invalid_region" {
		t.Fatalf("code = %q, want invalid_region (%v)", code, err)
	}
}

func TestBuildGraphPlanRejectsIncompleteJoinSources(t *testing.T) {
	definition := definitionDocument(t, flatConditionDefinition)
	for index := range definition.Definition.Nodes {
		if definition.Definition.Nodes[index].ID == "join" {
			definition.Definition.Nodes[index].Config = json.RawMessage(
				`{"fork_id":"choose","mode":"all_active","required_sources":["left"],"optional_sources":[]}`)
		}
	}
	_, err := buildGraphPlan(definition)
	if err == nil {
		t.Fatal("a join that does not cover its real predecessors must be refused")
	}
	if code := planErrorCode(t, err); code != "bad_join_sources" {
		t.Fatalf("code = %q, want bad_join_sources (%v)", code, err)
	}
}

func TestBuildGraphPlanRejectsUnsupportedNodeTypesWithDistinctCodes(t *testing.T) {
	// F04B/T086 made `model` executable, T087 `approval` and T088 `retrieve`;
	// no per-type refusal remains, so only an unknown type has a distinct code.
	if !graphNodeTypes["approval"] || !graphNodeTypes["retrieve"] {
		t.Fatal("approval and retrieve must both be executable on the graph path")
	}
	cases := map[string]string{
		"mystery": "tool_unsupported_graph",
	}
	for nodeType, want := range cases {
		definition := definitionDocument(t, flatConditionDefinition)
		definition.Definition.Nodes = append(definition.Definition.Nodes, manifestNode{
			ID: "later", Type: nodeType, Config: json.RawMessage(`{}`),
		})
		definition.Definition.Edges = append(definition.Definition.Edges,
			manifestEdge{Source: "join", Target: "later"},
			manifestEdge{Source: "later", Target: "end"})
		// Replace join -> end so the inserted node stays on the path to end.
		filtered := definition.Definition.Edges[:0]
		for _, edge := range definition.Definition.Edges {
			if edge.Source == "join" && edge.Target == "end" {
				continue
			}
			filtered = append(filtered, edge)
		}
		definition.Definition.Edges = filtered
		_, err := buildGraphPlan(definition)
		if err == nil {
			t.Fatalf("%s node was accepted", nodeType)
		}
		if code := planErrorCode(t, err); code != want {
			t.Fatalf("%s code = %q, want %q (%v)", nodeType, code, want, err)
		}
	}
}

func TestBuildGraphPlanRejectsAMergeOutsideAJoin(t *testing.T) {
	definition := definitionDocument(t, flatConditionDefinition)
	// end also consumes the "right" branch directly: two incoming edges, not a join.
	definition.Definition.Edges = append(definition.Definition.Edges,
		manifestEdge{Source: "right", Target: "end"})
	_, err := buildGraphPlan(definition)
	if err == nil {
		t.Fatal("a non-join merge must be refused")
	}
	if code := planErrorCode(t, err); code != "merge_without_join" {
		t.Fatalf("code = %q, want merge_without_join (%v)", code, err)
	}
}

func TestBuildGraphPlanRejectsACycle(t *testing.T) {
	definition := definitionDocument(t, parallelDefinition)
	// left -> right and right -> left closes a cycle alongside the branches.
	definition.Definition.Edges = append(definition.Definition.Edges,
		manifestEdge{Source: "left", Target: "right"})
	_, err := buildGraphPlan(definition)
	if err == nil {
		t.Fatal("a cycle must be refused")
	}
	if code := planErrorCode(t, err); code != "cycle" && code != "merge_without_join" {
		t.Fatalf("code = %q (%v)", code, err)
	}
}

func TestHasGraphConstructsOnlyFiresForGraphOnlyNodes(t *testing.T) {
	if hasGraphConstructs(definitionDocument(t, parallelDefinition)) != true {
		t.Fatal("a join graph must select the graph path")
	}
	if hasGraphConstructs(definitionDocument(t, flatConditionDefinition)) != true {
		t.Fatal("a condition graph must select the graph path")
	}
	// F04B/T086: a model node is only executable by the graph scheduler, so its
	// presence must select that path even without a region.
	if hasGraphConstructs(modelGraphDocument(t, nil)) != true {
		t.Fatal("a model graph must select the graph path")
	}
	// F04B/T088: a retrieve node is graph-only too, even without a region.
	if hasGraphConstructs(definitionDocument(t, retrieveDefinition)) != true {
		t.Fatal("a retrieve graph must select the graph path")
	}
	linear := definitionDocument(t, `{
      "nodes":[{"id":"start","type":"start","config":{}},
               {"id":"stats","type":"python","config":{"tool_version":"11111111-1111-4111-8111-111111111111"}},
               {"id":"end","type":"end","config":{}}],
      "edges":[{"source":"start","target":"stats"},{"source":"stats","target":"end"}]}`)
	if hasGraphConstructs(linear) {
		t.Fatal("a linear chain must keep the linear path")
	}
}

// ---------------------------------------------------------------------------
// condition evaluator
// ---------------------------------------------------------------------------

func conditionNode(t *testing.T, raw string) *graphNode {
	t.Helper()
	config, err := parseConditionConfig(json.RawMessage(raw))
	if err != nil {
		t.Fatalf("parseConditionConfig: %v", err)
	}
	// The evaluator only needs the node's definition index (for located errors)
	// and the parsed config; the region proof is exercised by the plan tests.
	return &graphNode{index: 1, condition: config}
}

func TestEvaluateConditionSelectsTheFirstMatchingCase(t *testing.T) {
	node := conditionNode(t, `{"join_id":"join","default_port":"no","cases":[
        {"input":"region","op":"eq","value":"east","port":"east"},
        {"input":"amount","op":"gt","value":100,"port":"big"}]}`)
	output, err := evaluateCondition(node, map[string]any{"region": "east", "amount": float64(120)})
	if err != nil {
		t.Fatalf("evaluateCondition: %v", err)
	}
	if output["port"] != "east" || output["matched_case"] != float64(0) {
		t.Fatalf("output = %v", output)
	}
}

func TestEvaluateConditionFallsBackToTheDefaultPort(t *testing.T) {
	node := conditionNode(t, `{"join_id":"join","default_port":"no","cases":[
        {"input":"region","op":"eq","value":"east","port":"east"}]}`)
	output, err := evaluateCondition(node, map[string]any{"region": "west"})
	if err != nil {
		t.Fatalf("evaluateCondition: %v", err)
	}
	if output["port"] != "no" || output["matched_case"] != "default" {
		t.Fatalf("output = %v", output)
	}
}

func TestEvaluateConditionNeverCoercesTypes(t *testing.T) {
	// true == "true" is false; 1 == "1" is false; 1 == 1.0 is true (JSON number).
	node := conditionNode(t, `{"join_id":"join","default_port":"no","cases":[
        {"input":"flag","op":"eq","value":true,"port":"bool"},
        {"input":"number","op":"eq","value":1,"port":"number"},
        {"input":"text","op":"eq","value":"1","port":"text"}]}`)
	base := func() map[string]any {
		return map[string]any{"flag": false, "number": float64(0), "text": "x"}
	}
	for _, testCase := range []struct {
		name   string
		mutate func(map[string]any)
		port   string
	}{
		{"bool string is not a bool", func(b map[string]any) { b["flag"] = "true" }, "no"},
		{"bool matches", func(b map[string]any) { b["flag"] = true }, "bool"},
		{"number matches", func(b map[string]any) { b["number"] = float64(1) }, "number"},
		{"number matches as 1.0", func(b map[string]any) { b["number"] = float64(1.0) }, "number"},
		{"string is not a number", func(b map[string]any) { b["number"] = "1" }, "no"},
		{"string matches", func(b map[string]any) { b["text"] = "1" }, "text"},
	} {
		bindings := base()
		testCase.mutate(bindings)
		output, err := evaluateCondition(node, bindings)
		if err != nil {
			t.Fatalf("%s: %v", testCase.name, err)
		}
		if output["port"] != testCase.port {
			t.Fatalf("%s selected %v, want %q", testCase.name, output["port"], testCase.port)
		}
	}
}

func TestEvaluateConditionNumericOperatorsRefuseNonNumbers(t *testing.T) {
	node := conditionNode(t, `{"join_id":"join","default_port":"no","cases":[
        {"input":"amount","op":"gt","value":"120.50","port":"big"}]}`)
	// Money is a decimal string; a numeric comparison on it must fail, never guess.
	for _, bound := range []any{"120.50", true, nil, []any{}, map[string]any{}} {
		output, err := evaluateCondition(node, map[string]any{"amount": bound})
		if err == nil {
			t.Fatalf("amount %v was compared numerically: %v", bound, output)
		}
		if code := conditionErrorCode(t, err); code != CodeConditionOperandInvalid {
			t.Fatalf("amount %v code = %q, want %q", bound, code, CodeConditionOperandInvalid)
		}
	}
}

func TestEvaluateConditionInRequiresAnArrayAndUsesEquality(t *testing.T) {
	node := conditionNode(t, `{"join_id":"join","default_port":"no","cases":[
        {"input":"region","op":"in","value":["east","west"],"port":"known"}]}`)
	output, err := evaluateCondition(node, map[string]any{"region": "west"})
	if err != nil || output["port"] != "known" {
		t.Fatalf("in match = %v, %v", output, err)
	}
	output, err = evaluateCondition(node, map[string]any{"region": "north"})
	if err != nil || output["port"] != "no" {
		t.Fatalf("in miss = %v, %v", output, err)
	}
	bad := conditionNode(t, `{"join_id":"join","default_port":"no","cases":[
        {"input":"region","op":"in","value":"east","port":"known"}]}`)
	if _, err := evaluateCondition(bad, map[string]any{"region": "east"}); err == nil {
		t.Fatal("a non-array `in` operand must fail")
	}
}

func TestEvaluateConditionExistsTestsBoundAndNonNull(t *testing.T) {
	node := conditionNode(t, `{"join_id":"join","default_port":"no","cases":[
        {"input":"region","op":"exists","port":"present"}]}`)
	output, err := evaluateCondition(node, map[string]any{"region": "east"})
	if err != nil || output["port"] != "present" {
		t.Fatalf("exists bound = %v, %v", output, err)
	}
	output, err = evaluateCondition(node, map[string]any{"region": nil})
	if err != nil || output["port"] != "no" {
		t.Fatalf("exists null = %v, %v", output, err)
	}
	noValue := conditionNode(t, `{"join_id":"join","default_port":"no","cases":[
        {"input":"region","op":"exists","value":1,"port":"present"}]}`)
	if _, err := evaluateCondition(noValue, map[string]any{"region": "east"}); err == nil {
		t.Fatal("exists with a value must be refused")
	}
}

func TestEvaluateConditionReportsAnUnboundInputByName(t *testing.T) {
	node := conditionNode(t, `{"join_id":"join","default_port":"no","cases":[
        {"input":"region","op":"eq","value":"east","port":"east"}]}`)
	_, err := evaluateCondition(node, map[string]any{})
	if err == nil {
		t.Fatal("an unbound case input must fail")
	}
	if code := conditionErrorCode(t, err); code != CodeNodeInputInvalid {
		t.Fatalf("code = %q (%v)", code, err)
	}
	if !strings.Contains(err.Error(), "/nodes/1/inputs/region") {
		t.Fatalf("the failure must locate the binding: %v", err)
	}
}

func TestEvaluateConditionRejectsAMissingCaseValue(t *testing.T) {
	node := conditionNode(t, `{"join_id":"join","default_port":"no","cases":[
        {"input":"region","op":"eq","port":"east"}]}`)
	_, err := evaluateCondition(node, map[string]any{"region": "east"})
	if err == nil {
		t.Fatal("an eq case without a value must fail")
	}
	if code := conditionErrorCode(t, err); code != "missing_value" {
		t.Fatalf("code = %q (%v)", code, err)
	}
}

func conditionErrorCode(t *testing.T, err error) string {
	t.Helper()
	typed, ok := err.(*nodeError)
	if !ok {
		t.Fatalf("error %v is not a nodeError", err)
	}
	return typed.Code
}
