package deletion

// F11/T071: the physical purge of the three scopes this ticket owns.
//
// The split follows the existing handler/guard pattern of internal/background:
//
//   - the maintenance handler (background/handlers.DeleteContent) reads the job
//     scope and deletes the named sealed objects — bytes only, no business row;
//   - the server-side completion guard (background.guardDeleteContentCompletion)
//     re-reads the same job inside one transaction, verifies every named object is
//     gone, removes the derived index and cache rows, marks the artifact rows
//     purged and appends the cleared scopes to completed_scopes. That evidence
//     transaction is T069's rule, and it is why a handler result can never claim
//     a scope it did not actually clear.
//
// Safety rule (BACKGROUND-JOBS.md §26 / F11 notes §4.4): an object is deleted only
// when the job's own scope_details names it (plus the memory-body revisions the
// schema cannot name, which are discovered only through the `memory_body` purpose
// and a live-memory exclusion). A named object that a *live* knowledge document,
// knowledge chunk or memory still references is never deleted: the object_store
// scope stays outstanding with the business code object_shared_reference, and the
// artifact row stays live. Over-deletion is impossible; an incomplete purge is
// visible.
//
// The engine is deliberately not a second state machine: `remaining_scopes` /
// `completed_scopes` on the T069 job row are the only progress record, and every
// transition is a CAS against one locked row.

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"strings"

	"orchara/platform/internal/objectstore"
)

// PurgeFailure names why a scope could not be cleared. The background package
// maps it onto its closed error-code vocabulary; this package never invents a
// retryable code that the controller's fixed table does not know.
type PurgeFailure string

const (
	PurgeOK              PurgeFailure = ""
	PurgeObjectsPresent  PurgeFailure = "objects_present"
	PurgeSharedReference PurgeFailure = "shared_reference"
	PurgeIndexRemain     PurgeFailure = "index_rows_remain"
	PurgeCacheRemain     PurgeFailure = "cache_rows_remain"
	// PurgeUnsupportedTarget is a resource type this build has no copy
	// descriptors for (a future resource added without a purge adapter). Clearing
	// nothing and claiming the scopes would be a lie, so every scope stays
	// outstanding with a non-retryable code the operator can see.
	PurgeUnsupportedTarget PurgeFailure = "unsupported_target"
)

// ErrorCode is the stable safe code recorded both on the background job and on
// the deletion job row. The spellings are the controller's closed vocabulary
// (internal/background); keeping one spelling is what lets an operator correlate
// GET /background-jobs with the deletion row without a second map.
func (f PurgeFailure) ErrorCode() string {
	switch f {
	case PurgeObjectsPresent:
		return "transient_storage_io"
	case PurgeIndexRemain, PurgeCacheRemain:
		return "transient_internal"
	case PurgeSharedReference:
		return "object_shared_reference"
	case PurgeUnsupportedTarget:
		return "purge_unsupported_target"
	default:
		return ""
	}
}

// PurgeJob is the deletion-job row as the purge needs it (T069 report §3.1).
type PurgeJob struct {
	ID           string
	OwnerID      string
	ResourceType ResourceType
	ResourceID   string
	State        string
	Remaining    []Scope
	Completed    []Scope
	AttemptCount int
}

// PurgeObject is one sealed object named by the job. ArtifactID is the row the
// object belongs to ("" when the descriptor named only a key); Shared means a
// live resource still references it, so neither the handler nor the guard may
// delete the bytes or mark the row purged.
type PurgeObject struct {
	ArtifactID string
	Key        string
	Shared     bool
}

// PurgeTargets is the authoritative, derived set of copies for one deletion job.
type PurgeTargets struct {
	Job              PurgeJob
	Objects          []PurgeObject
	DocumentIDs      []string
	ChunkIDs         []string
	KnowledgeBaseIDs []string
	MemoryIDs        []string
}

// queries is the read surface shared by *sql.DB (the handler's interim read, the
// same boundary artifact_finalize documents) and *sql.Tx (the completion guard).
type queries interface {
	QueryContext(ctx context.Context, query string, args ...any) (*sql.Rows, error)
	QueryRowContext(ctx context.Context, query string, args ...any) *sql.Row
}

// PurgeDedupKey is the (kind=delete_content, dedup_key) identity of the one purge
// unit of work for a deletion job. It is a function, not a literal at each call
// site, so the enqueue path and the tests cannot drift.
func PurgeDedupKey(deletionID string) string {
	return "deletion:" + strings.ToLower(strings.TrimSpace(deletionID))
}

