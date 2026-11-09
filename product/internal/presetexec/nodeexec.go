package presetexec

// C4.01/C4.02: the constrained node-execution input and the typed local outcome.
//
// A controlled node (the agent and call_app nodes) receives exactly one
// nodeExecution. The owning scheduler constructs it from
// sealed scheduling facts: the current sealed node, the run's linked inputs, an
// immutable snapshot of the upstream output namespace, the sealed node-id scope of
// this attempt, the validated node config it already proved, and the sandbox
// evidence observed so far. The input carries no scheduler state, no synthetic
// linear plan and no free artifact ids.
//
// The node returns a nodeOutcome: success, failure, cancellation or waiting. The
// outcome is local. The owning scheduler maps it to global behavior - commit the
// run terminal result, drain the other branches, emit skips, park the checkpoint -
// and remains the only writer of the Run terminal state. A node therefore never
// submits the run result, never skips siblings and never decides from a hidden
// context value which scheduler owns it.

import (
	"orchara/platform/internal/sandbox"
)

// nodeExecution is the explicit input one controlled node runs against.
type nodeExecution struct {
	// node is the current sealed manifest node.
	node *manifestNode
	// definition is the sealed definition the node belongs to. It is read-only
	// and is used only to classify a binding source by its sealed node type.
	definition *manifestDefinition
	// inputs are the run's linked input artifacts, a snapshot taken at start.
	inputs []InputArtifact
	// namespace is the upstream-output namespace the node's sealed bindings
	// resolve against, copied when the execution starts. Upstream outputs are
	// immutable once published, so no branch can mutate another branch's input
	// namespace after this snapshot exists.
	namespace map[string]any
	// startNodeID is the sealed start node's id (the `input#` source identity).
	startNodeID string
	// scopeNodeIDs is the sealed node-id authorization scope of this attempt,
	// snapshotted at start. It contains no artifact ids: the agent context can
	// only cite nodes this sealed attempt owns.
	scopeNodeIDs []string
	// agentConfig is the validated agent config when the scheduler already proved
	// it at plan build/dispatch time; nil for a node that is not an agent.
	agentConfig *agentConfig
	// callAppConfig is the validated fixed child-application version when the
	// scheduler already proved it at plan build/dispatch time; nil for a node
	// that is not a call_app.
	callAppConfig *callAppConfig
	// sandbox/report are the attempt's sandbox evidence observed before this node
	// started. The node updates them when one of its own sandboxes runs.
	sandbox sandbox.Outcome
	report  *SandboxReport
}

// nodeOutcomeKind is the closed set of local decisions a controlled node returns.
type nodeOutcomeKind string

const (
	// nodeOutcomeSucceeded: the node validated and registered its output.
	nodeOutcomeSucceeded nodeOutcomeKind = "succeeded"
	// nodeOutcomeFailed: the node refused locally; the scheduler commits.
	nodeOutcomeFailed nodeOutcomeKind = "failed"
	// nodeOutcomeCancelled: the node observed a cancellation.
	nodeOutcomeCancelled nodeOutcomeKind = "cancelled"
	// nodeOutcomeWaiting: the node parks the run at a stable checkpoint.
	nodeOutcomeWaiting nodeOutcomeKind = "waiting"
)

// nodeOutcome is one controlled node's typed local decision. It is not a run
// result and it never carries scheduler-owned state.
type nodeOutcome struct {
	kind nodeOutcomeKind
	// output is the validated output of a successful node.
	output map[string]any
	// code/message are the stable platform error code and the safe diagnostic.
	code    string
	message string

	// sandbox/report are the node's own latest sandbox facts; when the node ran
	// no sandbox they stay the facts observed before it started.
	sandbox sandbox.Outcome
	report  *SandboxReport

	// evidence is the attempt exit evidence the scheduler certifies after it
	// drained the other branches. failureEvidence asks the scheduler to upload
	// the safe failure evidence first; startedForExit certifies an attempt that
	// already did platform-side work even when no sandbox record survives.
	evidence        map[string]any
	failureEvidence bool
	startedForExit  bool

	// Waiting decisions carry the checkpoint proposal and the scheduler-facing
	// suggested state; the node refuses the proposal with the documented text
	// prefixes when the platform rejects it.
	proposal        *Proposal
	outcomeName     string
	fallbackCode    string
	proposalRefusal string
	exitRefusal     string
}

