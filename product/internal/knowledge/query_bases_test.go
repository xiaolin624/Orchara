package knowledge

// F04B/T088: the multi-knowledge-base merge used by the internal retrieval route.

import "testing"

func TestMergeChunkViewsOrdersByScoreThenChunkID(t *testing.T) {
	merged := mergeChunkViews([]ChunkView{
		{ChunkID: "chunk-b", Score: 0.5},
		{ChunkID: "chunk-c", Score: 0.9},
		{ChunkID: "chunk-a", Score: 0.5},
	}, 10)
	if len(merged) != 3 {
		t.Fatalf("merged = %+v", merged)
	}
	want := []string{"chunk-c", "chunk-a", "chunk-b"}
	for position, id := range want {
		if merged[position].ChunkID != id {
			t.Fatalf("position %d = %q, want %q (%+v)", position, merged[position].ChunkID, id, merged)
		}
	}
}

func TestMergeChunkViewsKeepsTopKAndDeduplicates(t *testing.T) {
	merged := mergeChunkViews([]ChunkView{
		{ChunkID: "chunk-a", Score: 0.9},
		{ChunkID: "chunk-a", Score: 0.9},
		{ChunkID: "chunk-b", Score: 0.8},
		{ChunkID: "chunk-c", Score: 0.7},
	}, 2)
	if len(merged) != 2 {
		t.Fatalf("merged = %+v", merged)
	}
	if merged[0].ChunkID != "chunk-a" || merged[1].ChunkID != "chunk-b" {
		t.Fatalf("merged = %+v", merged)
	}
}

func TestMergeChunkViewsIsAnEmptyListWithoutItems(t *testing.T) {
	merged := mergeChunkViews(nil, 5)
	if merged == nil || len(merged) != 0 {
		t.Fatalf("merged = %#v (want an empty non-nil list)", merged)
	}
}