// LoadPurgeTargets reads one deletion job and derives every physical copy its
// three scopes name. It never mutates anything; the handler uses it with the
// maintenance DB handle, the completion guard with its transaction.
func LoadPurgeTargets(ctx context.Context, db queries, deletionID string) (*PurgeTargets, error) {
	job, details, err := readPurgeJob(ctx, db, deletionID, false)
	if err != nil {
		return nil, err
	}
	return derivePurgeTargets(ctx, db, job, details)
}

// ApplyPurgeOutcome is the completion guard's evidence transaction. It locks the
// deletion job row, verifies the named objects are gone, clears the derived index
// and cache rows, marks the artifact rows purged, and writes the new scope lists
// in the same transaction. A still-present object or a shared reference is
// reported as a PurgeFailure, never as a Go error: a refusal must not abort the
// maintenance process (F11 notes §3.6), so only a genuine database/consistency
// failure returns an error.
func ApplyPurgeOutcome(
	ctx context.Context, tx *sql.Tx, store *objectstore.Store, deletionID string,
) (PurgeResult, error) {
	job, details, err := readPurgeJob(ctx, tx, deletionID, true)
	if err != nil {
		return PurgeResult{}, err
	}
	if job.State == StateCompleted && len(job.Remaining) == 0 {
		// Idempotent replay: the job is already done and no scope is owed.
		return PurgeResult{Done: true, State: StateCompleted, Completed: job.Completed}, nil
	}
	targets, err := derivePurgeTargets(ctx, tx, job, details)
	if err != nil {
		return PurgeResult{}, err
	}

	remaining := make([]Scope, 0, len(job.Remaining))
	completed := append([]Scope{}, job.Completed...)
	cleared := make(map[Scope]bool, len(job.Remaining))
	failure := PurgeOK

	// A resource type this build cannot derive copy descriptors for must not have
	// its scopes claimed as "cleared" because the derived set happens to be empty.
	// The four resource types the T069 foundation registers are supported:
	// conversation (added by F08/T074) stores its content as sealed artifacts
	// named under object_store, and has no chunk/vector index or retrieval-cache
	// table of its own, so those two scopes are legitimately empty for it. A
	// future resource type with no adapter keeps every scope outstanding.
	supported := job.ResourceType == ResourceArtifact ||
		job.ResourceType == ResourceMemory ||
		job.ResourceType == ResourceDocument ||
		job.ResourceType == ResourceConversation
	if !supported {
		failure = PurgeUnsupportedTarget
	}

	for _, scope := range job.Remaining {
		switch {
		case !supported:
			// Every scope stays outstanding; nothing is guessed.
		case scope == ScopeObjectStore:
			outcome, err := clearObjectStore(ctx, tx, store, targets)
			if err != nil {
				return PurgeResult{}, err
			}
			if outcome == PurgeOK {
				cleared[scope] = true
			} else if failure == PurgeOK {
				failure = outcome
			}
		case scope == ScopeSearchIndex:
			outcome, err := clearSearchIndex(ctx, tx, targets)
			if err != nil {
				return PurgeResult{}, err
			}
			if outcome == PurgeOK {
				cleared[scope] = true
			} else if failure == PurgeOK {
				failure = outcome
			}
		case scope == ScopeRetrievalCache:
			outcome, err := clearRetrievalCache(ctx, tx, targets)
			if err != nil {
				return PurgeResult{}, err
			}
			if outcome == PurgeOK {
				cleared[scope] = true
			} else if failure == PurgeOK {
				failure = outcome
			}
		default:
			// checkpoints|sandbox_temp|third_party_history|backup_catalog are
			// T072's; this ticket must leave them outstanding, never guess.
			remaining = append(remaining, scope)
		}
	}
	for _, scope := range job.Remaining {
		if cleared[scope] {
			completed = appendUniqueScope(completed, scope)
		} else if !containsScope(remaining, scope) {
			remaining = append(remaining, scope)
		}
	}
	sort.Slice(remaining, func(i, j int) bool { return remaining[i] < remaining[j] })
	sort.Slice(completed, func(i, j int) bool { return completed[i] < completed[j] })

	state := StatePending
	switch {
	case len(remaining) == 0:
		state = StateCompleted
	case failure != PurgeOK:
		state = StateFailed
	}
	if err := writePurgeProgress(ctx, tx, job, state, remaining, completed, failure); err != nil {
		return PurgeResult{}, err
	}
	return PurgeResult{
		Done:      len(remaining) == 0,
		State:     state,
		Failure:   failure,
		Remaining: remaining,
		Completed: completed,
	}, nil
}

// PurgeResult is what one evidence transaction decided.
type PurgeResult struct {
	Done      bool
	State     string
	Failure   PurgeFailure
	Remaining []Scope
	Completed []Scope
}

