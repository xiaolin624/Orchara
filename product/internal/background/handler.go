package background

import (
	"context"
	"database/sql"

	"orchara/platform/internal/objectstore"
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
