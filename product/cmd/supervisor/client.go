// Command supervisor: F00's stale-worker sweep (no arguments) and F05/T033's
// `exec --once` tool-debug executor.
//
// The sweep behaviour is unchanged: without a subcommand the process takes the
// supervisor lease, parks stale workers' runs and exits. `exec` is a separate
// mode that claims one F03 execution attempt and runs the fixed preset tool in
// the gVisor sandbox; it never sweeps and never starts more than one attempt.
package main

import (
	"bytes"
	"context"
	"crypto/tls"
	"crypto/x509"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"strings"
	"time"

	"orchara/platform/internal/capabilities"
	"orchara/platform/internal/config"
	"orchara/platform/internal/presetexec"
)

// The two internal capacities this process presents. The contract's artifact
// routes are worker routes and the claim/exit routes are supervisor routes; the
// same process holds both certificates until F04A splits the roles.
const (
	supervisorRole = "supervisor"
	workerRole     = "worker"
)

type supClient struct {
	baseURL    string
	supervisor *http.Client
	worker     *http.Client
	plain      *http.Client
}

func newSupClient(cfg *config.SupervisorExecConfig) (*supClient, error) {
	caPEM, err := os.ReadFile(cfg.CA)
	if err != nil {
		return nil, errors.New("cannot read the internal CA file")
	}
	pool := x509.NewCertPool()
	if !pool.AppendCertsFromPEM(caPEM) {
		return nil, errors.New("the internal CA file contains no certificate")
	}
	build := func(certFile, keyFile string) (*http.Client, error) {
		certificate, err := tls.LoadX509KeyPair(certFile, keyFile)
		if err != nil {
			return nil, errors.New("cannot load an internal client certificate")
		}
		transport := &http.Transport{
			TLSClientConfig: &tls.Config{
				MinVersion:   tls.VersionTLS12,
				Certificates: []tls.Certificate{certificate},
				RootCAs:      pool,
			},
			Proxy: nil,
		}
		return &http.Client{Transport: transport, Timeout: 60 * time.Second}, nil
	}
	supervisor, err := build(cfg.SupervisorCert, cfg.SupervisorKey)
	if err != nil {
		return nil, err
	}
	worker, err := build(cfg.WorkerCert, cfg.WorkerKey)
	if err != nil {
		return nil, err
	}
	return &supClient{
		baseURL:    strings.TrimSuffix(cfg.InternalURL, "/"),
		supervisor: supervisor,
		worker:     worker,
		// Signed object links are plain HTTP(S) on the public listener: no mTLS
		// and no redirects (a server-minted URL must stay the destination).
		plain: &http.Client{
			Timeout: 120 * time.Second,
			CheckRedirect: func(*http.Request, []*http.Request) error {
				return http.ErrUseLastResponse
			},
		},
	}, nil
}

// internalError is a contract error envelope from the internal API.
type internalError struct {
	Status  int
	Code    string
	Message string
}

func (e *internalError) Error() string {
	return fmt.Sprintf("internal API %d %s: %s", e.Status, e.Code, e.Message)
}

// ErrorCode exposes the stable contract code.
func (e *internalError) ErrorCode() string { return e.Code }

func isInternalCode(err error, code string) bool {
	var apiErr *internalError
	return errors.As(err, &apiErr) && apiErr.Code == code
}

