// Package presetexec is F05/T033's tool-debug executor: one claimed run of the
// fixed preset python tool.
//
// The flow is deliberately a straight line with explicit refusals:
//
//	claim ──> verify the sealed manifest bytes (never the JSONB projection)
//	      ──> locate exactly one python node whose tool_version is the preset
//	      ──> resolve exactly one task_input CSV/XLSX artifact linked to the run
//	      ──> download it through the run-scoped signed link and verify size/sha
//	      ──> run the fixed tool inside the gVisor sandbox (heartbeat while it runs,
//	          observe cancel_requested, kill and prove cleanup)
//	      ──> upload summary/CSV/diagnostics/log through the run-scoped endpoints,
//	          wait for the finalize guard, register the outputs
//	      ──> submit the result proposal, then confirm the attempt exit with the
//	          T031 identity evidence
//
// Everything the executor sees from the sandbox is untrusted: output.json is
// parsed strictly, the artifact list is admitted against the collected bytes, and
// the tool's rejection vocabulary is closed. Every failure either produces a
// specific run error code with a real exit confirmation, or refuses before any
// state change; there is no path that reports success without a registered,
// ready output artifact.
package presetexec

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"orchara/platform/internal/capabilities"
	agentcontext "orchara/platform/internal/context"
	"orchara/platform/internal/sandbox"
	"orchara/platform/internal/tools"
	"orchara/platform/internal/uuid"
)

// The fixed preset tool identity. The fixture registry and the seeded
// tool_versions row use this id; a python node that names anything else is not a
// tool this build may execute.
const (
	PresetToolID   = "11111111-1111-4111-8111-111111111111"
	PresetToolName = "sales_stats"
	// CompilerVersion is the only compiler contract this executor speaks.
	CompilerVersion = "orchara-compiler/1.0.0"
	// ProtocolVersion is the execution protocol version the lease must speak.
	ProtocolVersion = "1.0"
)

// Stable run error codes this package submits. They are the run's error_code
// (failed) or the operator report's reason; they are never invented by a caller.
const (
	CodeToolUnsupportedGraph = "tool_unsupported_graph"
	CodeToolUnsupportedTool  = "tool_unsupported_tool"
	CodeToolInputInvalid     = "tool_input_invalid"
	CodeToolInputAmbiguous   = "tool_input_ambiguous"
	CodeInputIntegrity       = "tool_input_integrity_failed"
	CodeManifestUnavailable  = "manifest_unavailable"
	CodeSandboxUnavailable   = "sandbox_unavailable"
	CodeSandboxOrphan        = "sandbox_orphan"
	CodeSandboxFailed        = "sandbox_failed"
	CodeToolTimeout          = "tool_timeout"
	CodeToolCrash            = "tool_crash"
	CodeToolOutputInvalid    = "tool_output_invalid"
	CodeToolArtifactInvalid  = "tool_artifact_invalid"
	CodeProtocolMismatch     = "worker_protocol_mismatch"
	// F04A/T042 linear-plan codes. T043 replaces the agent refusal; the node
	// input/output codes are the located binding/schema failures of one node.
	CodeNodeInputInvalid  = "node_input_invalid"
	CodeNodeOutputInvalid = "node_output_invalid"
)

// Defaults for the wait bounds. A deployment can shorten them; a tiny value is
// refused rather than busy-looping.
const (
	DefaultResultWait   = 60 * time.Second
	DefaultPollInterval = 100 * time.Millisecond
	MaxResultWait       = 10 * time.Minute
	MaxLogBytes         = 64 * 1024
	MaxInputBytes       = 20 * 1024 * 1024
)

// Lease is the contract claim response as the supervisor reads it.
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
}

// Heartbeat is the contract HeartbeatReply subset the executor needs.
type Heartbeat struct {
	CancelRequested bool
}

// UploadRequest is the createWorkerUpload body.
type UploadRequest struct {
	MediaType string
	SizeBytes int64
	Purpose   string
}

// UploadTicket is the createWorkerUpload response.
type UploadTicket struct {
	ArtifactID string
	URL        string
}

// ArtifactView is the contract's four-field Artifact.
type ArtifactView struct {
	ID        string
	State     string
	MediaType string
	SizeBytes int64
}

// Proposal is the submitWorkerResult body (attempt token added by the client).
type Proposal struct {
	EventID          string
	SuggestedState   string
	OutputArtifactID string
	ErrorCode        string
	// CheckpointID is the stable synthetic checkpoint a waiting proposal stops
	// at. F06/T050 uses "capability:<operation_id>" so the resumed attempt
	// re-enters the same logical capability call.
	CheckpointID string
}

// InputArtifact is one run input the trusted database identity resolved.
type InputArtifact struct {
	ID        string
	Purpose   string
	MediaType string
	SizeBytes int64
	SHA256    string
}

// NodeResult is one per-node provenance row. It never carries content: the
// artifact it names was already uploaded, completed and registered through the
// run-scoped artifact routes before this row is reported.
type NodeResult struct {
	NodeID     string
	Kind       string
	ArtifactID string
	Label      string
}

// SpawnChildRequest is the contract spawnChild body the executor sends. The
// logical key is derived deterministically from the run and the invocation path,
// so a retried attempt replays the same child instead of creating a second one.
type SpawnChildRequest struct {
	LogicalKey           string
	ApplicationVersionID string
	Input                map[string]any
}

// ChildRun is the contract Run subset the executor reads from spawnChild. It is
// the platform's own projection; the executor never invents a child identity.
type ChildRun struct {
	ID                   string
	State                string
	Revision             int64
	ApplicationVersionID string
	ParentRunID          string
	OutputArtifactID     string
	ErrorCode            string
	Kind                 string
	ExecutionSnapshotID  string
	Phase                string
}

// NodeApprovalRequest is the F04B/T087 business-approval proposal the executor
// asks the platform to prepare. It carries no decision and no capability: the
// node id, the sealed config and the run-resolved proposal are the only inputs,
// and the server rebuilds the canonical proposal/hash from them.
type NodeApprovalRequest struct {
	NodeID         string
	Title          string
	ExpiresSeconds int
	Proposal       map[string]any
}

// NodeApproval is the platform's answer to PrepareNodeApproval. The approval row
// is the server's business decision record (a `platform.approvals` row with no
// operation); the executor only copies its state into the run.
type NodeApproval struct {
	ID                 string
	State              string
	ParamsSHA256       string
	ProposalArtifactID string
	ExpiresAt          time.Time
	DecidedAt          *time.Time
}

// ChildResult is the non-contract terminal result read: the child's state, its
// sealed output artifact and the decoded JSON object that artifact holds.
type ChildResult struct {
	ChildRunID       string
	State            string
	OutputArtifactID string
	ErrorCode        string
	Output           map[string]any
}

