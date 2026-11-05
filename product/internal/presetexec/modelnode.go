package presetexec

// F04B/T086: the model node — one controlled generation, never an agent.
//
// The node is a single T041 capability call whose answer the platform validates
// itself. It has none of the agent loop's behaviour: no tools, no turns, no tool
// results, no hidden retry, no fallback it chooses itself. Availability retries
// and a compatible fallback belong to the capability proxy (F06/T045/T046), which
// keeps the operation receipt and the budget ledger; this file never calls a
// provider and never retries a call.
//
// Contract facts this file implements (D-f04b M1–M9, N-M10):
//
//   - config is exactly {model_profile_version, prompt_template, response_mode};
//     "the response schema" is the node's output_schema — there is no
//     response_schema config key (M1);
//   - `prompt_template` may only read already-bound inputs through `{{name}}`.
//     Objects/arrays/scalars are rendered as deterministic JSON, strings verbatim.
//     A placeholder whose name is not a bound input, a property chain such as
//     `{{a.b}}`, an expression or an unbalanced `{{`/`}}` is a located
//     `node_prompt_invalid` failure before any model call (M4; the DSL has no
//     expression language);
//   - the profile version is fixed and re-checked before the call. Only a passed,
//     unrevoked profile may spend a call; the capability proxy re-proves the same
//     fact and the frozen manifest membership (M3);
//   - `text` mode wraps the provider text as {text}; `json` mode publishes the
//     structured object. In both modes the *platform* validates the object against
//     the node's output_schema and a mismatch is an independent
//     `node_output_invalid` failure: the node fails, no success output artifact
//     exists, the run cannot succeed, and nothing is retried (M5, M6, N-M10).
//
// Node data artifact: the validated output object, label `data` (the run view
// already renders data artifacts by node; T083's data convention). The output
// enters the graph's `available` namespace so a later join/end can read it by
// node id, and the recovery seam reuses it on a resumed attempt without a second
// call.

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"

	"orchara/platform/internal/capabilities"
	"orchara/platform/internal/uuid"
)

// Stable model-node codes. `node_prompt_invalid` is new and documented in
// reports/f04b/T086-model-node.md; the other two make the capability's
// unhandled/unknown states explicit instead of swallowing them.
const (
	// CodeNodePromptInvalid is a prompt template outside the strict `{{name}}`
	// vocabulary over already-bound inputs.
	CodeNodePromptInvalid = "node_prompt_invalid"
	// CodeNodeCapabilityFailed is a capability answer/transport the node cannot
	// map to a more specific frozen code.
	CodeNodeCapabilityFailed = "node_capability_failed"
	// CodeNodeCapabilityUnknown is the capability proxy's `unknown` state: neither
	// success nor failure, so the node must not pretend either.
	CodeNodeCapabilityUnknown = "node_capability_unknown"
)

// maxModelPromptBytes is the capability contract's per-message content limit.
// The executor pre-checks it so an oversized prompt fails before an operation row
// exists.
const maxModelPromptBytes = 1 << 20

// modelNodeConfig is the validated closed config of exactly one model node.
type modelNodeConfig struct {
	ModelProfileVersion string
	PromptTemplate      string
	ResponseMode        string
}

// modelConfigKeys is the contract's closed config set (workflow.schema.json,
// additionalProperties:false). "response_schema" is deliberately absent: the
// response schema is node.output_schema.
var modelConfigKeys = []string{"model_profile_version", "prompt_template", "response_mode"}

// parseModelConfig re-proves the model node's sealed config on the frozen bytes.
// It is a plan refusal (bad_config), not a node failure: a definition the
// executor cannot understand must not spend a single operation.
func parseModelConfig(node *manifestNode, index int) (*modelNodeConfig, error) {
	if node == nil {
		return nil, errors.New("the model node is missing")
	}
	if len(node.Config) == 0 {
		return nil, errors.New("the model node has no config")
	}
	var raw map[string]json.RawMessage
	if err := json.Unmarshal(node.Config, &raw); err != nil {
		return nil, fmt.Errorf("the model config is not readable: %w", err)
	}
	allowed := make(map[string]bool, len(modelConfigKeys))
	for _, key := range modelConfigKeys {
		allowed[key] = true
	}
	for key := range raw {
		if !allowed[key] {
			return nil, fmt.Errorf("the model config key %q is not part of the closed set %v", key, modelConfigKeys)
		}
	}
	for _, key := range modelConfigKeys {
		if _, present := raw[key]; !present {
			return nil, fmt.Errorf("the model config has no %q", key)
		}
	}
	var profile, template, mode string
	if err := json.Unmarshal(raw["model_profile_version"], &profile); err != nil {
		return nil, errors.New("the model config model_profile_version is not a string")
	}
	if err := json.Unmarshal(raw["prompt_template"], &template); err != nil {
		return nil, errors.New("the model config prompt_template is not a string")
	}
	if err := json.Unmarshal(raw["response_mode"], &mode); err != nil {
		return nil, errors.New("the model config response_mode is not a string")
	}
	profile = strings.ToLower(strings.TrimSpace(profile))
	if !uuid.Valid(profile) {
		return nil, errors.New("the model config model_profile_version must be a UUID (never latest)")
	}
	mode = strings.TrimSpace(mode)
	if mode != "text" && mode != "json" {
		return nil, fmt.Errorf("the model config response_mode %q is not text or json", mode)
	}
	if !restrictedObjectSchema(node.OutputSchema) {
		return nil, errors.New("the model node output_schema must be an object schema with type=object")
	}
	return &modelNodeConfig{
		ModelProfileVersion: profile,
		PromptTemplate:      template,
		ResponseMode:        mode,
	}, nil
}

