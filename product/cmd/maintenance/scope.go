package main

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"time"

	"orchara/platform/internal/background"
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