// Client is the execution protocol as this executor uses it. The supervisor
// process implements it with two mTLS capacities (supervisor for claim/exit,
// worker for the artifact routes) and never exposes a generic request method.
type Client interface {
	Claim(ctx context.Context, workerID, executionUnitID string) (*Lease, error)
	Heartbeat(ctx context.Context, runID, attemptToken, executionUnitID string) (Heartbeat, error)
	Fetch(ctx context.Context, url string) ([]byte, error)
	PutBytes(ctx context.Context, url string, content []byte) error
	SubmitResult(ctx context.Context, runID, attemptToken string, proposal Proposal) error
	ConfirmExit(ctx context.Context, attemptID, executionUnitID string, evidence map[string]any) error
	CreateUpload(ctx context.Context, runID, attemptToken string, request UploadRequest) (UploadTicket, error)
	Complete(ctx context.Context, runID, attemptToken, artifactID, sha256 string) (ArtifactView, error)
	Status(ctx context.Context, runID, attemptToken, artifactID string) (ArtifactView, error)
	Register(ctx context.Context, runID, attemptToken, artifactID, relation string) (ArtifactView, error)
	Download(ctx context.Context, runID, attemptToken, artifactID string) ([]byte, error)
	RunState(ctx context.Context, runID string) (state, errorCode string, err error)
	// SubmitNodeStatus submits exactly one node.status transition
	// (pending/running/succeeded/failed). The implementation binds the payload's
	// required attempt_no to the live attempt and never carries content.
	SubmitNodeStatus(ctx context.Context, runID, attemptToken, nodeID, state string) error
	// RecordNodeResults reports per-node data/file/log provenance after the
	// artifacts are registered. The server re-proves the live attempt and the
	// artifact/purpose before writing a row.
	RecordNodeResults(ctx context.Context, runID, attemptToken string, results []NodeResult) error
	// InvokeCapability is one T041 run-scoped capability call (model or tool)
	// over the worker mTLS capacity. The server authorizes, meters and seals; the
	// worker never talks to a provider directly.
	InvokeCapability(
		ctx context.Context, runID, attemptToken string, input capabilities.Input,
	) (*capabilities.Result, error)
	// ReportCapabilityResult reports the validated result of a granted tool call
	// through the non-contract capability-results bridge. Idempotent.
	ReportCapabilityResult(
		ctx context.Context, runID, attemptToken string, report capabilities.ResultReport,
	) (*capabilities.Result, error)
	// SpawnChild creates (or replays) one delegated child Run through the
	// platform's contract spawnChild operation. This is the only way this
	// executor can create a Run: it holds a live attempt token on the parent and
	// the server re-proves the frozen closure, the child version and the mapping.
	SpawnChild(
		ctx context.Context, runID, attemptToken string, request SpawnChildRequest,
	) (*ChildRun, error)
	// ChildResult reads one terminal child's structured result. The server
	// re-proves the parent's live attempt and the parent_run_id link; the child
	// output bytes are re-hashed before they are decoded.
	ChildResult(
		ctx context.Context, runID, attemptToken, childRunID string,
	) (*ChildResult, error)
	// PrepareNodeApproval asks the platform to prepare (or replay) the one
	// server-built approval for this run's approval node. The executor never
	// supplies a decision; a pending approval parks the attempt at
	// `approval:<approval_id>` and an approved one lets the node publish its
	// decision output. The call is idempotent for the same canonical proposal.
	PrepareNodeApproval(
		ctx context.Context, runID, attemptToken string, request NodeApprovalRequest,
	) (*NodeApproval, error)
	// Retrieve runs one F04B/T088 knowledge retrieval through the non-contract
	// internal mTLS route. The server derives the run owner from the database and
	// applies each knowledge base's ACL before ranking; the executor never sees a
	// knowledge service, an embedding or an object key.
	Retrieve(
		ctx context.Context, runID, attemptToken string, request RetrieveRequest,
	) (*RetrieveResult, error)
}

// InputSource resolves the run's linked input artifacts. The supervisor's
// implementation is a read-only database query; the bytes still move through the
// signed link the Client mints.
type InputSource interface {
	Inputs(ctx context.Context, runID string) ([]InputArtifact, error)
}

// NodeOutputSource is the optional F04B recovery seam: the node_id -> canonical
// JSON bytes of every already-completed `data` node output of this run. A resumed
// attempt re-uses those outputs instead of re-calling a model/tool/sandbox, which
// is what keeps T087/T091's "wait twice, then join" from repeating finished work.
//
// The supervisor's implementation re-reads platform.run_node_outputs and verifies
// each artifact's sha256 against the stored bytes; the executor additionally
// re-validates the decoded object against the node's output_schema before it
// trusts a reused value. An error here is not fatal: the attempt re-executes the
// nodes instead (the executor logs the degradation).
type NodeOutputSource interface {
	NodeData(ctx context.Context, runID string) (map[string][]byte, error)
}

// RunScope is the trusted owner/application identity of one run. It is the
// authorization scope of personal-memory recall: an agent can only ever recall
// the run owner's memories for the run's own application. It is read from the
// database identity (runs.owner_id + the frozen execution snapshot), never from
// the sealed manifest or a request body.
type RunScope struct {
	OwnerID       string
	ApplicationID string
}

// RunIdentity is the optional trusted read-only identity source a configured
// Memory source is scoped by. A process that wires Memory without RunIdentity
// recalls nothing: the empty scope is refused by the recall source and the trace
// reports unavailable, which is the honest outcome rather than guessing a scope.
type RunIdentity interface {
	RunScope(ctx context.Context, runID string) (RunScope, error)
}

// Sandbox is the execution unit. The real implementation is *sandbox.Executor.
type Sandbox interface {
	Run(ctx context.Context, request sandbox.Request, control sandbox.Control) (sandbox.Outcome, error)
}

// Options is the executor's deployment profile.
type Options struct {
	WorkerID string
	// ExecutionUnitID is allocated for this claim before the claim is sent.
	ExecutionUnitID string
	ResultWait      time.Duration
	PollInterval    time.Duration
	HeartbeatTick   time.Duration
	MaxLogBytes     int
}

func (o Options) normalized() (Options, error) {
	if strings.TrimSpace(o.ExecutionUnitID) == "" {
		return Options{}, errors.New("execution unit id is required")
	}
	if strings.TrimSpace(o.WorkerID) == "" {
		o.WorkerID = "orchara-supervisor"
	}
	if o.ResultWait <= 0 {
		o.ResultWait = DefaultResultWait
	}
	if o.ResultWait > MaxResultWait {
		return Options{}, fmt.Errorf("result wait must be at most %s", MaxResultWait)
	}
	if o.PollInterval <= 0 {
		o.PollInterval = DefaultPollInterval
	}
	if o.PollInterval < 10*time.Millisecond {
		return Options{}, errors.New("poll interval must be at least 10ms")
	}
	if o.HeartbeatTick <= 0 {
		o.HeartbeatTick = 5 * time.Second
	}
	if o.MaxLogBytes <= 0 || o.MaxLogBytes > MaxLogBytes {
		o.MaxLogBytes = MaxLogBytes
	}
	return o, nil
}

// ArtifactReport is one artifact the executor moved.
type ArtifactReport struct {
	ID        string `json:"id"`
	Purpose   string `json:"purpose"`
	Relation  string `json:"relation,omitempty"`
	State     string `json:"state"`
	SizeBytes int64  `json:"size_bytes"`
}

// SandboxReport is the observed execution unit.
type SandboxReport struct {
	SandboxID    string               `json:"sandbox_id"`
	RootfsDigest string               `json:"rootfs_digest"`
	Started      bool                 `json:"started"`
	ExitCode     int                  `json:"exit_code"`
	TimedOut     bool                 `json:"timed_out"`
	Cancelled    bool                 `json:"cancelled"`
	DurationMS   int64                `json:"duration_ms"`
	Cleanup      sandbox.CleanupFacts `json:"cleanup"`
}

