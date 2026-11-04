package background

import (
	"context"
	"database/sql"
	"time"

	"orchara/platform/internal/embedding"
	"orchara/platform/internal/objectstore"
	"orchara/platform/internal/sandbox"
	"orchara/platform/internal/tools"
)

// HandlerWork is everything a compiled handler may use.
//
// Scope is the only way a handler reaches job-scoped platform data: it proves
// the lease and can only see the candidate/fixture/artifacts the server assigns
// to this job, through short-lived signed links. Store and DB are the
// maintenance process's own handles; a handler must not use them to read
// business rows (the T022 artifact_finalize interim read is the exception that
// T024 replaces for its own kind). Heartbeat may be called while a long step
// runs; the maintenance loop already heartbeats on a timer.
type HandlerWork struct {
	Job          *Job
	AttemptToken string
	Scope        JobScope
	Store        *objectstore.Store
	DB           *sql.DB
	Heartbeat    func(ctx context.Context) error
	// Embedder is the process's embedding seam (T055). The maintenance process
	// constructs it once from deployment configuration; a nil value means the
	// handler cannot embed and fails closed with
	// embedding_provider_not_configured instead of inventing vectors.
	Embedder *embedding.Service
	// Settings carries the maintenance process's operator-tunable handler
	// timeouts (T026). A zero value means "use the handler's documented default";
	// tests and deployments set short values explicitly rather than waiting a
	// minute for a bounded wait to expire.
	Settings HandlerSettings
	// Tools is the trusted preset-tool executor (T030). It is constructed and
	// configured by the maintenance process (python path, fixed script path,
	// per-run timeout); a nil executor means this process cannot run tool_run
	// jobs and the handler fails closed instead of guessing a command.
	Tools *tools.Executor
	// Sandbox is the T033 gVisor executor. The T037 tool_build handler uses it to
	// run deploy/sandbox/build_tool.py; a nil executor means this process cannot
	// run tool builds and the handler fails closed with a safe code.
	Sandbox SandboxRunner
}

// SandboxRunner is the executor seam for the build handler. The production
// implementation is *sandbox.Executor; tests may provide a fake that returns a
// checked-in driver report, but the real-stack tests exercise runsc.
type SandboxRunner interface {
	Run(ctx context.Context, request sandbox.Request, control sandbox.Control) (sandbox.Outcome, error)
}

// HandlerSettings is the per-process configuration a handler may consult.
//
// It exists instead of package-level env reads in handlers: the maintenance
// process loads and validates these once (flags/env) and passes them to every
// invocation, so a handler never parses configuration itself.
type HandlerSettings struct {
	// DatasetWaitTimeout bounds how long application_evaluate waits for its
	// dataset artifact to become ready.
	DatasetWaitTimeout time.Duration
	// EvaluationPollInterval is how often the waiting handler re-checks the
	// job-scoped artifact status (and heartbeats between checks).
	EvaluationPollInterval time.Duration
	// ReportWaitTimeout bounds how long a handler waits for its own report
	// artifact to be finalized by the system_io pool.
	ReportWaitTimeout time.Duration
	// ToolBuildTimeout bounds one sandboxed tool build (T037). A zero value means
	// the handler's documented default.
	ToolBuildTimeout time.Duration
	// ArtifactReadyTimeout bounds how long document_ingest waits for its staged
	// chunk-body artifacts to be activated by the system_io pool (T055). Zero
	// means the handler's documented default.
	ArtifactReadyTimeout time.Duration
	// ArtifactReadyPoll is the interval between those status checks. Zero means
	// the documented default.
	ArtifactReadyPoll time.Duration
}

// Outcome is what a handler reports. State is succeeded (the guard then decides
// activation) or failed with a code from the retry table. A handler never
// invents a new code vocabulary silently: an unknown code is a business failure,
// so a typo fails the job instead of retrying forever.
//
// ResultArtifactID is the artifact the handler registered as its result; only
// kinds whose completion accepts a result artifact set it. The server re-checks
// ownership/readiness before recording it.
type Outcome struct {
	State            State
	ErrorCode        string
	ResultArtifactID string
}

// Handler is one compile-time kind implementation. It returns an outcome rather
// than an error: infrastructure problems are expressed as a retryable error_code
// (transient_storage_io / transient_internal), which keeps classification in one
// table instead of in each handler.
type Handler func(ctx context.Context, work HandlerWork) Outcome

// Registry is the compile-time kind -> handler map. A kind that is not in the
// map is never claimed by this process, even if it is in the whitelist.
type Registry map[Kind]Handler

// SupportedKinds lists the registry in whitelist order, so claim requests and
// CLI output are deterministic.
func (r Registry) SupportedKinds() []Kind {
	kinds := make([]Kind, 0, len(r))
	for _, kind := range allKinds {
		if _, ok := r[kind]; ok {
			kinds = append(kinds, kind)
		}
	}
	return kinds
}

// Lookup returns the handler for kind.
func (r Registry) Lookup(kind Kind) (Handler, bool) {
	handler, ok := r[kind]
	return handler, ok
}

// Failed builds a failed outcome with a stable code.
func Failed(code string) Outcome {
	return Outcome{State: StateFailed, ErrorCode: code}
}

// Succeeded reports that the handler's work is done; activation, if any, is the
// server's decision.
func Succeeded() Outcome {
	return Outcome{State: StateSucceeded}
}

// SucceededWith reports success and names the result artifact the server must
// re-check (ready, owned by the job, linked to it) before recording it.
func SucceededWith(resultArtifactID string) Outcome {
	return Outcome{State: StateSucceeded, ResultArtifactID: resultArtifactID}
}
