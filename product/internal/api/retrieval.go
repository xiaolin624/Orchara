package api

// F04B/T088: the internal retrieval route (documented non-contract surface).
//
//	POST /internal/v1/runs/{run_id}/retrieval   retrieve (mTLS: worker)
//
// The supervisor executing a retrieve node cannot read knowledge itself: the
// platform ACL must be applied server-side, with the run owner derived from the
// database. This route is that boundary. It mirrors the run-scoped worker artifact
// routes:
//
//   - the caller presents a worker mTLS certificate and repeats the capacity in
//     X-Internal-Role (RequireInternalMTLS);
//   - the body's attempt_token is re-proved against the run in the path by
//     RunOwner (the same live-lease statement the artifact routes use), and the
//     run owner comes from the runs row — never from the body;
//   - the server calls knowledge.Service.QueryBases, which runs the existing
//     ACL-filtered Query per knowledge base; a foreign/missing/deleted base is
//     silently excluded and only counted (`requested`/`authorized`);
//   - the answer carries platform fields only: no embedding, object key, signed
//     URL, DSN or vector score beyond the documented `score`.
//
// Statuses: 400 invalid_json (malformed body), 409 stale_attempt (the token is not
// the live attempt), 422 validation_failed (body shape), 503 when the knowledge
// service has no embedding provider or object store, 500 otherwise. An empty
// merged result is 200 with `items:[]` — never an error.
//
// Registration lives in internal.go's RegisterInternal; the route is not part of
// contracts/openapi.json.

import (
	"errors"
	"fmt"
	"net/http"
	"strings"

	"orchara/platform/internal/knowledge"
)

// internalRetrievalRequest is the exact body this non-contract route accepts.
type internalRetrievalRequest struct {
	AttemptToken     string   `json:"attempt_token"`
	KnowledgeBaseIDs []string `json:"knowledge_base_ids"`
	Query            string   `json:"query"`
	TopK             int      `json:"top_k"`
}

// internalRetrievalResponse is the platform-only answer: the ranked chunks plus
// the ACL counters. The unauthorized knowledge-base ids are never echoed.
type internalRetrievalResponse struct {
	Items      []knowledgeChunkView `json:"items"`
	Requested  int                  `json:"requested"`
	Authorized int                  `json:"authorized"`
}

func (s *Server) handleInternalRunRetrieval(w http.ResponseWriter, r *http.Request) {
	if !requireInternalRole(w, r, InternalRoleWorker) {
		return
	}
	var request internalRetrievalRequest
	if err := decodeJSON(w, r, &request); err != nil {
		writeError(w, r, http.StatusBadRequest, "invalid_json", "request body is not valid JSON")
		return
	}
	if s.runArtifacts == nil || s.knowledge == nil {
		writeError(w, r, http.StatusServiceUnavailable, "provider_not_configured",
			"the knowledge retrieval service is not configured on this server")
		return
	}
	// The run owner is the database identity of the live attempt, not a request
	// field. RunOwner uses the exact same live-lease statement as the artifact
	// routes, so a superseded attempt cannot retrieve as its run.
	ownerID, err := s.runArtifacts.RunOwner(
		r.Context(), r.PathValue("run_id"), request.AttemptToken,
	)
	if !s.writeRunArtifactError(w, r, err) {
		return
	}
	if field, message := validateRetrievalRequest(request); field != "" {
		writeErrorWithDetails(w, r, http.StatusUnprocessableEntity, "validation_failed",
			message, map[string]any{"field": field})
		return
	}
	result, err := s.knowledge.QueryBases(
		r.Context(), ownerID, request.KnowledgeBaseIDs, request.Query, request.TopK,
	)
	if err != nil {
		s.writeRetrievalFailure(w, r, err)
		return
	}
	items := make([]knowledgeChunkView, 0, len(result.Items))
	for _, item := range result.Items {
		items = append(items, chunkViewOf(item, true))
	}
	writeJSON(w, http.StatusOK, internalRetrievalResponse{
		Items: items, Requested: result.Requested, Authorized: result.Authorized,
	})
}

// maxRetrieveTopK mirrors the contract's retrieve top_k ceiling. It is duplicated
// here (rather than imported from presetexec) because the API process must not
// depend on the executor package.
const maxRetrieveTopK = 20

// validateRetrievalRequest proves the body shape before any knowledge service
// work. It returns the failing field and a message; an empty field means valid.
// The limits are this route's resource bounds; the contract's node config
// (knowledge_base_ids min 1 unique, top_k 1..20) is the same shape.
func validateRetrievalRequest(request internalRetrievalRequest) (string, string) {
	if len(request.KnowledgeBaseIDs) == 0 {
		return "knowledge_base_ids", "knowledge_base_ids must contain at least one knowledge base"
	}
	if len(request.KnowledgeBaseIDs) > knowledge.MaxQueryBases {
		return "knowledge_base_ids", fmt.Sprintf(
			"knowledge_base_ids must contain at most %d knowledge bases", knowledge.MaxQueryBases)
	}
	seen := make(map[string]bool, len(request.KnowledgeBaseIDs))
	for position, rawID := range request.KnowledgeBaseIDs {
		id := strings.ToLower(strings.TrimSpace(rawID))
		if id == "" {
			return "knowledge_base_ids", fmt.Sprintf("knowledge_base_ids[%d] is required", position)
		}
		if seen[id] {
			return "knowledge_base_ids", "knowledge_base_ids repeats a knowledge base id"
		}
		seen[id] = true
	}
	query := strings.TrimSpace(request.Query)
	if query == "" {
		return "query", "query is required"
	}
	if len([]rune(query)) > 2000 {
		return "query", "query must be at most 2000 characters"
	}
	if request.TopK < 1 || request.TopK > maxRetrieveTopK {
		return "top_k", fmt.Sprintf("top_k must be an integer between 1 and %d", maxRetrieveTopK)
	}
	return "", ""
}

// writeRetrievalFailure maps the knowledge service errors onto the existing
// envelope, reusing writeKnowledgeFailure's vocabulary: a missing base was already
// excluded by QueryBases, so a remaining error is an infrastructure failure (503)
// or a semantic refusal (422).
func (s *Server) writeRetrievalFailure(w http.ResponseWriter, r *http.Request, err error) {
	switch {
	case errors.Is(err, knowledge.ErrEmbeddingUnavailable):
		writeError(w, r, http.StatusServiceUnavailable, "provider_not_configured",
			"no embedding provider is configured on this server")
	case errors.Is(err, knowledge.ErrObjectStoreUnavailable):
		writeError(w, r, http.StatusServiceUnavailable, "object_store_unavailable",
			"the object store is not configured on this server")
	default:
		s.writeKnowledgeFailure(w, r, err)
	}
}