// Report is the operator-facing result of one `exec --once`.
type Report struct {
	Status          string           `json:"status"` // idle|executed|failed
	RunID           string           `json:"run_id,omitempty"`
	JobID           string           `json:"job_id,omitempty"`
	ExecutionUnitID string           `json:"execution_unit_id"`
	Outcome         string           `json:"outcome,omitempty"` // succeeded|failed|cancelled|refused|error
	RunState        string           `json:"run_state,omitempty"`
	ErrorCode       string           `json:"error_code,omitempty"`
	Error           string           `json:"error,omitempty"`
	Heartbeats      int64            `json:"heartbeats"`
	Sandbox         *SandboxReport   `json:"sandbox,omitempty"`
	Artifacts       []ArtifactReport `json:"artifacts,omitempty"`
	DurationMS      int64            `json:"duration_ms"`
}

// Deps is everything one execution needs.
type Deps struct {
	Client  Client
	Inputs  InputSource
	Sandbox Sandbox
	// NodeOutputs is the optional F04B recovery seam. Nil means every node is
	// executed; a wired source lets a resumed attempt reuse completed node
	// outputs (see NodeOutputSource).
	NodeOutputs NodeOutputSource
	// Facts is the trusted read-only capability catalog. Agent nodes require it;
	// the T042 python chain does not.
	Facts Facts
	// Memory is the F09 confirmed-memory source of the agent context. Nil means
	// no source is wired and the trace reports memory_state=not_run.
	Memory agentcontext.MemorySource
	// RunIdentity scopes Memory to the run owner+application. It is required for
	// a real recall; without it the source receives an empty scope and recalls
	// nothing.
	RunIdentity RunIdentity
	Options     Options
	Logger      *log.Logger
}

// runner owns one execution's mutable state.
type runner struct {
	deps     Deps
	options  Options
	lease    *Lease
	manifest *manifestDocument
	// projection is the lease's JSONB manifest projection, used only as the
	// fallback source of the execution identity when the sealed bytes cannot be
	// read (a refusal still has to state which image it is refusing to run).
	projection *manifestDocument
	heartbeats atomic.Int64
	// artifactsMu guards artifacts: F04B/T085 node goroutines upload their own
	// node artifacts concurrently.
	artifactsMu sync.Mutex
	artifacts   []ArtifactReport
	logger      *log.Logger
}

// Run performs at most one claim and its execution. It never panics on a refusal:
// the returned Report says what happened, and Status=="idle" means no work.
func Run(ctx context.Context, deps Deps) Report {
	started := time.Now()
	report := Report{ExecutionUnitID: deps.Options.ExecutionUnitID}
	finish := func() Report {
		report.DurationMS = time.Since(started).Milliseconds()
		return report
	}
	options, err := deps.Options.normalized()
	if err != nil {
		report.Status = "failed"
		report.Error = err.Error()
		return finish()
	}
	if deps.Client == nil || deps.Inputs == nil || deps.Sandbox == nil {
		report.Status = "failed"
		report.Error = "the executor is not wired (client, input source or sandbox missing)"
		return finish()
	}
	logger := deps.Logger
	if logger == nil {
		logger = log.New(discardWriter{}, "", 0)
	}
	current := &runner{deps: deps, options: options, logger: logger}

	lease, err := current.deps.Client.Claim(ctx, options.WorkerID, options.ExecutionUnitID)
	if err != nil {
		report.Status = "failed"
		report.Error = "claim: " + err.Error()
		return finish()
	}
	if lease == nil {
		report.Status = "idle"
		return finish()
	}
	current.lease = lease
	var projection manifestDocument
	if len(lease.Manifest) > 0 && json.Unmarshal(lease.Manifest, &projection) == nil {
		current.projection = &projection
	}
	report.JobID = lease.JobID
	report.RunID = lease.RunID
	report.Status = "executed"

	outcome := current.execute(ctx)
	report.Outcome = outcome.outcome
	report.ErrorCode = outcome.code
	report.RunState = outcome.runState
	report.Error = outcome.err
	report.Heartbeats = current.heartbeats.Load()
	report.Sandbox = outcome.sandbox
	report.Artifacts = current.artifacts
	if outcome.failedStatus {
		report.Status = "failed"
	}
	if outcome.err != "" && outcome.outcome != "failed" {
		report.Status = "failed"
	}
	return finish()
}

type executionOutcome struct {
	outcome      string
	code         string
	err          string
	runState     string
	sandbox      *SandboxReport
	failedStatus bool
}

// execute is the whole attempt: verify, choose the plan, run, upload, register,
// propose, exit. Every early return still certifies the exit unless the token
// itself was lost.
func (r *runner) execute(ctx context.Context) executionOutcome {
	lease := r.lease
	if lease.ProtocolVersion != ProtocolVersion {
		return r.refuse(ctx, CodeProtocolMismatch, "the lease speaks protocol "+lease.ProtocolVersion)
	}
	_, manifest, err := r.verifyManifest(ctx)
	if err != nil {
		return r.refuse(ctx, CodeManifestUnavailable, err.Error())
	}
	r.manifest = manifest

	// F04A/T042/T043: legacy compatibility first. A graph whose single preset
	// python node binds the legacy `input#/file` shape is served by the unchanged
	// T033 single-python path; T033/T037 and T042's golden-graph regression depend
	// on it. The linear path requires the explicit `node:start#/artifact` facts
	// binding the T042 chain (and the T043 agent chain) documents.
	// F04B/T083: a definition with a structured branch region is served by the
	// graph scheduler. Everything else keeps the exact legacy/linear selection:
	// the T033 single-python path and the T042/T043 linear chain are unchanged.
	if hasGraphConstructs(manifest) {
		plan, planErr := buildGraphPlan(manifest)
		if planErr != nil {
			return r.refuse(ctx, codeOfPlanError(planErr), planErr.Error())
		}
		return r.executeGraph(ctx, plan)
	}
	if legacyPreferred(manifest) {
		return r.executeLegacy(ctx)
	}
	plan, planErr := buildLinearPlan(manifest)
	if planErr != nil {
		return r.refuse(ctx, codeOfPlanError(planErr), planErr.Error())
	}
	return r.executeLinear(ctx, plan)
}

// legacyPreferred reports whether the graph has exactly one executable preset
// python node bound the legacy way (`input#/file`). It is the documented
// compatibility selection between T033's single-python executor and the T042/T043
// linear chain.
func legacyPreferred(manifest *manifestDocument) bool {
	var python *manifestNode
	count, agents := 0, 0
	for index := range manifest.Definition.Nodes {
		node := &manifest.Definition.Nodes[index]
		if node.Type == "agent" {
			agents++
			continue
		}
		if node.Type != "python" {
			continue
		}
		var config struct {
			ToolVersion string `json:"tool_version"`
		}
		if json.Unmarshal(node.Config, &config) != nil || config.ToolVersion != PresetToolID {
			continue
		}
		count++
		python = node
	}
	if count != 1 || python == nil {
		return false
	}
	binding, ok := python.Inputs["file"]
	if ok && strings.TrimSpace(binding.Ref) != "" {
		base, _, _ := strings.Cut(binding.Ref, "#")
		if base == "input" {
			return true
		}
	}
	// Legacy fallback for a single-python graph the linear plan does not accept
	// (for example the T033 fixtures with no explicit chain edges). An agent
	// node never falls back: an invalid agent graph must be a typed refusal, not
	// a silently different execution.
	if agents == 0 {
		if _, err := buildLinearPlan(manifest); err != nil {
			return true
		}
	}
	return false
}

