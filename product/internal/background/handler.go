package background

import (
	"context"
	"database/sql"

	"orchara/platform/internal/objectstore"
)

// HandlerWork is everything a compiled handler may use.
//
// Store and DB are the maintenance process's own access to the object store and
// the platform database (the maintenance DSN). The job is a leased row: the
// handler may read its target's current state, but it never writes a business
// row: activation and failure recording belong to the server-side guard in
// Complete. Heartbeat may be called while a long step runs; the maintenance loop
// already heartbeats on a timer, so a handler with no long steps need not use it.
type HandlerWork struct {
	Job          *Job
	AttemptToken string
	Store        *objectstore.Store
	DB           *sql.DB
	Heartbeat    func(ctx context.Context) error
}

// Outcome is what a handler reports. State is succeeded (the guard then decides
// activation) or failed with a code from the retry table. A handler never
// invents a new code vocabulary silently: an unknown code is a business failure,
// so a typo fails the job instead of retrying forever.
type Outcome struct {
	State     State
	ErrorCode string
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
