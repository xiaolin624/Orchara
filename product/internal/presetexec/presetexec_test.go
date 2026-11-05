package presetexec

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"sync"
	"testing"
	"time"

	"orchara/platform/internal/capabilities"
	"orchara/platform/internal/sandbox"
)

// ---------------------------------------------------------------------------
// fakes: the executor's protocol seams
// ---------------------------------------------------------------------------

type recordedUpload struct {
	Request  UploadRequest
	Content  []byte
	Artifact string
}

type recordedProposal struct {
	Proposal
}

type recordedExit struct {
	AttemptID       string
	ExecutionUnitID string
	Evidence        map[string]any
}

type recordedRegistration struct {
	ArtifactID string
	Relation   string
}

type fakeClient struct {
	mu sync.Mutex

	lease         *Lease
	manifest      []byte
	inputs        []InputArtifact
	inputBytes    []byte
	runInput      []byte
	runInputErr   error
	runState      string
	runError      string
	cancelAt      int // heartbeat number that reports cancel; 0 = never
	heartbeats    int
	uploadSeq     int
	uploads       []recordedUpload
	proposals     []recordedProposal
	exits         []recordedExit
	registers     []recordedRegistration
	nodeEvents    []recordedNodeEvent
	nodeResults   []NodeResult
	statuses      map[string]ArtifactView
	registerErr   error
	statusErr     error
	nodeEventErr  error
	nodeResultErr error

	// scripted capability seam (agent tests)
	invokeCapability  func(capabilities.Input) (*capabilities.Result, error)
	reportCapability  func(capabilities.ResultReport) (*capabilities.Result, error)
	capabilityInputs  []capabilities.Input
	capabilityReports []capabilities.ResultReport

	// scripted delegation seam (call_app / agent subapp tests)
	spawnChild    func(SpawnChildRequest) (*ChildRun, error)
	childResult   func(childRunID string) (*ChildResult, error)
	spawnRequests []SpawnChildRequest
	childReads    []string

	// scripted approval seam (T087 approval-node tests)
	nodeApprovalScript   []*NodeApproval
	nodeApprovalErr      error
	nodeApprovalRequests []NodeApprovalRequest
}

// PrepareNodeApproval implements Client: the executor asks the platform for the
// one server-built approval; the scripted answer is the platform's row.
func (f *fakeClient) PrepareNodeApproval(
	_ context.Context, _ string, _ string, request NodeApprovalRequest,
) (*NodeApproval, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.nodeApprovalRequests = append(f.nodeApprovalRequests, request)
	if f.nodeApprovalErr != nil {
		return nil, f.nodeApprovalErr
	}
	if len(f.nodeApprovalScript) == 0 {
		return nil, errors.New("no scripted node approval")
	}
	next := f.nodeApprovalScript[0]
	f.nodeApprovalScript = f.nodeApprovalScript[1:]
	return next, nil
}

// SpawnChild implements Client: the executor never creates a Run itself.
func (f *fakeClient) SpawnChild(
	_ context.Context, _ string, _ string, request SpawnChildRequest,
) (*ChildRun, error) {
	f.mu.Lock()
	f.spawnRequests = append(f.spawnRequests, request)
	script := f.spawnChild
	f.mu.Unlock()
	if script == nil {
		return nil, errors.New("no scripted child")
	}
	return script(request)
}

// ChildResult implements Client: the terminal child read.
func (f *fakeClient) ChildResult(
	_ context.Context, _ string, _ string, childRunID string,
) (*ChildResult, error) {
	f.mu.Lock()
	f.childReads = append(f.childReads, childRunID)
	script := f.childResult
	f.mu.Unlock()
	if script == nil {
		return nil, errors.New("no scripted child result")
	}
	return script(childRunID)
}

// recordedNodeEvent is one observed node.status transition.
type recordedNodeEvent struct {
	NodeID string
	State  string
}

func (f *fakeClient) SubmitNodeStatus(
	_ context.Context, _ string, _ string, nodeID, state string,
) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.nodeEventErr != nil {
		return f.nodeEventErr
	}
	f.nodeEvents = append(f.nodeEvents, recordedNodeEvent{NodeID: nodeID, State: state})
	return nil
}

func (f *fakeClient) RecordNodeResults(
	_ context.Context, _ string, _ string, results []NodeResult,
) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.nodeResultErr != nil {
		return f.nodeResultErr
	}
	f.nodeResults = append(f.nodeResults, results...)
	return nil
}