// executeLegacy is the T033 single-python attempt flow: exactly one task_input
// CSV/XLSX, the fixed preset tool, summary/CSV/diagnostics/log outputs. It is
// unchanged except for the extracted function boundary.
func (r *runner) executeLegacy(ctx context.Context) executionOutcome {
	lease := r.lease
	if _, err := presetNode(r.manifest); err != nil {
		var unsupported *unsupportedError
		if errors.As(err, &unsupported) {
			return r.refuse(ctx, unsupported.code, unsupported.message)
		}
		return r.refuse(ctx, CodeToolUnsupportedGraph, err.Error())
	}

	input, err := r.resolveInput(ctx)
	if err != nil {
		var unsupported *unsupportedError
		if errors.As(err, &unsupported) {
			return r.refuse(ctx, unsupported.code, unsupported.message)
		}
		return r.refuse(ctx, CodeToolInputInvalid, err.Error())
	}
	inputBytes, err := r.downloadInput(ctx, input)
	if err != nil {
		return r.refuse(ctx, CodeInputIntegrity, err.Error())
	}

	inputName, err := sandbox.InputNameForMediaType(input.MediaType)
	if err != nil {
		return r.refuse(ctx, CodeToolInputInvalid, err.Error())
	}
	inputDocument, err := buildInputDocument(input, inputName)
	if err != nil {
		return r.refuse(ctx, CodeToolInputInvalid, err.Error())
	}

	sandboxOutcome, sandboxErr := r.runSandbox(ctx, sandbox.Request{
		Program:         sandbox.ProgramTool,
		MediaType:       strings.ToLower(strings.TrimSpace(input.MediaType)),
		Input:           inputBytes,
		InputJSON:       inputDocument,
		ExecutionUnitID: r.options.ExecutionUnitID,
	}, r.heartbeatControl())

	sandboxReport := sandboxReportOf(sandboxOutcome)
	baseEvidence := r.exitEvidence(r.heartbeatCount(), sandboxOutcome, sandboxErr)

	switch {
	case errors.Is(sandboxErr, sandbox.ErrCancelled):
		// A cancellation is not a result. The unit is gone; if it never started,
		// the honest evidence is started=false (the job may be retried), and if it
		// did start the run is parked for T036's cancel arbitration rather than
		// inventing a terminal cancel here.
		if err := r.confirmExit(ctx, sandboxOutcome.SandboxStarted, baseEvidence); err != nil {
			return executionOutcome{outcome: "cancelled", code: "cancel_requested",
				err: err.Error(), sandbox: sandboxReport, failedStatus: true}
		}
		return executionOutcome{outcome: "cancelled", code: "cancel_requested",
			runState: r.readRunState(ctx), sandbox: sandboxReport}

	case errors.Is(sandboxErr, sandbox.ErrHeartbeat):
		// The lease is gone: the server already recovered the attempt. Do not
		// submit a result or an exit that would race the recovery.
		return executionOutcome{outcome: "error", code: CodeSandboxFailed,
			err:     "the attempt lease was lost while the sandbox ran: " + errString(sandboxErr),
			sandbox: sandboxReport, failedStatus: true}

	case errors.Is(sandboxErr, sandbox.ErrTimeout):
		return r.failRun(ctx, CodeToolTimeout,
			"the preset tool exceeded its sandbox timeout", sandboxOutcome, sandboxErr,
			baseEvidence, nil, nil)

	case errors.Is(sandboxErr, sandbox.ErrRootfsUnavailable),
		errors.Is(sandboxErr, sandbox.ErrToolUnavailable),
		errors.Is(sandboxErr, sandbox.ErrRunnerUnavailable),
		errors.Is(sandboxErr, sandbox.ErrSandboxRefused),
		errors.Is(sandboxErr, sandbox.ErrDriverReport),
		errors.Is(sandboxErr, sandbox.ErrDigestMismatch):
		return r.failRun(ctx, CodeSandboxUnavailable,
			"the sandbox could not be started or trusted", sandboxOutcome, sandboxErr,
			baseEvidence, nil, nil)

	case errors.Is(sandboxErr, sandbox.ErrOrphan):
		return r.failRun(ctx, CodeSandboxOrphan,
			"the sandbox cleanup could not prove that every process exited", sandboxOutcome,
			sandboxErr, baseEvidence, nil, nil)

	case sandboxErr != nil:
		return r.failRun(ctx, CodeSandboxFailed,
			"the sandbox failed", sandboxOutcome, sandboxErr, baseEvidence, nil, nil)
	}

	// The sandbox ran to completion and proved cleanup. Its exit code decides
	// what the tool said; the platform never trusts output.json's existence alone.
	switch sandboxOutcome.ExitCode {
	case 0:
	case 3:
		rejection, rejectionErr := tools.ParseRejectionPayload(sandboxOutcome.OutputJSON)
		if rejectionErr != nil {
			return r.failRun(ctx, CodeToolOutputInvalid,
				"the tool rejected the input with an unreadable document", sandboxOutcome,
				rejectionErr, baseEvidence, nil, nil)
		}
		diagnostics := diagnosticsDocument(rejection.Code, rejection.Message, rejection.Fix,
			rejectionLocation(rejection), input)
		result := r.failRun(ctx, rejection.Code, rejection.Message, sandboxOutcome, nil,
			baseEvidence, diagnostics, nil)
		return result
	default:
		return r.failRun(ctx, CodeToolCrash,
			"the preset tool exited with code "+strconv.Itoa(sandboxOutcome.ExitCode),
			sandboxOutcome, nil, baseEvidence, nil, nil)
	}

	data, names, err := sandbox.ParseOutputDocument(sandboxOutcome.OutputJSON)
	if err != nil {
		return r.failRun(ctx, CodeToolOutputInvalid, err.Error(), sandboxOutcome, err,
			baseEvidence, nil, nil)
	}
	collected := make(map[string][]byte, len(sandboxOutcome.Artifacts))
	for _, artifact := range sandboxOutcome.Artifacts {
		collected[artifact.Name] = artifact.Content
	}
	admitted, err := sandbox.AdmitArtifacts(names, collected)
	if err != nil {
		return r.failRun(ctx, CodeToolArtifactInvalid, err.Error(), sandboxOutcome, err,
			baseEvidence, nil, nil)
	}
	var summary *sandbox.Artifact
	for index := range admitted {
		if admitted[index].Name == "summary.json" {
			summary = &admitted[index]
		}
	}
	if summary == nil {
		return r.failRun(ctx, CodeToolOutputInvalid,
			"the tool produced no summary.json", sandboxOutcome, nil, baseEvidence, nil, nil)
	}
	if !sandbox.CanonicalEqual(summary.Content, data) {
		return r.failRun(ctx, CodeToolOutputInvalid,
			"summary.json does not match output.json.data", sandboxOutcome, nil, baseEvidence, nil, nil)
	}

	outputID, outcome := r.uploadOutputs(ctx, sandboxOutcome, admitted, summary)
	if outcome != nil {
		outcome.sandbox = sandboxReport
		return *outcome
	}
	// Register before proposing: the finalizer requires the output artifact to be
	// linked to this run (T031's admission re-checks it).
	for _, artifact := range r.artifacts {
		if artifact.ID == "" || artifact.Relation == "" {
			continue
		}
		if _, err := r.deps.Client.Register(ctx, lease.RunID, lease.AttemptToken,
			artifact.ID, artifact.Relation); err != nil {
			return r.failAfterStart(ctx, CodeSandboxFailed,
				"registering the run artifact failed: "+err.Error(), sandboxOutcome, baseEvidence)
		}
	}
	if err := r.deps.Client.SubmitResult(ctx, lease.RunID, lease.AttemptToken, Proposal{
		EventID:          uuid.NewString(),
		SuggestedState:   "succeeded",
		OutputArtifactID: outputID,
	}); err != nil {
		return r.failAfterStart(ctx, CodeSandboxFailed,
			"the succeeded proposal was refused: "+err.Error(), sandboxOutcome, baseEvidence)
	}
	if err := r.confirmExit(ctx, true, baseEvidence); err != nil {
		return executionOutcome{outcome: "error", code: CodeSandboxFailed,
			err:     "the exit confirmation was refused: " + err.Error(),
			sandbox: sandboxReport, failedStatus: true}
	}
	return executionOutcome{outcome: "succeeded", runState: r.readRunState(ctx), sandbox: sandboxReport}
}

