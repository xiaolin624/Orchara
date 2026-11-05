package requests

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"strings"

	"orchara/platform/internal/deletion"
	"orchara/platform/internal/uuid"
)

// DeletionTarget is the conversation adapter of the F11/T069 shared deletion
// foundation. The tombstone commits in the caller's transaction before the API
// answers 202, so every conversation/request/message read refuses immediately;
// the queued requests are cancelled in the same transaction, and the affected
// non-terminal Runs go through the foundation's existing execution-cancel fan-out
// after the commit.
type DeletionTarget struct {
	Service *Service
}

// NewDeletionTarget builds the adapter the API registers with deletion.Service.
func NewDeletionTarget(service *Service) *DeletionTarget {
	return &DeletionTarget{Service: service}
}

// ResourceType implements deletion.Target.
func (t *DeletionTarget) ResourceType() deletion.ResourceType {
	return deletion.ResourceConversation
}

// Delete implements deletion.Target. It only writes the tombstone, the request
// state changes and the invalidation of pending interactions; the foundation owns
// the job row and the audit event, and the cancellation fan-out runs after the
// commit.
func (t *DeletionTarget) Delete(
	ctx context.Context, tx *sql.Tx, ownerID, conversationID string,
) (deletion.Plan, error) {
	if !uuid.Valid(ownerID) || !uuid.Valid(conversationID) {
		return deletion.Plan{}, deletion.ErrNotFound
	}
	var (
		revision int64
		deleted  sql.NullTime
	)
	err := tx.QueryRowContext(ctx, `
		SELECT revision, deleted_at
		  FROM platform.conversations
		 WHERE id = $1::uuid AND owner_id = $2::uuid
		 FOR UPDATE`, conversationID, ownerID,
	).Scan(&revision, &deleted)
	if errors.Is(err, sql.ErrNoRows) {
		return deletion.Plan{}, deletion.ErrNotFound
	}
	if err != nil {
		return deletion.Plan{}, fmt.Errorf("locking the conversation for deletion: %w", err)
	}

	plan := deletion.Plan{
		Revision:       revision,
		AlreadyDeleted: deleted.Valid,
	}
	if !deleted.Valid {
		if _, err := tx.ExecContext(ctx, `
			UPDATE platform.conversations
			   SET deleted_at = now(), revision = revision + 1
			 WHERE id = $1::uuid`, conversationID); err != nil {
			return deletion.Plan{}, fmt.Errorf("tombstoning the conversation: %w", err)
		}
		plan.Revision = revision + 1
	}

	// Every non-terminal Run of the conversation is cancelled through the existing
	// execution path after the tombstone commits (the foundation owns that call).
	runs, err := t.affectedRunsTx(ctx, tx, conversationID)
	if err != nil {
		return deletion.Plan{}, err
	}
	plan.AffectedRuns = runs

	// Queued/routing/awaiting requests become cancelled in the tombstone
	// transaction, so a concurrent submit that already lost the conversation lock
	// cannot leave a live request behind, and no request keeps a queue slot.
	if _, err := tx.ExecContext(ctx, `
		UPDATE platform.requests
		   SET state = 'cancelled', revision = revision + 1, updated_at = now()
		 WHERE conversation_id = $1::uuid
		   AND state IN ('queued', 'routing', 'awaiting_input', 'awaiting_selection')`,
		conversationID); err != nil {
		return deletion.Plan{}, fmt.Errorf("cancelling the conversation requests: %w", err)
	}
	if _, err := tx.ExecContext(ctx, `
		UPDATE platform.interactions AS i
		   SET state = 'invalidated'
		 WHERE i.state = 'pending'
		   AND i.request_id IN (
		        SELECT id FROM platform.requests WHERE conversation_id = $1::uuid
		       )`, conversationID); err != nil {
		return deletion.Plan{}, fmt.Errorf("invalidating the conversation interactions: %w", err)
	}

	details, err := t.detailsTx(ctx, tx, conversationID)
	if err != nil {
		return deletion.Plan{}, err
	}
	plan.Details = details
	return plan, nil
}

// affectedRunsTx selects every non-terminal Run of the conversation, top-level or
// child, so the foundation's cancel fan-out reaches all of them.
func (t *DeletionTarget) affectedRunsTx(
	ctx context.Context, tx *sql.Tx, conversationID string,
) ([]deletion.AffectedRun, error) {
	rows, err := tx.QueryContext(ctx, `
		SELECT id::text, state
		  FROM platform.runs
		 WHERE conversation_id = $1::uuid
		   AND deleted_at IS NULL
		   AND state NOT IN ('succeeded', 'failed', 'cancelled')
		 ORDER BY created_at, id`, conversationID)
	if err != nil {
		return nil, fmt.Errorf("reading the conversation runs for cancellation: %w", err)
	}
	defer rows.Close()
	runs := make([]deletion.AffectedRun, 0, 4)
	for rows.Next() {
		var run deletion.AffectedRun
		if err := rows.Scan(&run.RunID, &run.State); err != nil {
			return nil, fmt.Errorf("reading the conversation runs for cancellation: %w", err)
		}
		runs = append(runs, run)
	}
	return runs, rows.Err()
}

