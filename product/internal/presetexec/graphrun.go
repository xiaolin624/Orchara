package presetexec

// F04B/T083: the graph scheduler.
//
// The scheduler walks the plan's deterministic topological order and decides one
// activation question per node before executing it:
//
//   - start: always activated;
//   - an ordinary node: its single incoming edge must be taken by a succeeded
//     source, otherwise the node is skipped (this is how an unselected condition
//     branch is carried through the whole region);
//   - a join: every predecessor has settled (guaranteed by the topological order),
//     at least one edge into it must be taken and at least one activated source
//     must be required. Otherwise the region fails closed with
//     `invalid_activation`; a join whose fork was skipped is itself skipped, so a
//     region nested inside an unselected branch never dead-waits.
//
// Edge "taken" rules:
//
//   - leaf/ordinary/join/agent/call_app successes take every outgoing edge;
//   - a condition takes only the edges of its selected port.
//
// A node that succeeds exactly once therefore emits exactly one `succeeded`, and a
// join runs exactly once per run (one data artifact, one event), never per
// incoming edge. A failed node emits `failed`, everything not yet settled emits
// `skipped` in topological order and the run fails with the node's stable code.
//
// Node execution itself is the linear executor's node code: executeStartNode,
// executePythonNode, executeEndNode and — through a synthetic linear view — the
// agent and call_app segments. The graph only owns activation, skip propagation
// and the condition/join outputs.

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"

	"orchara/platform/internal/jcs"
	"orchara/platform/internal/sandbox"
)

// graphRun is one graph attempt's mutable state.
type graphRun struct {
	plan     *graphPlan
	inputs   []InputArtifact
	outputs  map[string]map[string]any
	state    map[string]string
	taken    map[int]bool
	position map[string]int
	last     sandbox.Outcome
	report   *SandboxReport
	// started records whether any node of this attempt attempted a sandbox; it
	// decides whether a later failure certifies a started attempt or a refusal.
	started bool
}

func newGraphRun(plan *graphPlan, inputs []InputArtifact) *graphRun {
	run := &graphRun{
		plan:     plan,
		inputs:   inputs,
		outputs:  make(map[string]map[string]any, len(plan.nodes)),
		state:    make(map[string]string, len(plan.nodes)),
		taken:    make(map[int]bool, len(plan.edges)),
		position: make(map[string]int, len(plan.order)),
	}
	for index, id := range plan.order {
		run.position[id] = index
	}
	return run
}

// available is the upstream-output namespace node bindings resolve against: every
// succeeded node by id, and the start output under the documented `input` name.
func (g *graphRun) available() map[string]any {
	available := make(map[string]any, len(g.outputs)+1)
	for id, output := range g.outputs {
		if output != nil {
			available[id] = output
		}
	}
	if start, ok := g.outputs[g.plan.startID]; ok {
		available["input"] = start
	}
	return available
}

func (g *graphRun) scopeIDs() []string {
	return append([]string(nil), g.plan.order...)
}

// takeOutEdges applies the plan's edge rule after one node succeeded.
func (g *graphRun) takeOutEdges(node *graphNode, output map[string]any) {
	if node.node.Type == "condition" {
		selected, _ := output["port"].(string)
		for _, edge := range node.outgoing {
			if edge.exitPort() == selected {
				g.taken[edge.index] = true
			}
		}
		return
	}
	for _, edge := range node.outgoing {
		g.taken[edge.index] = true
	}
}

