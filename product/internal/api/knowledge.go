package api

// F10/T055: the knowledge-base, document and retrieval surface.
//
// Contract operations (contracts/openapi.json):
//
//	POST /api/v1/knowledge-bases                              -> 201 Accepted
//	GET  /api/v1/knowledge-bases                              -> 200 {items,next_cursor}
//	GET  /api/v1/knowledge-bases/{knowledge_base_id}          -> 200 KnowledgeBase
//	POST /api/v1/knowledge-bases/{id}/documents               -> 202 Accepted
//	GET  /api/v1/knowledge-bases/{id}/documents               -> 200 {items,next_cursor}
//	GET  /api/v1/knowledge-documents/{document_id}            -> 200 KnowledgeDocument
//
// Documented non-contract surface (ADR 0010, precedent ADR 0005):
//
//	POST /api/v1/knowledge-bases/{id}/retrieval-queries        -> 200 ranked chunks
//	GET  /api/v1/knowledge-citations/{chunk_id}               -> 200 one chunk + citation
//	GET  /api/v1/knowledge-documents/{id}/diagnostics         -> 200 ingestion facts
//	POST /api/v1/knowledge-bases/{id}/answer                  -> 200 grounded answer
//	                                                             (ADR 0012, T064)
//
// The answer surface is the T064 grounded-citation contract: the answer's
// citations are validated against this retrieval and re-read under the caller's
// current ACL and the document's current revision before the answer is returned.
// A refusal is one of three distinct codes (answer_without_citation,
// citation_not_found, citation_unreadable); an empty retrieval is a normal empty
// answer (`no_results`), not an error. Generation is a labelled extractive stub
// (no model provider exists in this deployment).
//
// ACL: owner-only for every operation, administrator included. A foreign or
// missing resource is one 404 `resource_not_found`; existence is never leaked as
// 403. API keys use the existing application:read / application:write scopes
// (the interim mapping every non-admin surface uses); cookie sessions keep CSRF.
//
// Failure semantics: only the completion guard marks a document ready or failed.
// The HTTP layer never reports ready from a job creation, and a failed document
// exposes a safe code through the diagnostics read, not a stack trace or bytes.

import (
	"encoding/json"
	"errors"
	"net/http"

	"orchara/platform/internal/catalog"
	"orchara/platform/internal/identity"
	"orchara/platform/internal/knowledge"
)

type knowledgeBaseView struct {
	ID       string `json:"id"`
	Name     string `json:"name"`
	Revision int64  `json:"revision"`
}

type acceptedView struct {
	ID    string `json:"id"`
	State string `json:"state"`
}

type knowledgeDocumentView struct {
	ID               string `json:"id"`
	KnowledgeBaseID  string `json:"knowledge_base_id"`
	State            string `json:"state"`
	Revision         int64  `json:"revision"`
	SourceArtifactID string `json:"source_artifact_id"`
}

type knowledgeBaseListResponse struct {
	Items      []knowledgeBaseView `json:"items"`
	NextCursor *string             `json:"next_cursor"`
}

type knowledgeDocumentListResponse struct {
	Items      []knowledgeDocumentView `json:"items"`
	NextCursor *string                 `json:"next_cursor"`
}

type knowledgeChunkView struct {
	ChunkID          string          `json:"chunk_id"`
	DocumentID       string          `json:"document_id"`
	DocumentRevision int64           `json:"document_revision"`
	DocumentState    string          `json:"document_state"`
	ChunkNo          int             `json:"chunk_no"`
	Score            float64         `json:"score"`
	Text             string          `json:"text"`
	Span             knowledge.Span  `json:"span"`
	Citation         json.RawMessage `json:"citation"`
}

type knowledgeCitationView struct {
	ChunkID          string          `json:"chunk_id"`
	DocumentID       string          `json:"document_id"`
	DocumentRevision int64           `json:"document_revision"`
	DocumentState    string          `json:"document_state"`
	ChunkNo          int             `json:"chunk_no"`
	Text             string          `json:"text"`
	Span             knowledge.Span  `json:"span"`
	Citation         json.RawMessage `json:"citation"`
}