// renderModelPrompt renders the strict `{{name}}` template over the node's
// already-resolved bindings. Only bound input names are substituted; a string is
// inserted verbatim, every other JSON value uses its deterministic JSON form. Any
// other placeholder syntax (property chains, expressions, unbalanced braces) is a
// template refusal rather than an evaluation.
func renderModelPrompt(template string, bindings map[string]any) (string, error) {
	var builder strings.Builder
	rest := template
	for {
		open := strings.Index(rest, "{{")
		if open < 0 {
			if strings.Contains(rest, "}}") {
				return "", errors.New("the template has a closing }} without a matching {{")
			}
			builder.WriteString(rest)
			return builder.String(), nil
		}
		builder.WriteString(rest[:open])
		after := rest[open+2:]
		close := strings.Index(after, "}}")
		if close < 0 {
			return "", errors.New("the template has an unclosed {{ placeholder")
		}
		name := strings.TrimSpace(after[:close])
		if !validTemplateVariable(name) {
			return "", fmt.Errorf(
				"the placeholder {{%s}} is not a bound input name; the DSL has no expressions or property chains", name)
		}
		value, bound := bindings[name]
		if !bound {
			return "", fmt.Errorf(
				"the placeholder {{%s}} does not name a bound input of this node", name)
		}
		rendered, err := renderTemplateValue(value)
		if err != nil {
			return "", fmt.Errorf("the input %q cannot be rendered: %w", name, err)
		}
		builder.WriteString(rendered)
		rest = after[close+2:]
	}
}

// validTemplateVariable is the contract's `{{name}}` variable pattern: letters,
// digits and underscore, starting with a letter or underscore.
func validTemplateVariable(name string) bool {
	if name == "" {
		return false
	}
	for position, char := range name {
		switch {
		case char == '_':
		case char >= 'a' && char <= 'z':
		case char >= 'A' && char <= 'Z':
		case char >= '0' && char <= '9':
			if position == 0 {
				return false
			}
		default:
			return false
		}
	}
	return true
}

// renderTemplateValue renders one bound value: a string verbatim (a prompt is
// text), everything else as canonical-shape JSON.
func renderTemplateValue(value any) (string, error) {
	if text, ok := value.(string); ok {
		return text, nil
	}
	encoded, err := json.Marshal(value)
	if err != nil {
		return "", err
	}
	return string(encoded), nil
}

