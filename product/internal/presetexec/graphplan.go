package presetexec

// F04B/T083: the structured-region graph plan.
//
// The linear executor (plan.go) proves one chain over the node types F04A and
// F05 gave it. This file adds the second plan the graph path uses when the sealed
// definition contains a `condition`, `join` or `model` node: it parses the whole
// DAG, proves the structured regions the scheduler is about to activate, and
// keeps the raw nodes so the scheduler binds and validates every node exactly
// like the linear path does.
//
// The executor deliberately re-proves the structure instead of trusting the
// publish validator: the manifest is read from an object store, and a stale or
// hand-edited snapshot must not be able to make the scheduler do something the
// compiler would not have allowed. The refusal vocabulary is the one the
// validator already freezes (rules.json): bad_config, duplicate_id,
// start_end_count, cycle, unreachable_node, cannot_reach_end, merge_without_join,
// invalid_region, bad_port, cross_region_edge, bad_join_sources, missing_value.
// Three additions are documented in reports/f04b/T083-condition-branching.md:
// `condition_operand_invalid` (a condition operand has the wrong JSON type),
// `invalid_activation` (spec-named: a join region with no activated required
// source) and the per-type `node_unsupported_<type>` codes so a run view can tell
// "this build does not run this node type yet" from "this graph is broken".
// F04B/T086 made `model` executable and moved its closed config into the same
// defensive re-verification (`parseModelConfig`).
//
// What the plan proves:
//   - exactly one start and one end, a DAG, every node reachable from start and
//     able to reach end;
//   - every non-join node has at most one incoming edge (merge_without_join);
//   - every join is paired with exactly one fork (condition.join_id <->
//     join.fork_id), and every condition/fork has exactly one join;
//   - dominance/post-dominance define each region's inside set, and no edge may
//     cross a region boundary except through its fork or join;
//   - join.required_sources + join.optional_sources are exactly the join's real
//     direct predecessors, disjoint and without duplicates.
//
// Region membership is recomputed from the frozen definition, never persisted:
// the T083 report records that decision.

import (
	"encoding/json"
	"fmt"
	"sort"
	"strings"
)

// Graph-path refusal codes. The region codes are the validator's frozen
// vocabulary; the remaining per-type refusal is `retrieve` until T088 replaces
// it. F04B/T086 made `model` executable and F04B/T087 made `approval`
// executable, so their old `node_unsupported_*` refusals are gone.
const (
	CodeInvalidRegion = "invalid_region"
	CodeCrossRegion   = "cross_region_edge"
	CodeBadPort       = "bad_port"
	CodeBadJoinSource = "bad_join_sources"
	CodeBadConfig     = "bad_config"
	CodeBadEdge       = "bad_edge"
	CodeMergeJoin     = "merge_without_join"
	CodeCycle         = "cycle"
	CodeUnreachable   = "unreachable_node"
	CodeNoEnd         = "cannot_reach_end"
	CodeDuplicateID   = "duplicate_id"
	CodeStartEndCount = "start_end_count"
	CodeMissingValue  = "missing_value"

	CodeNodeUnsupportedRetrieve = "node_unsupported_retrieve"

	// CodeConditionOperandInvalid is a condition operand whose JSON type cannot
	// take part in the configured operator. It is a run-time refusal because the
	// operand comes from this run's bound values.
	CodeConditionOperandInvalid = "condition_operand_invalid"
	// CodeInvalidActivation is the spec's name for a join region that cannot
	// activate (no required source was activated by its fork).
	CodeInvalidActivation = "invalid_activation"
)

// graphNodeTypes is the closed set of node types the graph path can execute.
var graphNodeTypes = map[string]bool{
	"start":     true,
	"end":       true,
	"python":    true,
	"agent":     true,
	"call_app":  true,
	"condition": true,
	"join":      true,
	"approval":  true,
	"model":     true,
}

