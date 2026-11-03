package catalog

// Debug submission for F02A/T027.
//
// A debug submission freezes one draft revision into an immutable execution
// identity and queues exactly one run:
//
//	application_drafts(revision R) --compile--> sealed manifest artifact (T025)
//	                                           -> execution_snapshots(kind=debug)
//	                                           -> runs(kind=debug, queued, phase=idle)
//	                                           -> jobs(kind=start, state=ready)
//	                                           -> artifacts(purpose=run_input)
//	                                           -> run_artifacts(inputs)
//	                                           -> idempotency_keys(202 Run body)
//
// It creates no application_version, publication_review, request, conversation,
// report or background job: debug is not publication, and the contract's
// kind='debug' CHECKs say so in the database.
//
// Rules that shape this file:
//
//   - **Owner only.** A read grant is sufficient to read a draft and never to
//     compile or execute it. Non-owners (including administrators without
//     ownership) get the same "not visible" answer as an unknown id.
//   - **The draft revision is frozen by re-checking it under a row lock in the
//     creating transaction.** A save that races the submission either landed
//     before the lock (revision differs -> 409 revision_conflict) or blocks until
//     the run is committed (it then sees no change to this run). Every accepted
//     save increments `revision`, so an unchanged revision proves the validated
//     manifest still describes the frozen draft content.
//   - **Idempotency is atomic with creation.** The receipt row is inserted in the
//     same transaction as the snapshot/run/job; a concurrent duplicate blocks on
//     the unique key, then replays the committed 202 body. A replay never
//     recompiles, never writes a second artifact and never creates a row.
//   - **The run's canonical test input is an artifact.** Its bytes are the RFC
//     8785 JCS form of the submitted `input` object, sealed before the
//     transaction; the artifact row records the real sha256/size and is ready.
//   - **Nothing here claims execution.** F03 owns claim/lease/events/results;
//     this file only produces the queued run and its ready start job.

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"regexp"
	"sort"
	"strings"
	"time"

	"orchara/platform/internal/compiler"
	"orchara/platform/internal/execution"
	"orchara/platform/internal/jcs"
	"orchara/platform/internal/objectstore"
	"orchara/platform/internal/uuid"
)

const (
	// MaxDebugArtifacts is the contract's `artifact_ids` maxItems.
	MaxDebugArtifacts = 5
	// DebugInputPurpose is the artifact purpose of the run's canonical test
	// input. T028 owns user uploads; this purpose is server-written only.
	DebugInputPurpose = "run_input"
	// DebugInputRelation is the run_artifacts relation linking every input to
	// the run (F03 adds output/attempt relations).
	DebugInputRelation = "input"
	// IdempotencyTTL is the API.md §1 recommended receipt lifetime. Expiry is
	// informational: the stored response remains replayable (F07 owns cleanup).
	IdempotencyTTL = 24 * time.Hour
	// debugAcceptedStatus is the status the replayed body was stored with.
	// The contract's debugDraft success is 202.
	debugAcceptedStatus = 202
	// debugIdempotencyMethod is the only method this ticket stores receipts for.
	debugIdempotencyMethod = "POST"
)

// Artifact and policy error codes added by this file. They follow the T025
// located-error vocabulary (code/pointer/message) so a client renders debug
// refusals exactly like validate refusals.
const (
	// CodeArtifactNotFound: a caller-supplied artifact id does not exist.
	CodeArtifactNotFound = "artifact_not_found"
	// CodeArtifactNotVisible: the artifact belongs to another principal.
	CodeArtifactNotVisible = "artifact_not_visible"
	// CodeArtifactNotReady: the artifact is not ready (uploading/scanning/
	// quarantined/deleted or tombstoned).
	CodeArtifactNotReady = "artifact_not_ready"
	// CodePolicyIncomplete: the validated definition has no usable policy.
	// Defence in depth: the P05 validator already requires policy.
	CodePolicyIncomplete = "policy_incomplete"
	// CodeBudgetOutOfRange: the policy budget does not fit runs.budget_limit
	// (numeric(20,8)). The workflow schema has no upper bound, so this is a
	// debug-specific refusal rather than a DSL error.
	CodeBudgetOutOfRange = "policy_budget_out_of_range"
)

var (
	// ErrIdempotencyConflict is the sentinel behind *IdempotencyConflictError:
	// the same key was used with a different canonical request.
	ErrIdempotencyConflict = errors.New("idempotency key was used for a different request")
	// ErrRevisionConflict is the sentinel behind *RevisionConflictError.
	ErrRevisionConflict = errors.New("the draft revision has moved on")
	// ErrExecutionRefused is the sentinel behind *ExecutionRefusedError: the
	// draft (or one of its dependencies/inputs) is not executable.
	ErrExecutionRefused = errors.New("the draft cannot be executed")
)

// DebugRequest is one decoded debugDraft body. The three fields are raw JSON so
// a wrong type is a located 422 (field=draft_revision/input/artifact_ids),
// exactly like a wrong type in a draft save, instead of a generic decode error.
// UnknownFields carries top-level keys outside the contract schema so the
// service can report them through the same vocabulary.
type DebugRequest struct {
	DraftRevision json.RawMessage
	Input         json.RawMessage
	ArtifactIDs   json.RawMessage
	UnknownFields []string
}

