// Package background implements the durable background-job controller.
//
// The model is fixed by docs/spec/BACKGROUND-JOBS.md, DATA-MODEL.md and the
// F00B plan (D1/D4/D5):
//
//   - **A resource transaction writes the job.** EnqueueTx is called with the
//     caller's transaction, so a job cannot exist for a resource row that rolled
//     back, and a dedup key cannot be reused by a second intention.
//   - **Kinds are a compile-time whitelist.** Kind values and the retry tables
//     below are the only vocabulary; no request string ever names a handler, and
//     this package has no shell/exec import.
//   - **Claims are pessimistic and expiring.** PostgreSQL row locks (SKIP
//     LOCKED) hand a ready job to exactly one maintenance process and mint a
//     fresh attempt token with a 30s lease. An expired lease is infrastructure
//     failure, recovered by the next claim or sweep with the attempt already
//     counted.
//   - **Completion is guarded.** A result is accepted only for
//     state=leased + the current token + an unexpired lease. A terminal result
//     event id is idempotent; a different event on a terminal job conflicts.
//     Errors in a fixed per-kind table are retried at most three times with
//     exponential backoff; everything else fails immediately (fail safe).
//   - **Activation is server-side.** For artifact_finalize the controller
//     re-reads the sealed object, hashes it in the same transaction that would
//     activate the artifact, checks tombstone and source revision, and only then
//     sets state=ready. No handler result can skip that guard.
package background

import (
	"database/sql"
	"errors"
	"fmt"
	"strings"
	"time"
)

// Kind is one fixed handler kind. The database CHECK constraint and this
// whitelist must stay in sync; a value outside it is refused on enqueue and on
// claim.
type Kind string

const (
	KindArtifactFinalize    Kind = "artifact_finalize"
	KindRouteRequest        Kind = "route_request"
	KindToolBuild           Kind = "tool_build"
	KindDocumentIngest      Kind = "document_ingest"
	KindApplicationEvaluate Kind = "application_evaluate"
	KindModelValidate       Kind = "model_validate"
	KindDeleteContent       Kind = "delete_content"
	KindCatalogIndex        Kind = "catalog_index"
)

// allKinds is the closed whitelist from BACKGROUND-JOBS.md §2.
var allKinds = []Kind{
	KindArtifactFinalize,
	KindRouteRequest,
	KindToolBuild,
	KindDocumentIngest,
	KindApplicationEvaluate,
	KindModelValidate,
	KindDeleteContent,
	KindCatalogIndex,
}

var knownKinds = func() map[Kind]bool {
	known := make(map[Kind]bool, len(allKinds))
	for _, kind := range allKinds {
		known[kind] = true
	}
	return known
}()

// AllKinds returns the whitelist in a stable order. The slice is a copy: a
// caller cannot extend the whitelist by mutating it.
func AllKinds() []Kind {
	return append([]Kind(nil), allKinds...)
}

// KnownKind reports whether raw is one of the fixed kinds.
func KnownKind(raw string) bool { return knownKinds[Kind(raw)] }

// parseKinds validates and deduplicates a list of kind strings, preserving the
// caller's order. An empty list, an unknown kind or a duplicate is a
// ValidationError naming the field.
func parseKinds(field string, raw []string) ([]Kind, error) {
	if len(raw) == 0 {
		return nil, &ValidationError{Field: field, Message: "at least one kind is required"}
	}
	seen := make(map[Kind]bool, len(raw))
	kinds := make([]Kind, 0, len(raw))
	for _, candidate := range raw {
		kind := Kind(strings.TrimSpace(candidate))
		if !knownKinds[kind] {
			return nil, &ValidationError{Field: field, Message: fmt.Sprintf("unknown kind %q", candidate)}
		}
		if seen[kind] {
			return nil, &ValidationError{Field: field, Message: fmt.Sprintf("duplicate kind %q", candidate)}
		}
		seen[kind] = true
		kinds = append(kinds, kind)
	}
	return kinds, nil
}

// State is one background-job state from the contract.
type State string

const (
	StateReady     State = "ready"
	StateLeased    State = "leased"
	StateSucceeded State = "succeeded"
	StateFailed    State = "failed"
	StateCancelled State = "cancelled"
)

// Terminal reports whether the state ends the job's life.
func (s State) Terminal() bool {
	return s == StateSucceeded || s == StateFailed || s == StateCancelled
}

// ResultState reports whether raw is a valid state a worker may submit. A worker
// can never submit ready or leased.
func ResultState(raw string) (State, bool) {
	switch state := State(raw); state {
	case StateSucceeded, StateFailed, StateCancelled:
		return state, true
	default:
		return "", false
	}
}

// FailureClass separates failures that must never be retried from failures that
// may be.
type FailureClass string

const (
	// FailureBusiness is a validation, tombstone or content problem: retrying
	// cannot succeed, so the job fails immediately.
	FailureBusiness FailureClass = "business"
	// FailureInfrastructure is a transient/lease problem: the job may return to
	// ready with backoff until MaxAttempts is reached.
	FailureInfrastructure FailureClass = "infrastructure"
)

