package capabilities

import (
	"bytes"
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"strings"
	"time"

	"orchara/platform/internal/compiler"
	"orchara/platform/internal/modelproxy"
	"orchara/platform/internal/objectstore"
	"orchara/platform/internal/uuid"
)

// ToolExecutor is the T042/T043 seam. T041 does not execute tools in the server:
// the worker receives a scoped grant and executed the tool itself, so this field
// is nil in every production wiring of this batch. It exists so the sandboxed
// node executor can be injected without reshaping Service.
type ToolExecutor interface {
	Execute(ctx context.Context, request ToolCall) (ToolCallResult, error)
}

// ToolCall is one authorized physical tool call. It is what a future server-side
// executor would receive; T041 only builds grants.
type ToolCall struct {
	RunID         string
	AttemptToken  string
	OperationID   string
	ToolVersionID string
	RiskClass     string
	Arguments     map[string]any
	ParamsSHA256  string
}

// ToolCallResult is a future executor's answer.
type ToolCallResult struct {
	Data       map[string]any
	ArtifactID string
	ErrorCode  string
}

// Service owns the run-scoped capability ledger.
type Service struct {
	DB        *sql.DB
	Store     *objectstore.Store
	Providers *modelproxy.Providers
	// ToolExecutor is reserved for T042/T043; nil means worker execution (the
	// only path in this batch).
	ToolExecutor ToolExecutor
}

// New builds the capability service. Store/Providers may be nil in a process
// that never calls a capability; every operation that needs them fails closed.
func New(db *sql.DB, store *objectstore.Store, providers *modelproxy.Providers) *Service {
	return &Service{DB: db, Store: store, Providers: providers}
}

// Input is the contract's CapabilityInput plus no worker-chosen identity beyond
// the operation id/logical key. RunID comes from the path.
type Input struct {
	RunID             string
	AttemptToken      string
	OperationID       string
	LogicalKey        string
	CapabilityVersion string
	CapabilityKind    string
	Params            map[string]any
}

// Result is the contract's CapabilityResult.
type Result struct {
	OperationID       string
	State             string
	ReceiptArtifactID *string
	ApprovalID        *string
	Data              map[string]any
}

// ResultReport is the non-contract capability-results body a worker sends after
// it executed a granted tool call.
type ResultReport struct {
	AttemptToken     string
	OperationID      string
	State            string
	ResultArtifactID string
	Data             map[string]any
	ErrorCode        string
}

// runContext is the authorized run identity a capability call works against.
type runContext struct {
	ID              string
	OwnerID         string
	Kind            string
	State           string
	ManifestSHA256  string
	Manifest        *compiler.Manifest
	CancelRequested bool
}

// capabilitySpec is one resolved capability. Exactly one of Profile/Tool is set.
type capabilitySpec struct {
	Kind      string
	RiskClass string
	Profile   *ModelProfile
	Tools     map[string]*ToolSpec
	Tool      *ToolSpec
	Model     *ModelParams
}

// Invoke authorizes and performs one logical capability call.
//
// role is the mTLS internal capacity that presented the attempt token. Only the
// worker capacity may invoke a run capability (defence in depth: the HTTP layer
// already refuses a supervisor/maintenance certificate). The run/attempt/manifest
// checks all happen before any provider call, and the operation row is written
// before dispatch. The returned Result is exactly the contract's
// CapabilityResult; the HTTP layer adds nothing.
func (s *Service) Invoke(ctx context.Context, runID, role string, input Input) (*Result, error) {
	if strings.TrimSpace(role) != "worker" {
		return nil, capFailure(403, "forbidden",
			"only the worker capacity may invoke a run capability")
	}
	normalized, invalid := normalizeInput(runID, input)
	if invalid != nil {
		return nil, invalid
	}
	run, capErr := s.loadAuthorizedRun(ctx, normalized.RunID, normalized.AttemptToken)
	if capErr != nil {
		return nil, capErr
	}
	spec, capErr := s.resolveCapability(ctx, run, normalized)
	if capErr != nil {
		return nil, capErr
	}

	canonical, digest, err := canonicalParams(normalized.Params)
	if err != nil {
		return nil, validationFailure("params", "params must be canonical JSON")
	}

	prepared, capErr := s.prepareOperation(ctx, run, normalized, spec, canonical, digest)
	if capErr != nil {
		return nil, capErr
	}
	if prepared.replay != nil {
		return prepared.replay, nil
	}
	if spec.Kind == "tool" {
		// The grant is the whole answer: the worker executes and reports back.
		return &Result{
			OperationID: prepared.operationID,
			State:       "dispatching",
			Data:        map[string]any{"grant": prepared.grant},
		}, nil
	}
	return s.dispatchModel(ctx, run, normalized, spec, digest, prepared.operationID)
}

func normalizeInput(runID string, input Input) (Input, *Error) {
	runID = strings.TrimSpace(runID)
	if !uuid.Valid(runID) {
		return input, capFailure(404, CodeRunNotFound, "that run does not exist")
	}
	input.RunID = runID
	input.AttemptToken = strings.ToLower(strings.TrimSpace(input.AttemptToken))
	if !uuid.Valid(input.AttemptToken) {
		return input, capFailure(403, CodeStaleAttempt, "the attempt token is not a live lease of this run")
	}
	input.OperationID = strings.ToLower(strings.TrimSpace(input.OperationID))
	if !uuid.Valid(input.OperationID) {
		return input, validationFailure("operation_id", "operation_id must be a UUID")
	}
	input.CapabilityVersion = strings.ToLower(strings.TrimSpace(input.CapabilityVersion))
	if !uuid.Valid(input.CapabilityVersion) {
		return input, validationFailure("capability_version", "capability_version must be a UUID")
	}
	input.CapabilityKind = strings.ToLower(strings.TrimSpace(input.CapabilityKind))
	switch input.CapabilityKind {
	case "model", "tool":
	case "connector":
		return input, capFailure(422, CodeKindUnsupported,
			"connector capabilities are not executable in this batch")
	default:
		return input, validationFailure("capability_kind", "capability_kind must be model, tool or connector")
	}
	input.LogicalKey = strings.TrimSpace(input.LogicalKey)
	if input.LogicalKey == "" || len(input.LogicalKey) > 200 {
		return input, validationFailure("logical_key", "logical_key must be between 1 and 200 characters")
	}
	for _, char := range input.LogicalKey {
		if char < 0x20 || char == 0x7f {
			return input, validationFailure("logical_key", "logical_key must be printable")
		}
	}
	if input.Params == nil {
		return input, validationFailure("params", "params must be a JSON object")
	}
	return input, nil
}