// DebugPlan is a structurally valid request plus its canonical identities. It is
// constructed by ValidateDebugRequest, so the create path never handles an
// unparsed body.
type DebugPlan struct {
	// DraftRevision is the frozen application_drafts.revision.
	DraftRevision int64
	// InputBytes are the exact UTF-8 RFC 8785 JCS bytes of the submitted input.
	InputBytes []byte
	// ArtifactIDs are the caller-supplied inputs, lower-cased, deduplicated
	// (duplicates are refused) and sorted: the set is linked to the run.
	ArtifactIDs []string
	// RequestSHA256 is the canonical request digest stored in the receipt.
	RequestSHA256 string
}

// IdempotencyScope is the receipt's primary key. Path is the concrete request
// path (`/api/v1/applications/<uuid>/debug-runs`), so the same key on two
// applications cannot collide.
type IdempotencyScope struct {
	PrincipalID string
	Method      string
	Path        string
	Key         string
}

// Run is exactly the contract's Run schema: ten fields, additionalProperties
// false. ManifestSHA256 is deliberately not serialised (the schema has no field
// for it); the HTTP layer exposes it as the documented X-Manifest-SHA256 header.
type Run struct {
	ID                   string  `json:"id"`
	State                string  `json:"state"`
	Revision             int64   `json:"revision"`
	ApplicationVersionID *string `json:"application_version_id"`
	ParentRunID          *string `json:"parent_run_id"`
	OutputArtifactID     *string `json:"output_artifact_id"`
	ErrorCode            *string `json:"error_code"`
	Kind                 string  `json:"kind"`
	ExecutionSnapshotID  string  `json:"execution_snapshot_id"`
	Phase                string  `json:"phase"`

	// ManifestSHA256 is the frozen snapshot hash, carried in memory for the
	// non-contract response header. It is never part of the JSON body.
	ManifestSHA256 string `json:"-"`
}

// DebugSubmission is the result of a debugDraft call: the run to return and
// whether it was replayed from the idempotency receipt instead of created.
type DebugSubmission struct {
	Run      *Run
	Replayed bool
}

// RevisionConflictError means the named draft revision is no longer (or never
// was) the current one. A newer revision is never silently debugged instead.
type RevisionConflictError struct {
	RequestedRevision int64
	CurrentRevision   int64
}

func (e *RevisionConflictError) Error() string {
	return fmt.Sprintf("draft revision %d is not the current revision %d", e.RequestedRevision, e.CurrentRevision)
}

// Unwrap lets callers use errors.Is(err, ErrRevisionConflict).
func (e *RevisionConflictError) Unwrap() error { return ErrRevisionConflict }

// IdempotencyConflictError means the key was already used with a different
// canonical request. Nothing is created and the stored response is not returned.
type IdempotencyConflictError struct{}

func (e *IdempotencyConflictError) Error() string {
	return "this Idempotency-Key was already used for a different request"
}

// Unwrap lets callers use errors.Is(err, ErrIdempotencyConflict).
func (e *IdempotencyConflictError) Unwrap() error { return ErrIdempotencyConflict }

// ExecutionRefusedError carries every located reason a debug submission cannot
// be executed. It is the debug counterpart of validate's `valid:false` list.
type ExecutionRefusedError struct {
	Errors []ExecutableError
}

func (e *ExecutionRefusedError) Error() string {
	if len(e.Errors) == 0 {
		return "the draft cannot be executed"
	}
	return "the draft cannot be executed: " + e.Errors[0].Code + " at " + e.Errors[0].Pointer
}

// Unwrap lets callers use errors.Is(err, ErrExecutionRefused).
func (e *ExecutionRefusedError) Unwrap() error { return ErrExecutionRefused }