type retrievalMetadata struct {
	EmbeddingModelRevision string `json:"embedding_model_revision"`
	TopK                   int    `json:"top_k"`
	ACLEpoch               int64  `json:"acl_epoch"`
}

type retrievalResponse struct {
	Items      []knowledgeChunkView `json:"items"`
	NextCursor *string              `json:"next_cursor"`
	Retrieval  retrievalMetadata    `json:"retrieval"`
}

// ---------------------------------------------------------------------------
// F10/T064: the grounded answer surface
// ---------------------------------------------------------------------------

type answerGenerationView struct {
	Kind          string `json:"kind"`
	Version       string `json:"version"`
	Stub          bool   `json:"stub"`
	ModelProvider string `json:"model_provider"`
}

type answerCitationView struct {
	ChunkID          string          `json:"chunk_id"`
	DocumentID       string          `json:"document_id"`
	DocumentRevision int64           `json:"document_revision"`
	ChunkNo          int             `json:"chunk_no"`
	Span             knowledge.Span  `json:"span"`
	Score            float64         `json:"score"`
	Text             string          `json:"text"`
	Citation         json.RawMessage `json:"citation"`
}

type answerView struct {
	Text       string               `json:"text"`
	Generation answerGenerationView `json:"generation"`
	Citations  []answerCitationView `json:"citations"`
}

type answerRetrievalView struct {
	EmbeddingModelRevision string   `json:"embedding_model_revision"`
	TopK                   int      `json:"top_k"`
	ACLEpoch               int64    `json:"acl_epoch"`
	ChunkIDs               []string `json:"chunk_ids"`
}

type answerResponse struct {
	Outcome   string              `json:"outcome"`
	Answer    answerView          `json:"answer"`
	Retrieval answerRetrievalView `json:"retrieval"`
}

func answerResponseOf(result knowledge.AnswerResult) answerResponse {
	citations := make([]answerCitationView, 0, len(result.Answer.Citations))
	for _, citation := range result.Answer.Citations {
		citations = append(citations, answerCitationView{
			ChunkID:          citation.ChunkID,
			DocumentID:       citation.DocumentID,
			DocumentRevision: citation.DocumentRevision,
			ChunkNo:          citation.ChunkNo,
			Span:             citation.Span,
			Score:            citation.Score,
			Text:             citation.Text,
			Citation:         json.RawMessage(citation.Citation),
		})
	}
	return answerResponse{
		Outcome: string(result.Outcome),
		Answer: answerView{
			Text: result.Answer.Text,
			Generation: answerGenerationView{
				Kind:          result.Answer.Generation.Kind,
				Version:       result.Answer.Generation.Version,
				Stub:          result.Answer.Generation.Stub,
				ModelProvider: result.Answer.Generation.ModelProvider,
			},
			Citations: citations,
		},
		Retrieval: answerRetrievalView{
			EmbeddingModelRevision: result.Retrieval.EmbeddingModelRevision,
			TopK:                   result.Retrieval.TopK,
			ACLEpoch:               result.Retrieval.ACLEpoch,
			ChunkIDs:               result.Retrieval.ChunkIDs,
		},
	}
}

type diagnosticsView struct {
	DocumentID             string                    `json:"document_id"`
	State                  string                    `json:"state"`
	Revision               int64                     `json:"revision"`
	ActiveRevision         *int64                    `json:"active_revision"`
	ParserVersion          *string                   `json:"parser_version"`
	ChunkerVersion         *string                   `json:"chunker_version"`
	EmbeddingModelRevision *string                   `json:"embedding_model_revision"`
	ErrorCode              *string                   `json:"error_code"`
	Detail                 *diagnosticsDetail        `json:"detail"`
	ChunkCount             int                       `json:"chunk_count"`
	BackgroundJob          *diagnosticsBackgroundJob `json:"background_job"`
}

// diagnosticsDetail is the re-derived, safe location of a content refusal. It
// is null whenever this build cannot re-derive it (a non-parse failure, a
// tombstoned source, or a parser revision that is no longer registered).
type diagnosticsDetail struct {
	Sheet  string `json:"sheet,omitempty"`
	Row    int    `json:"row,omitempty"`
	Column string `json:"column,omitempty"`
	Cell   string `json:"cell,omitempty"`
	Part   string `json:"part,omitempty"`
	Limit  string `json:"limit,omitempty"`
	Value  int    `json:"value,omitempty"`
}

