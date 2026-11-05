package presetexec

// F07/T078: the `call_app` node and the agent's sub-application tool.
//
// A `call_app` node pins one published child application version in its sealed
// config and maps an explicit, minimal input object from its declared bindings.
// Executing it never creates a Run locally: it calls the platform's contract
// spawnChild operation through the injected Client (real worker mTLS in
// cmd/supervisor), then:
//
//   - child still running -> the parent proposes waiting_children with the stable
//     checkpoint `child:<child_run_id>` and confirms its exit. The parent never
//     polls and never holds a slot while a child runs; the resume and the
//     child-terminal hook are F07/T081.
//   - child succeeded (a replayed spawn after resume, or a fast child) -> the
//     executor reads the child's structured result through the non-contract
//     result route, validates it against the node's output_schema, uploads it as
//     the node's data artifact and continues the chain.
//   - child failed/cancelled -> a required branch failure: the run fails with the
//     child's stable platform code (or child_required_failed when it has none).
//
// The agent's `subapp_versions` allowed set uses the same path: the executor
// declares one fixed tool per allowed child version (`call_app_1`, ...) and the
// model's validated tool call is mapped to a spawnChild call with a per-call
// invocation key. There is no other way for a model to create a Run.

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"

	"orchara/platform/internal/uuid"
)

// Stable failure codes for the delegation node. They extend the presetexec code
// vocabulary and are the run's error_code when the node itself refuses.
const (
	CodeChildSpawnFailed    = "child_spawn_failed"
	CodeChildResultFailed   = "child_result_failed"
	CodeChildRequiredFailed = "child_required_failed"
)

// agentSubapp is one fixed child application version an agent node may call.
type agentSubapp struct {
	// Name is the tool name the model sees; derived deterministically from the
	// declared set order, never from model text.
	Name      string
	VersionID string
}

// callAppConfig is the validated config of exactly one call_app node.
type callAppConfig struct {
	VersionID string
}

// validateCallAppNode proves the node pins one UUID version and declares an
// object output schema. The mapping itself is validated at publication time by
// T075 and re-checked at spawn time by the server.
func validateCallAppNode(node *manifestNode) (*callAppConfig, error) {
	var raw struct {
		ApplicationVersion string `json:"application_version"`
	}
	if err := json.Unmarshal(node.Config, &raw); err != nil {
		return nil, &planError{code: CodeToolUnsupportedGraph,
			message: "the call_app node config is not the documented object"}
	}
	version := strings.ToLower(strings.TrimSpace(raw.ApplicationVersion))
	if !uuid.Valid(version) {
		return nil, &planError{code: CodeToolUnsupportedGraph,
			message: "the call_app node application_version must be a fixed UUID; latest is never accepted"}
	}
	if !restrictedObjectSchema(node.OutputSchema) {
		return nil, &planError{code: CodeToolUnsupportedGraph,
			message: "the call_app node output_schema must be an object schema with type=object"}
	}
	return &callAppConfig{VersionID: version}, nil
}

// availableBeforeCallApp is the output namespace the node's bindings resolve
// against: the start input plus the admitted upstream nodes.
func availableBeforeCallApp(run *linearRun) map[string]any {
	if run.available != nil {
		return run.available
	}
	available := map[string]any{
		"input":           run.start,
		run.plan.start.ID: run.start,
	}
	if run.plan.python != nil {
		available[run.plan.python.ID] = run.python
	}
	if run.plan.agent != nil {
		available[run.plan.agent.ID] = run.agent
	}
	return available
}

