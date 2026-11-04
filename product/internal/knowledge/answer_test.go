package knowledge

// F10/T064 unit seam: the answer draft is untrusted, and the server projection is
// built from this retrieval only. These tests exercise the pure validation that
// runs before any database re-read; the HTTP suite proves the re-read.

import (
	"context"
	"encoding/json"
	"errors"
	"strconv"
	"strings"
	"testing"
	"time"
)

func sampleChunk(id, documentID string, revision int64, no int, text string) ChunkView {
	return ChunkView{
		ChunkID:          id,
		DocumentID:       documentID,
		DocumentRevision: revision,
		DocumentState:    StateReady,
		ChunkNo:          no,
		Score:            0.9 - float64(no)*0.1,
		Text:             text,
		Span: Span{
			StartChar: no * 10,
			EndChar:   no*10 + len([]rune(text)),
			StartLine: no + 1,
			EndLine:   no + 1,
		},
		Citation: json.RawMessage(`{"document_id":"` + documentID + `","chunk_no":` +
			strconv.Itoa(no) + `}`),
	}
}

func TestExtractiveStubComposesTheAnswerFromTheRetrievedChunks(t *testing.T) {
	stub := &ExtractiveStub{}
	chunks := []ChunkView{
		sampleChunk("chunk-1", "doc-1", 1, 0, "华北地区十月的销售额是 1234.50 元"),
		sampleChunk("chunk-2", "doc-1", 1, 1, "退款需在订单完成后 30 天内提交申请"),
	}

	draft, err := stub.Generate(context.Background(), "华北销售额是多少", chunks)
	if err != nil {
		t.Fatalf("Generate: %v", err)
	}
	if !strings.Contains(draft.Text, "1234.50") || !strings.Contains(draft.Text, "30 天") {
		t.Fatalf("the stub answer must be composed from the retrieved chunk texts, got %q", draft.Text)
	}
	if len(draft.CitedChunkIDs) != 2 ||
		draft.CitedChunkIDs[0] != "chunk-1" || draft.CitedChunkIDs[1] != "chunk-2" {
		t.Fatalf("the stub must cite the chunks it used, in retrieval order: %v", draft.CitedChunkIDs)
	}
	if !stub.Stub() {
		t.Fatal("the extractive generator must label itself as a stub")
	}
	if stub.Version() != AnswerGeneratorVersion {
		t.Fatalf("version = %q", stub.Version())
	}
}

func TestExtractiveStubDoesNotCiteMoreChunksThanItsBoundedLimit(t *testing.T) {
	stub := &ExtractiveStub{}
	chunks := make([]ChunkView, 0, AnswerCiteLimit+2)
	for index := 0; index < AnswerCiteLimit+2; index++ {
		chunks = append(chunks, sampleChunk("chunk-"+strconv.Itoa(index), "doc-1", 1, index, "片段正文"))
	}
	draft, err := stub.Generate(context.Background(), "问题", chunks)
	if err != nil {
		t.Fatalf("Generate: %v", err)
	}
	if len(draft.CitedChunkIDs) != AnswerCiteLimit {
		t.Fatalf("cited %d chunks, want the bounded %d", len(draft.CitedChunkIDs), AnswerCiteLimit)
	}
}

func TestAnswerDraftWithoutACitationIsRefused(t *testing.T) {
	retrieved := []ChunkView{sampleChunk("chunk-1", "doc-1", 1, 0, "正文")}

	validation := validateAnswerDraft(AnswerDraft{Text: "我自己知道的答案"}, retrieved)
	if validation.Outcome != OutcomeAnswerWithoutCitation {
		t.Fatalf("outcome = %q, want %q", validation.Outcome, OutcomeAnswerWithoutCitation)
	}
	if len(validation.Citations) != 0 {
		t.Fatalf("a refused answer must not carry citations: %v", validation.Citations)
	}
}

func TestAnswerDraftWithACitationFromOutsideThisRetrievalIsRefused(t *testing.T) {
	retrieved := []ChunkView{sampleChunk("chunk-1", "doc-1", 1, 0, "正文")}

	validation := validateAnswerDraft(
		AnswerDraft{Text: "答案", CitedChunkIDs: []string{"chunk-1", "invented-chunk"}},
		retrieved,
	)
	if validation.Outcome != OutcomeCitationNotFound {
		t.Fatalf("outcome = %q, want %q", validation.Outcome, OutcomeCitationNotFound)
	}
	if validation.OffendingChunkID != "invented-chunk" {
		t.Fatalf("offending chunk = %q", validation.OffendingChunkID)
	}
	if len(validation.Citations) != 0 {
		t.Fatalf("a refused answer must not carry citations: %v", validation.Citations)
	}
}

