package knowledge

// F11/T069: the knowledge-document deletion adapter for internal/deletion.
//
// The tombstone is `knowledge_documents.deleted_at` plus `state='deleted'` and a
// revision bump. Every read predicate already used by retrieval, citation open,
// the retrieval-cache hit re-check (`readable_chunk.go`), the document reads
// (`service.go`), the diagnostics read and the re-index/ingest paths filters
// `kd.deleted_at` (or the `deleting`/`deleted` state), so the content stops being
// served in the same transaction that answers 202. The ingestion completion guard
// already refuses to activate a tombstoned document.
//
// T069 records the physical scopes (source object, every chunk-body object, the
// index versions and the retrieval-cache entries) for T071; it does not purge
// them.

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"sort"

	"orchara/platform/internal/deletion"
)

// DeletionTarget adapts the knowledge service to deletion.Target.
type DeletionTarget struct {
	Service *Service
}

// NewDeletionTarget builds the adapter.
func NewDeletionTarget(service *Service) *DeletionTarget {
	return &DeletionTarget{Service: service}
}

// ResourceType implements deletion.Target.
func (t *DeletionTarget) ResourceType() deletion.ResourceType {
	return deletion.ResourceDocument
}

// documentDeleteRow is the locked document plus its owner scope.
type documentDeleteRow struct {
	ID              string
	KnowledgeBaseID string
	SourceArtifact  string
	Revision        int64
	State           string
	DeletedAt       sql.NullTime
}

// Delete implements deletion.Target.
func (t *DeletionTarget) Delete(
	ctx context.Context, tx *sql.Tx, ownerID, documentID string,
) (deletion.Plan, error) {
	var row documentDeleteRow
	err := tx.QueryRowContext(ctx, `
		SELECT d.id::text, d.knowledge_base_id::text, d.source_artifact_id::text,
		       d.revision, d.state, d.deleted_at
		  FROM platform.knowledge_documents AS d
		  JOIN platform.knowledge_bases AS b ON b.id = d.knowledge_base_id
		 WHERE d.id = $1::uuid AND b.owner_id = $2::uuid AND b.deleted_at IS NULL
		 FOR UPDATE OF d`, documentID, ownerID,
	).Scan(
		&row.ID, &row.KnowledgeBaseID, &row.SourceArtifact, &row.Revision,
		&row.State, &row.DeletedAt,
	)
	if errors.Is(err, sql.ErrNoRows) {
		return deletion.Plan{}, deletion.ErrNotFound
	}
	if err != nil {
		return deletion.Plan{}, fmt.Errorf("reading the document for deletion: %w", err)
	}

	if row.DeletedAt.Valid || row.State == "deleted" || row.State == "deleting" {
		plan := deletion.Plan{Revision: row.Revision, AlreadyDeleted: true}
		plan.Details, err = t.scopeDetailsTx(ctx, tx, row)
		if err != nil {
			return deletion.Plan{}, err
		}
		return plan, nil
	}

	nextRevision := row.Revision + 1
	result, err := tx.ExecContext(ctx, `
		UPDATE platform.knowledge_documents
		   SET deleted_at = now(), state = 'deleted', revision = $2
		 WHERE id = $1::uuid AND deleted_at IS NULL AND revision = $3`,
		row.ID, nextRevision, row.Revision)
	if err != nil {
		return deletion.Plan{}, fmt.Errorf("tombstoning the knowledge document: %w", err)
	}
	affected, err := result.RowsAffected()
	if err != nil {
		return deletion.Plan{}, fmt.Errorf("counting the tombstoned document: %w", err)
	}
	if affected != 1 {
		return deletion.Plan{}, errors.New("the locked document changed before it was tombstoned")
	}
	row.Revision = nextRevision
	row.State = "deleted"
	plan := deletion.Plan{Revision: nextRevision}
	plan.Details, err = t.scopeDetailsTx(ctx, tx, row)
	if err != nil {
		return deletion.Plan{}, err
	}
	return plan, nil
}

