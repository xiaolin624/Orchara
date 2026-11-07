package capabilities

import (
	"bytes"
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"strings"
	"time"

	"orchara/platform/internal/approvals"
	"orchara/platform/internal/budget"
	"orchara/platform/internal/compiler"
	"orchara/platform/internal/connectors"
	"orchara/platform/internal/execution"
	"orchara/platform/internal/modelproxy"
	"orchara/platform/internal/objectstore"
	"orchara/platform/internal/providercodes"
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
	// Budget is the F06/T046 decimal budget ledger. It is the single budget
	// authority for every model attempt of the run tree.
	Budget *budget.Ledger
	// Connectors is the F06/T048 versioned connector registry and the guarded
	// egress client. Nil fails closed: no connector can be invoked.
	Connectors *connectors.Service
	// Approvals is the F06/T050 approval writer. Nil fails closed: a write-risk
	// connector operation can never be parked without its server-built proposal.
	Approvals *approvals.Service
	// Unknowns is the F03/T036 unknown-operation bridge writer. F06/T051 uses it
	// to commit the operation's `unknown` state and the run's unresolved-write row
	// in one transaction. Nil fails closed: a connector write with an ambiguous
	// outcome is still marked unknown (so it can never be resent) but cannot park
	// the Run.
	Unknowns UnknownOperationBridge
	// Reconciler is the F03/T036/T051 run-side bridge: it resolves the unknown
	// ledger row and re-drives the one finalizer inside the caller's transaction.
	// Nil fails closed: an unknown write cannot be settled.
	Reconciler ReconciliationBridge
	// ToolExecutor is reserved for T042/T043; nil means worker execution (the
	// only path in this batch).
	ToolExecutor ToolExecutor
}

// UnknownOperationBridge is the T036 recorder seam. internal/execution implements
// it; the connector dispatcher calls the Tx form so both facts commit atomically.
type UnknownOperationBridge interface {
	RecordUnknownOperationTx(
		ctx context.Context, tx *sql.Tx, runID, attemptToken, operationID, reason string,
	) (*execution.UnknownOperation, error)
}

// ReconciliationBridge is the T051 run-side settlement seam. internal/execution
// implements it; the reconcile transaction resolves the unknown row and lets the
// one finalizer decide the Run outcome.
type ReconciliationBridge interface {
	ReconcileUnknownOperationTx(
		ctx context.Context, tx *sql.Tx, input execution.ReconcileResolution,
	) (string, error)
}

// New builds the capability service. Store/Providers may be nil in a process
// that never calls a capability; every operation that needs them fails closed.
func New(db *sql.DB, store *objectstore.Store, providers *modelproxy.Providers) *Service {
	return &Service{DB: db, Store: store, Providers: providers, Budget: budget.New(db)}
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
	BudgetLimit     string
	Currency        string
	ManifestSHA256  string
	Manifest        *compiler.Manifest
	CancelRequested bool
	// SnapshotID/SnapshotSHA256 are the frozen execution snapshot the approval
	// proposal records as its data/source version.
	SnapshotID     string
	SnapshotSHA256 string
}