type diagnosticsBackgroundJob struct {
	ID           string  `json:"id"`
	State        string  `json:"state"`
	ErrorCode    *string `json:"error_code"`
	AttemptCount int     `json:"attempt_count"`
}

func baseViewOf(base knowledge.Base) knowledgeBaseView {
	return knowledgeBaseView{ID: base.ID, Name: base.Name, Revision: base.Revision}
}

func documentViewOf(document knowledge.Document) knowledgeDocumentView {
	return knowledgeDocumentView{
		ID:               document.ID,
		KnowledgeBaseID:  document.KnowledgeBaseID,
		State:            document.State,
		Revision:         document.Revision,
		SourceArtifactID: document.SourceArtifactID,
	}
}

func chunkViewOf(chunk knowledge.ChunkView, withScore bool) knowledgeChunkView {
	view := knowledgeChunkView{
		ChunkID:          chunk.ChunkID,
		DocumentID:       chunk.DocumentID,
		DocumentRevision: chunk.DocumentRevision,
		DocumentState:    chunk.DocumentState,
		ChunkNo:          chunk.ChunkNo,
		Text:             chunk.Text,
		Span:             chunk.Span,
		Citation:         chunk.Citation,
	}
	if withScore {
		view.Score = chunk.Score
	}
	return view
}

// writeKnowledgeFailure maps the service's typed errors onto the existing error
// envelope vocabulary. It always writes, so callers return immediately.
func (s *Server) writeKnowledgeFailure(w http.ResponseWriter, r *http.Request, err error) {
	var validation *knowledge.ValidationError
	switch {
	case errors.Is(err, knowledge.ErrNotFound), errors.Is(err, knowledge.ErrArtifactNotVisible):
		// One answer for missing, foreign, deleted and administrator-without-
		// ownership. The caller cannot tell which, so the surface cannot enumerate.
		writeError(w, r, http.StatusNotFound, "resource_not_found",
			"that knowledge resource does not exist")
	case errors.Is(err, knowledge.ErrArtifactNotReady):
		writeError(w, r, http.StatusConflict, "precondition_failed",
			"the source artifact is not ready; wait for its upload to finish")
	case errors.Is(err, knowledge.ErrUnsupportedInput):
		writeErrorWithDetails(w, r, http.StatusUnprocessableEntity, "validation_failed",
			"this source format is not supported yet; this slice ingests text/plain, "+
				"text/markdown, text/csv, XLSX and PDF",
			map[string]any{"field": "source_artifact_id"})
	case errors.As(err, &validation):
		writeErrorWithDetails(w, r, http.StatusUnprocessableEntity, "validation_failed",
			validation.Error(), map[string]any{"field": validation.Field})
	case errors.Is(err, knowledge.ErrEmbeddingUnavailable):
		writeError(w, r, http.StatusServiceUnavailable, "provider_not_configured",
			"no embedding provider is configured on this server")
	case errors.Is(err, knowledge.ErrObjectStoreUnavailable):
		writeError(w, r, http.StatusServiceUnavailable, "object_store_unavailable",
			"the object store is not configured on this server")
	case errors.Is(err, knowledge.ErrAnswerGeneratorUnavailable):
		// No generator means no grounded answer. Refuse instead of inventing one.
		writeError(w, r, http.StatusServiceUnavailable, "provider_not_configured",
			"no answer generator is configured on this server")
	case errors.Is(err, catalog.ErrInvalidCursor):
		writeError(w, r, http.StatusBadRequest, "invalid_cursor",
			"cursor is not a valid continuation token")
	default:
		s.internalError(w, r, err)
	}
}

// knowledgeScope applies the same interim API-key scope mapping as the artifact
// and run surfaces. A session cookie is not scope-limited; scope never replaces
// the owner check.
func (s *Server) knowledgeScope(
	w http.ResponseWriter, r *http.Request, caller *identity.Identity, scope string,
) bool {
	if failure := requireScope(caller, scope); failure != nil {
		writeError(w, r, failure.Status, failure.Code, failure.Message)
		return false
	}
	return true
}

