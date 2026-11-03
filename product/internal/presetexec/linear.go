package presetexec

// F04A/T042: execution of one explicit linear start -> python -> end chain.
//
// The chain is not a general workflow engine: it is the smallest honest path that
// lets a creator debug a fixed python node and see its data, files and logs.
// Each node is a real transition:
//
//	start   read the run's sealed input JSON (or the legacy single task_input),
//	        validate it against definition.input_schema, and publish the
//	        validated object plus the linked artifact's server facts
//	python  resolve the node's bindings, download the authorized input through
//	        the run-scoped signed link, run the fixed preset tool in the real
//	        gVisor sandbox, validate output.json.data against the node schema,
//	        admit each claimed artifact, and register data/files/log separately
//	end     map definition.output_schema from the upstream outputs, validate the
//	        final object, seal it as final_output, and propose the result
//
// Raw sandbox output is never an authorized artifact: every byte the run exposes
// was uploaded, completed, activated by the finalize guard and registered through
// the server's own run-scoped path first. The node result rows are reported only
// after that registration succeeded.

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"

	"orchara/platform/internal/jcs"
	"orchara/platform/internal/sandbox"
	"orchara/platform/internal/tools"
	"orchara/platform/internal/uuid"
)

// linearRun is one linear attempt's state.
type linearRun struct {
	plan    *linearPlan
	inputs  []InputArtifact
	start   map[string]any
	python  map[string]any
	outcome sandbox.Outcome
	report  *SandboxReport
}

// executeLinear runs the proven chain and returns the attempt outcome.
func (r *runner) executeLinear(ctx context.Context, plan *linearPlan) executionOutcome {
	linked, err := r.deps.Inputs.Inputs(ctx, r.lease.RunID)
	if err != nil {
		return r.failLinearBeforeSandbox(ctx, plan, CodeToolInputInvalid,
			"the run's linked inputs could not be resolved: "+err.Error(), plan.start)
	}
	run := &linearRun{plan: plan, inputs: linked}

	startOutput, startErr := r.runStartNode(ctx, run)
	if startErr != nil {
		code := CodeNodeInputInvalid
		if errors.Is(startErr, errRunInputUnavailable) {
			code = CodeToolInputInvalid
		}
		return r.failLinearBeforeSandbox(ctx, plan, code, startErr.Error(), plan.start)
	}
	run.start = startOutput

	pythonOutput, pythonOutcome, done := r.runPythonNode(ctx, run)
	if done {
		return pythonOutcome
	}
	run.python = pythonOutput
	return r.runEndNode(ctx, run)
}

// errRunInputUnavailable marks a transport-level failure to read the sealed run
// input (the graph itself is fine).
var errRunInputUnavailable = errors.New("the run input is unavailable")

// runStartNode publishes the validated run input and, when exactly one
// CSV/XLSX task_input artifact is linked, its server facts under the documented
// `artifact` key. Nothing from the client is trusted: the facts come from the
// database row, not from input JSON.
func (r *runner) runStartNode(ctx context.Context, run *linearRun) (map[string]any, error) {
	node := run.plan.start
	r.emitNode(ctx, node.ID, "pending")
	r.emitNode(ctx, node.ID, "running")

	lease := r.lease
	if strings.TrimSpace(lease.InputURL) == "" {
		return nil, fmt.Errorf("%w: the lease carries no run input URL", errRunInputUnavailable)
	}
	payload, err := r.deps.Client.Fetch(ctx, lease.InputURL)
	if err != nil {
		return nil, fmt.Errorf("%w: %v", errRunInputUnavailable, err)
	}
	if len(payload) == 0 || len(payload) > MaxInputBytes {
		return nil, &locatedError{Pointer: "#", Message: "the run input is empty or larger than the cap"}
	}
	value, err := decodeJSONDocument(payload)
	if err != nil {
		return nil, &locatedError{Pointer: "#", Message: "the run input is not valid JSON"}
	}
	object, ok := value.(map[string]any)
	if !ok {
		return nil, &locatedError{Pointer: "#", Message: "the run input must be a JSON object"}
	}
	if err := validateSchema(run.plan.definition.InputSchema, object, "#"); err != nil {
		return nil, &locatedError{Pointer: "#", Message: "input_schema: " + err.Error()}
	}

	taskInputs := make([]InputArtifact, 0, 1)
	for _, input := range run.inputs {
		if input.Purpose == "task_input" {
			taskInputs = append(taskInputs, input)
		}
	}
	switch len(taskInputs) {
	case 0:
		// A chain may run without a caller artifact (for example a literal-only
		// python node); there is simply no `artifact` fact to publish.
	case 1:
		facts := taskInputs[0]
		object["artifact"] = map[string]any{
			"artifact_id": facts.ID,
			"media_type":  facts.MediaType,
			"sha256":      facts.SHA256,
			"size_bytes":  float64(facts.SizeBytes),
		}
	default:
		return nil, &locatedError{Pointer: "#/artifact",
			Message: fmt.Sprintf("the run links %d task_input artifacts; at most one may be bound",
				len(taskInputs))}
	}
	r.emitNode(ctx, node.ID, "succeeded")
	return object, nil
}

