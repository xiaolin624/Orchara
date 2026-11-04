package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"time"

	"orchara/platform/internal/background"
	"orchara/platform/internal/evaluation"
)

// jobScope is the maintenance process's implementation of the handler seam.
//
// It is the only way a compiled handler reaches job-scoped platform data: every
// method proves the job's attempt token to the internal mTLS API, and bytes move
// through the signed link the server just minted. The handler never sees the
// database DSN, a candidate endpoint/credential, or an object path.
type jobScope struct {
	client *internalClient
	http   *http.Client
}

// newJobScope builds the scope. The plain HTTP client is for the public signed
// links only (the mTLS client is for the internal API).
func newJobScope(client *internalClient) *jobScope {
	return &jobScope{
		client: client,
		http: &http.Client{
			Timeout: 60 * time.Second,
			// A signed link never needs a redirect, and following one could only
			// turn a server-minted URL into an unexpected destination.
			CheckRedirect: func(*http.Request, []*http.Request) error {
				return http.ErrUseLastResponse
			},
		},
	}
}

// Download asks for a signed GET link for an in-scope artifact.
func (s *jobScope) Download(ctx context.Context, jobID, attemptToken, artifactID string) (string, error) {
	return s.client.artifactDownloadURL(ctx, jobID, attemptToken, artifactID)
}

// Upload reserves a staging target for an artifact this job will produce.
func (s *jobScope) Upload(
	ctx context.Context,
	jobID, attemptToken string,
	request background.UploadRequest,
) (background.UploadTicket, error) {
	ticket, err := s.client.createUpload(
		ctx, jobID, attemptToken, request.MediaType, request.SizeBytes, request.Purpose,
	)
	if err != nil {
		return background.UploadTicket{}, err
	}
	expiresAt, err := time.Parse(time.RFC3339, ticket.ExpiresAt)
	if err != nil {
		return background.UploadTicket{}, errors.New("the upload ticket carries an invalid expiry")
	}
	return background.UploadTicket{
		ArtifactID: ticket.ArtifactID,
		URL:        ticket.UploadURL,
		ExpiresAt:  expiresAt,
	}, nil
}

// Complete moves an uploaded artifact to scanning.
func (s *jobScope) Complete(
	ctx context.Context,
	jobID, attemptToken, artifactID, sha256 string,
) (background.ArtifactView, error) {
	view, err := s.client.completeUpload(ctx, jobID, artifactID, attemptToken, sha256)
	if err != nil {
		return background.ArtifactView{}, err
	}
	return view.backgroundView(), nil
}

// Status reads one in-scope artifact's state.
func (s *jobScope) Status(
	ctx context.Context,
	jobID, attemptToken, artifactID string,
) (background.ArtifactView, error) {
	view, err := s.client.artifactStatus(ctx, jobID, attemptToken, artifactID)
	if err != nil {
		return background.ArtifactView{}, err
	}
	return view.backgroundView(), nil
}

// ModelCall invokes the job's candidate through the platform proxy.
func (s *jobScope) ModelCall(
	ctx context.Context,
	jobID, attemptToken string,
	request background.ModelCallRequest,
) (background.ModelCallResult, error) {
	result, err := s.client.modelCall(ctx, jobID, attemptToken, request)
	if err != nil {
		return background.ModelCallResult{}, err
	}
	return background.ModelCallResult{
		CallID:           result.CallID,
		State:            result.State,
		ResultArtifactID: result.ResultArtifactID,
		Data:             result.Data,
	}, nil
}