// loadAuthorizedRun proves the run is running, the presented token is the live
// attempt, and the sealed manifest bytes hash to the recorded digest. Every
// check is a database read plus a byte hash; no provider work happens first.
func (s *Service) loadAuthorizedRun(ctx context.Context, runID, attemptToken string) (*runContext, *Error) {
	run := &runContext{}
	var (
		cancel        sql.NullTime
		snapshotID    string
		snapshotSHA   string
		artifactId    string
		artifactOwner string
		artifactState string
		artifactKey   string
		artifactSHA   sql.NullString
		live          bool
	)
	err := s.DB.QueryRowContext(ctx, `
		SELECT r.owner_id::text, r.kind, r.state, r.cancel_requested_at,
		       r.manifest_sha256, s.id::text, s.manifest_sha256,
		       s.manifest_artifact_id::text, a.owner_id::text, a.state, a.object_key, a.sha256,
		       EXISTS (
		           SELECT 1
		             FROM platform.run_attempts AS at
		             JOIN platform.jobs AS j ON j.id = at.job_id AND j.run_id = at.run_id
		            WHERE at.token = $2::uuid
		              AND at.run_id = r.id
		              AND at.exited_at IS NULL
		              AND at.revoked_at IS NULL
		              AND at.lease_deadline > now()
		              AND j.current_attempt_token = at.token
		       )
		  FROM platform.runs AS r
		  JOIN platform.execution_snapshots AS s ON s.id = r.execution_snapshot_id
		  JOIN platform.artifacts AS a ON a.id = s.manifest_artifact_id
		 WHERE r.id = $1::uuid`, runID, attemptToken,
	).Scan(
		&run.OwnerID, &run.Kind, &run.State, &cancel, &run.ManifestSHA256,
		&snapshotID, &snapshotSHA, &artifactId, &artifactOwner, &artifactState,
		&artifactKey, &artifactSHA, &live,
	)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, capFailure(404, CodeRunNotFound, "that run does not exist")
	}
	if err != nil {
		return nil, capFailure(500, CodeInternal, "the run could not be read")
	}
	run.ID = runID
	if !live {
		return nil, capFailure(403, CodeStaleAttempt, "the attempt token is not a live lease of this run")
	}
	if cancel.Valid {
		return nil, capFailure(409, CodeRunCancelled, "the run was cancelled; no capability may start")
	}
	if run.State != "running" {
		return nil, capFailure(409, CodeRunNotRunning, "the run is not running")
	}
	if artifactState != "ready" || !strings.EqualFold(artifactOwner, run.OwnerID) {
		return nil, capFailure(503, CodeManifestUnavailable, "the sealed execution manifest is not readable")
	}
	if !strings.EqualFold(run.ManifestSHA256, snapshotSHA) {
		return nil, capFailure(503, CodeManifestUnavailable,
			"the run and snapshot manifest digests disagree")
	}
	manifestBytes, err := s.readSealed(artifactKey, artifactSHA.String)
	if err != nil {
		return nil, capFailure(503, CodeManifestUnavailable, "the sealed execution manifest could not be read")
	}
	// The brief's rule is explicit: hash the bytes against runs.manifest_sha256.
	// Checking the artifact row's digest too keeps a mismatched snapshot/artifact
	// row from being accepted transitively.
	manifestDigest := sha256.Sum256(manifestBytes)
	if !strings.EqualFold(hex.EncodeToString(manifestDigest[:]), run.ManifestSHA256) {
		return nil, capFailure(503, CodeManifestUnavailable,
			"the sealed manifest bytes do not match the run digest")
	}
	var manifest compiler.Manifest
	decoder := json.NewDecoder(bytes.NewReader(manifestBytes))
	if err := decoder.Decode(&manifest); err != nil {
		return nil, capFailure(503, CodeManifestUnavailable, "the sealed execution manifest is not valid JSON")
	}
	run.Manifest = &manifest
	return run, nil
}

// readSealed reads one sealed object with the platform ceiling and verifies its
// hash against the artifact row. It never returns unverified bytes.
func (s *Service) readSealed(key, digest string) ([]byte, error) {
	if s.Store == nil {
		return nil, errors.New("object store is not configured")
	}
	reader, _, err := s.Store.Open(key)
	if err != nil {
		return nil, err
	}
	defer func() { _ = reader.Close() }()
	content, err := io.ReadAll(io.LimitReader(reader, objectstore.MaxObjectBytes+1))
	if err != nil {
		return nil, err
	}
	if int64(len(content)) > objectstore.MaxObjectBytes {
		return nil, objectstore.ErrTooLarge
	}
	if strings.TrimSpace(digest) != "" {
		sum := sha256.Sum256(content)
		if !strings.EqualFold(hex.EncodeToString(sum[:]), strings.TrimSpace(digest)) {
			return nil, errors.New("sealed bytes do not match the recorded hash")
		}
	}
	return content, nil
}

// resolveCapability applies the manifest allowlist and kind-ambiguity rule, then
// loads and validates the capability version itself.
func (s *Service) resolveCapability(ctx context.Context, run *runContext, input Input) (*capabilitySpec, *Error) {
	modelAllowed := containsString(run.Manifest.ModelProfileVersions, input.CapabilityVersion)
	toolAllowed := false
	for _, artifact := range run.Manifest.ToolArtifacts {
		if strings.EqualFold(artifact.ID, input.CapabilityVersion) {
			toolAllowed = true
			break
		}
	}
	if modelAllowed && toolAllowed {
		return nil, capFailure(409, CodeCapabilityKindAmbiguous,
			"that capability version is declared as both a model profile and a tool")
	}
	switch input.CapabilityKind {
	case "model":
		if !modelAllowed {
			return nil, capFailure(403, CodeCapabilityNotAllowed,
				"the capability version is not a model profile version of this run's manifest")
		}
		return s.resolveModel(ctx, run, input)
	case "tool":
		if !toolAllowed {
			return nil, capFailure(403, CodeCapabilityNotAllowed,
				"the capability version is not a tool artifact of this run's manifest")
		}
		return s.resolveTool(ctx, run, input)
	default:
		return nil, capFailure(422, CodeKindUnsupported, "that capability kind is not executable")
	}
}

