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
//
// C4.01/C4.02: the controlled nodes (agent and call_app) no longer read this
// view. The scheduler builds an explicit nodeExecution from these facts, runs the
// node and maps the returned local outcome to the attempt. The graph scheduler
// does not borrow this structure for any node either: it builds the same
// nodeExecution from its own scheduling facts.
type linearRun struct {
	plan    *linearPlan
	inputs  []InputArtifact
	start   map[string]any
	python  map[string]any
	agent   map[string]any
	callApp map[string]any
	outcome sandbox.Outcome
	report  *SandboxReport
}

// nodeHost is the scheduler-specific observable surface of one node failure:
// mark the node failed and skip the scheduler's remaining nodes.
type nodeHost struct {
	failed func()
	skip   func()
	// sandboxUnit overrides the runsc unit id for this node. The concurrent graph
	// scheduler gives every branch its own id so two sandboxes can run at once;
	// the linear chain leaves it empty and keeps the attempt's unit id.
	sandboxUnit string
}

func (h nodeHost) nodeFailed() {
	if h.failed != nil {
		h.failed()
	}
}

// executionUnit is the request's runsc unit id: the node's own id on the graph
// schedule, the attempt's id on the linear chain.
func (h nodeHost) executionUnit(attemptUnit string) string {
	if h.sandboxUnit != "" {
		return h.sandboxUnit
	}
	return attemptUnit
}

func (h nodeHost) skipRemaining() {
	if h.skip != nil {
		h.skip()
	}
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

	if plan.python != nil {
		pythonOutput, pythonOutcome, done := r.runPythonNode(ctx, run)
		if done {
			return pythonOutcome
		}
		run.python = pythonOutput
	}
	// The scheduler, not the node, maps the agent's local outcome to run behavior.
	if plan.agent != nil {
		agentOutput, agentOutcome, done := r.runLinearAgentNode(ctx, run)
		if done {
			return agentOutcome
		}
		run.agent = agentOutput
	}
	if plan.callApp != nil {
		childOutput, childOutcome, done := r.runLinearCallAppNode(ctx, run)
		if done {
			return childOutcome
		}
		run.callApp = childOutput
	}
	return r.runEndNode(ctx, run)
}

// runLinearAgentNode builds the explicit agent node execution from the chain's
// authoritative facts, runs the node, and maps its local outcome to the linear
// attempt. The node never skips the remaining chain and never commits the run;
// this linear scheduler does both, once.
func (r *runner) runLinearAgentNode(
	ctx context.Context, run *linearRun,
) (map[string]any, executionOutcome, bool) {
	node := run.plan.agent
	outcome := r.runAgentNode(ctx, r.newLinearNodeExecution(run, node))
	run.outcome, run.report = outcome.sandbox, outcome.report
	if outcome.kind == nodeOutcomeSucceeded {
		return outcome.output, executionOutcome{}, false
	}
	return nil, r.commitLinearNodeOutcome(ctx, run.plan, node, outcome), true
}

// runLinearCallAppNode builds the explicit call_app node execution from the
// chain's authoritative facts, runs the node, and maps its local outcome to the
// linear attempt. The node never skips the remaining chain and never commits the
// run; this linear scheduler does both, once.
func (r *runner) runLinearCallAppNode(
	ctx context.Context, run *linearRun,
) (map[string]any, executionOutcome, bool) {
	node := run.plan.callApp
	outcome := r.runCallAppNode(ctx, r.newLinearNodeExecution(run, node))
	run.outcome, run.report = outcome.sandbox, outcome.report
	if outcome.kind == nodeOutcomeSucceeded {
		return outcome.output, executionOutcome{}, false
	}
	return nil, r.commitLinearNodeOutcome(ctx, run.plan, node, outcome), true
}