// ValidateDebugRequest checks the contract body's shape and produces the
// canonical request identity.
//
// Field vocabulary (all 422 validation_failed with details.field):
//
//	draft_revision  integer >= 1
//	input           JSON object, canonicalisable (RFC 8785 JCS)
//	artifact_ids    array of at most 5 unique UUIDs (empty allowed, key required)
//
// It does not touch the database and does not decide executability.
func ValidateDebugRequest(request DebugRequest) (*DebugPlan, error) {
	if len(request.UnknownFields) > 0 {
		unknown := append([]string(nil), request.UnknownFields...)
		sort.Strings(unknown)
		return nil, &ValidationError{
			Field:   unknown[0],
			Message: "debugDraft accepts exactly draft_revision, input and artifact_ids",
		}
	}

	plan := &DebugPlan{}
	if len(bytes.TrimSpace(request.DraftRevision)) == 0 {
		return nil, &ValidationError{Field: "draft_revision", Message: "draft_revision is required"}
	}
	if err := json.Unmarshal(request.DraftRevision, &plan.DraftRevision); err != nil {
		return nil, &ValidationError{Field: "draft_revision", Message: "draft_revision must be an integer"}
	}
	if plan.DraftRevision < 1 {
		return nil, &ValidationError{Field: "draft_revision", Message: "draft_revision must be at least 1"}
	}

	trimmed := bytes.TrimSpace(request.Input)
	if len(trimmed) == 0 {
		return nil, &ValidationError{Field: "input", Message: "input is required"}
	}
	var object map[string]json.RawMessage
	if trimmed[0] != '{' || json.Unmarshal(trimmed, &object) != nil || object == nil {
		return nil, &ValidationError{Field: "input", Message: "input must be a JSON object"}
	}
	canonical, err := jcs.CanonicalizeBytes(trimmed)
	if err != nil {
		// Duplicate keys, NaN/Infinity or invalid Unicode are refused by JCS;
		// the run's input artifact must have one unambiguous byte form.
		return nil, &ValidationError{
			Field:   "input",
			Message: "input must be canonicalisable JSON (RFC 8785 JCS): " + err.Error(),
		}
	}
	if int64(len(canonical)) > objectstore.MaxObjectBytes {
		return nil, &ValidationError{
			Field:   "input",
			Message: fmt.Sprintf("the canonical input must be at most %d bytes", objectstore.MaxObjectBytes),
		}
	}
	plan.InputBytes = canonical

	if len(bytes.TrimSpace(request.ArtifactIDs)) == 0 {
		return nil, &ValidationError{Field: "artifact_ids", Message: "artifact_ids is required (it may be [])"}
	}
	var artifactIDs []string
	if err := json.Unmarshal(request.ArtifactIDs, &artifactIDs); err != nil || artifactIDs == nil {
		return nil, &ValidationError{Field: "artifact_ids", Message: "artifact_ids must be an array of UUIDs"}
	}
	if len(artifactIDs) > MaxDebugArtifacts {
		return nil, &ValidationError{
			Field:   "artifact_ids",
			Message: fmt.Sprintf("artifact_ids must contain at most %d artifacts", MaxDebugArtifacts),
		}
	}
	seen := make(map[string]bool, len(artifactIDs))
	normalised := make([]string, 0, len(artifactIDs))
	for _, id := range artifactIDs {
		if !uuid.Valid(id) {
			return nil, &ValidationError{Field: "artifact_ids", Message: "artifact_ids must contain only UUIDs"}
		}
		lower := strings.ToLower(id)
		if seen[lower] {
			return nil, &ValidationError{Field: "artifact_ids", Message: "artifact_ids must not repeat an artifact"}
		}
		seen[lower] = true
		normalised = append(normalised, lower)
	}
	sort.Strings(normalised)
	plan.ArtifactIDs = normalised
	return plan, nil
}

// debugRequestDigest is the canonical request fingerprint stored in the
// idempotency receipt: SHA-256 of the JCS bytes of
// {application_id, artifact_ids, draft_revision, input}. artifact_ids are the
// normalised set (sorted, unique), so a reordered list is the same request.
func debugRequestDigest(applicationID string, plan *DebugPlan) (string, error) {
	document := struct {
		ApplicationID string          `json:"application_id"`
		ArtifactIDs   []string        `json:"artifact_ids"`
		DraftRevision int64           `json:"draft_revision"`
		Input         json.RawMessage `json:"input"`
	}{
		ApplicationID: applicationID,
		ArtifactIDs:   plan.ArtifactIDs,
		DraftRevision: plan.DraftRevision,
		Input:         json.RawMessage(plan.InputBytes),
	}
	encoded, err := json.Marshal(document)
	if err != nil {
		return "", fmt.Errorf("encoding the idempotency request digest: %w", err)
	}
	canonical, err := jcs.CanonicalizeBytes(encoded)
	if err != nil {
		return "", fmt.Errorf("canonicalising the idempotency request digest: %w", err)
	}
	return jcs.SHA256Hex(canonical), nil
}

