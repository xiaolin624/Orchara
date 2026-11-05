package presetexec

// F04B/T088: the retrieve node and its internal retrieval protocol.
//
// A retrieve node does not read knowledge itself. The sealed config names stable
// knowledge-base **ids** and a top_k; the query is an ordinary node input binding
// resolved from this run's values. At execution time the executor calls the new
// non-contract internal route (POST /internal/v1/runs/{run_id}/retrieval) through
// the Client seam. The server re-proves the live attempt token, derives the run
// owner from the database (never from the request), and runs the platform
// knowledge service's ACL-filtered Query once per knowledge base: a base the owner
// cannot see is silently excluded, exactly like a missing one.
//
// The node output is the documented non-contract object:
//
//	{"items":[{"chunk_id","document_id","document_revision","document_state",
//	           "chunk_no","span","excerpt","citation"}],
//	 "citations":[{"chunk_id","document_id","document_revision","span","excerpt"}],
//	 "count":<int>}
//
// The output deliberately does not carry the full chunk body or the vector score
// (N-R10/R3): `excerpt` is the first 500 characters of the chunk and a caller that
// needs the body opens the owner-only citation route. The internal worker route
// still returns text and score so the server can rank and the worker can merge
// bases; those fields never become node data.
//
// The citation JSON and the span are passed through from the stored
// knowledge_chunks.citation document: this build never invents a citation, a
// revision or a span, and it never exposes embeddings, object keys or signed URLs.
// No results are normal (an empty object output, a succeeded node); only a refused
// or unreachable retrieval call fails with `retrieve_denied` /
// `retrieve_unavailable`.

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"strings"

	"orchara/platform/internal/uuid"
)

// Retrieval failure codes. `retrieve_denied` is reserved for a server-side
// refusal of the call itself (an authorization/role error), never for "no readable
// knowledge base": that case is the contract's empty result. `retrieve_unavailable`
// is the documented 503/network failure.
const (
	// CodeRetrieveDenied means the internal retrieval call was refused (403).
	CodeRetrieveDenied = "retrieve_denied"
	// CodeRetrieveUnavailable means the retrieval service answered 503 or the
	// call could not be completed (network, malformed response, stale attempt).
	CodeRetrieveUnavailable = "retrieve_unavailable"
	// maxRetrieveTopK mirrors workflow.schema.json's retrieve top_k ceiling.
	maxRetrieveTopK = 20
	// maxRetrieveExcerptRunes bounds the excerpt the node output exposes. The full
	// chunk body stays in the sealed source and is only readable through the
	// owner-only citation route (N-R10/R3).
	maxRetrieveExcerptRunes = 500
)

// retrieveConfig is the validated sealed config of one retrieve node. The query
// is deliberately absent: it is a node input, never configuration.
type retrieveConfig struct {
	KnowledgeBaseIDs []string
	TopK             int
}

// parseRetrieveConfig re-proves the contract's retrieve config against the sealed
// manifest. The publish validator already checked it, but a sealed snapshot could
// be stale or hand-edited, so the executor refuses a shape it does not speak:
// `knowledge_base_ids` is a non-empty, de-duplicated list of UUIDs, `top_k` is
// 1..20, and no other key (in particular no inline query) is accepted.
func parseRetrieveConfig(config json.RawMessage) (*retrieveConfig, error) {
	trimmed := strings.TrimSpace(string(config))
	if trimmed == "" || trimmed == "null" {
		return nil, errors.New("the retrieve node has no config")
	}
	decoder := json.NewDecoder(strings.NewReader(trimmed))
	decoder.DisallowUnknownFields()
	var raw struct {
		KnowledgeBaseIDs []string `json:"knowledge_base_ids"`
		TopK             *int     `json:"top_k"`
	}
	if err := decoder.Decode(&raw); err != nil {
		return nil, fmt.Errorf("the retrieve config is not the documented object: %w", err)
	}
	var trailing any
	if err := decoder.Decode(&trailing); !errors.Is(err, io.EOF) {
		return nil, errors.New("the retrieve config has trailing data")
	}
	if len(raw.KnowledgeBaseIDs) == 0 {
		return nil, errors.New("knowledge_base_ids must contain at least one knowledge base id")
	}
	seen := make(map[string]bool, len(raw.KnowledgeBaseIDs))
	ids := make([]string, 0, len(raw.KnowledgeBaseIDs))
	for position, rawID := range raw.KnowledgeBaseIDs {
		id := strings.ToLower(strings.TrimSpace(rawID))
		if !uuid.Valid(id) {
			return nil, fmt.Errorf("knowledge_base_ids[%d] is not a UUID", position)
		}
		if seen[id] {
			return nil, fmt.Errorf("knowledge_base_ids repeats the knowledge base %q", id)
		}
		seen[id] = true
		ids = append(ids, id)
	}
	if raw.TopK == nil || *raw.TopK < 1 || *raw.TopK > maxRetrieveTopK {
		return nil, fmt.Errorf("top_k must be an integer between 1 and %d", maxRetrieveTopK)
	}
	return &retrieveConfig{KnowledgeBaseIDs: ids, TopK: *raw.TopK}, nil
}

// RetrieveRequest is one internal retrieval call (non-contract). It carries the
// live attempt token separately; the executor never sends an owner id.
type RetrieveRequest struct {
	KnowledgeBaseIDs []string
	Query            string
	TopK             int
}

// RetrievedItem is one platform retrieval hit as the internal route returns it.
// Span and Citation stay raw so the stored citation document is forwarded
// unchanged; every other field is a documented platform field.
type RetrievedItem struct {
	ChunkID          string          `json:"chunk_id"`
	DocumentID       string          `json:"document_id"`
	DocumentRevision int64           `json:"document_revision"`
	DocumentState    string          `json:"document_state"`
	ChunkNo          int             `json:"chunk_no"`
	Span             json.RawMessage `json:"span"`
	Text             string          `json:"text"`
	Score            float64         `json:"score"`
	Citation         json.RawMessage `json:"citation"`
}