// runPythonNode resolves the node bindings, runs the fixed tool, validates its
// output and registers data/files/log. done=true means the returned outcome is
// terminal and the chain stops.
func (r *runner) runPythonNode(
	ctx context.Context, run *linearRun,
) (map[string]any, executionOutcome, bool) {
	node := run.plan.python
	r.emitNode(ctx, node.ID, "pending")
	r.emitNode(ctx, node.ID, "running")

	available := map[string]any{
		"input":           run.start,
		run.plan.start.ID: run.start,
	}
	bindings, err := resolveNodeBindings(node, available)
	if err != nil {
		return nil, r.failLinearBeforeSandbox(ctx, run.plan, CodeNodeInputInvalid, err.Error(), node), true
	}
	input, err := r.bindPythonFile(ctx, run, bindings)
	if err != nil {
		return nil, r.failLinearBeforeSandbox(ctx, run.plan, CodeNodeInputInvalid, err.Error(), node), true
	}

	inputBytes, err := r.downloadInput(ctx, input)
	if err != nil {
		return nil, r.failLinearBeforeSandbox(ctx, run.plan, CodeInputIntegrity, err.Error(), node), true
	}
	inputName, err := sandbox.InputNameForMediaType(input.MediaType)
	if err != nil {
		return nil, r.failLinearBeforeSandbox(ctx, run.plan, CodeToolInputInvalid, err.Error(), node), true
	}
	inputDocument, err := buildInputDocument(input, inputName)
	if err != nil {
		return nil, r.failLinearBeforeSandbox(ctx, run.plan, CodeToolInputInvalid, err.Error(), node), true
	}

	outcome, sandboxErr := r.runSandbox(ctx, sandbox.Request{
		Program:         sandbox.ProgramTool,
		MediaType:       strings.ToLower(strings.TrimSpace(input.MediaType)),
		Input:           inputBytes,
		InputJSON:       inputDocument,
		ExecutionUnitID: r.options.ExecutionUnitID,
	}, r.heartbeatControl())
	run.outcome = outcome
	run.report = sandboxReportOf(outcome)
	evidence := r.exitEvidence(r.heartbeatCount(), outcome, sandboxErr)

	switch {
	case errors.Is(sandboxErr, sandbox.ErrCancelled):
		r.emitNode(ctx, node.ID, "failed")
		r.skipRemaining(ctx, run.plan, node.ID)
		if err := r.confirmExit(ctx, outcome.SandboxStarted, evidence); err != nil {
			return nil, executionOutcome{outcome: "cancelled", code: "cancel_requested",
				err: err.Error(), sandbox: run.report, failedStatus: true}, true
		}
		return nil, executionOutcome{outcome: "cancelled", code: "cancel_requested",
			runState: r.readRunState(ctx), sandbox: run.report}, true

	case errors.Is(sandboxErr, sandbox.ErrHeartbeat):
		r.emitNode(ctx, node.ID, "failed")
		r.skipRemaining(ctx, run.plan, node.ID)
		return nil, executionOutcome{outcome: "error", code: CodeSandboxFailed,
			err:     "the attempt lease was lost while the sandbox ran: " + errString(sandboxErr),
			sandbox: run.report, failedStatus: true}, true

	case errors.Is(sandboxErr, sandbox.ErrTimeout):
		r.emitNode(ctx, node.ID, "failed")
		r.skipRemaining(ctx, run.plan, node.ID)
		return nil, r.failRun(ctx, CodeToolTimeout,
			"the preset tool exceeded its sandbox timeout", outcome, sandboxErr, evidence, nil, input), true

	case errors.Is(sandboxErr, sandbox.ErrRootfsUnavailable),
		errors.Is(sandboxErr, sandbox.ErrToolUnavailable),
		errors.Is(sandboxErr, sandbox.ErrRunnerUnavailable),
		errors.Is(sandboxErr, sandbox.ErrSandboxRefused),
		errors.Is(sandboxErr, sandbox.ErrDriverReport),
		errors.Is(sandboxErr, sandbox.ErrDigestMismatch):
		r.emitNode(ctx, node.ID, "failed")
		r.skipRemaining(ctx, run.plan, node.ID)
		return nil, r.failRun(ctx, CodeSandboxUnavailable,
			"the sandbox could not be started or trusted", outcome, sandboxErr, evidence, nil, input), true

	case errors.Is(sandboxErr, sandbox.ErrOrphan):
		r.emitNode(ctx, node.ID, "failed")
		r.skipRemaining(ctx, run.plan, node.ID)
		return nil, r.failRun(ctx, CodeSandboxOrphan,
			"the sandbox cleanup could not prove that every process exited", outcome,
			sandboxErr, evidence, nil, input), true

	case sandboxErr != nil:
		r.emitNode(ctx, node.ID, "failed")
		r.skipRemaining(ctx, run.plan, node.ID)
		return nil, r.failRun(ctx, CodeSandboxFailed, "the sandbox failed", outcome,
			sandboxErr, evidence, nil, input), true
	}

	switch outcome.ExitCode {
	case 0:
	case 3:
		rejection, rejectionErr := tools.ParseRejectionPayload(outcome.OutputJSON)
		if rejectionErr != nil {
			r.emitNode(ctx, node.ID, "failed")
			r.skipRemaining(ctx, run.plan, node.ID)
			return nil, r.failRun(ctx, CodeToolOutputInvalid,
				"the tool rejected the input with an unreadable document", outcome,
				rejectionErr, evidence, nil, input), true
		}
		diagnostics := diagnosticsDocument(rejection.Code, rejection.Message, rejection.Fix,
			rejectionLocation(rejection), input)
		r.emitNode(ctx, node.ID, "failed")
		r.skipRemaining(ctx, run.plan, node.ID)
		return nil, r.failRun(ctx, rejection.Code, rejection.Message, outcome, nil,
			evidence, diagnostics, input), true
	default:
		r.emitNode(ctx, node.ID, "failed")
		r.skipRemaining(ctx, run.plan, node.ID)
		return nil, r.failRun(ctx, CodeToolCrash,
			"the preset tool exited with code "+fmt.Sprint(outcome.ExitCode),
			outcome, nil, evidence, nil, input), true
	}

	data, names, err := sandbox.ParseOutputDocument(outcome.OutputJSON)
	if err != nil {
		return nil, r.pythonFailure(ctx, run, CodeToolOutputInvalid, err.Error(), outcome, err), true
	}
	collected := make(map[string][]byte, len(outcome.Artifacts))
	for _, artifact := range outcome.Artifacts {
		collected[artifact.Name] = artifact.Content
	}
	admitted, err := sandbox.AdmitArtifacts(names, collected)
	if err != nil {
		return nil, r.pythonFailure(ctx, run, CodeToolArtifactInvalid, err.Error(), outcome, err), true
	}
	dataValue, err := decodeJSONDocument(data)
	if err != nil {
		return nil, r.pythonFailure(ctx, run, CodeToolOutputInvalid,
			"output.json.data is not a JSON document", outcome, err), true
	}
	dataObject, ok := dataValue.(map[string]any)
	if !ok {
		return nil, r.pythonFailure(ctx, run, CodeToolOutputInvalid,
			"output.json.data is not an object", outcome, nil), true
	}
	if err := validateSchema(node.OutputSchema, dataObject, "#/data"); err != nil {
		return nil, r.pythonFailure(ctx, run, CodeNodeOutputInvalid,
			"output.json.data does not match the node output_schema: "+err.Error(), outcome, err), true
	}
	canonical, err := jcs.CanonicalizeBytes(data)
	if err != nil {
		return nil, r.pythonFailure(ctx, run, CodeToolOutputInvalid,
			"output.json.data cannot be canonicalized", outcome, err), true
	}

	results := make([]NodeResult, 0, len(admitted)+2)
	dataID, err := r.uploadNodeArtifact(ctx, "node_output", "application/json", canonical, "artifact")
	if err != nil {
		return nil, r.pythonFailure(ctx, run, CodeSandboxFailed,
			"registering output.json.data failed: "+err.Error(), outcome, err), true
	}
	results = append(results, NodeResult{NodeID: node.ID, Kind: "data", ArtifactID: dataID, Label: "data"})

	for _, artifact := range admitted {
		artifactID, uploadErr := r.uploadNodeArtifact(
			ctx, "node_output", mediaTypeForArtifact(artifact.Name), artifact.Content, "artifact",
		)
		if uploadErr != nil {
			return nil, r.pythonFailure(ctx, run, CodeSandboxFailed,
				"registering the tool artifact "+artifact.Name+" failed: "+uploadErr.Error(),
				outcome, uploadErr), true
		}
		results = append(results, NodeResult{
			NodeID: node.ID, Kind: "file", ArtifactID: artifactID, Label: artifact.Name,
		})
	}

	// stdout/stderr is a capped log, never protocol output and never run data.
	logContent := buildLog(outcome.Stdout, outcome.Stderr, r.options.MaxLogBytes)
	if logID, logErr := r.uploadNodeArtifact(ctx, "log", "text/plain", logContent, "log"); logErr == nil {
		results = append(results, NodeResult{
			NodeID: node.ID, Kind: "log", ArtifactID: logID, Label: "log",
		})
	} else {
		// The log is operator evidence, not run data: a storage failure here is
		// recorded but must not replace the node's real success.
		r.logger.Printf("run_id=%s: uploading the sandbox log failed: %v", r.lease.RunID, logErr)
	}

	if err := r.deps.Client.RecordNodeResults(
		ctx, r.lease.RunID, r.lease.AttemptToken, results,
	); err != nil {
		return nil, r.pythonFailure(ctx, run, CodeSandboxFailed,
			"recording the node results failed: "+err.Error(), outcome, err), true
	}
	r.emitNode(ctx, node.ID, "succeeded")
	return dataObject, executionOutcome{}, false
}

