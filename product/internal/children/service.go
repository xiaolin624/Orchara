// Package children owns F07/T078's fixed-version child Run creation: the
// spawnChild internal operation, its idempotent replay, and the read path the
// executor uses to fetch a terminal child's structured result.
//
// The design is one transaction (see Spawn):
//
//	lock parent Run ──> prove live attempt token ──> replay read by
//	(parent_run_id, parent_invocation_key) ──> freeze the child version
//	(ACL ∩ not-revoked/archived) ──> prove the parent's *frozen* manifest declares
//	it and T075's mapping/limits still hold ──> match the supplied input to one
//	declared mapping and validate it against the child's frozen input schema ──>
//	measure runtime depth and the parent's child-call count ──> seal the mapped
//	input ──> create the published child snapshot + Run + start job + receipt ──>
//	return the contract Run.
//
// The parent row is locked first, so check-then-insert is atomic even without
// the UNIQUE(parent_run_id, parent_invocation_key) backstop that 0014 already
// owns. The child Run's owner is always the parent Run's owner, never the child
// author: permissions are the caller's current ACL ∩ the parent's frozen grant ∩
// the child's own frozen manifest, and the last of those is the only capability
// surface the child can use.
//
// This package imports catalog and execution (never the reverse) and is wired by
// api.New. It writes no HTTP status: typed Error values are mapped by the API.
package children

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"strings"

	"orchara/platform/internal/catalog"
	"orchara/platform/internal/execution"
	"orchara/platform/internal/jcs"
	"orchara/platform/internal/objectstore"
	"orchara/platform/internal/uuid"
)

// Contract limits not fixed by the schema, chosen here and documented in the
// T078 report: the logical key is a stable operation identity (the same bound as
// a capability logical key), and a mapped child input is one JSON document.
const (
	MaxLogicalKeyBytes = 200
	MaxInputBytes      = 20 << 20
)

// Artifact purpose of the sealed mapped input. It is the same purpose the debug
// path uses for a run's sealed input, so F11 deletion scopes already cover it.
const PurposeRunInput = "run_input"

// Stable refusal codes. The contract leaves the semantics of 409/429 open; the
// T078 report/ADR fix them.
const (
	CodeRunNotFound            = "run_not_found"
	CodeRunTerminal            = "run_terminal"
	CodeRunStateConflict       = "run_state_conflict"
	CodeStaleAttempt           = "stale_attempt"
	CodeInvocationConflict     = "child_invocation_conflict"
	CodeChildNotInClosure      = "child_not_in_dependency_closure"
	CodeChildNotVisible        = "child_not_visible"
	CodeChildVersionRevoked    = "child_version_revoked"
	CodeChildVersionArchived   = "child_version_archived"
	CodeChildVersionNotReady   = "child_version_not_ready"
	CodeChildInputInvalid      = "child_input_invalid"
	CodeChildInputNotMapped    = "child_input_mapping_mismatch"
	CodeChildCallsExceeded     = "child_calls_exceeded"
	CodeChildDepthExceeded     = "child_depth_exceeded"
	CodeChildNotTerminal       = "child_not_terminal"
	CodeObjectStoreUnavailable = "object_store_unavailable"
	CodeResultNotReadable      = "child_result_unavailable"
)

// Error is one located refusal. The API maps Status directly and never invents
// its own status for this package's errors.
type Error struct {
	Status  int
	Code    string
	Message string
	Details map[string]any
}

func (e *Error) Error() string { return e.Message }

func refusal(status int, code, message string) *Error {
	return &Error{Status: status, Code: code, Message: message}
}

func refusalWithDetails(status int, code, message string, details map[string]any) *Error {
	return &Error{Status: status, Code: code, Message: message, Details: details}
}

// AsError reports whether err is a located refusal from this package.
func AsError(err error) (*Error, bool) {
	var typed *Error
	if errors.As(err, &typed) {
		return typed, true
	}
	return nil, false
}

// RunView is exactly the contract Run schema (ten additionalProperties:false
// fields). The projection is duplicated from catalog.Run on purpose: catalog
// imports execution, so this package cannot import catalog.Run without a cycle.
// The column list is identical; a contract change must touch both and the T078
// test asserts the response shape.
type RunView struct {
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

	// ManifestSHA256 is carried in memory for the non-contract X-Manifest-SHA256
	// response header the other run reads also use; never serialised.
	ManifestSHA256 string `json:"-"`
}

const runColumns = `id::text, state, revision, application_version_id::text,
	parent_run_id::text, output_artifact_id::text, error_code, kind,
	execution_snapshot_id::text, phase, manifest_sha256`

