package knowledge

// F10/T067 unit seam: the retrieval cache key, its deployment switch and the
// store policy. These are pure decisions, so they are testable without a
// database; the real-stack suite proves the hit/miss/validation behaviour.

import (
	"errors"
	"fmt"
	"strings"
	"testing"
	"time"

	"orchara/platform/internal/config"
	"orchara/platform/internal/objectstore"
)

func cacheLookup(values map[string]string) config.Lookup {
	return func(key string) (string, bool) {
		value, ok := values[key]
		return value, ok
	}
}

func TestCacheOptionsFromEnvDefaultsToEnabledAndRejectsUnknownValues(t *testing.T) {
	options, err := CacheOptionsFromEnv(cacheLookup(map[string]string{}))
	if err != nil {
		t.Fatalf("CacheOptionsFromEnv: %v", err)
	}
	if !options.Enabled || options.TTL != RetrievalCacheDefaultTTL {
		t.Fatalf("default options = %+v", options)
	}

	disabled, err := CacheOptionsFromEnv(
		cacheLookup(map[string]string{RetrievalCacheEnv: " off "}),
	)
	if err != nil {
		t.Fatalf("CacheOptionsFromEnv(off): %v", err)
	}
	if disabled.Enabled {
		t.Fatalf("off did not disable the cache: %+v", disabled)
	}
	if disabled.TTL != RetrievalCacheDefaultTTL {
		t.Fatalf("off changed the TTL: %+v", disabled)
	}

	if _, err := CacheOptionsFromEnv(
		cacheLookup(map[string]string{RetrievalCacheEnv: "maybe"}),
	); err == nil {
		t.Fatal("an unknown switch value must be refused, not guessed")
	}
}

func TestCanonicalCacheQueryTrimsOnlyTheOuterWhitespace(t *testing.T) {
	// The cache key must name exactly the string that is embedded and ranked;
	// T055 embeds strings.TrimSpace(query), so inner whitespace and line breaks
	// are significant and must not be collapsed.
	for raw, want := range map[string]string{
		"  query  ":  "query",
		"\n 华南 十月\n": "华南 十月",
		"a  b":       "a  b",
		"第一行\n第二行":   "第一行\n第二行",
	} {
		if got := canonicalCacheQuery(raw); got != want {
			t.Fatalf("canonicalCacheQuery(%q) = %q, want %q", raw, got, want)
		}
	}
}

func cacheTestKey() retrievalCacheKey {
	return retrievalCacheKey{
		PrincipalID:            "11111111-1111-4111-8111-111111111111",
		ACLEpoch:               3,
		KnowledgeBaseID:        "22222222-2222-4222-8222-222222222222",
		KBRevision:             3,
		SourceRevisionDigest:   strings.Repeat("a", 64),
		SourceDocumentCount:    2,
		EmbeddingModelRevision: "local-hash-v1",
		CanonicalQuery:         "华北地区十月的销售额是多少",
		TopK:                   8,
	}
}

func TestRetrievalCacheKeyIsDeterministicAndSeparatesEveryComponent(t *testing.T) {
	key := cacheTestKey()
	if key.digest() != cacheTestKey().digest() {
		t.Fatal("the same components must produce the same digest")
	}
	if len(key.digest()) != 64 || key.keyPrefix() != key.digest()[:16] {
		t.Fatalf("unexpected digest %q prefix %q", key.digest(), key.keyPrefix())
	}

	mutations := map[string]func(*retrievalCacheKey){
		"principal":       func(k *retrievalCacheKey) { k.PrincipalID = "33333333-3333-4333-8333-333333333333" },
		"acl_epoch":       func(k *retrievalCacheKey) { k.ACLEpoch++ },
		"knowledge_base":  func(k *retrievalCacheKey) { k.KnowledgeBaseID = "44444444-4444-4444-8444-444444444444" },
		"kb_revision":     func(k *retrievalCacheKey) { k.KBRevision++ },
		"source_digest":   func(k *retrievalCacheKey) { k.SourceRevisionDigest = strings.Repeat("b", 64) },
		"source_count":    func(k *retrievalCacheKey) { k.SourceDocumentCount++ },
		"embedding_model": func(k *retrievalCacheKey) { k.EmbeddingModelRevision = "local-hash-v2" },
		"query":           func(k *retrievalCacheKey) { k.CanonicalQuery += "？" },
		"top_k":           func(k *retrievalCacheKey) { k.TopK++ },
	}
	for name, mutate := range mutations {
		changed := cacheTestKey()
		mutate(&changed)
		if changed.digest() == key.digest() {
			t.Fatalf("changing %s did not change the cache key", name)
		}
	}
}

func TestRetrievalCacheKeyDoesNotCollideAcrossComponentBoundaries(t *testing.T) {
	// Length-prefixed components: moving a character from one component into the
	// next must change the digest, even when the concatenation is identical.
	left := cacheTestKey()
	left.CanonicalQuery = "ab"
	left.EmbeddingModelRevision = "c"
	right := cacheTestKey()
	right.CanonicalQuery = "a"
	right.EmbeddingModelRevision = "bc"
	if left.digest() == right.digest() {
		t.Fatal("component boundary collision: the digest must length-prefix each component")
	}
}

