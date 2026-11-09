package presetexec

// F07/T078: the `call_app` node and the agent's sub-application tool.
//
// A `call_app` node pins one published child application version in its sealed
// config and maps an explicit, minimal input object from its declared bindings.
// Executing it never creates a Run locally: it calls the platform's contract
// spawnChild operation through the injected Client (real worker mTLS in
// cmd/supervisor), then:
//
//   - child still running -> the node returns the local waiting decision for the
//     stable checkpoint `child:<child_run_id>`; the owning scheduler drains the
//     started siblings, submits the one waiting proposal and confirms the exit.
//     The parent never polls and never holds a slot while a child runs; the
//     resume and the child-terminal hook are F07/T081.
//   - child succeeded (a replayed spawn after resume, or a fast child) -> the
//     executor reads the child's structured result through the non-contract
//     result route, validates it against the node's output_schema, uploads it as
//     the node's data artifact, reports the node result and continues the chain.
//   - child failed/cancelled -> a required branch failure: the node returns the
//     local failure decision with the child's stable platform code (or
//     child_required_failed when it has none).
//
// C4.02: the node receives the same explicit nodeExecution as the agent node and
// returns a typed nodeOutcome. It never submits the run terminal result, never
// skips siblings and never reads a hidden scheduler record from the context: the
// graph and linear schedulers map the decision and remain the only committers.
//
// The agent's `subapp_versions` allowed set uses the same delegation seam: the
// executor declares one fixed tool per allowed child version (`call_app_1`, ...)
// and the model's validated tool call is mapped to a spawnChild call with a
// per-call invocation key. There is no other way for a model to create a Run.

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

// runCallAppNode executes the delegation node against the explicit node execution
// the owning scheduler built: the sealed node, the fixed child version the
// scheduler proved and the upstream-output namespace snapshot its bindings resolve
// against. The returned decision is local; the scheduler owns the run terminal
// result, the sibling drain and the skip facts.
func (r *runner) runCallAppNode(ctx context.Context, exec *nodeExecution) nodeOutcome {
	node := exec.node
	r.emitNode(ctx, node.ID, "pending")
	r.emitNode(ctx, node.ID, "running")

	config := exec.callAppConfig
	if config == nil {
		return r.callAppFailed(ctx, exec, CodeToolUnsupportedGraph,
			"the call_app node execution carries no sealed child application version")
	}
	bindings, err := resolveNodeBindings(node, exec.namespace)
	if err != nil {
		return r.callAppFailed(ctx, exec, CodeNodeInputInvalid, err.Error())
	}
	// The mapped input is exactly the declared bindings. Nothing from the parent
	// input/history is added here; the server re-checks the field set as well.
	child, spawnErr := r.deps.Client.SpawnChild(ctx, r.lease.RunID, r.lease.AttemptToken, SpawnChildRequest{
		LogicalKey:           "call_app:" + node.ID,
		ApplicationVersionID: config.VersionID,
		Input:                bindings,
	})
	if spawnErr != nil {
		return r.callAppFailed(ctx, exec, childFailureCode(spawnErr), childFailureMessage(spawnErr))
	}
	if child == nil || child.ID == "" {
		return r.callAppFailed(ctx, exec, CodeChildSpawnFailed,
			"the platform returned no child Run for the call_app node")
	}
	decision := r.classifyChildResult(ctx, node, child, true)
	if decision.child != nil {
		return r.callAppWaiting(exec, *decision.child)
	}
	if decision.code != "" {
		return r.callAppFailed(ctx, exec, decision.code, decision.message)
	}
	canonical, err := canonicalJSONDocument(decision.output)
	if err != nil {
		return r.callAppFailed(ctx, exec, CodeNodeOutputInvalid,
			"the child result cannot be canonicalised")
	}
	dataID, err := r.uploadNodeArtifact(ctx, "node_output", "application/json", canonical, "artifact")
	if err != nil {
		return r.callAppFailed(ctx, exec, CodeChildResultFailed,
			"registering the child result failed: "+err.Error())
	}
	results := []NodeResult{{
		NodeID: node.ID, Kind: "data", ArtifactID: dataID, Label: "child-run:" + child.ID,
	}}
	if err := r.deps.Client.RecordNodeResults(
		ctx, r.lease.RunID, r.lease.AttemptToken, results,
	); err != nil {
		return r.callAppFailed(ctx, exec, CodeChildResultFailed,
			"recording the child result failed: "+err.Error())
	}
	r.emitNode(ctx, node.ID, "succeeded")
	return r.nodeSucceeded(exec, decision.output)
}

// callAppFailed publishes the node's own failed fact and returns the local
// failure decision. The owning scheduler skips the remaining nodes and commits
// the run result; the node has no such authority.
func (r *runner) callAppFailed(
	ctx context.Context, exec *nodeExecution, code, message string,
) nodeOutcome {
	if exec.node != nil {
		r.emitNode(ctx, exec.node.ID, "failed")
	}
	return r.nodeFailed(exec, code, message)
}

// childDecision is the scheduler-neutral decision for one observed child Run. A
// failure carries code/message, a still-running child names the Run to wait on,
// and a succeeded child carries its validated structured result. Nothing is
// committed here: the owning scheduler maps the decision to its own behavior.
type childDecision struct {
	output  map[string]any
	code    string
	message string
	child   *ChildRun
}

