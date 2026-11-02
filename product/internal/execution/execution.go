// Package execution is F03's execution loop: claim/lease/attempt exit (T029)
// and, from T031, the worker result proposal, the identity-bearing exit
// evidence and the unified idempotent finalizer.
//
// It owns the only code path that turns a `ready` job into a leased attempt and
// the only code path that turns a proposed result plus supervisor exit evidence
// into a terminal Run:
//
//	Claim         one ready job whose run is queued and whose snapshot compiler
//	              version the caller supports -> one live run_attempts row, one
//	              global execution-slot allocation, job leased, run running.
//	Heartbeat     renew a live attempt's lease and report the run's control
//	              facts (cancel intent, policy revision, budget).
//	SubmitResult  store the worker's result proposal on the attempt, set the run
//	              phase to finalizing and never commit a terminal state by
//	              itself.
//	ConfirmExit   record supervisor exit evidence, revoke the attempt, release
//	              the slot and either return the work to the queue (evidence
//	              says it never started), park the run for recovery (no result)
//	              or run the finalizer (result present).
//
// Two invariants shape every query here:
//
//   - **The database clock is the only clock.** Lease deadlines, heartbeat
//     timestamps and expiry are all decided with `now()` in the same SQL
//     statement that writes the row; Go never compares a wall clock to decide
//     whether a lease is live (ADR 0004 §4, extended by §F03).
//   - **Only the trusted server process writes attempts.** Workers present a
//     token over the internal mTLS listener; they never touch `platform.*`
//     directly (`orchara_worker` has no schema privileges).
//
// F03 boundary: run_events, cancel arbitration and the conservative
// recovery/unknown-operation sweep are T032/T036/T038. This package writes the
// terminal transition, but no event row, no cancel decision and no new attempt
// after an unproven exit.
package execution

import (
	"database/sql"
	"encoding/json"
	"errors"
	"strings"
	"time"

	"orchara/platform/internal/objectstore"
)

// Lease and input bounds. They are deliberately fixed constants rather than
// per-request values: a worker cannot name its own lease length.
const (
	// DefaultLeaseTTL is the documented claim lease (BACKGROUND-JOBS §1 uses the
	// same 30s for maintenance work; EXECUTION.md §4 requires the supervisor to
	// keep heartbeating rather than hold an unbounded claim).
	DefaultLeaseTTL = 30 * time.Second
	// MaxLeaseTTL bounds a configured lease so a misconfiguration cannot park a
	// run for a day.
	MaxLeaseTTL = time.Hour
	// MinLeaseTTL is the shortest configured lease; below a second the
	// heartbeat protocol cannot be exercised deterministically.
	MinLeaseTTL = time.Second

	// LeaseProtocolVersion is the protocol version the claim advertises and the
	// exit evidence must echo. It is a constant, not a request value.
	LeaseProtocolVersion = "1.0"

	// MaxWorkerID/maxExecutionUnitID bound the two free-form identifiers. They
	// are diagnostics and attempt binding, not credentials.
	maxIdentifierLength = 128
	// maxSupportedCompilers bounds the accepted compiler set; the request body is
	// also capped by the HTTP layer.
	maxSupportedCompilers = 32
	// maxCheckpointID bounds `run_attempts.checkpoint_id`. LangGraph checkpoint
	// ids are UUID-shaped; the bound only stops an unbounded text value.
	maxCheckpointID = 256
	// maxErrorCode bounds a worker-supplied failure code.
	maxErrorCode = 128
)

// Quota coordinates. Migration 0016 seeds exactly this counter.
const (
	quotaScope     = "global"
	quotaDimension = "execution_slots"
)