func scanRun(row interface{ Scan(dest ...any) error }) (*RunView, error) {
	var (
		run                  RunView
		applicationVersionID sql.NullString
		parentRunID          sql.NullString
		outputArtifactID     sql.NullString
		errorCode            sql.NullString
	)
	if err := row.Scan(
		&run.ID, &run.State, &run.Revision, &applicationVersionID, &parentRunID,
		&outputArtifactID, &errorCode, &run.Kind, &run.ExecutionSnapshotID,
		&run.Phase, &run.ManifestSHA256,
	); err != nil {
		return nil, err
	}
	run.ApplicationVersionID = nullableString(applicationVersionID)
	run.ParentRunID = nullableString(parentRunID)
	run.OutputArtifactID = nullableString(outputArtifactID)
	run.ErrorCode = nullableString(errorCode)
	return &run, nil
}

func nullableString(value sql.NullString) *string {
	if !value.Valid {
		return nil
	}
	copy := value.String
	return &copy
}

// Service creates and reads delegated child Runs.
type Service struct {
	DB      *sql.DB
	Catalog *catalog.Reader
	Objects *objectstore.Store
}

// New builds the service. Catalog and Objects are required: a deployment without
// them refuses spawn instead of creating a Run whose input/manifest identity
// cannot be frozen.
func New(db *sql.DB, cat *catalog.Reader, objects *objectstore.Store) *Service {
	return &Service{DB: db, Catalog: cat, Objects: objects}
}

// SpawnRequest is the contract spawnChild body. Input stays raw so the canonical
// bytes (and their hash) are the caller's exact document, not a re-encoding.
type SpawnRequest struct {
	AttemptToken         string
	LogicalKey           string
	ApplicationVersionID string
	Input                json.RawMessage
}

// SpawnOutcome is the 202 body plus the replay fact for logging.
type SpawnOutcome struct {
	Run      *RunView
	Replayed bool
}

// ChildResultView is the executor's read of one terminal child. Output is the
// decoded JSON object that the child's own frozen output schema accepted.
type ChildResultView struct {
	ChildRunID       string         `json:"child_run_id"`
	State            string         `json:"state"`
	OutputArtifactID *string        `json:"output_artifact_id"`
	Output           map[string]any `json:"output,omitempty"`
	ErrorCode        *string        `json:"error_code,omitempty"`
}

type parentFacts struct {
	ID                  string
	OwnerID             string
	State               string
	CancelRequested     bool
	Deleted             bool
	ExecutionSnapshotID string
	ConversationID      sql.NullString
}

