package artifacts

// F11/T069: the artifact deletion adapter for internal/deletion.
//
// The adapter owns the artifact-specific tombstone and the "is this artifact a
// configuration input somebody still needs?" question. The foundation owns the
// job row, audit and run-cancellation fan-out.
//
// Tombstone semantics reuse the columns the existing read paths already filter:
// `deleted_at`, `state='deleted'` and a revision bump. `Get`, `readyRow`,
// `DownloadURL` and `OpenContent` therefore refuse the artifact in the same
// transaction that answers 202; the background finalize guard already refuses a
// tombstoned row too.
//
// Reference rule (DATA-MODEL.md §"删除与保留"): an artifact referenced by a
// published application version, a recoverable (non-terminal) run, a model
// profile, a tool version, an evaluation report or a live background job is a
// configuration input, not disposable user content: 409 resource_in_use. A
// knowledge source artifact is user content: deleting it also tombstones the
// document(s) derived from it, so retrieval/citations/cache refuse through the
// same `kd.deleted_at` predicate they already use.

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"sort"

	"orchara/platform/internal/deletion"
)

// DeletionTarget adapts the user artifact service to deletion.Target.
type DeletionTarget struct {
	Service *Service
}

// NewDeletionTarget builds the adapter. The service's DB is the transaction peer.
func NewDeletionTarget(service *Service) *DeletionTarget {
	return &DeletionTarget{Service: service}
}

// ResourceType implements deletion.Target.
func (t *DeletionTarget) ResourceType() deletion.ResourceType {
	return deletion.ResourceArtifact
}

// artifactDeleteRow is everything the tombstone and the scope plan need.
type artifactDeleteRow struct {
	ID         string
	OwnerID    string
	ObjectKey  string
	StagingKey sql.NullString
	SHA256     sql.NullString
	SizeBytes  sql.NullInt64
	Purpose    string
	State      string
	Revision   int64
	DeletedAt  sql.NullTime
}

// Delete implements deletion.Target.
func (t *DeletionTarget) Delete(
	ctx context.Context, tx *sql.Tx, ownerID, artifactID string,
) (deletion.Plan, error) {
	var (
		row     artifactDeleteRow
		deleted sql.NullTime
	)
	err := tx.QueryRowContext(ctx, `
		SELECT id::text, owner_id::text, object_key, staging_object_key, sha256,
		       size_bytes, purpose, state, revision, deleted_at
		  FROM platform.artifacts
		 WHERE id = $1::uuid AND owner_id = $2::uuid
		 FOR UPDATE`, artifactID, ownerID,
	).Scan(
		&row.ID, &row.OwnerID, &row.ObjectKey, &row.StagingKey, &row.SHA256,
		&row.SizeBytes, &row.Purpose, &row.State, &row.Revision, &deleted,
	)
	if errors.Is(err, sql.ErrNoRows) {
		return deletion.Plan{}, deletion.ErrNotFound
	}
	if err != nil {
		return deletion.Plan{}, fmt.Errorf("reading the artifact for deletion: %w", err)
	}
	row.DeletedAt = deleted

	reference, err := t.referencesTx(ctx, tx, artifactID)
	if err != nil {
		return deletion.Plan{}, err
	}
	if len(reference) > 0 {
		return deletion.Plan{}, &deletion.InUseError{References: reference}
	}

	runs, err := t.linkedRunsTx(ctx, tx, ownerID, artifactID)
	if err != nil {
		return deletion.Plan{}, err
	}
	affected := nonTerminalRuns(runs)

	if deleted.Valid || row.State == "deleted" {
		chunkIDs, err := t.chunkBodyIDsTx(ctx, tx, artifactID)
		if err != nil {
			return deletion.Plan{}, err
		}
		plan := deletion.Plan{
			Revision:       row.Revision,
			AlreadyDeleted: true,
			AffectedRuns:   affected,
		}
		plan.Details, err = t.scopeDetailsTx(ctx, tx, row, nil, nil, chunkIDs)
		if err != nil {
			return deletion.Plan{}, err
		}
		return plan, nil
	}

	// A knowledge source is user content: tombstone every live document derived
	// from it in this same transaction. The retrieval/citation/cache predicates
	// already filter kd.deleted_at, so the next query refuses the content.
	documentIDs, baseIDs, err := tombstoneDerivedDocumentsTx(ctx, tx, artifactID)
	if err != nil {
		return deletion.Plan{}, err
	}
	chunkIDs, err := t.chunkBodyIDsTx(ctx, tx, artifactID)
	if err != nil {
		return deletion.Plan{}, err
	}

	nextRevision := row.Revision + 1
	result, err := tx.ExecContext(ctx, `
		UPDATE platform.artifacts
		   SET deleted_at = now(), state = 'deleted', revision = $3
		 WHERE id = $1::uuid AND owner_id = $2::uuid
		   AND deleted_at IS NULL AND revision = $4`,
		artifactID, ownerID, nextRevision, row.Revision,
	)
	if err != nil {
		return deletion.Plan{}, fmt.Errorf("tombstoning the artifact: %w", err)
	}
	affectedRows, err := result.RowsAffected()
	if err != nil {
		return deletion.Plan{}, fmt.Errorf("counting the tombstoned artifact: %w", err)
	}
	if affectedRows != 1 {
		return deletion.Plan{}, errors.New("the locked artifact changed before it was tombstoned")
	}
	row.Revision = nextRevision
	row.State = "deleted"

	plan := deletion.Plan{
		Revision:     nextRevision,
		AffectedRuns: affected,
	}
	plan.Details, err = t.scopeDetailsTx(ctx, tx, row, documentIDs, baseIDs, chunkIDs)
	if err != nil {
		return deletion.Plan{}, err
	}
	return plan, nil
}

