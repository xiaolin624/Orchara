package presetexec

// F04A/T043: the controlled agent node.
//
// The agent is one explicit segment of the linear chain:
//
//	start -> python -> agent -> end
//
// It is not a general agent runtime. Every model turn and every tool call is one
// T041 capability operation; the server authorizes it against the sealed
// manifest, meters it and writes the ledger. The worker never talks to a provider
// and never executes a tool outside the real gVisor sandbox.
//
// The loop:
//
//  1. resolve the node's sealed `inputs` against the start/python outputs and
//     send one system message (the node instructions plus the fixed platform
//     constraints) and one user message carrying only the resolved inputs JSON;
//  2. call the model capability (logical key agent:<node>:model:<turn>);
//  3. while the model returns tool calls: validate their ids/names locally too
//     (the server already validated them), take the tool grant, run the fixed
//     preset sales_stats tool in the real sandbox with the run's linked
//     task_input, register its artifacts, report the result through
//     capability-results, and append one tool message containing only the
//     validated data JSON;
//  4. validate the final output (data.structured, else {text}) against the node
//     output_schema at a located pointer; a mismatch is node_output_invalid and
//     never a success proposal;
//  5. upload the canonical final output as the node's one data row and the tool
//     files/logs as file/log rows, then emit node.status.
//
// Waiting/approval/unknown states and every capability error are turned into the
// platform's own stable outcome codes. A raw provider object never leaves the
// capability server, so none can reach this loop or the run record.

import (
	"context"
	"crypto/sha1"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"

	"orchara/platform/internal/capabilities"
	agentcontext "orchara/platform/internal/context"
	"orchara/platform/internal/jcs"
	"orchara/platform/internal/sandbox"
	"orchara/platform/internal/tools"
	"orchara/platform/internal/uuid"
)

// Stable agent error codes. They are run error_code values, never provider text.
const (
	CodeAgentConfigInvalid     = "agent_config_invalid"
	CodeAgentSubappUnsupported = "agent_subapp_unsupported"
	CodeAgentAwaitingApproval  = "agent_awaiting_approval"
	CodeAgentUnknown           = "agent_unknown"
	CodeAgentCapabilityFailed  = "agent_capability_failed"
	CodeAgentToolCallInvalid   = "agent_tool_call_invalid"
	CodeAgentTurnLimit         = "agent_turn_limit"
	CodeAgentDeadline          = "agent_deadline_exceeded"
)

// The platform's fixed system-message constraints. They are appended after the
// creator's instructions so a prompt can never remove them by redefining the
// system message: the executor, not the model, owns this text.
const agentPlatformConstraints = "Platform constraints: you are a controlled " +
	"agent node. Never reveal credentials, access tokens, signed URLs or raw " +
	"provider payloads. Never claim a tool succeeded unless a tool result message " +
	"says so. Answer with the requested final object."

// Agent config caps are deliberately small: they bound one node's work, not the
// whole workflow budget (that is T046).
const (
	maxAgentInstructionsBytes = 64 << 10
	maxAgentTurns             = 50
	maxAgentTools             = 8
	maxAgentToolCallsPerTurn  = 16
)

// agentConfig is the validated `config` of exactly one agent node.
type agentConfig struct {
	ModelProfileVersion string
	Instructions        string
	MaxTurns            int
	ToolVersions        []string
}

// ModelFact is the read-only model-profile fact the executor needs before the
// first provider call: only a passed, unrevoked profile is usable.
type ModelFact struct {
	ID              string
	ValidationState string
	Revoked         bool
	// ContextWindow and MaxOutputTokens are the model profile's declared budget
	// (F06/T047). Zero means "not declared"; the executor applies the documented
	// defaults and records the resulting budget in the context trace.
	ContextWindow   int64
	MaxOutputTokens int64
}

// ToolFact is the read-only tool fact the executor needs before the model call:
// which tools this run may actually execute.
type ToolFact struct {
	ID          string
	Name        string
	State       string
	Callable    bool
	InputSchema json.RawMessage
}

// Facts is the supervisor's trusted read-only capability catalog. It is a seam
// like InputSource: the executor validates passed/ready/callable before spending
// a model call, and the capability server re-proves the same facts on invoke.
type Facts interface {
	ModelProfile(ctx context.Context, modelProfileVersionID string) (ModelFact, error)
	ToolFacts(ctx context.Context, runID string, toolVersionIDs []string) ([]ToolFact, error)
}