// graphPlan is the parsed and proven graph.
type graphPlan struct {
	definition *manifestDefinition
	startID    string
	endID      string
	nodes      map[string]*graphNode
	// order is a deterministic topological order (start first, end last).
	order []string
	// edges preserves definition order so refusals can point at /edges/<i>.
	edges        []*planEdge
	regions      []*graphRegion
	regionByJoin map[string]*graphRegion
	// dom/postDom are the dominance sets the region proof is computed from.
	dom     map[string]map[string]bool
	postDom map[string]map[string]bool
}

// graphNode is one sealed node plus the plan facts the scheduler reads.
type graphNode struct {
	node      *manifestNode
	index     int
	incoming  []*planEdge
	outgoing  []*planEdge
	condition *conditionConfig
	join      *joinConfig
	approval  *approvalConfig
	model     *modelNodeConfig
}

// planEdge is one sealed edge with its normalized exit port.
type planEdge struct {
	index  int
	source string
	target string
	port   string
}

// exitPort is the edge's condition port with the contract's omitted value
// normalised to the literal "default".
func (e *planEdge) exitPort() string {
	if e.port == "" {
		return "default"
	}
	return e.port
}

// graphRegion is one fork/join pair with its proven member sets.
type graphRegion struct {
	fork string
	join string
	// inside is every node dominated by fork and post-dominated by join
	// (excluding both endpoints).
	inside map[string]bool
	// branches maps a condition port to the immediate branch entries.
	branches map[string][]string
	// sources are the join's real direct predecessors, sorted.
	sources  []string
	required map[string]bool
	optional map[string]bool
}

// planRefusal is a graph-path structural refusal with its stable run code.
func planRefusal(code, message string) *planError {
	return &planError{code: code, message: message}
}

// hasGraphConstructs reports whether the sealed definition carries a construct
// only the graph plan can execute. The legacy and linear paths keep their exact
// previous selection for every other definition. F04B/T086 added `model` (one
// controlled generation, no branch) and T087 added `approval` (a human wait).
func hasGraphConstructs(manifest *manifestDocument) bool {
	if manifest == nil {
		return false
	}
	for _, node := range manifest.Definition.Nodes {
		switch node.Type {
		case "condition", "join", "model", "approval":
			return true
		}
	}
	return false
}

