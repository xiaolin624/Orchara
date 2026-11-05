package memory

// F11/T069: the memory deletion adapter for internal/deletion.
//
// The tombstone is the same pair F09/T058 defined: `memories.deleted_at` (the
// recall predicate already filters it), `memories.revision + 1` (the strong ETag
// and the "an old revision can never be current" fact) and the
// `memory:<owner>:<application>` policy epoch raised in the same transaction.
// T069 moves the job row/audit/cancellation into the shared foundation; the last
// revision's sealed artifact, its embedding slot (memory_embeddings does not
// exist yet) and every other physical copy are recorded as scopes for T071/T072.

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"strings"

	"orchara/platform/internal/deletion"
)

// DeletionTarget adapts the personal-memory store to deletion.Target.
type DeletionTarget struct {
	Service *Service
}

// NewDeletionTarget builds the adapter.
func NewDeletionTarget(service *Service) *DeletionTarget {
	return &DeletionTarget{Service: service}
}

// ResourceType implements deletion.Target.
func (t *DeletionTarget) ResourceType() deletion.ResourceType {
	return deletion.ResourceMemory
}

// Delete implements deletion.Target. The row lock serialises a repeated or
// concurrent delete with the first one: exactly one call tombstones and bumps
// the revision, the others observe AlreadyDeleted and reuse the stored job.
func (t *DeletionTarget) Delete(
	ctx context.Context, tx *sql.Tx, ownerID, memoryID string,
) (deletion.Plan, error) {
	var (
		memory    Memory
		candidate sql.NullString
		deletedAt sql.NullTime
	)
	err := tx.QueryRowContext(ctx, `
		SELECT id::text, owner_id::text, application_id::text, body_artifact_id::text,
		       candidate_id::text, revision, confirmed_at, confirmed_by::text, deleted_at
		  FROM platform.memories
		 WHERE id = $1::uuid AND owner_id = $2::uuid
		 FOR UPDATE`, memoryID, ownerID,
	).Scan(
		&memory.ID, &memory.OwnerID, &memory.ApplicationID, &memory.BodyArtifactID,
		&candidate, &memory.Revision, &memory.ConfirmedAt, &memory.ConfirmedBy, &deletedAt,
	)
	if errors.Is(err, sql.ErrNoRows) {
		return deletion.Plan{}, deletion.ErrNotFound
	}
	if err != nil {
		return deletion.Plan{}, fmt.Errorf("reading the memory for deletion: %w", err)
	}
	if candidate.Valid {
		memory.CandidateID = candidate.String
	}

	artifact, err := t.bodyArtifactTx(ctx, tx, memory.BodyArtifactID)
	if err != nil {
		return deletion.Plan{}, err
	}
	sourceRuns, err := t.sourceRunIDsTx(ctx, tx, memory.CandidateID)
	if err != nil {
		return deletion.Plan{}, err
	}

	if deletedAt.Valid {
		return deletion.Plan{
			Revision:       memory.Revision,
			AlreadyDeleted: true,
			Details:        memoryScopeDetails(memory, artifact, sourceRuns),
		}, nil
	}

	nextRevision := memory.Revision + 1
	result, err := tx.ExecContext(ctx, `
		UPDATE platform.memories
		   SET revision = $3, deleted_at = now()
		 WHERE id = $1::uuid AND owner_id = $2::uuid
		   AND deleted_at IS NULL AND revision = $4`,
		memory.ID, ownerID, nextRevision, memory.Revision,
	)
	if err != nil {
		return deletion.Plan{}, fmt.Errorf("tombstoning the memory: %w", err)
	}
	affected, err := result.RowsAffected()
	if err != nil {
		return deletion.Plan{}, fmt.Errorf("counting the tombstoned memory: %w", err)
	}
	if affected != 1 {
		return deletion.Plan{}, errors.New("the locked memory changed before it was tombstoned")
	}
	// The cache epoch is part of the same transaction: even if a future cache
	// loses the eviction wakeup, a key derived from the old revision/epoch cannot
	// be served as current.
	if err := bumpMemoryEpoch(ctx, tx, memory.OwnerID, memory.ApplicationID); err != nil {
		return deletion.Plan{}, err
	}
	memory.Revision = nextRevision
	return deletion.Plan{
		Revision: nextRevision,
		Details:  memoryScopeDetails(memory, artifact, sourceRuns),
	}, nil
}

// DeletionJob is the F09/T058 legacy answer shape. It is kept for the mem0
// experiment driver (experiments/mem0/pgdriver), which is an evidence tool, not a
// product surface; the product API now reads deletion.Job through the foundation.
type DeletionJob struct {
	ID         string
	State      string
	ResourceID string
}