// Typed errors the HTTP layer maps to contract codes. Callers use errors.Is.
var (
	// ErrStaleAttempt is 409 stale_attempt: the token is unknown for this run,
	// revoked, expired, already exited or bound to another execution unit.
	ErrStaleAttempt = errors.New("the attempt token does not own a live lease")
	// ErrAttemptNotFound is 404 attempt_not_found for a malformed/unknown attempt
	// id on the exit endpoint.
	ErrAttemptNotFound = errors.New("that attempt does not exist")
	// ErrEvidenceConflict is 409 attempt_evidence_conflict: the attempt already
	// exited with different evidence.
	ErrEvidenceConflict = errors.New("conflicting exit evidence for an exited attempt")
	// ErrObjectStoreUnavailable is 503 object_store_unavailable: the sealed
	// manifest/input bytes cannot be read or verified, so no lease is issued.
	ErrObjectStoreUnavailable = errors.New("the sealed execution object is unavailable")
	// ErrObjectSigningUnavailable is 503 object_signing_unavailable: no signer (or
	// no public base URL) is configured, so the lease cannot carry real links.
	ErrObjectSigningUnavailable = errors.New("object link signing is unavailable")
	// ErrQuotaUnavailable is 503 execution_capacity_unavailable: the seeded quota
	// counter row is missing, so the server cannot decide capacity.
	ErrQuotaUnavailable = errors.New("the execution quota counter is not configured")
	// ErrAllocationConflict is an internal invariant violation: a ready job
	// already holds an unreleased allocation with its own holder key.
	ErrAllocationConflict = errors.New("quota allocation invariant violated")
	// ErrRunNotFound is 404 run_not_found on the result endpoint: the path names
	// no run at all. A token that does not belong to an existing run is refused
	// as stale_attempt instead, so the endpoint cannot enumerate runs.
	ErrRunNotFound = errors.New("that run does not exist")
	// ErrRunTerminal is 409 run_terminal: a result arrived for a Run that already
	// committed succeeded/failed/cancelled. A terminal Run never revives.
	ErrRunTerminal = errors.New("the run is already terminal")
	// ErrRunStateConflict is 409 run_state_conflict: the Run is non-terminal but
	// not executing (waiting/cancelling/needs_reconciliation). T035/T036 own
	// those states; the interim refusal is explicit instead of guessing.
	ErrRunStateConflict = errors.New("the run is not in an executing state")
	// ErrEventConflict is 409 event_conflict: the attempt already stored a
	// different result proposal. The first proposal is authoritative.
	ErrEventConflict = errors.New("a different result proposal is already stored")
	// ErrIdentityMismatch is 409 execution_identity_mismatch: the exit evidence
	// names a protocol version, manifest hash or worker image digest that is not
	// the one this attempt was claimed with.
	ErrIdentityMismatch = errors.New("the exit evidence does not match the claimed execution identity")
)

// OutputArtifactError is one located 422 for a succeeded proposal whose output
// artifact cannot be accepted. Code is a stable reason in details.code; Field is
// always output_artifact_id.
type OutputArtifactError struct {
	Code    string
	Message string
}

func (e *OutputArtifactError) Error() string { return e.Message }

// Output artifact refusal codes. They are details.code values, not top-level
// error codes: the contract 422 carries the located field plus the reason.
const (
	// CodeArtifactNotFound: no artifact row for the id.
	CodeArtifactNotFound = "artifact_not_found"
	// CodeArtifactNotOwned: the artifact belongs to another principal.
	CodeArtifactNotOwned = "artifact_not_owned"
	// CodeArtifactNotReady: uploading/scanning/quarantined or tombstoned.
	CodeArtifactNotReady = "artifact_not_ready"
	// CodeArtifactNotLinked: no run_artifacts row links it to this Run (the
	// registration endpoint is T033; until then the link is the admission).
	CodeArtifactNotLinked = "artifact_not_linked"
)

// ValidationError is one located request-shape problem (422 validation_failed).
type ValidationError struct {
	Field   string
	Message string
}

func (e *ValidationError) Error() string { return e.Message }

// ClaimRequest is the contract's claimJob body, normalised.
type ClaimRequest struct {
	WorkerID           string
	SupportedCompilers []string
	ExecutionUnitID    string
}

// Lease is exactly the contract's Lease schema (14 fields,
// additionalProperties:false, protocol_version constant "1.0").
type Lease struct {
	JobID               string          `json:"job_id"`
	RunID               string          `json:"run_id"`
	AttemptToken        string          `json:"attempt_token"`
	LeaseDeadline       string          `json:"lease_deadline"`
	Manifest            json.RawMessage `json:"manifest"`
	ProtocolVersion     string          `json:"protocol_version"`
	JobKind             string          `json:"job_kind"`
	ExecutionSnapshotID string          `json:"execution_snapshot_id"`
	ManifestSHA256      string          `json:"manifest_sha256"`
	ManifestURL         string          `json:"manifest_url"`
	InputArtifactID     string          `json:"input_artifact_id"`
	InputURL            string          `json:"input_url"`
	ResumeArtifactID    *string         `json:"resume_artifact_id"`
	ResumeURL           *string         `json:"resume_url"`
}

