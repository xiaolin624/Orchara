package approvals

// F06/T050: exact-parameter approvals and safe resume.
//
// An approval is the *server-built* proposal for one parked operation. The
// worker never supplies the text the human reads: every field of the sealed
// proposal comes from the run's frozen snapshot manifest, the operation row, the
// connector policy row and the canonical params bytes the capability proxy wrote.
// The worker's own `approval_required` display is a status, never a source.
//
// Lifecycle:
//
//	PrepareTx   capability proxy: one pending approval per parked write operation,
//	            proposal artifact sealed, operation -> awaiting_approval. Idempotent
//	            for the same operation/params; changed params are a conflict.
//	Get/List    designated approver (or an administrator) only; everyone else 404.
//	Decide      row-locked decision: exact params hash, approver, current run and
//	            policy permission; same decision idempotent, opposite 409. Approve
//	            flips the operation to authorized (the resumed attempt still does
//	            its own permission/budget checks); reject/expire fail the waiting
//	            run through the F03/T035 decision store + resume transaction.
//
// The resume itself is never created here: internal/execution owns the single
// `maybeEnqueueResumeTx`, and this package only stores the decision through
// `ResolveWaiting`, exactly like T035 designed.

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"

	"orchara/platform/internal/execution"
	"orchara/platform/internal/objectstore"
	"orchara/platform/internal/uuid"
)

// DefaultTTL is how long a prepared approval waits for a human. Tests and a
// deployment that needs another window change the row's `expires_at`; the value
// is recorded in the sealed proposal so the page can show the real deadline.
const DefaultTTL = time.Hour

// Error codes are the stable API vocabulary for this aggregate. They are mapped
// one-to-one onto the contract error envelope by internal/api.
const (
	CodeNotFound          = "approval_not_found"
	CodeNotApprover       = "approver_mismatch"
	CodeNotPending        = "approval_not_pending"
	CodeExpired           = "approval_expired"
	CodeParamsMismatch    = "approval_params_mismatch"
	CodeDecisionConflict  = "decision_conflict"
	CodePermissionRevoked = "permission_revoked"
	CodePolicyRevoked     = "policy_revoked"
	CodeUsageConflict     = "operation_state_conflict"
)

// Error is one located, typed refusal. It never carries a proposal body, a
// credential or a provider payload.
type Error struct {
	Status  int
	Code    string
	Message string
	Field   string
}

func (e *Error) Error() string { return e.Message }

func failure(status int, code, message string) *Error {
	return &Error{Status: status, Code: code, Message: message}
}

// AsError renders a typed *Error from err (nil otherwise).
func AsError(err error) (*Error, bool) {
	var typed *Error
	if errors.As(err, &typed) && typed != nil {
		return typed, true
	}
	return nil, false
}

// Approval is the contract's public projection plus the fields the service needs
// to answer idempotently. `ProposalArtifactID` is the sealed proposal; the
// proposal bytes are not part of this struct.
type Approval struct {
	ID                 string
	RunID              string
	State              string
	ProposalArtifactID string
	ParamsSHA256       string
	ExpiresAt          time.Time
	ApproverID         string
	OperationID        string
	DecidedAt          *time.Time
}

// Service owns the approval table and the proposal artifact writer.
type Service struct {
	DB      *sql.DB
	Objects *objectstore.Store
	// Execution is the F03/T035 waiting/resume authority. It is used only after
	// the decision row is committed, so a resume failure never rewrites a decision.
	Execution *execution.Service
}

// New builds the service. Execution may be nil in a process that only reads
// approvals; every decision then fails closed (there is no other resume writer).
func New(db *sql.DB, objects *objectstore.Store, resume *execution.Service) *Service {
	return &Service{DB: db, Objects: objects, Execution: resume}
}

// Target is the *real* destination of a connector invocation, copied from the
// administrator-approved policy version and the validated call.
type Target struct {
	Origin     string            `json:"origin"`
	Method     string            `json:"method"`
	PathPrefix string            `json:"path_prefix"`
	Path       string            `json:"path"`
	Query      map[string]string `json:"query,omitempty"`
}