// Spawn creates (or idempotently replays) one child Run.
func (s *Service) Spawn(
	ctx context.Context, parentRunID string, request SpawnRequest,
) (*SpawnOutcome, error) {
	parentRunID = strings.ToLower(strings.TrimSpace(parentRunID))
	if !uuid.Valid(parentRunID) {
		return nil, refusal(404, CodeRunNotFound, "that run does not exist")
	}
	attemptToken := strings.ToLower(strings.TrimSpace(request.AttemptToken))
	if !uuid.Valid(attemptToken) {
		return nil, refusal(409, CodeStaleAttempt,
			"the attempt token does not own a live lease on this run")
	}
	logicalKey := strings.TrimSpace(request.LogicalKey)
	if !validLogicalKey(logicalKey) {
		return nil, refusalWithDetails(422, "validation_failed",
			"logical_key must be 1..200 printable characters",
			map[string]any{"field": "logical_key"})
	}
	versionID := strings.ToLower(strings.TrimSpace(request.ApplicationVersionID))
	if !uuid.Valid(versionID) {
		return nil, refusalWithDetails(422, "validation_failed",
			"application_version_id must be a UUID",
			map[string]any{"field": "application_version_id"})
	}
	input, canonical, sha, err := canonicalMappedInput(request.Input)
	if err != nil {
		return nil, err
	}
	if s.Catalog == nil {
		return nil, refusal(503, "catalog_unavailable",
			"the catalog is not configured; a child version cannot be resolved")
	}
	if s.Objects == nil {
		return nil, refusal(503, CodeObjectStoreUnavailable,
			"the object store is not configured; the mapped input cannot be sealed")
	}

	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return nil, fmt.Errorf("starting the child-run transaction: %w", err)
	}
	defer func() { _ = tx.Rollback() }()

	parent, err := lockParentRunTx(ctx, tx, parentRunID)
	if err != nil {
		return nil, err
	}
	if parent.Deleted {
		return nil, refusal(404, CodeRunNotFound, "that run does not exist")
	}
	switch parent.State {
	case "succeeded", "failed", "cancelled":
		return nil, refusal(409, CodeRunTerminal,
			"the parent run is already terminal; a late delegation never revives it")
	case "running":
	default:
		return nil, refusal(409, CodeRunStateConflict,
			"the parent run is not executing; only a running attempt may delegate")
	}
	if parent.CancelRequested {
		return nil, refusal(409, CodeRunStateConflict,
			"the parent run has a recorded cancel intent; no new child task is accepted")
	}
	if err := execution.RequireLiveAttemptTx(ctx, tx, parentRunID, attemptToken); err != nil {
		if errors.Is(err, execution.ErrStaleAttempt) {
			return nil, refusal(409, CodeStaleAttempt,
				"the attempt token does not own a live lease on this run")
		}
		return nil, err
	}

	// Idempotency is decided before anything is resolved again: a replay must
	// return the original child even if the caller's ACL or the child version was
	// revoked after it was created. The work already exists; hiding it would
	// strand the parent.
	existing, err := replayChildTx(ctx, tx, parentRunID, logicalKey)
	if err != nil {
		return nil, err
	}
	if existing != nil {
		if existing.childVersionID != versionID {
			return nil, refusalWithDetails(409, CodeInvocationConflict,
				"the same parent invocation key was already used for a different child application version",
				map[string]any{"field": "application_version_id"})
		}
		if existing.inputSHA256 != sha {
			return nil, refusalWithDetails(409, CodeInvocationConflict,
				"the same parent invocation key was already used with a different mapped input",
				map[string]any{"field": "input"})
		}
		run, err := readRunTx(ctx, tx, existing.childRunID)
		if err != nil {
			return nil, err
		}
		if err := tx.Commit(); err != nil {
			return nil, fmt.Errorf("committing the child-run replay: %w", err)
		}
		return &SpawnOutcome{Run: run, Replayed: true}, nil
	}

	// The child version is resolved as the *caller's* resource. A caller without
	// a current read grant cannot reach another principal's application through a
	// child, even when the parent manifest pinned it earlier.
	version, err := s.Catalog.ResolveRunnableVersionTx(ctx, tx, parent.OwnerID, versionID)
	if err != nil {
		switch {
		case errors.Is(err, catalog.ErrNotVisible):
			return nil, refusal(403, CodeChildNotVisible,
				"the child application version does not exist or the caller cannot read its application")
		case errors.Is(err, catalog.ErrVersionRevoked):
			return nil, refusal(409, CodeChildVersionRevoked,
				"the child application version was revoked; no new child Run may bind it")
		case errors.Is(err, catalog.ErrVersionArchived):
			return nil, refusal(409, CodeChildVersionArchived,
				"the child application version belongs to an archived application")
		default:
			return nil, err
		}
	}
	parentDefinition, snapshotManifest, err := parentFrozenDefinitionTx(ctx, tx, parent.ExecutionSnapshotID)
	if err != nil {
		return nil, err
	}
	childDefinition, ok := catalog.DefinitionOfManifest(version.Manifest)
	if !ok {
		return nil, refusal(422, CodeChildVersionNotReady,
			"the child version has no frozen execution definition; it is not callable")
	}

	// 1. The child must be part of the parent's *frozen* closure. This is the
	// published-fixed-dependency proof: the live draft and the router are never
	// consulted at spawn time.
	if !declaredSubapp(snapshotManifest, versionID) {
		return nil, refusal(403, CodeChildNotInClosure,
			"the child application version is not part of the parent run's frozen sub-application closure")
	}
	// 2. T075's mapping and declared-limit gate, re-run against the same frozen
	// schemas publication checked.
	mappings, located := catalog.CheckChildSpawn(parentDefinition, versionID, childDefinition)
	if len(located) > 0 {
		return nil, refusalWithDetails(422, "child_mapping_invalid",
			"the frozen parent definition no longer satisfies the child delegation gate",
			map[string]any{"errors": located})
	}
	// 3. Only explicitly declared fields may cross. The mapping comes from the
	// sealed definition, never from the caller.
	mapping, fields, matched := catalog.MatchChildInputMapping(mappings, input)
	if !matched {
		return nil, refusalWithDetails(422, CodeChildInputNotMapped,
			"the mapped input fields are not exactly one call_app declaration of the frozen parent definition",
			map[string]any{
				"declared": catalog.DescribeChildMappings(mappings),
				"supplied": fields,
			})
	}
	// 4. The mapped values must satisfy the child's frozen input schema.
	if located := catalog.ValidateChildInput(childDefinition, input); len(located) > 0 {
		return nil, refusalWithDetails(422, CodeChildInputInvalid,
			"the mapped child input does not satisfy the child version's frozen input schema",
			map[string]any{"errors": located})
	}
	inputSchemaSHA, outputSchemaSHA, ok := catalog.ChildSchemaHashes(childDefinition)
	if !ok {
		return nil, refusal(422, CodeChildVersionNotReady,
			"the child version has no frozen I/O schema identity")
	}

	// 5. Runtime depth: the child's depth is the number of Run generations above
	// it. The parent's own frozen policy bounds it; F07/T080 owns the budget
	// accounting, not this structural bound.
	depth, err := childDepthTx(ctx, tx, parentRunID)
	if err != nil {
		return nil, err
	}
	maxDepth, maxCalls, policyErr := parentChildLimits(parentDefinition)
	if policyErr != nil {
		return nil, refusal(422, "child_policy_invalid", policyErr.Error())
	}
	if depth > maxDepth {
		return nil, refusalWithDetails(422, CodeChildDepthExceeded,
			"the child Run would exceed the parent policy's maximum child depth",
			map[string]any{"depth": depth, "max_child_depth": maxDepth})
	}
	childCount, err := childCallCountTx(ctx, tx, parentRunID)
	if err != nil {
		return nil, err
	}
	if childCount >= maxCalls {
		return nil, refusalWithDetails(429, CodeChildCallsExceeded,
			"the parent Run has exhausted the child calls its frozen policy allows",
			map[string]any{"used": childCount, "max_child_calls": maxCalls})
	}

	// 6. Seal exactly the mapped object. The bytes are the canonical mapped
	// input and nothing else; the receipt records which field names crossed.
	objectKey, err := objectstore.SealedKey()
	if err != nil {
		return nil, err
	}
	object, err := s.Objects.PutSealed(objectKey, bytes.NewReader(canonical), int64(len(canonical)))
	if err != nil {
		return nil, fmt.Errorf("sealing the mapped child input: %w", err)
	}
	if object.SHA256 != sha || object.Size != int64(len(canonical)) {
		_ = s.Objects.Delete(objectKey)
		return nil, errors.New("the sealed child input does not match its canonical bytes")
	}
	// From here on a rollback leaves an unreferenced sealed object. It is handed to
	// the existing orphan sweep (documented in the T078 report); no row ever
	// references it, so it can never be read.
	inputArtifactID := uuid.NewString()
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO platform.artifacts
		       (id, owner_id, object_key, purpose, sha256, media_type, size_bytes, state, revision)
		VALUES ($1::uuid, $2::uuid, $3, $4, $5, 'application/json', $6, 'ready', 1)`,
		inputArtifactID, parent.OwnerID, objectKey, PurposeRunInput, sha, len(canonical),
	); err != nil {
		_ = s.Objects.Delete(objectKey)
		return nil, fmt.Errorf("registering the sealed child input: %w", err)
	}

	// 7. The child's execution identity is its own frozen version manifest, not
	// the parent's. The snapshot is created from the version row exactly like the
	// F08/T074 published-run path.
	childBudget, childCurrency, maxActiveMS, policyProblem := catalog.RunPolicy(childDefinition)
	if policyProblem != nil {
		return nil, refusalWithDetails(422, "child_policy_invalid",
			"the child version has no runnable policy", map[string]any{"reason": policyProblem.Message})
	}
	budget, currency, err := budgetRootAllowanceTx(ctx, tx, parentRunID)
	if err != nil {
		return nil, err
	}
	// The ledger is the budget authority (usage_ledger.budget_root_run_id); the
	// Run column is the frozen display limit. Prefer the root's value, fall back to
	// the child's policych when the root row is unreadable (never invent a limit).
	if budget == "" {
		budget = childBudget
	}
	if currency == "" {
		currency = childCurrency
	}
	snapshotID := uuid.NewString()
	childRunID := uuid.NewString()
	jobID := uuid.NewString()
	checkpointThreadID := uuid.NewString()
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO platform.execution_snapshots
		       (id, kind, application_id, application_version_id, draft_revision, owner_id,
		        manifest, manifest_sha256, manifest_artifact_id, compiler_version,
		        worker_image_digest, state_schema_version)
		VALUES ($1::uuid, 'published', $2::uuid, $3::uuid, NULL, $4::uuid,
		        $5::jsonb, $6, $7::uuid, $8, $9, $10)`,
		snapshotID, version.ApplicationID, version.ID, parent.OwnerID,
		string(version.Manifest), version.ManifestSHA256, version.ManifestArtifactID,
		version.CompilerVersion, version.WorkerImageDigest, version.StateSchemaVersion,
	); err != nil {
		return nil, fmt.Errorf("creating the child execution snapshot: %w", err)
	}
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO platform.runs
		       (id, request_id, conversation_id, parent_run_id, parent_invocation_key,
		        owner_id, kind, execution_snapshot_id, application_version_id,
		        manifest_sha256, input_artifact_id, output_artifact_id,
		        checkpoint_thread_id, state, revision, next_event_seq,
		        budget_limit, currency, phase, active_elapsed_ms, max_active_ms)
		VALUES ($1::uuid, NULL, $2, $3::uuid, $4,
		        $5::uuid, 'published', $6::uuid, $7::uuid,
		        $8, $9::uuid, NULL,
		        $10::uuid, 'queued', 1, 1, $11::numeric, $12, 'idle', 0, $13)`,
		childRunID, nullableUUID(parent.ConversationID.String), parentRunID, logicalKey,
		parent.OwnerID, snapshotID, version.ID,
		version.ManifestSHA256, inputArtifactID, checkpointThreadID,
		budget, currency, maxActiveMS,
	); err != nil {
		return nil, fmt.Errorf("creating the child run: %w", err)
	}
	if _, err := execution.AppendRunStatusTx(ctx, tx, childRunID); err != nil {
		return nil, fmt.Errorf("recording the child queued run event: %w", err)
	}
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO platform.jobs (id, run_id, kind, state, available_at)
		VALUES ($1::uuid, $2::uuid, 'start', 'ready', now())`,
		jobID, childRunID,
	); err != nil {
		return nil, fmt.Errorf("creating the child start job: %w", err)
	}
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO platform.run_artifacts (run_id, artifact_id, attempt_token, relation)
		VALUES ($1::uuid, $2::uuid, NULL, 'input')`,
		childRunID, inputArtifactID,
	); err != nil {
		return nil, fmt.Errorf("linking the child input: %w", err)
	}
	// The delegation receipt: the frozen version, the canonical mapped-input
	// identity, the explicitly mapped field names and the two schema identities.
	ioMapping, err := json.Marshal(map[string]any{
		"node_id": mapping.NodeID,
		"fields":  mapping.Fields,
	})
	if err != nil {
		return nil, fmt.Errorf("encoding the delegation mapping: %w", err)
	}
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO platform.run_children
		       (child_run_id, parent_run_id, parent_attempt_token, parent_invocation_key,
		        child_version_id, input_sha256, input_artifact_id, io_mapping,
		        input_schema_sha256, output_schema_sha256, required, depth)
		VALUES ($1::uuid, $2::uuid, $3::uuid, $4,
		        $5::uuid, $6, $7::uuid, $8::jsonb,
		        $9, $10, true, $11)`,
		childRunID, parentRunID, attemptToken, logicalKey,
		version.ID, sha, inputArtifactID, string(ioMapping),
		inputSchemaSHA, outputSchemaSHA, depth,
	); err != nil {
		return nil, fmt.Errorf("recording the delegation receipt: %w", err)
	}

	run, err := readRunTx(ctx, tx, childRunID)
	if err != nil {
		return nil, err
	}
	if err := tx.Commit(); err != nil {
		return nil, fmt.Errorf("committing the child run: %w", err)
	}
	return &SpawnOutcome{Run: run}, nil
}