// copyNodeNamespace returns an independent map for the binding namespace. The
// values are the sealed upstream outputs, which are immutable once published.
func copyNodeNamespace(source map[string]any) map[string]any {
	namespace := make(map[string]any, len(source)+2)
	for nodeID, output := range source {
		namespace[nodeID] = output
	}
	return namespace
}

// sealedNodeType returns the sealed definition's node type for one node id. It is
// used only to classify a binding source (start/python/tool) for the context
// trace; the authorization decision is the sealed node-id scope.
func (e *nodeExecution) sealedNodeType(nodeID string) string {
	if e.definition == nil {
		return "tool"
	}
	for index := range e.definition.Nodes {
		if e.definition.Nodes[index].ID == nodeID {
			return e.definition.Nodes[index].Type
		}
	}
	return "tool"
}

// nodeSucceeded returns the local success decision carrying the registered output.
func (r *runner) nodeSucceeded(exec *nodeExecution, output map[string]any) nodeOutcome {
	return nodeOutcome{
		kind: nodeOutcomeSucceeded, output: output,
		sandbox: exec.sandbox, report: exec.report,
	}
}

// nodeFailed returns the local failure decision. The scheduler decides when to
// drain and commit it; the evidence is computed from the node's own sandbox facts.
func (r *runner) nodeFailed(exec *nodeExecution, code, message string) nodeOutcome {
	return nodeOutcome{
		kind: nodeOutcomeFailed, code: code, message: message,
		sandbox: exec.sandbox, report: exec.report,
		evidence:       r.exitEvidence(r.heartbeatCount(), exec.sandbox, nil),
		startedForExit: true,
	}
}

// nodeCancelled returns the local cancellation decision.
func (r *runner) nodeCancelled(exec *nodeExecution) nodeOutcome {
	return nodeOutcome{
		kind: nodeOutcomeCancelled, code: "cancel_requested",
		sandbox: exec.sandbox, report: exec.report,
		evidence: r.exitEvidence(r.heartbeatCount(), exec.sandbox, nil),
	}
}

// nodeWaiting returns the local waiting decision with its stable checkpoint.
func (r *runner) nodeWaiting(
	exec *nodeExecution, proposal Proposal, outcomeName, fallbackCode string,
) nodeOutcome {
	return nodeOutcome{
		kind: nodeOutcomeWaiting, code: proposal.ErrorCode,
		proposal: &proposal, outcomeName: outcomeName, fallbackCode: fallbackCode,
		sandbox: exec.sandbox, report: exec.report,
		evidence: r.exitEvidence(r.heartbeatCount(), exec.sandbox, nil),
	}
}

// applyGraphNodeOutcome records one node's local outcome on the graph attempt
// record and returns the terminal outcome the scheduler must commit. A nil result
// means the node succeeded and the scheduler publishes its output.
func applyGraphNodeOutcome(attempt *graphNodeAttempt, outcome nodeOutcome) *executionOutcome {
	attempt.sandbox = outcome.sandbox
	attempt.report = outcome.report
	report := sandboxReportOf(outcome.sandbox)
	switch outcome.kind {
	case nodeOutcomeSucceeded:
		return nil
	case nodeOutcomeFailed:
		attempt.kind = graphAttemptFailed
		attempt.code = outcome.code
		attempt.message = outcome.message
		attempt.evidence = outcome.evidence
		attempt.failureEvidence = outcome.failureEvidence
		attempt.startedForExit = outcome.startedForExit
		return &executionOutcome{outcome: "failed", code: outcome.code,
			err: outcome.message, sandbox: report}
	case nodeOutcomeCancelled:
		attempt.kind = graphAttemptCancelled
		attempt.code = "cancel_requested"
		attempt.evidence = outcome.evidence
		return &executionOutcome{outcome: "cancelled", code: "cancel_requested",
			sandbox: report}
	case nodeOutcomeWaiting:
		attempt.kind = graphAttemptWaiting
		attempt.code = outcome.code
		attempt.proposal = outcome.proposal
		attempt.outcomeName = outcome.outcomeName
		attempt.fallbackCode = outcome.fallbackCode
		attempt.evidence = outcome.evidence
		return &executionOutcome{outcome: outcome.outcomeName, code: outcome.code,
			sandbox: report}
	default:
		attempt.kind = graphAttemptFailed
		attempt.code = CodeSandboxFailed
		attempt.message = "the node returned an unsupported local outcome"
		return &executionOutcome{outcome: "failed", code: CodeSandboxFailed,
			err: attempt.message, sandbox: report}
	}
}
