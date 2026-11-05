package api

// F08/T074: conversations, requests, the ordered queue and messages.
//
//	POST   /api/v1/conversations                      createConversation
//	GET    /api/v1/conversations                      listConversations
//	GET    /api/v1/conversations/{id}                 getConversation
//	GET    /api/v1/conversations/{id}/messages        listMessages
//	POST   /api/v1/requests                           submitRequest
//	GET    /api/v1/requests/{id}                      getRequest
//	POST   /api/v1/requests/{id}/cancel               cancelRequest
//	POST   /api/v1/conversations/{id}/handoff-preview non-contract handover preview (T082)
//	DELETE /api/v1/conversations/{id}                 deleteConversation (T069 foundation)
//
// The contract Request/Conversation schemas are additionalProperties:false, so
// every extra fact the UI needs is a documented non-contract response header (the
// X-Manifest-SHA256 precedent, ADR 0004): the handling application version, the
// conversation id, the queue position and the pending interaction's correlation
// id. No JSON field is added and no contract file is edited.

import (
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"strconv"
	"strings"
	"time"

	"orchara/platform/internal/catalog"
	"orchara/platform/internal/identity"
	"orchara/platform/internal/requests"
)

type conversationView struct {
	ID        string `json:"id"`
	CreatedAt string `json:"created_at"`
	Revision  int64  `json:"revision"`
}

type conversationListResponse struct {
	Items      []conversationView `json:"items"`
	NextCursor *string            `json:"next_cursor"`
}

type messageListResponse struct {
	Items      []requests.MessageView `json:"items"`
	NextCursor *string                `json:"next_cursor"`
}

func conversationViewOf(conversation requests.Conversation) conversationView {
	return conversationView{
		ID:        conversation.ID,
		CreatedAt: conversation.CreatedAt.UTC().Format(time.RFC3339Nano),
		Revision:  conversation.Revision,
	}
}

// writeRequestFailure maps the aggregate's located outcomes onto the contract
// error envelope. It always writes, so callers return immediately afterwards.
func (s *Server) writeRequestFailure(w http.ResponseWriter, r *http.Request, err error) {
	if typed, ok := requests.AsError(err); ok {
		writeErrorWithDetails(w, r, typed.Status, typed.Code, typed.Message, typed.Details)
		return
	}
	var conflict *requests.IdempotencyConflictError
	if errors.As(err, &conflict) {
		writeErrorWithDetails(w, r, http.StatusConflict, "idempotency_conflict",
			"this Idempotency-Key was already used for a different request",
			map[string]any{"header": "Idempotency-Key"})
		return
	}
	if errors.Is(err, requests.ErrInvalidCursor) {
		writeError(w, r, http.StatusBadRequest, "invalid_cursor",
			"cursor is not a valid continuation token")
		return
	}
	if errors.Is(err, catalog.ErrInvalidCursor) {
		writeError(w, r, http.StatusBadRequest, "invalid_cursor",
			"cursor is not a valid continuation token")
		return
	}
	s.internalError(w, r, err)
}

func (s *Server) requireRequestRead(caller *identity.Identity) *authzFailure {
	return requireScope(caller, identity.ScopeApplicationRead)
}

func (s *Server) requireRequestWrite(caller *identity.Identity) *authzFailure {
	return requireScope(caller, identity.ScopeApplicationWrite)
}

func (s *Server) handleCreateConversation(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "")
	if !ok {
		return
	}
	if failure := s.requireRequestWrite(caller); failure != nil {
		writeError(w, r, failure.Status, failure.Code, failure.Message)
		return
	}
	conversation, err := s.requests.CreateConversation(r.Context(), caller.PrincipalID)
	if err != nil {
		s.writeRequestFailure(w, r, err)
		return
	}
	// The contract's createConversation has no request body and no Idempotency-Key;
	// the Accepted body therefore repeats the created id with its live state.
	writeJSON(w, http.StatusCreated, acceptedResponse{ID: conversation.ID, State: "active"})
}

func (s *Server) handleListConversations(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "")
	if !ok {
		return
	}
	if failure := s.requireRequestRead(caller); failure != nil {
		writeError(w, r, failure.Status, failure.Code, failure.Message)
		return
	}
	query := r.URL.Query()
	limit, err := catalog.ParseLimit(query.Get("limit"))
	if err != nil {
		writeErrorWithDetails(w, r, http.StatusUnprocessableEntity, "validation_failed",
			"limit must be an integer between 1 and 100", map[string]any{"field": "limit"})
		return
	}
	items, next, err := s.requests.ListConversations(
		r.Context(), caller.PrincipalID, query.Get("cursor"), limit)
	if err != nil {
		s.writeRequestFailure(w, r, err)
		return
	}
	views := make([]conversationView, 0, len(items))
	for _, item := range items {
		views = append(views, conversationViewOf(item))
	}
	writeJSON(w, http.StatusOK, conversationListResponse{Items: views, NextCursor: next})
}