// executeGraph runs the proven graph.
func (r *runner) executeGraph(ctx context.Context, plan *graphPlan) executionOutcome {
	linked, err := r.deps.Inputs.Inputs(ctx, r.lease.RunID)
	if err != nil {
		return r.refuse(ctx, CodeToolInputInvalid,
			"the run's linked inputs could not be resolved: "+err.Error())
	}
	run := newGraphRun(plan, linked)
	reused := r.reusableNodeOutputs(ctx, plan)

	for _, id := range plan.order {
		node := plan.nodes[id]
		if output, present := reused[id]; present {
			run.state[id] = "succeeded"
			run.outputs[id] = output
			r.emitNode(ctx, id, "succeeded")
			run.takeOutEdges(node, output)
			continue
		}
		if id != plan.startID {
			switch {
			case node.node.Type == "join":
				region := plan.regionByJoin[id]
				if region == nil {
					return r.failGraph(ctx, run, id, CodeInvalidRegion,
						"the join has no proven region")
				}
				// A region whose fork was skipped is skipped as a whole; its join
				// must not wait for (or fail because of) branches that never
				// existed. This is the structured-region half of "no dead-wait".
				if run.state[region.fork] == "skipped" {
					run.state[id] = "skipped"
					r.emitNode(ctx, id, "skipped")
					continue
				}
				activated, requiredActivated := 0, false
				for _, edge := range node.incoming {
					if !run.taken[edge.index] {
						continue
					}
					activated++
					if region.required[edge.source] {
						requiredActivated = true
					}
				}
				if activated == 0 || !requiredActivated {
					return r.failGraph(ctx, run, id, CodeInvalidActivation, fmt.Sprintf(
						"the join %q has %d activated incoming edges and no activated required source",
						id, activated))
				}
			default:
				if len(node.incoming) != 1 {
					return r.failGraph(ctx, run, id, CodeMergeJoin, fmt.Sprintf(
						"the node %q has %d incoming edges but is not a join", id, len(node.incoming)))
				}
				if !run.taken[node.incoming[0].index] {
					run.state[id] = "skipped"
					r.emitNode(ctx, id, "skipped")
					continue
				}
			}
		}
		outcome, output, done := r.executeGraphNode(ctx, run, node)
		if done {
			return outcome
		}
		run.state[id] = "succeeded"
		run.outputs[id] = output
		run.takeOutEdges(node, output)
	}
	// A proven graph's only terminal node is `end`; finishing the order without
	// its outcome means the end was never activated. Fail closed rather than
	// reporting a success that never sealed an output.
	return r.failGraph(ctx, run, plan.endID, CodeInvalidActivation,
		"the graph's end node was never activated")
}

// executeGraphNode runs one activated node. done=true means the returned outcome
// is terminal (the node failed/cancelled/waiting and the run result was handled).
func (r *runner) executeGraphNode(
	ctx context.Context, run *graphRun, node *graphNode,
) (executionOutcome, map[string]any, bool) {
	id := node.node.ID
	switch node.node.Type {
	case "start":
		output, err := r.executeStartNode(ctx, node.node, run.plan.definition, run.inputs)
		if err != nil {
			code := CodeNodeInputInvalid
			if errors.Is(err, errRunInputUnavailable) {
				code = CodeToolInputInvalid
			}
			return r.failGraph(ctx, run, id, code, err.Error()), nil, true
		}
		return executionOutcome{}, output, false
	case "python":
		execution := r.executePythonNode(ctx, node.node, run.available(), run.inputs, nodeHost{
			failed: func() { r.emitNode(ctx, id, "failed") },
			skip:   func() { r.skipUnsettled(ctx, run, id) },
		})
		run.last, run.report = execution.sandbox, execution.report
		run.started = true
		if execution.terminal != nil {
			return *execution.terminal, nil, true
		}
		return executionOutcome{}, execution.output, false
	case "condition":
		return r.executeGraphCondition(ctx, run, node)
	case "join":
		return r.executeGraphJoin(ctx, run, node)
	case "agent":
		return r.executeGraphAgent(ctx, run, node)
	case "call_app":
		return r.executeGraphCallApp(ctx, run, node)
	case "retrieve":
		return r.executeGraphRetrieve(ctx, run, node)
	case "end":
		return r.executeEndNode(ctx, run.plan.definition, node.node,
			run.available(), run.last, run.report), nil, true
	default:
		return r.failGraph(ctx, run, id, CodeToolUnsupportedGraph,
			"the executor cannot run the node type "+node.node.Type), nil, true
	}
}

// executeGraphCondition resolves the node's bindings, selects one port and
// publishes the run-view output {"port":..., "matched_case":...}.
func (r *runner) executeGraphCondition(
	ctx context.Context, run *graphRun, node *graphNode,
) (executionOutcome, map[string]any, bool) {
	id := node.node.ID
	r.emitNode(ctx, id, "pending")
	r.emitNode(ctx, id, "running")
	bindings, err := resolveNodeBindings(node.node, run.available())
	if err != nil {
		return r.failGraphNodeBeforeSandbox(ctx, run, node, CodeNodeInputInvalid, err.Error()), nil, true
	}
	output, err := evaluateCondition(node, bindings)
	if err != nil {
		return r.failGraphNodeBeforeSandbox(ctx, run, node, codeOfNodeError(err), err.Error()), nil, true
	}
	canonical, err := canonicalNodeDocument(output)
	if err != nil {
		return r.failGraphNodeBeforeSandbox(ctx, run, node, CodeNodeOutputInvalid,
			"the condition output cannot be encoded"), nil, true
	}
	artifactID, err := r.uploadNodeArtifact(ctx, "node_output", "application/json", canonical, "artifact")
	if err != nil {
		return r.failGraphNodeAfterSandbox(ctx, run, node, CodeSandboxFailed,
			"registering the condition port failed: "+err.Error()), nil, true
	}
	if err := r.deps.Client.RecordNodeResults(ctx, r.lease.RunID, r.lease.AttemptToken,
		[]NodeResult{{NodeID: id, Kind: "data", ArtifactID: artifactID, Label: "port"}}); err != nil {
		return r.failGraphNodeAfterSandbox(ctx, run, node, CodeSandboxFailed,
			"recording the condition port failed: "+err.Error()), nil, true
	}
	r.emitNode(ctx, id, "succeeded")
	return executionOutcome{}, output, false
}