// readPurgeJob reads the deletion job. forUpdate takes the row lock the guard
// needs; the handler's plain read is the documented T022-style interim read.
func readPurgeJob(
	ctx context.Context, db queries, deletionID string, forUpdate bool,
) (PurgeJob, map[string]any, error) {
	query := `
		SELECT id::text, owner_id::text, resource_type, resource_id::text, state,
		       remaining_scopes, completed_scopes, attempt_count, scope_details
		  FROM platform.deletion_jobs
		 WHERE id = $1::uuid`
	if forUpdate {
		query += ` FOR UPDATE`
	}
	var (
		job     PurgeJob
		kind    string
		remain  []byte
		done    []byte
		details []byte
	)
	err := db.QueryRowContext(ctx, query, strings.TrimSpace(deletionID)).Scan(
		&job.ID, &job.OwnerID, &kind, &job.ResourceID, &job.State,
		&remain, &done, &job.AttemptCount, &details,
	)
	if errors.Is(err, sql.ErrNoRows) {
		return PurgeJob{}, nil, ErrNotFound
	}
	if err != nil {
		return PurgeJob{}, nil, fmt.Errorf("reading the deletion job for purge: %w", err)
	}
	job.ResourceType = ResourceType(kind)
	if job.Remaining, err = decodeScopes(remain); err != nil {
		return PurgeJob{}, nil, err
	}
	if job.Completed, err = decodeScopes(done); err != nil {
		return PurgeJob{}, nil, err
	}
	decoded := map[string]any{}
	if len(details) > 0 {
		if err := json.Unmarshal(details, &decoded); err != nil {
			return PurgeJob{}, nil, fmt.Errorf("decoding the deletion scope details: %w", err)
		}
	}
	return job, decoded, nil
}

func decodeScopes(raw []byte) ([]Scope, error) {
	var names []string
	if len(raw) > 0 {
		if err := json.Unmarshal(raw, &names); err != nil {
			return nil, fmt.Errorf("decoding the deletion scopes: %w", err)
		}
	}
	scopes := make([]Scope, 0, len(names))
	for _, name := range names {
		scopes = append(scopes, Scope(name))
	}
	return scopes, nil
}