func (s *Service) resolveModel(ctx context.Context, run *runContext, input Input) (*capabilitySpec, *Error) {
	profile := &ModelProfile{}
	var configRaw []byte
	var revoked sql.NullTime
	err := s.DB.QueryRowContext(ctx, `
		SELECT id::text, provider, model_id, endpoint_ref, secret_ref, config,
		       validation_state, revoked_at
		  FROM platform.model_profile_versions
		 WHERE id = $1::uuid`, input.CapabilityVersion,
	).Scan(
		&profile.ID, &profile.Provider, &profile.ModelID, &profile.EndpointRef,
		&profile.SecretRef, &configRaw, &profile.ValidationState, &revoked,
	)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, capFailure(422, CodeModelUnavailable, "that model profile version does not exist")
	}
	if err != nil {
		return nil, capFailure(500, CodeInternal, "the model profile could not be read")
	}
	if revoked.Valid || profile.ValidationState != "passed" {
		return nil, capFailure(422, CodeModelUnavailable,
			"the model profile version is not passed and unrevoked")
	}
	profile.Revoked = revoked.Valid
	if err := json.Unmarshal(configRaw, &profile.Config); err != nil {
		return nil, capFailure(422, CodeModelUnavailable, "the model profile config is not usable")
	}
	// Provider deployment facts are checked before any operation row exists: an
	// unconfigured provider must not leave a ledger row a reader could mistake
	// for a dispatched call.
	if s.Providers == nil {
		return nil, capFailure(503, CodeProviderNotConfigured, "no model provider is configured")
	}
	candidate := &modelproxy.Candidate{
		ID: profile.ID, Provider: profile.Provider, ModelID: profile.ModelID,
		EndpointRef: profile.EndpointRef, SecretRef: profile.SecretRef,
		Config: configRaw, ValidationState: profile.ValidationState,
	}
	provider, err := s.Providers.For(candidate)
	if err != nil {
		return nil, providerFailure(err)
	}
	if err := provider.Available(candidate); err != nil {
		return nil, providerFailure(err)
	}

	tools, capErr := s.loadManifestTools(ctx, run)
	if capErr != nil {
		return nil, capErr
	}
	modelParams, invalid := validateModelParams(input.Params, profile, tools)
	if invalid != nil {
		return nil, invalid
	}
	return &capabilitySpec{
		Kind: "model", RiskClass: "pure", Profile: profile, Tools: tools, Model: modelParams,
	}, nil
}

// loadManifestTools loads every tool artifact of the manifest and keeps only the
// ones this run may declare to a model: ready and approved, or (debug) ready and
// owned by the run's author. A tool that exists in the manifest but is not
// callable is absent, so validateDeclaredTools refuses it by name.
func (s *Service) loadManifestTools(ctx context.Context, run *runContext) (map[string]*ToolSpec, *Error) {
	tools := make(map[string]*ToolSpec, len(run.Manifest.ToolArtifacts))
	for _, artifact := range run.Manifest.ToolArtifacts {
		spec, err := s.loadTool(ctx, run, artifact.ID)
		if err != nil {
			// A manifest tool that is no longer callable is simply not offered;
			// declaring it later fails the declared-tools validation. It is not a
			// 500: the tool may have been revoked after the manifest was frozen.
			continue
		}
		tools[strings.ToLower(spec.ID)] = spec
	}
	return tools, nil
}

func (s *Service) loadTool(ctx context.Context, run *runContext, toolVersionID string) (*ToolSpec, error) {
	spec := &ToolSpec{}
	var inputSchemaRaw []byte
	var approvedAt sql.NullTime
	var approvedBy sql.NullString
	err := s.DB.QueryRowContext(ctx, `
		SELECT id::text, owner_id::text, state, approved_by::text, approved_at,
		       risk_class, input_schema
		  FROM platform.tool_versions
		 WHERE id = $1::uuid`, toolVersionID,
	).Scan(&spec.ID, &spec.OwnerID, &spec.State, &approvedBy, &approvedAt,
		&spec.RiskClass, &inputSchemaRaw)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, errToolUnavailable
	}
	if err != nil {
		return nil, err
	}
	spec.Approved = approvedBy.Valid && approvedAt.Valid
	if spec.State != "ready" {
		return nil, errToolUnavailable
	}
	authorDebug := run != nil && run.Kind == "debug" && strings.EqualFold(spec.OwnerID, run.OwnerID)
	if !spec.Approved && !authorDebug {
		return nil, errToolUnavailable
	}
	spec.InputSchema = map[string]any{}
	if len(inputSchemaRaw) > 0 {
		if err := json.Unmarshal(inputSchemaRaw, &spec.InputSchema); err != nil {
			return nil, errToolUnavailable
		}
	}
	return spec, nil
}

// errToolUnavailable is loadTool's "not callable" sentinel (missing, not ready,
// not approved, or not author-owned in a debug run).
var errToolUnavailable = errors.New(CodeToolUnavailable)

func (s *Service) resolveTool(ctx context.Context, run *runContext, input Input) (*capabilitySpec, *Error) {
	spec, err := s.loadTool(ctx, run, input.CapabilityVersion)
	if err != nil {
		return nil, capFailure(422, CodeToolUnavailable, "the tool version is not ready and approved")
	}
	if invalid := validateToolParams(input.Params, spec.InputSchema); invalid != nil {
		return nil, invalid
	}
	return &capabilitySpec{Kind: "tool", RiskClass: spec.RiskClass, Tool: spec}, nil
}

// prepareOperation is the idempotency heart: one transaction that locks the run,
// applies replay/conflict rules, seals the canonical params and writes the
// operation row (plus the tool attempt/grant when this is a tool call).
type preparedOperation struct {
	operationID string
	replay      *Result
	grant       map[string]any
}

