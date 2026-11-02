package background

import (
	"context"
	"time"
)

// JobScope is how a compiled handler reaches job-scoped platform data.
//
// A handler never reads or writes platform business tables directly: it holds a
// leased job and must prove that lease on every call. The implementation is the
// maintenance process's client of the internal mTLS API (cmd/maintenance), so a
// handler can only reach the candidate, fixture and artifacts that the server
// decides this job may reach. T022's interim "read the artifact row with the
// maintenance DSN" path is gone for the handlers that use this seam.
//
// The byte-moving methods (FetchURL, PutURL) receive a short-lived signed link
// the server minted for one operation and one object; a handler never constructs
// an object path or an API URL itself.
type JobScope interface {
	// DownloadFixture mints a signed GET link for an artifact this job may read.
	Download(ctx context.Context, jobID, attemptToken, artifactID string) (string, error)
	// Upload reserves a staging target for an artifact this job will produce.
	Upload(ctx context.Context, jobID, attemptToken string, request UploadRequest) (UploadTicket, error)
	// Complete moves an uploaded artifact to scanning and enqueues finalize.
	Complete(ctx context.Context, jobID, attemptToken, artifactID, sha256 string) (ArtifactView, error)
	// Status reads one in-scope artifact's state without any bytes.
	Status(ctx context.Context, jobID, attemptToken, artifactID string) (ArtifactView, error)
	// ModelCall invokes the job's candidate through the platform proxy.
	ModelCall(ctx context.Context, jobID, attemptToken string, request ModelCallRequest) (ModelCallResult, error)
	// FetchURL reads a signed GET link into memory, capped at maxBytes.
	FetchURL(ctx context.Context, url string, maxBytes int64) ([]byte, error)
	// PutURL writes body to a signed PUT link.
	PutURL(ctx context.Context, url string, body []byte) error
}

// UploadRequest is the handler half of createBackgroundUpload.
type UploadRequest struct {
	MediaType string
	SizeBytes int64
	Purpose   string
}

// UploadTicket is the contract's Upload: where to PUT and by when.
type UploadTicket struct {
	ArtifactID string
	URL        string
	ExpiresAt  time.Time
}

// ArtifactView is the contract's Artifact projection.
type ArtifactView struct {
	ID        string
	State     string
	MediaType string
	SizeBytes int64
}

// ModelCallRequest is the handler half of invokeBackgroundModel. The worker
// names the candidate it believes it owns and a logical key; it never names a
// fixture, an endpoint or a credential — the server resolves all three.
type ModelCallRequest struct {
	LogicalKey            string
	ModelProfileVersionID string
	Params                map[string]any
}

// ModelCallResult is the contract's model-call answer. Data is the sanitized
// provider result; it never contains a secret or a raw credential.
type ModelCallResult struct {
	CallID           string
	State            string
	ResultArtifactID *string
	Data             map[string]any
}