// derivePurgeTargets builds the copy set from the job row plus the rows the
// schema links to it. Nothing is inferred from "unreferenced": every extra object
// either belongs to a document the job names as tombstoned or is a memory_body
// artifact no live memory points at (the superseded revisions T058 documented as
// F07's purge debt).
func derivePurgeTargets(
	ctx context.Context, db queries, job PurgeJob, details map[string]any,
) (*PurgeTargets, error) {
	targets := &PurgeTargets{Job: job}
	objects := make(map[string]*PurgeObject)
	documents := make(map[string]bool)
	chunks := make(map[string]bool)
	bases := make(map[string]bool)
	memories := make(map[string]bool)

	objectStore := scopeMap(details, ScopeObjectStore)
	artifactOnly := make([]string, 0, 4)
	for _, item := range objectItems(objectStore) {
		artifactID := stringValue(item["artifact_id"])
		key := stringValue(item["object_key"])
		if key == "" {
			// T069's object_store descriptors come in two shapes: a combined
			// {artifact_id, object_key} item (knowledge source/chunk) and a pair of
			// separate artifact-only and object-only items (artifact/memory). Keep
			// the artifact-only ids and resolve their literal key below.
			if artifactID != "" {
				artifactOnly = append(artifactOnly, artifactID)
			}
			continue
		}
		key = strings.TrimSpace(key)
		existing, ok := objects[key]
		if !ok {
			existing = &PurgeObject{Key: key}
			objects[key] = existing
		}
		if artifactID != "" {
			existing.ArtifactID = artifactID
		}
	}
	if len(artifactOnly) > 0 {
		// Resolve the artifact-only descriptors against the authoritative row. This
		// is what lets the guard mark artifacts.purged_at; the key never comes from
		// a guess, only from the named artifact row itself.
		rows, err := db.QueryContext(ctx, `
			SELECT id::text, object_key FROM platform.artifacts
			 WHERE id IN (SELECT jsonb_array_elements_text($1::jsonb)::uuid)
			 ORDER BY id`, uuidArrayArg(sortedStrings(artifactOnly)))
		if err != nil {
			return nil, fmt.Errorf("reading the named purge artifacts: %w", err)
		}
		for rows.Next() {
			var artifactID, key string
			if err := rows.Scan(&artifactID, &key); err != nil {
				_ = rows.Close()
				return nil, fmt.Errorf("scanning a named purge artifact: %w", err)
			}
			if key = strings.TrimSpace(key); key == "" {
				continue
			}
			if existing, ok := objects[key]; ok {
				if existing.ArtifactID == "" {
					existing.ArtifactID = artifactID
				}
				continue
			}
			objects[key] = &PurgeObject{ArtifactID: artifactID, Key: key}
		}
		if err := rows.Err(); err != nil {
			_ = rows.Close()
			return nil, fmt.Errorf("reading the named purge artifacts: %w", err)
		}
		_ = rows.Close()
	}

	searchIndex := scopeMap(details, ScopeSearchIndex)
	for _, id := range stringListFrom(searchIndex["document_ids"]) {
		documents[id] = true
	}
	for _, id := range stringListFrom(searchIndex["chunk_ids"]) {
		chunks[id] = true
	}
	for _, id := range stringListFrom(searchIndex["knowledge_base_ids"]) {
		bases[id] = true
	}
	retrievalCache := scopeMap(details, ScopeRetrievalCache)
	for _, id := range stringListFrom(retrievalCache["document_ids"]) {
		documents[id] = true
	}
	for _, id := range stringListFrom(retrievalCache["knowledge_base_ids"]) {
		bases[id] = true
	}
	if job.ResourceType == ResourceDocument {
		documents[job.ResourceID] = true
	}
	if job.ResourceType == ResourceMemory {
		memories[job.ResourceID] = true
	}
	for _, id := range stringListFrom(searchIndex["memory_ids"]) {
		memories[id] = true
	}
	for _, id := range stringListFrom(retrievalCache["memory_ids"]) {
		memories[id] = true
	}

	// Documents the artifact deletion tombstoned (artifact -> derived documents)
	// and the document itself: their chunks, vectors and index versions are the
	// search_index scope, and their chunk-body objects are object_store copies.
	documentIDs := sortedKeys(documents)
	if len(documentIDs) > 0 {
		rows, err := db.QueryContext(ctx, `
			SELECT kc.id::text, kc.body_artifact_id::text, a.object_key
			  FROM platform.knowledge_chunks AS kc
			  JOIN platform.artifacts AS a ON a.id = kc.body_artifact_id
			 WHERE kc.document_id IN (SELECT jsonb_array_elements_text($1::jsonb)::uuid)
			 ORDER BY kc.id`, uuidArrayArg(documentIDs))
		if err != nil {
			return nil, fmt.Errorf("reading the purged chunks: %w", err)
		}
		defer func() { _ = rows.Close() }()
		for rows.Next() {
			var chunkID, artifactID, key string
			if err := rows.Scan(&chunkID, &artifactID, &key); err != nil {
				return nil, fmt.Errorf("scanning a purged chunk: %w", err)
			}
			chunks[chunkID] = true
			if key = strings.TrimSpace(key); key != "" {
				if existing, ok := objects[key]; ok {
					if existing.ArtifactID == "" {
						existing.ArtifactID = artifactID
					}
				} else {
					objects[key] = &PurgeObject{ArtifactID: artifactID, Key: key}
				}
			}
		}
		if err := rows.Err(); err != nil {
			return nil, fmt.Errorf("reading the purged chunks: %w", err)
		}
		_ = rows.Close()

		baseRows, err := db.QueryContext(ctx, `
			SELECT DISTINCT knowledge_base_id::text
			  FROM platform.knowledge_documents
			 WHERE id IN (SELECT jsonb_array_elements_text($1::jsonb)::uuid)`,
			uuidArrayArg(documentIDs))
		if err != nil {
			return nil, fmt.Errorf("reading the purged documents: %w", err)
		}
		for baseRows.Next() {
			var baseID string
			if err := baseRows.Scan(&baseID); err != nil {
				_ = baseRows.Close()
				return nil, fmt.Errorf("scanning a purged document: %w", err)
			}
			bases[baseID] = true
		}
		if err := baseRows.Err(); err != nil {
			_ = baseRows.Close()
			return nil, fmt.Errorf("reading the purged documents: %w", err)
		}
		_ = baseRows.Close()
	}

	// Memory bodies: the memory row names only the current body artifact; an
	// update leaves the superseded artifact without a back-reference (T058 §3
	// documents it as F07's purge debt). The provable set is "every memory_body
	// artifact of this owner that no live memory points at", which is exactly the
	// deleted memory's bodies plus superseded revisions. A live memory's body can
	// never be in this set, and memory_candidate bodies have another purpose.
	if job.ResourceType == ResourceMemory {
		rows, err := db.QueryContext(ctx, `
			SELECT a.id::text, a.object_key
			  FROM platform.artifacts AS a
			 WHERE a.owner_id = $1::uuid
			   AND a.purpose = 'memory_body'
			   AND a.state = 'ready'
			   AND a.deleted_at IS NULL
			   AND NOT EXISTS (
			        SELECT 1 FROM platform.memories AS m
			         WHERE m.body_artifact_id = a.id AND m.deleted_at IS NULL)
			 ORDER BY a.id`, job.OwnerID)
		if err != nil {
			return nil, fmt.Errorf("reading the memory body artifacts: %w", err)
		}
		for rows.Next() {
			var artifactID, key string
			if err := rows.Scan(&artifactID, &key); err != nil {
				_ = rows.Close()
				return nil, fmt.Errorf("scanning a memory body artifact: %w", err)
			}
			if key = strings.TrimSpace(key); key != "" {
				if _, ok := objects[key]; !ok {
					objects[key] = &PurgeObject{ArtifactID: artifactID, Key: key}
				}
			}
		}
		if err := rows.Err(); err != nil {
			_ = rows.Close()
			return nil, fmt.Errorf("reading the memory body artifacts: %w", err)
		}
		_ = rows.Close()
	}

	// A named object whose artifact a live resource still references is not
	// deleted and not marked purged. This is the legitimate-reference guard: e.g.
	// a deleted document's source artifact re-ingested into another knowledge base
	// before the purge runs.
	artifactIDs := make([]string, 0, len(objects))
	for _, object := range objects {
		if object.ArtifactID != "" {
			artifactIDs = append(artifactIDs, object.ArtifactID)
		}
	}
	shared, err := sharedArtifacts(ctx, db, sortedStrings(artifactIDs), sortedKeys(chunks))
	if err != nil {
		return nil, err
	}
	ordered := make([]PurgeObject, 0, len(objects))
	for _, object := range objects {
		if object.ArtifactID != "" && shared[object.ArtifactID] {
			object.Shared = true
		}
		ordered = append(ordered, *object)
	}
	sort.Slice(ordered, func(i, j int) bool { return ordered[i].Key < ordered[j].Key })
	targets.Objects = ordered
	targets.DocumentIDs = documentIDs
	targets.ChunkIDs = sortedKeys(chunks)
	targets.KnowledgeBaseIDs = sortedKeys(bases)
	targets.MemoryIDs = sortedKeys(memories)
	return targets, nil
}