// Evaluation resolves the job-scoped evaluation binding. The handler never
// learns which candidate/snapshot row the manifest came from, only the hash it
// must declare in the report.
func (s *jobScope) Evaluation(
	ctx context.Context,
	jobID, attemptToken string,
) (background.EvaluationBinding, error) {
	binding, err := s.client.evaluationBinding(ctx, jobID, attemptToken)
	if err != nil {
		return background.EvaluationBinding{}, err
	}
	return background.EvaluationBinding{
		EvaluationID:       binding.EvaluationID,
		Kind:               binding.Kind,
		DatasetArtifactID:  binding.DatasetArtifactID,
		ManifestSHA256:     binding.ManifestSHA256,
		SnapshotID:         binding.SnapshotID,
		ApplicationID:      binding.ApplicationID,
		DraftRevision:      binding.DraftRevision,
		CompilerVersion:    binding.CompilerVersion,
		WorkerImageDigest:  binding.WorkerImageDigest,
		StateSchemaVersion: binding.StateSchemaVersion,
	}, nil
}

// ToolBuild resolves the job-scoped build binding (T037).
func (s *jobScope) ToolBuild(
	ctx context.Context,
	jobID, attemptToken string,
) (background.ToolBuildBinding, error) {
	binding, err := s.client.toolBuildBinding(ctx, jobID, attemptToken)
	if err != nil {
		return background.ToolBuildBinding{}, err
	}
	return background.ToolBuildBinding{
		ToolVersionID:    binding.ToolVersionID,
		SourceArtifactID: binding.SourceArtifactID,
		SourceSHA256:     binding.SourceSHA256,
		DependencyLock:   binding.DependencyLock,
		ManifestSHA256:   binding.ManifestSHA256,
	}, nil
}

// EvaluationRun creates (or returns) the platform run for one evaluation case
// as the evaluation's owner (F13A/T052). The handler supplies the canonical case
// input; the server seals it, creates the run and its start job, and re-proves
// the owner's current access.
func (s *jobScope) EvaluationRun(
	ctx context.Context,
	jobID, attemptToken, caseID string,
	input []byte,
) (background.EvaluationRunRef, error) {
	var raw json.RawMessage
	if err := json.Unmarshal(input, &raw); err != nil {
		return background.EvaluationRunRef{}, errors.New("the case input is not valid JSON")
	}
	ref, err := s.client.evaluationRun(ctx, jobID, attemptToken, caseID, raw)
	if err != nil {
		return background.EvaluationRunRef{}, err
	}
	return background.EvaluationRunRef{RunID: ref.RunID, Created: ref.Created}, nil
}

// EvaluationRunFacts reads the independent truth of one case run.
func (s *jobScope) EvaluationRunFacts(
	ctx context.Context,
	jobID, attemptToken, runID string,
) (background.EvaluationRunFacts, error) {
	payload, err := s.client.evaluationRunFacts(ctx, jobID, attemptToken, runID)
	if err != nil {
		return background.EvaluationRunFacts{}, err
	}
	facts := background.EvaluationRunFacts{
		RunID:              payload.RunID,
		State:              payload.State,
		ErrorCode:          payload.ErrorCode,
		OutputArtifactID:   payload.OutputArtifactID,
		OutputSHA256:       payload.OutputSHA256,
		Output:             payload.Output,
		OutputCollected:    payload.OutputCollected,
		Effects:            payload.EffectCounts,
		EffectsCollected:   payload.EffectsCollected,
		Citations:          payload.Citations,
		CitationsCollected: payload.CitationsCollected,
		Provenance:         payload.Provenance,
	}
	if facts.StartedAt, err = parseOptionalTime(payload.StartedAt); err != nil {
		return background.EvaluationRunFacts{}, err
	}
	if facts.FinishedAt, err = parseOptionalTime(payload.FinishedAt); err != nil {
		return background.EvaluationRunFacts{}, err
	}
	return facts, nil
}

// EvaluationRunInteraction performs one predefined interaction on a case run as
// the evaluation's owner.
func (s *jobScope) EvaluationRunInteraction(
	ctx context.Context,
	jobID, attemptToken, runID, interaction string,
) error {
	return s.client.evaluationRunInteraction(ctx, jobID, attemptToken, runID, interaction)
}