// Error codes that cross the internal API or the database. They are stable:
// tests, report tables and later tickets match on these strings.
const (
	// Control-plane refusals.
	CodeStaleAttempt           = "stale_attempt"
	CodeEventConflict          = "event_conflict"
	CodeJobNotFound            = "job_not_found"
	CodeUnsupportedKind        = "unsupported_kind"
	CodeDedupConflict          = "dedup_conflict"
	CodeValidationFailed       = "validation_failed"
	CodeObjectStoreUnavailable = "object_store_unavailable"

	// artifact_finalize failure codes. Business codes imply quarantine (the
	// artifact can never become ready); transient codes are retried.
	CodeArtifactHashMismatch     = "artifact_hash_mismatch"
	CodeArtifactSizeMismatch     = "artifact_size_mismatch"
	CodeArtifactTooLarge         = "artifact_too_large"
	CodeArtifactObjectMissing    = "artifact_object_missing"
	CodeArtifactStagingMissing   = "artifact_staging_missing"
	CodeArtifactExpectedHashMiss = "artifact_expected_hash_missing"
	CodeSourceDeleted            = "source_deleted"
	CodeSourceChanged            = "source_changed"
	CodeTransientStorageIO       = "transient_storage_io"
	CodeTransientInternal        = "transient_internal"
	CodeLeaseExpired             = "lease_expired"
	CodeAttemptsExhausted        = "attempts_exhausted"
	CodeArtifactStateNotScanning = "artifact_state_not_scanning"

	// model_validate failure codes (T024). A model provider that is not
	// configured is a deployment fact, not a transient outage: retrying cannot
	// fix it, so it is a business failure. The compatibility report itself is a
	// successful job with a failed report, never a job failure.
	CodeProviderNotConfigured      = "provider_not_configured"
	CodeProviderAdapterUnavailable = "provider_adapter_unavailable"
	CodeModelCallRefused           = "model_call_refused"
	CodeFixtureMissing             = "fixture_missing"
	CodeReportNotReady             = "report_not_ready"

	// application_evaluate failure codes (T026). The first group is
	// infrastructure: the dataset may still become usable, so a bounded retry is
	// honest. The second group is business: the evaluation cannot be bound,
	// read or trusted, so retrying the same facts cannot help and no report row
	// may be created. A business *verdict* is not in this list at all: a failed
	// report is a succeeded job.
	CodeDatasetWaitTimeout        = "dataset_wait_timeout"
	CodeDatasetNotReady           = "dataset_not_ready"
	CodeEvaluationMissing         = "evaluation_missing"
	CodeEvaluationStateInvalid    = "evaluation_state_invalid"
	CodeEvaluationBindingMissing  = "evaluation_binding_missing"
	CodeEvaluationBindingMismatch = "evaluation_binding_mismatch"
	CodeEvaluationUnsupported     = "evaluation_unsupported_kind"
	CodeReportBindingMismatch     = "report_binding_mismatch"
	CodeReportInvalid             = "report_invalid"
)

// Failure codes that quarantine the artifact rather than leave it unavailable.
var quarantineCodes = map[string]bool{
	CodeArtifactHashMismatch:     true,
	CodeArtifactSizeMismatch:     true,
	CodeArtifactTooLarge:         true,
	CodeArtifactObjectMissing:    true,
	CodeArtifactExpectedHashMiss: true,
}

// ImpliesQuarantine reports whether a reported artifact_finalize failure must
// move the artifact to quarantined.
func ImpliesQuarantine(code string) bool { return quarantineCodes[code] }

// retryableByKind is the fixed per-kind retry table. A code that is not listed
// is not retried: an unknown error must not create an infinite retry loop.
// Later tickets that register a new handler extend this table in the same
// change; nothing is inferred from the code string.
var retryableByKind = map[Kind]map[string]bool{
	KindArtifactFinalize: {
		CodeTransientStorageIO: true,
		CodeTransientInternal:  true,
		CodeLeaseExpired:       true,
	},
	// model_validate (T024): only infrastructure failures are retried. A missing
	// provider adapter or an unconfigured endpoint is a business failure, and a
	// compatibility verdict is a report outcome, not a job failure.
	KindModelValidate: {
		CodeTransientStorageIO: true,
		CodeTransientInternal:  true,
		CodeLeaseExpired:       true,
	},
	// application_evaluate (T026): waiting for a dataset or reading bytes can
	// recover; a binding/report rejection cannot. A failed evaluation verdict is
	// a report outcome (job succeeded), never a retry.
	KindApplicationEvaluate: {
		CodeTransientStorageIO: true,
		CodeTransientInternal:  true,
		CodeLeaseExpired:       true,
		CodeDatasetWaitTimeout: true,
	},
}

// Retryable reports whether code is retryable for kind.
func Retryable(kind Kind, code string) bool {
	codes, ok := retryableByKind[kind]
	if !ok {
		return false
	}
	return codes[code]
}