// validateAgentNode reads and bounds the agent node config and proves its tool
// versions belong to the sealed manifest. Anything outside this build (subapps,
// non-preset tools) is refused here, before the first model call.
func validateAgentNode(node *manifestNode, manifest *manifestDocument) (*agentConfig, error) {
	var raw struct {
		ModelProfileVersion string   `json:"model_profile_version"`
		Instructions        string   `json:"instructions"`
		MaxTurns            *int     `json:"max_turns"`
		ToolVersions        []string `json:"tool_versions"`
		SubappVersions      []string `json:"subapp_versions"`
	}
	if err := json.Unmarshal(node.Config, &raw); err != nil {
		return nil, &planError{code: CodeAgentConfigInvalid,
			message: "the agent node config is not the documented object"}
	}
	model := strings.ToLower(strings.TrimSpace(raw.ModelProfileVersion))
	if !uuid.Valid(model) {
		return nil, &planError{code: CodeAgentConfigInvalid,
			message: "the agent node model_profile_version must be a UUID"}
	}
	if strings.TrimSpace(raw.Instructions) == "" {
		return nil, &planError{code: CodeAgentConfigInvalid,
			message: "the agent node instructions must not be empty"}
	}
	if len(raw.Instructions) > maxAgentInstructionsBytes {
		return nil, &planError{code: CodeAgentConfigInvalid,
			message: "the agent node instructions exceed the 64 KiB limit"}
	}
	if raw.MaxTurns == nil || *raw.MaxTurns < 1 || *raw.MaxTurns > maxAgentTurns {
		return nil, &planError{code: CodeAgentConfigInvalid,
			message: fmt.Sprintf("the agent node max_turns must be between 1 and %d", maxAgentTurns)}
	}
	if len(raw.SubappVersions) != 0 {
		return nil, &planError{code: CodeAgentSubappUnsupported,
			message: "the agent node declares subapp_versions; sub-application nodes are not executable in F04A"}
	}
	if len(raw.ToolVersions) > 1 {
		// Only the fixed preset tool is executable and every declaration uses its
		// fixed name, so more than one version would be ambiguous as well as
		// unsupported.
		return nil, &planError{code: CodeAgentConfigInvalid,
			message: "this build executes at most one tool version per agent node"}
	}
	manifestTools := make(map[string]bool, len(manifest.ToolArtifacts))
	for _, artifact := range manifest.ToolArtifacts {
		manifestTools[strings.ToLower(artifact.ID)] = true
	}
	seen := make(map[string]bool, len(raw.ToolVersions))
	versions := make([]string, 0, len(raw.ToolVersions))
	for _, value := range raw.ToolVersions {
		version := strings.ToLower(strings.TrimSpace(value))
		if !uuid.Valid(version) {
			return nil, &planError{code: CodeAgentConfigInvalid,
				message: "every agent tool_versions entry must be a UUID"}
		}
		if seen[version] {
			return nil, &planError{code: CodeAgentConfigInvalid,
				message: "the agent node repeats tool version " + version}
		}
		seen[version] = true
		if !manifestTools[version] {
			return nil, &planError{code: CodeToolUnsupportedTool,
				message: "the agent node references tool " + version +
					", which is not in the sealed manifest"}
		}
		if version != PresetToolID {
			// Only the fixed preset tool is executable in this build. Refusing
			// here spends no model call and cannot be confused with a runtime
			// grant failure.
			return nil, &planError{code: CodeToolUnsupportedTool,
				message: "the agent node references tool " + version +
					"; only the fixed preset sales_stats tool is executable in this build"}
		}
		versions = append(versions, version)
	}
	if !restrictedObjectSchema(node.OutputSchema) {
		return nil, &planError{code: CodeAgentConfigInvalid,
			message: "the agent node output_schema must be an object schema with type=object"}
	}
	return &agentConfig{
		ModelProfileVersion: model,
		Instructions:        raw.Instructions,
		MaxTurns:            *raw.MaxTurns,
		ToolVersions:        versions,
	}, nil
}

// restrictedObjectSchema accepts the root shape the platform's restricted schema
// validator speaks. It is a schema shape check, not a value validation.
func restrictedObjectSchema(schema json.RawMessage) bool {
	trimmed := strings.TrimSpace(string(schema))
	if trimmed == "" || trimmed == "null" {
		return false
	}
	var document map[string]any
	if err := json.Unmarshal(schema, &document); err != nil {
		return false
	}
	kind, _ := document["type"].(string)
	return kind == "object"
}

// deterministicOperationID derives the UUIDv5 operation id from the run id and
// one logical key, so a retried attempt replays the same T041 operation instead
// of creating a second one.
func deterministicOperationID(runID, logicalKey string) string {
	namespace := sha1.Sum([]byte(runID))
	name := sha1.Sum([]byte(logicalKey))
	raw := make([]byte, 0, 20+len(namespace)+len(name))
	raw = append(raw, namespace[:]...)
	raw = append(raw, name[:]...)
	sum := sha1.Sum(raw)
	var id [16]byte
	copy(id[:], sum[:16])
	id[6] = (id[6] & 0x0f) | 0x50 // version 5
	id[8] = (id[8] & 0x3f) | 0x80 // variant 10
	dashed := hex.EncodeToString(id[:])
	return dashed[0:8] + "-" + dashed[8:12] + "-" + dashed[12:16] + "-" +
		dashed[16:20] + "-" + dashed[20:32]
}

// agentToolCall is one validated tool call from one model response.
type agentToolCall struct {
	ID        string
	Name      string
	Arguments map[string]any
}

