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

	"orchara/platform/internal/background"
	"orchara/platform/internal/config"
)

// maintenanceRole is the capacity this client presents. The server requires the
// X-Internal-Role header to equal the certificate's organization; both are fixed
// by this binary, never by a request.
const maintenanceRole = "maintenance"

// lease is the contract's BackgroundLease as the maintenance client reads it.
type lease struct {
	JobID            string  `json:"job_id"`
	Kind             string  `json:"kind"`
	TargetID         string  `json:"target_id"`
	AttemptToken     string  `json:"attempt_token"`
	LeaseDeadline    string  `json:"lease_deadline"`
	ExpectedRevision *int64  `json:"expected_revision"`
	InputArtifactID  *string `json:"input_artifact_id"`
	InputURL         *string `json:"input_url"`
}

// jobView is the contract's BackgroundJob response to a result submission.
type jobView struct {
	ID        string  `json:"id"`
	Kind      string  `json:"kind"`
	State     string  `json:"state"`
	ErrorCode *string `json:"error_code"`
}

type resultBody struct {
	AttemptToken     string  `json:"attempt_token"`
	EventID          string  `json:"event_id"`
	State            string  `json:"state"`
	ResultArtifactID *string `json:"result_artifact_id,omitempty"`
	ErrorCode        *string `json:"error_code,omitempty"`
}

type claimBody struct {
	WorkerID       string   `json:"worker_id"`
	SupportedKinds []string `json:"supported_kinds"`
}

// apiError is a contract error envelope from the internal API.
type apiError struct {
	Status  int
	Code    string
	Message string
}

func (e *apiError) Error() string {
	return fmt.Sprintf("internal API %d %s: %s", e.Status, e.Code, e.Message)
}

// ErrorCode exposes the stable contract code to the handler layer without
// importing this package's client type (handlers map codes to job outcomes).
func (e *apiError) ErrorCode() string { return e.Code }

func isAPICode(err error, code string) bool {
	var apiErr *apiError
	return errors.As(err, &apiErr) && apiErr.Code == code
}

// internalClient is the maintenance side of the mTLS protocol. It only ever
// calls the three contract endpoints; there is no generic "run this path".
type internalClient struct {
	baseURL string
	http    *http.Client
}

func newInternalClient(cfg *config.MaintenanceConfig) (*internalClient, error) {
	certificate, err := tls.LoadX509KeyPair(cfg.ClientCert, cfg.ClientKey)
	if err != nil {
		return nil, errors.New("cannot load the internal client certificate")
	}
	caPEM, err := os.ReadFile(cfg.CA)
	if err != nil {
		return nil, errors.New("cannot read the internal CA file")
	}
	pool := x509.NewCertPool()
	if !pool.AppendCertsFromPEM(caPEM) {
		return nil, errors.New("the internal CA file contains no certificate")
	}
	if _, err := url.Parse(cfg.InternalURL); err != nil {
		return nil, errors.New("the internal URL is not valid")
	}
	transport := &http.Transport{
		TLSClientConfig: &tls.Config{
			MinVersion:   tls.VersionTLS12,
			Certificates: []tls.Certificate{certificate},
			RootCAs:      pool,
		},
		Proxy: nil,
	}
	return &internalClient{
		baseURL: strings.TrimSuffix(cfg.InternalURL, "/"),
		http:    &http.Client{Transport: transport, Timeout: 30 * time.Second},
	}, nil
}

// claim asks for one compatible ready job. A nil lease means 204.
func (c *internalClient) claim(ctx context.Context, workerID string, kinds []string) (*lease, error) {
	var response lease
	status, err := c.post(ctx, "/internal/v1/background/jobs/claim", claimBody{
		WorkerID:       workerID,
		SupportedKinds: kinds,
	}, &response)
	if err != nil {
		return nil, err
	}
	if status == http.StatusNoContent {
		return nil, nil
	}
	if status != http.StatusOK {
		return nil, fmt.Errorf("claim returned unexpected status %d", status)
	}
	if response.JobID == "" || response.AttemptToken == "" || response.Kind == "" {
		return nil, errors.New("claim response is missing its job identity or attempt token")
	}
	return &response, nil
}

func (c *internalClient) heartbeat(ctx context.Context, jobID, attemptToken string) error {
	status, err := c.post(ctx, "/internal/v1/background/jobs/"+url.PathEscape(jobID)+"/heartbeat",
		map[string]string{"attempt_token": attemptToken}, nil)
	if err != nil {
		return err
	}
	if status != http.StatusOK {
		return fmt.Errorf("heartbeat returned unexpected status %d", status)
	}
	return nil
}

func (c *internalClient) submitResult(
	ctx context.Context,
	jobID, attemptToken, eventID string,
	outcome background.Outcome,
) (*jobView, error) {
	body := resultBody{
		AttemptToken: attemptToken,
		EventID:      eventID,
		State:        string(outcome.State),
	}
	if outcome.ErrorCode != "" {
		code := outcome.ErrorCode
		body.ErrorCode = &code
	}
	if outcome.ResultArtifactID != "" {
		artifactID := outcome.ResultArtifactID
		body.ResultArtifactID = &artifactID
	}
	var response jobView
	status, err := c.post(ctx, "/internal/v1/background/jobs/"+url.PathEscape(jobID)+"/result", body, &response)
	if err != nil {
		return nil, err
	}
	if status != http.StatusOK {
		return nil, fmt.Errorf("result returned unexpected status %d", status)
	}
	return &response, nil
}

// post sends one JSON body and decodes a JSON response into target (when it is
// non-nil). A 204 is returned with no decoding. Non-2xx becomes *apiError.
func (c *internalClient) post(ctx context.Context, path string, payload, target any) (int, error) {
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
	// Certificate and header must agree; the server refuses a mismatch.
	request.Header.Set("X-Internal-Role", maintenanceRole)
	response, err := c.http.Do(request)
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
		return response.StatusCode, decodeAPIError(response.StatusCode, body)
	}
	if response.StatusCode == http.StatusNoContent || target == nil {
		return response.StatusCode, nil
	}
	if err := json.Unmarshal(body, target); err != nil {
		return response.StatusCode, errors.New("the internal response is not valid JSON")
	}
	return response.StatusCode, nil
}

func decodeAPIError(status int, body []byte) error {
	var envelope struct {
		Error struct {
			Code    string `json:"code"`
			Message string `json:"message"`
		} `json:"error"`
	}
	if err := json.Unmarshal(body, &envelope); err != nil || envelope.Error.Code == "" {
		return &apiError{Status: status, Code: "invalid_response", Message: "the internal API returned a non-contract error"}
	}
	return &apiError{Status: status, Code: envelope.Error.Code, Message: envelope.Error.Message}
}
