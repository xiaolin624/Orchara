package presetexec

// F04B/T083/T085: the graph scheduler.
//
// The scheduler is a ready-queue over the plan's proven DAG: every node whose
// predecessors have settled is decided (run / skip / fail) immediately, and every
// runnable node is started on its own goroutine. Independent branches therefore
// really overlap in time; a join waits only for its own activated region, never
// for an unselected port.
//
// One activation question per node:
//
//   - start: always activated;
//   - an ordinary node: its single incoming edge must be taken by a succeeded
//     source, otherwise the node is skipped (this is how an unselected condition
//     branch is carried through the whole region);
//   - a join: every predecessor has settled, at least one edge into it must be
//     taken and at least one activated source must be required. Otherwise the
//     region fails closed with `invalid_activation`; a join whose fork was skipped
//     (or whose condition could not select a port) is itself skipped, so a region
//     nested inside an unselected branch never dead-waits.
//
// Edge "taken" rules:
//
//   - leaf/ordinary/join/agent/call_app successes take every outgoing edge;
//   - a condition takes only the edges of its selected port.
//
// A node that succeeds exactly once therefore emits exactly one `succeeded`, and a
// join runs exactly once per run (one data artifact, one event), never per
// incoming edge. Replayed nodes are reused at activation time and emit only their
// `succeeded` fact; a reused join is never fired again.
//
// Fail-fast (EXECUTION §7, S4): a required failure cancels the work context of the
// other in-flight branches, waits until every node goroutine has returned (the
// sandbox cleanup proof is part of that return), emits `skipped` for the nodes that
// never started, and only then submits the run's failed result. The join never
// runs for a failed region.
//
// Optional convergence (S3/S6): a failure deep inside a branch whose join source is
// `optional`, produced by a pure-read/compute node type with an explicitly
// allowlisted error code, is folded into the join envelope as
// {status:"failed", error_code:"<stable>"} and the run continues. Everything else
// (agent/call_app, authorization, budget, approval, unknown write, persistence)
// still fails the run.
//
// Node execution itself is the linear executor's node code: executeStartNode,
// executePythonNode, executeEndNode and — through a synthetic linear view — the
// agent and call_app segments. F04B/T086's model node owns its own one-call
// generation in modelnode.go. The scheduler owns activation, concurrency, skip
// propagation and the condition/join outputs, and it owns the run-result commit
// for every terminal node decision (see graphNodeAttempt).
//
// Concurrency-safety: the scheduler goroutine is the single owner of graphRun
// (state/outputs/taken are never mutated concurrently). Node goroutines receive
// immutable snapshots (graphNodeView) and report back over a buffered channel;
// they never read scheduler-owned maps. The only shared runner state a node
// goroutine mutates is the heartbeat counter (atomic), the operator artifact list
// (mutex) and the protocol client (concurrency-safe by contract).

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"

	"orchara/platform/internal/jcs"
	"orchara/platform/internal/sandbox"
)

// graphRun is one graph attempt's scheduler-owned state. Only the scheduler
// goroutine mutates it.
type graphRun struct {
	plan    *graphPlan
	inputs  []InputArtifact
	outputs map[string]map[string]any
	state   map[string]string
	taken   map[int]bool
}

func newGraphRun(plan *graphPlan, inputs []InputArtifact) *graphRun {
	return &graphRun{
		plan:    plan,
		inputs:  inputs,
		outputs: make(map[string]map[string]any, len(plan.nodes)),
		state:   make(map[string]string, len(plan.nodes)),
		taken:   make(map[int]bool, len(plan.edges)),
	}
}