// referencesTx names the configuration references that make a plain user
// content delete a 409 instead. The values are reference classes only; no other
// user's id or content is returned.
func (t *DeletionTarget) referencesTx(
	ctx context.Context, tx *sql.Tx, artifactID string,
) ([]string, error) {
	checks := []struct {
		reference string
		query     string
	}{
		{
			"application_version",
			`SELECT 1 FROM platform.application_versions
			  WHERE manifest_artifact_id = $1::uuid LIMIT 1`,
		},
		{
			"recoverable_run",
			`SELECT 1 FROM platform.execution_snapshots AS s
			  JOIN platform.runs AS r ON r.execution_snapshot_id = s.id
			  WHERE s.manifest_artifact_id = $1::uuid
			    AND r.deleted_at IS NULL
			    AND r.state NOT IN ('succeeded', 'failed', 'cancelled')
			  LIMIT 1`,
		},
		{
			"model_profile_version",
			`SELECT 1 FROM platform.model_profile_versions
			  WHERE manifest_artifact_id = $1::uuid AND revoked_at IS NULL LIMIT 1`,
		},
		{
			"tool_version",
			`SELECT 1 FROM platform.tool_versions
			  WHERE source_artifact_id = $1::uuid AND state <> 'revoked' LIMIT 1`,
		},
		{
			"evaluation_report",
			`SELECT 1 FROM platform.evaluation_reports
			  WHERE result_artifact_id = $1::uuid LIMIT 1`,
		},
		{
			"background_job",
			`SELECT 1 FROM platform.background_jobs
			  WHERE (input_artifact_id = $1::uuid OR result_artifact_id = $1::uuid)
			    AND state IN ('ready', 'leased') LIMIT 1`,
		},
	}
	references := make([]string, 0, len(checks))
	var found bool
	for _, check := range checks {
		if err := tx.QueryRowContext(ctx, check.query, artifactID).Scan(&found); err != nil {
			if errors.Is(err, sql.ErrNoRows) {
				continue
			}
			return nil, fmt.Errorf("checking artifact reference %s: %w", check.reference, err)
		}
		references = append(references, check.reference)
	}
	sort.Strings(references)
	return references, nil
}

// linkedRun is one run that used the artifact, in any state.
type linkedRun struct {
	ID                 string
	State              string
	CheckpointThreadID string
}

// linkedRunsTx reads every caller-owned run linked to the artifact through its
// input or the run_artifacts relation. The owner filter is part of the query.
func (t *DeletionTarget) linkedRunsTx(
	ctx context.Context, tx *sql.Tx, ownerID, artifactID string,
) ([]linkedRun, error) {
	rows, err := tx.QueryContext(ctx, `
		SELECT DISTINCT r.id::text, r.state, r.checkpoint_thread_id::text
		  FROM platform.runs AS r
		 WHERE r.owner_id = $1::uuid AND r.deleted_at IS NULL
		   AND (r.input_artifact_id = $2::uuid
		        OR EXISTS (SELECT 1 FROM platform.run_artifacts AS ra
		                    WHERE ra.run_id = r.id AND ra.artifact_id = $2::uuid))
		 ORDER BY 1`, ownerID, artifactID)
	if err != nil {
		return nil, fmt.Errorf("reading the runs linked to the artifact: %w", err)
	}
	defer func() { _ = rows.Close() }()
	runs := make([]linkedRun, 0, 4)
	for rows.Next() {
		var run linkedRun
		if err := rows.Scan(&run.ID, &run.State, &run.CheckpointThreadID); err != nil {
			return nil, fmt.Errorf("scanning a linked run: %w", err)
		}
		runs = append(runs, run)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("reading the runs linked to the artifact: %w", err)
	}
	return runs, nil
}

func nonTerminalRuns(runs []linkedRun) []deletion.AffectedRun {
	affected := make([]deletion.AffectedRun, 0, len(runs))
	for _, run := range runs {
		switch run.State {
		case "succeeded", "failed", "cancelled":
			continue
		}
		affected = append(affected, deletion.AffectedRun{RunID: run.ID, State: run.State})
	}
	return affected
}