func (f *fakeClient) Claim(context.Context, string, string) (*Lease, error) {
	return f.lease, nil
}

func (f *fakeClient) Heartbeat(context.Context, string, string, string) (Heartbeat, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.heartbeats++
	if f.cancelAt > 0 && f.heartbeats >= f.cancelAt {
		return Heartbeat{CancelRequested: true}, nil
	}
	return Heartbeat{}, nil
}

func (f *fakeClient) Fetch(_ context.Context, link string) ([]byte, error) {
	if f.lease != nil && link == f.lease.InputURL {
		return f.runInput, f.runInputErr
	}
	return f.manifest, nil
}

func (f *fakeClient) PutBytes(_ context.Context, _ string, content []byte) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	if len(f.uploads) == 0 {
		return errors.New("put without an upload")
	}
	f.uploads[len(f.uploads)-1].Content = append([]byte(nil), content...)
	return nil
}

func (f *fakeClient) SubmitResult(_ context.Context, _ string, _ string, proposal Proposal) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.proposals = append(f.proposals, recordedProposal{Proposal: proposal})
	return nil
}

func (f *fakeClient) ConfirmExit(
	_ context.Context, attemptID, executionUnitID string, evidence map[string]any,
) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	copied := make(map[string]any, len(evidence))
	for key, value := range evidence {
		copied[key] = value
	}
	f.exits = append(f.exits, recordedExit{
		AttemptID: attemptID, ExecutionUnitID: executionUnitID, Evidence: copied,
	})
	return nil
}

func (f *fakeClient) CreateUpload(
	_ context.Context, _ string, _ string, request UploadRequest,
) (UploadTicket, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.uploadSeq++
	artifactID := fmt.Sprintf("artifact-%d", f.uploadSeq)
	f.uploads = append(f.uploads, recordedUpload{Request: request, Artifact: artifactID})
	return UploadTicket{ArtifactID: artifactID, URL: "http://object.invalid/put/" + artifactID}, nil
}

func (f *fakeClient) Complete(
	_ context.Context, _ string, _ string, artifactID, _ string,
) (ArtifactView, error) {
	return ArtifactView{ID: artifactID, State: "scanning", MediaType: "application/json"}, nil
}

func (f *fakeClient) Status(
	_ context.Context, _ string, _ string, artifactID string,
) (ArtifactView, error) {
	if f.statusErr != nil {
		return ArtifactView{}, f.statusErr
	}
	if view, ok := f.statuses[artifactID]; ok {
		return view, nil
	}
	return ArtifactView{ID: artifactID, State: "ready", MediaType: "application/json"}, nil
}

func (f *fakeClient) Register(
	_ context.Context, _ string, _ string, artifactID, relation string,
) (ArtifactView, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.registerErr != nil {
		return ArtifactView{}, f.registerErr
	}
	f.registers = append(f.registers, recordedRegistration{ArtifactID: artifactID, Relation: relation})
	return ArtifactView{ID: artifactID, State: "ready"}, nil
}

func (f *fakeClient) Download(context.Context, string, string, string) ([]byte, error) {
	return f.inputBytes, nil
}

func (f *fakeClient) RunState(context.Context, string) (string, string, error) {
	return f.runState, f.runError, nil
}

// InvokeCapability implements Client with the test's scripted capability seam.
func (f *fakeClient) InvokeCapability(
	_ context.Context, _ string, _ string, input capabilities.Input,
) (*capabilities.Result, error) {
	f.mu.Lock()
	f.capabilityInputs = append(f.capabilityInputs, input)
	handler := f.invokeCapability
	f.mu.Unlock()
	if handler == nil {
		return nil, errors.New("no capability handler is scripted")
	}
	return handler(input)
}

// ReportCapabilityResult implements Client with the test's scripted seam.
func (f *fakeClient) ReportCapabilityResult(
	_ context.Context, _ string, _ string, report capabilities.ResultReport,
) (*capabilities.Result, error) {
	f.mu.Lock()
	f.capabilityReports = append(f.capabilityReports, report)
	handler := f.reportCapability
	f.mu.Unlock()
	if handler == nil {
		return &capabilities.Result{
			OperationID: report.OperationID, State: report.State,
		}, nil
	}
	return handler(report)
}