// sharedArtifacts returns the named artifact ids a live resource still reads:
// a knowledge document's source, a live document's chunk body, a live memory's
// body or a pending memory candidate's body. Chunks named by this same job are
// excluded because the job removes them in the search_index scope (a deleted
// chunk-body artifact therefore cannot block its own purge). Tombstoned
// documents/memories are never references: their rows are the no-resurrection
// authority, not a live reader.
func sharedArtifacts(
	ctx context.Context, db queries, artifactIDs, jobChunkIDs []string,
) (map[string]bool, error) {
	shared := make(map[string]bool)
	if len(artifactIDs) == 0 {
		return shared, nil
	}
	rows, err := db.QueryContext(ctx, `
		SELECT DISTINCT a.id::text
		  FROM platform.artifacts AS a
		 WHERE a.id IN (SELECT jsonb_array_elements_text($1::jsonb)::uuid)
		   AND (
		        EXISTS (SELECT 1 FROM platform.knowledge_documents AS d
		                 WHERE d.source_artifact_id = a.id AND d.deleted_at IS NULL)
		     OR EXISTS (SELECT 1 FROM platform.knowledge_chunks AS kc
		                  JOIN platform.knowledge_documents AS d ON d.id = kc.document_id
		                 WHERE kc.body_artifact_id = a.id
		                   AND d.deleted_at IS NULL
		                   AND kc.id NOT IN (
		                        SELECT jsonb_array_elements_text($2::jsonb)::uuid))
		     OR EXISTS (SELECT 1 FROM platform.memories AS m
		                 WHERE m.body_artifact_id = a.id AND m.deleted_at IS NULL)
		     OR EXISTS (SELECT 1 FROM platform.memory_candidates AS c
		                 WHERE c.body_artifact_id = a.id AND c.state = 'pending')
		   )`, uuidArrayArg(artifactIDs), uuidArrayArg(jobChunkIDs))
	if err != nil {
		return nil, fmt.Errorf("reading the live artifact references: %w", err)
	}
	defer func() { _ = rows.Close() }()
	for rows.Next() {
		var id string
		if err := rows.Scan(&id); err != nil {
			return nil, fmt.Errorf("scanning a live artifact reference: %w", err)
		}
		shared[id] = true
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("reading the live artifact references: %w", err)
	}
	return shared, nil
}

