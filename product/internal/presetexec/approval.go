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
// either parks the attempt (pending) or publishes the decision (approved). It
// runs on T085's concurrent scheduler: the node only records its decision on the
// attempt record, and the scheduler commits it after the other branches stopped.
func (r *runner) executeGraphApproval(
	ctx context.Context, node *graphNode, view *graphNodeView,
) (executionOutcome, map[string]any, bool) {
	id := node.node.ID
	config := node.approval
	if config == nil {
		return r.failGraph(ctx, id, CodeBadConfig,
			"the approval node was not planned"), nil, true
	}
	r.emitNode(ctx, id, "pending")
	r.emitNode(ctx, id, "running")

	bindings, err := resolveNodeBindings(node.node, view.available)
	if err != nil {
		return r.failGraph(ctx, id, CodeNodeInputInvalid,
			err.Error()), nil, true
	}
	rawProposal, present := bindings["proposal"]
	if !present {
		return r.failGraph(ctx, id, CodeNodeInputInvalid,
			id+".proposal: the approval node has no bound proposal"), nil, true
	}
	proposal, ok := rawProposal.(map[string]any)
	if !ok {
		return r.failGraph(ctx, id, CodeNodeInputInvalid,
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
		return r.failGraph(ctx, id, CodeApprovalPrepareFailed,
			"the node approval could not be prepared: "+err.Error()), nil, true
	}

	switch approval.State {
	case "pending":
		return r.waitGraphApproval(ctx, view, approval), nil, true
	case "approved":
		return r.publishGraphApproval(ctx, node, approval, proposal)
	case "rejected":
		return r.failGraph(ctx, id, CodeApprovalRejected,
			"the node approval was rejected; the run stops before any later node"), nil, true
	case "expired":
		return r.failGraph(ctx, id, CodeApprovalExpired,
			"the node approval expired; the run stops before any later node"), nil, true
	case "invalidated":
		return r.failGraph(ctx, id, CodeApprovalInvalidated,
			"the node approval was invalidated; the run stops before any later node"), nil, true
	default:
		return r.failGraph(ctx, id, CodeApprovalPrepareFailed,
			"the node approval returned the unknown state "+approval.State), nil, true
	}
}

// waitGraphApproval parks the attempt at the fixed approval checkpoint. The
// scheduler submits the waiting proposal and certifies the supervisor exit fact
// after the other branches stopped, so nothing here reports node success or
// commits a result itself.
func (r *runner) waitGraphApproval(
	ctx context.Context, view *graphNodeView, approval *NodeApproval,
) executionOutcome {
	// The launch snapshot carries the run's deterministic last-sandbox evidence;
	// the scheduler commits it with the waiting decision.
	evidence := r.exitEvidence(r.heartbeatCount(), view.last, nil)
	captureGraphWaiting(ctx, Proposal{
		EventID:        uuid.NewString(),
		SuggestedState: "waiting_approval",
		CheckpointID:   "approval:" + approval.ID,
		ErrorCode:      CodeApprovalRequired,
	}, "waiting_approval", view.last, evidence, CodeApprovalPrepareFailed)
	return executionOutcome{}
}

// publishGraphApproval writes the approved decision as the node's data output.
// The output shape is documented in reports/f04b/T087-approval-node.md; it
// carries only server decision facts plus the resolved proposal the human saw.
func (r *runner) publishGraphApproval(
	ctx context.Context, node *graphNode,
	approval *NodeApproval, proposal map[string]any,
) (executionOutcome, map[string]any, bool) {
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
		return r.failGraphNode(ctx, node, CodeNodeOutputInvalid,
			"the approval output does not match the node output_schema: "+err.Error()), nil, true
	}
	return r.publishNodeData(ctx, node, output, "data", "approval output", "approval decision")
}
