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

	"orchara/platform/internal/providercodes"
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
	// KindToolRun is the T030 non-contract bridge for the trusted preset tool:
	// one caller-owned ready CSV/XLSX artifact in, summary/log/diagnostics
	// artifacts out. T033 binds preset tools to the sandboxed run protocol.
	KindToolRun Kind = "tool_run"
)

// allKinds is the closed whitelist from BACKGROUND-JOBS.md §2, extended by T030
// with the documented non-contract kind tool_run.
var allKinds = []Kind{
	KindArtifactFinalize,
	KindRouteRequest,
	KindToolBuild,
	KindDocumentIngest,
	KindApplicationEvaluate,
	KindModelValidate,
	KindDeleteContent,
	KindCatalogIndex,
	KindToolRun,
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

	// delete_content failure codes (F11/T071). A still-present named object is a
	// retryable storage fact; a shared object (a live document/memory still reads
	// it) is a deliberate, non-retryable over-deletion refusal. The handler's
	// bounded wait reports delete_timeout, which is retryable so a wedged object
	// store cannot burn the job in one attempt.
	CodeDeleteTimeout          = "delete_timeout"
	CodeObjectSharedReference  = "object_shared_reference"
	CodePurgeUnsupportedTarget = "purge_unsupported_target"

	// T028 format admission codes. Both are content refusals: the artifact can
	// never become ready, so the completion guard quarantines it. They are
	// deliberately not retryable — the same bytes cannot pass a later attempt.
	CodeArtifactFormatRejected   = "artifact_format_rejected"
	CodeArtifactUploadIncomplete = "artifact_upload_incomplete"

	// model_validate failure codes (T024). A model provider that is not
	// configured is a deployment fact, not a transient outage: retrying cannot
	// fix it, so it is a business failure. The compatibility report itself is a
	// successful job with a failed report, never a job failure.
	CodeProviderNotConfigured      = providercodes.NotConfigured
	CodeProviderAdapterUnavailable = providercodes.DispatchAdapterUnavailable
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

	// tool_run failure codes (T030). A tool rejection (missing_column,
	// duplicate_order_id, ...) is submitted verbatim: it is a business failure
	// and the unknown-code rule keeps it non-retryable. The codes below are the
	// platform-side edges: invalid/oversized/escaping output is a tool defect
	// that cannot improve on retry, while timeout/crash/storage IO may.
	CodeToolTimeout          = "tool_timeout"
	CodeToolCrash            = "tool_crash"
	CodeToolOutputInvalid    = "tool_output_invalid"
	CodeToolArtifactInvalid  = "tool_artifact_invalid"
	CodeToolArtifactEscape   = "tool_artifact_path_escape"
	CodeToolArtifactSymlink  = "tool_artifact_symlink"
	CodeToolArtifactTooLarge = "tool_artifact_too_large"
	CodeToolArtifactCount    = "tool_artifact_count_exceeded"
	CodeToolInputInvalid     = "tool_input_invalid"

	// document_ingest failure codes (T055). Empty parse, unsupported/corrupt
	// input and a handler failure must end in document failed with one of these
	// specific codes; none of them can ever produce ready. The chunk-artifact
	// wait and transient storage/internal failures are the only retryable ones.
	CodeIngestEmptyContent             = "ingest_empty_content"
	CodeIngestUnsupportedMediaType     = "ingest_unsupported_media_type"
	CodeIngestInvalidEncoding          = "ingest_invalid_encoding"
	CodeIngestSourceUnavailable        = "ingest_source_unavailable"
	CodeIngestSourceChanged            = "ingest_source_changed"
	CodeIngestTooManyChunks            = "ingest_too_many_chunks"
	CodeIngestChunkArtifactTimeout     = "ingest_chunk_artifact_timeout"
	CodeIngestIncomplete               = "ingest_incomplete"
	CodeIngestCitationInvalid          = "ingest_citation_invalid"
	CodeIngestCancelled                = "ingest_cancelled"
	CodeEmbeddingProviderNotConfigured = "embedding_provider_not_configured"
	// T061: activation refusals. A job whose source revision moved, whose index
	// version is no longer the building one, or whose deployment embedding model
	// changed between enqueue and execution must never activate anything. These
	// are business failures: the newer revision owns the document.
	CodeStaleRevision         = "stale_revision"
	CodeEmbeddingModelChanged = "embedding_model_changed"
	// T061 upgrade recovery. `ingest_index_unbound` is a pre-0039 document_ingest
	// job: it was created before `expected_index_version_id` existed, so it has no
	// build to stage into and can never activate. The migration 0041 repair and
	// the completion guard both end such an attempt with this code and leave the
	// document failed-and-re-ingestable instead of stuck in scanning/indexing.
	CodeIngestIndexUnbound = "ingest_index_unbound"
	// `ingest_build_abandoned` marks a document whose index build can no longer be
	// finished (no non-terminal job is bound to a still-building index version).
	// The recovery path writes it on the orphaned index version and on the
	// document before a new build starts, so the abandoned attempt is a named
	// fact rather than a silent scanning state.
	CodeIngestBuildAbandoned = "ingest_build_abandoned"

	// tool_build failure codes (T037). The handler reports the safe code; the
	// completion guard stores the same closed vocabulary on tool_versions.
	// Only the three transient codes are retryable; every build rejection is
	// deterministic (the same source/lock produces the same refusal), so it
	// fails immediately.
	CodeBuildFailed              = "build_failed"
	CodeBuildSourceInvalid       = "build_source_invalid"
	CodeBuildSourceChanged       = "build_source_changed"
	CodeBuildSandboxUnavailable  = "build_sandbox_unavailable"
	CodeBuildTimeout             = "build_timeout"
	CodeBuildCrashed             = "build_crashed"
	CodeBuildOutputInvalid       = "build_output_invalid"
	CodeBuildArtifactsInvalid    = "build_artifacts_invalid"
	CodeBuildArtifactInvalid     = "build_artifact_invalid"
	CodeBuildDependenciesMissing = "dependencies_unavailable"
)

