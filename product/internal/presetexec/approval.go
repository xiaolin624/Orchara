package presetexec

// F04B/T087: the human approval node.
//
// An approval node is a *business* decision, not a capability authorization. The
// executor never decides anything itself and never authorizes a later external
// write: it asks the platform to prepare (or replay) the one server-built
// approval for this run/node/proposal, parks the attempt at the stable checkpoint
// `approval:<approval_id>` while the row is pending, and — only once the human
// approved — publishes the decision as the node's data output and continues.
//
// Replay is idempotent on the server (`UNIQUE(run_id, node_key, params_sha256)`):
// the resumed attempt asks again with the same canonical proposal and receives
// the original approval instead of creating a second one. A changed proposal is a
// different approval (different `params_sha256`), never a reuse of the old
// decision.
//
// The node produces no data artifact while it waits. A rejected/expired/invalidated
// approval fails the node and the Run with the stable codes below; a finished
// upstream node is reused by the graph scheduler's recovery seam.

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"time"

	"orchara/platform/internal/uuid"
)

// Approval-node stable codes.
const (
	// CodeApprovalRequired is the waiting proposal's error_code while the human
	// has not decided yet.
	CodeApprovalRequired = "approval_required"
	// CodeApprovalRejected fails the node/Run when the human rejected.
	CodeApprovalRejected = "approval_rejected"
	// CodeApprovalExpired fails the node/Run when the approval expired.
	CodeApprovalExpired = "approval_expired"
	// CodeApprovalInvalidated fails the node/Run when the parked approval was
	// invalidated (cancelled run, revoked principal, terminal run).
	CodeApprovalInvalidated = "approval_invalidated"
	// CodeApprovalPrepareFailed is a platform-side refusal to prepare/replay the
	// approval (stale attempt, unusable sealed definition, object store). It is
	// documented in reports/f04b/T087-approval-node.md.
	CodeApprovalPrepareFailed = "approval_prepare_failed"
)

// approvalTitleLimit mirrors the server's display-title cap. The contract schema
// only says "string"; the server enforces the bound and the executor refuses to
// ask for something the server would reject.
const approvalTitleLimit = 200

// approvalConfig is the sealed approval node config.
type approvalConfig struct {
	Title          string `json:"title"`
	ExpiresSeconds int    `json:"expires_seconds"`
}

// parseApprovalConfig re-proves the sealed config and the required `proposal`
// input. The executor deliberately keeps the shape check here (plan time) and the
// value/object check at run time: a literal proposal is known now, a ref becomes
// known when its upstream node produced its output.
func parseApprovalConfig(node *manifestNode) (*approvalConfig, error) {
	if len(node.Config) == 0 {
		return nil, fmt.Errorf("the approval node has no config")
	}
	var parsed approvalConfig
	if err := json.Unmarshal(node.Config, &parsed); err != nil {
		return nil, fmt.Errorf("the approval config is not readable: %w", err)
	}
	parsed.Title = strings.TrimSpace(parsed.Title)
	if parsed.Title == "" {
		return nil, fmt.Errorf("the approval config has no title")
	}
	if len([]rune(parsed.Title)) > approvalTitleLimit {
		return nil, fmt.Errorf("the approval title must be at most %d characters", approvalTitleLimit)
	}
	if parsed.ExpiresSeconds < 60 || parsed.ExpiresSeconds > 86400 {
		return nil, fmt.Errorf("expires_seconds must be between 60 and 86400")
	}
	binding, present := node.Inputs["proposal"]
	if !present {
		return nil, fmt.Errorf("the approval node has no `proposal` input")
	}
	switch {
	case binding.Ref != "" && len(binding.Literal) > 0:
		return nil, fmt.Errorf("the `proposal` input must be exactly one of literal or ref")
	case binding.Ref == "" && len(binding.Literal) == 0:
		return nil, fmt.Errorf("the `proposal` input must be exactly one of literal or ref")
	case len(binding.Literal) > 0:
		var value any
		if err := json.Unmarshal(binding.Literal, &value); err != nil {
			return nil, fmt.Errorf("the `proposal` literal is not valid JSON: %w", err)
		}
		if _, ok := value.(map[string]any); !ok {
			return nil, fmt.Errorf("the `proposal` literal must be a JSON object")
		}
	case binding.Ref != "":
		base, _, _ := strings.Cut(binding.Ref, "#")
		if base != "input" && !strings.HasPrefix(base, "node:") {
			return nil, fmt.Errorf("the `proposal` ref must start with input# or node:<id>#")
		}
	}
	return &parsed, nil
}