// buildGraphPlan parses the sealed definition and proves the structured-region
// DAG the scheduler is about to run.
func buildGraphPlan(manifest *manifestDocument) (*graphPlan, error) {
	if manifest == nil {
		return nil, planRefusal(CodeToolUnsupportedGraph, "the sealed manifest carries no definition")
	}
	definition := &manifest.Definition
	if len(definition.Nodes) == 0 {
		return nil, planRefusal(CodeToolUnsupportedGraph, "the sealed definition carries no nodes")
	}
	plan := &graphPlan{
		definition:   definition,
		nodes:        make(map[string]*graphNode, len(definition.Nodes)),
		regionByJoin: make(map[string]*graphRegion),
		dom:          make(map[string]map[string]bool, len(definition.Nodes)),
		postDom:      make(map[string]map[string]bool, len(definition.Nodes)),
	}

	// 1. Nodes, ids and the closed type set.
	for index := range definition.Nodes {
		node := &definition.Nodes[index]
		if strings.TrimSpace(node.ID) == "" {
			return nil, planRefusal(CodeBadConfig,
				fmt.Sprintf("/nodes/%d: a sealed node has no id", index))
		}
		if _, duplicate := plan.nodes[node.ID]; duplicate {
			return nil, planRefusal(CodeDuplicateID,
				fmt.Sprintf("/nodes/%d: the sealed definition repeats node id %q", index, node.ID))
		}
		plan.nodes[node.ID] = &graphNode{node: node, index: index}
	}
	for index := range definition.Nodes {
		node := &definition.Nodes[index]
		if graphNodeTypes[node.Type] {
			continue
		}
		code := CodeToolUnsupportedGraph
		switch node.Type {
		case "retrieve":
			code = CodeNodeUnsupportedRetrieve
		}
		return nil, planRefusal(code, fmt.Sprintf(
			"/nodes/%d: the sealed definition contains a %q node; this executor runs start, end, python, agent, call_app, model, condition, join and approval",
			index, node.Type))
	}

	// 2. Edges: endpoints must exist and both directions are indexed.
	for index, edge := range definition.Edges {
		source, sourceOK := plan.nodes[edge.Source]
		target, targetOK := plan.nodes[edge.Target]
		if !sourceOK || !targetOK {
			return nil, planRefusal(CodeBadEdge, fmt.Sprintf(
				"/edges/%d: the edge names an unknown endpoint %q->%q",
				index, edge.Source, edge.Target))
		}
		parsed := &planEdge{index: index, source: edge.Source, target: edge.Target, port: edge.Port}
		plan.edges = append(plan.edges, parsed)
		source.outgoing = append(source.outgoing, parsed)
		target.incoming = append(target.incoming, parsed)
	}

	// 3. Exactly one start and one end.
	var starts, ends []string
	for index := range definition.Nodes {
		switch definition.Nodes[index].Type {
		case "start":
			starts = append(starts, definition.Nodes[index].ID)
		case "end":
			ends = append(ends, definition.Nodes[index].ID)
		}
	}
	if len(starts) != 1 || len(ends) != 1 {
		return nil, planRefusal(CodeStartEndCount, fmt.Sprintf(
			"the sealed definition has %d start and %d end nodes; exactly one of each is required",
			len(starts), len(ends)))
	}
	plan.startID, plan.endID = starts[0], ends[0]

	// 4. Deterministic topological order; a short order is a cycle.
	order, ok := plan.topologicalOrder()
	if !ok {
		return nil, planRefusal(CodeCycle, "the sealed definition's control-flow graph is not a DAG")
	}
	plan.order = order

	// 5. Reachability in both directions.
	reachable := plan.reachableFrom(plan.startID)
	toEnd := plan.reachableBackwardsFrom(plan.endID)
	for index := range definition.Nodes {
		id := definition.Nodes[index].ID
		if !reachable[id] {
			return nil, planRefusal(CodeUnreachable,
				fmt.Sprintf("/nodes/%d: node %q is not reachable from start", index, id))
		}
		if !toEnd[id] {
			return nil, planRefusal(CodeNoEnd,
				fmt.Sprintf("/nodes/%d: node %q has no path to end", index, id))
		}
	}

	// 6. Merge rule: only a join may have more than one incoming edge.
	for index := range definition.Nodes {
		node := &definition.Nodes[index]
		entry := plan.nodes[node.ID]
		if node.ID == plan.startID || len(entry.incoming) == 0 {
			continue
		}
		if node.Type != "join" && len(entry.incoming) >= 2 {
			return nil, planRefusal(CodeMergeJoin,
				fmt.Sprintf("/nodes/%d: node %q merges %d control paths without a join",
					index, node.ID, len(entry.incoming)))
		}
	}

	// 7. Parse the structured-region configs and the model node's closed config.
	for index := range definition.Nodes {
		node := &definition.Nodes[index]
		entry := plan.nodes[node.ID]
		switch node.Type {
		case "condition":
			config, err := parseConditionConfig(node.Config)
			if err != nil {
				return nil, planRefusal(CodeBadConfig,
					fmt.Sprintf("/nodes/%d/config: %v", index, err))
			}
			entry.condition = config
		case "join":
			config, err := parseJoinConfig(node.Config)
			if err != nil {
				return nil, planRefusal(CodeBadConfig,
					fmt.Sprintf("/nodes/%d/config: %v", index, err))
			}
			entry.join = config
		case "approval":
			config, err := parseApprovalConfig(node)
			if err != nil {
				return nil, planRefusal(CodeBadConfig,
					fmt.Sprintf("/nodes/%d/config: %v", index, err))
			}
			entry.approval = config
		case "model":
			// F04B/T086: the publish validator already checked the closed config
			// set; the executor re-proves it on the frozen bytes, before any node
			// of this attempt runs.
			config, err := parseModelConfig(node, index)
			if err != nil {
				return nil, planRefusal(CodeBadConfig,
					fmt.Sprintf("/nodes/%d/config: %v", index, err))
			}
			entry.model = config
		}
	}

	// 8. Fork/join pairing.
	declaredByJoin := map[string]string{} // fork id -> join id
	for index := range definition.Nodes {
		node := &definition.Nodes[index]
		if node.Type != "join" {
			continue
		}
		entry := plan.nodes[node.ID]
		forkID := entry.join.ForkID
		fork, known := plan.nodes[forkID]
		if !known || forkID == node.ID {
			return nil, planRefusal(CodeInvalidRegion,
				fmt.Sprintf("/nodes/%d/config/fork_id: the join names %q as its fork, which is not another node",
					index, forkID))
		}
		if fork.node.Type == "condition" {
			if fork.condition.JoinID != node.ID {
				return nil, planRefusal(CodeInvalidRegion,
					fmt.Sprintf("/nodes/%d/config/fork_id: the condition %q is paired with %q, not %q",
						index, forkID, fork.condition.JoinID, node.ID))
			}
		} else if len(fork.outgoing) < 2 {
			return nil, planRefusal(CodeInvalidRegion,
				fmt.Sprintf("/nodes/%d/config/fork_id: the fork %q has %d outgoing edges; a parallel fork has at least two",
					index, forkID, len(fork.outgoing)))
		}
		if existing, duplicate := declaredByJoin[forkID]; duplicate {
			return nil, planRefusal(CodeInvalidRegion,
				fmt.Sprintf("/nodes/%d/config/fork_id: the fork %q is already joined by %q",
					index, forkID, existing))
		}
		declaredByJoin[forkID] = node.ID
	}
	for index := range definition.Nodes {
		node := &definition.Nodes[index]
		if node.Type != "condition" {
			continue
		}
		entry := plan.nodes[node.ID]
		join, known := plan.nodes[entry.condition.JoinID]
		if !known || join.node.Type != "join" || join.join.ForkID != node.ID {
			return nil, planRefusal(CodeInvalidRegion,
				fmt.Sprintf("/nodes/%d/config/join_id: the condition names %q as its join, which is not its paired join",
					index, entry.condition.JoinID))
		}
	}
	// An ordinary node with several outgoing edges must be a declared fork; a
	// condition is implicitly a fork only when a join names it.
	for index := range definition.Nodes {
		node := &definition.Nodes[index]
		if node.Type == "condition" || node.Type == "join" {
			continue
		}
		entry := plan.nodes[node.ID]
		if len(entry.outgoing) >= 2 && declaredByJoin[node.ID] == "" {
			return nil, planRefusal(CodeInvalidRegion,
				fmt.Sprintf("/nodes/%d: node %q branches onto %d paths without a paired join",
					index, node.ID, len(entry.outgoing)))
		}
	}

	// 9. Exit ports: a condition's out-edge ports must be exactly the union of its
	// case ports and its default port; no other node may carry a non-default port.
	for index := range definition.Nodes {
		node := &definition.Nodes[index]
		entry := plan.nodes[node.ID]
		if node.Type != "condition" {
			for _, edge := range entry.outgoing {
				if edge.exitPort() != "default" {
					return nil, planRefusal(CodeBadPort,
						fmt.Sprintf("/edges/%d: the edge leaves %q with port %q; only a condition has ports",
							edge.index, node.ID, edge.port))
				}
			}
			continue
		}
		allowed := map[string]bool{entry.condition.DefaultPort: true}
		for _, testCase := range entry.condition.Cases {
			allowed[testCase.Port] = true
		}
		actual := map[string]bool{}
		for _, edge := range entry.outgoing {
			port := edge.exitPort()
			actual[port] = true
			if !allowed[port] {
				return nil, planRefusal(CodeBadPort, fmt.Sprintf(
					"/edges/%d: the condition %q has no case or default port %q",
					edge.index, node.ID, port))
			}
		}
		for port := range allowed {
			if !actual[port] {
				return nil, planRefusal(CodeBadPort, fmt.Sprintf(
					"/nodes/%d/config/cases: the condition %q declares port %q with no outgoing edge",
					index, node.ID, port))
			}
		}
	}

	// 10. Dominance/post-dominance and the region inside sets.
	plan.computeDominance()
	regionForks := make([]string, 0, len(declaredByJoin))
	for forkID := range declaredByJoin {
		regionForks = append(regionForks, forkID)
	}
	sort.Strings(regionForks)
	for _, forkID := range regionForks {
		joinID := declaredByJoin[forkID]
		region := &graphRegion{
			fork:     forkID,
			join:     joinID,
			inside:   map[string]bool{},
			branches: map[string][]string{},
			required: map[string]bool{},
			optional: map[string]bool{},
		}
		for id := range plan.nodes {
			if id == forkID || id == joinID {
				continue
			}
			if plan.dom[id][forkID] && plan.postDom[id][joinID] {
				region.inside[id] = true
			}
		}
		fork := plan.nodes[forkID]
		if fork.node.Type == "condition" {
			for _, edge := range fork.outgoing {
				region.branches[edge.exitPort()] = append(region.branches[edge.exitPort()], edge.target)
			}
		}
		joinEntry := plan.nodes[joinID]
		for source := range joinEntry.join.required {
			region.required[source] = true
		}
		for source := range joinEntry.join.optional {
			region.optional[source] = true
		}
		seen := map[string]bool{}
		for _, edge := range joinEntry.incoming {
			if !seen[edge.source] {
				seen[edge.source] = true
				region.sources = append(region.sources, edge.source)
			}
		}
		sort.Strings(region.sources)
		plan.regions = append(plan.regions, region)
		plan.regionByJoin[joinID] = region
	}

	// 11. Cross-region edges (the validator's stage-4 edge proof).
	for _, region := range plan.regions {
		for _, edge := range plan.edges {
			sourceInside := region.inside[edge.source]
			targetInside := region.inside[edge.target]
			illegal := false
			switch {
			case sourceInside && !targetInside && edge.target != region.join:
				illegal = true
			case targetInside && !sourceInside && edge.source != region.fork:
				illegal = true
			case edge.source == region.fork && edge.target != region.join && !targetInside:
				illegal = true
			case edge.target == region.join && edge.source != region.fork && !sourceInside:
				illegal = true
			}
			if illegal {
				return nil, planRefusal(CodeCrossRegion, fmt.Sprintf(
					"/edges/%d: the edge %q->%q crosses the boundary of the region %q->%q",
					edge.index, edge.source, edge.target, region.fork, region.join))
			}
		}
	}

	// 12. Join source coverage: exactly the join's real direct predecessors,
	// disjointly (validator stage 4d).
	for index := range definition.Nodes {
		node := &definition.Nodes[index]
		if node.Type != "join" {
			continue
		}
		entry := plan.nodes[node.ID]
		declared := map[string]bool{}
		duplicate := false
		for _, source := range entry.join.RequiredSources {
			if declared[source] {
				duplicate = true
			}
			declared[source] = true
		}
		for _, source := range entry.join.OptionalSources {
			if declared[source] {
				duplicate = true
			}
			declared[source] = true
		}
		actual := map[string]bool{}
		for _, edge := range entry.incoming {
			actual[edge.source] = true
		}
		if duplicate || len(declared) != len(actual) {
			return nil, planRefusal(CodeBadJoinSource,
				fmt.Sprintf("/nodes/%d/config/required_sources: the declared sources %v do not match the real predecessors %v",
					index, sortedKeys(declared), sortedKeys(actual)))
		}
		for source := range actual {
			if !declared[source] {
				return nil, planRefusal(CodeBadJoinSource,
					fmt.Sprintf("/nodes/%d/config/required_sources: the join does not declare the real predecessor %q",
						index, source))
			}
		}
	}

	// 13. Regions must nest strictly: two regions may not partially overlap.
	for first := 0; first < len(plan.regions); first++ {
		for second := first + 1; second < len(plan.regions); second++ {
			a, b := plan.regions[first], plan.regions[second]
			if !setsOverlap(a.inside, b.inside) {
				continue
			}
			if !setSubset(a.inside, b.inside) && !setSubset(b.inside, a.inside) {
				return nil, planRefusal(CodeInvalidRegion,
					fmt.Sprintf("the regions %q->%q and %q->%q partially overlap; regions must nest",
						a.fork, a.join, b.fork, b.join))
			}
		}
	}
	return plan, nil
}