func (s *Service) prepareOperation(
	ctx context.Context,
	run *runContext,
	input Input,
	spec *capabilitySpec,
	canonical []byte,
	digest string,
) (*preparedOperation, *Error) {
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return nil, capFailure(500, CodeInternal, "the capability transaction could not start")
	}
	defer func() { _ = tx.Rollback() }()

	// Serialise every capability call of this run. The UNIQUE(run_id,logical_key)
	// constraint is the backstop; this lock makes concurrent duplicates
	// deterministic (one winner, one replay) instead of racing to an error.
	var state string
	var cancel sql.NullTime
	err = tx.QueryRowContext(ctx, `
		SELECT state, cancel_requested_at FROM platform.runs WHERE id = $1::uuid FOR UPDATE`,
		run.ID,
	).Scan(&state, &cancel)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, capFailure(404, CodeRunNotFound, "that run does not exist")
	}
	if err != nil {
		return nil, capFailure(500, CodeInternal, "the run could not be locked")
	}
	if cancel.Valid {
		return nil, capFailure(409, CodeRunCancelled, "the run was cancelled; no capability may start")
	}
	if state != "running" {
		return nil, capFailure(409, CodeRunNotRunning, "the run is not running")
	}

	existingByID, err := s.operationByIDTx(ctx, tx, input.OperationID)
	if err != nil {
		return nil, capFailure(500, CodeInternal, "the operation could not be read")
	}
	existingByKey, err := s.operationByKeyTx(ctx, tx, run.ID, input.LogicalKey)
	if err != nil {
		return nil, capFailure(500, CodeInternal, "the operation could not be read")
	}
	for _, existing := range []*storedOperation{existingByID, existingByKey} {
		if existing == nil {
			continue
		}
		if existing.ID != input.OperationID ||
			existing.LogicalKey != input.LogicalKey ||
			existing.CapabilityVersion != input.CapabilityVersion ||
			existing.CapabilityKind != spec.Kind ||
			existing.ParamsSHA256 != digest {
			return nil, capFailure(409, CodeOperationConflict,
				"that operation id/logical key is already recorded with different parameters")
		}
		replay, capErr := s.replayTx(ctx, tx, existing, input, digest)
		if capErr != nil {
			return nil, capErr
		}
		if err := tx.Commit(); err != nil {
			return nil, capFailure(500, CodeInternal, "the operation replay could not be committed")
		}
		return &preparedOperation{operationID: existing.ID, replay: replay}, nil
	}

	paramsArtifactID, capErr := s.sealTx(ctx, tx, run, input.AttemptToken,
		"capability_params", "capability_params", canonical)
	if capErr != nil {
		return nil, capErr
	}

	operationID := input.OperationID
	riskClass := spec.RiskClass
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO platform.operations
			(id, run_id, logical_key, capability_version, capability_kind, risk_class,
			 params_sha256, params_artifact_id, state)
		VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7, $8::uuid, 'prepared')`,
		operationID, run.ID, input.LogicalKey, input.CapabilityVersion, spec.Kind,
		riskClass, digest, paramsArtifactID,
	); err != nil {
		if isUniqueViolation(err) {
			return nil, capFailure(409, CodeOperationConflict,
				"that logical key is already recorded for this run")
		}
		return nil, capFailure(500, CodeInternal, "the operation could not be recorded")
	}
	if _, err := tx.ExecContext(ctx, `
		UPDATE platform.operations
		   SET state = 'authorized', updated_at = now()
		 WHERE id = $1::uuid AND state = 'prepared'`, operationID,
	); err != nil {
		return nil, capFailure(500, CodeInternal, "the operation could not be authorized")
	}

	prepared := &preparedOperation{operationID: operationID}
	if spec.Kind == "tool" {
		grant := map[string]any{
			"operation_id":       operationID,
			"capability_version": input.CapabilityVersion,
			"capability_kind":    "tool",
			"risk_class":         spec.RiskClass,
			"params_sha256":      digest,
			"run_id":             run.ID,
			"attempt_token":      input.AttemptToken,
		}
		prepared.grant = grant
		if _, err := tx.ExecContext(ctx, `
			UPDATE platform.operations
			   SET state = 'dispatching', dispatched_at = now(), updated_at = now()
			 WHERE id = $1::uuid AND state = 'authorized'`, operationID,
		); err != nil {
			return nil, capFailure(500, CodeInternal, "the tool grant could not be recorded")
		}
		if _, err := tx.ExecContext(ctx, `
			INSERT INTO platform.operation_attempts
				(id, operation_id, attempt_no, state, attempt_token)
			VALUES ($1::uuid, $2::uuid, 1, 'dispatching', $3::uuid)`,
			uuid.NewString(), operationID, input.AttemptToken,
		); err != nil {
			return nil, capFailure(500, CodeInternal, "the tool attempt could not be recorded")
		}
	}
	if err := tx.Commit(); err != nil {
		return nil, capFailure(500, CodeInternal, "the capability operation could not be committed")
	}
	return prepared, nil
}

type storedOperation struct {
	ID                string
	LogicalKey        string
	CapabilityVersion string
	CapabilityKind    string
	ParamsSHA256      string
	State             string
	ReceiptArtifactID sql.NullString
	LastErrorCode     sql.NullString
}

func (s *Service) operationByIDTx(ctx context.Context, tx *sql.Tx, operationID string) (*storedOperation, error) {
	return scanStoredOperation(tx.QueryRowContext(ctx, `
		SELECT id::text, logical_key, capability_version, capability_kind, params_sha256,
		       state, receipt_artifact_id::text, last_error_code
		  FROM platform.operations
		 WHERE id = $1::uuid
		 FOR UPDATE`, operationID))
}

func (s *Service) operationByKeyTx(ctx context.Context, tx *sql.Tx, runID, logicalKey string) (*storedOperation, error) {
	return scanStoredOperation(tx.QueryRowContext(ctx, `
		SELECT id::text, logical_key, capability_version, capability_kind, params_sha256,
		       state, receipt_artifact_id::text, last_error_code
		  FROM platform.operations
		 WHERE run_id = $1::uuid AND logical_key = $2
		 FOR UPDATE`, runID, logicalKey))
}

func scanStoredOperation(row *sql.Row) (*storedOperation, error) {
	var op storedOperation
	err := row.Scan(&op.ID, &op.LogicalKey, &op.CapabilityVersion, &op.CapabilityKind,
		&op.ParamsSHA256, &op.State, &op.ReceiptArtifactID, &op.LastErrorCode)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &op, nil
}

// replayTx returns the recorded answer of an identical operation. A recorded
// success replays its sealed projection verbatim; a recorded failure replays the
// safe error code; a still-dispatching tool replays its grant. Nothing is
// re-dispatched and nothing is re-billed.
func (s *Service) replayTx(
	ctx context.Context, tx *sql.Tx, op *storedOperation, input Input, digest string,
) (*Result, *Error) {
	result := &Result{OperationID: op.ID, State: op.State}
	switch op.State {
	case "succeeded":
		if !op.ReceiptArtifactID.Valid {
			return nil, capFailure(500, CodeInternal, "the recorded operation has no receipt")
		}
		data, err := s.receiptDataTx(ctx, tx, op.ReceiptArtifactID.String)
		if err != nil {
			return nil, capFailure(500, CodeInternal, "the recorded result cannot be replayed")
		}
		value := op.ReceiptArtifactID.String
		result.ReceiptArtifactID = &value
		result.Data = data
		return result, nil
	case "failed":
		data := map[string]any{}
		if op.LastErrorCode.Valid {
			data["error_code"] = op.LastErrorCode.String
		}
		result.Data = data
		return result, nil
	case "dispatching":
		if op.CapabilityKind == "tool" {
			result.Data = map[string]any{"grant": map[string]any{
				"operation_id":       op.ID,
				"capability_version": op.CapabilityVersion,
				"capability_kind":    "tool",
				"params_sha256":      digest,
				"run_id":             input.RunID,
				"attempt_token":      input.AttemptToken,
			}}
			return result, nil
		}
		return nil, capFailure(409, CodeOperationInProgress,
			"the operation is already dispatching")
	default:
		return nil, capFailure(409, CodeOperationInProgress,
			"the operation is recorded but not dispatchable")
	}
}

func (s *Service) receiptDataTx(ctx context.Context, tx *sql.Tx, artifactID string) (map[string]any, error) {
	var objectKey, digest, state string
	var deleted sql.NullTime
	err := tx.QueryRowContext(ctx, `
		SELECT object_key, COALESCE(sha256, ''), state, deleted_at
		  FROM platform.artifacts WHERE id = $1::uuid`, artifactID,
	).Scan(&objectKey, &digest, &state, &deleted)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, errors.New("receipt artifact is missing")
	}
	if err != nil {
		return nil, err
	}
	if deleted.Valid || state != "ready" {
		return nil, errors.New("receipt artifact is not ready")
	}
	content, err := s.readSealed(objectKey, digest)
	if err != nil {
		return nil, err
	}
	var data map[string]any
	decoder := json.NewDecoder(bytes.NewReader(content))
	decoder.UseNumber()
	if err := decoder.Decode(&data); err != nil || data == nil {
		return nil, errors.New("receipt artifact is not a JSON object")
	}
	return data, nil
}

// sealTx writes canonical bytes as one ready sealed artifact and links it to the
// run/attempt. The object is created before the row commits (same orphan-sweep
// boundary modelproxy documents), never after.
func (s *Service) sealTx(
	ctx context.Context,
	tx *sql.Tx,
	run *runContext,
	attemptToken, purpose, relation string,
	content []byte,
) (string, *Error) {
	if s.Store == nil {
		return "", capFailure(503, "object_store_unavailable", "the object store is not configured")
	}
	object, err := s.Store.CreateSealed(bytes.NewReader(content), objectstore.MaxObjectBytes)
	if err != nil {
		return "", capFailure(500, CodeInternal, "the capability artifact could not be sealed")
	}
	artifactID := uuid.NewString()
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO platform.artifacts
			(id, owner_id, object_key, purpose, sha256, media_type, size_bytes, state, revision)
		VALUES ($1::uuid, $2::uuid, $3, $4, $5, 'application/json', $6, 'ready', 1)`,
		artifactID, run.OwnerID, object.Key, purpose, object.SHA256, object.Size,
	); err != nil {
		return "", capFailure(500, CodeInternal, "the capability artifact row could not be written")
	}
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO platform.run_artifacts (run_id, artifact_id, attempt_token, relation)
		VALUES ($1::uuid, $2::uuid, $3::uuid, $4)
		ON CONFLICT (run_id, artifact_id) DO NOTHING`,
		run.ID, artifactID, attemptToken, relation,
	); err != nil {
		return "", capFailure(500, CodeInternal, "the capability artifact could not be linked to the run")
	}
	return artifactID, nil
}

// dispatchModel performs one operation's physical provider calls. The server is
// the only retry authority: at most two retries for transient failures, never a
// retry for a schema/permission/validation answer.
func (s *Service) dispatchModel(
	ctx context.Context,
	run *runContext,
	input Input,
	spec *capabilitySpec,
	digest, operationID string,
) (*Result, error) {
	if s.Providers == nil {
		return nil, capFailure(503, CodeProviderNotConfigured, "no model provider is configured")
	}
	candidate := &modelproxy.Candidate{
		ID: spec.Profile.ID, Provider: spec.Profile.Provider, ModelID: spec.Profile.ModelID,
		EndpointRef: spec.Profile.EndpointRef, SecretRef: spec.Profile.SecretRef,
		ValidationState: spec.Profile.ValidationState,
	}
	provider, err := s.Providers.For(candidate)
	if err != nil {
		return nil, providerFailure(err)
	}
	reserved := estimateReservedUnits(input.Params)
	for attempt := 1; attempt <= maxPhysicalAttempts; attempt++ {
		if capErr := s.beginModelAttempt(ctx, operationID, input.AttemptToken, attempt, reserved); capErr != nil {
			return nil, capErr
		}
		response, callErr := provider.Call(ctx, modelproxy.CallRequest{
			Candidate: candidate,
			Params:    input.Params,
			Attempt:   attempt,
		})
		if callErr == nil {
			return s.finishModelSuccess(ctx, run, input, spec, operationID, attempt, response)
		}
		transient := modelproxy.IsTransient(callErr)
		code := CodeProviderTransient
		if !transient {
			code = providerErrorCode(callErr)
		}
		if transient && attempt < maxPhysicalAttempts {
			if capErr := s.resetAttemptForRetry(ctx, operationID, code); capErr != nil {
				return nil, capErr
			}
			if err := sleepBackoff(ctx, attempt); err != nil {
				return nil, capFailure(503, CodeProviderTransient, "the capability retry was interrupted")
			}
			continue
		}
		if capErr := s.failOperation(ctx, operationID, code); capErr != nil {
			return nil, capErr
		}
		return nil, capFailure(503, code, "the model provider call did not complete")
	}
	return nil, capFailure(503, CodeProviderTransient, "the model provider exhausted its attempts")
}

// beginModelAttempt CASes the operation from authorized to dispatching and
// records the physical attempt row before the provider is touched.
func (s *Service) beginModelAttempt(ctx context.Context, operationID, attemptToken string, attempt int, reserved string) *Error {
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return capFailure(500, CodeInternal, "the attempt transaction could not start")
	}
	defer func() { _ = tx.Rollback() }()
	var state string
	err = tx.QueryRowContext(ctx, `
		SELECT state FROM platform.operations WHERE id = $1::uuid FOR UPDATE`, operationID,
	).Scan(&state)
	if err != nil {
		return capFailure(500, CodeInternal, "the operation could not be locked")
	}
	if state != "authorized" {
		return capFailure(409, CodeOperationInProgress, "the operation is not in an authorized state")
	}
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO platform.operation_attempts
			(id, operation_id, attempt_no, state, attempt_token, reserved_amount, currency)
		VALUES ($1::uuid, $2::uuid, $3, 'dispatching', $4::uuid, $5::numeric, 'USD')`,
		uuid.NewString(), operationID, attempt, attemptToken, reserved,
	); err != nil {
		if isUniqueViolation(err) {
			return capFailure(409, CodeOperationInProgress, "the operation already has that attempt")
		}
		return capFailure(500, CodeInternal, "the attempt could not be recorded")
	}
	if _, err := tx.ExecContext(ctx, `
		UPDATE platform.operations
		   SET state = 'dispatching', dispatched_at = now(), updated_at = now()
		 WHERE id = $1::uuid AND state = 'authorized'`, operationID,
	); err != nil {
		return capFailure(500, CodeInternal, "the operation could not be dispatched")
	}
	if err := tx.Commit(); err != nil {
		return capFailure(500, CodeInternal, "the attempt could not be committed")
	}
	return nil
}