func (s *Server) handleGetConversation(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "")
	if !ok {
		return
	}
	if failure := s.requireRequestRead(caller); failure != nil {
		writeError(w, r, failure.Status, failure.Code, failure.Message)
		return
	}
	conversation, err := s.requests.GetConversation(
		r.Context(), caller.PrincipalID, r.PathValue("conversation_id"))
	if err != nil {
		s.writeRequestFailure(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, conversationViewOf(*conversation))
}

func (s *Server) handleListMessages(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "")
	if !ok {
		return
	}
	if failure := s.requireRequestRead(caller); failure != nil {
		writeError(w, r, failure.Status, failure.Code, failure.Message)
		return
	}
	query := r.URL.Query()
	limit, err := catalog.ParseLimit(query.Get("limit"))
	if err != nil {
		writeErrorWithDetails(w, r, http.StatusUnprocessableEntity, "validation_failed",
			"limit must be an integer between 1 and 100", map[string]any{"field": "limit"})
		return
	}
	items, next, err := s.requests.ListMessages(
		r.Context(), caller.PrincipalID, r.PathValue("conversation_id"),
		query.Get("cursor"), limit)
	if err != nil {
		s.writeRequestFailure(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, messageListResponse{Items: items, NextCursor: next})
}

// readSubmitBody decodes the top-level RequestInput members without committing to
// their types, so every member problem is a located 422 in the service (the
// T027/T025 vocabulary) instead of a generic decode error.
func readSubmitBody(w http.ResponseWriter, r *http.Request) (map[string]json.RawMessage, bool) {
	if r.Body == nil {
		writeError(w, r, http.StatusBadRequest, "invalid_json", "request body is missing")
		return nil, false
	}
	body := http.MaxBytesReader(w, r.Body, maxRequestBody)
	decoder := json.NewDecoder(body)
	var fields map[string]json.RawMessage
	if err := decoder.Decode(&fields); err != nil {
		var tooLarge *http.MaxBytesError
		if errors.As(err, &tooLarge) {
			writeError(w, r, http.StatusRequestEntityTooLarge, "payload_too_large",
				"the request body exceeds the request size limit")
			return nil, false
		}
		writeError(w, r, http.StatusBadRequest, "invalid_json", "request body is not valid JSON")
		return nil, false
	}
	if fields == nil {
		writeError(w, r, http.StatusBadRequest, "invalid_json", "request body must be a JSON object")
		return nil, false
	}
	if err := decoder.Decode(&struct{}{}); !errors.Is(err, io.EOF) {
		writeError(w, r, http.StatusBadRequest, "invalid_json", "request body contains trailing data")
		return nil, false
	}
	return fields, true
}

// handleHandoffPreview builds the server-produced minimal handover scope for one
// conversation (F08/T082). It is a non-contract, read-only POST because the source
// Run ids and the selected artifacts are an array body; it writes nothing and
// needs no Idempotency-Key. The page copies this response verbatim into the
// contract's RequestInput.handoff; the submit path re-derives and compares it.
func (s *Server) handleHandoffPreview(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "")
	if !ok {
		return
	}
	if failure := s.requireRequestRead(caller); failure != nil {
		writeError(w, r, failure.Status, failure.Code, failure.Message)
		return
	}
	fields, ok := readSubmitBody(w, r)
	if !ok {
		return
	}
	input, parseErr := requests.ParseHandoffPreview(fields)
	if parseErr != nil {
		s.writeRequestFailure(w, r, parseErr)
		return
	}
	preview, err := s.requests.HandoffPreview(
		r.Context(), caller.PrincipalID, r.PathValue("conversation_id"), input)
	if err != nil {
		s.writeRequestFailure(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, preview)
}

func (s *Server) handleSubmitRequest(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "")
	if !ok {
		return
	}
	if failure := s.requireRequestWrite(caller); failure != nil {
		writeError(w, r, failure.Status, failure.Code, failure.Message)
		return
	}
	values := r.Header.Values("Idempotency-Key")
	key := ""
	if len(values) == 1 {
		key = strings.TrimSpace(values[0])
	}
	if len(values) != 1 || key == "" {
		writeErrorWithDetails(w, r, http.StatusBadRequest, "idempotency_key_required",
			"the Idempotency-Key header is required and must be one non-empty value",
			map[string]any{"header": "Idempotency-Key"})
		return
	}
	fields, ok := readSubmitBody(w, r)
	if !ok {
		return
	}
	input, parseErr := requests.ParseSubmit(fields)
	if parseErr != nil {
		s.writeRequestFailure(w, r, parseErr)
		return
	}
	outcome, err := s.requests.Submit(r.Context(), caller.PrincipalID, requests.IdempotencyScope{
		PrincipalID: caller.PrincipalID,
		Method:      http.MethodPost,
		Path:        requests.SubmitPath,
		Key:         key,
	}, input)
	if err != nil {
		s.writeRequestFailure(w, r, err)
		return
	}
	if outcome == nil || outcome.Request == nil {
		s.internalError(w, r, errors.New("the request submission produced no request"))
		return
	}
	s.writeRequestHeaders(w, r, caller.PrincipalID, outcome.Request)
	writeJSON(w, http.StatusAccepted, outcome.Request)
}