// tombstoneDerivedDocumentsTx tombstones every live knowledge document whose
// source artifact is this artifact. It returns the document ids and their
// knowledge-base ids for the deletion scope details.
func tombstoneDerivedDocumentsTx(
	ctx context.Context, tx *sql.Tx, artifactID string,
) ([]string, []string, error) {
	rows, err := tx.QueryContext(ctx, `
		UPDATE platform.knowledge_documents
		   SET deleted_at = now(), state = 'deleted', revision = revision + 1
		 WHERE source_artifact_id = $1::uuid AND deleted_at IS NULL
		 RETURNING id::text, knowledge_base_id::text`, artifactID)
	if err != nil {
		return nil, nil, fmt.Errorf("tombstoning the documents derived from the artifact: %w", err)
	}
	defer func() { _ = rows.Close() }()
	documentIDs := make([]string, 0, 4)
	baseIDs := make([]string, 0, 4)
	seenBases := make(map[string]bool, 4)
	for rows.Next() {
		var documentID, baseID string
		if err := rows.Scan(&documentID, &baseID); err != nil {
			return nil, nil, fmt.Errorf("scanning a tombstoned document: %w", err)
		}
		documentIDs = append(documentIDs, documentID)
		if !seenBases[baseID] {
			seenBases[baseID] = true
			baseIDs = append(baseIDs, baseID)
		}
	}
	if err := rows.Err(); err != nil {
		return nil, nil, fmt.Errorf("tombstoning the documents derived from the artifact: %w", err)
	}
	sort.Strings(documentIDs)
	sort.Strings(baseIDs)
	return documentIDs, baseIDs, nil
}

// chunkBodyIDsTx finds chunk bodies that are this artifact (a knowledge chunk
// body is an artifact too). Deleting one must be recorded for T071's index scope.
func (t *DeletionTarget) chunkBodyIDsTx(
	ctx context.Context, tx *sql.Tx, artifactID string,
) ([]string, error) {
	rows, err := tx.QueryContext(ctx, `
		SELECT id::text FROM platform.knowledge_chunks
		 WHERE body_artifact_id = $1::uuid ORDER BY id`, artifactID)
	if err != nil {
		return nil, fmt.Errorf("reading the chunks whose body is the artifact: %w", err)
	}
	defer func() { _ = rows.Close() }()
	ids := make([]string, 0, 4)
	for rows.Next() {
		var id string
		if err := rows.Scan(&id); err != nil {
			return nil, fmt.Errorf("scanning a chunk body: %w", err)
		}
		ids = append(ids, id)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("reading the chunks whose body is the artifact: %w", err)
	}
	return ids, nil
}

// scopeDetailsTx builds the fixed-vocabulary descriptors for the job. The
// descriptor shapes are deliberately simple JSON objects (ids/keys/hashes), never
// content, so a backup ledger or a log line can carry them safely.
func (t *DeletionTarget) scopeDetailsTx(
	ctx context.Context, tx *sql.Tx, row artifactDeleteRow,
	documentIDs, baseIDs, chunkIDs []string,
) (map[deletion.Scope]any, error) {
	runs, err := t.linkedRunsTx(ctx, tx, row.OwnerID, row.ID)
	if err != nil {
		return nil, err
	}
	runIDs := make([]string, 0, len(runs))
	threadIDs := make([]string, 0, len(runs))
	for _, run := range runs {
		runIDs = append(runIDs, run.ID)
		threadIDs = append(threadIDs, run.CheckpointThreadID)
	}
	items := []map[string]any{
		{
			"kind": "artifact", "artifact_id": row.ID, "purpose": row.Purpose,
			"state": row.State,
		},
	}
	if row.ObjectKey != "" {
		items = append(items, map[string]any{
			"kind": "object", "object_key": row.ObjectKey,
			"sha256": row.SHA256.String, "size_bytes": row.SizeBytes.Int64,
		})
	}
	details := map[deletion.Scope]any{
		deletion.ScopeObjectStore: map[string]any{"items": items},
		deletion.ScopeSearchIndex: map[string]any{
			"document_ids": documentIDs, "knowledge_base_ids": baseIDs,
			"chunk_ids": chunkIDs,
		},
		deletion.ScopeRetrievalCache: map[string]any{
			"document_ids": documentIDs, "knowledge_base_ids": baseIDs,
		},
		deletion.ScopeCheckpoints: map[string]any{
			"run_ids": runIDs, "checkpoint_thread_ids": threadIDs,
		},
		deletion.ScopeSandboxTemp:       map[string]any{"run_ids": runIDs},
		deletion.ScopeThirdPartyHistory: map[string]any{"run_ids": runIDs, "purpose": row.Purpose},
		deletion.ScopeBackupCatalog: map[string]any{
			"resource_type": "artifact", "resource_id": row.ID,
		},
	}
	return details, nil
}