func (f *fakeClient) uploadsByPurpose() map[string]recordedUpload {
	result := map[string]recordedUpload{}
	for _, upload := range f.uploads {
		result[upload.Request.Purpose] = upload
	}
	return result
}

type fakeInputs struct {
	inputs []InputArtifact
	err    error
}

func (f *fakeInputs) Inputs(context.Context, string) ([]InputArtifact, error) {
	return f.inputs, f.err
}

type fakeSandbox struct {
	mu        sync.Mutex
	outcome   sandbox.Outcome
	err       error
	calls     int
	last      sandbox.Request
	interrupt func(ctx context.Context) (sandbox.Outcome, error)
}

func (f *fakeSandbox) Run(
	ctx context.Context, request sandbox.Request, _ sandbox.Control,
) (sandbox.Outcome, error) {
	f.mu.Lock()
	f.calls++
	f.last = request
	f.mu.Unlock()
	if f.interrupt != nil {
		return f.interrupt(ctx)
	}
	return f.outcome, f.err
}

// ---------------------------------------------------------------------------
// fixtures
// ---------------------------------------------------------------------------

func manifestBytes(t *testing.T, toolID string, pythonNodes int) ([]byte, string) {
	t.Helper()
	nodes := make([]map[string]any, 0, pythonNodes+2)
	nodes = append(nodes, map[string]any{"id": "start", "type": "start", "config": map[string]any{}})
	for index := 0; index < pythonNodes; index++ {
		nodes = append(nodes, map[string]any{
			"id":     fmt.Sprintf("stats-%d", index),
			"type":   "python",
			"config": map[string]any{"tool_version": toolID},
		})
	}
	nodes = append(nodes, map[string]any{"id": "end", "type": "end", "config": map[string]any{}})
	document := map[string]any{
		"manifest_version":    "orchara-execution-manifest/1.0",
		"compiler_version":    CompilerVersion,
		"worker_image_digest": "sha256:" + strings.Repeat("aa", 32),
		"definition":          map[string]any{"nodes": nodes},
	}
	payload, err := json.Marshal(document)
	if err != nil {
		t.Fatal(err)
	}
	sum := sha256.Sum256(payload)
	return payload, hex.EncodeToString(sum[:])
}

const salesCSV = "order_id,date,region,amount\no-1,2026-01-02,east,120.50\no-2,2026-01-02,east,79.50\n"

func successLease(t *testing.T, toolID string, pythonNodes int) (*Lease, []byte, string) {
	t.Helper()
	manifest, hash := manifestBytes(t, toolID, pythonNodes)
	return &Lease{
		JobID:           "job-1",
		RunID:           "11111111-2222-4333-8444-555555555555",
		AttemptToken:    "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
		ProtocolVersion: ProtocolVersion,
		ManifestSHA256:  hash,
		ManifestURL:     "http://object.invalid/get/manifest",
	}, manifest, hash
}

func inputArtifact() InputArtifact {
	sum := sha256.Sum256([]byte(salesCSV))
	return InputArtifact{
		ID:        "99999999-8888-4777-8666-555555555555",
		Purpose:   "task_input",
		MediaType: "text/csv",
		SizeBytes: int64(len(salesCSV)),
		SHA256:    hex.EncodeToString(sum[:]),
	}
}

func successSandbox() *fakeSandbox {
	output := []byte(`{"data":{"total":"200.00","regions":{"east":"200.00"},"order_count":2},` +
		`"artifacts":["summary.json","summary.csv"]}`)
	summary := []byte(`{"order_count":2,"regions":{"east":"200.00"},"total":"200.00"}`)
	csv := []byte("region,total\neast,200.00\n")
	return &fakeSandbox{outcome: sandbox.Outcome{
		SandboxID:       "unit-test-1",
		ExecutionUnitID: "unit-test-1",
		RootfsDigest:    "sha256:" + strings.Repeat("cd", 32),
		SandboxStarted:  true,
		ExitCode:        0,
		Cleanup:         sandbox.CleanupFacts{PID: 42, WorkspaceRemoved: true},
		CleanupVerified: true,
		OutputJSON:      output,
		Stdout:          []byte("ok\n"),
		Artifacts: []sandbox.Artifact{
			{Name: "summary.json", Content: summary},
			{Name: "summary.csv", Content: csv},
		},
	}}
}