// pythonFailure marks the node failed, skips the rest of the chain and produces
// the failed-run outcome after the sandbox already ran.
func (r *runner) pythonFailure(
	ctx context.Context, run *linearRun, code, message string,
	outcome sandbox.Outcome, err error,
) executionOutcome {
	r.emitNode(ctx, run.plan.python.ID, "failed")
	r.skipRemaining(ctx, run.plan, run.plan.python.ID)
	return r.failRun(ctx, code, message, outcome, err,
		r.exitEvidence(r.heartbeatCount(), outcome, nil), nil, nil)
}

// bindPythonFile resolves the python node's `file` binding to the linked input
// artifact's server facts. A path, a bare artifact id or a mismatching fact is a
// located node_input_invalid failure; the binding can never name a client path.
func (r *runner) bindPythonFile(
	ctx context.Context, run *linearRun, bindings map[string]any,
) (*InputArtifact, error) {
	raw, found := bindings["file"]
	if !found {
		return nil, &locatedError{Pointer: "file",
			Message: "the python node has no `file` input binding"}
	}
	facts, ok := raw.(map[string]any)
	if !ok {
		return nil, &locatedError{Pointer: "file",
			Message: "the `file` binding must resolve to artifact facts (artifact_id/media_type), not a path or id"}
	}
	artifactID, _ := facts["artifact_id"].(string)
	mediaType, _ := facts["media_type"].(string)
	if !uuid.Valid(strings.TrimSpace(artifactID)) || strings.TrimSpace(mediaType) == "" {
		return nil, &locatedError{Pointer: "file",
			Message: "the `file` binding lacks artifact_id/media_type"}
	}
	var linked *InputArtifact
	for index := range run.inputs {
		if run.inputs[index].ID == artifactID {
			linked = &run.inputs[index]
			break
		}
	}
	if linked == nil {
		return nil, &locatedError{Pointer: "file/artifact_id",
			Message: "the bound artifact is not one of this run's linked inputs"}
	}
	if !strings.EqualFold(strings.TrimSpace(mediaType), linked.MediaType) {
		return nil, &locatedError{Pointer: "file/media_type",
			Message: "the bound media_type does not match the linked artifact"}
	}
	if rawSHA, present := facts["sha256"]; present {
		sha, ok := rawSHA.(string)
		if !ok || !strings.EqualFold(sha, linked.SHA256) {
			return nil, &locatedError{Pointer: "file/sha256",
				Message: "the bound sha256 does not match the linked artifact"}
		}
	}
	if rawSize, present := facts["size_bytes"]; present {
		size, ok := rawSize.(float64)
		if !ok || int64(size) != linked.SizeBytes {
			return nil, &locatedError{Pointer: "file/size_bytes",
				Message: "the bound size_bytes does not match the linked artifact"}
		}
	}
	media := strings.ToLower(strings.TrimSpace(linked.MediaType))
	if media != "text/csv" &&
		media != "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" {
		return nil, &locatedError{Pointer: "file/media_type",
			Message: "the fixed preset tool accepts text/csv or XLSX input only"}
	}
	return linked, nil
}