// SubmitDebug validates and executes one debug submission.
//
// Order is deliberate and shared with the API's error mapping:
//
//  0. body shape (422)                       — done first, cheap
//  1. stored receipt (replay or 409)         — a replay needs no draft read
//  2. owner-only draft read (404)            — non-owners learn nothing more
//  3. current draft revision (409)           — a stale revision is not debugged
//  4. ValidateExecution (422/503)            — definition, card, dependencies
//  5. run policy budget/currency (422)
//  6. referenced artifacts (422)
//  7. seal the canonical input, then one transaction:
//     receipt + snapshot + run + start job + artifact rows
//
// A replay deliberately wins over steps 2-6: the same key and the same canonical
// request describe the same already-authorized operation, so it returns the
// stored 202 body even if the draft has since moved on (the run it created stays
// frozen). A *new* key naming an old revision is refused at step 3. This
// precedence is recorded in the T027 report/ADR.
func (r *Reader) SubmitDebug(
	ctx context.Context,
	callerID, applicationID string,
	scope IdempotencyScope,
	request DebugRequest,
	objects *objectstore.Store,
	inputs compiler.Inputs,
) (*DebugSubmission, error) {
	plan, err := ValidateDebugRequest(request)
	if err != nil {
		return nil, err
	}
	if !uuid.Valid(applicationID) {
		return nil, ErrNotVisible
	}
	if strings.TrimSpace(scope.PrincipalID) == "" || !strings.EqualFold(scope.PrincipalID, callerID) ||
		strings.TrimSpace(scope.Key) == "" || scope.Method != debugIdempotencyMethod {
		return nil, &ValidationError{Field: "idempotency_key", Message: "a complete idempotency scope is required"}
	}
	digest, err := debugRequestDigest(applicationID, plan)
	if err != nil {
		return nil, err
	}
	plan.RequestSHA256 = digest

	// 1. A committed receipt answers immediately, without recompiling.
	existing, err := r.lookupIdempotency(ctx, scope)
	if err != nil {
		return nil, err
	}
	if existing != nil {
		return r.replayIdempotency(ctx, existing, digest)
	}

	// 2. Owner-only. loadDraft also hides unknown and archived applications.
	draft, ownerID, err := r.loadDraft(ctx, applicationID)
	if err != nil {
		return nil, err
	}
	if !strings.EqualFold(callerID, ownerID) {
		return nil, ErrNotVisible
	}

	// 3. The requested revision must be the current one. A draft that only
	// changed its layout still increments revision, so a layout-only save also
	// makes an older debug request stale; the returned run keeps the revision it
	// froze.
	if draft.Revision != plan.DraftRevision {
		return nil, &RevisionConflictError{
			RequestedRevision: plan.DraftRevision,
			CurrentRevision:   draft.Revision,
		}
	}

	// 4. Compile with the author-private dependency rule. Debug allows the
	// author's own ready tools without organization approval; it never allows
	// another principal's unapproved tool or a shared-execution violation.
	execution, located, err := r.ValidateExecution(
		ctx, callerID, applicationID, objects, inputs, ExecutionOptions{PrivateDebug: true},
	)
	if err != nil {
		return nil, err
	}
	if len(located) > 0 {
		return nil, &ExecutionRefusedError{Errors: located}
	}
	if execution == nil || execution.Manifest == nil {
		return nil, errors.New("debug validation produced no manifest")
	}

	// 5. The run's budget and active-time limit are the definition policy. The
	// workflow schema bounds the shapes but not the numeric column's range; the
	// active-time limit is frozen on the Run (T034), never re-read from a newer
	// draft.
	budget, currency, maxActiveMS, policyProblem := debugPolicy(execution.Manifest.Definition)
	if policyProblem != nil {
		return nil, &ExecutionRefusedError{Errors: []ExecutableError{*policyProblem}}
	}

	// 6. Caller-supplied inputs must be the caller's own ready artifacts.
	if err := r.checkDebugArtifacts(ctx, callerID, plan.ArtifactIDs); err != nil {
		return nil, err
	}

	// 7. Seal the canonical input bytes before the transaction. A failed or
	// replayed create deletes this object; a crash in between leaves an
	// unreferenced sealed object for T026's orphan sweep (documented).
	if objects == nil {
		return nil, objectstore.ErrNotConfigured
	}
	sealedKey, err := objectstore.SealedKey()
	if err != nil {
		return nil, err
	}
	inputSHA := jcs.SHA256Hex(plan.InputBytes)
	object, err := objects.PutSealed(sealedKey, bytes.NewReader(plan.InputBytes), int64(len(plan.InputBytes)))
	if err != nil {
		return nil, fmt.Errorf("sealing the debug input: %w", err)
	}
	if object.SHA256 != inputSHA || object.Size != int64(len(plan.InputBytes)) {
		_ = objects.Delete(sealedKey)
		return nil, errors.New("the sealed debug input does not match its canonical bytes")
	}
	discardInput := func() { _ = objects.Delete(sealedKey) }

	submission, err := r.createDebugRun(ctx, createDebugParams{
		CallerID:       callerID,
		ApplicationID:  applicationID,
		Scope:          scope,
		Plan:           plan,
		Execution:      execution,
		Budget:         budget,
		Currency:       currency,
		MaxActiveMS:    maxActiveMS,
		SealedInputKey: sealedKey,
		InputSHA256:    inputSHA,
		InputSize:      int64(len(plan.InputBytes)),
	})
	if err != nil {
		discardInput()
		return nil, err
	}
	if submission.Replayed {
		// The concurrent winner's receipt describes a run that references its own
		// input object; the bytes this request sealed are unreferenced. Deleting
		// them here keeps the race from leaving an orphan.
		discardInput()
	}
	return submission, nil
}

// idempotencyRecord is one stored receipt, read before/after a creation race.
type idempotencyRecord struct {
	RequestSHA256 string
	ResponseCode  sql.NullInt64
	ResponseJSON  sql.NullString
}

// lookupIdempotency reads one committed receipt, or nil when there is none.
func (r *Reader) lookupIdempotency(
	ctx context.Context, scope IdempotencyScope,
) (*idempotencyRecord, error) {
	var record idempotencyRecord
	err := r.DB.QueryRowContext(ctx, `
		SELECT request_sha256, response_code, response_json::text
		  FROM platform.idempotency_keys
		 WHERE principal_id = $1::uuid
		   AND method = $2
		   AND path = $3
		   AND key = $4`,
		scope.PrincipalID, scope.Method, scope.Path, scope.Key,
	).Scan(&record.RequestSHA256, &record.ResponseCode, &record.ResponseJSON)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("reading the idempotency receipt: %w", err)
	}
	return &record, nil
}