func (c *supClient) post(
	ctx context.Context, client *http.Client, role, path string, payload, target any,
) (int, error) {
	encoded, err := json.Marshal(payload)
	if err != nil {
		return 0, fmt.Errorf("encoding the internal request: %w", err)
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, c.baseURL+path, bytes.NewReader(encoded))
	if err != nil {
		return 0, fmt.Errorf("building the internal request: %w", err)
	}
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("Accept", "application/json")
	request.Header.Set("X-Internal-Role", role)
	response, err := client.Do(request)
	if err != nil {
		return 0, fmt.Errorf("calling the internal API: %w", err)
	}
	defer func() {
		_, _ = io.Copy(io.Discard, response.Body)
		_ = response.Body.Close()
	}()
	body, readErr := io.ReadAll(io.LimitReader(response.Body, 1<<20))
	if readErr != nil {
		return response.StatusCode, errors.New("reading the internal response failed")
	}
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return response.StatusCode, decodeInternalError(response.StatusCode, body)
	}
	if response.StatusCode == http.StatusNoContent || target == nil {
		return response.StatusCode, nil
	}
	if err := json.Unmarshal(body, target); err != nil {
		return response.StatusCode, errors.New("the internal response is not valid JSON")
	}
	return response.StatusCode, nil
}

func decodeInternalError(status int, body []byte) error {
	var envelope struct {
		Error struct {
			Code    string `json:"code"`
			Message string `json:"message"`
		} `json:"error"`
	}
	if err := json.Unmarshal(body, &envelope); err != nil || envelope.Error.Code == "" {
		return &internalError{Status: status, Code: "invalid_response",
			Message: "the internal API returned a non-contract error"}
	}
	return &internalError{Status: status, Code: envelope.Error.Code, Message: envelope.Error.Message}
}

// runPath builds /internal/v1/runs/{id}{suffix} with an escaped id.
func runPath(runID, suffix string) string {
	return "/internal/v1/runs/" + url.PathEscape(runID) + suffix
}

// Claim implements presetexec.Client.
func (c *supClient) Claim(
	ctx context.Context, workerID, executionUnitID string,
) (*presetexec.Lease, error) {
	body := map[string]any{
		"worker_id":           workerID,
		"supported_compilers": []string{presetexec.CompilerVersion},
		"execution_unit_id":   executionUnitID,
	}
	var lease presetexec.Lease
	status, err := c.post(ctx, c.supervisor, supervisorRole, "/internal/v1/jobs/claim", body, &lease)
	if err != nil {
		return nil, err
	}
	if status == http.StatusNoContent {
		return nil, nil
	}
	if status != http.StatusOK {
		return nil, fmt.Errorf("claim returned unexpected status %d", status)
	}
	if lease.RunID == "" || lease.AttemptToken == "" || lease.JobID == "" {
		return nil, errors.New("claim response is missing its run or attempt identity")
	}
	return &lease, nil
}

// Heartbeat implements presetexec.Client.
func (c *supClient) Heartbeat(
	ctx context.Context, runID, attemptToken, executionUnitID string,
) (presetexec.Heartbeat, error) {
	body := map[string]any{"attempt_token": attemptToken, "execution_unit_id": executionUnitID}
	var reply struct {
		CancelRequested bool `json:"cancel_requested"`
	}
	status, err := c.post(ctx, c.supervisor, supervisorRole, runPath(runID, "/heartbeat"), body, &reply)
	if err != nil {
		return presetexec.Heartbeat{}, err
	}
	if status != http.StatusOK {
		return presetexec.Heartbeat{}, fmt.Errorf("heartbeat returned unexpected status %d", status)
	}
	return presetexec.Heartbeat{CancelRequested: reply.CancelRequested}, nil
}

// SubmitResult implements presetexec.Client.
func (c *supClient) SubmitResult(
	ctx context.Context, runID, attemptToken string, proposal presetexec.Proposal,
) error {
	body := map[string]any{
		"attempt_token":   attemptToken,
		"event_id":        proposal.EventID,
		"suggested_state": proposal.SuggestedState,
	}
	if proposal.OutputArtifactID != "" {
		body["output_artifact_id"] = proposal.OutputArtifactID
	}
	if proposal.ErrorCode != "" {
		body["error_code"] = proposal.ErrorCode
	}
	if proposal.CheckpointID != "" {
		body["checkpoint_id"] = proposal.CheckpointID
	}
	status, err := c.post(ctx, c.supervisor, supervisorRole, runPath(runID, "/result"), body, nil)
	if err != nil {
		return err
	}
	if status != http.StatusOK {
		return fmt.Errorf("result returned unexpected status %d", status)
	}
	return nil
}