func (s *Server) handleCreateKnowledgeBase(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "")
	if !ok {
		return
	}
	if !s.knowledgeScope(w, r, caller, identity.ScopeApplicationWrite) {
		return
	}
	var request struct {
		Name string `json:"name"`
	}
	if err := decodeJSON(w, r, &request); err != nil {
		writeError(w, r, http.StatusBadRequest, "invalid_json", "request body is not valid JSON")
		return
	}
	base, err := s.knowledge.CreateBase(r.Context(), caller.PrincipalID, request.Name)
	if err != nil {
		s.writeKnowledgeFailure(w, r, err)
		return
	}
	// `knowledge_bases` has no state column and the contract's Accepted.state is
	// an unconstrained string; the created resource is active.
	writeJSON(w, http.StatusCreated, acceptedView{ID: base.ID, State: "active"})
}

func (s *Server) handleListKnowledgeBases(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "")
	if !ok {
		return
	}
	if !s.knowledgeScope(w, r, caller, identity.ScopeApplicationRead) {
		return
	}
	query := r.URL.Query()
	limit, err := catalog.ParseLimit(query.Get("limit"))
	if err != nil {
		writeErrorWithDetails(w, r, http.StatusUnprocessableEntity, "validation_failed",
			"limit must be an integer between 1 and 100", map[string]any{"field": "limit"})
		return
	}
	items, next, err := s.knowledge.ListBases(
		r.Context(), caller.PrincipalID, query.Get("cursor"), limit,
	)
	if err != nil {
		s.writeKnowledgeFailure(w, r, err)
		return
	}
	views := make([]knowledgeBaseView, 0, len(items))
	for _, item := range items {
		views = append(views, baseViewOf(item))
	}
	writeJSON(w, http.StatusOK, knowledgeBaseListResponse{Items: views, NextCursor: next})
}

func (s *Server) handleGetKnowledgeBase(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "")
	if !ok {
		return
	}
	if !s.knowledgeScope(w, r, caller, identity.ScopeApplicationRead) {
		return
	}
	base, err := s.knowledge.GetBase(r.Context(), caller.PrincipalID, r.PathValue("knowledge_base_id"))
	if err != nil {
		s.writeKnowledgeFailure(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, baseViewOf(base))
}

func (s *Server) handleIngestDocument(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "")
	if !ok {
		return
	}
	if !s.knowledgeScope(w, r, caller, identity.ScopeApplicationWrite) {
		return
	}
	var request struct {
		SourceArtifactID string `json:"source_artifact_id"`
	}
	if err := decodeJSON(w, r, &request); err != nil {
		writeError(w, r, http.StatusBadRequest, "invalid_json", "request body is not valid JSON")
		return
	}
	document, err := s.knowledge.CreateDocument(
		r.Context(), caller.PrincipalID, r.PathValue("knowledge_base_id"), request.SourceArtifactID,
	)
	if err != nil {
		s.writeKnowledgeFailure(w, r, err)
		return
	}
	writeJSON(w, http.StatusAccepted, acceptedView{ID: document.ID, State: document.State})
}

func (s *Server) handleListKnowledgeDocuments(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "")
	if !ok {
		return
	}
	if !s.knowledgeScope(w, r, caller, identity.ScopeApplicationRead) {
		return
	}
	query := r.URL.Query()
	limit, err := catalog.ParseLimit(query.Get("limit"))
	if err != nil {
		writeErrorWithDetails(w, r, http.StatusUnprocessableEntity, "validation_failed",
			"limit must be an integer between 1 and 100", map[string]any{"field": "limit"})
		return
	}
	items, next, err := s.knowledge.ListDocuments(
		r.Context(), caller.PrincipalID, r.PathValue("knowledge_base_id"),
		query.Get("cursor"), limit,
	)
	if err != nil {
		s.writeKnowledgeFailure(w, r, err)
		return
	}
	views := make([]knowledgeDocumentView, 0, len(items))
	for _, item := range items {
		views = append(views, documentViewOf(item))
	}
	writeJSON(w, http.StatusOK, knowledgeDocumentListResponse{Items: views, NextCursor: next})
}