func baseDeps(t *testing.T, client *fakeClient, inputs *fakeInputs, box *fakeSandbox) Deps {
	t.Helper()
	return Deps{
		Client:  client,
		Inputs:  inputs,
		Sandbox: box,
		Options: Options{
			WorkerID:        "test-supervisor",
			ExecutionUnitID: "unit-test-1",
			ResultWait:      500 * time.Millisecond,
			PollInterval:    10 * time.Millisecond,
			HeartbeatTick:   20 * time.Millisecond,
		},
	}
}

func TestSucceededRunRegistersAndProposes(t *testing.T) {
	lease, manifest, _ := successLease(t, PresetToolID, 1)
	input := inputArtifact()
	client := &fakeClient{
		lease: lease, manifest: manifest, inputs: []InputArtifact{input},
		inputBytes: []byte(salesCSV), runState: "succeeded",
	}
	box := successSandbox()
	report := Run(context.Background(), baseDeps(t, client, &fakeInputs{inputs: client.inputs}, box))
	if report.Status != "executed" || report.Outcome != "succeeded" {
		t.Fatalf("report = %+v", report)
	}
	if box.calls != 1 {
		t.Fatalf("sandbox calls = %d", box.calls)
	}
	if box.last.MediaType != "text/csv" || len(box.last.Input) != len(salesCSV) {
		t.Fatalf("sandbox request = %+v", box.last)
	}
	var document struct {
		Tool     string         `json:"tool"`
		File     string         `json:"file"`
		Artifact map[string]any `json:"artifact"`
	}
	if err := json.Unmarshal(box.last.InputJSON, &document); err != nil {
		t.Fatalf("input.json: %v", err)
	}
	if document.Tool != PresetToolName || document.File != "input.csv" ||
		document.Artifact["artifact_id"] != input.ID || document.Artifact["sha256"] != input.SHA256 {
		t.Fatalf("input.json = %+v", document)
	}
	uploads := client.uploadsByPurpose()
	if _, ok := uploads["final_output"]; !ok {
		t.Fatalf("no final_output upload: %+v", uploads)
	}
	if _, ok := uploads["node_output"]; !ok {
		t.Fatalf("no node_output upload: %+v", uploads)
	}
	if _, ok := uploads["log"]; !ok {
		t.Fatalf("no log upload: %+v", uploads)
	}
	if len(client.proposals) != 1 || client.proposals[0].SuggestedState != "succeeded" {
		t.Fatalf("proposals = %+v", client.proposals)
	}
	if client.proposals[0].OutputArtifactID != uploads["final_output"].Artifact {
		t.Fatalf("the succeeded proposal must name the summary artifact: %+v", client.proposals[0])
	}
	relations := map[string]string{}
	for _, registration := range client.registers {
		relations[registration.ArtifactID] = registration.Relation
	}
	if relations[uploads["final_output"].Artifact] != "output" {
		t.Fatalf("relations = %+v", relations)
	}
	if relations[uploads["log"].Artifact] != "log" {
		t.Fatalf("the log must be registered as a log: %+v", relations)
	}
	if len(client.exits) != 1 {
		t.Fatalf("exits = %+v", client.exits)
	}
	evidence := client.exits[0].Evidence
	wantEvidence := map[string]any{
		"started":                  true,
		"protocol_version":         ProtocolVersion,
		"manifest_sha256":          lease.ManifestSHA256,
		"image_digest":             "sha256:" + strings.Repeat("aa", 32),
		"sandbox_id":               "unit-test-1",
		"sandbox_started":          true,
		"sandbox_rootfs_digest":    "sha256:" + strings.Repeat("cd", 32),
		"sandbox_cleanup_verified": true,
		"db_writes_drained":        true,
	}
	for key, want := range wantEvidence {
		if evidence[key] != want {
			t.Fatalf("evidence[%q] = %#v, want %#v", key, evidence[key], want)
		}
	}
	if report.RunState != "succeeded" {
		t.Fatalf("run state = %q", report.RunState)
	}
}

