package embedding

import (
	"context"
	"errors"
	"math"
	"testing"
)

func TestStubIsDeterministicAndFixedDimension(t *testing.T) {
	first := StubVector("Orchara 知识库 txt/md ingestion", StubDimensions)
	second := StubVector("Orchara 知识库 txt/md ingestion", StubDimensions)
	if len(first) != StubDimensions {
		t.Fatalf("dimension = %d, want %d", len(first), StubDimensions)
	}
	for index := range first {
		if first[index] != second[index] {
			t.Fatalf("vector is not deterministic at %d: %v != %v", index, first[index], second[index])
		}
	}
}

func TestStubVectorIsUnitNormAndDiscriminatesText(t *testing.T) {
	left := StubVector("alpha beta gamma", StubDimensions)
	right := StubVector("alpha beta gamma", StubDimensions)
	other := StubVector("unrelated words entirely", StubDimensions)
	var norm float64
	for _, value := range left {
		norm += value * value
	}
	if math.Abs(math.Sqrt(norm)-1) > 1e-9 {
		t.Fatalf("norm = %v, want 1", math.Sqrt(norm))
	}
	if score := Cosine(left, right); math.Abs(score-1) > 1e-9 {
		t.Fatalf("identical cosine = %v, want 1", score)
	}
	if score := Cosine(left, other); score >= 0.99 {
		t.Fatalf("unrelated cosine = %v, want clearly below 1", score)
	}
}

func TestEmptyTextHasZeroVector(t *testing.T) {
	vector := StubVector("...", StubDimensions)
	for index, value := range vector {
		if value != 0 {
			t.Fatalf("index %d = %v, want 0", index, value)
		}
	}
	if score := Cosine(vector, StubVector("anything", StubDimensions)); score != 0 {
		t.Fatalf("zero vector cosine = %v, want 0", score)
	}
}

func TestStubServiceRecordsTheStubRevision(t *testing.T) {
	service := New("", "")
	if !service.IsStub() {
		t.Fatal("the empty provider must select the stub")
	}
	if service.ModelRevision() != StubModelRevision {
		t.Fatalf("revision = %q, want %q", service.ModelRevision(), StubModelRevision)
	}
	vectors, err := service.Embed(context.Background(), []string{"one", "two"})
	if err != nil {
		t.Fatalf("stub Embed: %v", err)
	}
	if len(vectors) != 2 || len(vectors[0]) != StubDimensions {
		t.Fatalf("vectors = %d x %d, want 2 x %d", len(vectors), len(vectors[0]), StubDimensions)
	}
}

func TestRealProviderPathFailsClosed(t *testing.T) {
	service := New("openai", "text-embedding-3-small")
	if service.IsStub() {
		t.Fatal("a configured provider must not fall back to the stub")
	}
	if service.ModelRevision() != "text-embedding-3-small" {
		t.Fatalf("revision = %q", service.ModelRevision())
	}
	if _, err := service.Embed(context.Background(), []string{"hello"}); !errors.Is(err, ErrNotConfigured) {
		t.Fatalf("Embed error = %v, want ErrNotConfigured", err)
	}
	if _, err := service.EmbedOne(context.Background(), "hello"); !errors.Is(err, ErrNotConfigured) {
		t.Fatalf("EmbedOne error = %v, want ErrNotConfigured", err)
	}
}

func TestLocalHashV2IsADistinctDeterministicStubRevision(t *testing.T) {
	first := New("stub", StubModelRevisionV2)
	if !first.IsStub() {
		t.Fatal("provider=stub with local-hash-v2 must select a deterministic stub")
	}
	if first.ModelRevision() != StubModelRevisionV2 {
		t.Fatalf("revision = %q, want %q", first.ModelRevision(), StubModelRevisionV2)
	}
	if first.Dimensions() != StubDimensions {
		t.Fatalf("dimensions = %d, want %d (the dimension stays fixed)", first.Dimensions(), StubDimensions)
	}
	vectors, err := first.Embed(context.Background(), []string{"alpha beta gamma", "alpha beta gamma"})
	if err != nil {
		t.Fatalf("stub v2 Embed: %v", err)
	}
	if len(vectors) != 2 || len(vectors[0]) != StubDimensions {
		t.Fatalf("vectors = %d x %d", len(vectors), len(vectors[0]))
	}
	for index := range vectors[0] {
		if vectors[0][index] != vectors[1][index] {
			t.Fatalf("v2 vector is not deterministic at %d", index)
		}
	}
	var norm float64
	for _, value := range vectors[0] {
		norm += value * value
	}
	if math.Abs(math.Sqrt(norm)-1) > 1e-9 {
		t.Fatalf("v2 norm = %v, want 1", math.Sqrt(norm))
	}
	// A model switch must produce a genuinely different vector space for the
	// same text; otherwise the separate index revisions would be cosmetic.
	old := StubVector("alpha beta gamma", StubDimensions)
	if score := Cosine(old, vectors[0]); score >= 0.99 {
		t.Fatalf("v2 cosine against v1 = %v, want a distinct vector space", score)
	}
}

func TestUnknownStubModelRevisionFailsClosed(t *testing.T) {
	service := New("stub", "local-hash-v3")
	if service.IsStub() {
		t.Fatal("an unknown deterministic revision must not silently use v1")
	}
	if _, err := service.Embed(context.Background(), []string{"hello"}); !errors.Is(err, ErrNotConfigured) {
		t.Fatalf("Embed error = %v, want ErrNotConfigured", err)
	}
}

func TestFromEnvSelectsExplicitProvider(t *testing.T) {
	t.Setenv(ProviderEnv, "stub")
	if !FromEnv().IsStub() {
		t.Fatal("provider=stub must select the stub")
	}
	t.Setenv(ProviderEnv, "deepseek")
	service := FromEnv()
	if service.IsStub() {
		t.Fatal("provider=deepseek must select the real path")
	}
	if service.Dimensions() != 0 {
		t.Fatalf("real path dimensions = %d, want 0 (unresolved)", service.Dimensions())
	}
}