// executeGraphApproval asks the platform for the one approval of this node and
// either parks the attempt (pending) or publishes the decision (approved).
func (r *runner) executeGraphApproval(
	ctx context.Context, run *graphRun, node *graphNode,
) (executionOutcome, map[string]any, bool) {
	id := node.node.ID
	config := node.approval
	if config == nil {
		return r.failGraphNodeBeforeSandbox(ctx, run, node, CodeBadConfig,
			"the approval node was not planned"), nil, true
	}
	r.emitNode(ctx, id, "pending")
	r.emitNode(ctx, id, "running")

	bindings, err := resolveNodeBindings(node.node, run.available())
	if err != nil {
		return r.failGraphNodeBeforeSandbox(ctx, run, node, CodeNodeInputInvalid,
			err.Error()), nil, true
	}
	rawProposal, present := bindings["proposal"]
	if !present {
		return r.failGraphNodeBeforeSandbox(ctx, run, node, CodeNodeInputInvalid,
			id+".proposal: the approval node has no bound proposal"), nil, true
	}
	proposal, ok := rawProposal.(map[string]any)
	if !ok {
		return r.failGraphNodeBeforeSandbox(ctx, run, node, CodeNodeInputInvalid,
			id+".proposal: the approval proposal must be a JSON object"), nil, true
	}

	// The server builds and seals the proposal (canonical hash, approver, expiry)
	// and returns the original row on replay. The worker contributes no text and
	// no decision.
	approval, err := r.deps.Client.PrepareNodeApproval(
		ctx, r.lease.RunID, r.lease.AttemptToken, NodeApprovalRequest{
			NodeID:         id,
			Title:          config.Title,
			ExpiresSeconds: config.ExpiresSeconds,
			Proposal:       proposal,
		},
	)
	if err != nil {
		return r.failGraph(ctx, run, id, CodeApprovalPrepareFailed,
			"the node approval could not be prepared: "+err.Error()), nil, true
	}

	switch approval.State {
	case "pending":
		return r.waitGraphApproval(ctx, run, node, approval), nil, true
	case "approved":
		return r.publishGraphApproval(ctx, run, node, approval, proposal)
	case "rejected":
		return r.failGraph(ctx, run, id, CodeApprovalRejected,
			"the node approval was rejected; the run stops before any later node"), nil, true
	case "expired":
		return r.failGraph(ctx, run, id, CodeApprovalExpired,
			"the node approval expired; the run stops before any later node"), nil, true
	case "invalidated":
		return r.failGraph(ctx, run, id, CodeApprovalInvalidated,
			"the node approval was invalidated; the run stops before any later node"), nil, true
	default:
		return r.failGraph(ctx, run, id, CodeApprovalPrepareFailed,
			"the node approval returned the unknown state "+approval.State), nil, true
	}
}

// waitGraphApproval parks the attempt at the fixed approval checkpoint. The
// supervisor exit fact is certified before the platform may create the one
// resume job; nothing here reports node success.
func (r *runner) waitGraphApproval(
	ctx context.Context, run *graphRun, node *graphNode, approval *NodeApproval,
) executionOutcome {
	if err := r.deps.Client.SubmitResult(
		ctx, r.lease.RunID, r.lease.AttemptToken, Proposal{
			EventID:        uuid.NewString(),
			SuggestedState: "waiting_approval",
			CheckpointID:   "approval:" + approval.ID,
			ErrorCode:      CodeApprovalRequired,
		},
	); err != nil {
		return r.failGraph(ctx, run, node.node.ID, CodeApprovalPrepareFailed,
			"the approval waiting result was refused: "+err.Error())
	}
	evidence := r.exitEvidence(r.heartbeatCount(), run.last, nil)
	if err := r.confirmExit(ctx, run.started, evidence); err != nil {
		return executionOutcome{
			outcome:  "error",
			code:     CodeApprovalPrepareFailed,
			err:      "the approval exit confirmation was refused: " + err.Error(),
			runState: r.readRunState(ctx),
			sandbox:  run.report,
		}
	}
	return executionOutcome{
		outcome:  "waiting_approval",
		code:     CodeApprovalRequired,
		runState: r.readRunState(ctx),
		sandbox:  run.report,
	}
}

// publishGraphApproval writes the approved decision as the node's data output.
// The output shape is documented in reports/f04b/T087-approval-node.md; it
// carries only server decision facts plus the resolved proposal the human saw.
func (r *runner) publishGraphApproval(
	ctx context.Context, run *graphRun, node *graphNode,
	approval *NodeApproval, proposal map[string]any,
) (executionOutcome, map[string]any, bool) {
	id := node.node.ID
	decidedAt := ""
	if approval.DecidedAt != nil {
		decidedAt = approval.DecidedAt.UTC().Format(time.RFC3339)
	}
	output := map[string]any{
		"approval_id":   approval.ID,
		"decision":      "approved",
		"decided_at":    decidedAt,
		"params_sha256": approval.ParamsSHA256,
		"proposal":      proposal,
	}
	if err := validateSchema(node.node.OutputSchema, output, "#/data"); err != nil {
		return r.failGraphNodeBeforeSandbox(ctx, run, node, CodeNodeOutputInvalid,
			"the approval output does not match the node output_schema: "+err.Error()), nil, true
	}
	canonical, err := canonicalNodeDocument(output)
	if err != nil {
		return r.failGraphNodeBeforeSandbox(ctx, run, node, CodeNodeOutputInvalid,
			"the approval output cannot be encoded"), nil, true
	}
	artifactID, err := r.uploadNodeArtifact(ctx, "node_output", "application/json", canonical, "artifact")
	if err != nil {
		return r.failGraphNodeAfterSandbox(ctx, run, node, CodeSandboxFailed,
			"registering the approval decision failed: "+err.Error()), nil, true
	}
	if err := r.deps.Client.RecordNodeResults(ctx, r.lease.RunID, r.lease.AttemptToken,
		[]NodeResult{{NodeID: id, Kind: "data", ArtifactID: artifactID, Label: "data"}}); err != nil {
		return r.failGraphNodeAfterSandbox(ctx, run, node, CodeSandboxFailed,
			"recording the approval decision failed: "+err.Error()), nil, true
	}
	r.emitNode(ctx, id, "succeeded")
	return executionOutcome{}, output, false
}