func (s *Server) handleGetKnowledgeDocument(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "")
	if !ok {
		return
	}
	if !s.knowledgeScope(w, r, caller, identity.ScopeApplicationRead) {
		return
	}
	document, err := s.knowledge.GetDocument(
		r.Context(), caller.PrincipalID, r.PathValue("document_id"),
	)
	if err != nil {
		s.writeKnowledgeFailure(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, documentViewOf(document))
}

func (s *Server) handleKnowledgeDocumentDiagnostics(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "")
	if !ok {
		return
	}
	if !s.knowledgeScope(w, r, caller, identity.ScopeApplicationRead) {
		return
	}
	diagnostics, err := s.knowledge.DocumentDiagnostics(
		r.Context(), caller.PrincipalID, r.PathValue("document_id"),
	)
	if err != nil {
		s.writeKnowledgeFailure(w, r, err)
		return
	}
	view := diagnosticsView{
		DocumentID:             diagnostics.DocumentID,
		State:                  diagnostics.State,
		Revision:               diagnostics.Revision,
		ActiveRevision:         diagnostics.ActiveRevision,
		ParserVersion:          diagnostics.ParserVersion,
		ChunkerVersion:         diagnostics.ChunkerVersion,
		EmbeddingModelRevision: diagnostics.EmbeddingModelRevision,
		ErrorCode:              diagnostics.ErrorCode,
		ChunkCount:             diagnostics.ChunkCount,
	}
	if detail := diagnostics.Detail; detail != nil {
		view.Detail = &diagnosticsDetail{
			Sheet: detail.Sheet, Row: detail.Row, Column: detail.Column,
			Cell: detail.Cell, Part: detail.Part, Limit: detail.Limit, Value: detail.Value,
		}
	}
	if diagnostics.BackgroundJobID != nil {
		view.BackgroundJob = &diagnosticsBackgroundJob{
			ID:           *diagnostics.BackgroundJobID,
			State:        valueOrEmpty(diagnostics.BackgroundJobState),
			ErrorCode:    diagnostics.BackgroundJobCode,
			AttemptCount: diagnostics.BackgroundJobAttempts,
		}
	}
	writeJSON(w, http.StatusOK, view)
}

func (s *Server) handleKnowledgeRetrievalQuery(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "")
	if !ok {
		return
	}
	if !s.knowledgeScope(w, r, caller, identity.ScopeApplicationRead) {
		return
	}
	var request struct {
		Query string `json:"query"`
		TopK  int    `json:"top_k"`
	}
	if err := decodeJSON(w, r, &request); err != nil {
		writeError(w, r, http.StatusBadRequest, "invalid_json", "request body is not valid JSON")
		return
	}
	result, err := s.knowledge.Query(
		r.Context(), caller.PrincipalID, r.PathValue("knowledge_base_id"), request.Query, request.TopK,
	)
	if err != nil {
		s.writeKnowledgeFailure(w, r, err)
		return
	}
	items := make([]knowledgeChunkView, 0, len(result.Items))
	for _, item := range result.Items {
		items = append(items, chunkViewOf(item, true))
	}
	writeJSON(w, http.StatusOK, retrievalResponse{
		Items:      items,
		NextCursor: nil,
		Retrieval: retrievalMetadata{
			EmbeddingModelRevision: result.EmbeddingModelRevision,
			TopK:                   result.TopK,
			ACLEpoch:               result.ACLEpoch,
		},
	})
}

func (s *Server) handleGetKnowledgeCitation(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "")
	if !ok {
		return
	}
	if !s.knowledgeScope(w, r, caller, identity.ScopeApplicationRead) {
		return
	}
	chunk, err := s.knowledge.GetChunk(r.Context(), caller.PrincipalID, r.PathValue("chunk_id"))
	if err != nil {
		s.writeKnowledgeFailure(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, knowledgeCitationView{
		ChunkID:          chunk.ChunkID,
		DocumentID:       chunk.DocumentID,
		DocumentRevision: chunk.DocumentRevision,
		DocumentState:    chunk.DocumentState,
		ChunkNo:          chunk.ChunkNo,
		Text:             chunk.Text,
		Span:             chunk.Span,
		Citation:         chunk.Citation,
	})
}

