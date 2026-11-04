package knowledge

// F10/T064: the answer surface over knowledge retrieval.
//
// This deployment has **no model provider** (see internal/embedding's real-path
// boundary and ADR 0010). The generation step is therefore a **labelled
// extractive stub**: it composes the answer text from the chunk texts this
// retrieval returned and cites exactly the chunks it used. The stub is never
// presented as a model answer: the response carries
// `generation.kind=extractive_stub`, `stub=true` and `model_provider=not_configured`.
//
// The real deliverable is the citation contract and its server-side validation,
// which runs *before* any answer is returned and treats the generator's draft as
// untrusted:
//
//  1. every cited chunk id must belong to the chunk-id set of **this** retrieval,
//     otherwise the answer is refused with `citation_not_found` (a fabricated or
//     out-of-scope citation never reaches the caller);
//  2. every cited chunk is re-read under the caller's current ACL and the
//     document's current revision/activation before the answer is returned,
//     otherwise it is refused with `citation_unreadable` (a revoked or superseded
//     source never comes back inside an answer);
//  3. the per-citation provenance (document id, revision, chunk id, rune span and
//     line range, stored citation document) is projected from the server's own
//     retrieval row, never from generator-supplied fields.
//
// An empty retrieval is `no_results`: a normal empty answer, not an error.

import (
	"context"
	"errors"
	"fmt"
	"log"
	"strconv"
	"strings"
	"time"

	"orchara/platform/internal/config"
)

// AnswerFaultEnv selects documented dev-stub answer-generator misbehaviours.
//
// It exists for the same reason as the modelproxy stub fault model ids (ADR 0007
// D3/T045): the refusal paths (a generator that answers without a citation, or
// that invents one) can only be observed end to end if the stub can be told to
// misbehave. It is deployment configuration, never a request value, it is only
// honoured in a stub profile, and it can only cause the server to refuse — it can
// never bypass the validation below. Value: comma-separated `uncited`,
// `foreign-chunk`, `slow=<duration>`.
const AnswerFaultEnv = "ORCHARA_KNOWLEDGE_ANSWER_FAULTS"

const (
	// AnswerGeneratorKind and AnswerGeneratorVersion label the stub in every
	// answer response, so no caller mistakes it for a model's answer.
	AnswerGeneratorKind    = "extractive_stub"
	AnswerGeneratorVersion = "extractive-stub/1"
	// ModelProviderNotConfigured is the honest generation provenance of this
	// build: no provider adapter and no credential exist here.
	ModelProviderNotConfigured = "not_configured"
	// AnswerCiteLimit bounds how many retrieved chunks one answer may cite.
	AnswerCiteLimit = 3
	// AnswerExcerptRunes bounds one quoted chunk inside the composed answer text.
	AnswerExcerptRunes = 600
)

// AnswerFaultForeignChunkID is the fabricated chunk id the `foreign-chunk` fault
// cites. It is a well-formed UUID so only the retrieval-set check can reject it.
const AnswerFaultForeignChunkID = "00000000-0000-4000-8000-0000000000ff"

// AnswerOutcome is the distinct observable state of one answer request.
type AnswerOutcome string

const (
	// OutcomeAnswered means the answer is returned with validated citations.
	OutcomeAnswered AnswerOutcome = "answered"
	// OutcomeNoResults means the retrieval was empty: an empty answer, not an
	// error.
	OutcomeNoResults AnswerOutcome = "no_results"
	// OutcomeAnswerWithoutCitation means the generator answered but cited
	// nothing; citations are required, so the answer is refused.
	OutcomeAnswerWithoutCitation AnswerOutcome = "answer_without_citation"
	// OutcomeCitationNotFound means a cited chunk is not part of this retrieval.
	OutcomeCitationNotFound AnswerOutcome = "citation_not_found"
	// OutcomeCitationUnreadable means a cited chunk is no longer readable under
	// the caller's current ACL or the document's current revision/activation.
	OutcomeCitationUnreadable AnswerOutcome = "citation_unreadable"
)

