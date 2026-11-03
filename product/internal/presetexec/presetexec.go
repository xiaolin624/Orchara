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
	"sync/atomic"
	"time"

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
}

// InputArtifact is one run input the trusted database identity resolved.
type InputArtifact struct {
	ID        string
	Purpose   string
	MediaType string
	SizeBytes int64
	SHA256    string
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
}

// InputSource resolves the run's linked input artifacts. The supervisor's
// implementation is a read-only database query; the bytes still move through the
// signed link the Client mints.
type InputSource interface {
	Inputs(ctx context.Context, runID string) ([]InputArtifact, error)
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
	Options Options
	Logger  *log.Logger
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
	artifacts  []ArtifactReport
	logger     *log.Logger
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

// execute is the whole attempt: verify, resolve, run, upload, register, propose,
// exit. Every early return still certifies the exit unless the token itself was
// lost.
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
	if _, err := presetNode(manifest); err != nil {
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
	evidence := r.exitEvidence(0, sandbox.Outcome{}, nil)
	evidence["sandbox_started"] = false
	evidence["refusal_code"] = code
	if err := r.deps.Client.SubmitResult(ctx, r.lease.RunID, r.lease.AttemptToken, Proposal{
		EventID:        uuid.NewString(),
		SuggestedState: "failed",
		ErrorCode:      code,
	}); err != nil {
		return executionOutcome{outcome: "error", code: code,
			err: "the failed proposal was refused: " + err.Error(), failedStatus: true}
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
	r.uploadFailureEvidence(ctx, code, message, sandboxOutcome, diagnostics, input)
	proposal := Proposal{EventID: uuid.NewString(), SuggestedState: "failed", ErrorCode: code}
	if err := r.deps.Client.SubmitResult(ctx, r.lease.RunID, r.lease.AttemptToken, proposal); err != nil {
		return executionOutcome{outcome: "error", code: code,
			err:     "the failed proposal was refused: " + err.Error(),
			sandbox: sandboxReport, failedStatus: true}
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
// example a registration conflict): it fails the run with a real exit.
func (r *runner) failAfterStart(
	ctx context.Context, code, message string, sandboxOutcome sandbox.Outcome, evidence map[string]any,
) executionOutcome {
	sandboxReport := sandboxReportOf(sandboxOutcome)
	if err := r.deps.Client.SubmitResult(ctx, r.lease.RunID, r.lease.AttemptToken, Proposal{
		EventID: uuid.NewString(), SuggestedState: "failed", ErrorCode: code,
	}); err != nil {
		return executionOutcome{outcome: "error", code: code,
			err: "the failed proposal was refused: " + err.Error(), sandbox: sandboxReport, failedStatus: true}
	}
	if err := r.confirmExit(ctx, true, evidence); err != nil {
		return executionOutcome{outcome: "error", code: code,
			err: "the exit confirmation was refused: " + err.Error(), sandbox: sandboxReport, failedStatus: true}
	}
	return executionOutcome{outcome: "failed", code: code, err: message,
		runState: r.readRunState(ctx), sandbox: sandboxReport}
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
	ManifestVersion   string             `json:"manifest_version"`
	CompilerVersion   string             `json:"compiler_version"`
	WorkerImageDigest string             `json:"worker_image_digest"`
	Definition        manifestDefinition `json:"definition"`
}

type manifestDefinition struct {
	Nodes []manifestNode `json:"nodes"`
}

type manifestNode struct {
	ID     string          `json:"id"`
	Type   string          `json:"type"`
	Config json.RawMessage `json:"config"`
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
	r.artifacts = append(r.artifacts, ArtifactReport{
		ID:        ticket.ArtifactID,
		Purpose:   purpose,
		Relation:  relation,
		State:     ready.State,
		SizeBytes: ready.SizeBytes,
	})
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

// uploadFailureEvidence uploads the safe diagnostics/log of a failed attempt. It
// is best effort: a storage failure must not replace the real failure code.
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
		if _, err := r.uploadArtifact(ctx, "log", "text/plain", logContent, "log"); err != nil {
			r.logger.Printf("run_id=%s: failed to upload the sandbox log: %v", r.lease.RunID, err)
		}
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
	if _, err := r.uploadArtifact(ctx, "node_output", "application/json", payload, "artifact"); err != nil {
		r.logger.Printf("run_id=%s: failed to upload diagnostics: %v", r.lease.RunID, err)
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