// handleKnowledgeAnswer is the F10/T064 grounded-answer entry point.
//
// It returns the answer only when every citation came from this retrieval and
// was readable at the moment the answer was assembled. A refusal is a distinct
// error code per failure (never a partially-valid answer):
//
//	422 answer_without_citation  the generator answered but cited nothing
//	422 citation_not_found       a cited chunk is not in this retrieval
//	409 citation_unreadable      a cited chunk is no longer readable now
//
// An empty retrieval is 200 `no_results` with an empty answer, not an error.
func (s *Server) handleKnowledgeAnswer(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "")
	if !ok {
		return
	}
	if !s.knowledgeScope(w, r, caller, identity.ScopeApplicationRead) {
		return
	}
	var request struct {
		Query string `json:"query"`
		TopK  int    `json:"top_k"`
	}
	if err := decodeJSON(w, r, &request); err != nil {
		writeError(w, r, http.StatusBadRequest, "invalid_json",
			"request body is not valid JSON")
		return
	}
	result, err := s.knowledge.Answer(
		r.Context(), caller.PrincipalID, r.PathValue("knowledge_base_id"),
		request.Query, request.TopK,
	)
	if err != nil {
		s.writeKnowledgeFailure(w, r, err)
		return
	}
	switch result.Outcome {
	case knowledge.OutcomeAnswered, knowledge.OutcomeNoResults:
		writeJSON(w, http.StatusOK, answerResponseOf(result))
	case knowledge.OutcomeAnswerWithoutCitation:
		writeErrorWithDetails(w, r, http.StatusUnprocessableEntity,
			string(result.Outcome),
			"the generated answer cites no chunk; an answer over a knowledge base must cite this retrieval",
			map[string]any{"outcome": string(result.Outcome)})
	case knowledge.OutcomeCitationNotFound:
		writeErrorWithDetails(w, r, http.StatusUnprocessableEntity,
			string(result.Outcome),
			"a cited chunk is not part of this retrieval; the answer is refused",
			map[string]any{
				"outcome":  string(result.Outcome),
				"chunk_id": result.OffendingChunkID,
			})
	case knowledge.OutcomeCitationUnreadable:
		writeErrorWithDetails(w, r, http.StatusConflict,
			string(result.Outcome),
			"a cited chunk is no longer readable under the current access rights or document revision; the answer is refused",
			map[string]any{
				"outcome":  string(result.Outcome),
				"chunk_id": result.OffendingChunkID,
			})
	default:
		s.internalError(w, r, errors.New("unhandled answer outcome"))
	}
}

// ---------------------------------------------------------------------------
// internal job-scoped binding (mTLS maintenance capacity)
// ---------------------------------------------------------------------------

type internalDocumentIngestRequest struct {
	AttemptToken string `json:"attempt_token"`
}

func (r internalDocumentIngestRequest) attemptToken() string { return r.AttemptToken }

func (s *Server) handleInternalDocumentIngestBinding(w http.ResponseWriter, r *http.Request) {
	var request internalDocumentIngestRequest
	job, ok := s.withJobData(w, r, &request)
	if !ok {
		return
	}
	binding, err := s.knowledge.DocumentIngestBinding(r.Context(), job)
	if err != nil {
		switch {
		case errors.Is(err, knowledge.ErrNotFound):
			writeError(w, r, http.StatusNotFound, "resource_not_found",
				"that document is not in this job's scope")
		case errors.Is(err, knowledge.ErrArtifactNotReady):
			writeError(w, r, http.StatusConflict, "precondition_failed",
				"the bound source artifact is not ready")
		case errors.Is(err, knowledge.ErrUnsupportedInput):
			writeErrorWithDetails(w, r, http.StatusUnprocessableEntity, "validation_failed",
				"no parser is registered for the bound source media type",
				map[string]any{"field": "source_artifact_id"})
		default:
			s.internalError(w, r, err)
		}
		return
	}
	writeJSON(w, http.StatusOK, binding)
}

func valueOrEmpty(value *string) string {
	if value == nil {
		return ""
	}
	return *value
}