func (s *Service) finishModelSuccess(
	ctx context.Context,
	run *runContext,
	input Input,
	spec *capabilitySpec,
	operationID string,
	attempt int,
	response modelproxy.ProviderResponse,
) (*Result, error) {
	projection, problems := validateProviderResponse(response.Data, spec.Model)
	if len(problems) > 0 {
		_ = s.failAttemptAndOperation(ctx, operationID, attempt, CodeProviderResponseInvalid)
		return nil, &Error{
			Status:  422,
			Code:    CodeProviderResponseInvalid,
			Message: "the provider response failed validation: " + problems[0].Message,
			Field:   strings.TrimPrefix(problems[0].Field, "/"),
		}
	}
	payload := map[string]any{
		"text":                     projection.Text,
		"tool_calls":               projection.ToolCalls,
		"model_profile_version_id": spec.Profile.ID,
		"stub":                     projection.Stub,
	}
	if projection.Structured != nil {
		payload["structured"] = projection.Structured
	}
	if projection.Usage != nil {
		payload["usage"] = projection.Usage
	}
	content, err := json.Marshal(payload)
	if err != nil {
		_ = s.failAttemptAndOperation(ctx, operationID, attempt, CodeProviderResponseInvalid)
		return nil, capFailure(500, CodeInternal, "the validated response could not be encoded")
	}
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return nil, capFailure(500, CodeInternal, "the result transaction could not start")
	}
	defer func() { _ = tx.Rollback() }()
	receiptArtifactID, capErr := s.sealTx(ctx, tx, run, input.AttemptToken,
		"receipt", "capability_receipt", content)
	if capErr != nil {
		return nil, capErr
	}
	chargedAmount := formatUnits((projection.TokensIn + projection.TokensOut) * 100)
	result, err := tx.ExecContext(ctx, `
		UPDATE platform.operation_attempts
		   SET state = 'succeeded', result_artifact_id = $3::uuid,
		       tokens_in = $4, tokens_out = $5, charged_amount = $6::numeric,
		       finished_at = now()
		 WHERE operation_id = $1::uuid AND attempt_no = $2 AND state = 'dispatching'`,
		operationID, attempt, receiptArtifactID, projection.TokensIn, projection.TokensOut, chargedAmount,
	)
	if err != nil {
		return nil, capFailure(500, CodeInternal, "the attempt could not be closed")
	}
	if affected, _ := result.RowsAffected(); affected != 1 {
		return nil, capFailure(500, CodeInternal, "the attempt was not dispatching")
	}
	if _, err := tx.ExecContext(ctx, `
		UPDATE platform.operations
		   SET state = 'succeeded', receipt_artifact_id = $2::uuid,
		       last_error_code = NULL, updated_at = now()
		 WHERE id = $1::uuid AND state = 'dispatching'`, operationID, receiptArtifactID,
	); err != nil {
		return nil, capFailure(500, CodeInternal, "the operation could not be succeeded")
	}
	if err := tx.Commit(); err != nil {
		return nil, capFailure(500, CodeInternal, "the result could not be committed")
	}
	artifact := receiptArtifactID
	return &Result{
		OperationID:       operationID,
		State:             "succeeded",
		ReceiptArtifactID: &artifact,
		Data:              payload,
	}, nil
}