// canonicalMappedInput decodes, proves and canonicalises the mapped input.
func canonicalMappedInput(raw json.RawMessage) (map[string]any, []byte, string, error) {
	if len(raw) == 0 {
		return nil, nil, "", refusalWithDetails(422, "validation_failed",
			"input is required and must be the mapped JSON object",
			map[string]any{"field": "input"})
	}
	var input map[string]any
	if err := json.Unmarshal(raw, &input); err != nil || input == nil {
		return nil, nil, "", refusalWithDetails(422, CodeChildInputInvalid,
			"input must be a JSON object of the explicitly mapped child fields",
			map[string]any{"field": "input"})
	}
	canonical, err := jcs.CanonicalizeBytes(raw)
	if err != nil {
		return nil, nil, "", refusalWithDetails(422, CodeChildInputInvalid,
			"input must be valid JSON", map[string]any{"field": "input"})
	}
	if len(canonical) > MaxInputBytes {
		return nil, nil, "", refusalWithDetails(413, "payload_too_large",
			"the mapped input exceeds the child input size limit",
			map[string]any{"field": "input"})
	}
	return input, canonical, jcs.SHA256Hex(canonical), nil
}

func validLogicalKey(key string) bool {
	if key == "" || len(key) > MaxLogicalKeyBytes {
		return false
	}
	for _, char := range key {
		if char < 0x20 || char == 0x7f {
			return false
		}
	}
	return true
}