// runAgentNode executes the agent segment and returns its validated output.
// done=true means the returned outcome is terminal and the chain stops.
func (r *runner) runAgentNode(ctx context.Context, run *linearRun) (map[string]any, executionOutcome, bool) {
	node := run.plan.agent
	config := run.plan.agentConfig
	r.emitNode(ctx, node.ID, "pending")
	r.emitNode(ctx, node.ID, "running")

	available := map[string]any{
		"input":            run.start,
		run.plan.start.ID:  run.start,
		run.plan.python.ID: run.python,
	}
	bindings, err := resolveNodeBindings(node, available)
	if err != nil {
		return nil, r.failAgent(ctx, run, CodeNodeInputInvalid, err.Error()), true
	}

	if r.deps.Facts == nil {
		return nil, r.failAgent(ctx, run, CodeAgentCapabilityFailed,
			"the executor has no capability facts source for the agent node"), true
	}
	modelFact, err := r.deps.Facts.ModelProfile(ctx, config.ModelProfileVersion)
	if err != nil || modelFact.ValidationState != "passed" || modelFact.Revoked {
		return nil, r.failAgent(ctx, run, capabilities.CodeModelUnavailable,
			"the agent model_profile_version is not a passed, unrevoked profile"), true
	}
	if len(config.ToolVersions) > 0 {
		facts, factErr := r.deps.Facts.ToolFacts(ctx, r.lease.RunID, config.ToolVersions)
		if factErr != nil {
			return nil, r.failAgent(ctx, run, CodeAgentCapabilityFailed,
				"the agent tool facts could not be read: "+factErr.Error()), true
		}
		byID := make(map[string]ToolFact, len(facts))
		for _, fact := range facts {
			byID[strings.ToLower(fact.ID)] = fact
		}
		for _, version := range config.ToolVersions {
			fact, present := byID[version]
			if !present || fact.State != "ready" || !fact.Callable {
				return nil, r.failAgent(ctx, run, capabilities.CodeToolUnavailable,
					"the agent tool version "+version+" is not ready and callable for this run"), true
			}
		}
	}

	// F06/T047: the message list is rebuilt by the authorized-context builder
	// before every model turn. The executor passes only this run's resolved
	// bindings; the builder owns the token budget and the protected facts.
	authorizedInputs := resolvedAgentInputs(run.plan, node, bindings)
	contextScope := agentContextScope(run.plan, node)
	// F09/T063: personal memory is scoped by this run's trusted owner and
	// application. The identity read is the same read-only database identity the
	// input/facts readers use; a failure degrades recall to unavailable in the
	// trace instead of guessing a scope or failing an otherwise valid run.
	memorySource := agentcontext.MemorySource(agentcontext.NoMemorySource{})
	if r.deps.Memory != nil {
		memorySource = r.deps.Memory
		if r.deps.RunIdentity == nil {
			r.logger.Printf(
				"run_id=%s: a memory source is wired without a run identity; recall stays empty",
				r.lease.RunID)
		} else if identity, identityErr := r.deps.RunIdentity.RunScope(ctx, r.lease.RunID); identityErr != nil {
			r.logger.Printf("run_id=%s: reading the memory scope failed: %v",
				r.lease.RunID, identityErr)
		} else {
			contextScope.OwnerID = identity.OwnerID
			contextScope.ApplicationID = identity.ApplicationID
		}
	}
	modelBudget := modelContextBudget(modelFact)
	authorizedTools := make([]agentcontext.DeclaredTool, 0, len(config.ToolVersions))
	declared := make([]map[string]string, 0, len(config.ToolVersions))
	for _, version := range config.ToolVersions {
		authorizedTools = append(authorizedTools, agentcontext.DeclaredTool{
			Name: PresetToolName, CapabilityVersion: version,
		})
		declared = append(declared, map[string]string{
			"name": PresetToolName, "capability_version": version,
		})
	}
	history := make([]agentcontext.HistoryMessage, 0, 4)
	contextTraces := make([]agentcontext.Trace, 0, config.MaxTurns)
	var messages []map[string]any

	fileResults := make([]NodeResult, 0, 4)
	var final map[string]any
	turns := 0
	seenCallIDs := make(map[string]bool, 4)
	for turn := 1; turn <= config.MaxTurns; turn++ {
		if stopped, outcome, done := r.agentStopped(ctx, run, node, turns); done {
			return nil, outcome, stopped
		}
		turns = turn
		built, buildErr := agentcontext.Build(ctx, agentcontext.Request{
			Scope:   contextScope,
			System:  config.Instructions + "\n\n" + agentPlatformConstraints,
			Inputs:  authorizedInputs,
			History: history,
			Tools:   authorizedTools,
			Model:   modelBudget,
			// F09/T063: the confirmed memories of this run's owner+application.
			// The builder applies the memory budget and records the sources.
			Memory: memorySource,
		})
		if buildErr != nil {
			return nil, r.failAgent(ctx, run, contextFailureCode(buildErr),
				"the authorized agent context could not be built: "+buildErr.Error()), true
		}
		messages = built.Messages
		contextTraces = append(contextTraces, built.Trace)
		params := map[string]any{
			"task":     "agent",
			"messages": messagesToAny(messages),
		}
		if len(declared) > 0 {
			tools := make([]any, 0, len(declared))
			for _, tool := range declared {
				tools = append(tools, map[string]any{
					"name": tool["name"], "capability_version": tool["capability_version"],
				})
			}
			params["tools"] = tools
		}
		// The node output_schema is the requested structured response, so the
		// capability server validates the shape before the platform does. The
		// executor still re-validates at the located node pointer below: the
		// server's projection is trusted, but the node contract is the authority.
		var responseSchema map[string]any
		if err := json.Unmarshal(node.OutputSchema, &responseSchema); err == nil &&
			len(responseSchema) > 0 {
			params["response_schema"] = responseSchema
		}
		logicalKey := fmt.Sprintf("agent:%s:model:%d", node.ID, turn)
		operationID := deterministicOperationID(r.lease.RunID, logicalKey)
		result, invokeErr := r.deps.Client.InvokeCapability(
			ctx, r.lease.RunID, r.lease.AttemptToken, capabilities.Input{
				RunID:             r.lease.RunID,
				AttemptToken:      r.lease.AttemptToken,
				OperationID:       operationID,
				LogicalKey:        logicalKey,
				CapabilityVersion: config.ModelProfileVersion,
				CapabilityKind:    "model",
				Params:            params,
			})
		if invokeErr != nil {
			if code, cancelled := agentCapabilityError(invokeErr); cancelled {
				return nil, r.agentCancelled(ctx, run, node), true
			} else {
				return nil, r.failAgent(ctx, run, code,
					"the agent model call was refused by the platform"), true
			}
		}
		switch result.State {
		case "succeeded":
		case "failed":
			code := CodeAgentCapabilityFailed
			if rawCode, ok := result.Data["error_code"].(string); ok && safePlatformCode(rawCode) {
				code = rawCode
			}
			return nil, r.failAgent(ctx, run, code,
				"the replayed agent model operation is recorded as failed"), true
		case "awaiting_approval":
			// F06/T050: a write-risk capability parks the Run. The executor stops
			// the loop, proposes the stable waiting result and exits; the human
			// decision and the single resume job belong to the platform.
			return nil, r.waitAgent(ctx, run, operationID), true
		case "unknown":
			return nil, r.failAgent(ctx, run, CodeAgentUnknown,
				"the agent model call has an unknown provider outcome and must be reconciled"), true
		default:
			return nil, r.failAgent(ctx, run, CodeAgentCapabilityFailed,
				"the agent model call returned an unsupported platform state"), true
		}

		text, structured, calls, dataErr := readAgentModelData(result.Data)
		if dataErr != nil {
			code := CodeAgentToolCallInvalid
			if result.Data == nil {
				code = CodeAgentCapabilityFailed
			}
			return nil, r.failAgent(ctx, run, code, dataErr.Error()), true
		}
		if len(calls) == 0 {
			if structured != nil {
				final = structured
			} else {
				final = map[string]any{"text": text}
			}
			break
		}
		if len(calls) > maxAgentToolCallsPerTurn {
			return nil, r.failAgent(ctx, run, CodeAgentToolCallInvalid,
				"the model returned more tool calls than this turn allows"), true
		}
		for _, call := range calls {
			if seenCallIDs[call.ID] {
				return nil, r.failAgent(ctx, run, CodeAgentToolCallInvalid,
					"the model repeated tool-call id "+call.ID), true
			}
			seenCallIDs[call.ID] = true
			if call.Name != PresetToolName || len(config.ToolVersions) == 0 {
				return nil, r.failAgent(ctx, run, CodeToolUnsupportedTool,
					"the model called tool "+call.Name+", which this agent node did not declare"), true
			}
			if stopped, outcome, done := r.agentStopped(ctx, run, node, turns); done {
				return nil, outcome, stopped
			}
			message, results, callErr, failOutcome := r.executeAgentTool(ctx, run, node, call)
			if callErr != nil {
				return nil, *failOutcome, true
			}
			fileResults = append(fileResults, results...)
			history = append(history, agentcontext.HistoryMessage{
				Role: "tool", Content: message, Relevance: turn,
				Source: agentcontext.Source{Kind: "tool", NodeID: node.ID},
			})
			if len(history)+2 >= 250 {
				return nil, r.failAgent(ctx, run, CodeAgentTurnLimit,
					"the agent conversation reached the platform message cap"), true
			}
		}
	}
	if final == nil {
		return nil, r.failAgent(ctx, run, CodeAgentTurnLimit,
			fmt.Sprintf("the agent exhausted its %d-turn limit without a final output",
				config.MaxTurns)), true
	}
	if err := validateSchema(node.OutputSchema, final, "#"); err != nil {
		return nil, r.failAgent(ctx, run, CodeNodeOutputInvalid,
			"agent node "+node.ID+" output does not match its output_schema: "+err.Error()), true
	}
	canonical, err := canonicalJSONDocument(final)
	if err != nil {
		return nil, r.failAgent(ctx, run, CodeAgentCapabilityFailed,
			"the agent final output cannot be canonicalised"), true
	}
	dataID, err := r.uploadNodeArtifact(ctx, "node_output", "application/json", canonical, "artifact")
	if err != nil {
		return nil, r.failAgent(ctx, run, CodeSandboxFailed,
			"registering the agent node data failed: "+err.Error()), true
	}
	// F06/T047: the trace is one durable file row of the agent node. It carries
	// source kinds/ids, truncation reasons, protected key paths and token counts;
	// it never carries message text, memory text or secrets.
	traceBytes, traceEncodeErr := canonicalJSONDocument(agentContextTraceDocument{
		Version: 1, NodeID: node.ID, Turns: contextTraces,
	})
	if traceEncodeErr != nil {
		return nil, r.failAgent(ctx, run, CodeAgentCapabilityFailed,
			"the agent context trace cannot be encoded"), true
	}
	traceID, traceErr := r.uploadNodeArtifact(ctx, "node_output", "application/json",
		traceBytes, "artifact")
	if traceErr != nil {
		return nil, r.failAgent(ctx, run, CodeSandboxFailed,
			"registering the agent context trace failed: "+traceErr.Error()), true
	}
	results := make([]NodeResult, 0, len(fileResults)+2)
	results = append(results, NodeResult{NodeID: node.ID, Kind: "data", ArtifactID: dataID, Label: "data"})
	results = append(results, NodeResult{
		NodeID: node.ID, Kind: "file", ArtifactID: traceID, Label: "context-trace",
	})
	results = append(results, fileResults...)
	if err := r.deps.Client.RecordNodeResults(
		ctx, r.lease.RunID, r.lease.AttemptToken, results,
	); err != nil {
		return nil, r.failAgent(ctx, run, CodeSandboxFailed,
			"recording the agent node results failed: "+err.Error()), true
	}
	r.emitNode(ctx, node.ID, "succeeded")
	return final, executionOutcome{}, false
}