// ClassifyFailure maps a submitted failure code to its class. Only a code that
// appears in Retryable is infrastructure; everything else (including an unknown
// code) is business, so it fails immediately.
func ClassifyFailure(kind Kind, code string) FailureClass {
	if Retryable(kind, code) {
		return FailureInfrastructure
	}
	return FailureBusiness
}

// Job is one row of platform.background_jobs. It is the controller's internal
// model; the HTTP layer projects the contract's smaller BackgroundJob shape.
type Job struct {
	ID                string
	Kind              Kind
	TargetID          string
	OwnerID           string
	ExpectedRevision  *int64
	ExpectedSHA256    *string
	DedupKey          string
	InputArtifactID   *string
	ResultArtifactID  *string
	State             State
	AvailableAt       time.Time
	LeaseToken        *string
	LeaseDeadline     *time.Time
	WorkerID          *string
	AttemptCount      int
	ResultEventID     *string
	ResultEventDigest *string
	ErrorCode         *string
	CreatedAt         time.Time
	FinishedAt        *time.Time
	FailureClass      *string
	LastErrorAt       *time.Time
}

// ErrNotFound is "the row does not exist" and is deliberately the same error
// for every caller that may not see it.
var ErrNotFound = errors.New("background job not found")

// ErrStaleAttempt is a result/heartbeat presented with a token that no longer
// owns an unexpired lease. It never changes a business fact.
var ErrStaleAttempt = errors.New("the attempt no longer owns the job lease")

// ErrEventConflict is a second, different terminal result for the same job.
var ErrEventConflict = errors.New("a different result event already completed this job")

// ErrObjectStoreUnavailable means the server cannot verify a sealed object
// because ORCHARA_OBJECT_DIR is missing. The completion is refused (fail
// closed) and the job is not changed.
var ErrObjectStoreUnavailable = errors.New("the object store is not configured")

// ErrDuplicateEvent is the UNIQUE(result_event_id) violation translated to the
// same contract answer as ErrEventConflict.
var ErrDuplicateEvent = ErrEventConflict

// ErrDedupConflict is the sentinel behind DedupConflictError: (kind, dedup_key)
// already names a different intention.
var ErrDedupConflict = errors.New("dedup key is already used by a different job")

// ValidationError names the request field that failed semantic validation.
type ValidationError struct {
	Field   string
	Message string
}

func (e *ValidationError) Error() string {
	return fmt.Sprintf("%s: %s", e.Field, e.Message)
}

// scanJob reads the full job row in column order used by every query in this
// package. It keeps SELECT and scan order from drifting silently.
func scanJob(row interface{ Scan(dest ...any) error }) (*Job, error) {
	var (
		job         Job
		kind        string
		state       string
		expected    sql.NullInt64
		expSHA      sql.NullString
		input       sql.NullString
		result      sql.NullString
		token       sql.NullString
		deadline    sql.NullTime
		worker      sql.NullString
		event       sql.NullString
		eventDigest sql.NullString
		errorCode   sql.NullString
		finished    sql.NullTime
		failure     sql.NullString
		lastError   sql.NullTime
	)
	if err := row.Scan(
		&job.ID, &kind, &job.TargetID, &job.OwnerID, &expected, &expSHA, &job.DedupKey,
		&input, &result, &state, &job.AvailableAt, &token, &deadline, &worker,
		&job.AttemptCount, &event, &eventDigest, &errorCode, &job.CreatedAt, &finished, &failure, &lastError,
	); err != nil {
		return nil, err
	}
	job.Kind = Kind(kind)
	job.State = State(state)
	if expected.Valid {
		value := expected.Int64
		job.ExpectedRevision = &value
	}
	job.ExpectedSHA256 = nullString(expSHA)
	job.InputArtifactID = nullString(input)
	job.ResultArtifactID = nullString(result)
	job.LeaseToken = nullString(token)
	if deadline.Valid {
		value := deadline.Time
		job.LeaseDeadline = &value
	}
	job.WorkerID = nullString(worker)
	job.ResultEventID = nullString(event)
	job.ResultEventDigest = nullString(eventDigest)
	job.ErrorCode = nullString(errorCode)
	if finished.Valid {
		value := finished.Time
		job.FinishedAt = &value
	}
	job.FailureClass = nullString(failure)
	if lastError.Valid {
		value := lastError.Time
		job.LastErrorAt = &value
	}
	return &job, nil
}

func nullString(value sql.NullString) *string {
	if !value.Valid {
		return nil
	}
	copied := value.String
	return &copied
}

// selectJobColumns is the single projection scanJob expects.
const selectJobColumns = `
	id::text, kind, target_id::text, owner_id::text, expected_revision, expected_sha256,
	dedup_key, input_artifact_id::text, result_artifact_id::text, state, available_at,
	lease_token::text, lease_deadline, worker_id, attempt_count, result_event_id::text,
	result_event_digest, error_code, created_at, finished_at, failure_class, last_error_at`
