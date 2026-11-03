package execution

// F03/T038: conservative recovery — park on a lost execution host, and only
// re-queue after audited evidence proves the old unit and its writes are gone.
//
// EXECUTION.md §3 and STATE-MACHINES.md §2 are the authority:
//
//   - an expired lease is **parked**, never taken over: the Run moves to
//     `recovery_pending`, the job stays `held`, the allocation stays taken and no
//     attempt token is swapped;
//   - claim selects only `queued` Runs, so a parked Run cannot receive a second
//     executor; a live old worker's heartbeat is never treated as death;
//   - the only way out is `cmd/admin runs recover`, which proves (against the
//     real database and object store) that the old execution unit is not alive,
//     its checkpoint connections are drained, the frozen manifest and the input
//     are still usable, and records an audited evidence row in the same
//     transaction that finalizes the old attempt, revokes its token, re-arms
//     exactly one job and returns the Run to `queued`.
//
// Two command surfaces share this file's types:
//
//	cmd/admin runs sweep-leases [--grace 60s] [--dry-run]
//	cmd/admin runs recover --run <id> --evidence <json> [--check-only] [--actor-login <admin>]
//
// The sweep parks; it never requeues. The recover command is the only requeue.
// `recovery_reason` distinguishes a liveness loss (`lease_expired`) from T031's
// already-exited park (`exit_without_result`); both are recovered through the
// same checks.

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"strings"
	"time"

	"orchara/platform/internal/audit"
	"orchara/platform/internal/compiler"
	"orchara/platform/internal/jcs"
	"orchara/platform/internal/objectstore"
	"orchara/platform/internal/uuid"
)

// Recovery reasons. They are the closed `runs.recovery_reason` vocabulary.
const (
	// RecoveryReasonLeaseExpired is the sweep's park: the live attempt's lease
	// expired beyond the grace window, so the host is presumed lost (not dead).
	RecoveryReasonLeaseExpired = "lease_expired"
	// RecoveryReasonExitWithoutResult is T031's park: the supervisor proved the
	// unit exited but there is no result to commit.
	RecoveryReasonExitWithoutResult = "exit_without_result"
)

// Recovery window bounds. The grace window is the only judgement the command
// makes about liveness, and it is a deployment argument, never a request value.
const (
	// DefaultRecoveryGrace is the documented grace after the last heartbeat.
	DefaultRecoveryGrace = 60 * time.Second
	// MaxRecoveryGrace bounds a misconfigured grace so a typo cannot park or
	// recover a Run that heartbeated moments ago.
	MaxRecoveryGrace = time.Hour
	// maxRecoverySweep bounds one sweep pass; a second pass is idempotent.
	maxRecoverySweep = 100
	// maxRecoveryEvidenceBytes bounds the operator document before parsing.
	maxRecoveryEvidenceBytes = 64 * 1024
)

// Evidence document field bounds. Every string in the operator document is
// bounded so a pasted log or a credential cannot become the proof.
const (
	maxRecoveryUnitID       = 128
	maxRecoverySandboxID    = 128
	maxRecoveryCommand      = 512
	maxRecoveryObserved     = 512
	maxRecoveryNotes        = 1024
	maxRecoveryConnections  = 32
	recoveryEvidenceKindNew = "operator_recovery"
)

// RecoveryRefusal codes. They are stable snake_case codes printed by the CLI and
// documented in the T038 report; a refusal is a decision, not a crash.
const (
	RecoveryCodeRunNotFound            = "run_not_found"
	RecoveryCodeNotRecoveryPending     = "not_recovery_pending"
	RecoveryCodeAlreadyRecovered       = "already_recovered"
	RecoveryCodeOldExecutionStillLive  = "old_execution_still_live"
	RecoveryCodeDatabaseWritesInFlight = "database_writes_in_flight"
	RecoveryCodeArtifactUnavailable    = "artifact_unavailable"
	RecoveryCodeInputDeleted           = "input_deleted"
	RecoveryCodeManifestHashMismatch   = "manifest_hash_mismatch"
	RecoveryCodeManifestInvalid        = "manifest_invalid"
	RecoveryCodeInputHashMismatch      = "input_hash_mismatch"
	RecoveryCodeIdentityMismatch       = "execution_identity_mismatch"
	RecoveryCodeEvidenceRequired       = "evidence_required"
	RecoveryCodeEvidenceInvalid        = "evidence_invalid"
	RecoveryCodeEvidenceUnitMismatch   = "evidence_unit_mismatch"
	RecoveryCodeLiveAttemptConflict    = "live_attempt_conflict"
	RecoveryCodeJobUnavailable         = "job_unavailable"
	RecoveryCodeNeedsReconciliation    = "needs_reconciliation"
	RecoveryCodeActorNotFound          = "actor_not_found"
	RecoveryCodeCancelRequested        = "cancel_requested"
)

// RecoveryRefusal is a decided refusal: nothing was written and the CLI exits 1
// after printing the report. It is never a panic or an internal error.
type RecoveryRefusal struct {
	Code    string
	Message string
}

func (e *RecoveryRefusal) Error() string {
	if e.Message == "" {
		return e.Code
	}
	return e.Code + ": " + e.Message
}

func recoveryRefusal(code, format string, args ...any) *RecoveryRefusal {
	return &RecoveryRefusal{Code: code, Message: fmt.Sprintf(format, args...)}
}

// RecoveryCheck is one named verification in the operator report.
type RecoveryCheck struct {
	Name   string `json:"name"`
	Status string `json:"status"` // passed|failed|skipped
	Code   string `json:"code,omitempty"`
	Detail string `json:"detail,omitempty"`
}