// agentStopped checks cancellation before spending another model/tool call. It
// uses the protocol heartbeat (the authoritative cancel signal), never a local
// guess, and never submits success afterwards.
func (r *runner) agentStopped(
	ctx context.Context, run *linearRun, node *manifestNode, turns int,
) (stopped bool, outcome executionOutcome, done bool) {
	if ctx.Err() != nil {
		return true, r.agentCancelled(ctx, run, node), true
	}
	if deadline, err := time.Parse(time.RFC3339, r.lease.LeaseDeadline); err == nil &&
		!deadline.IsZero() && time.Now().After(deadline) {
		return true, r.failAgent(ctx, run, CodeAgentDeadline,
			"the attempt lease deadline passed before the next agent step"), true
	}
	heartbeat, err := r.deps.Client.Heartbeat(
		ctx, r.lease.RunID, r.lease.AttemptToken, r.options.ExecutionUnitID,
	)
	if err != nil {
		return true, r.failAgent(ctx, run, CodeAgentCapabilityFailed,
			"the agent control heartbeat failed"), true
	}
	if heartbeat.CancelRequested {
		return true, r.agentCancelled(ctx, run, node), true
	}
	return false, executionOutcome{}, false
}

// agentCancelled stops the chain without a success proposal.
func (r *runner) agentCancelled(
	ctx context.Context, run *linearRun, node *manifestNode,
) executionOutcome {
	if node != nil {
		r.emitNode(ctx, node.ID, "failed")
	}
	r.skipRemaining(ctx, run.plan, nodeIDOf(node))
	evidence := r.exitEvidence(r.heartbeatCount(), run.outcome, nil)
	if err := r.confirmExit(ctx, run.outcome.SandboxStarted, evidence); err != nil {
		return executionOutcome{outcome: "cancelled", code: "cancel_requested",
			err: err.Error(), sandbox: run.report, failedStatus: true}
	}
	return executionOutcome{outcome: "cancelled", code: "cancel_requested",
		runState: r.readRunState(ctx), sandbox: run.report}
}