// availableSnapshot is the immutable upstream-output namespace node bindings
// resolve against: every settled succeeded node by id, and the start output under
// the documented `input` name. The map is copied so a node goroutine can read it
// while the scheduler publishes further outputs.
func (g *graphRun) availableSnapshot() map[string]any {
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

// ---------------------------------------------------------------------------
// node attempts and their terminal decisions
// ---------------------------------------------------------------------------

const (
	// graphAttemptFailed: the node's attempt failed; the scheduler commits the
	// run result after it stopped the other branches (or absorbs the failure when
	// the branch is optional).
	graphAttemptFailed = "failed"
	// graphAttemptCancelled: the node observed a cancellation (platform cancel).
	// The scheduler confirms the attempt exit once the other branches stopped.
	graphAttemptCancelled = "cancelled"
	// graphAttemptWaiting: the node parks the run at a stable checkpoint. The
	// scheduler submits the waiting proposal and confirms the exit only after the
	// other branches stopped (the waiting hand-over of EXECUTION §4/§7).
	graphAttemptWaiting = "waiting"
)

// graphNodeAttempt is one graph node's attempt record. It carries the observed
// sandbox facts and, when the node ends the attempt, the decision the scheduler
// must commit. It travels in the node's context so the shared linear node code can
// record the decision without knowing the scheduler exists.
type graphNodeAttempt struct {
	sandbox sandbox.Outcome
	report  *SandboxReport
	started bool

	kind            string
	code            string
	message         string
	sandboxErr      error
	evidence        map[string]any
	failureEvidence bool
	startedForExit  bool
	proposal        *Proposal
	outcomeName     string
	fallbackCode    string
}

type graphAttemptKey struct{}

// graphAttemptFrom returns the attempt record when the node runs under the graph
// scheduler (nil on the linear/legacy paths).
func graphAttemptFrom(ctx context.Context) *graphNodeAttempt {
	attempt, _ := ctx.Value(graphAttemptKey{}).(*graphNodeAttempt)
	return attempt
}

// captureGraphFailure turns the node's own result commit into a decision for the
// scheduler. It returns the neutral outcome the node reports upward and true when
// the capture happened; false leaves the linear path untouched.
func captureGraphFailure(
	ctx context.Context, code, message string, outcome sandbox.Outcome,
	sandboxErr error, evidence map[string]any, failureEvidence, startedForExit bool,
) (executionOutcome, bool) {
	attempt := graphAttemptFrom(ctx)
	if attempt == nil {
		return executionOutcome{}, false
	}
	attempt.kind = graphAttemptFailed
	attempt.code = code
	attempt.message = message
	attempt.sandbox = outcome
	attempt.sandboxErr = sandboxErr
	attempt.evidence = evidence
	attempt.failureEvidence = failureEvidence
	attempt.startedForExit = startedForExit
	return executionOutcome{outcome: "failed", code: code, err: message,
		sandbox: sandboxReportOf(outcome)}, true
}

// captureGraphCancellation records that the node stopped because its attempt was
// cancelled; the scheduler confirms the exit after the other branches stopped.
func captureGraphCancellation(
	ctx context.Context, outcome sandbox.Outcome, evidence map[string]any,
) bool {
	attempt := graphAttemptFrom(ctx)
	if attempt == nil {
		return false
	}
	attempt.kind = graphAttemptCancelled
	attempt.code = "cancel_requested"
	attempt.sandbox = outcome
	attempt.evidence = evidence
	return true
}

// captureGraphWaiting records the stable checkpoint the run parks at.
func captureGraphWaiting(
	ctx context.Context, proposal Proposal, outcomeName string,
	outcome sandbox.Outcome, evidence map[string]any, fallbackCode string,
) bool {
	attempt := graphAttemptFrom(ctx)
	if attempt == nil {
		return false
	}
	attempt.kind = graphAttemptWaiting
	attempt.code = proposal.ErrorCode
	attempt.proposal = &proposal
	attempt.outcomeName = outcomeName
	attempt.sandbox = outcome
	attempt.evidence = evidence
	attempt.fallbackCode = fallbackCode
	return true
}

// graphNodeView is the immutable snapshot one node attempt runs against.
type graphNodeView struct {
	available map[string]any
	start     map[string]any
	branches  *graphJoinView
	// last/report are the run's sandbox evidence so far (deterministically the
	// highest plan index that started a sandbox), used by the end node's exit
	// evidence.
	last   sandbox.Outcome
	report *SandboxReport
}

// graphJoinView is the settled state of one join's region at join launch time.
type graphJoinView struct {
	states  map[string]string
	outputs map[string]map[string]any
	// failed maps an optional join source to the stable error code its branch
	// converged to; those entries are error data, not success data.
	failed map[string]string
}

// graphNodeResult is one finished node attempt reported to the scheduler loop.
type graphNodeResult struct {
	node      *graphNode
	attempt   *graphNodeAttempt
	output    map[string]any
	succeeded bool
	// terminal is the node's already-committed terminal outcome (the end node's
	// success), or the neutral outcome of a captured decision.
	terminal *executionOutcome
}

// ---------------------------------------------------------------------------
// scheduler
// ---------------------------------------------------------------------------

// graphScheduler is the ready-queue concurrent scheduler for one proven graph.
type graphScheduler struct {
	runner *runner
	plan   *graphPlan
	run    *graphRun

	// base is the attempt context the scheduler commits results and durable node
	// facts with; work is the cancelled branch context node goroutines run under.
	base   context.Context
	work   context.Context
	cancel context.CancelFunc

	reused map[string]map[string]any
	active map[string]bool
	// results is buffered for the whole plan so a stopped branch can always
	// deliver its result without blocking the scheduler goroutine.
	results  chan graphNodeResult
	inFlight int

	// evidence is the deterministic "last sandbox that ran" record: the highest
	// plan index among the nodes that started one.
	evidenceIndex int
	started       bool
	last          sandbox.Outcome
	report        *SandboxReport

	optionalFailures map[string]map[string]string
	endOutcome       *executionOutcome
}

// executeGraph runs the proven graph concurrently.
func (r *runner) executeGraph(ctx context.Context, plan *graphPlan) executionOutcome {
	linked, err := r.deps.Inputs.Inputs(ctx, r.lease.RunID)
	if err != nil {
		return r.refuse(ctx, CodeToolInputInvalid,
			"the run's linked inputs could not be resolved: "+err.Error())
	}
	run := newGraphRun(plan, linked)
	work, cancel := context.WithCancel(ctx)
	defer cancel()
	scheduler := &graphScheduler{
		runner:           r,
		plan:             plan,
		run:              run,
		base:             ctx,
		work:             work,
		cancel:           cancel,
		reused:           r.reusableNodeOutputs(ctx, plan),
		active:           make(map[string]bool, len(plan.nodes)),
		results:          make(chan graphNodeResult, len(plan.nodes)),
		evidenceIndex:    -1,
		optionalFailures: map[string]map[string]string{},
	}
	return scheduler.schedule()
}

// schedule is the ready-queue loop.
func (s *graphScheduler) schedule() executionOutcome {
	for {
		if outcome, terminal := s.launchReady(); terminal {
			return outcome
		}
		if s.settledAll() {
			if s.endOutcome != nil {
				return *s.endOutcome
			}
			return s.failRunState(CodeInvalidActivation,
				"the graph's end node was never activated")
		}
		if s.inFlight == 0 {
			return s.failRunState(CodeInvalidActivation,
				"no remaining node of the graph could become ready")
		}
		result := <-s.results
		s.inFlight--
		if outcome, terminal := s.apply(result); terminal {
			return outcome
		}
	}
}

// launchReady settles every node that can be decided right now: it emits skips,
// reuses completed outputs and starts one goroutine per runnable node.
func (s *graphScheduler) launchReady() (executionOutcome, bool) {
	for _, id := range s.plan.order {
		if s.run.state[id] != "" || s.active[id] {
			continue
		}
		if !s.predecessorsSettled(id) {
			continue
		}
		node := s.plan.nodes[id]
		action, code, message := s.decide(node)
		switch action {
		case graphActionSkip:
			s.settleSkipped(id)
		case graphActionFail:
			s.run.state[id] = "failed"
			s.runner.emitNode(s.base, id, "failed")
			return s.failRunState(code, message), true
		case graphActionRun:
			if output, reused := s.reused[id]; reused {
				// A replayed node reports only its terminal fact (no pending/running)
				// and never re-executes its external work.
				s.runner.emitNode(s.base, id, "succeeded")
				s.settleSucceeded(node, output, &graphNodeAttempt{})
				continue
			}
			s.launch(node)
		}
	}
	return executionOutcome{}, false
}

const (
	graphActionRun  = "run"
	graphActionSkip = "skip"
	graphActionFail = "fail"
)

// decide answers the one activation question for a ready node.
func (s *graphScheduler) decide(node *graphNode) (action, code, message string) {
	id := node.node.ID
	if id == s.plan.startID {
		return graphActionRun, "", ""
	}
	if node.node.Type == "join" {
		region := s.plan.regionByJoin[id]
		if region == nil {
			return graphActionFail, CodeInvalidRegion, "the join has no proven region"
		}
		// A region whose fork was skipped (or whose condition could not select a
		// port and was absorbed as error data) never activates: the join must not
		// wait for branches that never existed, nor fail the run for them.
		switch s.run.state[region.fork] {
		case "skipped", "failed":
			return graphActionSkip, "", ""
		}
		activated, requiredActivated := 0, false
		for _, edge := range node.incoming {
			if !s.run.taken[edge.index] {
				continue
			}
			activated++
			if region.required[edge.source] {
				requiredActivated = true
			}
		}
		if activated == 0 || !requiredActivated {
			return graphActionFail, CodeInvalidActivation, fmt.Sprintf(
				"the join %q has %d activated incoming edges and no activated required source",
				id, activated)
		}
		return graphActionRun, "", ""
	}
	if len(node.incoming) != 1 {
		return graphActionFail, CodeMergeJoin, fmt.Sprintf(
			"the node %q has %d incoming edges but is not a join", id, len(node.incoming))
	}
	if !s.run.taken[node.incoming[0].index] {
		return graphActionSkip, "", ""
	}
	return graphActionRun, "", ""
}

// predecessorsSettled reports whether every control predecessor of the node has a
// final state (a join needs all of them; every other node has at most one).
func (s *graphScheduler) predecessorsSettled(id string) bool {
	for _, edge := range s.plan.nodes[id].incoming {
		if s.run.state[edge.source] == "" {
			return false
		}
	}
	return true
}

func (s *graphScheduler) settledAll() bool {
	for _, id := range s.plan.order {
		if s.run.state[id] == "" {
			return false
		}
	}
	return true
}

// settleSkipped records a node the activation rule left unselected.
func (s *graphScheduler) settleSkipped(id string) {
	s.run.state[id] = "skipped"
	s.runner.emitNode(s.base, id, "skipped")
}

// settleSucceeded publishes one succeeded node's output and takes its edges.
func (s *graphScheduler) settleSucceeded(
	node *graphNode, output map[string]any, attempt *graphNodeAttempt,
) {
	id := node.node.ID
	s.run.state[id] = "succeeded"
	s.run.outputs[id] = output
	s.run.takeOutEdges(node, output)
	s.recordEvidence(node, attempt)
}

// launch starts one node attempt on its own goroutine with an immutable view.
func (s *graphScheduler) launch(node *graphNode) {
	id := node.node.ID
	view := &graphNodeView{
		available: s.run.availableSnapshot(),
		branches:  s.joinView(node),
		last:      s.last,
		report:    s.report,
	}
	if start, ok := s.run.outputs[s.plan.startID]; ok {
		view.start = start
	}
	s.active[id] = true
	s.inFlight++
	plan, run, work := s.plan, s.run, s.work
	go func() {
		s.results <- s.runner.executeGraphNode(work, plan, run.inputs, node, view)
	}()
}

// joinView snapshots the settled branch states a join needs. Only a join reads it.
func (s *graphScheduler) joinView(node *graphNode) *graphJoinView {
	if node.node.Type != "join" {
		return nil
	}
	region := s.plan.regionByJoin[node.node.ID]
	if region == nil {
		return nil
	}
	view := &graphJoinView{
		states:  make(map[string]string, len(region.sources)),
		outputs: make(map[string]map[string]any, len(region.sources)),
		failed:  map[string]string{},
	}
	for _, source := range region.sources {
		view.states[source] = s.run.state[source]
		if output, present := s.run.outputs[source]; present {
			view.outputs[source] = output
		}
	}
	for source, code := range s.optionalFailures[region.join] {
		view.failed[source] = code
	}
	return view
}

// recordEvidence keeps the deterministic sandbox evidence: the highest plan index
// that started a sandbox. Completion order never decides it.
func (s *graphScheduler) recordEvidence(node *graphNode, attempt *graphNodeAttempt) {
	if attempt == nil {
		return
	}
	if attempt.started {
		s.started = true
	}
	if !attempt.sandbox.SandboxStarted && attempt.report == nil {
		return
	}
	if node.index < s.evidenceIndex {
		return
	}
	s.evidenceIndex = node.index
	s.last = attempt.sandbox
	s.report = attempt.report
}

// apply folds one completed node attempt into the scheduler state.
func (s *graphScheduler) apply(result graphNodeResult) (executionOutcome, bool) {
	id := result.node.node.ID
	attempt := result.attempt
	if result.terminal == nil {
		s.settleSucceeded(result.node, result.output, attempt)
		return executionOutcome{}, false
	}
	if attempt == nil || attempt.kind == "" {
		// The node already committed a terminal run result itself (never expected
		// for a graph node, but a safe stop beats committing twice).
		s.run.state[id] = "failed"
		return s.stopForCommittedTerminal(result.terminal)
	}
	s.recordEvidence(result.node, attempt)
	switch attempt.kind {
	case graphAttemptFailed:
		s.run.state[id] = "failed"
		if s.absorbOptionalFailure(result.node, attempt) {
			return executionOutcome{}, false
		}
		return s.stopAndCommit(attempt), true
	case graphAttemptCancelled:
		s.run.state[id] = "failed"
		return s.stopAndCommit(attempt), true
	case graphAttemptWaiting:
		s.run.state[id] = "waiting"
		return s.stopAndCommit(attempt), true
	default:
		s.run.state[id] = "failed"
		return s.stopAndCommit(attempt), true
	}
}

// absorbOptionalFailure folds an explicitly allowlisted pure-read/compute failure
// of an optional branch into the join envelope. It returns false when the failure
// must fail the run.
func (s *graphScheduler) absorbOptionalFailure(
	node *graphNode, attempt *graphNodeAttempt,
) bool {
	region, source := s.plan.branchFor(node.node.ID)
	if region == nil || !region.optional[source] {
		return false
	}
	if !optionalFailureNodeTypes[node.node.Type] {
		return false
	}
	if !optionalFailureCodes[attempt.code] {
		return false
	}
	failures := s.optionalFailures[region.join]
	if failures == nil {
		failures = map[string]string{}
		s.optionalFailures[region.join] = failures
	}
	// Two concurrent failures can only map to the same source through an
	// already-invalid region; the topologically first one is the deterministic
	// branch result.
	if existing, present := failures[source]; present && existing != attempt.code {
		if node.index > s.plan.nodes[source].index {
			return true
		}
	}
	failures[source] = attempt.code
	return true
}

// stopAndCommit cancels the in-flight branches, waits until every node goroutine
// has returned (including the sandbox cleanup proof), emits the skips that the
// failure caused and finally commits the node's terminal decision once.
func (s *graphScheduler) stopAndCommit(attempt *graphNodeAttempt) executionOutcome {
	s.cancel()
	s.drain()
	s.skipUnsettled()
	switch attempt.kind {
	case graphAttemptCancelled:
		return s.commitCancelled(attempt)
	case graphAttemptWaiting:
		return s.commitWaiting(attempt)
	default:
		return s.commitFailure(attempt)
	}
}

// stopForCommittedTerminal stops the branches before returning a terminal outcome
// the node already committed itself.
func (s *graphScheduler) stopForCommittedTerminal(terminal *executionOutcome) (executionOutcome, bool) {
	s.cancel()
	s.drain()
	s.skipUnsettled()
	return *terminal, true
}

// drain receives every in-flight result without scheduling new work.
func (s *graphScheduler) drain() {
	for s.inFlight > 0 {
		result := <-s.results
		s.inFlight--
		id := result.node.node.ID
		if s.run.state[id] == "" {
			switch {
			case result.succeeded:
				s.run.state[id] = "succeeded"
				s.run.outputs[id] = result.output
			case result.terminal != nil && result.terminal.outcome == "succeeded":
				s.run.state[id] = "succeeded"
			default:
				s.run.state[id] = "failed"
			}
		}
		s.recordEvidence(result.node, result.attempt)
	}
}

// skipUnsettled emits the skipped fact for every node that never started.
func (s *graphScheduler) skipUnsettled() {
	for _, id := range s.plan.order {
		if s.run.state[id] != "" {
			continue
		}
		s.run.state[id] = "skipped"
		s.runner.emitNode(s.base, id, "skipped")
	}
}

// failRunState commits a scheduler-detected refusal (activation/deadlock) through
// the same fail-fast path as a node failure.
func (s *graphScheduler) failRunState(code, message string) executionOutcome {
	return s.stopAndCommit(&graphNodeAttempt{
		kind: graphAttemptFailed, code: code, message: message,
	})
}

// commitFailure submits the failed run result after the branches stopped. A run
// whose sandbox already ran keeps its exit evidence (the failed node's own facts,
// otherwise the deterministic last-sandbox record); a run that never touched a
// sandbox stays a deterministic refusal.
func (s *graphScheduler) commitFailure(attempt *graphNodeAttempt) executionOutcome {
	message := attempt.message
	if attempt.sandboxErr != nil {
		message = message + ": " + errString(attempt.sandboxErr)
	}
	if attempt.failureEvidence {
		s.runner.uploadFailureEvidence(s.base, attempt.code, message, attempt.sandbox, nil, nil)
	}
	outcome, evidence := attempt.sandbox, attempt.evidence
	if !outcome.SandboxStarted {
		outcome = s.last
		evidence = nil
	}
	if attempt.startedForExit || s.started || outcome.SandboxStarted {
		if evidence == nil {
			evidence = s.runner.exitEvidence(s.runner.heartbeatCount(), outcome, nil)
		}
		return s.runner.failAfterStart(s.base, attempt.code, message, outcome, evidence)
	}
	return s.runner.refuse(s.base, attempt.code, message)
}

// commitCancelled confirms the attempt exit for a cancelled node after the other
// branches stopped; a cancellation is not a failed result.
func (s *graphScheduler) commitCancelled(attempt *graphNodeAttempt) executionOutcome {
	evidence := attempt.evidence
	if evidence == nil {
		evidence = s.runner.exitEvidence(s.runner.heartbeatCount(), attempt.sandbox, nil)
	}
	if err := s.runner.confirmExit(s.base, attempt.sandbox.SandboxStarted, evidence); err != nil {
		return executionOutcome{outcome: "cancelled", code: "cancel_requested",
			err: err.Error(), sandbox: sandboxReportOf(attempt.sandbox), failedStatus: true}
	}
	return executionOutcome{outcome: "cancelled", code: "cancel_requested",
		runState: s.runner.readRunState(s.base), sandbox: sandboxReportOf(attempt.sandbox)}
}

// commitWaiting hands the run over at its stable checkpoint after the other
// branches stopped.
func (s *graphScheduler) commitWaiting(attempt *graphNodeAttempt) executionOutcome {
	evidence := attempt.evidence
	if evidence == nil {
		evidence = s.runner.exitEvidence(s.runner.heartbeatCount(), attempt.sandbox, nil)
	}
	if attempt.proposal == nil {
		return s.runner.failAfterStart(s.base, CodeSandboxFailed,
			"the node waited without a result proposal", attempt.sandbox, evidence)
	}
	if err := s.runner.deps.Client.SubmitResult(
		s.base, s.runner.lease.RunID, s.runner.lease.AttemptToken, *attempt.proposal,
	); err != nil {
		code := attempt.fallbackCode
		if code == "" {
			code = CodeSandboxFailed
		}
		return s.runner.failAfterStart(s.base, code,
			"the waiting result was refused: "+err.Error(), attempt.sandbox, evidence)
	}
	if err := s.runner.confirmExit(s.base, attempt.sandbox.SandboxStarted, evidence); err != nil {
		return executionOutcome{outcome: "error", code: attempt.fallbackCode,
			err:     "the waiting exit was refused: " + err.Error(),
			sandbox: sandboxReportOf(attempt.sandbox)}
	}
	return executionOutcome{outcome: attempt.outcomeName, code: attempt.code,
		runState: s.runner.readRunState(s.base), sandbox: sandboxReportOf(attempt.sandbox)}
}

// ---------------------------------------------------------------------------
// optional allowlists (S3/S6/N9)
// ---------------------------------------------------------------------------

// optionalFailureNodeTypes is the closed set of node types whose failures may be
// converged as error data: pure read/compute nodes only. An agent/call_app failure
// is never downgraded even when the branch is declared optional. `retrieve` is
// reserved for T088 and is not executable in this build.
var optionalFailureNodeTypes = map[string]bool{
	"python":    true,
	"condition": true,
	"join":      true,
	"retrieve":  true,
}

// optionalFailureCodes is the explicit allowlist of pure read/compute error codes
// an optional branch may fold into the join envelope. Everything outside it —// authorization/capability rejects, budget, approval, unknown-write
// reconciliation and every persistence/sandbox failure —still fails the run.
var optionalFailureCodes = map[string]bool{
	CodeNodeInputInvalid:        true, // binding/input shape, pure compute
	CodeNodeOutputInvalid:       true, // output schema mismatch, pure compute
	CodeConditionOperandInvalid: true,
	CodeMissingValue:            true,
	CodeToolCrash:               true, // the preset compute exited non-zero
	CodeToolOutputInvalid:       true,
	CodeToolArtifactInvalid:     true,
	// The fixed preset tool's deterministic data rejections (its frozen
	// vocabulary in worker/platform_worker/nodes/sales_stats.py and
	// deploy/sandbox). They are explicit read/compute errors, never writes.
	"unsupported_media_type": true,
	"formula_not_supported":  true,
	"field_too_large":        true,
	"invalid_date":           true,
	"invalid_amount":         true,
	"negative_amount":        true,
	"missing_column":         true,
	"extra_column":           true,
	"column_order_mismatch":  true,
	"invalid_row":            true,
	"duplicate_order_id":     true,
	"invalid_cell_type":      true,
}

// graphSandboxUnit derives a distinct sandbox unit id per graph node, so two
// branches can run two runsc units at the same time (the executor derives the
// sandbox id from the unit id).
func graphSandboxUnit(executionUnitID, nodeID string) string {
	return agentSandboxUnit(executionUnitID, "graph:"+nodeID)
}

// ---------------------------------------------------------------------------
// node dispatch
// ---------------------------------------------------------------------------

// executeGraphNode runs one activated node. The returned result carries the node's
// output on success or the attempt decision the scheduler must commit with.
func (r *runner) executeGraphNode(
	ctx context.Context, plan *graphPlan, inputs []InputArtifact,
	node *graphNode, view *graphNodeView,
) graphNodeResult {
	attempt := &graphNodeAttempt{}
	ctx = context.WithValue(ctx, graphAttemptKey{}, attempt)
	result := graphNodeResult{node: node, attempt: attempt}
	outcome, output, done := r.executeGraphNodeSegment(ctx, plan, inputs, node, view, attempt)
	if done {
		result.terminal = &outcome
		return result
	}
	result.output = output
	result.succeeded = true
	return result
}

// executeGraphNodeSegment dispatches one node type. done=true means the node ended
// the attempt (its decision is in the attempt record and/or the outcome).
func (r *runner) executeGraphNodeSegment(
	ctx context.Context, plan *graphPlan, inputs []InputArtifact,
	node *graphNode, view *graphNodeView, attempt *graphNodeAttempt,
) (executionOutcome, map[string]any, bool) {
	id := node.node.ID
	switch node.node.Type {
	case "start":
		output, err := r.executeStartNode(ctx, node.node, plan.definition, inputs)
		if err != nil {
			code := CodeNodeInputInvalid
			if errors.Is(err, errRunInputUnavailable) {
				code = CodeToolInputInvalid
			}
			return r.failGraph(ctx, id, code, err.Error()), nil, true
		}
		return executionOutcome{}, output, false
	case "python":
		execution := r.executePythonNode(ctx, node.node, view.available, inputs, nodeHost{
			sandboxUnit: graphSandboxUnit(r.options.ExecutionUnitID, id),
			failed:      func() { r.emitNode(ctx, id, "failed") },
			// The scheduler owns skip propagation.
			skip: func() {},
		})
		attempt.sandbox = execution.sandbox
		attempt.report = execution.report
		attempt.started = execution.sandbox.SandboxStarted
		if execution.terminal != nil {
			return *execution.terminal, nil, true
		}
		return executionOutcome{}, execution.output, false
	case "condition":
		return r.executeGraphCondition(ctx, node, view)
	case "join":
		return r.executeGraphJoin(ctx, node, view)
	case "agent":
		output, outcome, done := r.executeGraphAgent(ctx, plan, inputs, node, view, attempt)
		if done {
			return outcome, nil, true
		}
		return executionOutcome{}, output, false
	case "call_app":
		output, outcome, done := r.executeGraphCallApp(ctx, plan, inputs, node, view, attempt)
		if done {
			return outcome, nil, true
		}
		return executionOutcome{}, output, false
	case "model":
		// F04B/T086: one controlled generation; the platform validates the output
		// against node.output_schema and a schema failure is independent.
		output, outcome, done := r.executeGraphModel(ctx, node, view, attempt)
		if done {
			return outcome, nil, true
		}
		return executionOutcome{}, output, false
	case "end":
		outcome := r.executeEndNode(ctx, plan.definition, node.node, view.available,
			view.last, view.report)
		return outcome, nil, true
	default:
		return r.failGraph(ctx, id, CodeToolUnsupportedGraph,
			"the executor cannot run the node type "+node.node.Type), nil, true
	}
}

// executeGraphCondition resolves the node's bindings, selects one port and
// publishes the run-view output {"port":..., "matched_case":...}.
func (r *runner) executeGraphCondition(
	ctx context.Context, node *graphNode, view *graphNodeView,
) (executionOutcome, map[string]any, bool) {
	id := node.node.ID
	r.emitNode(ctx, id, "pending")
	r.emitNode(ctx, id, "running")
	bindings, err := resolveNodeBindings(node.node, view.available)
	if err != nil {
		return r.failGraphNodeBeforeSandbox(ctx, node, CodeNodeInputInvalid, err.Error()), nil, true
	}
	output, err := evaluateCondition(node, bindings)
	if err != nil {
		return r.failGraphNodeBeforeSandbox(ctx, node, codeOfNodeError(err), err.Error()), nil, true
	}
	canonical, err := canonicalNodeDocument(output)
	if err != nil {
		return r.failGraphNodeAfterSandbox(ctx, node, CodeNodeOutputInvalid,
			"the condition output cannot be encoded"), nil, true
	}
	artifactID, err := r.uploadNodeArtifact(ctx, "node_output", "application/json", canonical, "artifact")
	if err != nil {
		return r.failGraphNodeAfterSandbox(ctx, node, CodeSandboxFailed,
			"registering the condition port failed: "+err.Error()), nil, true
	}
	if err := r.deps.Client.RecordNodeResults(ctx, r.lease.RunID, r.lease.AttemptToken,
		[]NodeResult{{NodeID: id, Kind: "data", ArtifactID: artifactID, Label: "port"}}); err != nil {
		return r.failGraphNodeAfterSandbox(ctx, node, CodeSandboxFailed,
			"recording the condition port failed: "+err.Error()), nil, true
	}
	r.emitNode(ctx, id, "succeeded")
	return executionOutcome{}, output, false
}

// executeGraphJoin merges exactly the region's activated branches into the
// contract's results envelope and runs once per region activation.
func (r *runner) executeGraphJoin(
	ctx context.Context, node *graphNode, view *graphNodeView,
) (executionOutcome, map[string]any, bool) {
	id := node.node.ID
	r.emitNode(ctx, id, "pending")
	r.emitNode(ctx, id, "running")
	branches := view.branches
	if branches == nil {
		return r.failGraphNodeBeforeSandbox(ctx, node, CodeInvalidRegion,
			"the join has no settled branch view"), nil, true
	}

	results := make(map[string]any, len(branches.states))
	for source, state := range branches.states {
		status := state
		if status == "" {
			// Every predecessor is settled before the join is activated; an empty
			// state can only mean the branch was never activated.
			status = "skipped"
		}
		entry := map[string]any{"status": status}
		if code, failed := branches.failed[source]; failed {
			// An optional branch's explicit pure-compute failure is error data.
			entry["status"] = "failed"
			entry["error_code"] = code
		} else if status == "succeeded" {
			entry["value"] = branches.outputs[source]
		}
		results[source] = entry
	}
	envelope := map[string]any{"results": results}
	canonical, err := canonicalNodeDocument(envelope)
	if err != nil {
		return r.failGraphNodeAfterSandbox(ctx, node, CodeNodeOutputInvalid,
			"the join envelope cannot be encoded"), nil, true
	}
	artifactID, err := r.uploadNodeArtifact(ctx, "node_output", "application/json", canonical, "artifact")
	if err != nil {
		return r.failGraphNodeAfterSandbox(ctx, node, CodeSandboxFailed,
			"registering the join envelope failed: "+err.Error()), nil, true
	}
	if err := r.deps.Client.RecordNodeResults(ctx, r.lease.RunID, r.lease.AttemptToken,
		[]NodeResult{{NodeID: id, Kind: "data", ArtifactID: artifactID, Label: "results"}}); err != nil {
		return r.failGraphNodeAfterSandbox(ctx, node, CodeSandboxFailed,
			"recording the join envelope failed: "+err.Error()), nil, true
	}
	r.emitNode(ctx, id, "succeeded")
	return executionOutcome{}, envelope, false
}

// executeGraphAgent runs one agent node through the shared controlled loop by
// presenting it as the agent of a one-node linear view. The view shares the
// graph's upstream outputs, its sealed-node scope and the deterministic evidence.
func (r *runner) executeGraphAgent(
	ctx context.Context, plan *graphPlan, inputs []InputArtifact, node *graphNode,
	view *graphNodeView, attempt *graphNodeAttempt,
) (map[string]any, executionOutcome, bool) {
	config, err := validateAgentNode(node.node, r.manifest)
	if err != nil {
		return nil, r.failGraphNodeBeforeSandbox(ctx, node, codeOfPlanError(err), err.Error()), true
	}
	linearView := r.graphLinearView(plan, inputs, node.node, view)
	linearView.plan.agent = node.node
	linearView.plan.agentConfig = config
	output, outcome, done := r.runAgentNode(ctx, linearView)
	attempt.sandbox = linearView.outcome
	attempt.report = linearView.report
	attempt.started = linearView.outcome.SandboxStarted
	if done {
		return nil, outcome, true
	}
	return output, executionOutcome{}, false
}

// executeGraphCallApp runs one call_app node through the shared delegation code by
// presenting it as the call_app of a one-node linear view.
func (r *runner) executeGraphCallApp(
	ctx context.Context, plan *graphPlan, inputs []InputArtifact, node *graphNode,
	view *graphNodeView, attempt *graphNodeAttempt,
) (map[string]any, executionOutcome, bool) {
	config, err := validateCallAppNode(node.node)
	if err != nil {
		return nil, r.failGraphNodeBeforeSandbox(ctx, node, codeOfPlanError(err), err.Error()), true
	}
	linearView := r.graphLinearView(plan, inputs, node.node, view)
	linearView.plan.callApp = node.node
	linearView.plan.callAppConfig = config
	output, outcome, done := r.runCallAppNode(ctx, linearView)
	attempt.sandbox = linearView.outcome
	attempt.report = linearView.report
	attempt.started = linearView.outcome.SandboxStarted
	if done {
		return nil, outcome, true
	}
	return output, executionOutcome{}, false
}

// graphLinearView is the one-node linear view the agent/call_app segment code
// reads. It never rewrites the graph plan: it only carries the start node, the
// upstream namespace, the sealed scope and the scheduler's skip hook.
func (r *runner) graphLinearView(
	plan *graphPlan, inputs []InputArtifact, node *manifestNode, view *graphNodeView,
) *linearRun {
	startNode := plan.nodes[plan.startID].node
	return &linearRun{
		plan: &linearPlan{
			definition: plan.definition,
			start:      startNode,
			order:      []*manifestNode{startNode, node},
		},
		inputs:    inputs,
		start:     view.start,
		available: view.available,
		scopeIDs:  append([]string(nil), plan.order...),
		outcome:   view.last,
		report:    view.report,
		// The scheduler owns skip propagation: after a failure it emits the
		// skipped facts for the nodes that never started.
		skip: func() {},
	}
}

// failGraph marks one node failed and fails the run. A node running under the
// scheduler only records the failure; the scheduler stops the other branches
// first and then commits the result.
func (r *runner) failGraph(
	ctx context.Context, nodeID, code, message string,
) executionOutcome {
	r.emitNode(ctx, nodeID, "failed")
	return r.failGraphRun(ctx, code, message)
}

// failGraphNodeBeforeSandbox is a join/condition refusal that needs no sandbox.
func (r *runner) failGraphNodeBeforeSandbox(
	ctx context.Context, node *graphNode, code, message string,
) executionOutcome {
	return r.failGraph(ctx, node.node.ID, code, message)
}

// failGraphNodeAfterSandbox is a platform-side refusal after earlier work ran.
func (r *runner) failGraphNodeAfterSandbox(
	ctx context.Context, node *graphNode, code, message string,
) executionOutcome {
	return r.failGraph(ctx, node.node.ID, code, message)
}

// failGraphRun records the failed run decision. A node running under the scheduler
// only records it here (the scheduler owns the commit and never lets a node
// goroutine read scheduler-owned run state or submit the result before the other
// branches stopped).
func (r *runner) failGraphRun(
	ctx context.Context, code, message string,
) executionOutcome {
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