// executeGraphJoin merges exactly the region's activated branches into the
// contract's results envelope and runs once per region activation.
func (r *runner) executeGraphJoin(
	ctx context.Context, run *graphRun, node *graphNode,
) (executionOutcome, map[string]any, bool) {
	id := node.node.ID
	region := run.plan.regionByJoin[id]
	r.emitNode(ctx, id, "pending")
	r.emitNode(ctx, id, "running")

	results := make(map[string]any, len(region.sources))
	for _, source := range region.sources {
		status := run.state[source]
		if status == "" {
			// Every predecessor is settled by the topological order; an empty
			// state can only mean the branch was never activated.
			status = "skipped"
		}
		entry := map[string]any{"status": status}
		if status == "succeeded" {
			entry["value"] = run.outputs[source]
		}
		results[source] = entry
	}
	envelope := map[string]any{"results": results}
	canonical, err := canonicalNodeDocument(envelope)
	if err != nil {
		return r.failGraphNodeAfterSandbox(ctx, run, node, CodeNodeOutputInvalid,
			"the join envelope cannot be encoded"), nil, true
	}
	artifactID, err := r.uploadNodeArtifact(ctx, "node_output", "application/json", canonical, "artifact")
	if err != nil {
		return r.failGraphNodeAfterSandbox(ctx, run, node, CodeSandboxFailed,
			"registering the join envelope failed: "+err.Error()), nil, true
	}
	if err := r.deps.Client.RecordNodeResults(ctx, r.lease.RunID, r.lease.AttemptToken,
		[]NodeResult{{NodeID: id, Kind: "data", ArtifactID: artifactID, Label: "results"}}); err != nil {
		return r.failGraphNodeAfterSandbox(ctx, run, node, CodeSandboxFailed,
			"recording the join envelope failed: "+err.Error()), nil, true
	}
	r.emitNode(ctx, id, "succeeded")
	return executionOutcome{}, envelope, false
}

// executeGraphAgent runs one agent node through the shared controlled loop by
// presenting it as the agent of a one-node linear view. The view shares the
// graph's upstream outputs, its skip hook and its sealed-node scope.
func (r *runner) executeGraphAgent(
	ctx context.Context, run *graphRun, node *graphNode,
) (executionOutcome, map[string]any, bool) {
	config, err := validateAgentNode(node.node, r.manifest)
	if err != nil {
		return r.failGraphNodeBeforeSandbox(ctx, run, node, codeOfPlanError(err), err.Error()), nil, true
	}
	view := r.graphLinearView(ctx, run, node.node)
	view.plan.agent = node.node
	view.plan.agentConfig = config
	output, outcome, done := r.runAgentNode(ctx, view)
	run.last, run.report = view.outcome, view.report
	run.started = true
	if done {
		return outcome, nil, true
	}
	return executionOutcome{}, output, false
}

// executeGraphCallApp runs one call_app node through the shared delegation code
// by presenting it as the call_app of a one-node linear view.
func (r *runner) executeGraphCallApp(
	ctx context.Context, run *graphRun, node *graphNode,
) (executionOutcome, map[string]any, bool) {
	config, err := validateCallAppNode(node.node)
	if err != nil {
		return r.failGraphNodeBeforeSandbox(ctx, run, node, codeOfPlanError(err), err.Error()), nil, true
	}
	view := r.graphLinearView(ctx, run, node.node)
	view.plan.callApp = node.node
	view.plan.callAppConfig = config
	output, outcome, done := r.runCallAppNode(ctx, view)
	run.last, run.report = view.outcome, view.report
	run.started = true
	if done {
		return outcome, nil, true
	}
	return executionOutcome{}, output, false
}