// topologicalOrder is Kahn's algorithm with the definition index as the stable
// tie-break, so node.status sequences are reproducible.
func (p *graphPlan) topologicalOrder() ([]string, bool) {
	remaining := make(map[string]int, len(p.nodes))
	for id, node := range p.nodes {
		remaining[id] = len(node.incoming)
	}
	ready := make([]string, 0, len(p.nodes))
	for id, degree := range remaining {
		if degree == 0 {
			ready = append(ready, id)
		}
	}
	sort.Slice(ready, func(i, j int) bool { return p.nodes[ready[i]].index < p.nodes[ready[j]].index })
	order := make([]string, 0, len(p.nodes))
	for len(ready) > 0 {
		id := ready[0]
		ready = ready[1:]
		order = append(order, id)
		freed := make([]string, 0, 1)
		for _, edge := range p.nodes[id].outgoing {
			remaining[edge.target]--
			if remaining[edge.target] == 0 {
				freed = append(freed, edge.target)
			}
		}
		ready = append(ready, freed...)
		sort.Slice(ready, func(i, j int) bool { return p.nodes[ready[i]].index < p.nodes[ready[j]].index })
	}
	return order, len(order) == len(p.nodes)
}

// computeDominance fills p.dom and p.postDom. The topological order guarantees
// every predecessor/successor is already computed.
func (p *graphPlan) computeDominance() {
	for _, id := range p.order {
		if id == p.startID {
			p.dom[id] = map[string]bool{id: true}
			continue
		}
		var common map[string]bool
		for _, edge := range p.nodes[id].incoming {
			common = intersectSet(common, p.dom[edge.source])
		}
		if common == nil {
			common = map[string]bool{}
		}
		common[id] = true
		p.dom[id] = common
	}
	for position := len(p.order) - 1; position >= 0; position-- {
		id := p.order[position]
		if id == p.endID {
			p.postDom[id] = map[string]bool{id: true}
			continue
		}
		var common map[string]bool
		for _, edge := range p.nodes[id].outgoing {
			common = intersectSet(common, p.postDom[edge.target])
		}
		if common == nil {
			common = map[string]bool{}
		}
		common[id] = true
		p.postDom[id] = common
	}
}