// RecoveryReport is the JSON result of one recover invocation (and of a
// --check-only dry inspection). It never carries an attempt token or a secret.
type RecoveryReport struct {
	Status           string          `json:"status"` // recovered|check_only|refused
	Code             string          `json:"code,omitempty"`
	Message          string          `json:"message,omitempty"`
	CheckOnly        bool            `json:"check_only"`
	RunID            string          `json:"run_id,omitempty"`
	State            string          `json:"state,omitempty"`
	RecoveryReason   string          `json:"recovery_reason,omitempty"`
	RecoverySince    string          `json:"recovery_since,omitempty"`
	ExecutionUnitID  string          `json:"execution_unit_id,omitempty"`
	AttemptStartedAt string          `json:"attempt_started_at,omitempty"`
	HeartbeatAt      string          `json:"heartbeat_at,omitempty"`
	LeaseDeadline    string          `json:"lease_deadline,omitempty"`
	JobID            string          `json:"job_id,omitempty"`
	ResumedJobID     string          `json:"resumed_job_id,omitempty"`
	EvidenceSHA256   string          `json:"evidence_sha256,omitempty"`
	RecordedBy       string          `json:"recorded_by,omitempty"`
	RecordedAt       string          `json:"recorded_at,omitempty"`
	Checks           []RecoveryCheck `json:"checks"`
}

func (r *RecoveryReport) pass(name, detail string) {
	r.Checks = append(r.Checks, RecoveryCheck{Name: name, Status: "passed", Detail: detail})
}

func (r *RecoveryReport) skip(name, detail string) {
	r.Checks = append(r.Checks, RecoveryCheck{Name: name, Status: "skipped", Detail: detail})
}

// refuse records the failed check on the report and returns the typed refusal so
// the caller can print report + code and exit 1.
func (r *RecoveryReport) refuse(name, code, format string, args ...any) *RecoveryRefusal {
	refusal := recoveryRefusal(code, format, args...)
	r.Checks = append(r.Checks, RecoveryCheck{
		Name: name, Status: "failed", Code: code, Detail: refusal.Message,
	})
	return refusal
}

// RecoveryRequest is one `cmd/admin runs recover` invocation, normalised.
type RecoveryRequest struct {
	RunID string
	// EvidenceRaw is the operator JSON file bytes; empty means the flag was not
	// supplied and the command refuses with evidence_required.
	EvidenceRaw []byte
	// ActorLogin names the administrator recorded as the evidence author. Empty
	// resolves the bootstrap (earliest enabled human) administrator.
	ActorLogin string
	CheckOnly  bool
	Grace      time.Duration
}

// RecoveryFacts is the owner-facing projection of the parked/evidenced state of
// one Run. It is exposed through non-contract Run-detail headers (the contract
// Run schema has no recovery fields) and never carries the attempt token.
type RecoveryFacts struct {
	Reason         string
	Since          *time.Time
	EvidenceAt     *time.Time
	EvidenceSHA256 string
	ResumedJobID   *string
}

// LeaseSweepRequest is one `cmd/admin runs sweep-leases` invocation.
type LeaseSweepRequest struct {
	Grace  time.Duration
	DryRun bool
	Limit  int
}

// LeaseSweepEntry is one candidate in the sweep report.
type LeaseSweepEntry struct {
	RunID           string `json:"run_id"`
	JobID           string `json:"job_id"`
	ExecutionUnitID string `json:"execution_unit_id,omitempty"`
	LeaseDeadline   string `json:"lease_deadline,omitempty"`
	HeartbeatAt     string `json:"heartbeat_at,omitempty"`
	Action          string `json:"action"` // parked|would_park|skipped
	Note            string `json:"note,omitempty"`
}

// LeaseSweepReport is the JSON result of one sweep pass.
type LeaseSweepReport struct {
	Status    string            `json:"status"` // ok|error
	Grace     string            `json:"grace"`
	DryRun    bool              `json:"dry_run"`
	Scanned   int               `json:"scanned"`
	Parked    int               `json:"parked"`
	WouldPark int               `json:"would_park"`
	Skipped   int               `json:"skipped"`
	Runs      []LeaseSweepEntry `json:"runs"`
	Note      string            `json:"note"`
}

// ---------------------------------------------------------------------------
// grace + evidence validation (pure, no database)
// ---------------------------------------------------------------------------

// normalizeRecoveryGrace applies the documented default. A negative or oversized
// value is a usage error the CLI reports before touching the database.
func normalizeRecoveryGrace(value time.Duration) time.Duration {
	if value == 0 {
		return DefaultRecoveryGrace
	}
	return value
}

func validateRecoveryGrace(value time.Duration) error {
	if value < 0 {
		return fmt.Errorf("grace must not be negative")
	}
	if value > MaxRecoveryGrace {
		return fmt.Errorf("grace must be at most %s", MaxRecoveryGrace)
	}
	return nil
}

// recoveryEvidence is the parsed operator document. Every field is required
// except DBDrained and Notes; the writer cannot invent a recovery from free text.
type recoveryEvidence struct {
	EvidenceKind    string
	ExecutionUnitID string
	ObservedAt      time.Time
	Process         recoveryProcessProof
	Sandbox         recoverySandboxProof
	DBDrained       *recoveryDBProof
	Notes           string
}

type recoveryProcessProof struct {
	PID      int64
	Command  string
	Result   string
	Observed string
}

type recoverySandboxProof struct {
	SandboxID string
	Command   string
	Result    string
}

type recoveryDBProof struct {
	Command string
	Result  string
}

// Closed proof vocabularies. A caller can never store "probably dead".
var (
	recoveryProcessResults = map[string]bool{"absent": true, "terminated": true}
	recoverySandboxResults = map[string]bool{"absent": true, "deleted": true, "stopped": true}
	recoveryDBResults      = map[string]bool{"no_active_backends": true}
)

// raw evidence shapes. Pointers make "field missing" distinguishable from an
// empty value, and the decoders use DisallowUnknownFields so a new key can never
// silently become proof the server does not understand.
type rawRecoveryEvidence struct {
	EvidenceKind    *string             `json:"evidence_kind"`
	ExecutionUnitID *string             `json:"execution_unit_id"`
	ObservedAt      *string             `json:"observed_at"`
	Process         *rawRecoveryProcess `json:"process"`
	Sandbox         *rawRecoverySandbox `json:"sandbox"`
	DBDrained       *rawRecoveryDBProof `json:"db_drained"`
	Notes           *string             `json:"notes"`
}