// scopeDetailsTx builds the fixed-vocabulary descriptors: sealed source/chunk
// objects, index-version/chunk ids, the cache scope and the backup reference.
func (t *DeletionTarget) scopeDetailsTx(
	ctx context.Context, tx *sql.Tx, row documentDeleteRow,
) (map[deletion.Scope]any, error) {
	objectItems := make([]map[string]any, 0, 8)
	source, err := artifactDescriptorTx(ctx, tx, row.SourceArtifact)
	if err != nil {
		return nil, err
	}
	if source != nil {
		objectItems = append(objectItems, source)
	}

	chunkRows, err := tx.QueryContext(ctx, `
		SELECT kc.id::text, kc.document_revision, kc.chunk_no,
		       kc.body_artifact_id::text, a.object_key, a.sha256, a.size_bytes
		  FROM platform.knowledge_chunks AS kc
		  JOIN platform.artifacts AS a ON a.id = kc.body_artifact_id
		 WHERE kc.document_id = $1::uuid
		 ORDER BY kc.document_revision, kc.chunk_no`, row.ID)
	if err != nil {
		return nil, fmt.Errorf("reading the document chunks for deletion: %w", err)
	}
	chunkIDs := make([]string, 0, 16)
	for chunkRows.Next() {
		var (
			chunkID   string
			revision  int64
			chunkNo   int
			artifact  string
			objectKey string
			sha       sql.NullString
			size      sql.NullInt64
		)
		if err := chunkRows.Scan(
			&chunkID, &revision, &chunkNo, &artifact, &objectKey, &sha, &size,
		); err != nil {
			_ = chunkRows.Close()
			return nil, fmt.Errorf("scanning a document chunk for deletion: %w", err)
		}
		chunkIDs = append(chunkIDs, chunkID)
		objectItems = append(objectItems, map[string]any{
			"kind": "chunk_object", "chunk_id": chunkID, "artifact_id": artifact,
			"document_revision": revision, "chunk_no": chunkNo,
			"object_key": objectKey, "sha256": sha.String, "size_bytes": size.Int64,
		})
	}
	if err := chunkRows.Err(); err != nil {
		_ = chunkRows.Close()
		return nil, fmt.Errorf("reading the document chunks for deletion: %w", err)
	}
	_ = chunkRows.Close()
	sort.Strings(chunkIDs)

	versionRows, err := tx.QueryContext(ctx, `
		SELECT id::text FROM platform.knowledge_index_versions
		 WHERE document_id = $1::uuid ORDER BY id`, row.ID)
	if err != nil {
		return nil, fmt.Errorf("reading the document index versions for deletion: %w", err)
	}
	versionIDs := make([]string, 0, 4)
	for versionRows.Next() {
		var id string
		if err := versionRows.Scan(&id); err != nil {
			_ = versionRows.Close()
			return nil, fmt.Errorf("scanning a document index version: %w", err)
		}
		versionIDs = append(versionIDs, id)
	}
	if err := versionRows.Err(); err != nil {
		_ = versionRows.Close()
		return nil, fmt.Errorf("reading the document index versions for deletion: %w", err)
	}
	_ = versionRows.Close()

	return map[deletion.Scope]any{
		deletion.ScopeObjectStore: map[string]any{"items": objectItems},
		deletion.ScopeSearchIndex: map[string]any{
			"document_id": row.ID, "document_revision": row.Revision,
			"chunk_ids": chunkIDs, "index_version_ids": versionIDs,
		},
		deletion.ScopeRetrievalCache: map[string]any{
			"document_id": row.ID, "knowledge_base_id": row.KnowledgeBaseID,
		},
		deletion.ScopeCheckpoints: map[string]any{"document_id": row.ID},
		deletion.ScopeSandboxTemp: map[string]any{"document_id": row.ID},
		deletion.ScopeThirdPartyHistory: map[string]any{
			"document_id": row.ID,
			"note":        "documents are not sent to a third-party memory component; T072 records the boundary",
		},
		deletion.ScopeBackupCatalog: map[string]any{
			"resource_type": "knowledge_document", "resource_id": row.ID,
		},
	}, nil
}

// artifactDescriptorTx describes one sealed source artifact, if the row exists.
func artifactDescriptorTx(
	ctx context.Context, tx *sql.Tx, artifactID string,
) (map[string]any, error) {
	if artifactID == "" {
		return nil, nil
	}
	var (
		objectKey string
		sha       sql.NullString
		size      sql.NullInt64
	)
	err := tx.QueryRowContext(ctx, `
		SELECT object_key, sha256, size_bytes FROM platform.artifacts
		 WHERE id = $1::uuid`, artifactID).Scan(&objectKey, &sha, &size)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("reading the source artifact for deletion: %w", err)
	}
	return map[string]any{
		"kind": "object", "artifact_id": artifactID, "object_key": objectKey,
		"sha256": sha.String, "size_bytes": size.Int64,
	}, nil
}
