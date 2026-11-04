package knowledge

// F10/T067: the database side of the retrieval cache.
//
// Reads and writes are best effort: a failed cache lookup or store degrades to the
// authoritative (uncached) retrieval path, because the cache is derived state and
// losing it must never change what a query answers. The *only* mandatory checks
// are the ones that decide whether a stored result may be returned.

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"strconv"
	"strings"

	"orchara/platform/internal/uuid"
)

// CacheDiscardReason values are the safe, observable reasons a live entry was not
// served. They explain a miss on the page/ops surface without naming a chunk.
const (
	CacheDiscardExpired           = "expired"
	CacheDiscardComponentsChanged = "components_changed"
	CacheDiscardItemUnreadable    = "item_unreadable"
	CacheDiscardItemMismatch      = "item_mismatch"
	CacheDiscardUnavailable       = "cache_unavailable"
)

// RetrievalCacheInfo is the per-request observable state of the cache. It is
// additive metadata on the non-contract retrieval/answer surface (ADR 0014).
type RetrievalCacheInfo struct {
	Enabled         bool
	Hit             bool
	Discarded       bool
	DiscardReason   string
	KeyPrefix       string
	ValidatedChunks int
	Stored          bool
	TTLSeconds      int64
}

// cacheKeyFor derives the key from the *current* authoritative rows. The source
// digest makes a newly added, deleted or re-activated document a different key,
// so a result that lacks (or still contains) a changed source cannot be reused.
func (s *Service) cacheKeyFor(
	ctx context.Context, ownerID string, base Base, modelRevision, canonicalQuery string, topK int,
) (retrievalCacheKey, error) {
	digest, count, err := s.sourceRevisionDigest(ctx, ownerID, base.ID)
	if err != nil {
		return retrievalCacheKey{}, err
	}
	return retrievalCacheKey{
		PrincipalID:            ownerID,
		ACLEpoch:               base.Revision,
		KnowledgeBaseID:        base.ID,
		KBRevision:             base.Revision,
		SourceRevisionDigest:   digest,
		SourceDocumentCount:    count,
		EmbeddingModelRevision: modelRevision,
		CanonicalQuery:         canonicalQuery,
		TopK:                   topK,
	}, nil
}

// sourceRevisionDigest hashes the current non-deleted documents of one knowledge
// base: (id, revision, state, active_revision). Adding, deleting or re-activating
// a source changes it; the count is stored with the entry for debugging.
func (s *Service) sourceRevisionDigest(
	ctx context.Context, ownerID, baseID string,
) (string, int, error) {
	rows, err := s.DB.QueryContext(ctx, `
		SELECT d.id::text, d.revision, d.state, coalesce(d.active_revision, 0)
		  FROM platform.knowledge_documents AS d
		  JOIN platform.knowledge_bases AS b ON b.id = d.knowledge_base_id
		 WHERE d.knowledge_base_id = $1::uuid AND b.owner_id = $2::uuid
		   AND b.deleted_at IS NULL AND d.deleted_at IS NULL
		 ORDER BY d.id`,
		baseID, ownerID,
	)
	if err != nil {
		return "", 0, fmt.Errorf("reading the source revisions: %w", err)
	}
	defer func() { _ = rows.Close() }()

	var (
		builder     strings.Builder
		documentCnt int
	)
	for rows.Next() {
		var (
			documentID     string
			revision       int64
			state          string
			activeRevision int64
		)
		if err := rows.Scan(&documentID, &revision, &state, &activeRevision); err != nil {
			return "", 0, fmt.Errorf("reading a source revision: %w", err)
		}
		builder.WriteString(documentID)
		builder.WriteByte('|')
		builder.WriteString(strconv.FormatInt(revision, 10))
		builder.WriteByte('|')
		builder.WriteString(state)
		builder.WriteByte('|')
		builder.WriteString(strconv.FormatInt(activeRevision, 10))
		builder.WriteByte('\n')
		documentCnt++
	}
	if err := rows.Err(); err != nil {
		return "", 0, fmt.Errorf("reading the source revisions: %w", err)
	}
	return hashCacheComponents("knowledge-source-revisions/1", builder.String()), documentCnt, nil
}