// classifyChildResult turns one child Run fact into a local decision without
// committing the parent result. call_app maps it to its typed node outcome; the
// agent maps it to its own typed node outcome. node is nil for an agent sub-app
// tool (the agent's output is its final answer, not the child schema).
func (r *runner) classifyChildResult(
	ctx context.Context, node *manifestNode, child *ChildRun, required bool,
) childDecision {
	switch child.State {
	case "succeeded":
		result, err := r.deps.Client.ChildResult(
			ctx, r.lease.RunID, r.lease.AttemptToken, child.ID,
		)
		if err != nil {
			return childDecision{code: childFailureCode(err), message: childFailureMessage(err)}
		}
		if result == nil || result.State != "succeeded" || result.Output == nil {
			return childDecision{code: CodeChildResultFailed,
				message: "the child Run reported success without a structured result"}
		}
		// A call_app node declares an output_schema that is a refinement of the
		// child's frozen output schema (T075); the result must satisfy the node's
		// own declaration. An agent sub-app tool has no node output schema (the
		// agent's output is its final answer), so node is nil there.
		if node != nil {
			if err := validateSchema(node.OutputSchema, result.Output, "#"); err != nil {
				return childDecision{code: CodeNodeOutputInvalid,
					message: "the child result does not match the node output_schema: " + err.Error()}
			}
		}
		return childDecision{output: result.Output}
	case "failed", "cancelled":
		if !required {
			// Optional branches are not produced by this build (the contract's
			// spawnChild body has no `required` field and T080/T082 own branch
			// semantics); the switch stays honest for the seam.
			return childDecision{code: CodeChildRequiredFailed,
				message: "an optional child branch failed"}
		}
		code := CodeChildRequiredFailed
		if safePlatformCode(child.ErrorCode) {
			code = child.ErrorCode
		}
		return childDecision{code: code,
			message: "the required child Run ended " + child.State}
	default:
		return childDecision{child: child}
	}
}

// childWaitingProposal is the stable `child:<id>` checkpoint a delegation parks at.
func childWaitingProposal(child *ChildRun) Proposal {
	return Proposal{
		EventID:        uuid.NewString(),
		SuggestedState: "waiting_children",
		CheckpointID:   "child:" + child.ID,
		ErrorCode:      "children_pending",
	}
}

// callAppWaiting returns the local waiting decision for a live child Run. The
// owning scheduler drains the started siblings first, then submits the one
// waiting proposal and certifies the exit; the node never parks the run itself.
func (r *runner) callAppWaiting(exec *nodeExecution, child ChildRun) nodeOutcome {
	outcome := r.nodeWaiting(exec, childWaitingProposal(&child),
		"waiting_children", CodeChildSpawnFailed)
	outcome.proposalRefusal = "the waiting_children proposal was refused: "
	outcome.exitRefusal = "the waiting exit confirmation was refused: "
	return outcome
}

// executeAgentSubapp runs one model-requested sub-application call. The tool name
// is one the executor declared from the sealed allowed set; the arguments are the
// child input object (validated by the capability server against the child's
// frozen input schema) and are re-validated by spawnChild. A failure or waiting
// child returns a local node outcome: the owning scheduler commits it.
func (r *runner) executeAgentSubapp(
	ctx context.Context, exec *nodeExecution, node *manifestNode,
	sub agentSubapp, call agentToolCall,
) (string, []NodeResult, *nodeOutcome) {
	logicalKey := fmt.Sprintf("agent:%s:subapp:%s", node.ID, call.ID)
	if len(logicalKey) > 200 {
		outcome := r.agentFailed(ctx, exec, CodeAgentToolCallInvalid,
			"the sub-application tool logical key exceeds the platform limit")
		return "", nil, &outcome
	}
	child, err := r.deps.Client.SpawnChild(ctx, r.lease.RunID, r.lease.AttemptToken, SpawnChildRequest{
		LogicalKey:           logicalKey,
		ApplicationVersionID: sub.VersionID,
		Input:                call.Arguments,
	})
	if err != nil {
		outcome := r.agentFailed(ctx, exec, childFailureCode(err), childFailureMessage(err))
		return "", nil, &outcome
	}
	if child == nil || child.ID == "" {
		outcome := r.agentFailed(ctx, exec, CodeChildSpawnFailed,
			"the platform returned no child Run for the sub-application tool")
		return "", nil, &outcome
	}
	decision := r.classifyChildResult(ctx, nil, child, true)
	if decision.child != nil {
		outcome := r.agentWaiting(ctx, exec, childWaitingProposal(decision.child),
			"waiting_children", CodeChildSpawnFailed)
		return "", nil, &outcome
	}
	if decision.code != "" {
		outcome := r.agentFailed(ctx, exec, decision.code, decision.message)
		return "", nil, &outcome
	}
	message, err := canonicalJSONDocument(decision.output)
	if err != nil {
		outcome := r.agentFailed(ctx, exec, CodeChildResultFailed,
			"the child result cannot be encoded")
		return "", nil, &outcome
	}
	return string(message), nil, nil
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