// ConfirmExit implements presetexec.Client.
func (c *supClient) ConfirmExit(
	ctx context.Context, attemptID, executionUnitID string, evidence map[string]any,
) error {
	body := map[string]any{"execution_unit_id": executionUnitID, "evidence": evidence}
	status, err := c.post(ctx, c.supervisor, supervisorRole,
		"/internal/v1/attempts/"+url.PathEscape(attemptID)+"/exited", body, nil)
	if err != nil {
		return err
	}
	if status != http.StatusOK {
		return fmt.Errorf("exit returned unexpected status %d", status)
	}
	return nil
}

// Fetch reads one signed GET link (manifest or input).
func (c *supClient) Fetch(ctx context.Context, link string) ([]byte, error) {
	if err := validateSignedURL(link); err != nil {
		return nil, err
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, link, nil)
	if err != nil {
		return nil, errors.New("the signed link is not a valid request")
	}
	response, err := c.plain.Do(request)
	if err != nil {
		return nil, fmt.Errorf("fetching the signed object: %w", err)
	}
	defer func() {
		_, _ = io.Copy(io.Discard, response.Body)
		_ = response.Body.Close()
	}()
	if response.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("the signed download returned status %d", response.StatusCode)
	}
	body, err := io.ReadAll(io.LimitReader(response.Body, 64<<20))
	if err != nil {
		return nil, fmt.Errorf("reading the signed object: %w", err)
	}
	return body, nil
}

// PutBytes writes one signed PUT link.
func (c *supClient) PutBytes(ctx context.Context, link string, content []byte) error {
	if err := validateSignedURL(link); err != nil {
		return err
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodPut, link, bytes.NewReader(content))
	if err != nil {
		return errors.New("the signed upload link is not a valid request")
	}
	request.Header.Set("Content-Type", "application/octet-stream")
	response, err := c.plain.Do(request)
	if err != nil {
		return fmt.Errorf("uploading to the signed link: %w", err)
	}
	defer func() {
		_, _ = io.Copy(io.Discard, response.Body)
		_ = response.Body.Close()
	}()
	if response.StatusCode != http.StatusNoContent && response.StatusCode != http.StatusOK {
		return fmt.Errorf("the signed upload returned status %d", response.StatusCode)
	}
	return nil
}

type uploadTicketPayload struct {
	ArtifactID string `json:"artifact_id"`
	UploadURL  string `json:"upload_url"`
	ExpiresAt  string `json:"expires_at"`
}

type artifactPayload struct {
	ID        string `json:"id"`
	State     string `json:"state"`
	MediaType string `json:"media_type"`
	SizeBytes int64  `json:"size_bytes"`
}

func (a artifactPayload) view() presetexec.ArtifactView {
	return presetexec.ArtifactView{
		ID: a.ID, State: a.State, MediaType: a.MediaType, SizeBytes: a.SizeBytes,
	}
}

// CreateUpload implements presetexec.Client on the worker capacity.
func (c *supClient) CreateUpload(
	ctx context.Context, runID, attemptToken string, request presetexec.UploadRequest,
) (presetexec.UploadTicket, error) {
	body := map[string]any{
		"attempt_token": attemptToken,
		"media_type":    request.MediaType,
		"size_bytes":    request.SizeBytes,
		"purpose":       request.Purpose,
	}
	var response uploadTicketPayload
	status, err := c.post(ctx, c.worker, workerRole, runPath(runID, "/artifact-uploads"), body, &response)
	if err != nil {
		return presetexec.UploadTicket{}, err
	}
	if status != http.StatusCreated {
		return presetexec.UploadTicket{}, fmt.Errorf("artifact-uploads returned unexpected status %d", status)
	}
	if response.ArtifactID == "" || response.UploadURL == "" {
		return presetexec.UploadTicket{}, errors.New("artifact-uploads response is missing its ticket")
	}
	return presetexec.UploadTicket{ArtifactID: response.ArtifactID, URL: response.UploadURL}, nil
}