// AnswerFaults selects the documented extractive-stub misbehaviours.
type AnswerFaults struct {
	// Uncited answers with text but no citation.
	Uncited bool
	// ForeignChunk adds one fabricated citation to the real ones.
	ForeignChunk bool
	// Slow delays the draft after retrieval, which is how the revocation race
	// (a source revoked while the answer is being generated) is exercised.
	Slow time.Duration
}

// AnswerDraft is the untrusted generator output. The server builds the returned
// provenance from its own retrieval rows; the draft only names chunk ids.
type AnswerDraft struct {
	Text          string
	CitedChunkIDs []string
}

// AnswerGenerator composes one answer draft from the chunks this retrieval
// returned. Implementations must never reach another principal's data.
type AnswerGenerator interface {
	// Version is the stable generator revision recorded in the answer.
	Version() string
	// Stub reports whether the output is a stub (never a model answer).
	Stub() bool
	// Generate composes the draft. The chunks are already ACL/revision filtered
	// and ranked by Service.Query.
	Generate(ctx context.Context, question string, chunks []ChunkView) (AnswerDraft, error)
}

// ExtractiveStub is the labelled generation stub of this build. It is
// deterministic: the same chunks produce the same answer text and citations.
type ExtractiveStub struct {
	Faults AnswerFaults
	// Logger, when set, records the documented slow-fault checkpoint so an
	// operator (or the revocation-race test) can observe that retrieval finished
	// and generation is in progress. It never logs the question or chunk text.
	Logger *log.Logger
}

// Version identifies the stub revision recorded on every answer.
func (g *ExtractiveStub) Version() string { return AnswerGeneratorVersion }

// Stub always reports true: this is never a model's answer.
func (g *ExtractiveStub) Stub() bool { return true }

// Generate composes the extractive answer: a bounded number of the highest-ranked
// chunks, quoted verbatim, with exactly those chunks cited.
func (g *ExtractiveStub) Generate(
	ctx context.Context, _ string, chunks []ChunkView,
) (AnswerDraft, error) {
	if len(chunks) == 0 {
		return AnswerDraft{}, nil
	}
	if g.Faults.Slow > 0 {
		if g.Logger != nil {
			g.Logger.Printf(
				"knowledge answer stub: slow fault waiting %s after retrieving %d chunks",
				g.Faults.Slow, len(chunks),
			)
		}
		timer := time.NewTimer(g.Faults.Slow)
		defer timer.Stop()
		select {
		case <-ctx.Done():
			return AnswerDraft{}, ctx.Err()
		case <-timer.C:
		}
	}
	limit := len(chunks)
	if limit > AnswerCiteLimit {
		limit = AnswerCiteLimit
	}
	used := chunks[:limit]

	var text strings.Builder
	text.WriteString("根据本次检索到的 ")
	text.WriteString(strconv.Itoa(len(used)))
	text.WriteString(" 个片段整理（抽取式摘要，非模型生成）：")
	for index, chunk := range used {
		text.WriteString("\n\n[")
		text.WriteString(strconv.Itoa(index + 1))
		text.WriteString("] ")
		text.WriteString(answerExcerpt(chunk.Text))
	}
	cited := make([]string, 0, len(used))
	for _, chunk := range used {
		cited = append(cited, chunk.ChunkID)
	}
	draft := AnswerDraft{Text: text.String(), CitedChunkIDs: cited}
	if g.Faults.Uncited {
		draft.CitedChunkIDs = nil
	}
	if g.Faults.ForeignChunk {
		// Keep the real citations and add one that cannot resolve: the point is
		// that a single fabricated citation refuses the whole answer.
		draft.CitedChunkIDs = append([]string{AnswerFaultForeignChunkID}, draft.CitedChunkIDs...)
	}
	return draft, nil
}

func answerExcerpt(content string) string {
	runes := []rune(strings.TrimSpace(content))
	if len(runes) <= AnswerExcerptRunes {
		return string(runes)
	}
	return string(runes[:AnswerExcerptRunes]) + "…"
}