// replayIdempotency turns one receipt into the stored 202 response.
//
// A committed receipt always carries its response because both are written in
// the same transaction; a missing body is an internal defect, never a reason to
// create the run again. The frozen manifest hash is not part of the Run body
// (the contract schema has no field for it), so it is re-read from the run row
// for the non-contract response header.
func (r *Reader) replayIdempotency(
	ctx context.Context, record *idempotencyRecord, digest string,
) (*DebugSubmission, error) {
	if record.RequestSHA256 != digest {
		return nil, &IdempotencyConflictError{}
	}
	if !record.ResponseCode.Valid || record.ResponseCode.Int64 != debugAcceptedStatus ||
		!record.ResponseJSON.Valid {
		return nil, errors.New("the idempotency receipt has no stored 202 response")
	}
	var run Run
	if err := json.Unmarshal([]byte(record.ResponseJSON.String), &run); err != nil {
		return nil, fmt.Errorf("decoding the stored idempotent response: %w", err)
	}
	if err := r.DB.QueryRowContext(ctx,
		`SELECT manifest_sha256 FROM platform.runs WHERE id = $1::uuid`, run.ID,
	).Scan(&run.ManifestSHA256); err != nil {
		return nil, fmt.Errorf("reading the replayed run's manifest hash: %w", err)
	}
	return &DebugSubmission{Run: &run, Replayed: true}, nil
}

// createDebugParams is the validated state createDebugRun needs.
type createDebugParams struct {
	CallerID       string
	ApplicationID  string
	Scope          IdempotencyScope
	Plan           *DebugPlan
	Execution      *ValidatedExecution
	Budget         string
	Currency       string
	MaxActiveMS    int64
	SealedInputKey string
	InputSHA256    string
	InputSize      int64
}