// Delete is the legacy in-process entry point used by the mem0 baseline driver.
// It goes through the same shared foundation as the HTTP route, so the experiment
// observes exactly the product's tombstone/job semantics.
func (s *Service) Delete(ctx context.Context, ownerID, memoryID string) (*DeletionJob, error) {
	foundation := deletion.New(s.DB)
	foundation.Register(NewDeletionTarget(s))
	job, err := foundation.Delete(ctx, ownerID, deletion.ResourceMemory, memoryID)
	if err != nil {
		return nil, err
	}
	return &DeletionJob{ID: job.ID, State: job.State, ResourceID: job.ResourceID}, nil
}

// bodyArtifactRow is the sealed body descriptor recorded in the object_store scope.
type bodyArtifactRow struct {
	ObjectKey string
	SHA256    sql.NullString
	SizeBytes sql.NullInt64
}

func (t *DeletionTarget) bodyArtifactTx(
	ctx context.Context, tx *sql.Tx, artifactID string,
) (bodyArtifactRow, error) {
	var artifact bodyArtifactRow
	err := tx.QueryRowContext(ctx, `
		SELECT object_key, sha256, size_bytes FROM platform.artifacts
		 WHERE id = $1::uuid`, artifactID,
	).Scan(&artifact.ObjectKey, &artifact.SHA256, &artifact.SizeBytes)
	if errors.Is(err, sql.ErrNoRows) {
		// A memory whose body artifact row is already gone is still deletable; the
		// tombstone is the recall authority and the object key is unknown.
		return bodyArtifactRow{}, nil
	}
	if err != nil {
		return bodyArtifactRow{}, fmt.Errorf("reading the memory body artifact: %w", err)
	}
	return artifact, nil
}

// sourceRunIDsTx reads the provenance run of a candidate-backed memory. It is
// only a descriptor for the checkpoint/temp/third-party scopes; deleting a
// memory never cancels a run.
func (t *DeletionTarget) sourceRunIDsTx(
	ctx context.Context, tx *sql.Tx, candidateID string,
) ([]string, error) {
	if strings.TrimSpace(candidateID) == "" {
		return nil, nil
	}
	rows, err := tx.QueryContext(ctx, `
		SELECT COALESCE(source_run_id::text, '')
		  FROM platform.memory_candidates WHERE id = $1::uuid`, candidateID)
	if err != nil {
		return nil, fmt.Errorf("reading the memory source run: %w", err)
	}
	defer func() { _ = rows.Close() }()
	ids := make([]string, 0, 1)
	for rows.Next() {
		var id string
		if err := rows.Scan(&id); err != nil {
			return nil, fmt.Errorf("scanning the memory source run: %w", err)
		}
		if id != "" {
			ids = append(ids, id)
		}
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("reading the memory source run: %w", err)
	}
	return ids, nil
}

// memoryScopeDetails builds the fixed-vocabulary descriptors. They carry ids,
// keys and hashes only, never memory text.
func memoryScopeDetails(
	memory Memory, artifact bodyArtifactRow, sourceRuns []string,
) map[deletion.Scope]any {
	items := []map[string]any{
		{"kind": "artifact", "artifact_id": memory.BodyArtifactID},
	}
	if artifact.ObjectKey != "" {
		items = append(items, map[string]any{
			"kind": "object", "object_key": artifact.ObjectKey,
			"sha256": artifact.SHA256.String, "size_bytes": artifact.SizeBytes.Int64,
		})
	}
	previousRevision := memory.Revision - 1
	if previousRevision < 1 {
		previousRevision = 1
	}
	return map[deletion.Scope]any{
		deletion.ScopeObjectStore: map[string]any{"items": items},
		deletion.ScopeSearchIndex: map[string]any{
			"memory_ids":       []string{memory.ID},
			"memory_revisions": []int64{previousRevision},
			"note":             "platform.memory_embeddings is not created yet; T071 must treat the scope as absent, not fail",
		},
		deletion.ScopeRetrievalCache: map[string]any{
			"memory_scope":     "memory:" + strings.ToLower(memory.OwnerID) + ":" + strings.ToLower(memory.ApplicationID),
			"memory_ids":       []string{memory.ID},
			"memory_revisions": []int64{previousRevision},
		},
		deletion.ScopeCheckpoints: map[string]any{"run_ids": sourceRuns},
		deletion.ScopeSandboxTemp: map[string]any{"run_ids": sourceRuns},
		deletion.ScopeThirdPartyHistory: map[string]any{
			"memory_ids": []string{memory.ID},
			"note":       "third-party memory components are not adopted (T066 do_not_adopt); T072 records the boundary",
		},
		deletion.ScopeBackupCatalog: map[string]any{
			"resource_type": "memory", "resource_id": memory.ID,
		},
	}
}