// runModelNode executes the model node and returns its validated output. It is
// called by the graph scheduler for one activated node.
func (r *runner) executeGraphModel(
	ctx context.Context, run *graphRun, node *graphNode,
) (executionOutcome, map[string]any, bool) {
	id := node.node.ID
	config := node.model
	if config == nil {
		return r.failGraph(ctx, run, id, CodeBadConfig,
			"the model node has no proven config"), nil, true
	}
	r.emitNode(ctx, id, "pending")
	if ctx.Err() != nil {
		return r.modelCancelled(ctx, run, node), nil, true
	}
	heartbeat, err := r.deps.Client.Heartbeat(
		ctx, r.lease.RunID, r.lease.AttemptToken, r.options.ExecutionUnitID,
	)
	if err != nil {
		return r.failGraphNodeBeforeSandbox(ctx, run, node, CodeNodeCapabilityFailed,
			"the model control heartbeat failed"), nil, true
	}
	if heartbeat.CancelRequested {
		return r.modelCancelled(ctx, run, node), nil, true
	}
	r.emitNode(ctx, id, "running")

	// 1. Resolve only the node's own bindings and render the strict template.
	bindings, err := resolveNodeBindings(node.node, run.available())
	if err != nil {
		return r.failGraphNodeBeforeSandbox(ctx, run, node, CodeNodeInputInvalid,
			err.Error()), nil, true
	}
	prompt, err := renderModelPrompt(config.PromptTemplate, bindings)
	if err != nil {
		return r.failGraphNodeBeforeSandbox(ctx, run, node, CodeNodePromptInvalid,
			(&nodeError{
				Code:    CodeNodePromptInvalid,
				Pointer: fmt.Sprintf("/nodes/%d/config/prompt_template", node.index),
				Message: err.Error(),
			}).Error()), nil, true
	}
	if len(prompt) > maxModelPromptBytes {
		return r.failGraphNodeBeforeSandbox(ctx, run, node, CodeNodeInputInvalid,
			"the rendered prompt exceeds the capability's 1 MiB message limit"), nil, true
	}

	// 2. The fixed profile must still be passed and unrevoked. The capability
	// proxy re-proves the same fact (and the frozen manifest membership) on
	// invoke; the executor refuses earlier so an unusable profile never creates
	// an operation row.
	if r.deps.Facts == nil {
		return r.failGraphNodeBeforeSandbox(ctx, run, node, capabilities.CodeModelUnavailable,
			"the executor has no capability facts source for the model node"), nil, true
	}
	fact, factErr := r.deps.Facts.ModelProfile(ctx, config.ModelProfileVersion)
	if factErr != nil || fact.ValidationState != "passed" || fact.Revoked {
		return r.failGraphNodeBeforeSandbox(ctx, run, node, capabilities.CodeModelUnavailable,
			"the model_profile_version is not a passed, unrevoked profile"), nil, true
	}

	// 3. Exactly one controlled generation. No tools, no second turn.
	params := map[string]any{
		"task": "model",
		"messages": []any{
			map[string]any{"role": "user", "content": prompt},
		},
	}
	if config.ResponseMode == "json" {
		schema, schemaErr := decodeResponseSchema(node.node.OutputSchema)
		if schemaErr != nil {
			return r.failGraphNodeBeforeSandbox(ctx, run, node, CodeBadConfig,
				"the model node output_schema is not a usable object schema"), nil, true
		}
		params["response_schema"] = schema
	}
	logicalKey := fmt.Sprintf("model:%s:1", id)
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
		code, cancelled := modelCapabilityError(invokeErr)
		if cancelled {
			return r.modelCancelled(ctx, run, node), nil, true
		}
		message := "the model call was refused by the platform: " + invokeErr.Error()
		if code == capabilities.CodeProviderResponseInvalid {
			// The provider's answer violated the node's own response schema. That
			// IS the node's independent schema failure; it is never retried and
			// never turned into another model call.
			return r.failGraphNodeBeforeSandbox(ctx, run, node, CodeNodeOutputInvalid,
				message), nil, true
		}
		return r.failGraphNodeBeforeSandbox(ctx, run, node, code, message), nil, true
	}
	if result == nil {
		return r.failGraphNodeBeforeSandbox(ctx, run, node, CodeNodeCapabilityFailed,
			"the model capability returned no result object"), nil, true
	}
	switch result.State {
	case "succeeded":
	case "failed":
		code := CodeNodeCapabilityFailed
		if rawCode, ok := result.Data["error_code"].(string); ok && safePlatformCode(rawCode) {
			code = rawCode
		}
		return r.failGraphNodeBeforeSandbox(ctx, run, node, code,
			"the replayed model operation is recorded as failed"), nil, true
	case "awaiting_approval":
		// A pure model call should never park; if a deployment ever does, the
		// node follows the existing waiting semantics instead of inventing one.
		return r.waitModelNode(ctx, run, node, operationID), nil, true
	case "unknown":
		return r.failGraphNodeBeforeSandbox(ctx, run, node, CodeNodeCapabilityUnknown,
			"the model call has an unknown provider outcome and must be reconciled"), nil, true
	default:
		return r.failGraphNodeBeforeSandbox(ctx, run, node, CodeNodeCapabilityFailed,
			"the model call returned an unsupported platform state"), nil, true
	}

	// 4. The platform — not "the JSON looks valid" — validates the output object.
	output, outputErr := modelOutput(config.ResponseMode, result.Data)
	if outputErr != nil {
		return r.failGraphNodeBeforeSandbox(ctx, run, node, CodeNodeOutputInvalid,
			outputErr.Error()), nil, true
	}
	if err := validateSchema(node.node.OutputSchema, output, "#"); err != nil {
		return r.failGraphNodeBeforeSandbox(ctx, run, node, CodeNodeOutputInvalid,
			"model node "+id+" output does not match its output_schema: "+err.Error()), nil, true
	}
	canonical, err := canonicalNodeDocument(output)
	if err != nil {
		return r.failGraphNodeBeforeSandbox(ctx, run, node, CodeNodeOutputInvalid,
			"the model node output cannot be canonicalised"), nil, true
	}

	// 5. Publish exactly one data artifact, then the terminal success fact.
	artifactID, err := r.uploadNodeArtifact(ctx, "node_output", "application/json", canonical, "artifact")
	if err != nil {
		return r.failGraphNodeAfterSandbox(ctx, run, node, CodeSandboxFailed,
			"registering the model node data failed: "+err.Error()), nil, true
	}
	if err := r.deps.Client.RecordNodeResults(ctx, r.lease.RunID, r.lease.AttemptToken,
		[]NodeResult{{NodeID: id, Kind: "data", ArtifactID: artifactID, Label: "data"}}); err != nil {
		return r.failGraphNodeAfterSandbox(ctx, run, node, CodeSandboxFailed,
			"recording the model node data failed: "+err.Error()), nil, true
	}
	r.emitNode(ctx, id, "succeeded")
	return executionOutcome{}, output, false
}