// createDebugRun writes the receipt, snapshot, run, start job, input artifact
// and input links in one transaction. Any error rolls all of it back; the caller
// then deletes the sealed input object.
func (r *Reader) createDebugRun(
	ctx context.Context, params createDebugParams,
) (*DebugSubmission, error) {
	tx, err := r.DB.BeginTx(ctx, nil)
	if err != nil {
		return nil, fmt.Errorf("starting the debug transaction: %w", err)
	}
	defer func() { _ = tx.Rollback() }()

	// Claim the receipt first. A concurrent duplicate blocks here on the unique
	// key, then loses this insert (affected 0) and replays the committed body.
	result, err := tx.ExecContext(ctx, `
		INSERT INTO platform.idempotency_keys
		       (principal_id, method, path, key, request_sha256, expires_at)
		VALUES ($1::uuid, $2, $3, $4, $5, now() + $6::interval)
		ON CONFLICT (principal_id, method, path, key) DO NOTHING`,
		params.Scope.PrincipalID, params.Scope.Method, params.Scope.Path, params.Scope.Key,
		params.Plan.RequestSHA256, intervalOf(IdempotencyTTL),
	)
	if err != nil {
		return nil, fmt.Errorf("claiming the idempotency receipt: %w", err)
	}
	claimed, err := result.RowsAffected()
	if err != nil {
		return nil, fmt.Errorf("reading the idempotency claim: %w", err)
	}
	if claimed == 0 {
		// Lost the race. The winner has committed (otherwise this insert would
		// have succeeded); read its receipt and answer from it.
		_ = tx.Rollback()
		winner, lookupErr := r.lookupIdempotency(ctx, params.Scope)
		if lookupErr != nil {
			return nil, lookupErr
		}
		if winner == nil {
			return nil, errors.New("the idempotency claim conflicted but no receipt is visible")
		}
		return r.replayIdempotency(ctx, winner, params.Plan.RequestSHA256)
	}

	// Freeze the draft revision under a row lock. A save that already committed
	// changes the revision (409); one that starts now blocks until this run is
	// committed, so the frozen snapshot can never describe half an edit.
	var revision int64
	err = tx.QueryRowContext(ctx, `
		SELECT revision FROM platform.application_drafts
		 WHERE application_id = $1::uuid
		 FOR UPDATE`,
		params.ApplicationID,
	).Scan(&revision)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrNotVisible
	}
	if err != nil {
		return nil, fmt.Errorf("locking the draft revision: %w", err)
	}
	if revision != params.Plan.DraftRevision {
		return nil, &RevisionConflictError{
			RequestedRevision: params.Plan.DraftRevision,
			CurrentRevision:   revision,
		}
	}

	snapshotID := uuid.NewString()
	runID := uuid.NewString()
	jobID := uuid.NewString()
	inputArtifactID := uuid.NewString()
	checkpointThreadID := uuid.NewString()

	// The canonical test input. It is ready because the bytes are already sealed
	// and verified above; no finalize job is implied.
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO platform.artifacts
		       (id, owner_id, object_key, purpose, sha256, media_type, size_bytes, state, revision)
		VALUES ($1::uuid, $2::uuid, $3, $4, $5, 'application/json', $6, 'ready', 1)`,
		inputArtifactID, params.CallerID, params.SealedInputKey, DebugInputPurpose,
		params.InputSHA256, params.InputSize,
	); err != nil {
		return nil, fmt.Errorf("registering the debug input artifact: %w", err)
	}

	// The fixed execution snapshot. `manifest` is the queryable projection; the
	// hash covers the sealed canonical bytes, never this JSONB.
	manifestJSON, err := json.Marshal(params.Execution.Manifest)
	if err != nil {
		return nil, fmt.Errorf("encoding the snapshot manifest projection: %w", err)
	}
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO platform.execution_snapshots
		       (id, kind, application_id, application_version_id, draft_revision, owner_id,
		        manifest, manifest_sha256, manifest_artifact_id, compiler_version,
		        worker_image_digest, state_schema_version)
		VALUES ($1::uuid, 'debug', $2::uuid, NULL, $3, $4::uuid, $5::jsonb, $6, $7::uuid,
		        $8, $9, $10)`,
		snapshotID, params.ApplicationID, params.Plan.DraftRevision, params.CallerID,
		string(manifestJSON), params.Execution.SHA256, params.Execution.ArtifactID,
		params.Execution.Manifest.CompilerVersion, params.Execution.Manifest.WorkerImageDigest,
		params.Execution.Manifest.StateSchemaVersion,
	); err != nil {
		return nil, fmt.Errorf("creating the execution snapshot: %w", err)
	}

	// The queued debug run. request/conversation/application_version/parent stay
	// NULL: debug is private and is not a published execution.
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO platform.runs
		       (id, request_id, conversation_id, parent_run_id, parent_invocation_key,
		        owner_id, kind, execution_snapshot_id, application_version_id,
		        manifest_sha256, input_artifact_id, output_artifact_id,
		        checkpoint_thread_id, state, revision, next_event_seq,
		        budget_limit, currency, phase, active_elapsed_ms, max_active_ms)
		VALUES ($1::uuid, NULL, NULL, NULL, NULL, $2::uuid, 'debug', $3::uuid, NULL,
		        $4, $5::uuid, NULL, $6::uuid, 'queued', 1, 1, $7::numeric, $8, 'idle', 0, $9)`,
		runID, params.CallerID, snapshotID, params.Execution.SHA256, inputArtifactID,
		checkpointThreadID, params.Budget, params.Currency, params.MaxActiveMS,
	); err != nil {
		return nil, fmt.Errorf("creating the debug run: %w", err)
	}

	// F03/T032: the very first durable timeline fact, in the same transaction as
	// the queued Run. The execution helper is the single event writer, so the
	// debug path cannot grow a second seq/payload implementation.
	if _, err := execution.AppendRunStatusTx(ctx, tx, runID); err != nil {
		return nil, fmt.Errorf("recording the queued run event: %w", err)
	}

	// Exactly one ready start job; F03 owns claiming it.
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO platform.jobs (id, run_id, kind, state, available_at)
		VALUES ($1::uuid, $2::uuid, 'start', 'ready', now())`,
		jobID, runID,
	); err != nil {
		return nil, fmt.Errorf("creating the debug start job: %w", err)
	}

	// Link the canonical input first, then every caller-supplied input.
	linked := append([]string{inputArtifactID}, params.Plan.ArtifactIDs...)
	for _, artifactID := range linked {
		if _, err := tx.ExecContext(ctx, `
			INSERT INTO platform.run_artifacts (run_id, artifact_id, attempt_token, relation)
			VALUES ($1::uuid, $2::uuid, NULL, $3)`,
			runID, artifactID, DebugInputRelation,
		); err != nil {
			return nil, fmt.Errorf("linking the debug input artifact: %w", err)
		}
	}

	run := &Run{
		ID:                  runID,
		State:               "queued",
		Revision:            1,
		Kind:                "debug",
		ExecutionSnapshotID: snapshotID,
		Phase:               "idle",
		ManifestSHA256:      params.Execution.SHA256,
	}
	payload, err := json.Marshal(run)
	if err != nil {
		return nil, fmt.Errorf("encoding the debug response: %w", err)
	}
	if _, err := tx.ExecContext(ctx, `
		UPDATE platform.idempotency_keys
		   SET response_code = $5, response_json = $6::jsonb
		 WHERE principal_id = $1::uuid AND method = $2 AND path = $3 AND key = $4`,
		params.Scope.PrincipalID, params.Scope.Method, params.Scope.Path, params.Scope.Key,
		debugAcceptedStatus, string(payload),
	); err != nil {
		return nil, fmt.Errorf("storing the idempotent debug response: %w", err)
	}

	if err := tx.Commit(); err != nil {
		return nil, fmt.Errorf("committing the debug submission: %w", err)
	}
	return &DebugSubmission{Run: run, Replayed: false}, nil
}