// RetrieveResult is the internal route's answer: the merged, top_k-limited items
// plus the ACL counters. `requested` is how many knowledge bases were named and
// `authorized` how many this run's owner could actually see; the unauthorized ids
// themselves are never echoed.
type RetrieveResult struct {
	Items      []RetrievedItem `json:"items"`
	Requested  int             `json:"requested"`
	Authorized int             `json:"authorized"`
}

// executeGraphRetrieve runs one activated retrieve node. It is a pure read on
// T085's concurrent scheduler: it resolves bindings from the launch snapshot,
// records its failure on the attempt instead of committing a result, and lets the
// scheduler stop and commit after the other branches stopped.
func (r *runner) executeGraphRetrieve(
	ctx context.Context, node *graphNode, view *graphNodeView,
) (executionOutcome, map[string]any, bool) {
	id := node.node.ID
	r.emitNode(ctx, id, "pending")
	r.emitNode(ctx, id, "running")
	if node.retrieve == nil {
		return r.failGraph(ctx, id, CodeBadConfig,
			"the retrieve node has no validated config"), nil, true
	}
	bindings, err := resolveNodeBindings(node.node, view.available)
	if err != nil {
		return r.failGraph(ctx, id, CodeNodeInputInvalid, err.Error()), nil, true
	}
	query, ok := bindings["query"].(string)
	if !ok {
		return r.failGraph(ctx, id, CodeNodeInputInvalid, fmt.Sprintf(
			"/nodes/%d/inputs/query: the retrieve node must bind a string query", node.index)), nil, true
	}
	query = strings.TrimSpace(query)
	if query == "" {
		return r.failGraph(ctx, id, CodeNodeInputInvalid, fmt.Sprintf(
			"/nodes/%d/inputs/query: the retrieve node query must not be empty", node.index)), nil, true
	}
	result, err := r.deps.Client.Retrieve(
		ctx, r.lease.RunID, r.lease.AttemptToken, RetrieveRequest{
			KnowledgeBaseIDs: node.retrieve.KnowledgeBaseIDs,
			Query:            query,
			TopK:             node.retrieve.TopK,
		})
	if err != nil {
		code := CodeRetrieveUnavailable
		if retrieveDenied(err) {
			code = CodeRetrieveDenied
		}
		return r.failGraph(ctx, id, code,
			"the knowledge retrieval call failed: "+err.Error()), nil, true
	}
	if result == nil {
		result = &RetrieveResult{}
	}
	output, err := retrieveOutputDocument(result.Items)
	if err != nil {
		return r.failGraph(ctx, id, CodeNodeOutputInvalid,
			"the retrieval output cannot be encoded: "+err.Error()), nil, true
	}
	if err := validateSchema(node.node.OutputSchema, output, "#"); err != nil {
		return r.failGraph(ctx, id, CodeNodeOutputInvalid,
			"the retrieval output does not match output_schema: "+err.Error()), nil, true
	}
	return r.publishNodeData(ctx, node, output, "results", "retrieval output", "retrieval result")
}

// retrieveOutputDocument projects the internal items onto the documented node
// output. The citation and span bytes are forwarded unchanged; the citations
// array is the lightweight projection the canvas and later steps read. Neither
// the full chunk body nor the vector score leaves the platform boundary: `excerpt`
// is a bounded prefix and the owner-only citation route serves the real text.
func retrieveOutputDocument(items []RetrievedItem) (map[string]any, error) {
	outputItems := make([]any, 0, len(items))
	citations := make([]any, 0, len(items))
	for _, item := range items {
		span := json.RawMessage(item.Span)
		citation := json.RawMessage(item.Citation)
		excerpt := retrieveExcerpt(item.Text)
		outputItems = append(outputItems, map[string]any{
			"chunk_id":          item.ChunkID,
			"document_id":       item.DocumentID,
			"document_revision": item.DocumentRevision,
			"document_state":    item.DocumentState,
			"chunk_no":          item.ChunkNo,
			"span":              span,
			"excerpt":           excerpt,
			"citation":          citation,
		})
		citations = append(citations, map[string]any{
			"chunk_id":          item.ChunkID,
			"document_id":       item.DocumentID,
			"document_revision": item.DocumentRevision,
			"span":              span,
			"excerpt":           excerpt,
		})
	}
	payload, err := json.Marshal(map[string]any{
		"items": outputItems, "citations": citations, "count": len(items),
	})
	if err != nil {
		return nil, err
	}
	value, err := decodeJSONDocument(payload)
	if err != nil {
		return nil, err
	}
	object, ok := value.(map[string]any)
	if !ok {
		return nil, errors.New("the retrieval output is not a JSON object")
	}
	return object, nil
}

// retrieveExcerpt returns at most maxRetrieveExcerptRunes characters. It counts
// runes so a multi-byte chunk body is never cut in the middle of a character.
func retrieveExcerpt(text string) string {
	runes := []rune(text)
	if len(runes) <= maxRetrieveExcerptRunes {
		return text
	}
	return string(runes[:maxRetrieveExcerptRunes])
}

// retrieveDenied distinguishes a server-side refusal of the call (403) from an
// unavailable service. It reads the stable internal error code the supervisor
// client exposes; any other failure is `retrieve_unavailable`.
func retrieveDenied(err error) bool {
	var coded interface{ ErrorCode() string }
	if errors.As(err, &coded) {
		return coded.ErrorCode() == "forbidden"
	}
	return false
}