func lockParentRunTx(ctx context.Context, tx *sql.Tx, runID string) (*parentFacts, error) {
	facts := &parentFacts{}
	err := tx.QueryRowContext(ctx, `
		SELECT id::text, owner_id::text, state,
		       cancel_requested_at IS NOT NULL, deleted_at IS NOT NULL,
		       execution_snapshot_id::text, conversation_id::text
		  FROM platform.runs
		 WHERE id = $1::uuid
		 FOR UPDATE`, runID,
	).Scan(&facts.ID, &facts.OwnerID, &facts.State, &facts.CancelRequested,
		&facts.Deleted, &facts.ExecutionSnapshotID, &facts.ConversationID)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, refusal(404, CodeRunNotFound, "that run does not exist")
	}
	if err != nil {
		return nil, fmt.Errorf("locking the parent run: %w", err)
	}
	return facts, nil
}

type existingChild struct {
	childRunID     string
	childVersionID string
	inputSHA256    string
}

func replayChildTx(
	ctx context.Context, tx *sql.Tx, parentRunID, logicalKey string,
) (*existingChild, error) {
	row := &existingChild{}
	err := tx.QueryRowContext(ctx, `
		SELECT child_run_id::text, child_version_id::text, input_sha256
		  FROM platform.run_children
		 WHERE parent_run_id = $1::uuid AND parent_invocation_key = $2`,
		parentRunID, logicalKey,
	).Scan(&row.childRunID, &row.childVersionID, &row.inputSHA256)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("reading the delegation receipt: %w", err)
	}
	return row, nil
}