func TestUnsupportedGraphIsAFailedRunNotARetryLoop(t *testing.T) {
	for name, pythonNodes := range map[string]int{"no python node": 0, "two python nodes": 2} {
		lease, manifest, _ := successLease(t, PresetToolID, pythonNodes)
		client := &fakeClient{lease: lease, manifest: manifest, runState: "failed"}
		box := &fakeSandbox{}
		report := Run(context.Background(), baseDeps(t, client, &fakeInputs{}, box))
		if report.Outcome != "refused" || report.ErrorCode != CodeToolUnsupportedGraph {
			t.Fatalf("%s: report = %+v", name, report)
		}
		if box.calls != 0 {
			t.Fatalf("%s: a refusal must not start the sandbox", name)
		}
		if len(client.proposals) != 1 || client.proposals[0].ErrorCode != CodeToolUnsupportedGraph {
			t.Fatalf("%s: proposals = %+v", name, client.proposals)
		}
		if len(client.exits) != 1 || client.exits[0].Evidence["sandbox_started"] != false {
			t.Fatalf("%s: exits = %+v", name, client.exits)
		}
		if client.exits[0].Evidence["started"] != true {
			t.Fatalf("%s: a deterministic refusal must still commit a failed run", name)
		}
	}
}

func TestAnotherToolIsRefused(t *testing.T) {
	lease, manifest, _ := successLease(t, "22222222-2222-4222-8222-222222222222", 1)
	client := &fakeClient{lease: lease, manifest: manifest, runState: "failed"}
	report := Run(context.Background(), baseDeps(t, client, &fakeInputs{}, &fakeSandbox{}))
	if report.Outcome != "refused" || report.ErrorCode != CodeToolUnsupportedTool {
		t.Fatalf("report = %+v", report)
	}
}

func TestInputResolutionRequiresExactlyOneTaskInput(t *testing.T) {
	lease, manifest, _ := successLease(t, PresetToolID, 1)
	cases := map[string]struct {
		inputs []InputArtifact
		code   string
	}{
		"no input":   {nil, CodeToolInputInvalid},
		"two inputs": {[]InputArtifact{inputArtifact(), inputArtifact()}, CodeToolInputAmbiguous},
		"not csv": {[]InputArtifact{{
			ID: "1", Purpose: "task_input", MediaType: "text/plain", SizeBytes: 1, SHA256: "x",
		}}, CodeToolInputInvalid},
	}
	for name, scenario := range cases {
		client := &fakeClient{lease: lease, manifest: manifest, runState: "failed"}
		report := Run(context.Background(), baseDeps(t, client, &fakeInputs{inputs: scenario.inputs}, &fakeSandbox{}))
		if report.Outcome != "refused" || report.ErrorCode != scenario.code {
			t.Fatalf("%s: report = %+v", name, report)
		}
	}
}

func TestInputIntegrityIsVerifiedBeforeTheSandbox(t *testing.T) {
	lease, manifest, _ := successLease(t, PresetToolID, 1)
	input := inputArtifact()
	client := &fakeClient{lease: lease, manifest: manifest, inputs: []InputArtifact{input},
		inputBytes: []byte("tampered"), runState: "failed"}
	box := &fakeSandbox{}
	report := Run(context.Background(), baseDeps(t, client, &fakeInputs{inputs: client.inputs}, box))
	if report.Outcome != "refused" || report.ErrorCode != CodeInputIntegrity {
		t.Fatalf("report = %+v", report)
	}
	if box.calls != 0 {
		t.Fatal("the sandbox must not run on unverified input bytes")
	}
}

func TestManifestHashMismatchIsRefused(t *testing.T) {
	lease, manifest, _ := successLease(t, PresetToolID, 1)
	lease.ManifestSHA256 = strings.Repeat("ff", 32)
	client := &fakeClient{lease: lease, manifest: manifest, runState: "failed"}
	report := Run(context.Background(), baseDeps(t, client, &fakeInputs{}, &fakeSandbox{}))
	if report.Outcome != "refused" || report.ErrorCode != CodeManifestUnavailable {
		t.Fatalf("report = %+v", report)
	}
}

func TestCancelStopsTheUnitWithoutASuccessProposal(t *testing.T) {
	lease, manifest, _ := successLease(t, PresetToolID, 1)
	input := inputArtifact()
	client := &fakeClient{lease: lease, manifest: manifest, inputs: []InputArtifact{input},
		inputBytes: []byte(salesCSV), runState: "recovery_pending", cancelAt: 2}
	box := &fakeSandbox{interrupt: func(context.Context) (sandbox.Outcome, error) {
		// The real sandbox executor observes cancel_requested through the control
		// heartbeat and returns ErrCancelled after killing the unit; the fake
		// reports the same terminal fact without a real process.
		return sandbox.Outcome{SandboxStarted: true, Cancelled: true,
			CleanupVerified: true, Cleanup: sandbox.CleanupFacts{WorkspaceRemoved: true}}, sandbox.ErrCancelled
	}}
	report := Run(context.Background(), baseDeps(t, client, &fakeInputs{inputs: client.inputs}, box))
	if report.Outcome != "cancelled" {
		t.Fatalf("report = %+v", report)
	}
	if len(client.proposals) != 0 {
		t.Fatalf("a cancellation must not propose a result: %+v", client.proposals)
	}
	if len(client.exits) != 1 || client.exits[0].Evidence["started"] != true {
		t.Fatalf("exits = %+v", client.exits)
	}
	if report.RunState != "recovery_pending" {
		t.Fatalf("run state = %q", report.RunState)
	}
}

