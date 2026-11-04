package background

import (
	"context"
	"encoding/json"
	"time"

	"orchara/platform/internal/evaluation"
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
	// Evaluation returns the job-scoped evaluation binding: which evaluation
	// this job is, its kind, its dataset and the manifest hash its report must
	// declare. The server resolves all of them; the handler supplies only its
	// lease proof.
	Evaluation(ctx context.Context, jobID, attemptToken string) (EvaluationBinding, error)
	// EvaluationRun creates (or returns) the platform run that executes one
	// evaluation case, as the evaluation's owner, from the evaluation's frozen
	// snapshot. The handler supplies the case input bytes; the server seals them,
	// creates the run and its start job, and re-proves the owner's access. A
	// non-nil taskInput asks the server to bind the owner's own fixture to the
	// run as its task_input artifact (F13A/T056). It is the F13A/T052 bridge
	// (ADR 0011, extended by ADR 0015).
	EvaluationRun(ctx context.Context, jobID, attemptToken, caseID string, input []byte, taskInput *evaluation.TaskInputBinding) (EvaluationRunRef, error)
	// EvaluationRunFacts reads the independent truth of one evaluation case run:
	// its state, its sealed output artifact, the real operation ledger counts and
	// the citation sources that output declares. It is a platform read, never the
	// model's trace.
	EvaluationRunFacts(ctx context.Context, jobID, attemptToken, runID string) (EvaluationRunFacts, error)
	// EvaluationRunInteraction performs one predefined interaction on the case
	// run as the evaluation's owner (this build performs cancel only).
	EvaluationRunInteraction(ctx context.Context, jobID, attemptToken, runID, interaction string) error
	// ToolBuild returns the job-scoped build binding (T037): the admitted source
	// artifact/hash and the canonical dependency lock. The handler never reads
	// the tool row itself.
	ToolBuild(ctx context.Context, jobID, attemptToken string) (ToolBuildBinding, error)
	// DocumentIngest returns the job-scoped ingestion binding (T055): the
	// document/revision and the exact source artifact/hash/media type to parse.
	// The handler never reads the document row itself.
	DocumentIngest(ctx context.Context, jobID, attemptToken string) (DocumentIngestBinding, error)
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

// DocumentIngestBinding is the T055 job-scoped ingestion binding.
type DocumentIngestBinding struct {
	DocumentID       string
	KnowledgeBaseID  string
	DocumentRevision int64
	SourceArtifactID string
	SourceSHA256     string
	SourceMediaType  string
	ParserVersion    string
}

// ToolBuildBinding is the T037 job-scoped tool build binding. DependencyLock is
// the canonical JSON the sandbox must receive.
type ToolBuildBinding struct {
	ToolVersionID    string
	SourceArtifactID string
	SourceSHA256     string
	DependencyLock   json.RawMessage
	ManifestSHA256   string
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

// EvaluationRunRef is one evaluation case's platform run, as the bridge
// answers it (F13A/T052).
type EvaluationRunRef struct {
	RunID   string
	Created bool
}

// EvaluationRunFacts is the independent truth of one case run. Each *Collected
// flag separates "the platform said none" from "the fact could not be read":
// the checker treats an uncollected fact as a failure, never as zero.
type EvaluationRunFacts struct {
	RunID              string
	State              string
	ErrorCode          string
	OutputArtifactID   *string
	OutputSHA256       string
	Output             json.RawMessage
	OutputCollected    bool
	Effects            map[string]int64
	EffectsCollected   bool
	Citations          []string
	CitationsCollected bool
	// F13A/T056: the caller fixture bound to the run, by artifact id and hash.
	TaskInputArtifactID string
	TaskInputSHA256     string
	Provenance          evaluation.RunProvenance
	StartedAt           *time.Time
	FinishedAt          *time.Time
}