// Complete implements presetexec.Client.
func (c *supClient) Complete(
	ctx context.Context, runID, attemptToken, artifactID, sha256 string,
) (presetexec.ArtifactView, error) {
	body := map[string]string{"attempt_token": attemptToken, "sha256": sha256}
	var response artifactPayload
	status, err := c.post(ctx, c.worker, workerRole,
		runPath(runID, "/artifacts/"+url.PathEscape(artifactID)+"/complete"), body, &response)
	if err != nil {
		return presetexec.ArtifactView{}, err
	}
	if status != http.StatusAccepted {
		return presetexec.ArtifactView{}, fmt.Errorf("complete returned unexpected status %d", status)
	}
	return response.view(), nil
}

// Status implements presetexec.Client.
func (c *supClient) Status(
	ctx context.Context, runID, attemptToken, artifactID string,
) (presetexec.ArtifactView, error) {
	body := map[string]string{"attempt_token": attemptToken, "artifact_id": artifactID}
	var response artifactPayload
	status, err := c.post(ctx, c.worker, workerRole, runPath(runID, "/artifact-status"), body, &response)
	if err != nil {
		return presetexec.ArtifactView{}, err
	}
	if status != http.StatusOK {
		return presetexec.ArtifactView{}, fmt.Errorf("artifact-status returned unexpected status %d", status)
	}
	return response.view(), nil
}

// Register implements presetexec.Client.
func (c *supClient) Register(
	ctx context.Context, runID, attemptToken, artifactID, relation string,
) (presetexec.ArtifactView, error) {
	body := map[string]string{
		"attempt_token": attemptToken,
		"artifact_id":   artifactID,
		"relation":      relation,
	}
	var response artifactPayload
	status, err := c.post(ctx, c.worker, workerRole, runPath(runID, "/artifacts"), body, &response)
	if err != nil {
		return presetexec.ArtifactView{}, err
	}
	if status != http.StatusOK {
		return presetexec.ArtifactView{}, fmt.Errorf("register returned unexpected status %d", status)
	}
	return response.view(), nil
}

// Download mints a run-scoped signed GET and reads the bytes.
func (c *supClient) Download(
	ctx context.Context, runID, attemptToken, artifactID string,
) ([]byte, error) {
	body := map[string]string{"attempt_token": attemptToken, "artifact_id": artifactID}
	var ticket struct {
		URL string `json:"url"`
	}
	status, err := c.post(ctx, c.worker, workerRole, runPath(runID, "/artifact-downloads"), body, &ticket)
	if err != nil {
		return nil, err
	}
	if status != http.StatusOK || ticket.URL == "" {
		return nil, fmt.Errorf("artifact-downloads returned status %d without a URL", status)
	}
	return c.Fetch(ctx, ticket.URL)
}

// capabilityResultPayload is the exact CapabilityResult subset the worker reads.
type capabilityResultPayload struct {
	OperationID       string         `json:"operation_id"`
	State             string         `json:"state"`
	ReceiptArtifactID *string        `json:"receipt_artifact_id"`
	ApprovalID        *string        `json:"approval_id"`
	Data              map[string]any `json:"data,omitempty"`
}

func (p capabilityResultPayload) result() *capabilities.Result {
	return &capabilities.Result{
		OperationID:       p.OperationID,
		State:             p.State,
		ReceiptArtifactID: p.ReceiptArtifactID,
		ApprovalID:        p.ApprovalID,
		Data:              p.Data,
	}
}