// newLinearNodeExecution snapshots the chain's facts for one node: the resolved
// upstream namespace, the sealed node-id scope, the validated node config the
// scheduler already proved and the sandbox evidence observed so far. The
// namespace copy is the node's stable binding view for its whole execution; later
// scheduler mutations cannot change it.
func (r *runner) newLinearNodeExecution(
	run *linearRun, node *manifestNode,
) *nodeExecution {
	namespace := map[string]any{
		"input":           run.start,
		run.plan.start.ID: run.start,
	}
	if run.plan.python != nil {
		namespace[run.plan.python.ID] = run.python
	}
	scopeIDs := make([]string, 0, len(run.plan.order))
	for _, planned := range run.plan.order {
		scopeIDs = append(scopeIDs, planned.ID)
	}
	return &nodeExecution{
		node: node, definition: run.plan.definition, inputs: run.inputs,
		namespace: namespace, startNodeID: run.plan.start.ID,
		scopeNodeIDs: scopeIDs, sandbox: run.outcome, report: run.report,
		agentConfig: run.plan.agentConfig, callAppConfig: run.plan.callAppConfig,
	}
}

// commitLinearNodeOutcome maps one terminal local node outcome to the linear
// attempt. This is the scheduler half of C4.01: it skips the remaining chain and
// submits/certifies the run result here and only here.
func (r *runner) commitLinearNodeOutcome(
	ctx context.Context, plan *linearPlan, node *manifestNode, outcome nodeOutcome,
) executionOutcome {
	report := sandboxReportOf(outcome.sandbox)
	switch outcome.kind {
	case nodeOutcomeFailed:
		r.skipRemaining(ctx, plan, node.ID)
		return r.commitFailedAfterStart(ctx, outcome.code, outcome.message,
			outcome.sandbox, outcome.evidence)

	case nodeOutcomeCancelled:
		r.skipRemaining(ctx, plan, node.ID)
		if err := r.confirmExit(ctx, outcome.sandbox.SandboxStarted, outcome.evidence); err != nil {
			return executionOutcome{outcome: "cancelled", code: "cancel_requested",
				err: err.Error(), sandbox: report, failedStatus: true}
		}
		return executionOutcome{outcome: "cancelled", code: "cancel_requested",
			runState: r.readRunState(ctx), sandbox: report}

	case nodeOutcomeWaiting:
		if outcome.proposal == nil {
			r.skipRemaining(ctx, plan, node.ID)
			return r.commitFailedAfterStart(ctx, CodeSandboxFailed,
				"the node waited without a result proposal", outcome.sandbox, outcome.evidence)
		}
		if err := r.deps.Client.SubmitResult(
			ctx, r.lease.RunID, r.lease.AttemptToken, *outcome.proposal,
		); err != nil {
			r.emitNode(ctx, node.ID, "failed")
			r.skipRemaining(ctx, plan, node.ID)
			code := outcome.fallbackCode
			if code == "" {
				code = CodeSandboxFailed
			}
			return r.commitFailedAfterStart(ctx, code, outcome.proposalRefusal+err.Error(),
				outcome.sandbox, r.exitEvidence(r.heartbeatCount(), outcome.sandbox, nil))
		}
		if err := r.confirmExit(ctx, outcome.sandbox.SandboxStarted, outcome.evidence); err != nil {
			return executionOutcome{outcome: "error", code: outcome.fallbackCode,
				err: outcome.exitRefusal + err.Error(), sandbox: report}
		}
		return executionOutcome{outcome: outcome.outcomeName, code: outcome.code,
			runState: r.readRunState(ctx), sandbox: report}

	default:
		return executionOutcome{outcome: "error", code: CodeSandboxFailed,
			err: "the linear scheduler received no terminal node outcome", failedStatus: true}
	}
}

// errRunInputUnavailable marks a transport-level failure to read the sealed run
// input (the graph itself is fine).
var errRunInputUnavailable = errors.New("the run input is unavailable")

// runStartNode publishes the validated run input for the linear chain.
func (r *runner) runStartNode(ctx context.Context, run *linearRun) (map[string]any, error) {
	return r.executeStartNode(ctx, run.plan.start, run.plan.definition, run.inputs)
}