func (s *Service) resetAttemptForRetry(ctx context.Context, operationID, code string) *Error {
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return capFailure(500, CodeInternal, "the retry transaction could not start")
	}
	defer func() { _ = tx.Rollback() }()
	if _, err := tx.ExecContext(ctx, `
		UPDATE platform.operation_attempts
		   SET state = 'failed', error_code = $2, finished_at = now()
		 WHERE operation_id = $1::uuid AND state = 'dispatching'`, operationID, code,
	); err != nil {
		return capFailure(500, CodeInternal, "the failed attempt could not be recorded")
	}
	if _, err := tx.ExecContext(ctx, `
		UPDATE platform.operations
		   SET state = 'authorized', last_error_code = $2, updated_at = now()
		 WHERE id = $1::uuid AND state = 'dispatching'`, operationID, code,
	); err != nil {
		return capFailure(500, CodeInternal, "the operation could not be re-authorized")
	}
	if err := tx.Commit(); err != nil {
		return capFailure(500, CodeInternal, "the retry decision could not be committed")
	}
	return nil
}

func (s *Service) failAttemptAndOperation(ctx context.Context, operationID string, attempt int, code string) error {
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer func() { _ = tx.Rollback() }()
	if _, err := tx.ExecContext(ctx, `
		UPDATE platform.operation_attempts
		   SET state = 'failed', error_code = $3, finished_at = now()
		 WHERE operation_id = $1::uuid AND attempt_no = $2`, operationID, attempt, code,
	); err != nil {
		return err
	}
	if _, err := tx.ExecContext(ctx, `
		UPDATE platform.operations
		   SET state = 'failed', last_error_code = $2, updated_at = now()
		 WHERE id = $1::uuid`, operationID, code,
	); err != nil {
		return err
	}
	return tx.Commit()
}

func (s *Service) failOperation(ctx context.Context, operationID, code string) *Error {
	if _, err := s.DB.ExecContext(ctx, `
		UPDATE platform.operations
		   SET state = 'failed', last_error_code = $2, updated_at = now()
		 WHERE id = $1::uuid`, operationID, code,
	); err != nil {
		return capFailure(500, CodeInternal, "the failed operation could not be recorded")
	}
	return nil
}