// runEndNode maps the definition output from upstream outputs, validates it,
// seals it as final_output and proposes the result.
func (r *runner) runEndNode(ctx context.Context, run *linearRun) executionOutcome {
	node := run.plan.end
	r.emitNode(ctx, node.ID, "pending")
	r.emitNode(ctx, node.ID, "running")

	available := map[string]any{
		"input":            run.start,
		run.plan.start.ID:  run.start,
		run.plan.python.ID: run.python,
	}
	bindings, err := resolveNodeBindings(node, available)
	if err != nil {
		r.emitNode(ctx, node.ID, "failed")
		return r.failAfterStart(ctx, CodeNodeInputInvalid, err.Error(), run.outcome,
			r.exitEvidence(r.heartbeatCount(), run.outcome, nil))
	}
	var final any = bindings
	if err := validateSchema(run.plan.definition.OutputSchema, bindings, "#"); err != nil {
		r.emitNode(ctx, node.ID, "failed")
		return r.failAfterStart(ctx, CodeNodeOutputInvalid,
			"the final output does not match definition.output_schema: "+err.Error(),
			run.outcome, r.exitEvidence(r.heartbeatCount(), run.outcome, nil))
	}
	finalObject, ok := final.(map[string]any)
	if !ok {
		r.emitNode(ctx, node.ID, "failed")
		return r.failAfterStart(ctx, CodeNodeOutputInvalid,
			"the final output must be a JSON object", run.outcome,
			r.exitEvidence(r.heartbeatCount(), run.outcome, nil))
	}
	payload, err := json.Marshal(finalObject)
	if err != nil {
		r.emitNode(ctx, node.ID, "failed")
		return r.failAfterStart(ctx, CodeNodeOutputInvalid,
			"the final output cannot be encoded", run.outcome,
			r.exitEvidence(r.heartbeatCount(), run.outcome, nil))
	}
	canonical, err := jcs.CanonicalizeBytes(payload)
	if err != nil {
		r.emitNode(ctx, node.ID, "failed")
		return r.failAfterStart(ctx, CodeNodeOutputInvalid,
			"the final output cannot be canonicalized", run.outcome,
			r.exitEvidence(r.heartbeatCount(), run.outcome, nil))
	}
	outputID, err := r.uploadNodeArtifact(ctx, "final_output", "application/json", canonical, "output")
	if err != nil {
		r.emitNode(ctx, node.ID, "failed")
		return r.failAfterStart(ctx, CodeSandboxFailed,
			"registering the final output failed: "+err.Error(), run.outcome,
			r.exitEvidence(r.heartbeatCount(), run.outcome, nil))
	}
	// The end node's work is done once the final output is registered; report it
	// before the result proposal, because the exit confirmation commits the
	// terminal run state that refuses further worker events.
	r.emitNode(ctx, node.ID, "succeeded")
	if err := r.deps.Client.SubmitResult(ctx, r.lease.RunID, r.lease.AttemptToken, Proposal{
		EventID:          uuid.NewString(),
		SuggestedState:   "succeeded",
		OutputArtifactID: outputID,
	}); err != nil {
		return r.failAfterStart(ctx, CodeSandboxFailed,
			"the succeeded proposal was refused: "+err.Error(), run.outcome,
			r.exitEvidence(r.heartbeatCount(), run.outcome, nil))
	}
	if err := r.confirmExit(ctx, true,
		r.exitEvidence(r.heartbeatCount(), run.outcome, nil)); err != nil {
		return executionOutcome{outcome: "error", code: CodeSandboxFailed,
			err:     "the exit confirmation was refused: " + err.Error(),
			sandbox: run.report, failedStatus: true}
	}
	return executionOutcome{
		outcome: "succeeded", runState: r.readRunState(ctx), sandbox: run.report,
	}
}