func TestTimeoutFailsTheRunWithASpecificCode(t *testing.T) {
	lease, manifest, _ := successLease(t, PresetToolID, 1)
	input := inputArtifact()
	client := &fakeClient{lease: lease, manifest: manifest, inputs: []InputArtifact{input},
		inputBytes: []byte(salesCSV), runState: "failed"}
	box := &fakeSandbox{outcome: sandbox.Outcome{SandboxStarted: true, TimedOut: true,
		CleanupVerified: true, Cleanup: sandbox.CleanupFacts{WorkspaceRemoved: true}},
		err: sandbox.ErrTimeout}
	report := Run(context.Background(), baseDeps(t, client, &fakeInputs{inputs: client.inputs}, box))
	if report.Outcome != "failed" || report.ErrorCode != CodeToolTimeout {
		t.Fatalf("report = %+v", report)
	}
	if len(client.proposals) != 1 || client.proposals[0].ErrorCode != CodeToolTimeout {
		t.Fatalf("proposals = %+v", client.proposals)
	}
}

func TestToolRejectionKeepsTheToolCodeAndUploadsDiagnostics(t *testing.T) {
	lease, manifest, _ := successLease(t, PresetToolID, 1)
	input := inputArtifact()
	client := &fakeClient{lease: lease, manifest: manifest, inputs: []InputArtifact{input},
		inputBytes: []byte(salesCSV), runState: "failed"}
	rejection := []byte(`{"error":{"code":"negative_amount","message":"amount must not be negative","fix":"fix the row","location":{"row":2}}}`)
	box := &fakeSandbox{outcome: sandbox.Outcome{
		SandboxStarted: true, ExitCode: 3, CleanupVerified: true,
		Cleanup:    sandbox.CleanupFacts{WorkspaceRemoved: true},
		OutputJSON: rejection, Stderr: []byte("sales_stats: rejected negative_amount\n"),
	}}
	report := Run(context.Background(), baseDeps(t, client, &fakeInputs{inputs: client.inputs}, box))
	if report.Outcome != "failed" || report.ErrorCode != "negative_amount" {
		t.Fatalf("report = %+v", report)
	}
	if len(client.proposals) != 1 || client.proposals[0].ErrorCode != "negative_amount" {
		t.Fatalf("proposals = %+v", client.proposals)
	}
	uploads := client.uploadsByPurpose()
	diagnostics, ok := uploads["node_output"]
	if !ok {
		t.Fatalf("no diagnostics upload: %+v", uploads)
	}
	var document map[string]any
	if err := json.Unmarshal(diagnostics.Content, &document); err != nil {
		t.Fatalf("diagnostics: %v", err)
	}
	if document["error_code"] != "negative_amount" || document["message"] == "" {
		t.Fatalf("diagnostics = %v", document)
	}
	if strings.Contains(string(diagnostics.Content), "east,120.50") {
		t.Fatal("diagnostics must not contain input bytes")
	}
}