// Proposal is the sealed server-canonical document the human page renders. The
// closed shape is deliberate: it names the run, operation, capability, exact
// params/hash, real target, data destination, the frozen source versions and the
// deadline. It never contains a credential or a raw provider payload.
type Proposal struct {
	ApprovalVersion   int             `json:"approval_version"`
	RunID             string          `json:"run_id"`
	OperationID       string          `json:"operation_id"`
	LogicalKey        string          `json:"logical_key"`
	NodeKey           string          `json:"node_key"`
	CapabilityKind    string          `json:"capability_kind"`
	CapabilityVersion string          `json:"capability_version"`
	RiskClass         string          `json:"risk_class"`
	Target            Target          `json:"target"`
	Params            json.RawMessage `json:"params"`
	ParamsSHA256      string          `json:"params_sha256"`
	DataDestination   string          `json:"data_destination"`
	PolicySHA256      string          `json:"policy_sha256,omitempty"`
	ManifestSHA256    string          `json:"manifest_sha256"`
	SnapshotID        string          `json:"snapshot_id"`
	SnapshotSHA256    string          `json:"snapshot_sha256"`
	ExpiresAt         string          `json:"expires_at"`
}

// approvalDocument is the canonical params artifact reference the proposal
// fingerprint is checked against.
type approvalParamsArtifact struct {
	ObjectKey string
	SHA256    string
}

// nodeKeyForLogicalKey derives the stable node identity the contract's
// `approvals.node_key` asks for from the only identity the worker sends. The
// T043 agent loop uses `agent:<node_id>:model:<turn>` and
// `agent:<node_id>:tool:<call>`; a direct invocation has no node segment and is
// its own node.
func nodeKeyForLogicalKey(logicalKey string) string {
	key := strings.TrimSpace(logicalKey)
	parts := strings.Split(key, ":")
	if len(parts) >= 3 && parts[0] == "agent" && parts[1] != "" {
		return parts[0] + ":" + parts[1]
	}
	return key
}

// expiresAt normalises the deadline: a zero value means DefaultTTL from now.
func expiresAt(requested time.Time) time.Time {
	if requested.IsZero() {
		return time.Now().UTC().Add(DefaultTTL)
	}
	return requested.UTC()
}

// approverForOwnerTx resolves the designated human. For a user-owned run (every
// F04A path) it is the run owner. A service-account run uses its responsible
// human; a service account with no responsible human cannot start a high-impact
// approval, so preparation fails closed.
func approverForOwnerTx(ctx context.Context, tx *sql.Tx, ownerID string) (string, error) {
	var approver string
	err := tx.QueryRowContext(ctx, `
		SELECT principal_id::text FROM platform.users
		 WHERE principal_id = $1::uuid`, ownerID).Scan(&approver)
	if err == nil {
		return approver, nil
	}
	if !errors.Is(err, sql.ErrNoRows) {
		return "", fmt.Errorf("reading the run owner: %w", err)
	}
	err = tx.QueryRowContext(ctx, `
		SELECT responsible_user_id::text FROM platform.principals
		 WHERE id = $1::uuid AND kind = 'service' AND responsible_user_id IS NOT NULL`,
		ownerID).Scan(&approver)
	if errors.Is(err, sql.ErrNoRows) {
		return "", failure(422, "approver_unavailable",
			"the run owner has no designated human approver")
	}
	if err != nil {
		return "", fmt.Errorf("reading the responsible human: %w", err)
	}
	return approver, nil
}

// validateDecision normalises the closed decision vocabulary.
func validateDecision(decision string) (string, error) {
	switch strings.ToLower(strings.TrimSpace(decision)) {
	case "approve":
		return "approve", nil
	case "reject":
		return "reject", nil
	default:
		return "", &Error{Status: 422, Code: "validation_failed",
			Message: "decision must be approve or reject", Field: "decision"}
	}
}

// approvalFromRow scans the common column list.
func approvalFromRow(row *sql.Row) (*Approval, error) {
	var (
		approval  Approval
		decidedAt sql.NullTime
	)
	err := row.Scan(
		&approval.ID, &approval.RunID, &approval.State, &approval.ProposalArtifactID,
		&approval.ParamsSHA256, &approval.ExpiresAt, &approval.ApproverID,
		&approval.OperationID, &decidedAt,
	)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	if decidedAt.Valid {
		value := decidedAt.Time
		approval.DecidedAt = &value
	}
	return &approval, nil
}

const approvalColumns = `
	id::text, run_id::text, state, proposal_artifact_id::text, params_sha256,
	expires_at, approver_id::text, operation_id::text, decided_at`

// loadApprovalTx reads (and optionally locks) one approval.
func loadApprovalTx(ctx context.Context, tx *sql.Tx, approvalID string, lock bool) (*Approval, error) {
	query := `SELECT ` + approvalColumns + ` FROM platform.approvals WHERE id = $1::uuid`
	if lock {
		query += ` FOR UPDATE`
	}
	return approvalFromRow(tx.QueryRowContext(ctx, query, approvalID))
}

// validApprovalID reports whether the path value can name a row. An invalid UUID
// is an ordinary 404, never a database error.
func validApprovalID(value string) bool {
	return uuid.Valid(strings.TrimSpace(value))
}