// checkDebugArtifacts verifies every caller-supplied input: it must exist, be
// ready, not tombstoned and belong to the caller. Problems are located at
// /artifact_ids/<index> so the client can point at the offending entry.
func (r *Reader) checkDebugArtifacts(
	ctx context.Context, callerID string, ids []string,
) error {
	if len(ids) == 0 {
		return nil
	}
	type artifactFact struct {
		ownerID   string
		state     string
		deleted   bool
		sha256    sql.NullString
		sizeBytes sql.NullInt64
	}
	rows, err := r.DB.QueryContext(ctx, `
		SELECT id::text, owner_id::text, state, deleted_at IS NOT NULL, sha256, size_bytes
		  FROM platform.artifacts
		 WHERE id = ANY($1::uuid[])`, ids)
	if err != nil {
		return fmt.Errorf("reading the debug input artifacts: %w", err)
	}
	defer rows.Close()
	found := make(map[string]artifactFact, len(ids))
	for rows.Next() {
		var (
			id   string
			fact artifactFact
		)
		if err := rows.Scan(&id, &fact.ownerID, &fact.state, &fact.deleted, &fact.sha256, &fact.sizeBytes); err != nil {
			return fmt.Errorf("reading the debug input artifacts: %w", err)
		}
		found[id] = fact
	}
	if err := rows.Err(); err != nil {
		return fmt.Errorf("reading the debug input artifacts: %w", err)
	}

	located := make([]ExecutableError, 0)
	for index, id := range ids {
		fact, present := found[id]
		pointer := fmt.Sprintf("/artifact_ids/%d", index)
		switch {
		case !present:
			located = append(located, ExecutableError{
				Code: CodeArtifactNotFound, Pointer: pointer,
				Message: "the referenced input artifact does not exist",
			})
		case !strings.EqualFold(fact.ownerID, callerID):
			located = append(located, ExecutableError{
				Code: CodeArtifactNotVisible, Pointer: pointer,
				Message: "the referenced input artifact belongs to another principal; a debug run may only use the caller's own artifacts",
			})
		case fact.state != "ready" || fact.deleted || !fact.sha256.Valid || !fact.sizeBytes.Valid:
			located = append(located, ExecutableError{
				Code: CodeArtifactNotReady, Pointer: pointer,
				Message: "the referenced input artifact is not ready (state=" + fact.state +
					"); only a sealed, verified artifact can be a run input",
			})
		}
	}
	if len(located) > 0 {
		return &ExecutionRefusedError{Errors: located}
	}
	return nil
}

// debugPolicy extracts the run budget/currency and the frozen active-time limit
// from the validated definition. The workflow validator already requires all
// fields, so a problem here is defence in depth; the numeric(20,8) range is a
// debug-specific rule and the active-time limit mirrors workflow.schema.json's
// integer [1, 3600] seconds (stored on the Run as milliseconds, T034).
func debugPolicy(definition map[string]any) (string, string, int64, *ExecutableError) {
	policy, ok := definition["policy"].(map[string]any)
	if !ok {
		return "", "", 0, &ExecutableError{
			Code: CodePolicyIncomplete, Pointer: "/policy",
			Message: "the execution policy is missing or not an object",
		}
	}
	budget, ok := policy["budget_limit"].(string)
	if !ok {
		return "", "", 0, &ExecutableError{
			Code: CodePolicyIncomplete, Pointer: "/policy/budget_limit",
			Message: "policy.budget_limit must be a decimal string",
		}
	}
	if !fitsNumeric208(budget) {
		return "", "", 0, &ExecutableError{
			Code: CodeBudgetOutOfRange, Pointer: "/policy/budget_limit",
			Message: "policy.budget_limit must fit numeric(20,8): at most 12 integer digits and at most 8 fractional digits",
		}
	}
	currency, ok := policy["currency"].(string)
	if !ok || len(currency) != 3 {
		return "", "", 0, &ExecutableError{
			Code: CodePolicyIncomplete, Pointer: "/policy/currency",
			Message: "policy.currency must be a three-letter currency code",
		}
	}
	maxActiveSeconds, ok := wholeSeconds(policy["max_active_seconds"])
	if !ok || maxActiveSeconds < 1 || maxActiveSeconds > maxActiveSecondsLimit {
		return "", "", 0, &ExecutableError{
			Code: CodePolicyIncomplete, Pointer: "/policy/max_active_seconds",
			Message: "policy.max_active_seconds must be an integer between 1 and 3600",
		}
	}
	return budget, currency, maxActiveSeconds * 1000, nil
}

// maxActiveSecondsLimit mirrors workflow.schema.json's policy bound.
const maxActiveSecondsLimit = 3600

// wholeSeconds accepts the JSON number forms the validated definition can carry
// (encoding/json decodes numbers as float64; unit tests may pass int/int64) and
// refuses fractions instead of silently truncating a policy.
func wholeSeconds(value any) (int64, bool) {
	switch number := value.(type) {
	case float64:
		if math.IsNaN(number) || math.IsInf(number, 0) || number != math.Trunc(number) {
			return 0, false
		}
		return int64(number), true
	case int:
		return int64(number), true
	case int64:
		return number, true
	case json.Number:
		parsed, err := number.Int64()
		return parsed, err == nil
	default:
		return 0, false
	}
}

var decimalPattern = regexp.MustCompile(`^[0-9]+(\.[0-9]{1,8})?$`)

// fitsNumeric208 reports whether a decimal string fits runs.budget_limit
// numeric(20,8) without silently rounding: 20 total digits, 8 after the point.
func fitsNumeric208(value string) bool {
	if !decimalPattern.MatchString(value) {
		return false
	}
	integerPart, _, _ := strings.Cut(value, ".")
	trimmed := strings.TrimLeft(integerPart, "0")
	return len(trimmed) <= 12
}

// intervalOf renders a duration as the fixed SQL interval literal used above.
// Only whole seconds are produced; the value is never request input.
func intervalOf(duration time.Duration) string {
	return fmt.Sprintf("%d seconds", int64(duration/time.Second))
}