// clearObjectStore verifies every named object is gone and, only then, marks the
// owning artifact rows purged in this same transaction. A shared object is never
// verified or marked: it belongs to a live resource.
func clearObjectStore(
	ctx context.Context, tx *sql.Tx, store *objectstore.Store, targets *PurgeTargets,
) (PurgeFailure, error) {
	if store == nil {
		return PurgeOK, ErrObjectStoreUnavailable
	}
	present := false
	purgeable := make([]string, 0, len(targets.Objects))
	for _, object := range targets.Objects {
		if object.Shared {
			present = true
			continue
		}
		if _, err := store.Stat(object.Key); err != nil {
			if errors.Is(err, objectstore.ErrNotExist) {
				if object.ArtifactID != "" {
					purgeable = append(purgeable, object.ArtifactID)
				}
				continue
			}
			// The store could not even answer: retryable, and nothing is marked.
			return PurgeObjectsPresent, nil
		}
		present = true
	}
	if len(purgeable) > 0 {
		if _, err := tx.ExecContext(ctx, `
			UPDATE platform.artifacts
			   SET purged_at = now(),
			       state = 'deleted',
			       deleted_at = COALESCE(deleted_at, now())
			 WHERE id IN (SELECT jsonb_array_elements_text($1::jsonb)::uuid)
			   AND purged_at IS NULL`, uuidArrayArg(purgeable)); err != nil {
			return PurgeOK, fmt.Errorf("marking the purged artifacts: %w", err)
		}
	}
	hasShared := false
	for _, object := range targets.Objects {
		if object.Shared {
			hasShared = true
			break
		}
	}
	switch {
	case hasShared:
		return PurgeSharedReference, nil
	case present:
		return PurgeObjectsPresent, nil
	default:
		return PurgeOK, nil
	}
}

// clearSearchIndex deletes the chunk/vector rows, the index versions and the
// activation pointers of the tombstoned documents the job owns. For a deleted
// chunk-body artifact (artifact -> chunk, live document) only the named chunk
// rows go; the document itself is untouched.
func clearSearchIndex(
	ctx context.Context, tx *sql.Tx, targets *PurgeTargets,
) (PurgeFailure, error) {
	documentIDs := targets.DocumentIDs
	chunkIDs := targets.ChunkIDs
	if len(documentIDs) > 0 {
		documents := uuidArrayArg(documentIDs)
		// The pointer must fall before the rows it points at; the tombstone (not
		// the index) remains the read authority.
		if _, err := tx.ExecContext(ctx, `
			UPDATE platform.knowledge_documents
			   SET active_index_id = NULL,
			       active_revision = NULL,
			       updated_at = now()
			 WHERE id IN (SELECT jsonb_array_elements_text($1::jsonb)::uuid)
			   AND deleted_at IS NOT NULL`, documents); err != nil {
			return PurgeOK, fmt.Errorf("clearing the purged document activation: %w", err)
		}
		// A background job may hold the FK to an index version being deleted. The
		// document is tombstoned, so no ingest job can ever activate it again;
		// unbinding the job keeps the deletion legal without touching its state.
		if _, err := tx.ExecContext(ctx, `
			UPDATE platform.background_jobs AS j
			   SET expected_index_version_id = NULL
			 WHERE j.expected_index_version_id IN (
			       SELECT iv.id FROM platform.knowledge_index_versions AS iv
			        WHERE iv.document_id IN (
			              SELECT jsonb_array_elements_text($1::jsonb)::uuid))`,
			documents); err != nil {
			return PurgeOK, fmt.Errorf("unbinding the purged index versions: %w", err)
		}
		if _, err := tx.ExecContext(ctx, `
			DELETE FROM platform.knowledge_chunks
			 WHERE document_id IN (SELECT jsonb_array_elements_text($1::jsonb)::uuid)`,
			documents); err != nil {
			return PurgeOK, fmt.Errorf("deleting the purged chunk rows: %w", err)
		}
		if _, err := tx.ExecContext(ctx, `
			DELETE FROM platform.knowledge_index_versions
			 WHERE document_id IN (SELECT jsonb_array_elements_text($1::jsonb)::uuid)`,
			documents); err != nil {
			return PurgeOK, fmt.Errorf("deleting the purged index versions: %w", err)
		}
	}
	if len(chunkIDs) > 0 {
		if _, err := tx.ExecContext(ctx, `
			DELETE FROM platform.knowledge_chunks
			 WHERE id IN (SELECT jsonb_array_elements_text($1::jsonb)::uuid)`,
			uuidArrayArg(chunkIDs)); err != nil {
			return PurgeOK, fmt.Errorf("deleting the purged chunk rows: %w", err)
		}
	}
	// Vector storage in this deployment is the embedding column on the chunk row
	// (pgvector is unavailable, 0034 §1), so deleting the row deletes the vector.
	// A memory's embeddings would live in platform.memory_embeddings, which 0033
	// documents but does not create: the scope is honestly empty, not failed.
	embeddingsExist := false
	if len(targets.MemoryIDs) > 0 {
		var table sql.NullString
		if err := tx.QueryRowContext(ctx,
			`SELECT to_regclass('platform.memory_embeddings')::text`).Scan(&table); err != nil {
			return PurgeOK, fmt.Errorf("checking the memory embedding table: %w", err)
		}
		embeddingsExist = table.Valid
		if embeddingsExist {
			if _, err := tx.ExecContext(ctx, `
				DELETE FROM platform.memory_embeddings
				 WHERE memory_id IN (SELECT jsonb_array_elements_text($1::jsonb)::uuid)`,
				uuidArrayArg(targets.MemoryIDs)); err != nil {
				return PurgeOK, fmt.Errorf("deleting the purged memory embeddings: %w", err)
			}
		}
	}
	// The positive evidence the guard needs: the rows the transaction believes it
	// deleted must really be gone before the scope can be called cleared.
	if len(documentIDs) > 0 || len(chunkIDs) > 0 {
		remaining, err := countRemaining(ctx, tx, `
			SELECT count(*) FROM platform.knowledge_chunks
			 WHERE document_id IN (SELECT jsonb_array_elements_text($1::jsonb)::uuid)
			    OR id IN (SELECT jsonb_array_elements_text($2::jsonb)::uuid)`,
			uuidArrayArg(documentIDs), uuidArrayArg(chunkIDs))
		if err != nil {
			return PurgeOK, err
		}
		if remaining > 0 {
			return PurgeIndexRemain, nil
		}
	}
	if len(documentIDs) > 0 {
		remaining, err := countRemaining(ctx, tx, `
			SELECT count(*) FROM platform.knowledge_index_versions
			 WHERE document_id IN (SELECT jsonb_array_elements_text($1::jsonb)::uuid)`,
			uuidArrayArg(documentIDs))
		if err != nil {
			return PurgeOK, err
		}
		if remaining > 0 {
			return PurgeIndexRemain, nil
		}
	}
	if embeddingsExist {
		remaining, err := countRemaining(ctx, tx, `
			SELECT count(*) FROM platform.memory_embeddings
			 WHERE memory_id IN (SELECT jsonb_array_elements_text($1::jsonb)::uuid)`,
			uuidArrayArg(targets.MemoryIDs))
		if err != nil {
			return PurgeOK, err
		}
		if remaining > 0 {
			return PurgeIndexRemain, nil
		}
	}
	return PurgeOK, nil
}