type rawRecoveryProcess struct {
	PID      *int64  `json:"pid"`
	Command  *string `json:"command"`
	Result   *string `json:"result"`
	Observed *string `json:"observed"`
}

type rawRecoverySandbox struct {
	SandboxID *string `json:"sandbox_id"`
	Command   *string `json:"command"`
	Result    *string `json:"result"`
}

type rawRecoveryDBProof struct {
	Command *string `json:"command"`
	Result  *string `json:"result"`
}

func recoveryEvidenceInvalid(format string, args ...any) *RecoveryRefusal {
	return recoveryRefusal(RecoveryCodeEvidenceInvalid, format, args...)
}

// boundedString trims and bounds one evidence string.
func boundedString(field string, value *string, maximum int, required bool) (string, *RecoveryRefusal) {
	if value == nil {
		if required {
			return "", recoveryEvidenceInvalid("%s is required", field)
		}
		return "", nil
	}
	trimmed := strings.TrimSpace(*value)
	if required && trimmed == "" {
		return "", recoveryEvidenceInvalid("%s must not be blank", field)
	}
	if len(trimmed) > maximum {
		return "", recoveryEvidenceInvalid("%s must be at most %d characters", field, maximum)
	}
	return trimmed, nil
}

// parseRecoveryEvidence validates the closed operator document and returns the
// normalised document, its RFC 8785 canonical bytes and the SHA-256 of those
// bytes. Nothing about "the host is probably gone" is accepted: the proof names
// the execution unit, the observed process command/result and the sandbox
// command/result.
func parseRecoveryEvidence(raw []byte) (*recoveryEvidence, []byte, string, error) {
	if len(raw) == 0 {
		return nil, nil, "", recoveryRefusal(
			RecoveryCodeEvidenceRequired,
			"an operator evidence document is required (--evidence)",
		)
	}
	if len(raw) > maxRecoveryEvidenceBytes {
		return nil, nil, "", recoveryEvidenceInvalid(
			"the evidence document must be at most %d bytes", maxRecoveryEvidenceBytes,
		)
	}
	decoder := json.NewDecoder(strings.NewReader(string(raw)))
	decoder.DisallowUnknownFields()
	var source rawRecoveryEvidence
	if err := decoder.Decode(&source); err != nil {
		var refusal *RecoveryRefusal
		if errors.As(err, &refusal) {
			return nil, nil, "", refusal
		}
		return nil, nil, "", recoveryEvidenceInvalid(
			"the evidence document must be one JSON object with the closed operator shape: %v", err,
		)
	}
	var trailing any
	if err := decoder.Decode(&trailing); !errors.Is(err, io.EOF) {
		return nil, nil, "", recoveryEvidenceInvalid("the evidence document must be exactly one JSON object")
	}

	kind, refusal := boundedString("evidence_kind", source.EvidenceKind, 64, true)
	if refusal != nil {
		return nil, nil, "", refusal
	}
	if kind != recoveryEvidenceKindNew {
		return nil, nil, "", recoveryEvidenceInvalid(
			"evidence_kind must be %q", recoveryEvidenceKindNew,
		)
	}
	unitID, refusal := boundedString("execution_unit_id", source.ExecutionUnitID, maxRecoveryUnitID, true)
	if refusal != nil {
		return nil, nil, "", refusal
	}
	observedRaw, refusal := boundedString("observed_at", source.ObservedAt, 64, true)
	if refusal != nil {
		return nil, nil, "", refusal
	}
	observedAt, err := time.Parse(time.RFC3339Nano, observedRaw)
	if err != nil {
		return nil, nil, "", recoveryEvidenceInvalid(
			"observed_at must be an RFC3339 timestamp",
		)
	}
	if source.Process == nil {
		return nil, nil, "", recoveryEvidenceInvalid("process proof is required")
	}
	if source.Process.PID == nil {
		return nil, nil, "", recoveryEvidenceInvalid("process.pid is required")
	}
	if *source.Process.PID < 1 {
		return nil, nil, "", recoveryEvidenceInvalid("process.pid must be a positive integer")
	}
	processCommand, refusal := boundedString("process.command", source.Process.Command, maxRecoveryCommand, true)
	if refusal != nil {
		return nil, nil, "", refusal
	}
	processResult, refusal := boundedString("process.result", source.Process.Result, 32, true)
	if refusal != nil {
		return nil, nil, "", refusal
	}
	if !recoveryProcessResults[processResult] {
		return nil, nil, "", recoveryEvidenceInvalid(
			"process.result must be one of absent|terminated",
		)
	}
	processObserved, refusal := boundedString("process.observed", source.Process.Observed, maxRecoveryObserved, false)
	if refusal != nil {
		return nil, nil, "", refusal
	}
	if source.Sandbox == nil {
		return nil, nil, "", recoveryEvidenceInvalid("sandbox proof is required")
	}
	sandboxID, refusal := boundedString("sandbox.sandbox_id", source.Sandbox.SandboxID, maxRecoverySandboxID, true)
	if refusal != nil {
		return nil, nil, "", refusal
	}
	sandboxCommand, refusal := boundedString("sandbox.command", source.Sandbox.Command, maxRecoveryCommand, true)
	if refusal != nil {
		return nil, nil, "", refusal
	}
	sandboxResult, refusal := boundedString("sandbox.result", source.Sandbox.Result, 32, true)
	if refusal != nil {
		return nil, nil, "", refusal
	}
	if !recoverySandboxResults[sandboxResult] {
		return nil, nil, "", recoveryEvidenceInvalid(
			"sandbox.result must be one of absent|deleted|stopped",
		)
	}
	var dbProof *recoveryDBProof
	if source.DBDrained != nil {
		dbCommand, refusal := boundedString("db_drained.command", source.DBDrained.Command, maxRecoveryCommand, true)
		if refusal != nil {
			return nil, nil, "", refusal
		}
		dbResult, refusal := boundedString("db_drained.result", source.DBDrained.Result, 32, true)
		if refusal != nil {
			return nil, nil, "", refusal
		}
		if !recoveryDBResults[dbResult] {
			return nil, nil, "", recoveryEvidenceInvalid(
				"db_drained.result must be no_active_backends",
			)
		}
		dbProof = &recoveryDBProof{Command: dbCommand, Result: dbResult}
	}
	notes, refusal := boundedString("notes", source.Notes, maxRecoveryNotes, false)
	if refusal != nil {
		return nil, nil, "", refusal
	}

	document := &recoveryEvidence{
		EvidenceKind:    kind,
		ExecutionUnitID: unitID,
		ObservedAt:      observedAt,
		Process: recoveryProcessProof{
			PID: *source.Process.PID, Command: processCommand,
			Result: processResult, Observed: processObserved,
		},
		Sandbox: recoverySandboxProof{
			SandboxID: sandboxID, Command: sandboxCommand, Result: sandboxResult,
		},
		DBDrained: dbProof,
		Notes:     notes,
	}

	// Canonicalise the *normalised* document: unknown/duplicate keys are already
	// refused, so the hash is a stable identity for this exact proof.
	canonicalDocument := map[string]any{
		"evidence_kind":     document.EvidenceKind,
		"execution_unit_id": document.ExecutionUnitID,
		"observed_at":       document.ObservedAt.UTC().Format("2006-01-02T15:04:05.000000Z"),
		"process": map[string]any{
			// jcs.Canonicalize accepts float64 numbers; a PID is far below 2^53,
			// so this is exact and RFC 8785 renders it as an integer.
			"pid":     float64(document.Process.PID),
			"command": document.Process.Command,
			"result":  document.Process.Result,
		},
		"sandbox": map[string]any{
			"sandbox_id": document.Sandbox.SandboxID,
			"command":    document.Sandbox.Command,
			"result":     document.Sandbox.Result,
		},
	}
	if document.Process.Observed != "" {
		canonicalDocument["process"].(map[string]any)["observed"] = document.Process.Observed
	}
	if document.DBDrained != nil {
		canonicalDocument["db_drained"] = map[string]any{
			"command": document.DBDrained.Command,
			"result":  document.DBDrained.Result,
		}
	}
	if document.Notes != "" {
		canonicalDocument["notes"] = document.Notes
	}
	canonical, err := jcs.Canonicalize(canonicalDocument)
	if err != nil {
		return nil, nil, "", recoveryEvidenceInvalid(
			"the evidence document cannot be canonicalised: %v", err,
		)
	}
	return document, []byte(canonical), jcs.SHA256Hex([]byte(canonical)), nil
}