func (s *Server) handleGetRequest(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "")
	if !ok {
		return
	}
	if failure := s.requireRequestRead(caller); failure != nil {
		writeError(w, r, failure.Status, failure.Code, failure.Message)
		return
	}
	request, facts, err := s.requests.GetRequest(
		r.Context(), caller.PrincipalID, r.PathValue("request_id"))
	if err != nil {
		s.writeRequestFailure(w, r, err)
		return
	}
	s.writeRequestHeadersWithFacts(w, request, facts)
	writeJSON(w, http.StatusOK, request)
}

func (s *Server) handleCancelRequest(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "")
	if !ok {
		return
	}
	if failure := s.requireRequestWrite(caller); failure != nil {
		writeError(w, r, failure.Status, failure.Code, failure.Message)
		return
	}
	request, err := s.requests.Cancel(
		r.Context(), caller.PrincipalID, r.PathValue("request_id"))
	if err != nil {
		s.writeRequestFailure(w, r, err)
		return
	}
	// The contract's cancelRequest returns Accepted, not Request; the client
	// re-reads getRequest for the resulting state (API.md §16).
	writeJSON(w, http.StatusAccepted, acceptedResponse{ID: request.ID, State: request.State})
}

// handleReplyInteraction answers one pending input/selection interaction. The
// contract's InteractionReply anyOf and additionalProperties:false are enforced by
// requests.ParseReply; the Idempotency-Key is required and scoped to this exact
// request path, so a key can never be borrowed by another request's reply.
func (s *Server) handleReplyInteraction(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "")
	if !ok {
		return
	}
	if failure := s.requireRequestWrite(caller); failure != nil {
		writeError(w, r, failure.Status, failure.Code, failure.Message)
		return
	}
	values := r.Header.Values("Idempotency-Key")
	key := ""
	if len(values) == 1 {
		key = strings.TrimSpace(values[0])
	}
	if len(values) != 1 || key == "" {
		writeErrorWithDetails(w, r, http.StatusBadRequest, "idempotency_key_required",
			"the Idempotency-Key header is required and must be one non-empty value",
			map[string]any{"header": "Idempotency-Key"})
		return
	}
	requestID := strings.ToLower(strings.TrimSpace(r.PathValue("request_id")))
	fields, ok := readSubmitBody(w, r)
	if !ok {
		return
	}
	input, parseErr := requests.ParseReply(fields)
	if parseErr != nil {
		s.writeRequestFailure(w, r, parseErr)
		return
	}
	outcome, err := s.requests.ReplyInteraction(r.Context(), caller.PrincipalID, requestID,
		requests.IdempotencyScope{
			PrincipalID: caller.PrincipalID,
			Method:      http.MethodPost,
			Path:        requests.ReplyPath(requestID),
			Key:         key,
		}, input)
	if err != nil {
		s.writeRequestFailure(w, r, err)
		return
	}
	if outcome == nil || outcome.Request == nil {
		s.internalError(w, r, errors.New("the interaction reply produced no request"))
		return
	}
	s.writeRequestHeaders(w, r, caller.PrincipalID, outcome.Request)
	writeJSON(w, http.StatusAccepted, outcome.Request)
}

// writeRequestHeaders computes the documented non-contract facts for a request
// write response; a failure to read them is logged, not fatal: the contract body
// is already authoritative.
func (s *Server) writeRequestHeaders(
	w http.ResponseWriter, r *http.Request, callerID string, request *requests.Request,
) {
	facts, err := s.requests.Facts(r.Context(), request)
	if err != nil {
		s.logger.Printf("request_id=%s request facts: %v", RequestID(r.Context()), err)
		return
	}
	s.writeRequestHeadersWithFacts(w, request, facts)
}

func (s *Server) writeRequestHeadersWithFacts(
	w http.ResponseWriter, request *requests.Request, facts *requests.RequestFacts,
) {
	w.Header().Set("X-Request-Conversation-Id", request.ConversationID)
	if facts == nil {
		return
	}
	if facts.ApplicationVersionID != "" {
		w.Header().Set("X-Request-Application-Version-Id", facts.ApplicationVersionID)
		w.Header().Set("X-Request-Application-Version-No", strconv.FormatInt(facts.VersionNo, 10))
	}
	if facts.InteractionID != "" {
		w.Header().Set("X-Request-Interaction-Id", facts.InteractionID)
	}
	if facts.CorrelationID != "" {
		w.Header().Set("X-Request-Correlation-Id", facts.CorrelationID)
	}
	if facts.QueuePosition > 0 {
		w.Header().Set("X-Request-Queue-Position", strconv.Itoa(facts.QueuePosition))
	}
}