// InvokeCapability implements presetexec.Client: one T041 capability call over
// the worker mTLS capacity. The worker never talks to a provider directly.
func (c *supClient) InvokeCapability(
	ctx context.Context, runID, attemptToken string, input capabilities.Input,
) (*capabilities.Result, error) {
	body := map[string]any{
		"attempt_token":      attemptToken,
		"operation_id":       input.OperationID,
		"logical_key":        input.LogicalKey,
		"capability_version": input.CapabilityVersion,
		"capability_kind":    input.CapabilityKind,
		"params":             input.Params,
	}
	var response capabilityResultPayload
	status, err := c.post(ctx, c.worker, workerRole,
		runPath(runID, "/capabilities"), body, &response)
	if err != nil {
		return nil, err
	}
	if status != http.StatusOK {
		return nil, fmt.Errorf("capabilities returned unexpected status %d", status)
	}
	return response.result(), nil
}

// ReportCapabilityResult implements presetexec.Client: the non-contract
// capability-results bridge for a granted tool call.
func (c *supClient) ReportCapabilityResult(
	ctx context.Context, runID, attemptToken string, report capabilities.ResultReport,
) (*capabilities.Result, error) {
	body := map[string]any{
		"attempt_token": attemptToken,
		"operation_id":  report.OperationID,
		"state":         report.State,
	}
	if report.ResultArtifactID != "" {
		body["result_artifact_id"] = report.ResultArtifactID
	}
	if report.Data != nil {
		body["data"] = report.Data
	}
	if report.ErrorCode != "" {
		body["error_code"] = report.ErrorCode
	}
	var response capabilityResultPayload
	status, err := c.post(ctx, c.worker, workerRole,
		runPath(runID, "/capability-results"), body, &response)
	if err != nil {
		return nil, err
	}
	if status != http.StatusOK {
		return nil, fmt.Errorf("capability-results returned unexpected status %d", status)
	}
	return response.result(), nil
}

// childRunPayload is the contract Run subset spawnChild answers with.
type childRunPayload struct {
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
}

func (p childRunPayload) run() *presetexec.ChildRun {
	return &presetexec.ChildRun{
		ID: p.ID, State: p.State, Revision: p.Revision,
		ApplicationVersionID: stringOrEmpty(p.ApplicationVersionID),
		ParentRunID:          stringOrEmpty(p.ParentRunID),
		OutputArtifactID:     stringOrEmpty(p.OutputArtifactID),
		ErrorCode:            stringOrEmpty(p.ErrorCode),
		Kind:                 p.Kind, ExecutionSnapshotID: p.ExecutionSnapshotID, Phase: p.Phase,
	}
}

// SpawnChild implements presetexec.Client on the worker mTLS capacity: the
// contract declares x-internal-role: worker for spawnChild. The parent attempt
// token in the body is the only delegation authority; a supervisor certificate
// is refused by the server.
func (c *supClient) SpawnChild(
	ctx context.Context, runID, attemptToken string, request presetexec.SpawnChildRequest,
) (*presetexec.ChildRun, error) {
	body := map[string]any{
		"attempt_token":          attemptToken,
		"logical_key":            request.LogicalKey,
		"application_version_id": request.ApplicationVersionID,
		"input":                  request.Input,
	}
	var payload childRunPayload
	status, err := c.post(ctx, c.worker, workerRole, runPath(runID, "/children"), body, &payload)
	if err != nil {
		return nil, err
	}
	if status != http.StatusAccepted {
		return nil, fmt.Errorf("spawnChild returned unexpected status %d", status)
	}
	if payload.ID == "" || payload.ParentRunID == nil {
		return nil, errors.New("spawnChild response is not a child Run")
	}
	return payload.run(), nil
}