// unsupportedError names a deterministic refusal with its stable code.
type unsupportedError struct {
	code    string
	message string
}

func (e *unsupportedError) Error() string { return e.message }

// refuse fails the run before any sandbox exists: a specific error code, a real
// result proposal and exit confirmation (started=true certifies the attempt;
// sandbox_started=false records that no unit ran, so a deterministic refusal is a
// terminal failure instead of an infinite requeue loop). The distinction is
// recorded in the T033 report.
func (r *runner) refuse(ctx context.Context, code, message string) executionOutcome {
	if captured, ok := captureGraphFailure(
		ctx, code, message, sandbox.Outcome{}, nil, nil, false, false,
	); ok {
		return captured
	}
	evidence := r.exitEvidence(0, sandbox.Outcome{}, nil)
	evidence["sandbox_started"] = false
	evidence["refusal_code"] = code
	if err := r.deps.Client.SubmitResult(ctx, r.lease.RunID, r.lease.AttemptToken, Proposal{
		EventID:        uuid.NewString(),
		SuggestedState: "failed",
		ErrorCode:      code,
	}); err != nil {
		return r.endRefusedProposal(ctx, err, code, message, true, evidence, nil)
	}
	if err := r.confirmExit(ctx, true, evidence); err != nil {
		return executionOutcome{outcome: "error", code: code,
			err: "the exit confirmation was refused: " + err.Error(), failedStatus: true}
	}
	return executionOutcome{outcome: "refused", code: code, err: message,
		runState: r.readRunState(ctx)}
}

// failRun uploads whatever safe evidence the failure produced, then submits the
// failed result and exit.
func (r *runner) failRun(
	ctx context.Context,
	code, message string,
	sandboxOutcome sandbox.Outcome,
	sandboxErr error,
	evidence map[string]any,
	diagnostics map[string]any,
	input *InputArtifact,
) executionOutcome {
	sandboxReport := sandboxReportOf(sandboxOutcome)
	if captured, ok := captureGraphFailure(
		ctx, code, message, sandboxOutcome, sandboxErr, evidence, true, true,
	); ok {
		// The graph scheduler records the failure evidence and commits the run
		// result after it stopped the other branches.
		return captured
	}
	r.uploadFailureEvidence(ctx, code, message, sandboxOutcome, diagnostics, input)
	proposal := Proposal{EventID: uuid.NewString(), SuggestedState: "failed", ErrorCode: code}
	if err := r.deps.Client.SubmitResult(ctx, r.lease.RunID, r.lease.AttemptToken, proposal); err != nil {
		return r.endRefusedProposal(ctx, err, code, message, true, evidence, sandboxReport)
	}
	if err := r.confirmExit(ctx, true, evidence); err != nil {
		return executionOutcome{outcome: "error", code: code,
			err:     "the exit confirmation was refused: " + err.Error(),
			sandbox: sandboxReport, failedStatus: true}
	}
	if sandboxErr != nil {
		message = message + ": " + errString(sandboxErr)
	}
	return executionOutcome{outcome: "failed", code: code, err: message,
		runState: r.readRunState(ctx), sandbox: sandboxReport}
}

// failAfterStart handles a platform-side refusal after a successful sandbox (for
// example a registration conflict): it fails the run with a real exit. The graph
// capture path is kept for the node implementations this ticket does not migrate;
// a scheduler that already holds the typed local outcome commits through
// commitFailedAfterStart instead.
func (r *runner) failAfterStart(
	ctx context.Context, code, message string, sandboxOutcome sandbox.Outcome, evidence map[string]any,
) executionOutcome {
	if captured, ok := captureGraphFailure(
		ctx, code, message, sandboxOutcome, nil, evidence, false, true,
	); ok {
		return captured
	}
	return r.commitFailedAfterStart(ctx, code, message, sandboxOutcome, evidence)
}

// commitFailedAfterStart submits the failed result and confirms the attempt exit.
// It is the scheduler-owned half with no hidden context lookup.
func (r *runner) commitFailedAfterStart(
	ctx context.Context, code, message string, sandboxOutcome sandbox.Outcome, evidence map[string]any,
) executionOutcome {
	sandboxReport := sandboxReportOf(sandboxOutcome)
	if err := r.deps.Client.SubmitResult(ctx, r.lease.RunID, r.lease.AttemptToken, Proposal{
		EventID: uuid.NewString(), SuggestedState: "failed", ErrorCode: code,
	}); err != nil {
		return r.endRefusedProposal(ctx, err, code, message, true, evidence, sandboxReport)
	}
	if err := r.confirmExit(ctx, true, evidence); err != nil {
		return executionOutcome{outcome: "error", code: code,
			err: "the exit confirmation was refused: " + err.Error(), sandbox: sandboxReport, failedStatus: true}
	}
	return executionOutcome{outcome: "failed", code: code, err: message,
		runState: r.readRunState(ctx), sandbox: sandboxReport}
}