// ---------------------------------------------------------------------------
// database-drained check
// ---------------------------------------------------------------------------

type recoveryCheckpointConnection struct {
	PID             *int64  `json:"pid"`
	BackendStart    *string `json:"backend_start"`
	ApplicationName *string `json:"application_name"`
}

// recoveryConnections parses the recorded checkpoint_connections JSON array.
func recoveryConnections(raw []byte) ([]recoveryCheckpointConnection, error) {
	if len(raw) == 0 {
		return nil, nil
	}
	var entries []json.RawMessage
	if err := json.Unmarshal(raw, &entries); err != nil {
		return nil, err
	}
	if len(entries) > maxRecoveryConnections {
		return nil, fmt.Errorf("more than %d checkpoint connections recorded", maxRecoveryConnections)
	}
	connections := make([]recoveryCheckpointConnection, 0, len(entries))
	for _, entry := range entries {
		decoder := json.NewDecoder(strings.NewReader(string(entry)))
		decoder.DisallowUnknownFields()
		var connection recoveryCheckpointConnection
		if err := decoder.Decode(&connection); err != nil {
			return nil, err
		}
		if connection.PID == nil || *connection.PID < 1 {
			return nil, fmt.Errorf("a recorded checkpoint connection has no positive pid")
		}
		if connection.BackendStart == nil || connection.ApplicationName == nil {
			return nil, fmt.Errorf("a recorded checkpoint connection is incomplete")
		}
		connections = append(connections, connection)
	}
	return connections, nil
}

// checkDatabaseDrained proves every checkpoint connection the attempt recorded is
// gone. The match is pid + backend_start (PID reuse must not be treated as the
// old session) and it fails closed: a connection that cannot be parsed or looked
// up is "not proven drained".
func (s *Service) checkDatabaseDrained(
	ctx context.Context, q recoveryQueryer, attempt *recoveryAttemptFacts,
) *RecoveryRefusal {
	connections, err := recoveryConnections(attempt.CheckpointConnections)
	if err != nil {
		return recoveryRefusal(
			RecoveryCodeDatabaseWritesInFlight,
			"the attempt's recorded checkpoint connections cannot be validated: %v", err,
		)
	}
	for _, connection := range connections {
		backendStart, err := time.Parse(time.RFC3339, *connection.BackendStart)
		if err != nil {
			return recoveryRefusal(
				RecoveryCodeDatabaseWritesInFlight,
				"recorded checkpoint connection pid %d has no valid backend_start",
				*connection.PID,
			)
		}
		var exists bool
		if err := q.QueryRowContext(ctx, `
			SELECT EXISTS (
			    SELECT 1 FROM pg_stat_activity
			     WHERE pid = $1 AND backend_start = $2
			)`,
			*connection.PID, backendStart,
		).Scan(&exists); err != nil {
			return recoveryRefusal(
				RecoveryCodeDatabaseWritesInFlight,
				"cannot inspect pg_stat_activity for checkpoint connection pid %d: %v",
				*connection.PID, err,
			)
		}
		if exists {
			return recoveryRefusal(
				RecoveryCodeDatabaseWritesInFlight,
				"checkpoint connection pid %d (application_name %q) is still present in pg_stat_activity; old writes may be in flight",
				*connection.PID, *connection.ApplicationName,
			)
		}
	}
	return nil
}