func TestCrashAndInvalidOutputFailWithTheirCodes(t *testing.T) {
	cases := map[string]struct {
		outcome sandbox.Outcome
		code    string
	}{
		"crash": {sandbox.Outcome{SandboxStarted: true, ExitCode: 9,
			CleanupVerified: true, Cleanup: sandbox.CleanupFacts{WorkspaceRemoved: true}}, CodeToolCrash},
		"missing output": {sandbox.Outcome{SandboxStarted: true, ExitCode: 0,
			CleanupVerified: true, Cleanup: sandbox.CleanupFacts{WorkspaceRemoved: true}}, CodeToolOutputInvalid},
		"invalid artifact": {sandbox.Outcome{SandboxStarted: true, ExitCode: 0,
			CleanupVerified: true, Cleanup: sandbox.CleanupFacts{WorkspaceRemoved: true},
			OutputJSON: []byte(`{"data":{},"artifacts":["../escape"]}`)}, CodeToolOutputInvalid},
	}
	for name, scenario := range cases {
		lease, manifest, _ := successLease(t, PresetToolID, 1)
		input := inputArtifact()
		client := &fakeClient{lease: lease, manifest: manifest, inputs: []InputArtifact{input},
			inputBytes: []byte(salesCSV), runState: "failed"}
		report := Run(context.Background(), baseDeps(t, client, &fakeInputs{inputs: client.inputs},
			&fakeSandbox{outcome: scenario.outcome}))
		if report.Outcome != "failed" || report.ErrorCode != scenario.code {
			t.Fatalf("%s: report = %+v", name, report)
		}
		if len(client.exits) != 1 {
			t.Fatalf("%s: a failure must still confirm the exit: %+v", name, client.exits)
		}
	}
}

func TestOrphanFailsTheRunAndKeepsTheCleanupEvidence(t *testing.T) {
	lease, manifest, _ := successLease(t, PresetToolID, 1)
	input := inputArtifact()
	client := &fakeClient{lease: lease, manifest: manifest, inputs: []InputArtifact{input},
		inputBytes: []byte(salesCSV), runState: "failed"}
	box := &fakeSandbox{outcome: sandbox.Outcome{SandboxStarted: true, ExitCode: 0,
		Cleanup: sandbox.CleanupFacts{SandboxListMatches: 1, PSMatches: 1}}, err: sandbox.ErrOrphan}
	report := Run(context.Background(), baseDeps(t, client, &fakeInputs{inputs: client.inputs}, box))
	if report.Outcome != "failed" || report.ErrorCode != CodeSandboxOrphan {
		t.Fatalf("report = %+v", report)
	}
	cleanup, ok := client.exits[0].Evidence["sandbox_cleanup"].(map[string]any)
	if !ok || cleanup["ps_matches"] != 1 {
		t.Fatalf("exit evidence lost the orphan facts: %+v", client.exits[0].Evidence)
	}
}

func TestPreStartSandboxRefusalKeepsTheProvenFalseFlag(t *testing.T) {
	lease, manifest, _ := successLease(t, PresetToolID, 1)
	input := inputArtifact()
	client := &fakeClient{lease: lease, manifest: manifest, inputs: []InputArtifact{input},
		inputBytes: []byte(salesCSV), runState: "failed"}
	// The executor proved no unit launched: SandboxStarted is false and the
	// typed refusal maps to sandbox_unavailable without inventing cleanup facts.
	box := &fakeSandbox{
		outcome: sandbox.Outcome{SandboxStarted: false},
		err:     sandbox.ErrSandboxRefused,
	}
	report := Run(context.Background(), baseDeps(t, client, &fakeInputs{inputs: client.inputs}, box))
	if report.Outcome != "failed" || report.ErrorCode != CodeSandboxUnavailable {
		t.Fatalf("report = %+v", report)
	}
	if len(client.exits) != 1 {
		t.Fatalf("exits = %+v", client.exits)
	}
	evidence := client.exits[0].Evidence
	if evidence["sandbox_started"] != false {
		t.Fatalf("sandbox_started must use the proven flag: %+v", evidence)
	}
	if evidence["started"] != true {
		t.Fatalf("the attempt still certifies its exit: %+v", evidence)
	}
	if evidence["sandbox_cleanup_verified"] != false {
		t.Fatalf("a pre-start refusal must not claim cleanup was verified: %+v", evidence)
	}
}

func TestSandboxUnavailableIsAFailedRun(t *testing.T) {
	lease, manifest, _ := successLease(t, PresetToolID, 1)
	input := inputArtifact()
	client := &fakeClient{lease: lease, manifest: manifest, inputs: []InputArtifact{input},
		inputBytes: []byte(salesCSV), runState: "failed"}
	box := &fakeSandbox{err: sandbox.ErrRootfsUnavailable}
	report := Run(context.Background(), baseDeps(t, client, &fakeInputs{inputs: client.inputs}, box))
	if report.Outcome != "failed" || report.ErrorCode != CodeSandboxUnavailable {
		t.Fatalf("report = %+v", report)
	}
	if client.exits[0].Evidence["sandbox_started"] != false {
		t.Fatalf("evidence = %+v", client.exits[0].Evidence)
	}
}
