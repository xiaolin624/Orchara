// Package deletion is the F11/T069 shared deletion foundation.
//
// One durable job row (`platform.deletion_jobs`) is the authority for a logical
// delete. The delete request must, in one transaction:
//
//  1. tombstone the resource (the resource package's Target owns that SQL, so the
//     tombstone is the same column every read predicate already filters);
//  2. record the closed set of physical scopes that still have to be cleared;
//  3. append the minimal audit event (ids and safe codes only); and
//  4. commit before the API answers 202.
//
// After the tombstone commits the affected non-terminal runs are cancelled
// through the existing F03/T036 cancel path (`CancelRun`). Cancellation is not a
// precondition for unreadability: the tombstone already made every read refuse.
//
// The ticket deliberately does not purge anything. T071 consumes/reduces the
// first three scopes (object store, search index, retrieval cache); T072 the last
// four (checkpoints, sandbox temp, third-party history, backup catalog). They
// update `remaining_scopes`/`completed_scopes`/`attempt_count`/`error_code` on
// the same row; no parallel job table exists.
package deletion

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"strings"

	"orchara/platform/internal/audit"
	"orchara/platform/internal/uuid"
)

// Scope is one member of the closed physical-copy vocabulary. The values are
// the contract's `DeletionJob.remaining_scopes` strings and are identical in the
// database, the API and the later cleanup tickets. Do not add a value without a
// migration and a report/ADR update.
type Scope string

const (
	ScopeObjectStore       Scope = "object_store"
	ScopeSearchIndex       Scope = "search_index"
	ScopeRetrievalCache    Scope = "retrieval_cache"
	ScopeCheckpoints       Scope = "checkpoints"
	ScopeSandboxTemp       Scope = "sandbox_temp"
	ScopeThirdPartyHistory Scope = "third_party_history"
	ScopeBackupCatalog     Scope = "backup_catalog"
)

// AllScopes is the fixed vocabulary in a stable (alphabetical) order. A fresh
// T069 job always starts with every scope outstanding: this ticket cannot prove
// any class of derived copy gone, so claiming a scope complete would be a lie.
var AllScopes = []Scope{
	ScopeBackupCatalog,
	ScopeCheckpoints,
	ScopeObjectStore,
	ScopeRetrievalCache,
	ScopeSandboxTemp,
	ScopeSearchIndex,
	ScopeThirdPartyHistory,
}

// ResourceType is the durable `resource_type` of a deletion job.
type ResourceType string

const (
	ResourceArtifact     ResourceType = "artifact"
	ResourceMemory       ResourceType = "memory"
	ResourceDocument     ResourceType = "knowledge_document"
	ResourceConversation ResourceType = "conversation"
)

// Job states, the contract's closed enum.
const (
	StatePending   = "pending"
	StateRunning   = "running"
	StateCompleted = "completed"
	StateFailed    = "failed"
)

// ErrNotFound is the located 404: unknown, malformed, foreign, or (for a
// resource type with no authority yet, such as conversations) missing.
var ErrNotFound = errors.New("deletion target does not exist")

// InUseError is the 409 resource_in_use refusal: the artifact is a configuration
// artifact referenced by a published application version, a recoverable run, a
// model profile, a tool version, an evaluation report or a live background job.
// References names the reference classes only, never another user's body.
type InUseError struct {
	References []string
}

func (e *InUseError) Error() string {
	return "the artifact is still referenced by " + strings.Join(e.References, ", ")
}

// AffectedRun is one non-terminal run selected for cancellation by a Target.
type AffectedRun struct {
	RunID string
	State string
}

// Plan is what a Target returns after locking and tombstoning a resource.
type Plan struct {
	// Revision is the deletion revision recorded on the job: the tombstoned
	// revision (previous + 1) for a new delete, the current one on a repeat.
	Revision int64
	// AlreadyDeleted means the tombstone already existed; the foundation then
	// reuses the existing job instead of creating a second one.
	AlreadyDeleted bool
	// Details maps each vocabulary scope to its copy descriptors. Missing keys
	// are written as empty objects.
	Details map[Scope]any
	// AffectedRuns are the caller-owned non-terminal runs that must enter the
	// existing cancel path. Empty is a valid plan.
	AffectedRuns []AffectedRun
}