func TestAnswerValidationBuildsProvenanceFromTheRetrievalOnly(t *testing.T) {
	retrieved := []ChunkView{
		sampleChunk("chunk-1", "doc-1", 3, 2, "第一段正文"),
		sampleChunk("chunk-2", "doc-2", 1, 0, "第二段正文"),
	}

	validation := validateAnswerDraft(
		AnswerDraft{
			Text: "答案",
			// A duplicate citation is collapsed; order follows the retrieval, not
			// the draft, so a generator cannot reorder the provenance it gets.
			CitedChunkIDs: []string{"chunk-2", "chunk-1", "chunk-2"},
		},
		retrieved,
	)
	if validation.Outcome != OutcomeAnswered {
		t.Fatalf("outcome = %q, want answered", validation.Outcome)
	}
	if len(validation.Citations) != 2 {
		t.Fatalf("citations = %v, want two distinct", validation.Citations)
	}
	first := validation.Citations[0]
	if first.ChunkID != "chunk-2" || first.DocumentID != "doc-2" ||
		first.DocumentRevision != 1 || first.ChunkNo != 0 {
		t.Fatalf("first citation provenance = %+v", first)
	}
	if first.Span != retrieved[1].Span || first.Text != "第二段正文" {
		t.Fatalf("citation must project the retrieved span/text, got %+v", first)
	}
	if string(first.Citation) != string(retrieved[1].Citation) {
		t.Fatalf("the stored citation document must be carried through, got %s", first.Citation)
	}
}

func TestExtractiveStubUncitedFaultProducesAnAnswerWithoutCitations(t *testing.T) {
	stub := &ExtractiveStub{Faults: AnswerFaults{Uncited: true}}
	draft, err := stub.Generate(context.Background(), "问题",
		[]ChunkView{sampleChunk("chunk-1", "doc-1", 1, 0, "正文")})
	if err != nil {
		t.Fatalf("Generate: %v", err)
	}
	if draft.Text == "" {
		t.Fatal("the uncited fault must still produce answer text so the refusal is about the citation")
	}
	if len(draft.CitedChunkIDs) != 0 {
		t.Fatalf("cited chunks = %v, want none", draft.CitedChunkIDs)
	}
}

func TestExtractiveStubForeignChunkFaultCitesAChunkOutsideTheRetrieval(t *testing.T) {
	stub := &ExtractiveStub{Faults: AnswerFaults{ForeignChunk: true}}
	draft, err := stub.Generate(context.Background(), "问题",
		[]ChunkView{sampleChunk("chunk-1", "doc-1", 1, 0, "正文")})
	if err != nil {
		t.Fatalf("Generate: %v", err)
	}
	if len(draft.CitedChunkIDs) != 2 {
		t.Fatalf("the fault must keep the real citation and add the fabricated one: %v", draft.CitedChunkIDs)
	}
	if draft.CitedChunkIDs[0] != AnswerFaultForeignChunkID {
		t.Fatalf("fabricated citation = %q", draft.CitedChunkIDs[0])
	}

	validation := validateAnswerDraft(draft, []ChunkView{sampleChunk("chunk-1", "doc-1", 1, 0, "正文")})
	if validation.Outcome != OutcomeCitationNotFound {
		t.Fatalf("outcome = %q, want %q", validation.Outcome, OutcomeCitationNotFound)
	}
	if validation.OffendingChunkID != AnswerFaultForeignChunkID {
		t.Fatalf("offending chunk = %q", validation.OffendingChunkID)
	}
}

func TestExtractiveStubSlowFaultHonoursCancellation(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()

	_, err := (&ExtractiveStub{Faults: AnswerFaults{Slow: time.Minute}}).Generate(
		ctx, "问题", []ChunkView{sampleChunk("chunk-1", "doc-1", 1, 0, "正文")},
	)
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("err = %v, want context.Canceled", err)
	}
}