// capabilitySpec is one resolved capability. Exactly one of Profile/Tool/
// Connector is set.
type capabilitySpec struct {
	Kind          string
	RiskClass     string
	Profile       *ModelProfile
	Tools         map[string]*ToolSpec
	Tool          *ToolSpec
	Model         *ModelParams
	Connector     *connectors.Policy
	ConnectorCall connectors.Call
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
	if prepared.awaitingApproval {
		return connectorApprovalResult(prepared.operationID, spec, prepared.approvalID), nil
	}
	if spec.Kind == "tool" {
		// The grant is the whole answer: the worker executes and reports back.
		return &Result{
			OperationID: prepared.operationID,
			State:       "dispatching",
			Data:        map[string]any{"grant": prepared.grant},
		}, nil
	}
	if spec.Kind == ConnectorKind {
		return s.dispatchConnector(ctx, run, normalized, spec, prepared.operationID)
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
	case "model", "tool", "connector":
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
		disabled      sql.NullTime
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
		       r.budget_limit::text, r.currency,
		       r.manifest_sha256, s.id::text, s.manifest_sha256,
		       s.manifest_artifact_id::text, a.owner_id::text, a.state, a.object_key, a.sha256,
		       p.disabled_at,
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
		  JOIN platform.principals AS p ON p.id = r.owner_id
		 WHERE r.id = $1::uuid`, runID, attemptToken,
	).Scan(
		&run.OwnerID, &run.Kind, &run.State, &cancel, &run.BudgetLimit, &run.Currency,
		&run.ManifestSHA256,
		&snapshotID, &snapshotSHA, &artifactId, &artifactOwner, &artifactState,
		&artifactKey, &artifactSHA, &disabled, &live,
	)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, capFailure(404, CodeRunNotFound, "that run does not exist")
	}
	if err != nil {
		return nil, capFailure(500, CodeInternal, "the run could not be read")
	}
	run.ID = runID
	run.SnapshotID = snapshotID
	run.SnapshotSHA256 = snapshotSHA
	if !live {
		return nil, capFailure(403, CodeStaleAttempt, "the attempt token is not a live lease of this run")
	}
	if disabled.Valid {
		// F06/T050: an approval never grants permission. A principal disabled
		// after approval finds the same refusal on the resumed attempt.
		return nil, capFailure(403, "permission_revoked",
			"the run owner's permission was revoked")
	}
	if cancel.Valid {
		return nil, capFailure(409, CodeRunCancelled, "the run was cancelled; no capability may start")
	}
	if run.State != "running" {
		return nil, capFailure(409, CodeRunNotRunning, "the run is not running")
	}
	// F06/T041 sealed a debug snapshot's manifest artifact as the run's author, so
	// artifact owner and run owner were the same principal. An F08/T074 published
	// Run pins the *version's* manifest artifact, which the publisher sealed
	// (SubmitPublication is owner-only); the run owner is the granted requester.
	// The ownership equality is therefore a debug-run rule. For a published Run the
	// boundary is the immutable version digest, re-proven below: the artifact
	// digest must equal the snapshot digest and the bytes must hash to
	// runs.manifest_sha256, which DispatchVersionTx copied from the version it
	// resolved under the read-grant and revocation checks. Keeping the equality for
	// published Runs made every granted reader's agent/model/tool/connector call
	// fail closed with manifest_unavailable.
	if artifactState != "ready" ||
		(run.Kind != "published" && !strings.EqualFold(artifactOwner, run.OwnerID)) {
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
// loads and validates the capability version itself. A version id declared in
// more than one of the model/tool/connector sets is refused rather than guessed.
func (s *Service) resolveCapability(ctx context.Context, run *runContext, input Input) (*capabilitySpec, *Error) {
	modelAllowed := containsString(run.Manifest.ModelProfileVersions, input.CapabilityVersion)
	toolAllowed := false
	for _, artifact := range run.Manifest.ToolArtifacts {
		if strings.EqualFold(artifact.ID, input.CapabilityVersion) {
			toolAllowed = true
			break
		}
	}
	connectorAllowed := manifestConnectorPolicy(run.Manifest, input.CapabilityVersion) != nil
	memberships := 0
	for _, allowed := range []bool{modelAllowed, toolAllowed, connectorAllowed} {
		if allowed {
			memberships++
		}
	}
	if memberships > 1 {
		return nil, capFailure(409, CodeCapabilityKindAmbiguous,
			"that capability version is declared as more than one capability kind (model, tool, connector)")
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
	case ConnectorKind:
		if !connectorAllowed {
			return nil, capFailure(403, CodeCapabilityNotAllowed,
				"the capability version is not a connector policy version of this run's manifest")
		}
		return s.resolveConnector(ctx, run, input)
	default:
		return nil, capFailure(422, CodeKindUnsupported, "that capability kind is not executable")
	}
}

func (s *Service) resolveModel(ctx context.Context, run *runContext, input Input) (*capabilitySpec, *Error) {
	profile, capErr := s.loadModelProfile(ctx, input.CapabilityVersion)
	if capErr != nil {
		return nil, capErr
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
		Config: profile.ConfigJSON, ValidationState: profile.ValidationState,
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

// loadModelProfile reads one model profile version and refuses a profile that is
// absent, revoked or not passed. The T045 fallback gate reuses it so a fallback
// is held to exactly the readiness rule the primary passed.
func (s *Service) loadModelProfile(ctx context.Context, profileVersionID string) (*ModelProfile, *Error) {
	profile := &ModelProfile{}
	var configRaw []byte
	var revoked sql.NullTime
	err := s.DB.QueryRowContext(ctx, `
		SELECT id::text, provider, model_id, endpoint_ref, secret_ref, config,
		       validation_state, revoked_at
		  FROM platform.model_profile_versions
		 WHERE id = $1::uuid`, profileVersionID,
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
	profile.ConfigJSON = configRaw
	if err := json.Unmarshal(configRaw, &profile.Config); err != nil {
		return nil, capFailure(422, CodeModelUnavailable, "the model profile config is not usable")
	}
	return profile, nil
}

// loadManifestTools loads every tool artifact of the manifest and keeps only the
// ones this run may declare to a model: ready and approved, or (debug) ready and
// owned by the run's author. A tool that exists in the manifest but is not
// callable is absent, so validateDeclaredTools refuses it by name.
//
// F07/T078: the run's frozen `subapp_versions` are callable model tools too. A
// sub-application tool is not a capability operation — the executor maps the
// validated model tool call to the platform's spawnChild endpoint, which
// re-proves the frozen closure, the child version's ACL and the mapping. The
// server only needs the child version's frozen input schema to validate the tool
// call arguments before they reach the executor. A revoked/archived child version
// is not offered, so a model call naming it is refused here.
func (s *Service) loadManifestTools(ctx context.Context, run *runContext) (map[string]*ToolSpec, *Error) {
	tools := make(map[string]*ToolSpec, len(run.Manifest.ToolArtifacts)+len(run.Manifest.SubappVersions))
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
	for version, spec := range s.loadManifestSubapps(ctx, run) {
		if _, clash := tools[version]; !clash {
			tools[version] = spec
		}
	}
	return tools, nil
}

// loadManifestSubapps resolves the frozen sub-application closure into the tool
// specs a model may declare. Only a version whose own frozen definition carries
// an input schema is offered; revocation/archival after publication removes it,
// never a stale grant.
func (s *Service) loadManifestSubapps(ctx context.Context, run *runContext) map[string]*ToolSpec {
	specs := make(map[string]*ToolSpec, len(run.Manifest.SubappVersions))
	for _, rawID := range run.Manifest.SubappVersions {
		version := strings.ToLower(strings.TrimSpace(rawID))
		if !uuid.Valid(version) {
			continue
		}
		var (
			schemaRaw []byte
			revoked   sql.NullTime
			archived  sql.NullTime
		)
		err := s.DB.QueryRowContext(ctx, `
			SELECT v.manifest -> 'definition' -> 'input_schema', v.revoked_at, a.archived_at
			  FROM platform.application_versions AS v
			  JOIN platform.applications AS a ON a.id = v.application_id
			 WHERE v.id = $1::uuid`, version,
		).Scan(&schemaRaw, &revoked, &archived)
		if err != nil || revoked.Valid || archived.Valid || len(schemaRaw) == 0 {
			continue
		}
		var schema map[string]any
		if err := json.Unmarshal(schemaRaw, &schema); err != nil || schema == nil {
			continue
		}
		specs[version] = &ToolSpec{ID: version, State: "subapp", InputSchema: schema}
	}
	return specs
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
	operationID      string
	replay           *Result
	grant            map[string]any
	awaitingApproval bool
	approvalID       string
	// proceed means a parked write operation whose approval was granted: the
	// caller must dispatch now. The authorized -> dispatching CAS in
	// beginConnectorAttempt is still the single dispatch gate.
	proceed bool
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
		if existing.State == "authorized" && spec.Kind == ConnectorKind && spec.RiskClass == "write" {
			// F06/T050 resume: the human approved the exact params hash and the
			// decision flipped the parked operation to authorized. Dispatch still
			// goes through beginConnectorAttempt's authorized -> dispatching CAS,
			// so a second concurrent resume cannot write twice.
			//
			// F06/review2 P1-4: the approval is a time-bounded grant, not a
			// permanent capability. Re-read it here, on the same transaction and
			// with the database clock, before allowing the CAS: an approval that
			// expired between the decision and the resume must never write.
			commitRefusal, approvalErr := s.resumeApprovedWriteTx(ctx, tx, existing.ID, digest)
			if approvalErr != nil {
				if commitRefusal {
					if err := tx.Commit(); err != nil {
						return nil, capFailure(500, CodeInternal, "the approval refusal could not be committed")
					}
				}
				return nil, approvalErr
			}
			if err := tx.Commit(); err != nil {
				return nil, capFailure(500, CodeInternal, "the approval resume could not be committed")
			}
			return &preparedOperation{operationID: existing.ID, proceed: true}, nil
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
			 params_sha256, params_artifact_id, state, reconciliation_limitation)
		VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7, $8::uuid, 'prepared', $9)`,
		operationID, run.ID, input.LogicalKey, input.CapabilityVersion, spec.Kind,
		riskClass, digest, paramsArtifactID, connectorReconciliationLimitation(spec),
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
	if s.connectorAwaitingApproval(spec) {
		// F06/T050: the capability proxy, not the worker, builds the proposal and
		// parks the operation. Both rows are written in this one transaction.
		if s.Approvals == nil {
			return nil, capFailure(503, "approval_unavailable",
				"no approval service is configured; a write operation cannot be parked")
		}
		approval, approvalErr := s.Approvals.PrepareRecordTx(ctx, tx, approvals.PrepareRecord{
			RunID:             run.ID,
			OperationID:       operationID,
			LogicalKey:        input.LogicalKey,
			AttemptToken:      input.AttemptToken,
			OwnerID:           run.OwnerID,
			ParamsSHA256:      digest,
			Params:            json.RawMessage(canonical),
			CapabilityKind:    spec.Kind,
			CapabilityVersion: input.CapabilityVersion,
			RiskClass:         spec.RiskClass,
			Target:            connectorApprovalTarget(spec),
			DataDestination:   spec.Connector.Origin,
			PolicySHA256:      spec.Connector.PolicySHA256,
			ManifestSHA256:    run.ManifestSHA256,
			SnapshotID:        run.SnapshotID,
			SnapshotSHA256:    run.SnapshotSHA256,
		})
		if approvalErr != nil {
			if typed, ok := approvals.AsError(approvalErr); ok {
				return nil, capFailure(typed.Status, typed.Code, typed.Message)
			}
			return nil, capFailure(500, CodeInternal, "the approval could not be prepared")
		}
		prepared.approvalID = approval.ID
		if _, err := tx.ExecContext(ctx, `
			UPDATE platform.operations
			   SET state = 'awaiting_approval', updated_at = now()
			 WHERE id = $1::uuid AND state = 'authorized'`, operationID,
		); err != nil {
			return nil, capFailure(500, CodeInternal, "the connector approval state could not be recorded")
		}
		prepared.awaitingApproval = true
	}
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
	case "unknown":
		// F06/T051: a lost-receipt write is neither success nor failure. A
		// duplicate invoke of the same operation id/logical key returns the
		// recorded unknown state and never makes a second physical request.
		// The replay carries the same limitation disclosure as the first
		// dispatch and the owner ledger: the provider cannot be asked whether
		// the write landed.
		data := map[string]any{
			"error_code":              "operation_unknown",
			"requires_reconciliation": true,
			"reconciliation_status":   "pending",
			"dispatch_state":          "unknown",
			"limitation":              ReconciliationLimitationNoIdempotency,
		}
		if op.LastErrorCode.Valid {
			data["dispatch_error_code"] = op.LastErrorCode.String
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
	case "awaiting_approval":
		// A replayed write-risk connector answers the same parked state; it must
		// never become a second egress attempt for the same logical key.
		result.Data = map[string]any{
			"error_code":                  "approval_required",
			"connector_policy_version_id": op.CapabilityVersion,
			"risk_class":                  "write",
			"status":                      "awaiting_approval",
		}
		var approvalID string
		if err := tx.QueryRowContext(ctx, `
			SELECT id::text FROM platform.approvals WHERE operation_id = $1::uuid`,
			op.ID).Scan(&approvalID); err == nil {
			result.ApprovalID = &approvalID
		}
		return result, nil
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
//
// F06/T045: after the primary's bounded retries are exhausted by an availability
// fault, the first *compatible* declared fallback gets exactly one physical
// attempt of its own. A permission/authorisation/syntax/schema/tool-argument
// answer never reaches the fallback path. When no fallback is compatible the
// primary's typed failure is returned unchanged.
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
		Config:          spec.Profile.ConfigJSON,
		ValidationState: spec.Profile.ValidationState,
	}
	provider, err := s.Providers.For(candidate)
	if err != nil {
		return nil, providerFailure(err)
	}
	// F06/T046: the conservative price-version reservation of this profile. It
	// is reserved before every physical attempt and settled/released after it.
	reservation, capErr := reservationFor(spec.Profile, run, input.Params)
	if capErr != nil {
		return nil, capErr
	}
	pricing, capErr := priceForProfile(spec.Profile, run.Currency)
	if capErr != nil {
		return nil, capErr
	}
	failureCode := CodeProviderTransient
	failureReason := ""
	for attempt := 1; attempt <= maxPhysicalAttempts; attempt++ {
		// A caller cancellation that arrived before this attempt must not create
		// a ledger failure for a call that never happened (H2).
		if ctx.Err() != nil {
			return nil, s.finishCallerCancelled(ctx, operationID, attempt)
		}
		if _, capErr := s.beginModelAttempt(ctx, run, operationID, input.AttemptToken, attempt, reservation); capErr != nil {
			return nil, capErr
		}
		response, callErr := provider.Call(ctx, modelproxy.CallRequest{
			Candidate: candidate,
			Params:    input.Params,
			Attempt:   attempt,
		})
		if callErr == nil {
			return s.finishModelSuccess(ctx, run, input, spec, operationID, attempt, response, nil, pricing)
		}
		code, cancelled := modelCallFailureCode(ctx, callErr)
		if cancelled {
			// The caller is gone: no retry, no fallback, no provider failure and
			// no provider_error row. Close the real attempt with a clean-up
			// context and answer the (likely already disconnected) caller.
			return nil, s.finishCallerCancelled(ctx, operationID, attempt)
		}
		reason, fallbackEligible := fallbackReasonFor(callErr)
		if !fallbackEligible {
			// Permission, authorisation, syntax, schema-validation, tool
			// argument and policy failures are terminal: no retry, no fallback.
			if capErr := s.failAttemptAndOperation(ctx, operationID, attempt, code); capErr != nil {
				return nil, capErr
			}
			return nil, capFailure(503, code, "the model provider call did not complete")
		}
		failureCode, failureReason = code, reason
		if attempt < maxPhysicalAttempts {
			if capErr := s.resetAttemptForRetry(ctx, operationID, attempt, code); capErr != nil {
				return nil, capErr
			}
			if err := sleepBackoff(ctx, attempt); err != nil {
				// The caller vanished during the backoff: close the operation as a
				// cancellation instead of reporting an interrupted provider retry.
				return nil, s.finishCallerCancelled(ctx, operationID, attempt)
			}
			continue
		}
	}

	// The primary's availability fault outlived its bounded retries. Release the
	// last failed attempt's reservation before selecting a fallback: the
	// fallback must fit the budget that is actually still free, not one still
	// held by the attempt that just failed.
	if capErr := s.resetAttemptForRetry(ctx, operationID, maxPhysicalAttempts, failureCode); capErr != nil {
		return nil, capErr
	}
	selection := s.selectFallback(ctx, run, spec, input.Params, failureReason)
	if selection != nil {
		if selection.Reason == "" {
			selection.Reason = FallbackReasonAvailability
		}
		return s.dispatchFallback(ctx, run, input, spec, operationID,
			maxPhysicalAttempts+1, selection)
	}
	if capErr := s.failOperation(ctx, operationID, failureCode); capErr != nil {
		return nil, capErr
	}
	return nil, capFailure(503, failureCode,
		"the model provider exhausted its attempts")
}

// fallbackSelection is one compatible fallback chosen before dispatch.
type fallbackSelection struct {
	Profile *ModelProfile
	Reason  string
}

// dispatchFallback spends the operation's single fallback attempt. A failed
// fallback attempt is recorded like any other physical attempt; the returned
// error names the fallback's own code, never a fabricated success.
func (s *Service) dispatchFallback(
	ctx context.Context,
	run *runContext,
	input Input,
	spec *capabilitySpec,
	operationID string,
	attempt int,
	selection *fallbackSelection,
) (*Result, error) {
	profile := selection.Profile
	candidate := &modelproxy.Candidate{
		ID: profile.ID, Provider: profile.Provider, ModelID: profile.ModelID,
		EndpointRef: profile.EndpointRef, SecretRef: profile.SecretRef,
		Config:          profile.ConfigJSON,
		ValidationState: profile.ValidationState,
	}
	provider, err := s.Providers.For(candidate)
	if err != nil {
		if capErr := s.failOperation(ctx, operationID, providerErrorCode(err)); capErr != nil {
			return nil, capErr
		}
		return nil, providerFailure(err)
	}
	if err := provider.Available(candidate); err != nil {
		if capErr := s.failOperation(ctx, operationID, providerErrorCode(err)); capErr != nil {
			return nil, capErr
		}
		return nil, providerFailure(err)
	}
	reservation, capErr := reservationFor(profile, run, input.Params)
	if capErr != nil {
		_ = s.failOperation(ctx, operationID, capErr.Code)
		return nil, capErr
	}
	pricing, capErr := priceForProfile(profile, run.Currency)
	if capErr != nil {
		_ = s.failOperation(ctx, operationID, capErr.Code)
		return nil, capErr
	}
	if _, capErr := s.beginModelAttempt(ctx, run, operationID, input.AttemptToken, attempt, reservation); capErr != nil {
		return nil, capErr
	}
	response, callErr := provider.Call(ctx, modelproxy.CallRequest{
		Candidate: candidate,
		Params:    input.Params,
		Attempt:   attempt,
	})
	if callErr == nil {
		fact := &fallbackFact{ProfileVersionID: profile.ID, Reason: selection.Reason}
		return s.finishModelSuccess(ctx, run, input, spec, operationID, attempt, response, fact, pricing)
	}
	code, cancelled := modelCallFailureCode(ctx, callErr)
	if cancelled {
		// The caller went away during the fallback attempt: close it as a
		// cancellation, never as a provider failure (H2).
		return nil, s.finishCallerCancelled(ctx, operationID, attempt)
	}
	if capErr := s.failAttemptAndOperation(ctx, operationID, attempt, code); capErr != nil {
		return nil, capErr
	}
	return nil, capFailure(503, code,
		"the fallback model provider call did not complete")
}

// selectFallback applies the T045 gates in order: the declared list of the
// primary profile, manifest membership, passed/unrevoked, a dispatchable
// provider, compatibility (kind/protocol/context/tools/schema) and the run
// budget. The first profile that passes every gate is returned; anything that
// fails one is skipped, never silently used.
func (s *Service) selectFallback(
	ctx context.Context,
	run *runContext,
	spec *capabilitySpec,
	params map[string]any,
	reason string,
) *fallbackSelection {
	if spec == nil || spec.Profile == nil || spec.Model == nil || s.Providers == nil || run.Manifest == nil {
		return nil
	}
	ids := fallbackProfileIDs(spec.Profile)
	if len(ids) == 0 {
		return nil
	}
	budgetChecked := false
	budgetAllowed := false
	for _, id := range ids {
		if !containsString(run.Manifest.ModelProfileVersions, id) {
			// Not frozen into this run's manifest: never used, even if the
			// profile row exists and is passed.
			continue
		}
		profile, capErr := s.loadModelProfile(ctx, id)
		if capErr != nil {
			continue
		}
		candidate := &modelproxy.Candidate{
			ID: profile.ID, Provider: profile.Provider, ModelID: profile.ModelID,
			EndpointRef: profile.EndpointRef, SecretRef: profile.SecretRef,
			Config:          profile.ConfigJSON,
			ValidationState: profile.ValidationState,
		}
		provider, err := s.Providers.For(candidate)
		if err != nil {
			continue
		}
		if err := provider.Available(candidate); err != nil {
			continue
		}
		if checkFallbackCompatibility(spec.Profile, profile, spec.Model) != "" {
			continue
		}
		if !budgetChecked {
			budgetChecked = true
			budgetAllowed = s.budgetAllowsFallback(ctx, run, profile, params)
		}
		if !budgetAllowed {
			// The conservative reservation of this fallback does not fit the
			// remaining run budget: no fallback beyond budget.
			return nil
		}
		return &fallbackSelection{Profile: profile, Reason: reason}
	}
	return nil
}

// budgetAllowsFallback asks the decimal ledger whether the fallback profile's
// own conservative reservation still fits the run tree's remaining budget. The
// authoritative refusal remains ReserveTx; this pre-check keeps T045's rule
// that no fallback is attempted beyond budget (the primary's typed failure is
// returned instead). A read error refuses the fallback (fail closed).
func (s *Service) budgetAllowsFallback(ctx context.Context, run *runContext, profile *ModelProfile, params map[string]any) bool {
	if s.Budget == nil {
		return false
	}
	reservation, capErr := reservationFor(profile, run, params)
	if capErr != nil {
		return false
	}
	totals, err := s.Budget.Totals(ctx, run.ID)
	if err != nil {
		return false
	}
	if totals.Overrun {
		return false
	}
	required, err := budget.CompareAmounts(reservation.Amount, totals.Remaining)
	if err != nil {
		return false
	}
	return required <= 0
}

// beginModelAttempt CASes the operation from authorized to dispatching, reserves
// the conservative budget hold and records the physical attempt row atomically.
// A budget refusal rolls the whole transaction back, so no attempt row and no
// provider call exist, then fails the operation with the typed budget code so
// the owner ledger shows why the call stopped. It returns the attempt row id,
// which the streaming path uses as the frame generation id.
func (s *Service) beginModelAttempt(
	ctx context.Context,
	run *runContext,
	operationID, attemptToken string,
	attempt int,
	reservation *budget.ReserveRequest,
) (string, *Error) {
	if s.Budget == nil {
		return "", capFailure(503, "budget_unavailable", "the budget ledger is not configured")
	}
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return "", capFailure(500, CodeInternal, "the attempt transaction could not start")
	}
	defer func() { _ = tx.Rollback() }()
	var state string
	err = tx.QueryRowContext(ctx, `
		SELECT state FROM platform.operations WHERE id = $1::uuid FOR UPDATE`, operationID,
	).Scan(&state)
	if err != nil {
		return "", capFailure(500, CodeInternal, "the operation could not be locked")
	}
	if state != "authorized" {
		return "", capFailure(409, CodeOperationInProgress, "the operation is not in an authorized state")
	}
	request := *reservation
	request.RunID = run.ID
	request.OperationID = operationID
	request.AttemptKey = budget.AttemptKey(operationID, attempt)
	held, err := s.Budget.ReserveTx(ctx, tx, request)
	if err != nil {
		// Roll back the operation lock before recording the refusal: the failed
		// operation write must be its own committed transaction.
		_ = tx.Rollback()
		mapped := mapBudgetError(err)
		if mapped.Code == CodeBudgetExhausted || mapped.Code == CodeBudgetCurrencyMismatch {
			_ = s.failOperation(ctx, operationID, mapped.Code)
		}
		return "", mapped
	}
	attemptID := uuid.NewString()
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO platform.operation_attempts
			(id, operation_id, attempt_no, state, attempt_token,
			 reserved_amount, currency, price_version, settlement_state)
		VALUES ($1::uuid, $2::uuid, $3, 'dispatching', $4::uuid,
		        $5::numeric, $6, $7, 'reserved')`,
		attemptID, operationID, attempt, attemptToken,
		held.Amount, held.Currency, held.PriceVersion,
	); err != nil {
		if isUniqueViolation(err) {
			return "", capFailure(409, CodeOperationInProgress, "the operation already has that attempt")
		}
		return "", capFailure(500, CodeInternal, "the attempt could not be recorded")
	}
	if _, err := tx.ExecContext(ctx, `
		UPDATE platform.operations
		   SET state = 'dispatching', dispatched_at = now(), updated_at = now()
		 WHERE id = $1::uuid AND state = 'authorized'`, operationID,
	); err != nil {
		return "", capFailure(500, CodeInternal, "the operation could not be dispatched")
	}
	if err := tx.Commit(); err != nil {
		return "", capFailure(500, CodeInternal, "the attempt could not be committed")
	}
	return attemptID, nil
}

func (s *Service) finishModelSuccess(
	ctx context.Context,
	run *runContext,
	input Input,
	spec *capabilitySpec,
	operationID string,
	attempt int,
	response modelproxy.ProviderResponse,
	fallback *fallbackFact,
	pricing *modelPrice,
) (*Result, error) {
	projection, problems := validateProviderResponse(response.Data, spec.Model)
	if len(problems) > 0 {
		// F06/review2 P1-5: the provider answered and may have reported real
		// usage. A projection failure is a platform-side refusal, not proof that
		// the call was free: settle the actual usage instead of releasing it.
		_ = s.failModelAttemptAfterProvider(ctx, operationID, attempt,
			CodeProviderResponseInvalid, projection, pricing)
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
	if fallback != nil {
		// The receipt records the switch, not only the answer: a run that used a
		// fallback must never look like a seamless primary success.
		payload["fallback"] = map[string]any{
			"primary_profile_version_id": spec.Profile.ID,
			"profile_version_id":         fallback.ProfileVersionID,
			"reason":                     fallback.Reason,
		}
	}
	if projection.Structured != nil {
		payload["structured"] = projection.Structured
	}
	if projection.Usage != nil {
		payload["usage"] = projection.Usage
	}
	content, err := json.Marshal(payload)
	if err != nil {
		// The provider answer exists; its usage must still be settled.
		_ = s.failModelAttemptAfterProvider(ctx, operationID, attempt,
			CodeProviderResponseInvalid, projection, pricing)
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
		_ = tx.Rollback()
		// The provider answer exists; settling its usage must not depend on the
		// receipt artifact being writable.
		_ = s.failModelAttemptAfterProvider(ctx, operationID, attempt,
			CodeInternal, projection, pricing)
		return nil, capErr
	}
	// F06/T046: settle the actual usage against the price version. The charge
	// and its paired release, the receipt and the attempt projection commit
	// together, so a recorded success always has exactly one settled charge.
	// A provider answer without a usage object keeps the conservative
	// reservation (`usage_missing`) for maintenance reconciliation.
	if pricing == nil {
		return nil, capFailure(500, CodeInternal, "the settled price version is missing")
	}
	if projection.Usage == nil {
		if _, err := tx.ExecContext(ctx, `
			UPDATE platform.operation_attempts
			   SET state = 'succeeded', result_artifact_id = $3::uuid,
			       tokens_in = 0, tokens_out = 0, charged_amount = NULL,
			       price_version = $4, settlement_state = 'usage_missing',
			       finished_at = now()
			 WHERE operation_id = $1::uuid AND attempt_no = $2 AND state = 'dispatching'`,
			operationID, attempt, receiptArtifactID, pricing.version,
		); err != nil {
			return nil, capFailure(500, CodeInternal, "the attempt could not be closed")
		}
	} else {
		chargedAmount, err := budget.ActualCharge(pricing.price, projection.TokensIn, projection.TokensOut)
		if err != nil {
			return nil, capFailure(500, CodeInternal, "the actual usage cannot be priced")
		}
		tokensIn, tokensOut := projection.TokensIn, projection.TokensOut
		settled, err := s.Budget.SettleTx(ctx, tx, budget.Settlement{
			AttemptKey:   budget.AttemptKey(operationID, attempt),
			Amount:       chargedAmount,
			Currency:     pricing.price.Currency,
			PriceVersion: pricing.version,
			TokensIn:     &tokensIn,
			TokensOut:    &tokensOut,
		})
		if err != nil {
			return nil, mapBudgetError(err)
		}
		result, err := tx.ExecContext(ctx, `
			UPDATE platform.operation_attempts
			   SET state = 'succeeded', result_artifact_id = $3::uuid,
			       tokens_in = $4, tokens_out = $5, charged_amount = $6::numeric,
			       price_version = $7, settlement_state = 'settled',
			       finished_at = now()
			 WHERE operation_id = $1::uuid AND attempt_no = $2 AND state = 'dispatching'`,
			operationID, attempt, receiptArtifactID, projection.TokensIn, projection.TokensOut,
			settled.ChargedAmount, pricing.version,
		)
		if err != nil {
			return nil, capFailure(500, CodeInternal, "the attempt could not be closed")
		}
		if affected, _ := result.RowsAffected(); affected != 1 {
			return nil, capFailure(500, CodeInternal, "the attempt was not dispatching")
		}
	}
	if _, err := tx.ExecContext(ctx, `
		UPDATE platform.operations
		   SET state = 'succeeded', receipt_artifact_id = $2::uuid,
		       fallback_profile_version_id = NULL, fallback_reason = NULL,
		       last_error_code = NULL, updated_at = now()
		 WHERE id = $1::uuid AND state = 'dispatching'`, operationID, receiptArtifactID,
	); err != nil {
		return nil, capFailure(500, CodeInternal, "the operation could not be succeeded")
	}
	if fallback != nil {
		// The operation keeps the primary capability_version the worker named
		// and records which profile actually answered, and why it switched.
		if _, err := tx.ExecContext(ctx, `
			UPDATE platform.operations
			   SET fallback_profile_version_id = $2::uuid, fallback_reason = $3,
			       last_error_code = NULL, updated_at = now()
			 WHERE id = $1::uuid AND state = 'succeeded'`,
			operationID, fallback.ProfileVersionID, fallback.Reason,
		); err != nil {
			return nil, capFailure(500, CodeInternal, "the fallback fact could not be recorded")
		}
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

func (s *Service) resetAttemptForRetry(ctx context.Context, operationID string, attempt int, code string) *Error {
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return capFailure(500, CodeInternal, "the retry transaction could not start")
	}
	defer func() { _ = tx.Rollback() }()
	if s.Budget != nil {
		if _, err := s.Budget.ReleaseTx(ctx, tx, budget.AttemptKey(operationID, attempt)); err != nil {
			return capFailure(500, CodeInternal, "the failed attempt reservation could not be released")
		}
	}
	if _, err := tx.ExecContext(ctx, `
		UPDATE platform.operation_attempts
		   SET state = 'failed', error_code = $2, finished_at = now(),
		       settlement_state = CASE WHEN settlement_state = 'reserved'
		                                THEN 'released' ELSE settlement_state END
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

// failModelAttemptAfterProvider closes a model attempt that failed after the
// provider's answer was already received. The provider's tokens were really
// consumed, so when its usage object parsed the actual amount is settled (charge
// + paired release) and recorded on the failed attempt; a missing or unusable
// usage keeps the conservative reservation as `usage_missing` for maintenance
// reconciliation. The operation is failed with code. The reservation of a call
// the provider answered is never silently released.
func (s *Service) failModelAttemptAfterProvider(
	ctx context.Context,
	operationID string,
	attempt int,
	code string,
	projection *providerProjection,
	pricing *modelPrice,
) error {
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer func() { _ = tx.Rollback() }()

	settled := false
	var chargedAmount string
	var tokensIn, tokensOut int64
	if s.Budget != nil && pricing != nil {
		if in, out, ok := settleFailureUsage(projection); ok {
			amount, chargeErr := budget.ActualCharge(pricing.price, in, out)
			if chargeErr == nil {
				result, settleErr := s.Budget.SettleTx(ctx, tx, budget.Settlement{
					AttemptKey:   budget.AttemptKey(operationID, attempt),
					Amount:       amount,
					Currency:     pricing.price.Currency,
					PriceVersion: pricing.version,
					TokensIn:     &in,
					TokensOut:    &out,
				})
				if settleErr == nil {
					settled = true
					chargedAmount = result.ChargedAmount
					tokensIn, tokensOut = in, out
				}
			}
		}
	}
	switch {
	case settled:
		if _, err := tx.ExecContext(ctx, `
			UPDATE platform.operation_attempts
			   SET state = 'failed', error_code = $3, tokens_in = $4, tokens_out = $5,
			       charged_amount = $6::numeric, price_version = $7,
			       settlement_state = 'settled', finished_at = now()
			 WHERE operation_id = $1::uuid AND attempt_no = $2`,
			operationID, attempt, code, tokensIn, tokensOut, chargedAmount, pricing.version,
		); err != nil {
			return err
		}
	case s.Budget != nil && pricing != nil:
		// The provider answered but its usage is missing or unusable: keep the
		// conservative reservation instead of pretending the call was free.
		if _, err := tx.ExecContext(ctx, `
			UPDATE platform.operation_attempts
			   SET state = 'failed', error_code = $3, price_version = $4,
			       settlement_state = CASE WHEN settlement_state = 'reserved'
			                               THEN 'usage_missing' ELSE settlement_state END,
			       finished_at = now()
			 WHERE operation_id = $1::uuid AND attempt_no = $2`,
			operationID, attempt, code, pricing.version,
		); err != nil {
			return err
		}
	default:
		// No price version means there is no meaningful charge to record; keep
		// the plain release behaviour of a pre-dispatch failure.
		if s.Budget != nil {
			if _, err := s.Budget.ReleaseTx(ctx, tx, budget.AttemptKey(operationID, attempt)); err != nil {
				return err
			}
		}
		if _, err := tx.ExecContext(ctx, `
			UPDATE platform.operation_attempts
			   SET state = 'failed', error_code = $3, finished_at = now(),
			       settlement_state = CASE WHEN settlement_state = 'reserved'
			                               THEN 'released' ELSE settlement_state END
			 WHERE operation_id = $1::uuid AND attempt_no = $2`, operationID, attempt, code,
		); err != nil {
			return err
		}
	}
	if _, err := tx.ExecContext(ctx, `
		UPDATE platform.operations
		   SET state = 'failed', last_error_code = $2, updated_at = now()
		 WHERE id = $1::uuid`, operationID, code); err != nil {
		return err
	}
	return tx.Commit()
}

func (s *Service) failAttemptAndOperation(ctx context.Context, operationID string, attempt int, code string) error {
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer func() { _ = tx.Rollback() }()
	if s.Budget != nil {
		if _, err := s.Budget.ReleaseTx(ctx, tx, budget.AttemptKey(operationID, attempt)); err != nil {
			return err
		}
	}
	if _, err := tx.ExecContext(ctx, `
		UPDATE platform.operation_attempts
		   SET state = 'failed', error_code = $3, finished_at = now(),
		       settlement_state = CASE WHEN settlement_state = 'reserved'
		                                THEN 'released' ELSE settlement_state END
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
// tool-call/structured summary. T045 adds the fallback facts and the summed
// cost/tokens of every physical attempt, so a switched call is visibly not a
// seamless primary success.
type LedgerOperation struct {
	ID                string  `json:"id"`
	CapabilityKind    string  `json:"capability_kind"`
	RiskClass         string  `json:"risk_class"`
	ConnectorTarget   *string `json:"connector_target,omitempty"`
	CapabilityVersion string  `json:"capability_version"`
	State             string  `json:"state"`
	Attempts          int     `json:"attempts"`
	ErrorCode         *string `json:"error_code"`
	ReceiptArtifactID *string `json:"receipt_artifact_id"`
	// F06/T051 unknown-write reconciliation projection. RequiresReconciliation
	// is true only while the operation is `unknown`; the limitation is the
	// provider-capability fact recorded before dispatch (null when the provider
	// can be queried / carries an idempotency key).
	RequiresReconciliation   bool             `json:"requires_reconciliation,omitempty"`
	ReconciledBy             *string          `json:"reconciled_by,omitempty"`
	ReconciliationEvidenceID *string          `json:"reconciliation_evidence_id,omitempty"`
	ReconciliationConclusion *string          `json:"reconciliation_conclusion,omitempty"`
	ReconciliationLimitation *string          `json:"reconciliation_limitation,omitempty"`
	ProviderIdempotencyKey   *string          `json:"provider_idempotency_key,omitempty"`
	FallbackProfileVersionID *string          `json:"fallback_profile_version_id,omitempty"`
	FallbackReason           *string          `json:"fallback_reason,omitempty"`
	TokensIn                 int64            `json:"tokens_in"`
	TokensOut                int64            `json:"tokens_out"`
	ChargedAmount            string           `json:"charged_amount"`
	ToolCallSummary          []LedgerToolCall `json:"tool_call_summary"`
	StructuredKeys           []string         `json:"structured_output_keys"`
	Stub                     bool             `json:"stub"`
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
		       o.risk_class, cp.origin, cp.method, cp.path_prefix,
		       o.last_error_code, o.receipt_artifact_id::text,
		       o.fallback_profile_version_id::text, o.fallback_reason,
		       o.reconciled_by::text, o.reconciliation_evidence_id::text,
		       o.reconciliation_conclusion, o.reconciliation_limitation,
		       o.provider_idempotency_key,
		       (SELECT count(*) FROM platform.operation_attempts AS a
		         WHERE a.operation_id = o.id),
		       COALESCE((SELECT sum(a.tokens_in) FROM platform.operation_attempts AS a
		                  WHERE a.operation_id = o.id), 0),
		       COALESCE((SELECT sum(a.tokens_out) FROM platform.operation_attempts AS a
		                  WHERE a.operation_id = o.id), 0),
		       COALESCE((SELECT sum(a.charged_amount) FROM platform.operation_attempts AS a
		                  WHERE a.operation_id = o.id), 0)::text,
		       a.object_key, a.sha256, a.state, a.deleted_at
		  FROM platform.operations AS o
		  LEFT JOIN platform.artifacts AS a ON a.id = o.receipt_artifact_id
		  LEFT JOIN platform.connector_policies AS cp
		         ON o.capability_kind = 'connector' AND cp.id::text = o.capability_version
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
			fallbackID    sql.NullString
			fallbackWhy   sql.NullString
			reconciledBy  sql.NullString
			evidenceID    sql.NullString
			conclusion    sql.NullString
			limitation    sql.NullString
			providerKey   sql.NullString
			origin        sql.NullString
			method        sql.NullString
			pathPrefix    sql.NullString
			objectKey     sql.NullString
			digest        sql.NullString
			artifactState sql.NullString
			deleted       sql.NullTime
		)
		if err := rows.Scan(&entry.ID, &entry.CapabilityKind, &entry.CapabilityVersion,
			&entry.State, &entry.RiskClass, &origin, &method, &pathPrefix, &errorCode, &receiptID, &fallbackID, &fallbackWhy,
			&reconciledBy, &evidenceID, &conclusion, &limitation, &providerKey,
			&entry.Attempts, &entry.TokensIn, &entry.TokensOut, &entry.ChargedAmount,
			&objectKey, &digest, &artifactState, &deleted); err != nil {
			return nil, capFailure(500, CodeInternal, "the capability ledger could not be read")
		}
		entry.RequiresReconciliation = entry.State == "unknown"
		if reconciledBy.Valid {
			value := reconciledBy.String
			entry.ReconciledBy = &value
		}
		if evidenceID.Valid {
			value := evidenceID.String
			entry.ReconciliationEvidenceID = &value
		}
		if conclusion.Valid {
			value := conclusion.String
			entry.ReconciliationConclusion = &value
		}
		if limitation.Valid {
			value := limitation.String
			entry.ReconciliationLimitation = &value
		}
		if providerKey.Valid {
			value := providerKey.String
			entry.ProviderIdempotencyKey = &value
		}
		entry.ToolCallSummary = []LedgerToolCall{}
		entry.StructuredKeys = []string{}
		if origin.Valid && method.Valid && pathPrefix.Valid {
			target := method.String + " " + origin.String + pathPrefix.String
			entry.ConnectorTarget = &target
		}
		if errorCode.Valid {
			value := errorCode.String
			entry.ErrorCode = &value
		}
		if receiptID.Valid {
			value := receiptID.String
			entry.ReceiptArtifactID = &value
		}
		if fallbackID.Valid {
			value := fallbackID.String
			entry.FallbackProfileVersionID = &value
		}
		if fallbackWhy.Valid {
			value := fallbackWhy.String
			entry.FallbackReason = &value
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

// LedgerBudget is the owner-facing budget projection. It is computed from the
// usage ledger, never from a client hint; Overrun means actual usage exceeded
// the limit and the run must not be presented as a clean success.
type LedgerBudget struct {
	BudgetLimit string `json:"budget_limit"`
	Reserved    string `json:"reserved_amount"`
	Charged     string `json:"charged_amount"`
	Remaining   string `json:"remaining_amount"`
	Currency    string `json:"currency"`
	Overrun     bool   `json:"overrun"`
}

// BudgetFacts returns the run tree's decimal budget projection for its owner.
func (s *Service) BudgetFacts(ctx context.Context, ownerID, runID string) (*LedgerBudget, *Error) {
	if s.Budget == nil {
		return nil, capFailure(503, "budget_unavailable", "the budget ledger is not configured")
	}
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
	totals, err := s.Budget.Totals(ctx, runID)
	if err != nil {
		return nil, capFailure(500, CodeInternal, "the run budget could not be read")
	}
	return &LedgerBudget{
		BudgetLimit: totals.BudgetLimit, Reserved: totals.Reserved,
		Charged: totals.Charged, Remaining: totals.Remaining,
		Currency: totals.Currency, Overrun: totals.Overrun,
	}, nil
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
	case errors.Is(err, modelproxy.ErrDestinationUnapproved):
		return capFailure(503, CodeProviderDestinationNotApproved,
			"the model endpoint destination is not approved by the deployment catalog")
	case errors.Is(err, modelproxy.ErrAdapterUnavailable):
		return capFailure(503, CodeAdapterUnavailable, "no vendor adapter is compiled for this provider")
	case errors.Is(err, modelproxy.ErrCandidateUnavailable):
		return capFailure(422, CodeModelUnavailable, "the model profile version is not callable")
	case errors.Is(err, context.Canceled), errors.Is(err, context.DeadlineExceeded):
		// A caller cancellation is not a provider claim; it is never recorded as
		// a provider failure (B2 review fix H2).
		return capFailure(503, CodeCallerCancelled, "the capability call was cancelled")
	default:
		if code := modelproxy.ProviderCode(err); code != "" {
			return capFailure(503, code, "the model provider call did not complete")
		}
		return capFailure(503, CodeProviderNotConfigured, "the model provider is not available")
	}
}

// providerErrorCode is the one ledger code of a provider-dispatch failure. The
// provider vocabulary itself is shared (internal/providercodes); this function
// only decides which member applies and the capability-only fallback.
//
// A caller cancellation is mapped explicitly: it is a cancellation, not an
// unclassified provider failure, so a raced call cannot be recorded as
// provider_error (B2 review fix H2; the dispatch loop short-circuits even
// earlier, so no retry/fallback/ledger failure is attempted for it).
func providerErrorCode(err error) string {
	switch {
	case errors.Is(err, modelproxy.ErrNotConfigured):
		return CodeProviderNotConfigured
	case errors.Is(err, modelproxy.ErrAdapterUnavailable):
		return CodeAdapterUnavailable
	case errors.Is(err, context.Canceled), errors.Is(err, context.DeadlineExceeded):
		return CodeCallerCancelled
	}
	if code := modelproxy.ProviderCode(err); code != "" {
		return code
	}
	return providercodes.Unclassified
}

// modelCallFailureCode classifies one failed physical model call before anything
// is written. It returns (code, cancelled):
//
//   - cancelled is true for a caller cancellation or an already-done caller
//     context. The caller-owned request is gone; the attempt still has to be
//     closed (with a detached cleanup context), but the failure must not be
//     retried, must not select a fallback and must never be recorded as a
//     provider failure.
//   - otherwise code is the provider ledger code, with a typed transient error
//     reported as CodeProviderTransient exactly like the retry loop expects.
func modelCallFailureCode(ctx context.Context, err error) (string, bool) {
	if errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) {
		return CodeCallerCancelled, true
	}
	if ctx != nil && ctx.Err() != nil {
		return CodeCallerCancelled, true
	}
	if modelproxy.IsTransient(err) {
		return CodeProviderTransient, false
	}
	return providerErrorCode(err), false
}

// finishCallerCancelled closes the physical attempt of a caller-cancelled call
// and releases its reservation with a context that outlives the dead request.
// It mirrors the streaming path's cleanup (stream.go: a non-cancellable 5s
// context) so the ledger row is never left `dispatching` and nothing is billed.
func (s *Service) finishCallerCancelled(ctx context.Context, operationID string, attempt int) *Error {
	cleanupCtx, cancel := context.WithTimeout(context.WithoutCancel(ctx), 5*time.Second)
	defer cancel()
	if err := s.failAttemptAndOperation(cleanupCtx, operationID, attempt, CodeCallerCancelled); err != nil {
		return capFailure(500, CodeInternal, "the cancelled capability attempt could not be closed")
	}
	return capFailure(503, CodeCallerCancelled, "the capability call was cancelled by its caller")
}

// sleepBackoff is the bounded retry pause. It is not a budget gate: the
// reservation and settlement are durable ledgers, not timing heuristics.
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