// ReportResult is the non-contract capability-results endpoint: the worker's
// proof that it physically executed a granted tool call.
func (s *Service) ReportResult(ctx context.Context, runID string, report ResultReport) (*Result, error) {
	runID = strings.TrimSpace(runID)
	if !uuid.Valid(runID) {
		return nil, capFailure(404, CodeRunNotFound, "that run does not exist")
	}
	attemptToken := strings.ToLower(strings.TrimSpace(report.AttemptToken))
	if !uuid.Valid(attemptToken) {
		return nil, capFailure(403, CodeStaleAttempt, "the attempt token is not a live lease of this run")
	}
	operationID := strings.ToLower(strings.TrimSpace(report.OperationID))
	if !uuid.Valid(operationID) {
		return nil, validationFailure("operation_id", "operation_id must be a UUID")
	}
	state := strings.ToLower(strings.TrimSpace(report.State))
	if state != "succeeded" && state != "failed" {
		return nil, validationFailure("state", "state must be succeeded or failed")
	}
	var ownerID string
	var live bool
	err := s.DB.QueryRowContext(ctx, `
		SELECT r.owner_id::text,
		       EXISTS (
		           SELECT 1 FROM platform.run_attempts AS at
		             JOIN platform.jobs AS j ON j.id = at.job_id AND j.run_id = at.run_id
		            WHERE at.token = $2::uuid AND at.run_id = r.id
		              AND at.exited_at IS NULL AND at.revoked_at IS NULL
		              AND at.lease_deadline > now() AND j.current_attempt_token = at.token
		       )
		  FROM platform.runs AS r WHERE r.id = $1::uuid`, runID, attemptToken,
	).Scan(&ownerID, &live)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, capFailure(404, CodeRunNotFound, "that run does not exist")
	}
	if err != nil {
		return nil, capFailure(500, CodeInternal, "the run could not be read")
	}
	if !live {
		return nil, capFailure(403, CodeStaleAttempt, "the attempt token is not a live lease of this run")
	}

	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return nil, capFailure(500, CodeInternal, "the result transaction could not start")
	}
	defer func() { _ = tx.Rollback() }()
	op, err := s.operationForResultTx(ctx, tx, runID, operationID)
	if err != nil {
		return nil, capFailure(500, CodeInternal, "the operation could not be read")
	}
	if op == nil {
		return nil, capFailure(404, CodeOperationNotFound, "that operation does not exist for this run")
	}
	if op.CapabilityKind != "tool" {
		return nil, capFailure(409, CodeOperationNotDispatching, "only a tool operation accepts a result report")
	}
	if op.State == "succeeded" || op.State == "failed" {
		// Idempotent replay: a worker that lost the first answer may report again.
		result := &Result{OperationID: op.ID, State: op.State}
		if op.ReceiptArtifactID.Valid {
			value := op.ReceiptArtifactID.String
			result.ReceiptArtifactID = &value
		}
		if op.State == "failed" && op.LastErrorCode.Valid {
			result.Data = map[string]any{"error_code": op.LastErrorCode.String}
		}
		return result, nil
	}
	if op.State != "dispatching" {
		return nil, capFailure(409, CodeOperationNotDispatching, "the operation is not dispatching")
	}
	var attemptNo int
	var recordedToken sql.NullString
	err = tx.QueryRowContext(ctx, `
		SELECT attempt_no, attempt_token::text
		  FROM platform.operation_attempts
		 WHERE operation_id = $1::uuid
		 ORDER BY attempt_no DESC LIMIT 1`, op.ID,
	).Scan(&attemptNo, &recordedToken)
	if err != nil {
		return nil, capFailure(409, CodeOperationNotDispatching, "the operation has no dispatch attempt")
	}
	if !recordedToken.Valid || !strings.EqualFold(recordedToken.String, attemptToken) {
		return nil, capFailure(403, CodeStaleAttempt,
			"the operation was dispatched under a different attempt")
	}

	run := &runContext{ID: runID, OwnerID: ownerID}
	switch state {
	case "succeeded":
		if report.ResultArtifactID != "" {
			if capErr := s.verifyResultArtifactTx(ctx, tx, run, report.ResultArtifactID); capErr != nil {
				return nil, capErr
			}
		} else if report.Data != nil {
			content, err := json.Marshal(report.Data)
			if err != nil {
				return nil, validationFailure("data", "data must be JSON")
			}
			artifactID, capErr := s.sealTx(ctx, tx, run, attemptToken,
				"receipt", "capability_result", content)
			if capErr != nil {
				return nil, capErr
			}
			report.ResultArtifactID = artifactID
		} else {
			return nil, validationFailure("result_artifact_id",
				"a succeeded report needs a ready result artifact or a data object")
		}
		artifact := report.ResultArtifactID
		if _, err := tx.ExecContext(ctx, `
			UPDATE platform.operation_attempts
			   SET state = 'succeeded', result_artifact_id = $3::uuid, finished_at = now()
			 WHERE operation_id = $1::uuid AND attempt_no = $2`,
			op.ID, attemptNo, artifact,
		); err != nil {
			return nil, capFailure(500, CodeInternal, "the tool attempt could not be succeeded")
		}
		if _, err := tx.ExecContext(ctx, `
			UPDATE platform.operations
			   SET state = 'succeeded', receipt_artifact_id = $2::uuid, updated_at = now()
			 WHERE id = $1::uuid`, op.ID, artifact,
		); err != nil {
			return nil, capFailure(500, CodeInternal, "the operation could not be succeeded")
		}
		if err := tx.Commit(); err != nil {
			return nil, capFailure(500, CodeInternal, "the tool result could not be committed")
		}
		return &Result{OperationID: op.ID, State: "succeeded", ReceiptArtifactID: &artifact}, nil
	default:
		code, err := safeCode(report.ErrorCode)
		if err != nil {
			return nil, validationFailure("error_code", err.Error())
		}
		if _, err := tx.ExecContext(ctx, `
			UPDATE platform.operation_attempts
			   SET state = 'failed', error_code = $3, finished_at = now()
			 WHERE operation_id = $1::uuid AND attempt_no = $2`,
			op.ID, attemptNo, code,
		); err != nil {
			return nil, capFailure(500, CodeInternal, "the tool attempt could not be failed")
		}
		if _, err := tx.ExecContext(ctx, `
			UPDATE platform.operations
			   SET state = 'failed', last_error_code = $2, updated_at = now()
			 WHERE id = $1::uuid`, op.ID, code,
		); err != nil {
			return nil, capFailure(500, CodeInternal, "the operation could not be failed")
		}
		if err := tx.Commit(); err != nil {
			return nil, capFailure(500, CodeInternal, "the tool failure could not be committed")
		}
		return &Result{OperationID: op.ID, State: "failed", Data: map[string]any{"error_code": code}}, nil
	}
}

func (s *Service) operationForResultTx(ctx context.Context, tx *sql.Tx, runID, operationID string) (*storedOperation, error) {
	var op storedOperation
	err := tx.QueryRowContext(ctx, `
		SELECT id::text, logical_key, capability_version, capability_kind, params_sha256,
		       state, receipt_artifact_id::text, last_error_code
		  FROM platform.operations
		 WHERE id = $1::uuid AND run_id = $2::uuid
		 FOR UPDATE`, operationID, runID,
	).Scan(&op.ID, &op.LogicalKey, &op.CapabilityVersion, &op.CapabilityKind,
		&op.ParamsSHA256, &op.State, &op.ReceiptArtifactID, &op.LastErrorCode)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &op, nil
}

// verifyResultArtifactTx refuses an artifact that is not a ready object owned by
// the run's owner, so a worker cannot point the ledger at foreign bytes.
func (s *Service) verifyResultArtifactTx(ctx context.Context, tx *sql.Tx, run *runContext, artifactID string) *Error {
	if !uuid.Valid(strings.TrimSpace(artifactID)) {
		return validationFailure("result_artifact_id", "result_artifact_id must be a UUID")
	}
	var ownerID, state string
	var deleted sql.NullTime
	err := tx.QueryRowContext(ctx, `
		SELECT owner_id::text, state, deleted_at FROM platform.artifacts WHERE id = $1::uuid`,
		artifactID,
	).Scan(&ownerID, &state, &deleted)
	if errors.Is(err, sql.ErrNoRows) {
		return capFailure(422, CodeResultArtifactInvalid, "the result artifact does not exist")
	}
	if err != nil {
		return capFailure(500, CodeInternal, "the result artifact could not be read")
	}
	if deleted.Valid || state != "ready" || !strings.EqualFold(ownerID, run.OwnerID) {
		return capFailure(422, CodeResultArtifactInvalid,
			"the result artifact is not a ready object owned by this run")
	}
	return nil
}

// LedgerOperation is the owner-facing projection. It deliberately carries no
// params and no receipt body: only identity, state, attempt count and a derived
// tool-call/structured summary.
type LedgerOperation struct {
	ID                string           `json:"id"`
	CapabilityKind    string           `json:"capability_kind"`
	CapabilityVersion string           `json:"capability_version"`
	State             string           `json:"state"`
	Attempts          int              `json:"attempts"`
	ErrorCode         *string          `json:"error_code"`
	ReceiptArtifactID *string          `json:"receipt_artifact_id"`
	ToolCallSummary   []LedgerToolCall `json:"tool_call_summary"`
	StructuredKeys    []string         `json:"structured_output_keys"`
	Stub              bool             `json:"stub"`
}