func (p *graphPlan) reachableFrom(start string) map[string]bool {
	seen := map[string]bool{start: true}
	pending := []string{start}
	for len(pending) > 0 {
		current := pending[len(pending)-1]
		pending = pending[:len(pending)-1]
		for _, edge := range p.nodes[current].outgoing {
			if !seen[edge.target] {
				seen[edge.target] = true
				pending = append(pending, edge.target)
			}
		}
	}
	return seen
}

func (p *graphPlan) reachableBackwardsFrom(end string) map[string]bool {
	seen := map[string]bool{end: true}
	pending := []string{end}
	for len(pending) > 0 {
		current := pending[len(pending)-1]
		pending = pending[:len(pending)-1]
		for _, edge := range p.nodes[current].incoming {
			if !seen[edge.source] {
				seen[edge.source] = true
				pending = append(pending, edge.source)
			}
		}
	}
	return seen
}

func intersectSet(current, next map[string]bool) map[string]bool {
	if current == nil {
		return cloneSet(next)
	}
	for id := range current {
		if !next[id] {
			delete(current, id)
		}
	}
	return current
}

func cloneSet(source map[string]bool) map[string]bool {
	cloned := make(map[string]bool, len(source))
	for id := range source {
		cloned[id] = true
	}
	return cloned
}