// runColumns is the one Run projection every run read shares. The contract Run
// is ten additionalProperties:false fields; manifest_sha256 is carried in memory
// for the documented X-Manifest-SHA256 header and created_at only feeds the
// list's stable keyset cursor (neither is serialised).
const runColumns = `id::text, state, revision, application_version_id::text,
	parent_run_id::text, output_artifact_id::text, error_code, kind,
	execution_snapshot_id::text, phase, manifest_sha256, created_at`

// rowScanner is the common shape of *sql.Row and *sql.Rows.
type rowScanner interface {
	Scan(dest ...any) error
}

// scanRun reads one row selected with runColumns (plus its created_at keyset
// position) into the contract Run projection.
func scanRun(row rowScanner) (*Run, time.Time, error) {
	var (
		run                  Run
		applicationVersionID sql.NullString
		parentRunID          sql.NullString
		outputArtifactID     sql.NullString
		errorCode            sql.NullString
		createdAt            time.Time
	)
	if err := row.Scan(
		&run.ID, &run.State, &run.Revision, &applicationVersionID, &parentRunID,
		&outputArtifactID, &errorCode, &run.Kind, &run.ExecutionSnapshotID, &run.Phase,
		&run.ManifestSHA256, &createdAt,
	); err != nil {
		return nil, time.Time{}, err
	}
	run.ApplicationVersionID = nullableString(applicationVersionID)
	run.ParentRunID = nullableString(parentRunID)
	run.OutputArtifactID = nullableString(outputArtifactID)
	run.ErrorCode = nullableString(errorCode)
	return &run, createdAt, nil
}

// GetRun returns one run when the caller owns it (a run has no grant type in
// this batch). Unknown, malformed, deleted and not-owned ids are all
// ErrNotVisible, so the endpoint cannot be used to enumerate runs.
func (r *Reader) GetRun(ctx context.Context, callerID, runID string) (*Run, error) {
	if !uuid.Valid(runID) || !uuid.Valid(callerID) {
		return nil, ErrNotVisible
	}
	run, _, err := scanRun(r.DB.QueryRowContext(ctx, `
		SELECT `+runColumns+`
		  FROM platform.runs
		 WHERE id = $1::uuid
		   AND owner_id = $2::uuid
		   AND deleted_at IS NULL`,
		runID, callerID,
	))
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrNotVisible
	}
	if err != nil {
		return nil, fmt.Errorf("reading the run: %w", err)
	}
	return run, nil
}

// ListRuns returns the caller's own runs, newest first, with the same stable
// (created_at DESC, id DESC) keyset order as every other cursor list.
//
// The list is owner-only and contains no grant path: a run belongs to the
// principal who submitted it (T027's getRun rule), and an administrator sees
// only the runs it owns. The owner predicate is in the same WHERE as the cursor
// and LIMIT, so filtering happens before pagination.
func (r *Reader) ListRuns(
	ctx context.Context,
	ownerID, cursor string,
	limit int,
) ([]Run, *string, error) {
	if !uuid.Valid(ownerID) {
		return nil, nil, ErrNotVisible
	}
	if limit == 0 {
		limit = DefaultLimit
	}
	if limit < MinLimit || limit > MaxLimit {
		return nil, nil, ErrInvalidLimit
	}
	var (
		cursorAt any
		cursorID any
	)
	if strings.TrimSpace(cursor) != "" {
		at, id, err := DecodeCursor(cursor)
		if err != nil {
			return nil, nil, err
		}
		cursorAt, cursorID = at, id
	}

	rows, err := r.DB.QueryContext(ctx, `
		SELECT `+runColumns+`
		  FROM platform.runs
		 WHERE owner_id = $1::uuid
		   AND deleted_at IS NULL
		   AND (
		        $2::timestamptz IS NULL
		        OR (created_at, id) < ($2::timestamptz, $3::uuid)
		       )
		 ORDER BY created_at DESC, id DESC
		 LIMIT $4`,
		ownerID, cursorAt, cursorID, limit+1,
	)
	if err != nil {
		return nil, nil, fmt.Errorf("listing runs: %w", err)
	}
	defer rows.Close()

	type scored struct {
		run       Run
		createdAt time.Time
	}
	all := make([]scored, 0, limit+1)
	for rows.Next() {
		run, createdAt, err := scanRun(rows)
		if err != nil {
			return nil, nil, fmt.Errorf("listing runs: %w", err)
		}
		all = append(all, scored{run: *run, createdAt: createdAt})
	}
	if err := rows.Err(); err != nil {
		return nil, nil, fmt.Errorf("listing runs: %w", err)
	}

	items := make([]Run, 0, len(all))
	for _, entry := range all {
		items = append(items, entry.run)
	}
	var next *string
	if len(all) > limit {
		// One extra row proves another page exists; the cursor names the last
		// returned row so the next page starts strictly after it.
		last := all[limit-1]
		token := EncodeCursor(last.createdAt, last.run.ID)
		next = &token
		items = items[:limit]
	}
	return items, next, nil
}

// nullableString converts a SQL NULL into the contract's JSON null.
func nullableString(value sql.NullString) *string {
	if !value.Valid {
		return nil
	}
	copied := value.String
	return &copied
}