// runCallAppNode executes the delegation segment. done=true means the returned
// outcome is terminal (failed/cancelled/waiting) and the chain stops.
func (r *runner) runCallAppNode(
	ctx context.Context, run *linearRun,
) (map[string]any, executionOutcome, bool) {
	node := run.plan.callApp
	config := run.plan.callAppConfig
	r.emitNode(ctx, node.ID, "pending")
	r.emitNode(ctx, node.ID, "running")

	bindings, err := resolveNodeBindings(node, availableBeforeCallApp(run))
	if err != nil {
		return nil, r.failDelegation(ctx, run, CodeNodeInputInvalid, err.Error()), true
	}
	// The mapped input is exactly the declared bindings. Nothing from the parent
	// input/history is added here; the server re-checks the field set as well.
	child, spawnErr := r.deps.Client.SpawnChild(ctx, r.lease.RunID, r.lease.AttemptToken, SpawnChildRequest{
		LogicalKey:           "call_app:" + node.ID,
		ApplicationVersionID: config.VersionID,
		Input:                bindings,
	})
	if spawnErr != nil {
		return nil, r.failDelegation(ctx, run, childFailureCode(spawnErr), childFailureMessage(spawnErr)), true
	}
	if child == nil || child.ID == "" {
		return nil, r.failDelegation(ctx, run, CodeChildSpawnFailed,
			"the platform returned no child Run for the call_app node"), true
	}
	output, outcome, done := r.adoptChildResult(ctx, run, node, child, true)
	if done {
		return nil, outcome, true
	}
	canonical, err := canonicalJSONDocument(output)
	if err != nil {
		return nil, r.failDelegation(ctx, run, CodeNodeOutputInvalid,
			"the child result cannot be canonicalised"), true
	}
	dataID, err := r.uploadNodeArtifact(ctx, "node_output", "application/json", canonical, "artifact")
	if err != nil {
		return nil, r.failDelegation(ctx, run, CodeChildResultFailed,
			"registering the child result failed: "+err.Error()), true
	}
	results := []NodeResult{{
		NodeID: node.ID, Kind: "data", ArtifactID: dataID, Label: "child-run:" + child.ID,
	}}
	if err := r.deps.Client.RecordNodeResults(
		ctx, r.lease.RunID, r.lease.AttemptToken, results,
	); err != nil {
		return nil, r.failDelegation(ctx, run, CodeChildResultFailed,
			"recording the child result failed: "+err.Error()), true
	}
	r.emitNode(ctx, node.ID, "succeeded")
	return output, executionOutcome{}, false
}

// adoptChildResult handles one child Run fact: wait, read the structured result
// and validate the node's output_schema, or fail a required branch.
func (r *runner) adoptChildResult(
	ctx context.Context, run *linearRun, node *manifestNode, child *ChildRun, required bool,
) (map[string]any, executionOutcome, bool) {
	switch child.State {
	case "succeeded":
		result, err := r.deps.Client.ChildResult(
			ctx, r.lease.RunID, r.lease.AttemptToken, child.ID,
		)
		if err != nil {
			return nil, r.failDelegation(ctx, run, childFailureCode(err), childFailureMessage(err)), true
		}
		if result == nil || result.State != "succeeded" || result.Output == nil {
			return nil, r.failDelegation(ctx, run, CodeChildResultFailed,
				"the child Run reported success without a structured result"), true
		}
		// A call_app node declares an output_schema that is a refinement of the
		// child's frozen output schema (T075); the result must satisfy the node's
		// own declaration. An agent sub-app tool has no node output schema (the
		// agent's output is its final answer), so node is nil there.
		if node != nil {
			if err := validateSchema(node.OutputSchema, result.Output, "#"); err != nil {
				return nil, r.failDelegation(ctx, run, CodeNodeOutputInvalid,
					"the child result does not match the node output_schema: "+err.Error()), true
			}
		}
		return result.Output, executionOutcome{}, false
	case "failed", "cancelled":
		if !required {
			// Optional branches are not produced by this build (the contract's
			// spawnChild body has no `required` field and T080/T082 own branch
			// semantics); the switch stays honest for the seam.
			return nil, r.failDelegation(ctx, run, CodeChildRequiredFailed,
				"an optional child branch failed"), true
		}
		code := CodeChildRequiredFailed
		if safePlatformCode(child.ErrorCode) {
			code = child.ErrorCode
		}
		return nil, r.failDelegation(ctx, run, code,
			"the required child Run ended "+child.State), true
	default:
		return nil, r.waitForChild(ctx, run, node, child), true
	}
}