// clearRetrievalCache invalidates every cached retrieval entry that could return
// the deleted content: an entry whose items contain one of the job's documents or
// chunks. The memory cache is the policy epoch, already raised in the tombstone
// transaction (T058), so a memory job has no row to delete here.
func clearRetrievalCache(
	ctx context.Context, tx *sql.Tx, targets *PurgeTargets,
) (PurgeFailure, error) {
	for _, documentID := range targets.DocumentIDs {
		if _, err := tx.ExecContext(ctx, `
			DELETE FROM platform.knowledge_retrieval_cache
			 WHERE principal_id = $1::uuid
			   AND items @> jsonb_build_array(jsonb_build_object('document_id', $2::text))`,
			targets.Job.OwnerID, documentID); err != nil {
			return PurgeOK, fmt.Errorf("deleting the purged cache entries: %w", err)
		}
	}
	for _, chunkID := range targets.ChunkIDs {
		if _, err := tx.ExecContext(ctx, `
			DELETE FROM platform.knowledge_retrieval_cache
			 WHERE principal_id = $1::uuid
			   AND items @> jsonb_build_array(jsonb_build_object('chunk_id', $2::text))`,
			targets.Job.OwnerID, chunkID); err != nil {
			return PurgeOK, fmt.Errorf("deleting the purged cache entries: %w", err)
		}
	}
	remaining, err := countRemaining(ctx, tx, `
		SELECT count(*)
		  FROM platform.knowledge_retrieval_cache AS c
		 WHERE c.principal_id = $1::uuid
		   AND EXISTS (
		        SELECT 1 FROM jsonb_array_elements(c.items) AS item
		         WHERE item->>'document_id' IN (
		                 SELECT jsonb_array_elements_text($2::jsonb))
		            OR item->>'chunk_id' IN (
		                 SELECT jsonb_array_elements_text($3::jsonb)))`,
		targets.Job.OwnerID, uuidArrayArg(targets.DocumentIDs), uuidArrayArg(targets.ChunkIDs))
	if err != nil {
		return PurgeOK, err
	}
	if remaining > 0 {
		return PurgeCacheRemain, nil
	}
	return PurgeOK, nil
}