// ---------------------------------------------------------------------------
// frozen artifact checks
// ---------------------------------------------------------------------------

// checkManifestArtifact re-verifies the frozen manifest the parked attempt was
// claimed with: the artifact row is ready/not deleted, its hash is the snapshot's
// hash, the sealed bytes are the recorded bytes (size + SHA-256, then the full
// compiler verification), and the manifest's worker image digest is still the
// snapshot's deployment identity (EXECUTION.md §8).
func (s *Service) checkManifestArtifact(
	ctx context.Context, q recoveryQueryer, run *recoveryRunFacts,
) *RecoveryRefusal {
	if run.SnapshotManifestArtifactID == "" || run.SnapshotManifestSHA256 == "" {
		return recoveryRefusal(
			RecoveryCodeArtifactUnavailable,
			"the execution snapshot names no frozen manifest artifact",
		)
	}
	if !strings.EqualFold(run.ManifestSHA256, run.SnapshotManifestSHA256) {
		return recoveryRefusal(
			RecoveryCodeArtifactUnavailable,
			"the run and snapshot manifest hashes disagree",
		)
	}
	facts, found, err := loadRecoveryArtifact(ctx, q, run.SnapshotManifestArtifactID)
	if err != nil {
		return recoveryRefusal(RecoveryCodeArtifactUnavailable, "%v", err)
	}
	if !found {
		return recoveryRefusal(
			RecoveryCodeArtifactUnavailable,
			"the frozen manifest artifact %s no longer exists",
			run.SnapshotManifestArtifactID,
		)
	}
	if facts.State != "ready" || facts.Deleted.Valid {
		return recoveryRefusal(
			RecoveryCodeArtifactUnavailable,
			"the frozen manifest artifact is not ready (state=%s deleted=%t)",
			facts.State, facts.Deleted.Valid,
		)
	}
	if !facts.SHA256.Valid || !strings.EqualFold(facts.SHA256.String, run.SnapshotManifestSHA256) {
		return recoveryRefusal(
			RecoveryCodeArtifactUnavailable,
			"the frozen manifest artifact hash is not the snapshot's hash",
		)
	}
	if !facts.SizeBytes.Valid {
		return recoveryRefusal(
			RecoveryCodeArtifactUnavailable,
			"the frozen manifest artifact has no recorded size",
		)
	}
	if s.Objects == nil {
		return recoveryRefusal(
			RecoveryCodeArtifactUnavailable,
			"the object store is not configured (ORCHARA_OBJECT_DIR); the sealed manifest cannot be verified",
		)
	}
	manifest, _, err := compiler.VerifyManifestBytes(s.Objects, compiler.ArtifactRef{
		ObjectKey: facts.ObjectKey,
		SHA256:    run.SnapshotManifestSHA256,
		SizeBytes: facts.SizeBytes.Int64,
	})
	switch {
	case err == nil:
	case errors.Is(err, compiler.ErrManifestHashMismatch),
		errors.Is(err, compiler.ErrManifestSizeMismatch):
		return recoveryRefusal(
			RecoveryCodeManifestHashMismatch,
			"the sealed manifest bytes do not match the frozen hash/size: %v", err,
		)
	case errors.Is(err, compiler.ErrManifestInvalid):
		return recoveryRefusal(
			RecoveryCodeManifestInvalid,
			"the sealed manifest is not a valid canonical execution manifest: %v", err,
		)
	case errors.Is(err, objectstore.ErrNotExist), errors.Is(err, objectstore.ErrNotConfigured):
		return recoveryRefusal(
			RecoveryCodeArtifactUnavailable,
			"the sealed manifest object is unavailable: %v", err,
		)
	default:
		return recoveryRefusal(
			RecoveryCodeArtifactUnavailable,
			"the sealed manifest cannot be verified: %v", err,
		)
	}
	if !strings.EqualFold(manifest.WorkerImageDigest, run.SnapshotWorkerImageDigest) {
		return recoveryRefusal(
			RecoveryCodeIdentityMismatch,
			"the sealed manifest names worker image %s but the snapshot froze %s",
			manifest.WorkerImageDigest, run.SnapshotWorkerImageDigest,
		)
	}
	return nil
}

// checkInputArtifact proves the run's input artifact is still there, ready, not
// deleted, with the recorded size and SHA-256. A deleted input means the Run may
// not be re-executed (SECURITY.md §5 / STATE-MACHINES.md §6).
func (s *Service) checkInputArtifact(
	ctx context.Context, q recoveryQueryer, run *recoveryRunFacts,
) *RecoveryRefusal {
	if run.InputArtifactID == "" {
		return recoveryRefusal(RecoveryCodeInputDeleted, "the run has no input artifact")
	}
	facts, found, err := loadRecoveryArtifact(ctx, q, run.InputArtifactID)
	if err != nil {
		return recoveryRefusal(RecoveryCodeInputDeleted, "%v", err)
	}
	if !found {
		return recoveryRefusal(
			RecoveryCodeInputDeleted,
			"the run input artifact %s no longer exists", run.InputArtifactID,
		)
	}
	if facts.State != "ready" || facts.Deleted.Valid {
		return recoveryRefusal(
			RecoveryCodeInputDeleted,
			"the run input artifact is not usable (state=%s deleted=%t)",
			facts.State, facts.Deleted.Valid,
		)
	}
	if !facts.SHA256.Valid || !facts.SizeBytes.Valid {
		return recoveryRefusal(
			RecoveryCodeInputDeleted,
			"the run input artifact has no recorded hash/size",
		)
	}
	if s.Objects == nil {
		return recoveryRefusal(
			RecoveryCodeInputDeleted,
			"the object store is not configured (ORCHARA_OBJECT_DIR); the input bytes cannot be verified",
		)
	}
	object, err := s.Objects.Hash(facts.ObjectKey)
	if err != nil {
		if errors.Is(err, objectstore.ErrNotExist) {
			return recoveryRefusal(
				RecoveryCodeInputDeleted,
				"the sealed input object is missing",
			)
		}
		return recoveryRefusal(
			RecoveryCodeInputDeleted,
			"the sealed input object cannot be read: %v", err,
		)
	}
	if !strings.EqualFold(object.SHA256, facts.SHA256.String) || object.Size != facts.SizeBytes.Int64 {
		return recoveryRefusal(
			RecoveryCodeInputHashMismatch,
			"the sealed input bytes (%d bytes, %s) do not match the recorded artifact (%d bytes, %s)",
			object.Size, object.SHA256, facts.SizeBytes.Int64, facts.SHA256.String,
		)
	}
	return nil
}