func TestShouldStoreRetrievalCacheRefusesEmptyPartialOrOversizedEntries(t *testing.T) {
	chunks := []ChunkView{
		sampleChunk("chunk-1", "doc-1", 1, 0, "华北地区十月的销售额是 1234.50 元"),
	}
	if ok, reason := shouldStoreRetrievalCache(chunks, 0); !ok {
		t.Fatalf("one complete chunk must be cacheable, refused with %q", reason)
	}
	if ok, reason := shouldStoreRetrievalCache(nil, 0); ok || reason != "empty_result" {
		t.Fatalf("empty retrieval must not be cached (reason %q)", reason)
	}
	if ok, reason := shouldStoreRetrievalCache(chunks, 1); ok || reason != "partial_result" {
		t.Fatalf("a retrieval with a skipped candidate must not be cached (reason %q)", reason)
	}
	huge := []ChunkView{
		sampleChunk("chunk-1", "doc-1", 1, 0, strings.Repeat("x", RetrievalCacheMaxEntryBytes+1)),
	}
	if ok, reason := shouldStoreRetrievalCache(huge, 0); ok || reason != "entry_too_large" {
		t.Fatalf("an entry over the byte bound must not be cached (reason %q)", reason)
	}
}

func TestRetrievalCacheTTLIsBoundedAndPositive(t *testing.T) {
	if RetrievalCacheDefaultTTL < time.Minute || RetrievalCacheDefaultTTL > 24*time.Hour {
		t.Fatalf("RetrievalCacheDefaultTTL = %s is outside the documented bound", RetrievalCacheDefaultTTL)
	}
}

func TestCachedBodyVerificationRejectsChangedSealedBytes(t *testing.T) {
	// F13B/T113: a hit may only serve the bytes that are still sealed under the
	// chunk's body artifact. The database sha comparison alone is not enough: a
	// temporarily unreadable or replaced object would otherwise be served from the
	// memoized text while the authoritative path skips it.
	text := "华北地区十月的销售额是 1234.50 元"
	item := retrievalCacheItem{
		ChunkID:    "chunk-1",
		Text:       text,
		BodySHA256: cacheBodySHA256(text),
	}
	if ok, reason := item.matchesSealedBody(text); !ok {
		t.Fatalf("identical sealed bytes must match (reason %q)", reason)
	}
	if ok, reason := item.matchesSealedBody(text + "篡改"); ok || reason != CacheDiscardItemMismatch {
		t.Fatalf("changed bytes must be a mismatch (ok=%v reason=%q)", ok, reason)
	}
	changedSHA := item
	changedSHA.BodySHA256 = strings.Repeat("0", 64)
	if ok, reason := changedSHA.matchesSealedBody(text); ok || reason != CacheDiscardItemMismatch {
		t.Fatalf("a body whose sha no longer matches must be a mismatch (ok=%v reason=%q)", ok, reason)
	}
}

// TestTransientSealedBodyReadFailureKeepsTheCacheRow is F13B/T113 review fix
// T113-2. A hit re-reads every sealed body; an I/O or availability blip must be
// a miss for this request only (the cache is a rebuildable optimization), while
// a deterministic refusal (missing object, invalid key, oversized object) still
// discards the entry with the existing safe vocabulary.
func TestTransientSealedBodyReadFailureKeepsTheCacheRow(t *testing.T) {
	cases := []struct {
		name        string
		readErr     error
		wantReason  string
		wantDiscard bool
	}{
		{"missing object", objectstore.ErrNotExist, CacheDiscardItemUnreadable, true},
		{"wrapped missing object", fmt.Errorf("opening the object: %w", objectstore.ErrNotExist), CacheDiscardItemUnreadable, true},
		{"invalid key", objectstore.ErrInvalidKey, CacheDiscardItemUnreadable, true},
		{"oversized object", objectstore.ErrTooLarge, CacheDiscardItemUnreadable, true},
		{"io error", errors.New("the device is temporarily unavailable"), CacheDiscardUnavailable, false},
		{"wrapped io error", fmt.Errorf("opening the object: %w", errors.New("sharing violation")), CacheDiscardUnavailable, false},
	}
	safe := map[string]bool{
		CacheDiscardExpired:           true,
		CacheDiscardComponentsChanged: true,
		CacheDiscardItemUnreadable:    true,
		CacheDiscardItemMismatch:      true,
		CacheDiscardUnavailable:       true,
	}
	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			reason := cacheHitReadFailureReason(testCase.readErr)
			if reason != testCase.wantReason {
				t.Fatalf("reason = %q, want %q", reason, testCase.wantReason)
			}
			if discard := cacheDiscardDecision(reason); discard != testCase.wantDiscard {
				t.Fatalf("discard = %v, want %v (the row must survive only a transient failure)",
					discard, testCase.wantDiscard)
			}
			if !safe[reason] {
				t.Fatalf("reason %q is outside the ADR 0015 D6 safe vocabulary", reason)
			}
		})
	}
}