// detailsTx collects the copy descriptors each F11 cleanup scope consumes. The
// object store scope carries the sealed artifact keys (request inputs, attachments,
// run outputs and the confirmed handover summaries); the other scopes carry the
// ids their owning ticket needs, never another user's content.
func (t *DeletionTarget) detailsTx(
	ctx context.Context, tx *sql.Tx, conversationID string,
) (map[deletion.Scope]any, error) {
	objectRows, err := tx.QueryContext(ctx, `
		SELECT a.id::text, a.object_key, a.purpose
		  FROM platform.artifacts AS a
		 WHERE a.id IN (
		        SELECT input_artifact_id FROM platform.requests
		         WHERE conversation_id = $1::uuid
		        UNION SELECT unnest(artifact_ids) FROM platform.requests
		         WHERE conversation_id = $1::uuid
		        UNION SELECT input_artifact_id FROM platform.runs
		         WHERE conversation_id = $1::uuid
		        UNION SELECT output_artifact_id FROM platform.runs
		         WHERE conversation_id = $1::uuid AND output_artifact_id IS NOT NULL
		        UNION SELECT summary_artifact_id FROM platform.request_handoffs
		         WHERE conversation_id = $1::uuid
		        UNION SELECT i.proposal_artifact_id FROM platform.interactions AS i
		         JOIN platform.requests AS r ON r.id = i.request_id
		         WHERE r.conversation_id = $1::uuid
		        UNION SELECT i.response_artifact_id FROM platform.interactions AS i
		         JOIN platform.requests AS r ON r.id = i.request_id
		         WHERE r.conversation_id = $1::uuid AND i.response_artifact_id IS NOT NULL
		       )
		 ORDER BY a.id`, conversationID)
	if err != nil {
		return nil, fmt.Errorf("reading the conversation object scope: %w", err)
	}
	defer objectRows.Close()
	objects := make([]map[string]any, 0, 8)
	for objectRows.Next() {
		var (
			artifactID string
			objectKey  string
			purpose    string
		)
		if err := objectRows.Scan(&artifactID, &objectKey, &purpose); err != nil {
			return nil, fmt.Errorf("reading the conversation object scope: %w", err)
		}
		objects = append(objects, map[string]any{
			"artifact_id": artifactID,
			"object_key":  objectKey,
			"purpose":     purpose,
		})
	}
	if err := objectRows.Err(); err != nil {
		return nil, err
	}

	requestIDs, err := t.stringColumnTx(ctx, tx, `
		SELECT id::text FROM platform.requests
		 WHERE conversation_id = $1::uuid ORDER BY created_at, id`, conversationID)
	if err != nil {
		return nil, err
	}
	runRows, err := tx.QueryContext(ctx, `
		SELECT id::text, checkpoint_thread_id::text FROM platform.runs
		 WHERE conversation_id = $1::uuid ORDER BY created_at, id`, conversationID)
	if err != nil {
		return nil, fmt.Errorf("reading the conversation run ids: %w", err)
	}
	defer runRows.Close()
	runIDs := make([]string, 0, 4)
	checkpointIDs := make([]string, 0, 4)
	for runRows.Next() {
		var runID, checkpointID string
		if err := runRows.Scan(&runID, &checkpointID); err != nil {
			return nil, fmt.Errorf("reading the conversation run ids: %w", err)
		}
		runIDs = append(runIDs, runID)
		checkpointIDs = append(checkpointIDs, checkpointID)
	}
	if err := runRows.Err(); err != nil {
		return nil, err
	}
	interactionIDs, err := t.stringColumnTx(ctx, tx, `
		SELECT i.id::text FROM platform.interactions AS i
		  JOIN platform.requests AS r ON r.id = i.request_id
		 WHERE r.conversation_id = $1::uuid ORDER BY i.created_at, i.id`, conversationID)
	if err != nil {
		return nil, err
	}
	handoffIDs, err := t.stringColumnTx(ctx, tx, `
		SELECT request_id::text FROM platform.request_handoffs
		 WHERE conversation_id = $1::uuid ORDER BY confirmed_at, request_id`, conversationID)
	if err != nil {
		return nil, err
	}

	return map[deletion.Scope]any{
		deletion.ScopeObjectStore: map[string]any{"items": objects},
		deletion.ScopeSearchIndex: map[string]any{
			"request_ids":     requestIDs,
			"interaction_ids": interactionIDs,
		},
		deletion.ScopeRetrievalCache: map[string]any{"run_ids": runIDs},
		deletion.ScopeCheckpoints: map[string]any{
			"run_ids":               runIDs,
			"checkpoint_thread_ids": checkpointIDs,
		},
		deletion.ScopeSandboxTemp:       map[string]any{"run_ids": runIDs},
		deletion.ScopeThirdPartyHistory: map[string]any{"run_ids": runIDs},
		deletion.ScopeBackupCatalog: map[string]any{
			"conversation_id": conversationID,
			"handoff_ids":     handoffIDs,
		},
	}, nil
}

func (t *DeletionTarget) stringColumnTx(
	ctx context.Context, tx *sql.Tx, query, conversationID string,
) ([]string, error) {
	rows, err := tx.QueryContext(ctx, query, conversationID)
	if err != nil {
		return nil, fmt.Errorf("reading a conversation deletion scope: %w", err)
	}
	defer rows.Close()
	values := make([]string, 0, 4)
	for rows.Next() {
		var value string
		if err := rows.Scan(&value); err != nil {
			return nil, fmt.Errorf("reading a conversation deletion scope: %w", err)
		}
		values = append(values, strings.TrimSpace(value))
	}
	return values, rows.Err()
}