// AnswererFromEnv builds the deployment's answer generator.
//
// The default is the plain extractive stub. Fault selection is refused outside a
// stub profile (fail closed) and an unknown fault refuses startup; a deployment
// must never silently run a misbehaving generator.
func AnswererFromEnv(
	profile config.Profile, lookup config.Lookup, logger *log.Logger,
) (AnswerGenerator, error) {
	if lookup == nil {
		lookup = config.FromEnv()
	}
	raw, _ := lookup(AnswerFaultEnv)
	faults, err := parseAnswerFaults(raw)
	if err != nil {
		return nil, err
	}
	if faults != (AnswerFaults{}) &&
		profile != config.ProfileDevStub && profile != config.ProfileCapacityStub {
		return nil, fmt.Errorf(
			"%s is only honoured in a stub profile (dev-stub/capacity-stub), not %q",
			AnswerFaultEnv, profile,
		)
	}
	return &ExtractiveStub{Faults: faults, Logger: logger}, nil
}

func parseAnswerFaults(raw string) (AnswerFaults, error) {
	var faults AnswerFaults
	for _, token := range strings.Split(raw, ",") {
		token = strings.TrimSpace(token)
		if token == "" {
			continue
		}
		name, value, _ := strings.Cut(token, "=")
		switch strings.TrimSpace(name) {
		case "uncited":
			faults.Uncited = true
		case "foreign-chunk":
			faults.ForeignChunk = true
		case "slow":
			delay, err := time.ParseDuration(strings.TrimSpace(value))
			if err != nil || delay <= 0 {
				return AnswerFaults{}, fmt.Errorf(
					"%s: slow needs a positive duration such as slow=5s", AnswerFaultEnv)
			}
			faults.Slow = delay
		default:
			return AnswerFaults{}, fmt.Errorf("%s: unknown fault %q", AnswerFaultEnv, token)
		}
	}
	return faults, nil
}

// AnswerCitation is one validated citation: its provenance is projected from the
// retrieval row (and re-read before the answer is returned).
type AnswerCitation struct {
	ChunkID          string
	DocumentID       string
	DocumentRevision int64
	ChunkNo          int
	Span             Span
	Score            float64
	Text             string
	Citation         []byte
}

// Answer is the returned answer and its citations.
type Answer struct {
	Text       string
	Citations  []AnswerCitation
	Generation AnswerGeneration
}

// AnswerGeneration labels how the answer text was produced.
type AnswerGeneration struct {
	Kind          string
	Version       string
	Stub          bool
	ModelProvider string
}

// AnswerRetrieval records the retrieval this answer is grounded in. ChunkIDs is
// the ordered chunk-id set of that retrieval: a citation outside it is refused.
type AnswerRetrieval struct {
	EmbeddingModelRevision string
	TopK                   int
	ACLEpoch               int64
	ChunkIDs               []string
}

// AnswerResult is one answer request's outcome.
type AnswerResult struct {
	Outcome AnswerOutcome
	Answer  Answer
	// Retrieval is always populated (even for no_results and refusals) so the
	// caller can see what this request actually retrieved.
	Retrieval AnswerRetrieval
	// OffendingChunkID names the citation that caused a refusal.
	OffendingChunkID string
}

// AnswerValidation is the pure result of checking one draft against one
// retrieval. It never touches the database; Service.Answer then re-reads every
// surviving citation.
type AnswerValidation struct {
	Outcome          AnswerOutcome
	Citations        []AnswerCitation
	OffendingChunkID string
}

// validateAnswerDraft applies the citation-set contract: citations are required,
// every cited id must be in this retrieval, duplicates collapse, and the
// provenance is copied from the retrieval row rather than the draft.
func validateAnswerDraft(draft AnswerDraft, retrieved []ChunkView) AnswerValidation {
	if len(draft.CitedChunkIDs) == 0 {
		return AnswerValidation{Outcome: OutcomeAnswerWithoutCitation}
	}
	byID := make(map[string]ChunkView, len(retrieved))
	for _, chunk := range retrieved {
		byID[chunk.ChunkID] = chunk
	}
	citations := make([]AnswerCitation, 0, len(draft.CitedChunkIDs))
	seen := make(map[string]bool, len(draft.CitedChunkIDs))
	for _, chunkID := range draft.CitedChunkIDs {
		if seen[chunkID] {
			continue
		}
		seen[chunkID] = true
		chunk, ok := byID[chunkID]
		if !ok {
			return AnswerValidation{
				Outcome: OutcomeCitationNotFound, OffendingChunkID: chunkID,
			}
		}
		citations = append(citations, AnswerCitation{
			ChunkID:          chunk.ChunkID,
			DocumentID:       chunk.DocumentID,
			DocumentRevision: chunk.DocumentRevision,
			ChunkNo:          chunk.ChunkNo,
			Span:             chunk.Span,
			Score:            chunk.Score,
			Text:             chunk.Text,
			Citation:         []byte(chunk.Citation),
		})
	}
	if len(citations) == 0 {
		return AnswerValidation{Outcome: OutcomeAnswerWithoutCitation}
	}
	return AnswerValidation{Outcome: OutcomeAnswered, Citations: citations}
}