// executeStartNode publishes the validated run input and, when exactly one
// CSV/XLSX task_input artifact is linked, its server facts under the documented
// `artifact` key. Nothing from the client is trusted: the facts come from the
// database row, not from input JSON.
func (r *runner) executeStartNode(
	ctx context.Context, node *manifestNode, definition *manifestDefinition, inputs []InputArtifact,
) (map[string]any, error) {
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
	if err := validateSchema(definition.InputSchema, object, "#"); err != nil {
		return nil, &locatedError{Pointer: "#", Message: "input_schema: " + err.Error()}
	}

	taskInputs := make([]InputArtifact, 0, 1)
	for _, input := range inputs {
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

// runPythonNode executes the linear chain's python node. The work itself lives in
// executePythonNode so the graph scheduler runs exactly the same node semantics.
func (r *runner) runPythonNode(
	ctx context.Context, run *linearRun,
) (map[string]any, executionOutcome, bool) {
	available := map[string]any{
		"input":           run.start,
		run.plan.start.ID: run.start,
	}
	execution := r.executePythonNode(ctx, run.plan.python, available, run.inputs, nodeHost{
		failed: func() { r.emitNode(ctx, run.plan.python.ID, "failed") },
		skip:   func() { r.skipRemaining(ctx, run.plan, run.plan.python.ID) },
	})
	run.outcome = execution.sandbox
	run.report = execution.report
	if execution.terminal != nil {
		return nil, *execution.terminal, true
	}
	return execution.output, executionOutcome{}, false
}

// pythonExecution is one python node attempt's result. A handled failure has
// already submitted the run's failed/cancelled proposal and lives in terminal;
// output is set only when the node succeeded.
type pythonExecution struct {
	output   map[string]any
	sandbox  sandbox.Outcome
	report   *SandboxReport
	terminal *executionOutcome
}

// executePythonNode resolves the node bindings, runs the fixed tool, validates its
// output and registers data/files/log. The host callback owns the scheduler's
// observable failure/skip facts so the linear and graph paths stay identical.
func (r *runner) executePythonNode(
	ctx context.Context, node *manifestNode, available map[string]any,
	inputs []InputArtifact, host nodeHost,
) pythonExecution {
	r.emitNode(ctx, node.ID, "pending")
	r.emitNode(ctx, node.ID, "running")

	bindings, err := resolveNodeBindings(node, available)
	if err != nil {
		return pythonTerminal(r.failNodeBeforeSandbox(ctx, node, host, CodeNodeInputInvalid, err.Error()))
	}
	input, err := r.bindPythonFile(bindings, inputs)
	if err != nil {
		return pythonTerminal(r.failNodeBeforeSandbox(ctx, node, host, CodeNodeInputInvalid, err.Error()))
	}

	inputBytes, err := r.downloadInput(ctx, input)
	if err != nil {
		return pythonTerminal(r.failNodeBeforeSandbox(ctx, node, host, CodeInputIntegrity, err.Error()))
	}
	inputName, err := sandbox.InputNameForMediaType(input.MediaType)
	if err != nil {
		return pythonTerminal(r.failNodeBeforeSandbox(ctx, node, host, CodeToolInputInvalid, err.Error()))
	}
	inputDocument, err := buildInputDocument(input, inputName)
	if err != nil {
		return pythonTerminal(r.failNodeBeforeSandbox(ctx, node, host, CodeToolInputInvalid, err.Error()))
	}

	outcome, sandboxErr := r.runSandbox(ctx, sandbox.Request{
		Program:         sandbox.ProgramTool,
		MediaType:       strings.ToLower(strings.TrimSpace(input.MediaType)),
		Input:           inputBytes,
		InputJSON:       inputDocument,
		ExecutionUnitID: host.executionUnit(r.options.ExecutionUnitID),
	}, r.heartbeatControl())
	report := sandboxReportOf(outcome)
	evidence := r.exitEvidence(r.heartbeatCount(), outcome, sandboxErr)

	switch {
	case errors.Is(sandboxErr, sandbox.ErrCancelled):
		host.nodeFailed()
		host.skipRemaining()
		if captureGraphCancellation(ctx, outcome, evidence) {
			// The graph scheduler confirms the exit only after the other branches
			// stopped, so a cancelled branch never races their cleanup.
			return pythonExecution{sandbox: outcome, report: report,
				terminal: &executionOutcome{outcome: "cancelled", code: "cancel_requested",
					sandbox: report}}
		}
		if err := r.confirmExit(ctx, outcome.SandboxStarted, evidence); err != nil {
			return pythonExecution{sandbox: outcome, report: report,
				terminal: &executionOutcome{outcome: "cancelled", code: "cancel_requested",
					err: err.Error(), sandbox: report, failedStatus: true}}
		}
		return pythonExecution{sandbox: outcome, report: report,
			terminal: &executionOutcome{outcome: "cancelled", code: "cancel_requested",
				runState: r.readRunState(ctx), sandbox: report}}

	case errors.Is(sandboxErr, sandbox.ErrHeartbeat):
		host.nodeFailed()
		host.skipRemaining()
		return pythonExecution{sandbox: outcome, report: report,
			terminal: &executionOutcome{outcome: "error", code: CodeSandboxFailed,
				err:     "the attempt lease was lost while the sandbox ran: " + errString(sandboxErr),
				sandbox: report, failedStatus: true}}

	case errors.Is(sandboxErr, sandbox.ErrTimeout):
		return pythonTerminal(r.failPythonRun(ctx, node, host, CodeToolTimeout,
			"the preset tool exceeded its sandbox timeout", outcome, sandboxErr, evidence, nil, input))

	case errors.Is(sandboxErr, sandbox.ErrRootfsUnavailable),
		errors.Is(sandboxErr, sandbox.ErrToolUnavailable),
		errors.Is(sandboxErr, sandbox.ErrRunnerUnavailable),
		errors.Is(sandboxErr, sandbox.ErrSandboxRefused),
		errors.Is(sandboxErr, sandbox.ErrDriverReport),
		errors.Is(sandboxErr, sandbox.ErrDigestMismatch):
		return pythonTerminal(r.failPythonRun(ctx, node, host, CodeSandboxUnavailable,
			"the sandbox could not be started or trusted", outcome, sandboxErr, evidence, nil, input))

	case errors.Is(sandboxErr, sandbox.ErrOrphan):
		return pythonTerminal(r.failPythonRun(ctx, node, host, CodeSandboxOrphan,
			"the sandbox cleanup could not prove that every process exited", outcome,
			sandboxErr, evidence, nil, input))

	case sandboxErr != nil:
		return pythonTerminal(r.failPythonRun(ctx, node, host, CodeSandboxFailed,
			"the sandbox failed", outcome, sandboxErr, evidence, nil, input))
	}

	switch outcome.ExitCode {
	case 0:
	case 3:
		rejection, rejectionErr := tools.ParseRejectionPayload(outcome.OutputJSON)
		if rejectionErr != nil {
			return pythonTerminal(r.failPythonRun(ctx, node, host, CodeToolOutputInvalid,
				"the tool rejected the input with an unreadable document", outcome,
				rejectionErr, evidence, nil, input))
		}
		diagnostics := diagnosticsDocument(rejection.Code, rejection.Message, rejection.Fix,
			rejectionLocation(rejection), input)
		return pythonTerminal(r.failPythonRun(ctx, node, host, rejection.Code, rejection.Message,
			outcome, nil, evidence, diagnostics, input))
	default:
		return pythonTerminal(r.failPythonRun(ctx, node, host, CodeToolCrash,
			"the preset tool exited with code "+fmt.Sprint(outcome.ExitCode),
			outcome, nil, evidence, nil, input))
	}

	data, names, err := sandbox.ParseOutputDocument(outcome.OutputJSON)
	if err != nil {
		return pythonTerminal(r.pythonFailure(ctx, node, host, CodeToolOutputInvalid,
			err.Error(), outcome, err))
	}
	collected := make(map[string][]byte, len(outcome.Artifacts))
	for _, artifact := range outcome.Artifacts {
		collected[artifact.Name] = artifact.Content
	}
	admitted, err := sandbox.AdmitArtifacts(names, collected)
	if err != nil {
		return pythonTerminal(r.pythonFailure(ctx, node, host, CodeToolArtifactInvalid,
			err.Error(), outcome, err))
	}
	dataValue, err := decodeJSONDocument(data)
	if err != nil {
		return pythonTerminal(r.pythonFailure(ctx, node, host, CodeToolOutputInvalid,
			"output.json.data is not a JSON document", outcome, err))
	}
	dataObject, ok := dataValue.(map[string]any)
	if !ok {
		return pythonTerminal(r.pythonFailure(ctx, node, host, CodeToolOutputInvalid,
			"output.json.data is not an object", outcome, nil))
	}
	if err := validateSchema(node.OutputSchema, dataObject, "#/data"); err != nil {
		return pythonTerminal(r.pythonFailure(ctx, node, host, CodeNodeOutputInvalid,
			"output.json.data does not match the node output_schema: "+err.Error(), outcome, err))
	}
	canonical, err := jcs.CanonicalizeBytes(data)
	if err != nil {
		return pythonTerminal(r.pythonFailure(ctx, node, host, CodeToolOutputInvalid,
			"output.json.data cannot be canonicalized", outcome, err))
	}

	results := make([]NodeResult, 0, len(admitted)+2)
	dataID, err := r.uploadNodeArtifact(ctx, "node_output", "application/json", canonical, "artifact")
	if err != nil {
		return pythonTerminal(r.pythonFailure(ctx, node, host, CodeSandboxFailed,
			"registering output.json.data failed: "+err.Error(), outcome, err))
	}
	results = append(results, NodeResult{NodeID: node.ID, Kind: "data", ArtifactID: dataID, Label: "data"})

	for _, artifact := range admitted {
		artifactID, uploadErr := r.uploadNodeArtifact(
			ctx, "node_output", mediaTypeForArtifact(artifact.Name), artifact.Content, "artifact",
		)
		if uploadErr != nil {
			return pythonTerminal(r.pythonFailure(ctx, node, host, CodeSandboxFailed,
				"registering the tool artifact "+artifact.Name+" failed: "+uploadErr.Error(),
				outcome, uploadErr))
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
		return pythonTerminal(r.pythonFailure(ctx, node, host, CodeSandboxFailed,
			"recording the node results failed: "+err.Error(), outcome, err))
	}
	r.emitNode(ctx, node.ID, "succeeded")
	return pythonExecution{output: dataObject, sandbox: outcome, report: report}
}

func pythonTerminal(outcome executionOutcome) pythonExecution {
	return pythonExecution{terminal: &outcome}
}

// failNodeBeforeSandbox refuses a node before any sandbox ran: the node's failed
// fact, the scheduler's skips, then the run's deterministic refusal.
func (r *runner) failNodeBeforeSandbox(
	ctx context.Context, node *manifestNode, host nodeHost, code, message string,
) executionOutcome {
	host.nodeFailed()
	host.skipRemaining()
	return r.refuse(ctx, code, message)
}

// failPythonRun marks the node failed, skips the scheduler's remaining nodes and
// produces the failed-run outcome after the sandbox already ran.
func (r *runner) failPythonRun(
	ctx context.Context, node *manifestNode, host nodeHost, code, message string,
	outcome sandbox.Outcome, sandboxErr error, evidence map[string]any,
	diagnostics map[string]any, input *InputArtifact,
) executionOutcome {
	host.nodeFailed()
	host.skipRemaining()
	return r.failRun(ctx, code, message, outcome, sandboxErr, evidence, diagnostics, input)
}

// pythonFailure is the post-sandbox failure path with the default evidence.
func (r *runner) pythonFailure(
	ctx context.Context, node *manifestNode, host nodeHost, code, message string,
	outcome sandbox.Outcome, err error,
) executionOutcome {
	host.nodeFailed()
	host.skipRemaining()
	return r.failRun(ctx, code, message, outcome, err,
		r.exitEvidence(r.heartbeatCount(), outcome, nil), nil, nil)
}

// bindPythonFile resolves the python node's `file` binding to the linked input
// artifact's server facts. A path, a bare artifact id or a mismatching fact is a
// located node_input_invalid failure; the binding can never name a client path.
func (r *runner) bindPythonFile(
	bindings map[string]any, inputs []InputArtifact,
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
	for index := range inputs {
		if inputs[index].ID == artifactID {
			linked = &inputs[index]
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

// runEndNode maps the linear chain's definition output from its upstream outputs.
func (r *runner) runEndNode(ctx context.Context, run *linearRun) executionOutcome {
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
	if run.plan.callApp != nil {
		available[run.plan.callApp.ID] = run.callApp
	}
	return r.executeEndNode(ctx, run.plan.definition, run.plan.end, available, run.outcome, run.report)
}

// executeEndNode maps the definition output from upstream outputs, validates it,
// seals it as final_output and proposes the result. The graph scheduler passes its
// own upstream namespace and last sandbox facts.
func (r *runner) executeEndNode(
	ctx context.Context, definition *manifestDefinition, node *manifestNode,
	available map[string]any, last sandbox.Outcome, report *SandboxReport,
) executionOutcome {
	r.emitNode(ctx, node.ID, "pending")
	r.emitNode(ctx, node.ID, "running")

	bindings, err := resolveNodeBindings(node, available)
	if err != nil {
		r.emitNode(ctx, node.ID, "failed")
		return r.failAfterStart(ctx, CodeNodeInputInvalid, err.Error(), last,
			r.exitEvidence(r.heartbeatCount(), last, nil))
	}
	var final any = bindings
	if err := validateSchema(definition.OutputSchema, bindings, "#"); err != nil {
		r.emitNode(ctx, node.ID, "failed")
		return r.failAfterStart(ctx, CodeNodeOutputInvalid,
			"the final output does not match definition.output_schema: "+err.Error(),
			last, r.exitEvidence(r.heartbeatCount(), last, nil))
	}
	finalObject, ok := final.(map[string]any)
	if !ok {
		r.emitNode(ctx, node.ID, "failed")
		return r.failAfterStart(ctx, CodeNodeOutputInvalid,
			"the final output must be a JSON object", last,
			r.exitEvidence(r.heartbeatCount(), last, nil))
	}
	payload, err := json.Marshal(finalObject)
	if err != nil {
		r.emitNode(ctx, node.ID, "failed")
		return r.failAfterStart(ctx, CodeNodeOutputInvalid,
			"the final output cannot be encoded", last,
			r.exitEvidence(r.heartbeatCount(), last, nil))
	}
	canonical, err := jcs.CanonicalizeBytes(payload)
	if err != nil {
		r.emitNode(ctx, node.ID, "failed")
		return r.failAfterStart(ctx, CodeNodeOutputInvalid,
			"the final output cannot be canonicalized", last,
			r.exitEvidence(r.heartbeatCount(), last, nil))
	}
	outputID, err := r.uploadNodeArtifact(ctx, "final_output", "application/json", canonical, "output")
	if err != nil {
		r.emitNode(ctx, node.ID, "failed")
		return r.failAfterStart(ctx, CodeSandboxFailed,
			"registering the final output failed: "+err.Error(), last,
			r.exitEvidence(r.heartbeatCount(), last, nil))
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
			"the succeeded proposal was refused: "+err.Error(), last,
			r.exitEvidence(r.heartbeatCount(), last, nil))
	}
	if err := r.confirmExit(ctx, true,
		r.exitEvidence(r.heartbeatCount(), last, nil)); err != nil {
		return executionOutcome{outcome: "error", code: CodeSandboxFailed,
			err:     "the exit confirmation was refused: " + err.Error(),
			sandbox: report, failedStatus: true}
	}
	return executionOutcome{
		outcome: "succeeded", runState: r.readRunState(ctx), sandbox: report,
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
	submitCtx := ctx
	if ctx.Err() != nil {
		// A node.status transition is a durable fact about what happened, not work
		// the cancellation should erase: F04B/T085 cancels a branch's context on
		// fail-fast, and the interrupted branch's terminal fact must still be
		// recorded before the run's failure is committed. The protocol client
		// bounds this call with its own timeout.
		submitCtx = context.WithoutCancel(ctx)
	}
	if err := r.deps.Client.SubmitNodeStatus(
		submitCtx, r.lease.RunID, r.lease.AttemptToken, nodeID, state,
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