// parentFrozenDefinitionTx reads the parent Run's frozen snapshot definition and
// manifest. The snapshot is the authority, never the live draft.
func parentFrozenDefinitionTx(
	ctx context.Context, tx *sql.Tx, snapshotID string,
) (map[string]any, map[string]any, error) {
	var manifestText string
	err := tx.QueryRowContext(ctx, `
		SELECT manifest::text FROM platform.execution_snapshots WHERE id = $1::uuid`,
		snapshotID).Scan(&manifestText)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil, refusal(409, CodeRunStateConflict,
			"the parent run's execution snapshot no longer exists")
	}
	if err != nil {
		return nil, nil, fmt.Errorf("reading the parent execution snapshot: %w", err)
	}
	var manifest map[string]any
	if err := json.Unmarshal([]byte(manifestText), &manifest); err != nil {
		return nil, nil, refusal(409, CodeRunStateConflict,
			"the parent run's frozen manifest is not readable")
	}
	definition, ok := manifest["definition"].(map[string]any)
	if !ok {
		return nil, nil, refusal(409, CodeRunStateConflict,
			"the parent run's frozen manifest has no definition")
	}
	return definition, manifest, nil
}

func declaredSubapp(manifest map[string]any, versionID string) bool {
	raw, _ := manifest["subapp_versions"].([]any)
	for _, item := range raw {
		if value, ok := item.(string); ok && strings.EqualFold(value, versionID) {
			return true
		}
	}
	return false
}

func parentChildLimits(definition map[string]any) (int, int, error) {
	policy, _ := definition["policy"].(map[string]any)
	maxDepth, okDepth := integerMember(policy, "max_child_depth")
	maxCalls, okCalls := integerMember(policy, "max_child_calls")
	if !okDepth || !okCalls {
		return 0, 0, errors.New("the parent's frozen policy does not declare max_child_depth/max_child_calls")
	}
	return maxDepth, maxCalls, nil
}

func integerMember(object map[string]any, key string) (int, bool) {
	if object == nil {
		return 0, false
	}
	switch typed := object[key].(type) {
	case float64:
		return int(typed), true
	case int:
		return typed, true
	case json.Number:
		if parsed, err := typed.Int64(); err == nil {
			return int(parsed), true
		}
	}
	return 0, false
}

// childDepthTx returns the depth of a new child of parentRunID: a top-level
// parent's child is depth 1, a child's child is depth 2, and so on. The recursion
// is bounded so a damaged parent chain cannot loop.
func childDepthTx(ctx context.Context, tx *sql.Tx, parentRunID string) (int, error) {
	var depth int
	err := tx.QueryRowContext(ctx, `
		WITH RECURSIVE ancestors(id, parent_run_id, level) AS (
		    SELECT id, parent_run_id, 1 FROM platform.runs WHERE id = $1::uuid
		    UNION ALL
		    SELECT r.id, r.parent_run_id, a.level + 1
		      FROM platform.runs AS r
		      JOIN ancestors AS a ON r.id = a.parent_run_id
		     WHERE a.level < 8
		)
		SELECT count(*) FROM ancestors`, parentRunID).Scan(&depth)
	if err != nil {
		return 0, fmt.Errorf("measuring the child depth: %w", err)
	}
	return depth, nil
}

