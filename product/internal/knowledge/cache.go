package knowledge

// F10/T067: the PostgreSQL retrieval cache.
//
// A repeated query may reuse one **qualified read-only** retrieval result, but a
// cache hit may never return deleted or revoked content:
//
//   - the key is a digest of principal + effective authorization revision +
//     knowledge-base revision + a digest of the current readable documents
//     (revision/state/active revision/active index id/active embedding revision)
//     + embedding model revision + canonical query + top_k, recomputed from
//     current state on every request. Identity, permission, source-version or
//     index-activation changes therefore produce a different key; an asynchronous
//     eviction is an optimization, never a permission guarantee.
//   - every hit re-checks each returned chunk's document in SQL (not deleted, the
//     chunk belongs to the document's active ready index version, body artifact
//     ready/not deleted/same owner) and re-reads the sealed body artifact: its
//     bytes must equal the stored text and its sha256 must equal the stored body
//     hash. Any disagreement, or an object that has become unreadable (which the
//     authoritative path would skip), discards the entry and the request proceeds
//     as a miss. The memoized text is never returned without the bytes behind it.
//   - the row is looked up by (cache_key, principal_id, knowledge_base_id), so a
//     stored body can never be served to a different principal.
//   - `expires_at` is a TTL, not an authorization: a hit inside the TTL still runs
//     the checks above.
//
// The cache is derived state. Losing it, or failing to write it, only costs
// performance: a failed lookup/write is handled as a miss and the authoritative
// retrieval path still answers.

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"strconv"
	"strings"
	"time"

	"orchara/platform/internal/config"
)

// RetrievalCacheEnv selects the deployment cache switch. "off" disables reuse
// (used by the comparison experiment); any other non-empty value is refused at
// startup instead of being guessed.
const RetrievalCacheEnv = "ORCHARA_KNOWLEDGE_RETRIEVAL_CACHE"

const (
	// RetrievalCacheKeyVersion changes whenever the key encoding changes; an old
	// row then simply never matches again.
	RetrievalCacheKeyVersion = "knowledge-retrieval-cache/1"
	// RetrievalCacheDefaultTTL bounds how long a memoized result may exist. It is
	// garbage collection, not authorization: every hit is still re-checked.
	RetrievalCacheDefaultTTL = 15 * time.Minute
	// RetrievalCacheMaxEntryBytes bounds one stored entry so a cache row cannot
	// become an unbounded copy of the knowledge base (top_k <= 50 chunks; the
	// chunker bounds one chunk at 1200 runes, so this is generous).
	RetrievalCacheMaxEntryBytes = 256 * 1024
	// retrievalCacheKeyPrefixLen is how much of the digest the API exposes for
	// debugging (never enough to reconstruct a query).
	retrievalCacheKeyPrefixLen = 16
)

// CacheOptions is the deployment's retrieval-cache configuration.
type CacheOptions struct {
	// Enabled is the deployment switch; the zero value is disabled so a caller
	// that forgets to configure the cache gets no cache rather than a surprise.
	Enabled bool
	// TTL is how long one entry may exist.
	TTL time.Duration
	// Now is injectable for tests; nil means time.Now.
	Now func() time.Time
}

// DefaultCacheOptions is the documented deployment default: enabled, 15 minutes.
func DefaultCacheOptions() CacheOptions {
	return CacheOptions{Enabled: true, TTL: RetrievalCacheDefaultTTL}
}

func (o CacheOptions) now() time.Time {
	if o.Now != nil {
		return o.Now()
	}
	return time.Now()
}

func (o CacheOptions) ttl() time.Duration {
	if o.TTL <= 0 {
		return RetrievalCacheDefaultTTL
	}
	return o.TTL
}

// CacheOptionsFromEnv reads ORCHARA_KNOWLEDGE_RETRIEVAL_CACHE. An unknown value
// is a startup error, never a silent default.
func CacheOptionsFromEnv(lookup config.Lookup) (CacheOptions, error) {
	options := DefaultCacheOptions()
	raw, _ := lookup(RetrievalCacheEnv)
	switch strings.ToLower(strings.TrimSpace(raw)) {
	case "", "on", "enabled", "true", "1":
		options.Enabled = true
	case "off", "disabled", "false", "0":
		options.Enabled = false
	default:
		return CacheOptions{}, fmt.Errorf(
			"%s must be one of on/off (got %q); the retrieval cache is never silently guessed",
			RetrievalCacheEnv, raw)
	}
	return options, nil
}

// canonicalCacheQuery is exactly the string the retrieval path embeds and ranks
// (strings.TrimSpace). Inner whitespace is significant because the embedding
// input is significant; normalizing it here would make a "hit" mean a different
// query than a miss would have answered.
func canonicalCacheQuery(raw string) string {
	return strings.TrimSpace(raw)
}

// retrievalCacheKey is every component a cached result depends on. The values
// are stored with the entry for debuggability and compared again on a hit.
type retrievalCacheKey struct {
	PrincipalID            string
	ACLEpoch               int64
	KnowledgeBaseID        string
	KBRevision             int64
	SourceRevisionDigest   string
	SourceDocumentCount    int
	EmbeddingModelRevision string
	CanonicalQuery         string
	TopK                   int
}