// failAgent marks the agent node failed, skips the rest of the chain and fails
// the run with a stable platform code (never provider text).
func (r *runner) failAgent(
	ctx context.Context, run *linearRun, code, message string,
) executionOutcome {
	if run.plan.agent != nil {
		r.emitNode(ctx, run.plan.agent.ID, "failed")
	}
	r.skipRemaining(ctx, run.plan, nodeIDOf(run.plan.agent))
	return r.failAfterStart(ctx, code, message, run.outcome,
		r.exitEvidence(r.heartbeatCount(), run.outcome, nil))
}

// waitAgent parks the attempt for a human approval instead of failing the Run.
//
// The proposal is the F03/T035 waiting shape: suggested_state=waiting_approval,
// the stable synthetic checkpoint `capability:<operation_id>` (the same logical
// capability call the resumed attempt re-enters) and error_code=approval_required.
// The executor then certifies its exit; only after that fact may the single
// resume job exist. Nothing here reports success.
func (r *runner) waitAgent(
	ctx context.Context, run *linearRun, operationID string,
) executionOutcome {
	proposal := Proposal{
		EventID:        uuid.NewString(),
		SuggestedState: "waiting_approval",
		CheckpointID:   "capability:" + operationID,
		ErrorCode:      "approval_required",
	}
	if err := r.deps.Client.SubmitResult(
		ctx, r.lease.RunID, r.lease.AttemptToken, proposal,
	); err != nil {
		return r.failAgent(ctx, run, CodeAgentCapabilityFailed,
			"the approval waiting result was refused: "+err.Error())
	}
	evidence := r.exitEvidence(r.heartbeatCount(), run.outcome, nil)
	if err := r.confirmExit(ctx, run.outcome.SandboxStarted, evidence); err != nil {
		return executionOutcome{outcome: "error", code: CodeAgentCapabilityFailed,
			err:     "the approval exit confirmation was refused: " + err.Error(),
			sandbox: sandboxReportOf(run.outcome)}
	}
	return executionOutcome{
		outcome:  "waiting_approval",
		code:     "approval_required",
		runState: r.readRunState(ctx),
		sandbox:  sandboxReportOf(run.outcome),
	}
}