// Failure codes that quarantine the artifact rather than leave it unavailable.
var quarantineCodes = map[string]bool{
	CodeArtifactHashMismatch:     true,
	CodeArtifactSizeMismatch:     true,
	CodeArtifactTooLarge:         true,
	CodeArtifactObjectMissing:    true,
	CodeArtifactExpectedHashMiss: true,
	// T028: user-upload content that the platform refuses to admit, and an
	// upload whose staging bytes never arrived. Both can never become ready, so
	// the owner must see quarantined rather than a permanent scanning state.
	CodeArtifactFormatRejected:   true,
	CodeArtifactUploadIncomplete: true,
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
	// tool_run (T030): the trusted tool runs in a bounded subprocess. A timeout
	// or crash can be transient (a loaded host, a transient Python failure) and
	// is retried at most MaxAttempts; a user-input rejection or an invalid tool
	// output is deterministic and fails immediately. The tool's rejection codes
	// are deliberately absent here: an unknown code classifies as business, so a
	// later tool cannot accidentally introduce an infinite retry loop.
	KindToolRun: {
		CodeTransientStorageIO: true,
		CodeTransientInternal:  true,
		CodeLeaseExpired:       true,
		CodeToolTimeout:        true,
		CodeToolCrash:          true,
	},
	// document_ingest (T055): waiting for the chunk-body artifacts to be
	// finalized can recover, as can transient storage/internal/lease failures. A
	// parse rejection, an unbound/deleted source or an incomplete staged set is
	// deterministic and is never retried; an unknown code stays business
	// (fail safe), so a document can never be retried into ready by accident.
	KindDocumentIngest: {
		CodeTransientStorageIO:         true,
		CodeTransientInternal:          true,
		CodeLeaseExpired:               true,
		CodeIngestChunkArtifactTimeout: true,
	},
	// tool_build (T037): transient storage/internal/lease failures are retried;
	// a dependency-unavailable, unsafe-source or sandbox refusal is deterministic
	// and business. An unknown code classifies as business (fail safe).
	KindToolBuild: {
		CodeTransientStorageIO: true,
		CodeTransientInternal:  true,
		CodeLeaseExpired:       true,
	},
	// delete_content (F11/T071): a still-present object or a storage read error
	// can recover (the object store may be temporarily unavailable, the object may
	// be released); a timeout can too. A shared-reference refusal and a target
	// this build cannot purge are deliberately absent: retrying the same facts
	// cannot help, so they fail the job and leave the deletion visibly incomplete.
	KindDeleteContent: {
		CodeTransientStorageIO: true,
		CodeTransientInternal:  true,
		CodeLeaseExpired:       true,
		CodeDeleteTimeout:      true,
	},
	// catalog_index (T077): the projection write can be retried after transient
	// storage/internal/lease failures. A card/source refusal is deterministic (the
	// same authority row produces the same refusal), so it is a business failure.
	KindCatalogIndex: {
		CodeTransientStorageIO: true,
		CodeTransientInternal:  true,
		CodeLeaseExpired:       true,
	},
	// route_request (T079): a temporarily unavailable control model or a transient
	// internal failure can recover; the request stays in `routing` while the job is
	// retried, so the conversation slot is not stranded. A candidate/authorization
	// refusal is committed on the Request by the server, so it never reaches here.
	KindRouteRequest: {
		CodeTransientStorageIO: true,
		CodeTransientInternal:  true,
		CodeLeaseExpired:       true,
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
	ID               string
	Kind             Kind
	TargetID         string
	OwnerID          string
	ExpectedRevision *int64
	// ExpectedIndexVersionID binds a document_ingest job to one
	// platform.knowledge_index_versions row (T061). The completion guard uses it
	// to verify the staged chunk set and the activation decision for the same
	// build; a job without it never activates an index.
	ExpectedIndexVersionID *string
	ExpectedSHA256         *string
	DedupKey               string
	InputArtifactID        *string
	ResultArtifactID       *string
	State                  State
	AvailableAt            time.Time
	LeaseToken             *string
	LeaseDeadline          *time.Time
	WorkerID               *string
	AttemptCount           int
	ResultEventID          *string
	ResultEventDigest      *string
	ErrorCode              *string
	CreatedAt              time.Time
	FinishedAt             *time.Time
	FailureClass           *string
	LastErrorAt            *time.Time
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
		expectedIdx sql.NullString
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
		&job.ID, &kind, &job.TargetID, &job.OwnerID, &expected, &expectedIdx, &expSHA, &job.DedupKey,
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
	job.ExpectedIndexVersionID = nullString(expectedIdx)
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
	id::text, kind, target_id::text, owner_id::text, expected_revision,
	expected_index_version_id::text, expected_sha256,
	dedup_key, input_artifact_id::text, result_artifact_id::text, state, available_at,
	lease_token::text, lease_deadline, worker_id, attempt_count, result_event_id::text,
	result_event_digest, error_code, created_at, finished_at, failure_class, last_error_at`