// failLinearBeforeSandbox refuses a plan/binding failure before any sandbox ran.
func (r *runner) failLinearBeforeSandbox(
	ctx context.Context, plan *linearPlan, code, message string, node *manifestNode,
) executionOutcome {
	if node != nil {
		r.emitNode(ctx, node.ID, "failed")
	}
	if plan != nil {
		r.skipRemaining(ctx, plan, nodeIDOf(node))
	}
	return r.refuse(ctx, code, message)
}

func nodeIDOf(node *manifestNode) string {
	if node == nil {
		return ""
	}
	return node.ID
}

// skipRemaining emits one `skipped` event for every chain node after `after`.
func (r *runner) skipRemaining(ctx context.Context, plan *linearPlan, after string) {
	seen := false
	for _, node := range plan.order {
		if !seen {
			if node.ID == after {
				seen = true
			}
			continue
		}
		r.emitNode(ctx, node.ID, "skipped")
	}
}

// emitNode submits one node.status transition. It is observability, not
// authority: a failed submission is logged and never turns a node success into a
// fake success (the run's result proposal and exit confirmation stay the one
// commit path).
func (r *runner) emitNode(ctx context.Context, nodeID, state string) {
	if r.deps.Client == nil {
		return
	}
	if err := r.deps.Client.SubmitNodeStatus(
		ctx, r.lease.RunID, r.lease.AttemptToken, nodeID, state,
	); err != nil {
		r.logger.Printf("run_id=%s: node.status %s=%s was not recorded: %v",
			r.lease.RunID, nodeID, state, err)
	}
}

// uploadNodeArtifact uploads and registers one node-produced artifact. The
// artifact is visible to the run only after Register accepts it; the caller may
// then (and only then) report the node result row.
func (r *runner) uploadNodeArtifact(
	ctx context.Context, purpose, mediaType string, content []byte, relation string,
) (string, error) {
	artifactID, err := r.uploadArtifact(ctx, purpose, mediaType, content, relation)
	if err != nil {
		return "", err
	}
	if _, err := r.deps.Client.Register(
		ctx, r.lease.RunID, r.lease.AttemptToken, artifactID, relation,
	); err != nil {
		return "", err
	}
	return artifactID, nil
}

// codeOfPlanError maps a plan refusal to its stable code.
func codeOfPlanError(err error) string {
	var plan *planError
	if errors.As(err, &plan) && plan.code != "" {
		return plan.code
	}
	return CodeToolUnsupportedGraph
}
