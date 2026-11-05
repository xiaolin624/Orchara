package background

// F08/T079: the route_request completion guard.
//
// The routing round itself commits the user-visible outcome server-side, so a
// successful job needs no completion rewrite. A **terminally failed** job, however,
// must not leave its request in `routing`: that request is the conversation's slot
// holder and the queue would be stranded. The guard runs after the server has
// exhausted the retry table (a failure that will be retried returns early), fails
// the still-routing request with the stable `route_failed` code and advances the
// conversation queue through the injected T074 seam. A deterministic refusal that
// the router already committed moves the request out of `routing`, so the guard
// observes a non-routing state and writes nothing.

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
)

// CodeRouteFailed is the stable request error code for a routing job that failed
// terminally outside the router's own typed refusals.
const CodeRouteFailed = "route_failed"

func guardRouteRequestCompletion(
	ctx context.Context, c *Controller, tx *sql.Tx, job *Job, completion *Completion,
) error {
	if completion.State != StateFailed {
		return nil
	}
	// A failure the fixed retry table will retry is not terminal: the request stays
	// in routing and keeps its slot while the job is re-claimed.
	if retryAllowed(job, completion.ErrorCode) {
		return nil
	}
	var (
		state          string
		conversationID string
		ownerID        string
	)
	err := tx.QueryRowContext(ctx, `
		SELECT state, conversation_id::text, owner_id::text
		  FROM platform.requests
		 WHERE id = $1::uuid
		 FOR UPDATE`, job.TargetID,
	).Scan(&state, &conversationID, &ownerID)
	if errors.Is(err, sql.ErrNoRows) {
		// The request was physically removed (F11 purge): nothing to fail.
		return nil
	}
	if err != nil {
		return fmt.Errorf("reading the failed route request: %w", err)
	}
	if state != "routing" {
		return nil
	}
	code := completion.ErrorCode
	switch code {
	case "", CodeTransientInternal, CodeTransientStorageIO, CodeLeaseExpired, "route_refused":
		// A retry-ceiling or protocol failure is terminal from the user's point of
		// view; expose one stable code instead of leaking the retry vocabulary.
		code = CodeRouteFailed
	}
	if _, err := tx.ExecContext(ctx, `
		UPDATE platform.requests
		   SET state = 'failed', error_code = $2, revision = revision + 1,
		       selected_version_id = NULL, updated_at = now()
		 WHERE id = $1::uuid AND state = 'routing'`, job.TargetID, code); err != nil {
		return fmt.Errorf("failing the stranded route request: %w", err)
	}
	metadata := map[string]any{
		"error_code": code,
		"job_error":  completion.ErrorCode,
		"source":     "route_request_guard",
	}
	payload, err := json.Marshal(metadata)
	if err != nil {
		return fmt.Errorf("encoding the failed route audit: %w", err)
	}
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO platform.audit_events
		       (id, actor_id, action, resource_type, resource_id, metadata)
		VALUES (gen_random_uuid(), $1::uuid, 'request.routing_failed', 'request',
		        $2::uuid, $3::jsonb)`,
		ownerID, job.TargetID, string(payload),
	); err != nil {
		return fmt.Errorf("recording the failed route request: %w", err)
	}
	if c.QueueAdvancer != nil {
		if _, err := c.QueueAdvancer(ctx, tx, conversationID); err != nil {
			return err
		}
	}
	return nil
}
