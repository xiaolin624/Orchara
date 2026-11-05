package knowledge

// F04B/T088: a read-only multi-knowledge-base query used by the internal
// retrieval route. It is deliberately not a new retrieval algorithm: it calls the
// existing ACL-filtered Query once per knowledge base, then merges the surviving
// ranked chunks. A base the owner cannot see is silently excluded and only counted
// (no existence leak); an empty merged set is a normal empty answer.

import (
	"context"
	"errors"
	"fmt"
	"sort"
	"strings"

	"orchara/platform/internal/uuid"
)

// MaxQueryBases bounds how many knowledge bases one retrieval may name. It is the
// internal route's resource bound, not the contract's node config.
const MaxQueryBases = 20

// MultiQueryResult is the non-contract answer over several knowledge bases.
// Requested is how many bases were named; Authorized is how many the owner could
// actually see. The excluded ids are never echoed.
type MultiQueryResult struct {
	Items      []ChunkView
	Requested  int
	Authorized int
}

// QueryBases answers one query over several owner-scoped knowledge bases.
//
// Each base goes through the existing Query path, so the SQL ACL, the document
// tombstones, the active index version, the embedding revision and the body
// artifact readiness are all applied before ranking. Bases that are missing,
// foreign or deleted are skipped (Query's ErrNotFound), matching the platform's
// "404, never 403" rule. A real infrastructure failure (embedding provider,
// database) aborts the whole answer instead of pretending those bases were empty.
func (s *Service) QueryBases(
	ctx context.Context, ownerID string, baseIDs []string, query string, topK int,
) (MultiQueryResult, error) {
	if len(baseIDs) == 0 {
		return MultiQueryResult{}, &ValidationError{
			Field: "knowledge_base_ids", Message: "at least one knowledge base is required",
		}
	}
	if len(baseIDs) > MaxQueryBases {
		return MultiQueryResult{}, &ValidationError{
			Field:   "knowledge_base_ids",
			Message: fmt.Sprintf("at most %d knowledge bases may be queried at once", MaxQueryBases),
		}
	}
	result := MultiQueryResult{Requested: len(baseIDs)}
	merged := make([]ChunkView, 0, topK*len(baseIDs))
	seen := make(map[string]bool, len(baseIDs))
	for position, rawID := range baseIDs {
		id := strings.ToLower(strings.TrimSpace(rawID))
		if !uuid.Valid(id) {
			return MultiQueryResult{}, &ValidationError{
				Field:   "knowledge_base_ids",
				Message: fmt.Sprintf("knowledge_base_ids[%d] is not a UUID", position),
			}
		}
		if seen[id] {
			continue
		}
		seen[id] = true
		answer, err := s.Query(ctx, ownerID, id, query, topK)
		switch {
		case errors.Is(err, ErrNotFound):
			// Missing, foreign, deleted: excluded silently. The caller only learns
			// the authorized count, never which id was invisible.
			continue
		case err != nil:
			return MultiQueryResult{}, err
		}
		result.Authorized++
		merged = append(merged, answer.Items...)
	}
	result.Items = mergeChunkViews(merged, topK)
	return result, nil
}

// mergeChunkViews merges the per-base top_k lists into one deterministic answer:
// score DESC, then chunk_id ASC as the stable tie-break (chunk ids are globally
// unique), truncated to top_k. Duplicate chunk ids cannot occur for distinct
// bases, but the merge still de-duplicates defensively.
func mergeChunkViews(items []ChunkView, topK int) []ChunkView {
	unique := make([]ChunkView, 0, len(items))
	seen := make(map[string]bool, len(items))
	for _, item := range items {
		if seen[item.ChunkID] {
			continue
		}
		seen[item.ChunkID] = true
		unique = append(unique, item)
	}
	sort.SliceStable(unique, func(left, right int) bool {
		if unique[left].Score != unique[right].Score {
			return unique[left].Score > unique[right].Score
		}
		return unique[left].ChunkID < unique[right].ChunkID
	})
	if topK > 0 && len(unique) > topK {
		unique = unique[:topK]
	}
	return unique
}