func setSubset(inner, outer map[string]bool) bool {
	for id := range inner {
		if !outer[id] {
			return false
		}
	}
	return true
}

func setsOverlap(a, b map[string]bool) bool {
	for id := range a {
		if b[id] {
			return true
		}
	}
	return false
}

func sortedKeys(set map[string]bool) []string {
	keys := make([]string, 0, len(set))
	for id := range set {
		keys = append(keys, id)
	}
	sort.Strings(keys)
	return keys
}

// conditionConfig is the sealed condition node config.
type conditionConfig struct {
	JoinID      string          `json:"join_id"`
	DefaultPort string          `json:"default_port"`
	Cases       []conditionCase `json:"cases"`
}

// conditionCase is one condition case. It must know whether `value` was present:
// an absent value is a missing_value refusal, while an explicit null is a value
// the equality operators may compare against.
type conditionCase struct {
	Input    string
	Op       string
	Port     string
	Value    any
	HasValue bool
}

func (c *conditionCase) UnmarshalJSON(payload []byte) error {
	var raw struct {
		Input string          `json:"input"`
		Op    string          `json:"op"`
		Port  string          `json:"port"`
		Value json.RawMessage `json:"value"`
	}
	if err := json.Unmarshal(payload, &raw); err != nil {
		return err
	}
	c.Input, c.Op, c.Port = raw.Input, raw.Op, raw.Port
	if len(raw.Value) > 0 {
		value, err := decodeJSONDocument(raw.Value)
		if err != nil {
			return fmt.Errorf("the case value is not valid JSON: %w", err)
		}
		c.Value = value
		c.HasValue = true
	}
	return nil
}