// readAgentModelData decodes the platform's validated model projection.
func readAgentModelData(data map[string]any) (string, map[string]any, []agentToolCall, error) {
	if data == nil {
		return "", nil, nil, errors.New("the agent model call returned no validated data")
	}
	text, _ := data["text"].(string)
	var structured map[string]any
	if raw, present := data["structured"]; present {
		value, ok := raw.(map[string]any)
		if !ok {
			return "", nil, nil, errors.New("the agent model structured output is not an object")
		}
		structured = value
	}
	rawCalls, present := data["tool_calls"]
	if !present {
		return text, structured, nil, nil
	}
	items, ok := rawCalls.([]any)
	if !ok {
		return "", nil, nil, errors.New("the agent model tool_calls is not an array")
	}
	calls := make([]agentToolCall, 0, len(items))
	seen := make(map[string]bool, len(items))
	for _, item := range items {
		call, ok := item.(map[string]any)
		if !ok {
			return "", nil, nil, errors.New("a tool call is not an object")
		}
		id, _ := call["id"].(string)
		if strings.TrimSpace(id) == "" || len(id) > 128 || !printableKey(id) {
			return "", nil, nil, errors.New("a tool call has an empty or invalid id")
		}
		if seen[id] {
			return "", nil, nil, errors.New("a tool call repeats an id in the same response")
		}
		seen[id] = true
		name, _ := call["name"].(string)
		if strings.TrimSpace(name) == "" {
			return "", nil, nil, errors.New("a tool call has no name")
		}
		arguments, ok := call["arguments"].(map[string]any)
		if !ok {
			return "", nil, nil, errors.New("a tool call arguments value is not an object")
		}
		calls = append(calls, agentToolCall{ID: id, Name: name, Arguments: arguments})
	}
	return text, structured, calls, nil
}

// printableKey keeps a tool-call id usable as a logical-key suffix.
func printableKey(value string) bool {
	for _, char := range value {
		if char < 0x20 || char == 0x7f {
			return false
		}
	}
	return true
}

// executeAgentTool takes the grant, runs the fixed preset tool in the real
// sandbox and reports the validated result. The returned message is the only
// thing the model sees; raw stdout/stderr never leaves the executor.
func (r *runner) executeAgentTool(
	ctx context.Context, run *linearRun, node *manifestNode, call agentToolCall,
) (string, []NodeResult, error, *executionOutcome) {
	version := run.plan.agentConfig.ToolVersions[0]
	logicalKey := fmt.Sprintf("agent:%s:tool:%s", node.ID, call.ID)
	if len(logicalKey) > 200 {
		outcome := r.failAgent(ctx, run, CodeAgentToolCallInvalid,
			"the tool-call logical key exceeds the platform limit")
		return "", nil, errors.New("tool logical key too long"), &outcome
	}
	operationID := deterministicOperationID(r.lease.RunID, logicalKey)
	grantResult, err := r.deps.Client.InvokeCapability(
		ctx, r.lease.RunID, r.lease.AttemptToken, capabilities.Input{
			RunID:             r.lease.RunID,
			AttemptToken:      r.lease.AttemptToken,
			OperationID:       operationID,
			LogicalKey:        logicalKey,
			CapabilityVersion: version,
			CapabilityKind:    "tool",
			Params:            call.Arguments,
		})
	if err != nil {
		if code, cancelled := agentCapabilityError(err); cancelled {
			outcome := r.agentCancelled(ctx, run, node)
			return "", nil, err, &outcome
		} else {
			outcome := r.failAgent(ctx, run, code, "the agent tool call was refused by the platform")
			return "", nil, err, &outcome
		}
	}
	switch grantResult.State {
	case "dispatching":
		// The fresh grant: execute the tool below.
	case "succeeded":
		// A retried attempt replaying an operation that already succeeded: the
		// sealed receipt data is the validated tool result, and nothing is
		// executed a second time.
		value := grantResult.Data
		if value == nil {
			outcome := r.failAgent(ctx, run, CodeAgentCapabilityFailed,
				"the replayed tool operation carries no result data")
			return "", nil, errors.New("tool replay without data"), &outcome
		}
		message, err := canonicalJSONDocument(value)
		if err != nil {
			outcome := r.failAgent(ctx, run, CodeAgentCapabilityFailed,
				"the replayed tool result cannot be encoded")
			return "", nil, err, &outcome
		}
		return string(message), nil, nil, nil
	case "failed":
		code := CodeAgentCapabilityFailed
		if rawCode, ok := grantResult.Data["error_code"].(string); ok && safePlatformCode(rawCode) {
			code = rawCode
		}
		outcome := r.failAgent(ctx, run, code,
			"the replayed tool operation is recorded as failed")
		return "", nil, errors.New("tool operation failed"), &outcome
	case "awaiting_approval":
		outcome := r.waitAgent(ctx, run, operationID)
		return "", nil, errors.New("tool awaiting approval"), &outcome
	case "unknown":
		outcome := r.failAgent(ctx, run, CodeAgentUnknown,
			"the agent tool call has an unknown provider outcome and must be reconciled")
		return "", nil, errors.New("tool unknown state"), &outcome
	default:
		outcome := r.failAgent(ctx, run, CodeAgentCapabilityFailed,
			"the agent tool grant did not return a dispatching operation")
		return "", nil, errors.New("tool grant not dispatching"), &outcome
	}
	grant, _ := grantResult.Data["grant"].(map[string]any)
	grantOperation, _ := grant["operation_id"].(string)
	if !uuid.Valid(grantOperation) || grantOperation != operationID {
		outcome := r.failAgent(ctx, run, CodeAgentCapabilityFailed,
			"the agent tool grant does not name the requested operation")
		return "", nil, errors.New("grant mismatch"), &outcome
	}

	results, dataArtifactID, message, failure := r.runAgentToolSandbox(ctx, run, node, call)
	if failure != nil {
		if failure.code == "cancel_requested" {
			outcome := r.agentCancelled(ctx, run, node)
			return "", nil, errors.New(failure.message), &outcome
		}
		// A tool failure marks the granted operation failed before the node is
		// failed, so the ledger never shows a dispatching orphan.
		if _, reportErr := r.deps.Client.ReportCapabilityResult(
			ctx, r.lease.RunID, r.lease.AttemptToken, capabilities.ResultReport{
				AttemptToken: r.lease.AttemptToken,
				OperationID:  grantOperation,
				State:        "failed",
				ErrorCode:    failure.code,
			}); reportErr != nil {
			r.logger.Printf("run_id=%s: reporting the failed tool operation failed: %v",
				r.lease.RunID, reportErr)
		}
		outcome := r.failAgent(ctx, run, failure.code, failure.message)
		return "", nil, errors.New(failure.message), &outcome
	}
	if _, err := r.deps.Client.ReportCapabilityResult(
		ctx, r.lease.RunID, r.lease.AttemptToken, capabilities.ResultReport{
			AttemptToken:     r.lease.AttemptToken,
			OperationID:      grantOperation,
			State:            "succeeded",
			ResultArtifactID: dataArtifactID,
		}); err != nil {
		outcome := r.failAgent(ctx, run, CodeAgentCapabilityFailed,
			"reporting the agent tool result failed")
		return "", nil, err, &outcome
	}
	return message, results, nil, nil
}