// Target is one resource package's deletion adapter. It executes the tombstone
// inside the caller's transaction and returns the copy plan; it never inserts a
// job and never writes audit rows (the foundation owns both).
type Target interface {
	ResourceType() ResourceType
	// Delete locks the owner's resource. Missing/foreign/malformed is ErrNotFound.
	Delete(ctx context.Context, tx *sql.Tx, ownerID, resourceID string) (Plan, error)
}

// CancelRun is the existing F03/T036 cancel path, injected as a function so this
// package does not depend on internal/execution.
type CancelRun func(ctx context.Context, callerID, runID string) error

// Service owns the durable job table and the cancellation/audit fan-out.
type Service struct {
	DB *sql.DB
	// CancelRun is required to honour "affected runs enter cancellation". A nil
	// value is a configuration error: the job is still written (the tombstone is
	// the security fact), but it records run_cancel_unavailable.
	CancelRun CancelRun
	targets   map[ResourceType]Target
}

// New builds the foundation over one database handle.
func New(db *sql.DB) *Service {
	return &Service{DB: db, targets: make(map[ResourceType]Target)}
}

// Register adds one resource adapter. Registering the same type twice is a
// programming error and panics at wiring time, never at request time.
func (s *Service) Register(target Target) {
	kind := target.ResourceType()
	if _, exists := s.targets[kind]; exists {
		panic("deletion: target registered twice for " + string(kind))
	}
	s.targets[kind] = target
}

// Job is the contract's DeletionJob plus the owner-side facts the foundation needs.
type Job struct {
	ID              string
	State           string
	ResourceID      string
	ResourceType    ResourceType
	RemainingScopes []string
}

// Delete performs one logical delete for callerID.
//
// Idempotency is structural: the resource tombstone is the key, and the
// UNIQUE(resource_type, resource_id, deletion_revision) row means a repeated or
// concurrent DELETE returns the stored job (same id) instead of a second one.
// The transaction commits before the 202 is written; the cancellation fan-out
// happens after it and a cancellation failure is recorded on the job, never by
// making the deleted content readable again.
func (s *Service) Delete(
	ctx context.Context, callerID string, resourceType ResourceType, resourceID string,
) (*Job, error) {
	caller := strings.TrimSpace(callerID)
	if !uuid.Valid(caller) {
		return nil, failure(401, "unauthenticated", "an authenticated human is required")
	}
	target, ok := s.targets[resourceType]
	if !ok {
		// A resource type with no authority yet (conversation is T074's) is
		// indistinguishable from a missing id by design.
		return nil, ErrNotFound
	}
	id := strings.TrimSpace(resourceID)
	if !uuid.Valid(id) {
		return nil, ErrNotFound
	}

	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return nil, fmt.Errorf("starting the deletion transaction: %w", err)
	}
	defer func() { _ = tx.Rollback() }()

	plan, err := target.Delete(ctx, tx, caller, id)
	if err != nil {
		return nil, err
	}

	job, err := s.existingJobTx(ctx, tx, resourceType, id)
	if err != nil {
		return nil, err
	}
	created := false
	if job == nil {
		job, err = s.insertJobTx(ctx, tx, caller, resourceType, id, plan)
		if err != nil {
			return nil, err
		}
		created = true
	}

	metadata := map[string]any{
		"deletion_id":       job.ID,
		"resource_type":     string(resourceType),
		"resource_id":       id,
		"deletion_revision": plan.Revision,
		"remaining_scopes":  job.RemainingScopes,
	}
	runIDs := make([]string, 0, len(plan.AffectedRuns))
	for _, run := range plan.AffectedRuns {
		runIDs = append(runIDs, run.RunID)
	}
	metadata["affected_run_ids"] = runIDs
	actor := caller
	if err := audit.Record(ctx, tx, &actor, "deletion.requested", string(resourceType), &id, metadata); err != nil {
		return nil, err
	}
	if err := tx.Commit(); err != nil {
		return nil, fmt.Errorf("committing the deletion: %w", err)
	}

	// A repeated delete also retries cancellation for runs that were still
	// non-terminal when the target re-read them.
	if len(plan.AffectedRuns) > 0 {
		s.cancelAffectedRuns(ctx, caller, job, plan.AffectedRuns, created)
	}
	return job, nil
}