// cachedRetrieval looks one entry up by (cache_key, principal, knowledge base) and
// runs the mandatory hit checks. It returns a hit only when every check passes and
// every returned chunk is readable now.
func (s *Service) cachedRetrieval(
	ctx context.Context, key retrievalCacheKey,
) ([]ChunkView, RetrievalCacheInfo, error) {
	info := RetrievalCacheInfo{
		Enabled:    true,
		KeyPrefix:  key.keyPrefix(),
		TTLSeconds: int64(s.Cache.ttl().Seconds()),
	}
	var (
		aclEpoch               int64
		kbRevision             int64
		sourceRevisionDigest   string
		sourceDocumentCount    int
		embeddingModelRevision string
		canonicalQuery         string
		topK                   int
		itemsJSON              []byte
		expiresAt              sql.NullTime
	)
	err := s.DB.QueryRowContext(ctx, `
		SELECT acl_epoch, kb_revision, source_revision_digest, source_document_count,
		       embedding_model_revision, canonical_query, top_k, items, expires_at
		  FROM platform.knowledge_retrieval_cache
		 WHERE cache_key = $1 AND principal_id = $2::uuid AND knowledge_base_id = $3::uuid`,
		key.digest(), key.PrincipalID, key.KnowledgeBaseID,
	).Scan(
		&aclEpoch, &kbRevision, &sourceRevisionDigest, &sourceDocumentCount,
		&embeddingModelRevision, &canonicalQuery, &topK, &itemsJSON, &expiresAt,
	)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, info, nil
	}
	if err != nil {
		// Derived state: an unreadable cache is a miss, never a failed query.
		info.Discarded = true
		info.DiscardReason = CacheDiscardUnavailable
		return nil, info, nil
	}

	// Stored components must agree with the recomputed key. The digest already
	// encodes them; this comparison makes a tampered/colliding row visible and
	// keeps the stored values honest for debugging.
	if aclEpoch != key.ACLEpoch || kbRevision != key.KBRevision ||
		sourceRevisionDigest != key.SourceRevisionDigest ||
		sourceDocumentCount != key.SourceDocumentCount ||
		embeddingModelRevision != key.EmbeddingModelRevision ||
		canonicalQuery != key.CanonicalQuery || topK != key.TopK {
		s.discardCacheEntry(ctx, key)
		info.Discarded = true
		info.DiscardReason = CacheDiscardComponentsChanged
		return nil, info, nil
	}
	if !expiresAt.Valid || !expiresAt.Time.After(s.Cache.now()) {
		s.discardCacheEntry(ctx, key)
		info.Discarded = true
		info.DiscardReason = CacheDiscardExpired
		return nil, info, nil
	}

	var items []retrievalCacheItem
	if err := json.Unmarshal(itemsJSON, &items); err != nil || len(items) == 0 {
		s.discardCacheEntry(ctx, key)
		info.Discarded = true
		info.DiscardReason = CacheDiscardItemMismatch
		return nil, info, nil
	}
	ok, reason := s.validateCachedItems(ctx, key, items)
	if !ok {
		if reason == CacheDiscardUnavailable {
			// The validation query itself failed; leave the row alone and miss.
			info.Discarded = true
			info.DiscardReason = reason
			return nil, info, nil
		}
		s.discardCacheEntry(ctx, key)
		info.Discarded = true
		info.DiscardReason = reason
		return nil, info, nil
	}

	// Evidence only; failing to record the hit does not change the answer.
	_, _ = s.DB.ExecContext(ctx, `
		UPDATE platform.knowledge_retrieval_cache
		   SET hit_count = hit_count + 1, last_hit_at = now()
		 WHERE cache_key = $1`, key.digest())

	chunks := make([]ChunkView, 0, len(items))
	for _, item := range items {
		chunks = append(chunks, item.chunkView())
	}
	info.Hit = true
	info.ValidatedChunks = len(items)
	return chunks, info, nil
}