// parseOptionalTime decodes an RFC 3339 timestamp the server omitted (empty) or
// sent.
func parseOptionalTime(value string) (*time.Time, error) {
	if value == "" {
		return nil, nil
	}
	parsed, err := time.Parse(time.RFC3339Nano, value)
	if err != nil {
		return nil, errors.New("the evaluation run facts carry an invalid timestamp")
	}
	return &parsed, nil
}

// FetchURL reads one signed GET link, capped at maxBytes.
func (s *jobScope) FetchURL(ctx context.Context, link string, maxBytes int64) ([]byte, error) {
	if err := validateSignedURL(link); err != nil {
		return nil, err
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, link, nil)
	if err != nil {
		return nil, errors.New("the signed download link is not a valid request")
	}
	response, err := s.http.Do(request)
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
	body, err := io.ReadAll(io.LimitReader(response.Body, maxBytes+1))
	if err != nil {
		return nil, fmt.Errorf("reading the signed object: %w", err)
	}
	if int64(len(body)) > maxBytes {
		return nil, errors.New("the signed object exceeds the allowed size")
	}
	return body, nil
}

// PutURL writes body to one signed PUT link.
func (s *jobScope) PutURL(ctx context.Context, link string, body []byte) error {
	if err := validateSignedURL(link); err != nil {
		return err
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodPut, link, bytes.NewReader(body))
	if err != nil {
		return errors.New("the signed upload link is not a valid request")
	}
	request.Header.Set("Content-Type", "application/octet-stream")
	response, err := s.http.Do(request)
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

// validateSignedURL keeps the client from ever sending bytes to something other
// than an HTTP(S) link the server minted.
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

// ---------------------------------------------------------------------------
// internal API calls behind the job scope
// ---------------------------------------------------------------------------

type uploadTicket struct {
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

func (a artifactPayload) backgroundView() background.ArtifactView {
	return background.ArtifactView{
		ID:        a.ID,
		State:     a.State,
		MediaType: a.MediaType,
		SizeBytes: a.SizeBytes,
	}
}

type downloadTicket struct {
	URL       string `json:"url"`
	ExpiresAt string `json:"expires_at"`
}

type modelCallPayload struct {
	CallID           string         `json:"call_id"`
	State            string         `json:"state"`
	ResultArtifactID *string        `json:"result_artifact_id"`
	Data             map[string]any `json:"data"`
}

// evaluationBindingPayload is the non-contract internal evaluation read (T026).
type evaluationBindingPayload struct {
	EvaluationID       string `json:"evaluation_id"`
	Kind               string `json:"kind"`
	DatasetArtifactID  string `json:"dataset_artifact_id"`
	ManifestSHA256     string `json:"manifest_sha256"`
	SnapshotID         string `json:"snapshot_id"`
	ApplicationID      string `json:"application_id"`
	DraftRevision      int64  `json:"draft_revision"`
	CompilerVersion    string `json:"compiler_version"`
	WorkerImageDigest  string `json:"worker_image_digest"`
	StateSchemaVersion string `json:"state_schema_version"`
}

// toolBuildBindingPayload is the non-contract internal tool-build binding (T037).
type toolBuildBindingPayload struct {
	ToolVersionID    string          `json:"tool_version_id"`
	SourceArtifactID string          `json:"source_artifact_id"`
	SourceSHA256     string          `json:"source_sha256"`
	DependencyLock   json.RawMessage `json:"dependency_lock"`
	ManifestSHA256   string          `json:"manifest_sha256"`
}

func (c *internalClient) createUpload(
	ctx context.Context,
	jobID, attemptToken, mediaType string,
	sizeBytes int64,
	purpose string,
) (uploadTicket, error) {
	body := map[string]any{
		"attempt_token": attemptToken,
		"media_type":    mediaType,
		"size_bytes":    sizeBytes,
		"purpose":       purpose,
	}
	var response uploadTicket
	status, err := c.post(ctx, "/internal/v1/background/jobs/"+url.PathEscape(jobID)+"/artifact-uploads", body, &response)
	if err != nil {
		return uploadTicket{}, err
	}
	if status != http.StatusCreated {
		return uploadTicket{}, fmt.Errorf("artifact-uploads returned unexpected status %d", status)
	}
	if response.ArtifactID == "" || response.UploadURL == "" {
		return uploadTicket{}, errors.New("artifact-uploads response is missing its ticket")
	}
	return response, nil
}

func (c *internalClient) completeUpload(
	ctx context.Context,
	jobID, artifactID, attemptToken, sha256 string,
) (artifactPayload, error) {
	body := map[string]string{"attempt_token": attemptToken, "sha256": sha256}
	path := "/internal/v1/background/jobs/" + url.PathEscape(jobID) +
		"/artifacts/" + url.PathEscape(artifactID) + "/complete"
	var response artifactPayload
	status, err := c.post(ctx, path, body, &response)
	if err != nil {
		return artifactPayload{}, err
	}
	if status != http.StatusAccepted {
		return artifactPayload{}, fmt.Errorf("complete returned unexpected status %d", status)
	}
	return response, nil
}

func (c *internalClient) artifactStatus(
	ctx context.Context,
	jobID, attemptToken, artifactID string,
) (artifactPayload, error) {
	body := map[string]string{"attempt_token": attemptToken, "artifact_id": artifactID}
	var response artifactPayload
	status, err := c.post(
		ctx, "/internal/v1/background/jobs/"+url.PathEscape(jobID)+"/artifact-status", body, &response)
	if err != nil {
		return artifactPayload{}, err
	}
	if status != http.StatusOK {
		return artifactPayload{}, fmt.Errorf("artifact-status returned unexpected status %d", status)
	}
	return response, nil
}

func (c *internalClient) artifactDownloadURL(
	ctx context.Context,
	jobID, attemptToken, artifactID string,
) (string, error) {
	body := map[string]string{"attempt_token": attemptToken, "artifact_id": artifactID}
	var response downloadTicket
	status, err := c.post(
		ctx, "/internal/v1/background/jobs/"+url.PathEscape(jobID)+"/artifact-downloads", body, &response)
	if err != nil {
		return "", err
	}
	if status != http.StatusOK {
		return "", fmt.Errorf("artifact-downloads returned unexpected status %d", status)
	}
	if response.URL == "" {
		return "", errors.New("artifact-downloads returned no URL")
	}
	return response.URL, nil
}

func (c *internalClient) modelCall(
	ctx context.Context,
	jobID, attemptToken string,
	request background.ModelCallRequest,
) (modelCallPayload, error) {
	body := map[string]any{
		"attempt_token":            attemptToken,
		"logical_key":              request.LogicalKey,
		"model_profile_version_id": request.ModelProfileVersionID,
		"params":                   request.Params,
	}
	var response modelCallPayload
	status, err := c.post(
		ctx, "/internal/v1/background/jobs/"+url.PathEscape(jobID)+"/model-calls", body, &response)
	if err != nil {
		return modelCallPayload{}, err
	}
	if status != http.StatusOK {
		return modelCallPayload{}, fmt.Errorf("model-calls returned unexpected status %d", status)
	}
	return response, nil
}

// toolBuildBinding is the T037 job-scoped build read.
func (c *internalClient) toolBuildBinding(
	ctx context.Context,
	jobID, attemptToken string,
) (toolBuildBindingPayload, error) {
	var response toolBuildBindingPayload
	status, err := c.post(
		ctx,
		"/internal/v1/background/jobs/"+url.PathEscape(jobID)+"/tool-build",
		map[string]string{"attempt_token": attemptToken},
		&response,
	)
	if err != nil {
		return toolBuildBindingPayload{}, err
	}
	if status != http.StatusOK {
		return toolBuildBindingPayload{}, fmt.Errorf("tool-build binding returned unexpected status %d", status)
	}
	return response, nil
}

// evaluationBinding is the T026 job-scoped evaluation read. The job id and the
// attempt token are the whole request; the server resolves everything else.
func (c *internalClient) evaluationBinding(
	ctx context.Context,
	jobID, attemptToken string,
) (evaluationBindingPayload, error) {
	var response evaluationBindingPayload
	status, err := c.post(
		ctx,
		"/internal/v1/background/jobs/"+url.PathEscape(jobID)+"/evaluation",
		map[string]string{"attempt_token": attemptToken},
		&response,
	)
	if err != nil {
		return evaluationBindingPayload{}, err
	}
	if status != http.StatusOK {
		return evaluationBindingPayload{}, fmt.Errorf("evaluation binding returned unexpected status %d", status)
	}
	return response, nil
}

// evaluationRunPayload is the T052 bridge's create answer.
type evaluationRunPayload struct {
	RunID   string `json:"run_id"`
	Created bool   `json:"created"`
}

// evaluationRunFactsPayload is the T052 bridge's independent-truth answer.
type evaluationRunFactsPayload struct {
	RunID              string                   `json:"run_id"`
	State              string                   `json:"state"`
	ErrorCode          string                   `json:"error_code"`
	OutputArtifactID   *string                  `json:"output_artifact_id"`
	OutputSHA256       string                   `json:"output_sha256"`
	Output             json.RawMessage          `json:"output"`
	OutputCollected    bool                     `json:"output_collected"`
	EffectCounts       map[string]int64         `json:"effect_counts"`
	EffectsCollected   bool                     `json:"effects_collected"`
	Citations          []string                 `json:"citations"`
	CitationsCollected bool                     `json:"citations_collected"`
	Provenance         evaluation.RunProvenance `json:"provenance"`
	StartedAt          string                   `json:"started_at"`
	FinishedAt         string                   `json:"finished_at"`
}

// evaluationRun creates (or returns) one case's platform run.
func (c *internalClient) evaluationRun(
	ctx context.Context,
	jobID, attemptToken, caseID string,
	input json.RawMessage,
) (evaluationRunPayload, error) {
	body := map[string]any{
		"attempt_token": attemptToken,
		"case_id":       caseID,
		"input":         input,
	}
	var response evaluationRunPayload
	status, err := c.post(
		ctx,
		"/internal/v1/background/jobs/"+url.PathEscape(jobID)+"/evaluation-runs",
		body,
		&response,
	)
	if err != nil {
		return evaluationRunPayload{}, err
	}
	if status != http.StatusOK {
		return evaluationRunPayload{}, fmt.Errorf("evaluation-runs returned unexpected status %d", status)
	}
	if response.RunID == "" {
		return evaluationRunPayload{}, errors.New("evaluation-runs returned no run id")
	}
	return response, nil
}

// evaluationRunFacts reads one case run's independent truth.
func (c *internalClient) evaluationRunFacts(
	ctx context.Context,
	jobID, attemptToken, runID string,
) (evaluationRunFactsPayload, error) {
	var response evaluationRunFactsPayload
	status, err := c.post(
		ctx,
		"/internal/v1/background/jobs/"+url.PathEscape(jobID)+"/evaluation-runs/"+
			url.PathEscape(runID)+"/facts",
		map[string]string{"attempt_token": attemptToken},
		&response,
	)
	if err != nil {
		return evaluationRunFactsPayload{}, err
	}
	if status != http.StatusOK {
		return evaluationRunFactsPayload{}, fmt.Errorf("evaluation-run facts returned unexpected status %d", status)
	}
	return response, nil
}

// evaluationRunInteraction performs one predefined interaction on a case run.
func (c *internalClient) evaluationRunInteraction(
	ctx context.Context,
	jobID, attemptToken, runID, interaction string,
) error {
	body := map[string]string{"attempt_token": attemptToken, "interaction": interaction}
	status, err := c.post(
		ctx,
		"/internal/v1/background/jobs/"+url.PathEscape(jobID)+"/evaluation-runs/"+
			url.PathEscape(runID)+"/interactions",
		body,
		nil,
	)
	if err != nil {
		return err
	}
	if status != http.StatusOK {
		return fmt.Errorf("evaluation-run interaction returned unexpected status %d", status)
	}
	return nil
}