// Get returns one deletion job to its owner. A foreign/unknown/malformed id is
// the same 404 (no enumeration); an administrator is not exempt.
func (s *Service) Get(ctx context.Context, callerID, deletionID string) (*Job, error) {
	caller := strings.TrimSpace(callerID)
	if !uuid.Valid(caller) {
		return nil, failure(401, "unauthenticated", "an authenticated human is required")
	}
	id := strings.TrimSpace(deletionID)
	if !uuid.Valid(id) {
		return nil, ErrNotFound
	}
	var (
		job  Job
		raw  []byte
		kind string
	)
	err := s.DB.QueryRowContext(ctx, `
		SELECT id::text, state, resource_id::text, resource_type, remaining_scopes
		  FROM platform.deletion_jobs
		 WHERE id = $1::uuid AND owner_id = $2::uuid`, id, caller,
	).Scan(&job.ID, &job.State, &job.ResourceID, &kind, &raw)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrNotFound
	}
	if err != nil {
		return nil, fmt.Errorf("reading the deletion job: %w", err)
	}
	job.ResourceType = ResourceType(kind)
	if err := json.Unmarshal(raw, &job.RemainingScopes); err != nil {
		return nil, fmt.Errorf("decoding the deletion scopes: %w", err)
	}
	if job.RemainingScopes == nil {
		job.RemainingScopes = []string{}
	}
	return &job, nil
}