type agentToolFailure struct {
	code    string
	message string
}

// runAgentToolSandbox executes the fixed preset tool for one agent tool call.
// dataArtifactID is the validated data JSON the capability result points at; the
// rows are only the tool's files/logs (the agent's own final output is its one
// data row, written by runAgentNode).
func (r *runner) runAgentToolSandbox(
	ctx context.Context, run *linearRun, node *manifestNode, call agentToolCall,
) ([]NodeResult, string, string, *agentToolFailure) {
	var linked *InputArtifact
	for index := range run.inputs {
		if run.inputs[index].Purpose == "task_input" {
			if linked != nil {
				return nil, "", "", &agentToolFailure{CodeNodeInputInvalid,
					"the run links more than one task_input artifact; the preset tool needs exactly one"}
			}
			linked = &run.inputs[index]
		}
	}
	if linked == nil {
		return nil, "", "", &agentToolFailure{CodeToolInputInvalid,
			"the run has no linked task_input artifact for the preset tool"}
	}
	inputBytes, err := r.downloadInput(ctx, linked)
	if err != nil {
		return nil, "", "", &agentToolFailure{CodeInputIntegrity, err.Error()}
	}
	inputName, err := sandbox.InputNameForMediaType(linked.MediaType)
	if err != nil {
		return nil, "", "", &agentToolFailure{CodeToolInputInvalid, err.Error()}
	}
	inputDocument, err := buildInputDocument(linked, inputName)
	if err != nil {
		return nil, "", "", &agentToolFailure{CodeToolInputInvalid, err.Error()}
	}
	unit := agentSandboxUnit(r.options.ExecutionUnitID, call.ID)
	outcome, sandboxErr := r.runSandbox(ctx, sandbox.Request{
		Program:         sandbox.ProgramTool,
		MediaType:       strings.ToLower(strings.TrimSpace(linked.MediaType)),
		Input:           inputBytes,
		InputJSON:       inputDocument,
		ExecutionUnitID: unit,
	}, r.heartbeatControl())
	run.outcome = outcome
	run.report = sandboxReportOf(outcome)
	switch {
	case errors.Is(sandboxErr, sandbox.ErrCancelled):
		return nil, "", "", &agentToolFailure{"cancel_requested", "the preset tool was cancelled"}
	case errors.Is(sandboxErr, sandbox.ErrTimeout):
		return nil, "", "", &agentToolFailure{CodeToolTimeout, "the preset tool exceeded its sandbox timeout"}
	case sandboxErr != nil:
		return nil, "", "", &agentToolFailure{CodeSandboxFailed, "the preset tool sandbox failed"}
	}
	switch outcome.ExitCode {
	case 0:
	case 3:
		rejection, rejectionErr := tools.ParseRejectionPayload(outcome.OutputJSON)
		if rejectionErr != nil {
			return nil, "", "", &agentToolFailure{CodeToolOutputInvalid, "the tool rejection document is unreadable"}
		}
		return nil, "", "", &agentToolFailure{rejection.Code, rejection.Message}
	default:
		return nil, "", "", &agentToolFailure{CodeToolCrash,
			"the preset tool exited with code " + fmt.Sprint(outcome.ExitCode)}
	}
	data, names, err := sandbox.ParseOutputDocument(outcome.OutputJSON)
	if err != nil {
		return nil, "", "", &agentToolFailure{CodeToolOutputInvalid, err.Error()}
	}
	collected := make(map[string][]byte, len(outcome.Artifacts))
	for _, artifact := range outcome.Artifacts {
		collected[artifact.Name] = artifact.Content
	}
	admitted, err := sandbox.AdmitArtifacts(names, collected)
	if err != nil {
		return nil, "", "", &agentToolFailure{CodeToolArtifactInvalid, err.Error()}
	}
	value, err := decodeJSONDocument(data)
	if err != nil {
		return nil, "", "", &agentToolFailure{CodeToolOutputInvalid, "output.json.data is not a JSON document"}
	}
	if _, ok := value.(map[string]any); !ok {
		return nil, "", "", &agentToolFailure{CodeToolOutputInvalid, "output.json.data is not an object"}
	}
	canonical, err := jcs.CanonicalizeBytes(data)
	if err != nil {
		return nil, "", "", &agentToolFailure{CodeToolOutputInvalid, "output.json.data cannot be canonicalized"}
	}
	results := make([]NodeResult, 0, len(admitted)+1)
	dataID, err := r.uploadNodeArtifact(ctx, "node_output", "application/json", canonical, "artifact")
	if err != nil {
		return nil, "", "", &agentToolFailure{CodeSandboxFailed,
			"registering the tool result failed: " + err.Error()}
	}
	for _, artifact := range admitted {
		artifactID, uploadErr := r.uploadNodeArtifact(
			ctx, "node_output", mediaTypeForArtifact(artifact.Name), artifact.Content, "artifact",
		)
		if uploadErr != nil {
			return nil, "", "", &agentToolFailure{CodeSandboxFailed,
				"registering the tool file " + artifact.Name + " failed"}
		}
		results = append(results, NodeResult{
			NodeID: node.ID, Kind: "file", ArtifactID: artifactID, Label: artifact.Name,
		})
	}
	logContent := buildLog(outcome.Stdout, outcome.Stderr, r.options.MaxLogBytes)
	if logID, logErr := r.uploadNodeArtifact(ctx, "log", "text/plain", logContent, "log"); logErr == nil {
		results = append(results, NodeResult{
			NodeID: node.ID, Kind: "log", ArtifactID: logID, Label: "tool-log-" + call.ID,
		})
	} else {
		r.logger.Printf("run_id=%s: uploading the agent tool log failed: %v", r.lease.RunID, logErr)
	}
	messageBytes, err := jcs.CanonicalizeBytes(data)
	if err != nil {
		return nil, "", "", &agentToolFailure{CodeToolOutputInvalid, "the tool result cannot be encoded"}
	}
	return results, dataID, string(messageBytes), nil
}

