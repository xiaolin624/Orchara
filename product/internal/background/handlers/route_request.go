package handlers

// F08/T079 route_request handler.
//
// Routing must run behind a durable job lease so a crashed process cannot leave a
// request stuck in `routing` (IMPLEMENTATION-DETAILS.md §43). The handler itself is
// deliberately thin: the candidate query, the governed control-model call, the
// validation of the model's suggestion and every write happen server-side, behind
// the job-scoped mTLS protocol. The handler supplies only its lease proof; it never
// reads user text, a candidate row or a credential, and it cannot submit a decision
// of its own.
//
// The server's round is idempotent (it looks up an existing interaction/Run first),
// so a retried handler cannot create a second interaction or Run. A deterministic
// refusal is already committed on the Request and answers 200; only a transient
// control-model failure maps to a retryable code.

import (
	"context"
	"errors"

	"orchara/platform/internal/background"
)

// RouteRequest executes one routing round for the job's target request.
func RouteRequest(ctx context.Context, work background.HandlerWork) background.Outcome {
	if work.Scope == nil || work.Job == nil {
		return background.Failed(background.CodeTransientInternal)
	}
	if _, err := work.Scope.Route(ctx, work.Job.ID, work.AttemptToken); err != nil {
		if errors.Is(err, background.ErrRouteTransient) {
			// The control model failed transiently. The fixed retry table bounds the
			// attempts; the request stays in `routing` and the conversation slot is
			// still held by it, so nothing is stranded or double-created.
			return background.Failed(background.CodeTransientInternal)
		}
		// A stale/foreign job, a missing request or an internal protocol refusal is
		// deterministic; retrying the same lease cannot succeed.
		return background.Failed("route_refused")
	}
	return background.Succeeded()
}