// insertJobTx writes the one job row for a resource. ON CONFLICT keeps a
// concurrent second delete from failing; the read-back makes the returned id the
// stored one.
func (s *Service) insertJobTx(
	ctx context.Context, tx *sql.Tx, ownerID string, resourceType ResourceType,
	resourceID string, plan Plan,
) (*Job, error) {
	scopes := make([]string, 0, len(AllScopes))
	for _, scope := range AllScopes {
		scopes = append(scopes, string(scope))
	}
	// The vocabulary is sorted at the source; keep the stored array sorted too so
	// the contract projection is stable for tests and operators.
	sort.Strings(scopes)
	encodedScopes, err := json.Marshal(scopes)
	if err != nil {
		return nil, fmt.Errorf("encoding the deletion scopes: %w", err)
	}
	details := make(map[string]any, len(AllScopes))
	for _, scope := range AllScopes {
		if value, ok := plan.Details[scope]; ok && value != nil {
			details[string(scope)] = value
		} else {
			details[string(scope)] = map[string]any{}
		}
	}
	encodedDetails, err := json.Marshal(details)
	if err != nil {
		return nil, fmt.Errorf("encoding the deletion scope details: %w", err)
	}

	job := &Job{
		ID:              uuid.NewString(),
		State:           StatePending,
		ResourceID:      resourceID,
		ResourceType:    resourceType,
		RemainingScopes: scopes,
	}
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO platform.deletion_jobs
		       (id, owner_id, resource_type, resource_id, deletion_revision, state,
		        remaining_scopes, scope_details, completed_scopes, attempt_count,
		        created_at, updated_at)
		VALUES ($1::uuid, $2::uuid, $3, $4::uuid, $5, 'pending',
		        $6::jsonb, $7::jsonb, '[]'::jsonb, 0, now(), now())
		ON CONFLICT (resource_type, resource_id, deletion_revision) DO NOTHING`,
		job.ID, ownerID, string(resourceType), resourceID, plan.Revision,
		string(encodedScopes), string(encodedDetails),
	); err != nil {
		return nil, fmt.Errorf("recording the deletion job: %w", err)
	}
	stored, err := s.existingJobTx(ctx, tx, resourceType, resourceID)
	if err != nil {
		return nil, err
	}
	if stored == nil {
		return nil, errors.New("the deletion job disappeared inside its own transaction")
	}
	return stored, nil
}

// existingJobTx reads the newest job for a resource. The tombstone and the
// unique constraint allow exactly one non-retried job today; ordering keeps the
// read total if a future cleanup ticket ever inserts another revision.
func (s *Service) existingJobTx(
	ctx context.Context, tx *sql.Tx, resourceType ResourceType, resourceID string,
) (*Job, error) {
	var (
		job  Job
		raw  []byte
		kind string
	)
	err := tx.QueryRowContext(ctx, `
		SELECT id::text, state, resource_id::text, resource_type, remaining_scopes
		  FROM platform.deletion_jobs
		 WHERE resource_type = $1 AND resource_id = $2::uuid
		 ORDER BY created_at DESC, id DESC
		 LIMIT 1`, string(resourceType), resourceID,
	).Scan(&job.ID, &job.State, &job.ResourceID, &kind, &raw)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("reading the deletion job: %w", err)
	}
	job.ResourceType = ResourceType(kind)
	if err := json.Unmarshal(raw, &job.RemainingScopes); err != nil {
		return nil, fmt.Errorf("decoding the deletion scopes: %w", err)
	}
	if job.RemainingScopes == nil {
		job.RemainingScopes = []string{}
	}
	return &job, nil
}

// cancelAffectedRuns drives the existing T036 cancel path for every selected run
// and records the safe outcome in the audit trail. A cancellation refusal never
// resurrects the resource: the tombstone already committed. The job's
// error_code records an incomplete fan-out so operators (and a repeated DELETE)
// can retry it.
func (s *Service) cancelAffectedRuns(
	ctx context.Context, callerID string, job *Job, runs []AffectedRun, firstAttempt bool,
) {
	results := make([]map[string]any, 0, len(runs))
	failures := 0
	for _, run := range runs {
		outcome := "requested"
		errCode := ""
		if s.CancelRun == nil {
			outcome, errCode = "unavailable", "run_cancel_unavailable"
		} else if err := s.CancelRun(ctx, callerID, run.RunID); err != nil {
			outcome, errCode = "failed", "run_cancel_failed"
		}
		state := s.runState(ctx, run.RunID)
		if state == "cancelled" || state == "succeeded" || state == "failed" {
			outcome = "terminal"
		}
		if errCode != "" {
			failures++
		}
		results = append(results, map[string]any{
			"run_id":         run.RunID,
			"previous_state": run.State,
			"result_state":   state,
			"outcome":        outcome,
		})
	}
	if len(results) == 0 {
		return
	}

	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		// Best effort: the next repeated DELETE re-selects the affected runs.
		return
	}
	defer func() { _ = tx.Rollback() }()
	actor := callerID
	for _, result := range results {
		runID, _ := result["run_id"].(string)
		if err := audit.Record(ctx, tx, &actor, "deletion.run_cancel", "run", &runID, result); err != nil {
			return
		}
	}
	if failures > 0 {
		if _, err := tx.ExecContext(ctx, `
			UPDATE platform.deletion_jobs
			   SET error_code = 'run_cancel_incomplete', updated_at = now()
			 WHERE id = $1::uuid AND state <> 'completed'`, job.ID); err != nil {
			return
		}
	} else if firstAttempt {
		if _, err := tx.ExecContext(ctx, `
			UPDATE platform.deletion_jobs SET updated_at = now()
			 WHERE id = $1::uuid`, job.ID); err != nil {
			return
		}
	}
	_ = tx.Commit()
}

// runState reads the committed run state, or "" when the run is gone.
func (s *Service) runState(ctx context.Context, runID string) string {
	var state string
	if err := s.DB.QueryRowContext(ctx, `
		SELECT state FROM platform.runs WHERE id = $1::uuid AND deleted_at IS NULL`,
		runID).Scan(&state); err != nil {
		return ""
	}
	return state
}

// Error is one located refusal the API maps onto the contract envelope. The
// package returns ErrNotFound/InUseError for the two deletion-specific outcomes
// and this type only for authentication, mirroring the memory service precedent.
type Error struct {
	Status  int
	Code    string
	Message string
}

func (e *Error) Error() string { return e.Message }

func failure(status int, code, message string) *Error {
	return &Error{Status: status, Code: code, Message: message}
}

// AsError reports whether err is a located refusal from this package.
func AsError(err error) (*Error, bool) {
	var typed *Error
	if errors.As(err, &typed) {
		return typed, true
	}
	return nil, false
}