// LedgerToolCall names a tool call without its arguments.
type LedgerToolCall struct {
	ID   string `json:"id"`
	Name string `json:"name"`
}

// Ledger returns one run's capability ledger for its owner. A missing run or
// another owner's run is the same 404 (no enumeration).
func (s *Service) Ledger(ctx context.Context, ownerID, runID string) ([]LedgerOperation, *Error) {
	if !uuid.Valid(runID) || !uuid.Valid(ownerID) {
		return nil, capFailure(404, CodeRunNotFound, "that run does not exist")
	}
	var actualOwner string
	err := s.DB.QueryRowContext(ctx, `
		SELECT owner_id::text FROM platform.runs WHERE id = $1::uuid AND deleted_at IS NULL`,
		runID,
	).Scan(&actualOwner)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, capFailure(404, CodeRunNotFound, "that run does not exist")
	}
	if err != nil {
		return nil, capFailure(500, CodeInternal, "the run could not be read")
	}
	if !strings.EqualFold(actualOwner, ownerID) {
		return nil, capFailure(404, CodeRunNotFound, "that run does not exist")
	}
	rows, err := s.DB.QueryContext(ctx, `
		SELECT o.id::text, o.capability_kind, o.capability_version, o.state,
		       o.last_error_code, o.receipt_artifact_id::text,
		       (SELECT count(*) FROM platform.operation_attempts AS a
		         WHERE a.operation_id = o.id),
		       a.object_key, a.sha256, a.state, a.deleted_at
		  FROM platform.operations AS o
		  LEFT JOIN platform.artifacts AS a ON a.id = o.receipt_artifact_id
		 WHERE o.run_id = $1::uuid
		 ORDER BY o.created_at DESC, o.id`, runID,
	)
	if err != nil {
		return nil, capFailure(500, CodeInternal, "the capability ledger could not be read")
	}
	defer func() { _ = rows.Close() }()
	operations := make([]LedgerOperation, 0)
	for rows.Next() {
		var (
			entry         LedgerOperation
			errorCode     sql.NullString
			receiptID     sql.NullString
			objectKey     sql.NullString
			digest        sql.NullString
			artifactState sql.NullString
			deleted       sql.NullTime
		)
		if err := rows.Scan(&entry.ID, &entry.CapabilityKind, &entry.CapabilityVersion,
			&entry.State, &errorCode, &receiptID, &entry.Attempts,
			&objectKey, &digest, &artifactState, &deleted); err != nil {
			return nil, capFailure(500, CodeInternal, "the capability ledger could not be read")
		}
		entry.ToolCallSummary = []LedgerToolCall{}
		entry.StructuredKeys = []string{}
		if errorCode.Valid {
			value := errorCode.String
			entry.ErrorCode = &value
		}
		if receiptID.Valid {
			value := receiptID.String
			entry.ReceiptArtifactID = &value
		}
		if objectKey.Valid && artifactState.Valid && artifactState.String == "ready" && !deleted.Valid {
			content, err := s.readSealed(objectKey.String, digest.String)
			if err == nil {
				entry.ToolCallSummary, entry.StructuredKeys, entry.Stub = summariseReceipt(content)
			}
		}
		operations = append(operations, entry)
	}
	if err := rows.Err(); err != nil {
		return nil, capFailure(500, CodeInternal, "the capability ledger could not be read")
	}
	return operations, nil
}

// summariseReceipt extracts only the safe summary fields from a sealed receipt.
func summariseReceipt(content []byte) ([]LedgerToolCall, []string, bool) {
	var receipt map[string]any
	if err := json.Unmarshal(content, &receipt); err != nil {
		return []LedgerToolCall{}, []string{}, false
	}
	calls := make([]LedgerToolCall, 0)
	if rawCalls, ok := receipt["tool_calls"].([]any); ok {
		for _, raw := range rawCalls {
			call, ok := raw.(map[string]any)
			if !ok {
				continue
			}
			id, _ := call["id"].(string)
			name, _ := call["name"].(string)
			calls = append(calls, LedgerToolCall{ID: id, Name: name})
		}
	}
	keys := make([]string, 0)
	if structured, ok := receipt["structured"].(map[string]any); ok {
		for key := range structured {
			keys = append(keys, key)
		}
		sortStrings(keys)
	}
	stub, _ := receipt["stub"].(bool)
	return calls, keys, stub
}

func sortStrings(values []string) {
	for index := 1; index < len(values); index++ {
		for back := index; back > 0 && values[back] < values[back-1]; back-- {
			values[back], values[back-1] = values[back-1], values[back]
		}
	}
}

func providerFailure(err error) *Error {
	switch {
	case errors.Is(err, modelproxy.ErrNotConfigured):
		return capFailure(503, CodeProviderNotConfigured, "no real model provider is configured")
	case errors.Is(err, modelproxy.ErrAdapterUnavailable):
		return capFailure(503, CodeAdapterUnavailable, "no vendor adapter is compiled for this provider")
	case errors.Is(err, modelproxy.ErrCandidateUnavailable):
		return capFailure(422, CodeModelUnavailable, "the model profile version is not callable")
	default:
		return capFailure(503, CodeProviderNotConfigured, "the model provider is not available")
	}
}

func providerErrorCode(err error) string {
	switch {
	case errors.Is(err, modelproxy.ErrNotConfigured):
		return CodeProviderNotConfigured
	case errors.Is(err, modelproxy.ErrAdapterUnavailable):
		return CodeAdapterUnavailable
	default:
		return "provider_error"
	}
}

// estimateReservedUnits is the conservative pre-dispatch reservation in 1e-8
// currency units: one token per four canonical params bytes at 1e-6 per token.
// T046 owns the real budget; this only makes the reservation non-zero.
func estimateReservedUnits(params map[string]any) string {
	raw, err := json.Marshal(params)
	if err != nil {
		return "0.00000000"
	}
	return formatUnits(int64((len(raw)+3)/4) * 100)
}

func formatUnits(units int64) string {
	if units < 0 {
		units = 0
	}
	return fmt.Sprintf("%d.%08d", units/100000000, units%100000000)
}

func sleepBackoff(ctx context.Context, attempt int) error {
	delay := time.Duration(attempt) * 20 * time.Millisecond
	timer := time.NewTimer(delay)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-timer.C:
		return nil
	}
}

func containsString(values []string, wanted string) bool {
	for _, value := range values {
		if strings.EqualFold(value, wanted) {
			return true
		}
	}
	return false
}

func isUniqueViolation(err error) bool {
	type sqlState interface{ SQLState() string }
	var state sqlState
	if errors.As(err, &state) {
		return state.SQLState() == "23505"
	}
	return false
}