// waitForChild parks the parent at the stable `child:<id>` checkpoint and
// certifies its exit. The single resume job and the child-terminal hook belong to
// F07/T081; the waiting condition is the durable hand-over F03/T035 consumes.
func (r *runner) waitForChild(
	ctx context.Context, run *linearRun, node *manifestNode, child *ChildRun,
) executionOutcome {
	proposal := Proposal{
		EventID:        uuid.NewString(),
		SuggestedState: "waiting_children",
		CheckpointID:   "child:" + child.ID,
		ErrorCode:      "children_pending",
	}
	if err := r.deps.Client.SubmitResult(
		ctx, r.lease.RunID, r.lease.AttemptToken, proposal,
	); err != nil {
		return r.failDelegation(ctx, run, CodeChildSpawnFailed,
			"the waiting_children proposal was refused: "+err.Error())
	}
	evidence := r.exitEvidence(r.heartbeatCount(), run.outcome, nil)
	if err := r.confirmExit(ctx, run.outcome.SandboxStarted, evidence); err != nil {
		return executionOutcome{outcome: "error", code: CodeChildSpawnFailed,
			err:     "the waiting exit confirmation was refused: " + err.Error(),
			sandbox: sandboxReportOf(run.outcome)}
	}
	return executionOutcome{
		outcome:  "waiting_children",
		code:     "children_pending",
		runState: r.readRunState(ctx),
		sandbox:  sandboxReportOf(run.outcome),
	}
}

// executeAgentSubapp runs one model-requested sub-application call. The tool name
// is one the executor declared from the sealed allowed set; the arguments are the
// child input object (validated by the capability server against the child's
// frozen input schema) and are re-validated by spawnChild.
func (r *runner) executeAgentSubapp(
	ctx context.Context, run *linearRun, node *manifestNode,
	sub agentSubapp, call agentToolCall,
) (string, []NodeResult, error, *executionOutcome) {
	logicalKey := fmt.Sprintf("agent:%s:subapp:%s", node.ID, call.ID)
	if len(logicalKey) > 200 {
		outcome := r.failDelegation(ctx, run, CodeAgentToolCallInvalid,
			"the sub-application tool logical key exceeds the platform limit")
		return "", nil, fmt.Errorf("subapp logical key too long"), &outcome
	}
	child, err := r.deps.Client.SpawnChild(ctx, r.lease.RunID, r.lease.AttemptToken, SpawnChildRequest{
		LogicalKey:           logicalKey,
		ApplicationVersionID: sub.VersionID,
		Input:                call.Arguments,
	})
	if err != nil {
		outcome := r.failDelegation(ctx, run, childFailureCode(err), childFailureMessage(err))
		return "", nil, err, &outcome
	}
	if child == nil || child.ID == "" {
		outcome := r.failDelegation(ctx, run, CodeChildSpawnFailed,
			"the platform returned no child Run for the sub-application tool")
		return "", nil, fmt.Errorf("no child run"), &outcome
	}
	output, outcome, done := r.adoptChildResult(ctx, run, nil, child, true)
	if done {
		return "", nil, fmt.Errorf("the sub-application child did not produce a result"), &outcome
	}
	message, err := canonicalJSONDocument(output)
	if err != nil {
		outcome := r.failDelegation(ctx, run, CodeChildResultFailed,
			"the child result cannot be encoded")
		return "", nil, err, &outcome
	}
	return string(message), nil, nil, nil
}

// failDelegation marks the delegation node failed, skips the rest of the chain and
// fails the run with a stable platform code.
func (r *runner) failDelegation(
	ctx context.Context, run *linearRun, code, message string,
) executionOutcome {
	if run.plan.callApp != nil {
		r.emitNode(ctx, run.plan.callApp.ID, "failed")
		r.skipAfter(ctx, run, run.plan.callApp.ID)
	} else if run.plan.agent != nil {
		r.emitNode(ctx, run.plan.agent.ID, "failed")
		r.skipAfter(ctx, run, run.plan.agent.ID)
	}
	return r.failAfterStart(ctx, code, message, run.outcome,
		r.exitEvidence(r.heartbeatCount(), run.outcome, nil))
}

// childFailureCode maps an internal-API refusal to a stable run code. The server's
// error codes are already a closed platform vocabulary (child_calls_exceeded,
// run_state_conflict, ...); anything else is child_spawn_failed.
func childFailureCode(err error) string {
	var coded interface{ ErrorCode() string }
	if err == nil || !errors.As(err, &coded) {
		return CodeChildSpawnFailed
	}
	candidate := strings.ToLower(strings.TrimSpace(coded.ErrorCode()))
	if safePlatformCode(candidate) {
		return candidate
	}
	return CodeChildSpawnFailed
}

func childFailureMessage(err error) string {
	if err == nil {
		return "the child Run could not be created"
	}
	return "the child Run could not be created: " + err.Error()
}
