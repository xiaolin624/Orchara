// Package execution is F03's claim/lease/attempt-exit primitive.
//
// It owns the only code path that turns a `ready` job into a leased attempt:
//
//	Claim         one ready job whose run is queued and whose snapshot compiler
//	              version the caller supports -> one live run_attempts row, one
//	              global execution-slot allocation, job leased, run running.
//	Heartbeat     renew a live attempt's lease and report the run's control
//	              facts (cancel intent, policy revision, budget).
//	ConfirmExit   record supervisor exit evidence, revoke the attempt, release
//	              the slot and either return the work to the queue (evidence
//	              says it never started) or hold it for the F03 finalizer.
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
// F03 boundary: result submission, run_events, the unified finalizer, cancel
// and recovery are T031–T038. This package records exit evidence and releases
// the slot; it never writes `runs.output_artifact_id`, `reported_result`,
// events or terminal states.
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

	// MaxWorkerID/maxExecutionUnitID bound the two free-form identifiers. They
	// are diagnostics and attempt binding, not credentials.
	maxIdentifierLength = 128
	// maxSupportedCompilers bounds the accepted compiler set; the request body is
	// also capped by the HTTP layer.
	maxSupportedCompilers = 32
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

// ExitResult is the contract's Accepted body for a confirmed exit.
type ExitResult struct {
	ID    string `json:"id"`
	State string `json:"state"`
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