// graphLinearView is the one-node linear view the agent/call_app segment code
// reads. It never rewrites the graph plan: it only carries the start node, the
// upstream namespace and the scheduler's skip hook.
func (r *runner) graphLinearView(
	ctx context.Context, run *graphRun, node *manifestNode,
) *linearRun {
	startNode := run.plan.nodes[run.plan.startID].node
	view := &linearRun{
		plan: &linearPlan{
			definition: run.plan.definition,
			start:      startNode,
			order:      []*manifestNode{startNode, node},
		},
		inputs:    run.inputs,
		start:     run.outputs[run.plan.startID],
		available: run.available(),
		scopeIDs:  run.scopeIDs(),
		outcome:   run.last,
		report:    run.report,
		skip:      func() { r.skipUnsettled(ctx, run, node.ID) },
	}
	return view
}

// skipUnsettled emits one skipped event for every node after `afterID` that has
// not settled yet, in topological order.
func (r *runner) skipUnsettled(ctx context.Context, run *graphRun, afterID string) {
	index, known := run.position[afterID]
	if !known {
		index = -1
	}
	for position, id := range run.plan.order {
		if position <= index || run.state[id] != "" {
			continue
		}
		run.state[id] = "skipped"
		r.emitNode(ctx, id, "skipped")
	}
}

// failGraph marks one node failed, skips everything unsettled and fails the run.
func (r *runner) failGraph(
	ctx context.Context, run *graphRun, nodeID, code, message string,
) executionOutcome {
	r.emitNode(ctx, nodeID, "failed")
	r.skipUnsettled(ctx, run, nodeID)
	return r.failGraphRun(ctx, run, code, message)
}

// failGraphNodeBeforeSandbox is a join/condition refusal that needs no sandbox.
func (r *runner) failGraphNodeBeforeSandbox(
	ctx context.Context, run *graphRun, node *graphNode, code, message string,
) executionOutcome {
	return r.failGraph(ctx, run, node.node.ID, code, message)
}

// failGraphNodeAfterSandbox is a platform-side refusal after earlier work ran.
func (r *runner) failGraphNodeAfterSandbox(
	ctx context.Context, run *graphRun, node *graphNode, code, message string,
) executionOutcome {
	r.emitNode(ctx, node.node.ID, "failed")
	r.skipUnsettled(ctx, run, node.node.ID)
	return r.failGraphRun(ctx, run, code, message)
}

// failGraphRun submits the failed run result. An attempt that never started a
// sandbox is a deterministic refusal (sandbox_started=false); one that did keeps
// its last sandbox facts as the exit evidence.
func (r *runner) failGraphRun(
	ctx context.Context, run *graphRun, code, message string,
) executionOutcome {
	if run.started {
		return r.failAfterStart(ctx, code, message, run.last,
			r.exitEvidence(r.heartbeatCount(), run.last, nil))
	}
	return r.refuse(ctx, code, message)
}

// reusableNodeOutputs reads the run's completed node data (the recovery seam) and
// validates every reused object against the node's sealed output_schema. A
// transport error or a single malformed object degrades to re-execution, never to
// an unvalidated value.
func (r *runner) reusableNodeOutputs(ctx context.Context, plan *graphPlan) map[string]map[string]any {
	if r.deps.NodeOutputs == nil {
		return nil
	}
	data, err := r.deps.NodeOutputs.NodeData(ctx, r.lease.RunID)
	if err != nil {
		r.logger.Printf("run_id=%s: reading completed node outputs failed; every node will run: %v",
			r.lease.RunID, err)
		return nil
	}
	outputs := make(map[string]map[string]any, len(data))
	for nodeID, payload := range data {
		node, known := plan.nodes[nodeID]
		if !known {
			continue
		}
		value, err := decodeJSONDocument(payload)
		if err != nil {
			r.logger.Printf("run_id=%s: node %s has unreadable completed output; it will run again: %v",
				r.lease.RunID, nodeID, err)
			continue
		}
		object, ok := value.(map[string]any)
		if !ok {
			r.logger.Printf("run_id=%s: node %s has a non-object completed output; it will run again",
				r.lease.RunID, nodeID)
			continue
		}
		if err := validateSchema(node.node.OutputSchema, object, "#/data"); err != nil {
			r.logger.Printf("run_id=%s: node %s completed output no longer matches its sealed schema; it will run again: %v",
				r.lease.RunID, nodeID, err)
			continue
		}
		outputs[nodeID] = object
	}
	return outputs
}

// canonicalNodeDocument is the canonical JSON a node data artifact carries.
func canonicalNodeDocument(document map[string]any) ([]byte, error) {
	payload, err := json.Marshal(document)
	if err != nil {
		return nil, err
	}
	return jcs.CanonicalizeBytes(payload)
}