// endRefusedProposal handles the platform refusing a result proposal while the
// attempt may still be live (409 run_state_conflict/run_terminal/stale_attempt, a
// deadline refusal or any other refusal of the proposal transaction).
//
// A refused proposal wrote nothing: the Run is not finalized. The attempt must
// still certify that it ended, because that exit is what lets the one finalizer
// commit the Run's real outcome in its fixed priority (cancel > deadline >
// proposal) and release the execution slot. Without it, a cancel that lands while
// a capability call is in flight leaves the Run cancelling forever with a live
// attempt and a leaked slot (F13B/T114 CANCEL-RACE-SLOT-LEAK).
//
// The other half is equally explicit: when the platform already considers the
// token dead (the Attempt exited, was revoked or a newer attempt owns the lease)
// the exit confirmation answers stale_attempt. That is a safe return - one
// attempt, a readable combined diagnostic, never a retry loop, a panic or a
// credential in the message.
func (r *runner) endRefusedProposal(
	ctx context.Context,
	refusal error, code, message string,
	started bool, evidence map[string]any, sandboxReport *SandboxReport,
) executionOutcome {
	r.logger.Printf("run_id=%s: the %s proposal was refused (%v); confirming the attempt exit",
		r.lease.RunID, code, refusal)
	if err := r.confirmExit(ctx, started, evidence); err != nil {
		return executionOutcome{outcome: "error", code: code,
			err: "the proposal was refused: " + refusal.Error() +
				"; the attempt exit could not be confirmed: " + err.Error(),
			sandbox: sandboxReport, failedStatus: true}
	}
	runState := r.readRunState(ctx)
	if runState == "cancelled" {
		// The finalizer's cancel priority committed cancelled: this attempt ended
		// because it was cancelled, not because of the failure it was reporting.
		return executionOutcome{outcome: "cancelled", code: "cancel_requested",
			runState: runState, sandbox: sandboxReport}
	}
	return executionOutcome{outcome: "failed", code: code,
		err: message + ": the proposal was refused: " + refusal.Error() +
			"; the attempt exit was confirmed",
		runState: runState, sandbox: sandboxReport}
}

// verifyManifest fetches the sealed manifest bytes, verifies the frozen hash and
// returns the parsed document. The JSONB projection on the lease is never used as
// the authority.
func (r *runner) verifyManifest(ctx context.Context) ([]byte, *manifestDocument, error) {
	lease := r.lease
	if strings.TrimSpace(lease.ManifestURL) == "" {
		return nil, nil, errors.New("the lease carries no manifest_url")
	}
	payload, err := r.deps.Client.Fetch(ctx, lease.ManifestURL)
	if err != nil {
		return nil, nil, err
	}
	sum := sha256.Sum256(payload)
	if hex.EncodeToString(sum[:]) != strings.ToLower(lease.ManifestSHA256) {
		return nil, nil, errors.New("the sealed manifest bytes do not match the lease hash")
	}
	var document manifestDocument
	if err := json.Unmarshal(payload, &document); err != nil {
		return nil, nil, errors.New("the sealed manifest is not valid JSON")
	}
	if document.WorkerImageDigest == "" || document.CompilerVersion == "" {
		return nil, nil, errors.New("the sealed manifest lacks its execution identity")
	}
	return payload, &document, nil
}

// manifestDocument is the subset of the frozen manifest this executor reads.
type manifestDocument struct {
	ManifestVersion      string                 `json:"manifest_version"`
	CompilerVersion      string                 `json:"compiler_version"`
	WorkerImageDigest    string                 `json:"worker_image_digest"`
	Definition           manifestDefinition     `json:"definition"`
	ToolArtifacts        []manifestToolArtifact `json:"tool_artifacts"`
	ModelProfileVersions []string               `json:"model_profile_versions"`
	// SubappVersions is the frozen fixed sub-application closure. A call_app node
	// or an agent's subapp_versions entry must be one of these ids; the spawn
	// endpoint re-proves the same membership again.
	SubappVersions []string `json:"subapp_versions"`
}

// manifestToolArtifact is one manifest.tool_artifacts entry (the id is the tool
// version id a python/agent config references).
type manifestToolArtifact struct {
	ID          string `json:"id"`
	ImageDigest string `json:"image_digest"`
}

type manifestDefinition struct {
	DSLVersion   string          `json:"dsl_version"`
	InputSchema  json.RawMessage `json:"input_schema"`
	OutputSchema json.RawMessage `json:"output_schema"`
	Nodes        []manifestNode  `json:"nodes"`
	Edges        []manifestEdge  `json:"edges"`
}

type manifestEdge struct {
	Source string `json:"source"`
	Target string `json:"target"`
	// Port is the condition exit port this edge leaves from. Omitted means the
	// contract's literal "default"; the executor normalises it at plan time.
	Port string `json:"port,omitempty"`
}

type manifestNode struct {
	ID           string                 `json:"id"`
	Type         string                 `json:"type"`
	Inputs       map[string]planBinding `json:"inputs"`
	OutputSchema json.RawMessage        `json:"output_schema"`
	Config       json.RawMessage        `json:"config"`
}

// presetNode locates exactly one python node and proves it names the preset tool.
func presetNode(manifest *manifestDocument) (*manifestNode, error) {
	pythonNodes := make([]manifestNode, 0, 1)
	for _, node := range manifest.Definition.Nodes {
		if node.Type == "python" {
			pythonNodes = append(pythonNodes, node)
		}
	}
	if len(pythonNodes) != 1 {
		return nil, &unsupportedError{
			code: CodeToolUnsupportedGraph,
			message: fmt.Sprintf("this executor runs exactly one preset python node; the manifest has %d",
				len(pythonNodes)),
		}
	}
	node := pythonNodes[0]
	var config struct {
		ToolVersion string `json:"tool_version"`
	}
	if err := json.Unmarshal(node.Config, &config); err != nil {
		return nil, &unsupportedError{code: CodeToolUnsupportedGraph,
			message: "the python node has no readable tool_version"}
	}
	if config.ToolVersion != PresetToolID {
		return nil, &unsupportedError{code: CodeToolUnsupportedTool,
			message: "the python node references tool " + config.ToolVersion + ", not the fixed preset"}
	}
	return &node, nil
}

// resolveInput picks exactly one CSV/XLSX task_input artifact of the run.
func (r *runner) resolveInput(ctx context.Context) (*InputArtifact, error) {
	inputs, err := r.deps.Inputs.Inputs(ctx, r.lease.RunID)
	if err != nil {
		return nil, err
	}
	candidates := make([]InputArtifact, 0, len(inputs))
	for _, input := range inputs {
		if input.Purpose != "task_input" {
			continue
		}
		mediaType := strings.ToLower(strings.TrimSpace(input.MediaType))
		if mediaType != "text/csv" &&
			mediaType != "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" {
			continue
		}
		candidates = append(candidates, input)
	}
	switch len(candidates) {
	case 0:
		return nil, &unsupportedError{code: CodeToolInputInvalid,
			message: "the run has no ready CSV/XLSX task_input artifact"}
	case 1:
		return &candidates[0], nil
	default:
		return nil, &unsupportedError{code: CodeToolInputAmbiguous,
			message: fmt.Sprintf("the run has %d CSV/XLSX task_input artifacts; exactly one is required",
				len(candidates))}
	}
}

// downloadInput fetches the input through the run-scoped signed link and proves
// the bytes are the ones the database registered (size and sha256).
func (r *runner) downloadInput(ctx context.Context, input *InputArtifact) ([]byte, error) {
	if input.SizeBytes <= 0 || input.SizeBytes > MaxInputBytes {
		return nil, fmt.Errorf("the input artifact size %d is outside the tool protocol", input.SizeBytes)
	}
	payload, err := r.deps.Client.Download(ctx, r.lease.RunID, r.lease.AttemptToken, input.ID)
	if err != nil {
		return nil, err
	}
	if int64(len(payload)) != input.SizeBytes {
		return nil, errors.New("the downloaded input size does not match the registered artifact")
	}
	sum := sha256.Sum256(payload)
	if !strings.EqualFold(hex.EncodeToString(sum[:]), input.SHA256) {
		return nil, errors.New("the downloaded input sha256 does not match the registered artifact")
	}
	return payload, nil
}