func childCallCountTx(ctx context.Context, tx *sql.Tx, parentRunID string) (int, error) {
	var count int
	err := tx.QueryRowContext(ctx, `
		SELECT count(*) FROM platform.run_children WHERE parent_run_id = $1::uuid`,
		parentRunID).Scan(&count)
	if err != nil {
		return 0, fmt.Errorf("counting the parent's child runs: %w", err)
	}
	return count, nil
}

// budgetRootAllowanceTx reads the allowance from the tree's root Run. The
// usage_ledger root (usage_ledger.budget_root_run_id) is the accounting
// authority; the Run column is the frozen display limit copied at creation.
func budgetRootAllowanceTx(
	ctx context.Context, tx *sql.Tx, parentRunID string,
) (string, string, error) {
	var budget, currency string
	err := tx.QueryRowContext(ctx, `
		WITH RECURSIVE chain(id, parent_run_id, level) AS (
		    SELECT id, parent_run_id, 1 FROM platform.runs WHERE id = $1::uuid
		    UNION ALL
		    SELECT r.id, r.parent_run_id, c.level + 1
		      FROM platform.runs AS r
		      JOIN chain AS c ON r.id = c.parent_run_id
		     WHERE c.level < 8
		)
		SELECT root.budget_limit::text, root.currency
		  FROM platform.runs AS root
		 WHERE root.id = (SELECT id FROM chain WHERE parent_run_id IS NULL LIMIT 1)`,
		parentRunID).Scan(&budget, &currency)
	if errors.Is(err, sql.ErrNoRows) {
		return "", "", nil
	}
	if err != nil {
		return "", "", fmt.Errorf("reading the budget root: %w", err)
	}
	return budget, currency, nil
}

// readRunTx reads one contract Run projection inside a transaction.
func readRunTx(ctx context.Context, tx *sql.Tx, runID string) (*RunView, error) {
	run, err := scanRun(tx.QueryRowContext(ctx, `
		SELECT `+runColumns+` FROM platform.runs WHERE id = $1::uuid`, runID))
	if errors.Is(err, sql.ErrNoRows) {
		return nil, refusal(404, CodeRunNotFound, "that run does not exist")
	}
	if err != nil {
		return nil, fmt.Errorf("reading the child run: %w", err)
	}
	return run, nil
}

// Result returns one terminal child's structured result to the parent executor.
// The caller must still hold a live attempt on the parent: a delegated child is
// only readable inside the parent Run's own authorized execution.
func (s *Service) Result(
	ctx context.Context, parentRunID, childRunID, attemptToken string,
) (*ChildResultView, error) {
	parentRunID = strings.ToLower(strings.TrimSpace(parentRunID))
	childRunID = strings.ToLower(strings.TrimSpace(childRunID))
	attemptToken = strings.ToLower(strings.TrimSpace(attemptToken))
	if !uuid.Valid(parentRunID) || !uuid.Valid(childRunID) || !uuid.Valid(attemptToken) {
		return nil, refusal(404, CodeRunNotFound, "that run does not exist")
	}
	if s.Objects == nil {
		return nil, refusal(503, CodeObjectStoreUnavailable,
			"the object store is not configured; the child result cannot be read")
	}
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return nil, fmt.Errorf("starting the child-result transaction: %w", err)
	}
	defer func() { _ = tx.Rollback() }()

	parent, err := lockParentRunTx(ctx, tx, parentRunID)
	if err != nil {
		return nil, err
	}
	if parent.Deleted {
		return nil, refusal(404, CodeRunNotFound, "that run does not exist")
	}
	if err := execution.RequireLiveAttemptTx(ctx, tx, parentRunID, attemptToken); err != nil {
		if errors.Is(err, execution.ErrStaleAttempt) {
			return nil, refusal(409, CodeStaleAttempt,
				"the attempt token does not own a live lease on this run")
		}
		return nil, err
	}
	var linked bool
	if err := tx.QueryRowContext(ctx, `
		SELECT EXISTS (
		    SELECT 1 FROM platform.run_children
		     WHERE child_run_id = $1::uuid AND parent_run_id = $2::uuid
		)`, childRunID, parentRunID).Scan(&linked); err != nil {
		return nil, fmt.Errorf("proving the child belongs to the parent: %w", err)
	}
	if !linked {
		return nil, refusal(404, CodeRunNotFound,
			"that child run does not belong to this parent run")
	}
	var (
		state            string
		outputArtifactID sql.NullString
		errorCode        sql.NullString
	)
	err = tx.QueryRowContext(ctx, `
		SELECT state, output_artifact_id::text, error_code
		  FROM platform.runs WHERE id = $1::uuid`, childRunID,
	).Scan(&state, &outputArtifactID, &errorCode)
	if err != nil {
		return nil, fmt.Errorf("reading the child run: %w", err)
	}
	view := &ChildResultView{ChildRunID: childRunID, State: state}
	if errorCode.Valid {
		view.ErrorCode = &errorCode.String
	}
	if outputArtifactID.Valid {
		view.OutputArtifactID = &outputArtifactID.String
	}
	switch state {
	case "succeeded":
		if !outputArtifactID.Valid {
			return nil, refusal(409, CodeResultNotReadable,
				"the child run succeeded without an output artifact")
		}
		output, err := s.readOutputArtifactTx(ctx, tx, outputArtifactID.String)
		if err != nil {
			return nil, err
		}
		view.Output = output
	case "failed", "cancelled":
		// A terminal non-success is returned as facts; the parent executor turns a
		// required-child failure into its own stable node outcome (T080 owns the
		// parent-budget/required-branch convergence).
	default:
		return nil, refusal(409, CodeChildNotTerminal,
			"the child run has not reached a terminal state; the parent must wait for it")
	}
	if err := tx.Commit(); err != nil {
		return nil, fmt.Errorf("committing the child-result read: %w", err)
	}
	return view, nil
}