// ChildResult implements presetexec.Client: the non-contract read of one
// terminal child's structured result. It is only reachable while the parent holds
// a live attempt, and the child must be linked to that parent.
func (c *supClient) ChildResult(
	ctx context.Context, runID, attemptToken, childRunID string,
) (*presetexec.ChildResult, error) {
	body := map[string]any{"attempt_token": attemptToken}
	var payload struct {
		ChildRunID       string         `json:"child_run_id"`
		State            string         `json:"state"`
		OutputArtifactID *string        `json:"output_artifact_id"`
		Output           map[string]any `json:"output"`
		ErrorCode        *string        `json:"error_code"`
	}
	status, err := c.post(ctx, c.worker, workerRole,
		runPath(runID, "/children/"+url.PathEscape(childRunID)+"/result"), body, &payload)
	if err != nil {
		return nil, err
	}
	if status != http.StatusOK {
		return nil, fmt.Errorf("child result returned unexpected status %d", status)
	}
	return &presetexec.ChildResult{
		ChildRunID:       payload.ChildRunID,
		State:            payload.State,
		OutputArtifactID: stringOrEmpty(payload.OutputArtifactID),
		ErrorCode:        stringOrEmpty(payload.ErrorCode),
		Output:           payload.Output,
	}, nil
}

func stringOrEmpty(value *string) string {
	if value == nil {
		return ""
	}
	return *value
}

// retrievePayload is the non-contract internal retrieval answer.
type retrievePayload struct {
	Items []struct {
		ChunkID          string          `json:"chunk_id"`
		DocumentID       string          `json:"document_id"`
		DocumentRevision int64           `json:"document_revision"`
		DocumentState    string          `json:"document_state"`
		ChunkNo          int             `json:"chunk_no"`
		Span             json.RawMessage `json:"span"`
		Text             string          `json:"text"`
		Score            float64         `json:"score"`
		Citation         json.RawMessage `json:"citation"`
	} `json:"items"`
	Requested  int `json:"requested"`
	Authorized int `json:"authorized"`
}

// Retrieve implements presetexec.Client: F04B/T088's knowledge retrieval through
// the non-contract internal route. The worker capacity is the same one the
// artifact/capability routes use, and the attempt token in the body is the only
// run identity the server trusts (it derives the owner from the database).
func (c *supClient) Retrieve(
	ctx context.Context, runID, attemptToken string, request presetexec.RetrieveRequest,
) (*presetexec.RetrieveResult, error) {
	body := map[string]any{
		"attempt_token":      attemptToken,
		"knowledge_base_ids": request.KnowledgeBaseIDs,
		"query":              request.Query,
		"top_k":              request.TopK,
	}
	var payload retrievePayload
	status, err := c.post(ctx, c.worker, workerRole, runPath(runID, "/retrieval"), body, &payload)
	if err != nil {
		return nil, err
	}
	if status != http.StatusOK {
		return nil, fmt.Errorf("retrieval returned unexpected status %d", status)
	}
	result := &presetexec.RetrieveResult{
		Items:      make([]presetexec.RetrievedItem, 0, len(payload.Items)),
		Requested:  payload.Requested,
		Authorized: payload.Authorized,
	}
	for _, item := range payload.Items {
		result.Items = append(result.Items, presetexec.RetrievedItem{
			ChunkID:          item.ChunkID,
			DocumentID:       item.DocumentID,
			DocumentRevision: item.DocumentRevision,
			DocumentState:    item.DocumentState,
			ChunkNo:          item.ChunkNo,
			Span:             item.Span,
			Text:             item.Text,
			Score:            item.Score,
			Citation:         item.Citation,
		})
	}
	return result, nil
}

// validateSignedURL keeps bytes moving only to HTTP(S) links the server minted.
func validateSignedURL(link string) error {
	parsed, err := url.Parse(link)
	if err != nil || (parsed.Scheme != "http" && parsed.Scheme != "https") || parsed.Host == "" {
		return errors.New("the signed object link is not an HTTP(S) URL")
	}
	if parsed.User != nil {
		return errors.New("the signed object link must not carry credentials")
	}
	return nil
}