// inputDocument is the tool's input.json (WORKFLOW-SPEC §5).
type inputDocument struct {
	Tool     string             `json:"tool"`
	File     string             `json:"file"`
	Artifact inputArtifactFacts `json:"artifact"`
}

type inputArtifactFacts struct {
	ArtifactID string `json:"artifact_id"`
	MediaType  string `json:"media_type"`
	SHA256     string `json:"sha256"`
	SizeBytes  int64  `json:"size_bytes"`
}

func buildInputDocument(input *InputArtifact, fileName string) ([]byte, error) {
	document := inputDocument{
		Tool: PresetToolName,
		File: fileName,
		Artifact: inputArtifactFacts{
			ArtifactID: input.ID,
			MediaType:  input.MediaType,
			SHA256:     input.SHA256,
			SizeBytes:  input.SizeBytes,
		},
	}
	return json.Marshal(document)
}

// heartbeatControl wires the executor's sandbox ticker to the lease heartbeat.
func (r *runner) heartbeatControl() sandbox.Control {
	return sandbox.Control{
		HeartbeatInterval: r.options.HeartbeatTick,
		Heartbeat: func(ctx context.Context) (sandbox.HeartbeatState, error) {
			reply, err := r.deps.Client.Heartbeat(
				ctx, r.lease.RunID, r.lease.AttemptToken, r.options.ExecutionUnitID,
			)
			if err != nil {
				return sandbox.HeartbeatState{}, err
			}
			r.heartbeats.Add(1)
			return sandbox.HeartbeatState{CancelRequested: reply.CancelRequested}, nil
		},
	}
}

func (r *runner) heartbeatCount() int64 { return r.heartbeats.Load() }

// runSandbox is a seam for tests: the real call goes to the sandbox executor.
func (r *runner) runSandbox(
	ctx context.Context, request sandbox.Request, control sandbox.Control,
) (sandbox.Outcome, error) {
	return r.deps.Sandbox.Run(ctx, request, control)
}

// exitEvidence is the T031-required identity evidence plus the sandbox facts.
func (r *runner) exitEvidence(heartbeats int64, outcome sandbox.Outcome, sandboxErr error) map[string]any {
	lease := r.lease
	imageDigest := ""
	identitySource := "none"
	if r.manifest != nil {
		imageDigest = r.manifest.WorkerImageDigest
		identitySource = "sealed_manifest"
	} else if r.projection != nil {
		// The sealed bytes could not be verified: the projection is the same
		// snapshot row, and a refusal still has to certify which image it did not
		// run. T031 re-checks the value against the snapshot before any write.
		imageDigest = r.projection.WorkerImageDigest
		identitySource = "lease_projection"
	}
	evidence := map[string]any{
		"started":               true,
		"protocol_version":      lease.ProtocolVersion,
		"manifest_sha256":       strings.ToLower(lease.ManifestSHA256),
		"image_digest":          imageDigest,
		"identity_source":       identitySource,
		"execution_unit_id":     r.options.ExecutionUnitID,
		"sandbox_id":            outcome.SandboxID,
		"sandbox_started":       outcome.SandboxStarted,
		"sandbox_rootfs_digest": outcome.RootfsDigest,
		"sandbox_exit_code":     outcome.ExitCode,
		"sandbox_cleanup": map[string]any{
			"sandbox_list_matches": outcome.Cleanup.SandboxListMatches,
			"pid":                  outcome.Cleanup.PID,
			"pid_alive":            outcome.Cleanup.PIDAlive,
			"ps_matches":           outcome.Cleanup.PSMatches,
			"workspace_removed":    outcome.Cleanup.WorkspaceRemoved,
		},
		"sandbox_pid":              outcome.Cleanup.PID,
		"sandbox_started_at":       outcome.StartedAt.UTC().Format(time.RFC3339),
		"sandbox_cleanup_verified": outcome.CleanupVerified,
		"heartbeats":               heartbeats,
		"db_writes_drained":        true,
	}
	if sandboxErr != nil {
		evidence["sandbox_error"] = errString(sandboxErr)
	}
	return evidence
}

func (r *runner) confirmExit(ctx context.Context, started bool, evidence map[string]any) error {
	evidence["started"] = started
	return r.deps.Client.ConfirmExit(ctx, r.lease.AttemptToken, r.options.ExecutionUnitID, evidence)
}

func (r *runner) readRunState(ctx context.Context) string {
	state, _, err := r.deps.Client.RunState(ctx, r.lease.RunID)
	if err != nil {
		return ""
	}
	return state
}

// uploadOutputs uploads and waits for every produced artifact, then builds the
// registered artifact list. A nil outcome means success.
func (r *runner) uploadOutputs(
	ctx context.Context,
	outcome sandbox.Outcome,
	admitted []sandbox.Artifact,
	summary *sandbox.Artifact,
) (string, *executionOutcome) {
	outputID := ""
	for _, artifact := range admitted {
		purpose := "node_output"
		relation := "artifact"
		if artifact.Name == summary.Name {
			purpose = "final_output"
			relation = "output"
		}
		mediaType := mediaTypeForArtifact(artifact.Name)
		artifactID, uploadErr := r.uploadArtifact(ctx, purpose, mediaType, artifact.Content, relation)
		if uploadErr != nil {
			failure := r.failAfterStart(ctx, CodeSandboxFailed,
				"uploading "+artifact.Name+" failed: "+uploadErr.Error(), outcome,
				r.exitEvidence(r.heartbeatCount(), outcome, nil))
			return "", &failure
		}
		if artifact.Name == summary.Name {
			outputID = artifactID
		}
	}
	// stdout/stderr is a capped log, never protocol output and never run data;
	// it is still uploaded so an operator can see what the tool printed.
	logContent := buildLog(outcome.Stdout, outcome.Stderr, r.options.MaxLogBytes)
	if _, err := r.uploadArtifact(ctx, "log", "text/plain", logContent, "log"); err != nil {
		r.logger.Printf("run_id=%s: uploading the sandbox log failed: %v", r.lease.RunID, err)
	}
	if outputID == "" {
		failure := r.failAfterStart(ctx, CodeToolOutputInvalid,
			"no summary.json artifact was produced", outcome,
			r.exitEvidence(r.heartbeatCount(), outcome, nil))
		return "", &failure
	}
	return outputID, nil
}