// readOutputArtifactTx reads and verifies one sealed output artifact the same way
// the execution controller verifies a final output before reporting success.
func (s *Service) readOutputArtifactTx(
	ctx context.Context, tx *sql.Tx, artifactID string,
) (map[string]any, error) {
	var (
		objectKey string
		sha256    string
		size      int64
		state     string
		deleted   bool
	)
	err := tx.QueryRowContext(ctx, `
		SELECT object_key, sha256, size_bytes, state, deleted_at IS NOT NULL
		  FROM platform.artifacts WHERE id = $1::uuid`, artifactID,
	).Scan(&objectKey, &sha256, &size, &state, &deleted)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, refusal(409, CodeResultNotReadable, "the child output artifact no longer exists")
	}
	if err != nil {
		return nil, fmt.Errorf("reading the child output artifact: %w", err)
	}
	if state != "ready" || deleted {
		return nil, refusal(409, CodeResultNotReadable, "the child output artifact is not a ready artifact")
	}
	if size <= 0 || size > MaxInputBytes {
		return nil, refusal(409, CodeResultNotReadable, "the child output artifact has an unusable size")
	}
	reader, _, err := s.Objects.Open(objectKey)
	if err != nil {
		return nil, fmt.Errorf("opening the child output artifact: %w", err)
	}
	defer reader.Close()
	payload, err := io.ReadAll(io.LimitReader(reader, MaxInputBytes+1))
	if err != nil {
		return nil, fmt.Errorf("reading the child output artifact: %w", err)
	}
	if int64(len(payload)) != size || !strings.EqualFold(jcs.SHA256Hex(payload), sha256) {
		return nil, refusal(409, CodeResultNotReadable,
			"the child output artifact bytes do not match the recorded hash")
	}
	var output map[string]any
	if err := json.Unmarshal(payload, &output); err != nil || output == nil {
		return nil, refusal(409, CodeResultNotReadable,
			"the child output artifact is not a JSON object")
	}
	return output, nil
}

// ListForParent returns every child Run of one parent in creation order. The API
// checks the owner through catalog.GetRun first; this read has no ACL of its own
// and is never reachable from an unauthenticated path.
func (s *Service) ListForParent(ctx context.Context, parentRunID string) ([]RunView, error) {
	if !uuid.Valid(parentRunID) {
		return nil, refusal(404, CodeRunNotFound, "that run does not exist")
	}
	rows, err := s.DB.QueryContext(ctx, `
		SELECT `+runColumns+`
		  FROM platform.runs
		 WHERE parent_run_id = $1::uuid
		   AND deleted_at IS NULL
		 ORDER BY created_at, id`, parentRunID)
	if err != nil {
		return nil, fmt.Errorf("listing the child runs: %w", err)
	}
	defer rows.Close()
	items := make([]RunView, 0, 4)
	for rows.Next() {
		run, err := scanRun(rows)
		if err != nil {
			return nil, fmt.Errorf("listing the child runs: %w", err)
		}
		items = append(items, *run)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("listing the child runs: %w", err)
	}
	return items, nil
}

// nullableUUID renders an optional uuid text column value for a nullable parameter.
func nullableUUID(value string) any {
	if strings.TrimSpace(value) == "" {
		return nil
	}
	return value
}