// ---------------------------------------------------------------------------
// Recover
// ---------------------------------------------------------------------------

// Recover is the only command that leaves recovery_pending for queued. It runs
// the full proof against PostgreSQL and the object store, then one transaction
// that records the audited evidence, finalizes the old attempt, revokes its
// token, re-arms exactly one job, returns the Run to queued and appends the
// run.status event. Everything it refuses is a typed RecoveryRefusal and writes
// nothing.
func (s *Service) Recover(ctx context.Context, request RecoveryRequest) (*RecoveryReport, error) {
	report := &RecoveryReport{CheckOnly: request.CheckOnly, Checks: []RecoveryCheck{}}
	runID := strings.TrimSpace(request.RunID)
	if !uuid.Valid(runID) {
		return report, recoveryRefusal(RecoveryCodeRunNotFound, "not a Run id")
	}
	report.RunID = runID
	if err := validateRecoveryGrace(request.Grace); err != nil {
		return report, err
	}
	grace := normalizeRecoveryGrace(request.Grace)

	evidence, canonical, evidenceSHA, err := parseRecoveryEvidence(request.EvidenceRaw)
	if err != nil {
		var refusal *RecoveryRefusal
		if errors.As(err, &refusal) {
			report.Code = refusal.Code
			report.Message = refusal.Message
		}
		return report, err
	}
	report.EvidenceSHA256 = evidenceSHA

	run, err := loadRecoveryRunFacts(ctx, s.DB, runID, false)
	if err != nil {
		return report, err
	}
	if run == nil {
		return report, recoveryRefusal(RecoveryCodeRunNotFound, "that run does not exist")
	}
	report.State = run.State
	if run.RecoveryReason.Valid {
		report.RecoveryReason = run.RecoveryReason.String
	}
	if run.RecoverySince.Valid {
		report.RecoverySince = run.RecoverySince.Time.UTC().Format(time.RFC3339Nano)
	}
	if run.State != "recovery_pending" {
		report.skip("run_state", "the run is "+run.State)
		if run.HasEvidence {
			return report, recoveryRefusal(
				RecoveryCodeAlreadyRecovered,
				"this run already has audited recovery evidence and is no longer parked; a repeated command never creates a second attempt",
			)
		}
		return report, recoveryRefusal(
			RecoveryCodeNotRecoveryPending,
			"the run is %s, not recovery_pending", run.State,
		)
	}
	if run.CancelRequestedAt.Valid {
		return report, report.refuse(
			"cancel_intent", RecoveryCodeCancelRequested,
			"the run carries a cancel intent; the T036 cancel/exit path owns that transition and recovery never re-queues a cancelled run",
		)
	}
	report.pass("run_state", "recovery_pending reason="+report.RecoveryReason)

	attempt, err := loadRecoveryAttemptFacts(ctx, s.DB, runID, grace, false)
	if err != nil {
		return report, err
	}
	if attempt == nil {
		return report, report.refuse(
			"attempt", RecoveryCodeNeedsReconciliation,
			"the run has no attempt to finalize. This is the documented needs_reconciliation case: an operator must move it through the reconciliation path (a cancelled/needs_reconciliation decision), not invent a requeue here",
		)
	}
	report.ExecutionUnitID = attempt.ExecutionUnitID
	report.AttemptStartedAt = attempt.StartedAt
	report.HeartbeatAt = attempt.HeartbeatAt
	report.LeaseDeadline = attempt.LeaseDeadline
	report.JobID = attempt.JobID
	if run.LiveAttempts > 1 {
		return report, report.refuse(
			"attempt", RecoveryCodeLiveAttemptConflict,
			"%d live attempts exist for this run; recovery needs exactly the one recorded attempt",
			run.LiveAttempts,
		)
	}
	report.pass("attempt", "one attempt, execution unit "+attempt.ExecutionUnitID)

	// The evidence must certify *this* execution unit. The token itself is never
	// compared or printed: the unit id is the operator-facing identity.
	if !strings.EqualFold(strings.TrimSpace(evidence.ExecutionUnitID), strings.TrimSpace(attempt.ExecutionUnitID)) {
		return report, report.refuse(
			"evidence_unit", RecoveryCodeEvidenceUnitMismatch,
			"the evidence names execution unit %q but the attempt ran %q",
			evidence.ExecutionUnitID, attempt.ExecutionUnitID,
		)
	}
	report.pass("evidence_unit", "evidence and attempt name the same execution unit")

	// Liveness: an exited attempt already carries the supervisor's proof; a still
	// live attempt must have a heartbeat older than the grace window, otherwise a
	// live old worker could still be running and must never get a second executor.
	if attempt.ExitedAt.Valid {
		report.skip("old_execution", "the supervisor's exit evidence is already recorded")
	} else if attempt.HeartbeatFresh {
		return report, report.refuse(
			"old_execution", RecoveryCodeOldExecutionStillLive,
			"the attempt heartbeat is inside the %s grace window; a live old worker must never be replaced",
			grace,
		)
	} else {
		report.pass("old_execution", "last heartbeat "+attempt.HeartbeatAt+" is older than the grace window")
	}

	if attempt.JobState != "held" && attempt.JobState != "leased" {
		return report, report.refuse(
			"job", RecoveryCodeJobUnavailable,
			"the attempt's job is %s, not a live held/leased job", attempt.JobState,
		)
	}
	if attempt.LiveJobs != 1 {
		return report, report.refuse(
			"job", RecoveryCodeJobUnavailable,
			"the run has %d live jobs; recovery re-arms exactly one", attempt.LiveJobs,
		)
	}
	report.pass("job", "one live job in state "+attempt.JobState)

	if refusal := s.checkManifestArtifact(ctx, s.DB, run); refusal != nil {
		return report, report.refuse("manifest_artifact", refusal.Code, "%s", refusal.Message)
	}
	report.pass("manifest_artifact", "sealed manifest bytes verify against the frozen hash")
	if refusal := s.checkInputArtifact(ctx, s.DB, run); refusal != nil {
		return report, report.refuse("input_artifact", refusal.Code, "%s", refusal.Message)
	}
	report.pass("input_artifact", "input artifact is ready and its sealed bytes match")
	if refusal := s.checkDatabaseDrained(ctx, s.DB, attempt); refusal != nil {
		return report, report.refuse("database_drained", refusal.Code, "%s", refusal.Message)
	}
	report.pass("database_drained", "no recorded checkpoint connection is present in pg_stat_activity")

	actorID, actorLogin, err := resolveRecoveryActor(ctx, s.DB, request.ActorLogin)
	if err != nil {
		return report, err
	}
	if actorID == "" {
		return report, report.refuse(
			"actor", RecoveryCodeActorNotFound,
			"no enabled human administrator matches --actor-login; an audited recovery needs a real principal",
		)
	}
	report.RecordedBy = actorID
	report.pass("actor", "recorded by "+actorLogin)

	if request.CheckOnly {
		report.Status = "check_only"
		return report, nil
	}

	// One transaction: record evidence, finalize the old attempt, revoke its
	// token, re-arm the one job, return the Run to queued, append the event,
	// audit. Every fact is re-read under the row locks; anything that moved
	// since the inspection refuses and rolls the whole transaction back.
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return report, fmt.Errorf("starting the recovery transaction: %w", err)
	}
	defer func() { _ = tx.Rollback() }()

	lockedRun, err := loadRecoveryRunFacts(ctx, tx, runID, true)
	if err != nil {
		return report, err
	}
	if lockedRun == nil || lockedRun.State != "recovery_pending" {
		return report, report.refuse(
			"run_state", RecoveryCodeNotRecoveryPending,
			"the run changed state while the evidence was being checked",
		)
	}
	if lockedRun.Revision != run.Revision {
		return report, report.refuse(
			"run_state", RecoveryCodeNotRecoveryPending,
			"the run revision moved while the evidence was being checked",
		)
	}
	if lockedRun.CancelRequestedAt.Valid {
		return report, report.refuse(
			"cancel_intent", RecoveryCodeCancelRequested,
			"a cancel intent was recorded while the evidence was being checked; recovery never re-queues a cancelled run",
		)
	}
	lockedAttempt, err := loadRecoveryAttemptFacts(ctx, tx, runID, grace, true)
	if err != nil {
		return report, err
	}
	if lockedAttempt == nil || lockedAttempt.Token != attempt.Token {
		return report, report.refuse(
			"attempt", RecoveryCodeLiveAttemptConflict,
			"the run's attempt changed while the evidence was being checked",
		)
	}
	if !lockedAttempt.ExitedAt.Valid && lockedAttempt.HeartbeatFresh {
		return report, report.refuse(
			"old_execution", RecoveryCodeOldExecutionStillLive,
			"the attempt heartbeat became fresh while the evidence was being checked; a live old worker must never be replaced",
		)
	}
	if lockedAttempt.JobState != "held" && lockedAttempt.JobState != "leased" {
		return report, report.refuse(
			"job", RecoveryCodeJobUnavailable,
			"the job changed state while the evidence was being checked",
		)
	}
	// Row-level re-check of the frozen artifacts (bytes were verified just above;
	// an artifact deleted in between must still refuse).
	if refusal := recheckRecoveryArtifactRows(ctx, tx, lockedRun); refusal != nil {
		return report, report.refuse("artifact_rows", refusal.Code, "%s", refusal.Message)
	}
	if refusal := s.checkDatabaseDrained(ctx, tx, lockedAttempt); refusal != nil {
		return report, report.refuse("database_drained", refusal.Code, "%s", refusal.Message)
	}

	// T034: recovery is a no-op when the attempt was already settled (the lease
	// sweep now settles at the lease deadline, and T031's exit-without-result
	// park settles in its finalizer). Only an inherited, still-unsettled attempt
	// is settled here, with the same one helper as every other transition.
	if !lockedAttempt.ActiveSettledAt.Valid {
		if _, err := settleAttemptActiveTx(ctx, tx, runID); err != nil {
			return report, err
		}
	}
	if err := finalizeRecoveredAttemptTx(
		ctx, tx, lockedAttempt.Token, canonical,
	); err != nil {
		return report, err
	}
	if err := requeueRecoveredJobTx(ctx, tx, lockedAttempt.JobID, runID); err != nil {
		return report, err
	}
	if err := returnRecoveredRunToQueueTx(ctx, tx, runID, lockedRun.Revision); err != nil {
		return report, err
	}
	if err := releaseAllocationOnceTx(ctx, tx, "job:"+lockedAttempt.JobID); err != nil {
		return report, err
	}
	resumedJobID, recordedAt, err := insertRecoveryEvidenceTx(
		ctx, tx, runID, lockedAttempt.Token, canonical, evidenceSHA, actorID, lockedAttempt.JobID,
	)
	if err != nil {
		return report, err
	}
	if _, err := AppendRunStatusTx(ctx, tx, runID); err != nil {
		return report, err
	}
	metadata := map[string]any{
		"reason":            report.RecoveryReason,
		"execution_unit_id": lockedAttempt.ExecutionUnitID,
		"evidence_sha256":   evidenceSHA,
		"job_id":            lockedAttempt.JobID,
		"recorded_by_login": actorLogin,
	}
	if resumedJobID != "" {
		metadata["resumed_job_id"] = resumedJobID
	}
	if err := audit.Record(
		ctx, tx, &actorID, "run.recovery_recovered", "run", &runID, metadata,
	); err != nil {
		return report, err
	}
	if err := tx.Commit(); err != nil {
		return report, fmt.Errorf("committing the recovery transaction: %w", err)
	}

	report.Status = "recovered"
	report.State = "queued"
	report.ResumedJobID = resumedJobID
	report.RecordedAt = recordedAt.UTC().Format(time.RFC3339Nano)
	report.pass("requeue", "one job re-armed and the run returned to queued")
	return report, nil
}