// writePurgeProgress is the one evidence transaction's write: scope lists, state,
// attempt count and error code on the locked job row. It never touches the
// tombstones (the read authority) and never deletes the job row.
func writePurgeProgress(
	ctx context.Context, tx *sql.Tx, job PurgeJob, state string,
	remaining, completed []Scope, failure PurgeFailure,
) error {
	remainingJSON, err := encodeScopes(remaining)
	if err != nil {
		return err
	}
	completedJSON, err := encodeScopes(completed)
	if err != nil {
		return err
	}
	var errorCode any
	if state == StateFailed {
		errorCode = failure.ErrorCode()
	}
	if _, err := tx.ExecContext(ctx, `
		UPDATE platform.deletion_jobs
		   SET remaining_scopes = $2::jsonb,
		       completed_scopes = $3::jsonb,
		       state = $4,
		       error_code = $5,
		       attempt_count = attempt_count + 1,
		       started_at = COALESCE(started_at, now()),
		       completed_at = CASE WHEN $4 = 'completed' THEN now() ELSE completed_at END,
		       updated_at = now()
		 WHERE id = $1::uuid`,
		job.ID, string(remainingJSON), string(completedJSON), state, errorCode); err != nil {
		return fmt.Errorf("recording the purge progress: %w", err)
	}
	return nil
}

func encodeScopes(scopes []Scope) ([]byte, error) {
	names := make([]string, 0, len(scopes))
	for _, scope := range scopes {
		names = append(names, string(scope))
	}
	encoded, err := json.Marshal(names)
	if err != nil {
		return nil, fmt.Errorf("encoding the deletion scopes: %w", err)
	}
	return encoded, nil
}

func countRemaining(ctx context.Context, tx *sql.Tx, query string, args ...any) (int, error) {
	var count int
	if err := tx.QueryRowContext(ctx, query, args...).Scan(&count); err != nil {
		return 0, fmt.Errorf("counting the remaining purge rows: %w", err)
	}
	return count, nil
}

// ErrObjectStoreUnavailable means the completion guard cannot verify the sealed
// bytes because this process has no object store. Fail closed: nothing is marked.
var ErrObjectStoreUnavailable = errors.New("the object store is not configured")

func scopeMap(details map[string]any, scope Scope) map[string]any {
	value, ok := details[string(scope)]
	if !ok {
		return nil
	}
	mapped, ok := value.(map[string]any)
	if !ok {
		return nil
	}
	return mapped
}

func objectItems(scope map[string]any) []map[string]any {
	raw, ok := scope["items"]
	if !ok {
		return nil
	}
	list, ok := raw.([]any)
	if !ok {
		return nil
	}
	items := make([]map[string]any, 0, len(list))
	for _, entry := range list {
		if mapped, ok := entry.(map[string]any); ok {
			items = append(items, mapped)
		}
	}
	return items
}

func stringValue(value any) string {
	text, _ := value.(string)
	return strings.TrimSpace(text)
}

// stringList accepts the descriptor arrays T069 writes ([]any of strings) and a
// bare string for a single-id descriptor.
func stringListFrom(value any) []string {
	switch typed := value.(type) {
	case nil:
		return nil
	case string:
		if trimmed := strings.TrimSpace(typed); trimmed != "" {
			return []string{trimmed}
		}
		return nil
	case []any:
		list := make([]string, 0, len(typed))
		for _, entry := range typed {
			if text, ok := entry.(string); ok {
				if trimmed := strings.TrimSpace(text); trimmed != "" {
					list = append(list, trimmed)
				}
			}
		}
		return list
	default:
		return nil
	}
}

func sortedKeys(set map[string]bool) []string {
	keys := make([]string, 0, len(set))
	for key := range set {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	return keys
}

func sortedStrings(values []string) []string {
	set := make(map[string]bool, len(values))
	for _, value := range values {
		if strings.TrimSpace(value) != "" {
			set[value] = true
		}
	}
	return sortedKeys(set)
}

func appendUniqueScope(scopes []Scope, scope Scope) []Scope {
	if containsScope(scopes, scope) {
		return scopes
	}
	return append(scopes, scope)
}

func containsScope(scopes []Scope, scope Scope) bool {
	for _, candidate := range scopes {
		if candidate == scope {
			return true
		}
	}
	return false
}

// uuidArrayArg encodes an id list for `jsonb_array_elements_text($n::jsonb)::uuid`.
// It is the array binding this codebase already uses (no driver-specific uuid[]
// encoding), and unlike `string_to_array` it is safe for an empty list.
func uuidArrayArg(ids []string) string {
	if len(ids) == 0 {
		return "[]"
	}
	encoded, err := json.Marshal(ids)
	if err != nil {
		return "[]"
	}
	return string(encoded)
}