// agentSandboxUnit derives a distinct runsc unit id for each agent tool call; the
// attempt's own execution unit id is already used by the python node.
func agentSandboxUnit(executionUnitID, callID string) string {
	sum := sha1.Sum([]byte(callID))
	suffix := "-a" + hex.EncodeToString(sum[:4])
	base := executionUnitID
	if len(base)+len(suffix) > 48 {
		base = base[:48-len(suffix)]
	}
	return base + suffix
}

// agentCapabilityError turns a transport/capability error into a stable platform
// code. A cancel code is reported separately because it is not a failure.
func agentCapabilityError(err error) (code string, cancelled bool) {
	var coded interface{ ErrorCode() string }
	if errors.As(err, &coded) {
		candidate := strings.ToLower(strings.TrimSpace(coded.ErrorCode()))
		if safePlatformCode(candidate) {
			switch candidate {
			case capabilities.CodeRunCancelled, "cancel_requested":
				return "cancel_requested", true
			default:
				return candidate, false
			}
		}
	}
	return CodeAgentCapabilityFailed, false
}

func safePlatformCode(value string) bool {
	if value == "" || len(value) > 64 {
		return false
	}
	for _, char := range value {
		if (char < 'a' || char > 'z') && (char < '0' || char > '9') && char != '_' {
			return false
		}
	}
	return true
}

// messagesToAny converts the typed message list for JSON params.
func messagesToAny(messages []map[string]any) []any {
	items := make([]any, 0, len(messages))
	for _, message := range messages {
		items = append(items, message)
	}
	return items
}

// canonicalJSONDocument renders one decoded JSON value as RFC 8785 bytes.
func canonicalJSONDocument(value any) ([]byte, error) {
	raw, err := json.Marshal(value)
	if err != nil {
		return nil, err
	}
	return jcs.CanonicalizeBytes(raw)
}