// validateCachedItems re-checks every returned chunk's document in one SQL
// statement, using exactly the retrieval/citation predicates: owner ACL, KB and
// document tombstones, ready + active revision, chunk revision = current revision,
// body artifact ready/not-deleted/same owner. The body artifact's content hash and
// the chunk provenance must equal the stored values, so a memoized body is only
// served while it is still the sealed body of the current chunk.
func (s *Service) validateCachedItems(
	ctx context.Context, key retrievalCacheKey, items []retrievalCacheItem,
) (bool, string) {
	ids := make([]string, 0, len(items))
	for _, item := range items {
		if !uuid.Valid(item.ChunkID) {
			return false, CacheDiscardItemMismatch
		}
		ids = append(ids, item.ChunkID)
	}
	rows, err := s.DB.QueryContext(ctx, `
		SELECT kc.id::text, kc.document_id::text, kc.document_revision, kc.chunk_no,
		       kc.body_artifact_id::text, coalesce(a.sha256, '')
		  FROM platform.knowledge_chunks AS kc
		  JOIN platform.knowledge_documents AS kd ON kd.id = kc.document_id
		  JOIN platform.knowledge_bases AS kb ON kb.id = kd.knowledge_base_id
		  JOIN platform.artifacts AS a ON a.id = kc.body_artifact_id
		 WHERE kc.id = ANY(string_to_array($1::text, ',')::uuid[])
		   AND kb.id = $2::uuid AND kb.owner_id = $3::uuid AND kb.deleted_at IS NULL
		   AND kd.deleted_at IS NULL AND kd.state = 'ready'
		   AND kd.active_revision = kd.revision
		   AND kc.document_revision = kd.revision
		   AND a.owner_id = kb.owner_id
		   AND a.deleted_at IS NULL AND a.state = 'ready'`,
		strings.Join(ids, ","), key.KnowledgeBaseID, key.PrincipalID,
	)
	if err != nil {
		return false, CacheDiscardUnavailable
	}
	defer func() { _ = rows.Close() }()

	type currentChunk struct {
		documentID     string
		revision       int64
		chunkNo        int
		bodyArtifactID string
		bodySHA256     string
	}
	found := make(map[string]currentChunk, len(items))
	for rows.Next() {
		var (
			chunkID string
			current currentChunk
		)
		if err := rows.Scan(
			&chunkID, &current.documentID, &current.revision, &current.chunkNo,
			&current.bodyArtifactID, &current.bodySHA256,
		); err != nil {
			return false, CacheDiscardUnavailable
		}
		found[chunkID] = current
	}
	if err := rows.Err(); err != nil {
		return false, CacheDiscardUnavailable
	}
	if len(found) != len(items) {
		return false, CacheDiscardItemUnreadable
	}
	for _, item := range items {
		current, ok := found[item.ChunkID]
		if !ok {
			return false, CacheDiscardItemUnreadable
		}
		if current.documentID != item.DocumentID ||
			current.revision != item.DocumentRevision ||
			current.chunkNo != item.ChunkNo ||
			current.bodyArtifactID != item.BodyArtifactID ||
			current.bodySHA256 != item.BodySHA256 {
			return false, CacheDiscardItemMismatch
		}
	}
	return true, ""
}

func (s *Service) discardCacheEntry(ctx context.Context, key retrievalCacheKey) {
	_, _ = s.DB.ExecContext(ctx, `
		DELETE FROM platform.knowledge_retrieval_cache
		 WHERE cache_key = $1 AND principal_id = $2::uuid AND knowledge_base_id = $3::uuid`,
		key.digest(), key.PrincipalID, key.KnowledgeBaseID)
}

// storeCachedRetrieval writes one qualified result. It refuses entries the policy
// excludes and is best effort: the caller keeps the uncached result on error.
func (s *Service) storeCachedRetrieval(
	ctx context.Context, key retrievalCacheKey, items []retrievalCacheItem,
) (bool, error) {
	if len(items) == 0 {
		return false, nil
	}
	for _, item := range items {
		if item.BodyArtifactID == "" || item.BodySHA256 == "" || item.ChunkID == "" {
			return false, nil
		}
	}
	bodyBytes := cacheItemsBytes(items)
	if bodyBytes > RetrievalCacheMaxEntryBytes {
		return false, nil
	}
	encoded, err := json.Marshal(items)
	if err != nil {
		return false, fmt.Errorf("encoding the cache entry: %w", err)
	}
	// Lazy TTL cleanup for this principal only: bounded work on the miss path.
	_, _ = s.DB.ExecContext(ctx, `
		DELETE FROM platform.knowledge_retrieval_cache
		 WHERE principal_id = $1::uuid AND expires_at <= now()`, key.PrincipalID)

	now := s.Cache.now()
	expiresAt := now.Add(s.Cache.ttl())
	if _, err := s.DB.ExecContext(ctx, `
		INSERT INTO platform.knowledge_retrieval_cache
			(cache_key, principal_id, knowledge_base_id, acl_epoch, kb_revision,
			 source_revision_digest, source_document_count, embedding_model_revision,
			 canonical_query, query_sha256, top_k, item_count, body_bytes, items,
			 created_at, expires_at)
		VALUES ($1, $2::uuid, $3::uuid, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13,
		        $14::jsonb, $15, $16)
		ON CONFLICT (cache_key) DO UPDATE
		   SET items = EXCLUDED.items,
		       item_count = EXCLUDED.item_count,
		       body_bytes = EXCLUDED.body_bytes,
		       created_at = EXCLUDED.created_at,
		       expires_at = EXCLUDED.expires_at`,
		key.digest(), key.PrincipalID, key.KnowledgeBaseID, key.ACLEpoch, key.KBRevision,
		key.SourceRevisionDigest, key.SourceDocumentCount, key.EmbeddingModelRevision,
		key.CanonicalQuery, hashCacheComponents("knowledge-query/1", key.CanonicalQuery),
		key.TopK, len(items), bodyBytes, string(encoded),
		now, expiresAt,
	); err != nil {
		return false, fmt.Errorf("storing the cache entry: %w", err)
	}
	return true, nil
}