// modelCapabilityError turns a transport/capability error into a stable
// model-node code. A cancel code is reported separately because it is not a
// failure; an uncoded transport failure is the model node's own
// `node_capability_failed`, never an agent code.
func modelCapabilityError(err error) (code string, cancelled bool) {
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
	return CodeNodeCapabilityFailed, false
}

// decodeResponseSchema decodes the node output_schema into the capability
// request's response_schema value. The plan already proved the root type.
func decodeResponseSchema(raw json.RawMessage) (map[string]any, error) {
	var schema map[string]any
	if err := json.Unmarshal(raw, &schema); err != nil {
		return nil, err
	}
	if schema == nil {
		return nil, errors.New("the schema is empty")
	}
	return schema, nil
}

// modelOutput builds the node's JSON object from the capability projection. Both
// modes publish an object: text wraps {text}, json is the structured object.
func modelOutput(mode string, data map[string]any) (map[string]any, error) {
	if data == nil {
		return nil, errors.New("the model call returned no validated data")
	}
	switch mode {
	case "text":
		text, ok := data["text"].(string)
		if !ok {
			return nil, errors.New("the model call returned no text for a text node")
		}
		return map[string]any{"text": text}, nil
	case "json":
		structured, ok := data["structured"].(map[string]any)
		if !ok {
			return nil, errors.New("the model call returned no structured object for a json node")
		}
		return structured, nil
	default:
		return nil, fmt.Errorf("the model node response_mode %q is not executable", mode)
	}
}

// waitModelNode parks the attempt for a human approval instead of failing it. A
// pure model call should never reach this path; it exists so the behaviour is the
// platform's existing waiting shape (a stable synthetic checkpoint naming the
// same logical capability call the resumed attempt re-enters) rather than an
// invented state. Nothing here reports success.
func (r *runner) waitModelNode(
	ctx context.Context, run *graphRun, node *graphNode, operationID string,
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
		return r.failGraph(ctx, run, node.node.ID, CodeNodeCapabilityFailed,
			"the approval waiting result was refused: "+err.Error())
	}
	evidence := r.exitEvidence(r.heartbeatCount(), run.last, nil)
	if err := r.confirmExit(ctx, run.started, evidence); err != nil {
		return executionOutcome{outcome: "error", code: CodeNodeCapabilityFailed,
			err:     "the approval exit confirmation was refused: " + err.Error(),
			sandbox: run.report}
	}
	return executionOutcome{
		outcome:  "waiting_approval",
		code:     "approval_required",
		runState: r.readRunState(ctx),
		sandbox:  run.report,
	}
}

// modelCancelled stops the attempt without a success proposal. It mirrors the
// agent segment's cancellation: the node is marked failed, every unsettled node
// is skipped and the exit is certified.
func (r *runner) modelCancelled(
	ctx context.Context, run *graphRun, node *graphNode,
) executionOutcome {
	r.emitNode(ctx, node.node.ID, "failed")
	r.skipUnsettled(ctx, run, node.node.ID)
	evidence := r.exitEvidence(r.heartbeatCount(), run.last, nil)
	if err := r.confirmExit(ctx, run.started, evidence); err != nil {
		return executionOutcome{outcome: "cancelled", code: "cancel_requested",
			err: err.Error(), sandbox: run.report, failedStatus: true}
	}
	return executionOutcome{outcome: "cancelled", code: "cancel_requested",
		runState: r.readRunState(ctx), sandbox: run.report}
}