// ---------------------------------------------------------------------------
// Sweep
// ---------------------------------------------------------------------------

// SweepExpiredLeases parks every running Run whose live attempt lease expired
// beyond the grace window. It never requeues, never swaps a token and never
// releases the allocation: a parked Run keeps its held job and its slot until the
// operator proof arrives. `--dry-run` reports the same decisions without writing.
func (s *Service) SweepExpiredLeases(
	ctx context.Context, request LeaseSweepRequest,
) (*LeaseSweepReport, error) {
	grace := request.Grace
	if err := validateRecoveryGrace(grace); err != nil {
		return nil, err
	}
	grace = normalizeRecoveryGrace(grace)
	limit := request.Limit
	if limit <= 0 || limit > maxRecoverySweep {
		limit = maxRecoverySweep
	}
	report := &LeaseSweepReport{
		Status: "ok",
		Grace:  grace.String(),
		DryRun: request.DryRun,
		Runs:   []LeaseSweepEntry{},
		Note: "a parked run keeps its held job and its execution slot; no requeue, token swap or second executor happens here. " +
			"Re-queueing requires cmd/admin runs recover with audited operator evidence.",
	}

	candidates, err := loadLeaseSweepCandidates(ctx, s.DB, grace, limit)
	if err != nil {
		return report, err
	}
	for _, candidate := range candidates {
		report.Scanned++
		entry := LeaseSweepEntry{
			RunID:           candidate.RunID,
			JobID:           candidate.JobID,
			ExecutionUnitID: candidate.ExecutionUnitID,
			LeaseDeadline:   candidate.LeaseDeadline,
			HeartbeatAt:     candidate.HeartbeatAt,
		}
		if request.DryRun {
			entry.Action = "would_park"
			report.WouldPark++
			report.Runs = append(report.Runs, entry)
			continue
		}
		parked, note, err := s.parkExpiredLease(ctx, candidate, grace)
		if err != nil {
			return report, err
		}
		if !parked {
			entry.Action = "skipped"
			entry.Note = note
			report.Skipped++
			report.Runs = append(report.Runs, entry)
			continue
		}
		entry.Action = "parked"
		report.Parked++
		report.Runs = append(report.Runs, entry)
	}
	return report, nil
}