// uploadArtifact is create -> PUT -> complete -> wait-for-ready -> remember.
func (r *runner) uploadArtifact(
	ctx context.Context, purpose, mediaType string, content []byte, relation string,
) (string, error) {
	if len(content) == 0 {
		return "", errors.New("refusing to upload an empty artifact")
	}
	if int64(len(content)) > sandbox.MaxArtifactBytes {
		return "", errors.New("the artifact exceeds the per-file cap")
	}
	ticket, err := r.deps.Client.CreateUpload(ctx, r.lease.RunID, r.lease.AttemptToken, UploadRequest{
		MediaType: mediaType,
		SizeBytes: int64(len(content)),
		Purpose:   purpose,
	})
	if err != nil {
		return "", err
	}
	if err := r.deps.Client.PutBytes(ctx, ticket.URL, content); err != nil {
		return "", err
	}
	sum := sha256.Sum256(content)
	view, err := r.deps.Client.Complete(ctx, r.lease.RunID, r.lease.AttemptToken,
		ticket.ArtifactID, hex.EncodeToString(sum[:]))
	if err != nil {
		return "", err
	}
	ready, err := r.waitReady(ctx, ticket.ArtifactID, view)
	if err != nil {
		return "", err
	}
	r.artifactsMu.Lock()
	r.artifacts = append(r.artifacts, ArtifactReport{
		ID:        ticket.ArtifactID,
		Purpose:   purpose,
		Relation:  relation,
		State:     ready.State,
		SizeBytes: ready.SizeBytes,
	})
	r.artifactsMu.Unlock()
	return ticket.ArtifactID, nil
}

// waitReady polls the run-scoped status until the finalize guard activates the
// artifact, heartbeating between polls so the lease does not expire.
func (r *runner) waitReady(
	ctx context.Context, artifactID string, current ArtifactView,
) (ArtifactView, error) {
	deadline := time.Now().Add(r.options.ResultWait)
	view := current
	for {
		switch view.State {
		case "ready":
			return view, nil
		case "quarantined", "deleted":
			return view, fmt.Errorf("the artifact is %s", view.State)
		}
		if time.Now().After(deadline) {
			return view, errors.New("the artifact did not become ready before the wait deadline")
		}
		if err := ctx.Err(); err != nil {
			return view, err
		}
		if _, err := r.deps.Client.Heartbeat(
			ctx, r.lease.RunID, r.lease.AttemptToken, r.options.ExecutionUnitID,
		); err != nil {
			return view, err
		}
		r.heartbeats.Add(1)
		select {
		case <-ctx.Done():
			return view, ctx.Err()
		case <-time.After(r.options.PollInterval):
		}
		next, err := r.deps.Client.Status(ctx, r.lease.RunID, r.lease.AttemptToken, artifactID)
		if err != nil {
			return view, err
		}
		view = next
	}
}

// uploadFailureEvidence uploads the safe diagnostics/log of a failed attempt and
// links them to the run. It is best effort: a storage failure must not replace the
// real failure code.
//
// F13A/T056 links the failure evidence through the same Register path as a
// success artifact, so a per-case result can offer the actual diagnostics/log
// attachments of a failed case instead of only a state code. The bytes stay the
// attempt's own: Register re-proves the live attempt token.
func (r *runner) uploadFailureEvidence(
	ctx context.Context,
	code, message string,
	outcome sandbox.Outcome,
	diagnostics map[string]any,
	input *InputArtifact,
) {
	if outcome.SandboxStarted {
		// The log is uploaded even when the streams are empty: the header proves
		// the platform captured them, and an empty log is a fact too.
		logContent := buildLog(outcome.Stdout, outcome.Stderr, r.options.MaxLogBytes)
		r.uploadFailureArtifact(ctx, "log", "text/plain", logContent, "log")
	}
	document := diagnostics
	if document == nil {
		document = diagnosticsDocument(code, message, "", nil, input)
	}
	payload, err := json.Marshal(document)
	if err != nil {
		return
	}
	payload = append(payload, '\n')
	if len(payload) > sandbox.MaxArtifactBytes {
		return
	}
	r.uploadFailureArtifact(ctx, "node_output", "application/json", payload, "artifact")
}

// uploadFailureArtifact uploads one piece of failure evidence and registers it to
// the run. Both halves are best effort and separately logged: a missing link must
// not mask the failure code, and it must not look like a produced result either.
func (r *runner) uploadFailureArtifact(
	ctx context.Context, purpose, mediaType string, content []byte, relation string,
) {
	artifactID, err := r.uploadArtifact(ctx, purpose, mediaType, content, relation)
	if err != nil {
		r.logger.Printf("run_id=%s: uploading the %s failure evidence failed: %v",
			r.lease.RunID, purpose, err)
		return
	}
	if _, err := r.deps.Client.Register(
		ctx, r.lease.RunID, r.lease.AttemptToken, artifactID, relation,
	); err != nil {
		r.logger.Printf("run_id=%s: registering the %s failure evidence failed: %v",
			r.lease.RunID, purpose, err)
	}
}

// diagnosticsDocument is safe failure metadata: no path, object key, input bytes
// or credential.
func diagnosticsDocument(
	code, message, fix string,
	location map[string]any,
	input *InputArtifact,
) map[string]any {
	document := map[string]any{
		"diagnostics_version": 1,
		"tool":                PresetToolName,
		"status":              "failed",
		"error_code":          code,
		"message":             message,
	}
	if fix != "" {
		document["fix"] = fix
	}
	if len(location) > 0 {
		document["location"] = location
	}
	if input != nil {
		document["input"] = map[string]any{
			"artifact_id": input.ID,
			"media_type":  input.MediaType,
			"size_bytes":  input.SizeBytes,
			"sha256":      input.SHA256,
		}
	}
	return document
}

func rejectionLocation(rejection tools.Rejection) map[string]any {
	location := map[string]any{}
	if rejection.Row != nil {
		location["row"] = *rejection.Row
	}
	if rejection.Column != "" {
		location["column"] = rejection.Column
	}
	if rejection.Cell != "" {
		location["cell"] = rejection.Cell
	}
	return location
}

// buildLog caps and prefixes the sandbox's captured streams. It is a log, never
// protocol output: the tool's results come from output.json only.
func buildLog(stdout, stderr []byte, limit int) []byte {
	if limit <= 0 {
		limit = MaxLogBytes
	}
	content := make([]byte, 0, len(stdout)+len(stderr)+64)
	content = append(content, "stdout:\n"...)
	content = append(content, stdout...)
	content = append(content, "\nstderr:\n"...)
	content = append(content, stderr...)
	content = append(content, '\n')
	if len(content) > limit {
		content = content[:limit]
	}
	return content
}

func mediaTypeForArtifact(name string) string {
	switch {
	case strings.HasSuffix(name, ".json"):
		return "application/json"
	case strings.HasSuffix(name, ".csv"):
		return "text/csv"
	case strings.HasSuffix(name, ".xlsx"):
		return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
	default:
		return "text/plain"
	}
}

func sandboxReportOf(outcome sandbox.Outcome) *SandboxReport {
	if outcome.SandboxID == "" {
		return nil
	}
	return &SandboxReport{
		SandboxID:    outcome.SandboxID,
		RootfsDigest: outcome.RootfsDigest,
		Started:      outcome.SandboxStarted,
		ExitCode:     outcome.ExitCode,
		TimedOut:     outcome.TimedOut,
		Cancelled:    outcome.Cancelled,
		DurationMS:   outcome.Duration.Milliseconds(),
		Cleanup:      outcome.Cleanup,
	}
}

func errString(err error) string {
	if err == nil {
		return ""
	}
	return err.Error()
}

type discardWriter struct{}

func (discardWriter) Write(payload []byte) (int, error) { return len(payload), nil }