// Answer runs one grounded question against one owner's knowledge base.
//
// It is retrieval -> untrusted generation -> citation validation -> read-time
// re-check, all inside the request. The answer is only returned when every
// citation came from this retrieval and is readable right now.
func (s *Service) Answer(
	ctx context.Context, ownerID, baseID, query string, topK int,
) (AnswerResult, error) {
	retrieval, err := s.Query(ctx, ownerID, baseID, query, topK)
	if err != nil {
		return AnswerResult{}, err
	}
	result := AnswerResult{
		Outcome: OutcomeNoResults,
		Answer: Answer{
			Citations:  []AnswerCitation{},
			Generation: s.answerGeneration(),
		},
		Retrieval: AnswerRetrieval{
			EmbeddingModelRevision: retrieval.EmbeddingModelRevision,
			TopK:                   retrieval.TopK,
			ACLEpoch:               retrieval.ACLEpoch,
			ChunkIDs:               chunkIDsOf(retrieval.Items),
		},
	}
	if len(retrieval.Items) == 0 {
		return result, nil
	}
	if s.Answerer == nil {
		return AnswerResult{}, ErrAnswerGeneratorUnavailable
	}
	draft, err := s.Answerer.Generate(ctx, strings.TrimSpace(query), retrieval.Items)
	if err != nil {
		return AnswerResult{}, fmt.Errorf("generating the answer: %w", err)
	}
	validation := validateAnswerDraft(draft, retrieval.Items)
	switch validation.Outcome {
	case OutcomeAnswerWithoutCitation, OutcomeCitationNotFound:
		result.Outcome = validation.Outcome
		result.OffendingChunkID = validation.OffendingChunkID
		return result, nil
	}
	// Read-time re-check: every cited chunk must still be readable under the
	// caller's current ACL and the document's current revision/activation. The
	// citation endpoint applies exactly those predicates, so an answer can never
	// carry a source the caller could not open at the moment the answer returns.
	for index := range validation.Citations {
		citation := &validation.Citations[index]
		current, err := s.GetChunk(ctx, ownerID, citation.ChunkID)
		if errors.Is(err, ErrNotFound) {
			result.Outcome = OutcomeCitationUnreadable
			result.OffendingChunkID = citation.ChunkID
			return result, nil
		}
		if err != nil {
			return AnswerResult{}, err
		}
		if current.DocumentID != citation.DocumentID ||
			current.DocumentRevision != citation.DocumentRevision ||
			current.ChunkNo != citation.ChunkNo ||
			current.Text != citation.Text {
			result.Outcome = OutcomeCitationUnreadable
			result.OffendingChunkID = citation.ChunkID
			return result, nil
		}
	}
	result.Outcome = OutcomeAnswered
	result.Answer.Text = draft.Text
	result.Answer.Citations = validation.Citations
	return result, nil
}

func chunkIDsOf(chunks []ChunkView) []string {
	ids := make([]string, 0, len(chunks))
	for _, chunk := range chunks {
		ids = append(ids, chunk.ChunkID)
	}
	return ids
}

func (s *Service) answerGeneration() AnswerGeneration {
	if s.Answerer == nil {
		return AnswerGeneration{
			Kind:          AnswerGeneratorKind,
			Version:       AnswerGeneratorVersion,
			Stub:          true,
			ModelProvider: ModelProviderNotConfigured,
		}
	}
	return AnswerGeneration{
		Kind:          AnswerGeneratorKind,
		Version:       s.Answerer.Version(),
		Stub:          s.Answerer.Stub(),
		ModelProvider: ModelProviderNotConfigured,
	}
}