// digest is the hex SHA-256 of the length-prefixed components. Length prefixes
// keep component boundaries unambiguous (moving a character from the query into
// the model revision must not collide).
func (k retrievalCacheKey) digest() string {
	return hashCacheComponents(
		RetrievalCacheKeyVersion,
		k.PrincipalID,
		strconv.FormatInt(k.ACLEpoch, 10),
		k.KnowledgeBaseID,
		strconv.FormatInt(k.KBRevision, 10),
		k.SourceRevisionDigest,
		strconv.Itoa(k.SourceDocumentCount),
		k.EmbeddingModelRevision,
		k.CanonicalQuery,
		strconv.Itoa(k.TopK),
	)
}

// keyPrefix is the debuggable, non-reconstructive part of the digest.
func (k retrievalCacheKey) keyPrefix() string {
	digest := k.digest()
	if len(digest) <= retrievalCacheKeyPrefixLen {
		return digest
	}
	return digest[:retrievalCacheKeyPrefixLen]
}

func hashCacheComponents(components ...string) string {
	hasher := sha256.New()
	for _, component := range components {
		// Length-prefix each component and terminate it; the newline is only
		// cosmetic because the length already delimits the value.
		_, _ = fmt.Fprintf(hasher, "%d:", len(component))
		_, _ = hasher.Write([]byte(component))
		_, _ = hasher.Write([]byte{'\n'})
	}
	return hex.EncodeToString(hasher.Sum(nil))
}

// shouldStoreRetrievalCache decides whether one miss result may be memoized.
//
// Empty and partial results are never cached: an empty retrieval is exactly how
// the "missing target source" RAG failure class is observed, and a retrieval that
// had to skip an unreadable candidate is a transient condition. Caching either
// would mask the failure for the TTL.
func shouldStoreRetrievalCache(chunks []ChunkView, skipped int) (bool, string) {
	if len(chunks) == 0 {
		return false, "empty_result"
	}
	if skipped > 0 {
		return false, "partial_result"
	}
	bytes := 0
	for _, chunk := range chunks {
		bytes += len([]byte(chunk.Text)) + len(chunk.Citation)
	}
	if bytes > RetrievalCacheMaxEntryBytes {
		return false, "entry_too_large"
	}
	return true, ""
}

// cacheItemsBytes is the same body accounting the row stores in `body_bytes`.
func cacheItemsBytes(items []retrievalCacheItem) int {
	total := 0
	for _, item := range items {
		total += len([]byte(item.Text)) + len(item.Citation)
	}
	return total
}

// retrievalCacheItem is one returned chunk as stored in `items`. It carries the
// exact body artifact id and content hash so a hit can prove the memoized text is
// still the sealed body of the current chunk, not merely that some document row
// exists.
type retrievalCacheItem struct {
	ChunkID          string          `json:"chunk_id"`
	DocumentID       string          `json:"document_id"`
	DocumentRevision int64           `json:"document_revision"`
	ChunkNo          int             `json:"chunk_no"`
	Span             Span            `json:"span"`
	Citation         json.RawMessage `json:"citation"`
	BodyArtifactID   string          `json:"body_artifact_id"`
	BodySHA256       string          `json:"body_sha256"`
	Text             string          `json:"text"`
	Score            float64         `json:"score"`
}

// cacheItemOf projects one ranked retrieval row into the stored shape.
func cacheItemOf(view ChunkView, bodyArtifactID, bodySHA256 string) retrievalCacheItem {
	return retrievalCacheItem{
		ChunkID:          view.ChunkID,
		DocumentID:       view.DocumentID,
		DocumentRevision: view.DocumentRevision,
		ChunkNo:          view.ChunkNo,
		Span:             view.Span,
		Citation:         view.Citation,
		BodyArtifactID:   bodyArtifactID,
		BodySHA256:       bodySHA256,
		Text:             view.Text,
		Score:            view.Score,
	}
}

// cachedChunkView projects a stored item back to the retrieval answer. It does
// not add or invent provenance: every field is the one stored from the retrieval
// row (and re-validated in SQL before this is called).
func (item retrievalCacheItem) chunkView() ChunkView {
	return ChunkView{
		ChunkID:          item.ChunkID,
		DocumentID:       item.DocumentID,
		DocumentRevision: item.DocumentRevision,
		DocumentState:    StateReady,
		ChunkNo:          item.ChunkNo,
		Span:             item.Span,
		Citation:         item.Citation,
		Text:             item.Text,
		Score:            item.Score,
	}
}

// matchesSealedBody proves this memoized item is still exactly the sealed body:
// the bytes and their sha256 both have to equal what the entry stored. The
// database rows alone cannot prove this (the object is the authoritative byte
// store). It lives on the item it inspects rather than taking the item as a
// free-function argument (F13B/T113 review fix T113-4, Feature Envy).
func (item retrievalCacheItem) matchesSealedBody(sealedText string) (bool, string) {
	if sealedText != item.Text {
		return false, CacheDiscardItemMismatch
	}
	if cacheBodySHA256(sealedText) != item.BodySHA256 {
		return false, CacheDiscardItemMismatch
	}
	return true, ""
}