// parseConditionConfig decodes and shape-checks the static condition config. The
// operand semantics are checked when the node runs: the values come from this
// run's bindings.
func parseConditionConfig(config json.RawMessage) (*conditionConfig, error) {
	if len(config) == 0 {
		return nil, fmt.Errorf("the condition node has no config")
	}
	var parsed conditionConfig
	if err := json.Unmarshal(config, &parsed); err != nil {
		return nil, fmt.Errorf("the condition config is not readable: %w", err)
	}
	if strings.TrimSpace(parsed.JoinID) == "" {
		return nil, fmt.Errorf("the condition config has no join_id")
	}
	if strings.TrimSpace(parsed.DefaultPort) == "" {
		return nil, fmt.Errorf("the condition config has no default_port")
	}
	if len(parsed.Cases) == 0 {
		return nil, fmt.Errorf("the condition config has no cases")
	}
	operations := map[string]bool{
		"eq": true, "ne": true, "gt": true, "gte": true, "lt": true, "lte": true,
		"in": true, "exists": true,
	}
	for position, testCase := range parsed.Cases {
		if strings.TrimSpace(testCase.Input) == "" {
			return nil, fmt.Errorf("case %d has no input", position)
		}
		if !operations[testCase.Op] {
			return nil, fmt.Errorf("case %d has the unknown op %q", position, testCase.Op)
		}
		if strings.TrimSpace(testCase.Port) == "" {
			return nil, fmt.Errorf("case %d has no port", position)
		}
	}
	return &parsed, nil
}

// joinConfig is the sealed join node config.
type joinConfig struct {
	ForkID          string   `json:"fork_id"`
	Mode            string   `json:"mode"`
	RequiredSources []string `json:"required_sources"`
	OptionalSources []string `json:"optional_sources"`
	required        map[string]bool
	optional        map[string]bool
}

func parseJoinConfig(config json.RawMessage) (*joinConfig, error) {
	if len(config) == 0 {
		return nil, fmt.Errorf("the join node has no config")
	}
	var parsed joinConfig
	if err := json.Unmarshal(config, &parsed); err != nil {
		return nil, fmt.Errorf("the join config is not readable: %w", err)
	}
	if strings.TrimSpace(parsed.ForkID) == "" {
		return nil, fmt.Errorf("the join config has no fork_id")
	}
	if parsed.Mode != "all_active" {
		return nil, fmt.Errorf("the join mode %q is not all_active", parsed.Mode)
	}
	if parsed.RequiredSources == nil {
		return nil, fmt.Errorf("the join config has no required_sources")
	}
	if parsed.OptionalSources == nil {
		return nil, fmt.Errorf("the join config has no optional_sources")
	}
	parsed.required = make(map[string]bool, len(parsed.RequiredSources))
	for _, source := range parsed.RequiredSources {
		parsed.required[source] = true
	}
	parsed.optional = make(map[string]bool, len(parsed.OptionalSources))
	for _, source := range parsed.OptionalSources {
		parsed.optional[source] = true
	}
	return &parsed, nil
}