// Manifest is the snapshot's stored JSONB projection, passed through verbatim.
// It is a projection only: the authority is the sealed byte sequence behind
// manifest_url, and this type must never be re-serialised into a hash input.
// It is a json.RawMessage so the response embeds the object rather than a
// base64 string.
type Manifest = []byte

// HeartbeatRequest is the contract's Heartbeat body, normalised. RunID comes
// from the path; AttemptToken is the only credential.
type HeartbeatRequest struct {
	RunID           string
	AttemptToken    string
	ExecutionUnitID string
	// CheckpointConnections is the raw JSON array when the body carried one.
	// A nil slice means "not reported": the stored value is left unchanged.
	CheckpointConnections []byte
}

// HeartbeatReply is exactly the contract's HeartbeatReply schema.
type HeartbeatReply struct {
	LeaseDeadline   string `json:"lease_deadline"`
	CancelRequested bool   `json:"cancel_requested"`
	PolicyRevision  int64  `json:"policy_revision"`
	BudgetRemaining string `json:"budget_remaining"`
}

// ExitRequest is the contract's confirmAttemptExit body, normalised.
type ExitRequest struct {
	AttemptID       string
	ExecutionUnitID string
	Evidence        []byte
}

// ExitEvidence is the identity-bearing evidence T031 requires on top of the
// contract's open `evidence` object. The contract schema leaves evidence open
// (additionalProperties:true); T031 fixes these four required keys so a
// supervisor must state which execution identity it is certifying. The full
// evidence object is still stored verbatim.
type ExitEvidence struct {
	Started         bool
	ProtocolVersion string
	ManifestSHA256  string
	ImageDigest     string
}

// ExitResult is the contract's Accepted body for a confirmed exit.
type ExitResult struct {
	ID    string `json:"id"`
	State string `json:"state"`
}

// WorkerResult is the contract's WorkerResult body, normalised. It is a
// *proposal*: the state it suggests is only committed by the finalizer once the
// supervisor's exit evidence is present too.
type WorkerResult struct {
	AttemptToken     string  `json:"attempt_token"`
	EventID          string  `json:"event_id"`
	SuggestedState   string  `json:"suggested_state"`
	OutputArtifactID *string `json:"output_artifact_id,omitempty"`
	CheckpointID     *string `json:"checkpoint_id,omitempty"`
	ErrorCode        *string `json:"error_code,omitempty"`
}

// ResultOutcome is what the result endpoint needs after the proposal committed:
// the Run to return (the API reads it through the owner-only Run projection) and
// its owner, so the internal protocol never has to bypass that projection's ACL.
type ResultOutcome struct {
	RunID   string
	OwnerID string
}

// Service owns the execution primitives over one database handle, the object
// store and the link signer.
type Service struct {
	DB      *sql.DB
	Objects *objectstore.Store
	Signer  *objectstore.Signer
	// BaseURL is the absolute public base URL signed sealed links are minted
	// against. Empty means the process cannot issue a lease (fail closed).
	BaseURL string
	// LeaseTTL is the claim lease length. Values outside [MinLeaseTTL,
	// MaxLeaseTTL] fall back to DefaultLeaseTTL.
	LeaseTTL time.Duration
}

// New builds the service. Objects and Signer may be nil: a claim then fails
// closed with the typed 503 rather than issuing a lease without bytes or links.
func New(
	db *sql.DB,
	objects *objectstore.Store,
	signer *objectstore.Signer,
	baseURL string,
	leaseTTL time.Duration,
) *Service {
	if leaseTTL < MinLeaseTTL || leaseTTL > MaxLeaseTTL {
		leaseTTL = DefaultLeaseTTL
	}
	return &Service{
		DB:       db,
		Objects:  objects,
		Signer:   signer,
		BaseURL:  strings.TrimSuffix(strings.TrimSpace(baseURL), "/"),
		LeaseTTL: leaseTTL,
	}
}

// linkURL turns a signed token into the absolute transfer URL.
func (s *Service) linkURL(token string) string {
	return s.BaseURL + "/object-links/" + token
}