// parkExpiredLease parks one candidate in its own transaction and reports
// whether the re-check under the locks still allowed it.
func (s *Service) parkExpiredLease(
	ctx context.Context, candidate leaseSweepCandidate, grace time.Duration,
) (bool, string, error) {
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return false, "", fmt.Errorf("starting the lease-sweep transaction: %w", err)
	}
	defer func() { _ = tx.Rollback() }()

	locked, err := lockLeaseSweepCandidateTx(ctx, tx, candidate.Token, grace)
	if err != nil {
		return false, "", err
	}
	if locked == nil {
		return false, "the lease or run changed since the scan", nil
	}
	// T034: parking leaves executing, so the attempt settles exactly once here.
	// The provable activity end is the lease deadline, not park time: charging
	// the parked window would corrupt the Run's accumulated active time, jump
	// the UI clock and could fail the next claim with deadline_exceeded.
	if _, err := settleAttemptActiveUntilTx(
		ctx, tx, locked.RunID, locked.LeaseDeadlineTime,
	); err != nil {
		return false, "", err
	}
	if err := parkRunForRecoveryTx(ctx, tx, locked.RunID, RecoveryReasonLeaseExpired); err != nil {
		return false, "", err
	}
	if err := holdJobTx(ctx, tx, locked.JobID); err != nil {
		return false, "", err
	}
	if _, err := AppendRunStatusTx(ctx, tx, locked.RunID); err != nil {
		return false, "", err
	}
	// A sweep is a system action (the run detail records no human decision), so
	// the audit row keeps actor_id NULL and names only non-secret facts.
	metadata := map[string]any{
		"reason":            RecoveryReasonLeaseExpired,
		"execution_unit_id": locked.ExecutionUnitID,
		"job_id":            locked.JobID,
		"lease_deadline":    locked.LeaseDeadline,
		"grace":             grace.String(),
	}
	if err := audit.Record(
		ctx, tx, nil, "run.recovery_parked", "run", &locked.RunID, metadata,
	); err != nil {
		return false, "", err
	}
	if err := tx.Commit(); err != nil {
		return false, "", fmt.Errorf("committing the lease-sweep transaction: %w", err)
	}
	return true, "", nil
}

// RecoveryFacts reads the parked/evidenced state of one Run for the owner-facing
// Run detail. A missing/deleted run returns nil so the caller keeps its 404 rule.
func (s *Service) RecoveryFacts(ctx context.Context, runID string) (*RecoveryFacts, error) {
	if !uuid.Valid(strings.TrimSpace(runID)) {
		return nil, nil
	}
	return loadRecoveryFacts(ctx, s.DB, strings.TrimSpace(runID))
}
