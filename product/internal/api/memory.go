package api

// F09/T054: personal long-term memory.
//
//	POST /api/v1/memories                              createMemory
//	GET  /api/v1/memories                              listMemories
//	GET  /api/v1/memories/{memory_id}                  getMemory      (strong ETag)
//	GET  /api/v1/memory-candidates                     listMemoryCandidates
//	POST /api/v1/memory-candidates/{id}/decision       decideMemoryCandidate
//	POST /internal/v1/runs/{run_id}/memory-candidates  proposeMemoryCandidate
//
// F09/T058 adds the revisioned read/write surface: the strong ETag on the detail
// read is the revision token a PATCH must echo, and update/delete raise the
// revision and the memory-scope cache epoch in the same transaction so the old
// content stops being recallable before any asynchronous cleanup.
//
// Every public handler refuses a service credential with 403
// `personal_scope_forbidden` before any store code runs (the F01/T020 boundary:
// personal memory belongs to humans only). The contract lists `apiKey` on these
// operations; the refusal is the deliberate deviation recorded in ADR 0009 §1.
//
// The handlers contain no SQL: every candidate/memory row and every sealed body
// comes from internal/memory. A typed *memory.Error maps one-to-one onto the
// contract error envelope, including the located `field` detail.

import (
	"net/http"
	"strings"
	"time"

	"orchara/platform/internal/catalog"
	"orchara/platform/internal/identity"
	"orchara/platform/internal/memory"
)

// memoryView is exactly the contract's Memory schema: no state, no expiry.
type memoryView struct {
	ID            string `json:"id"`
	ApplicationID string `json:"application_id"`
	Revision      int64  `json:"revision"`
	Text          string `json:"text"`
}

// candidateView is exactly the contract's MemoryCandidate schema.
type candidateView struct {
	ID            string `json:"id"`
	ApplicationID string `json:"application_id"`
	Text          string `json:"text"`
	State         string `json:"state"`
	ExpiresAt     string `json:"expires_at"`
}

// decisionView is exactly the contract's MemoryCandidateDecision schema.
type decisionView struct {
	CandidateID string  `json:"candidate_id"`
	State       string  `json:"state"`
	MemoryID    *string `json:"memory_id"`
}

func viewOfMemory(entry memory.Memory) memoryView {
	return memoryView{
		ID:            entry.ID,
		ApplicationID: entry.ApplicationID,
		Revision:      entry.Revision,
		Text:          entry.Text,
	}
}

func viewOfCandidate(entry memory.Candidate) candidateView {
	return candidateView{
		ID:            entry.ID,
		ApplicationID: entry.ApplicationID,
		Text:          entry.Text,
		State:         entry.State,
		ExpiresAt:     entry.ExpiresAt.UTC().Format(time.RFC3339),
	}
}

func viewOfDecision(result *memory.Decision) decisionView {
	return decisionView{
		CandidateID: result.CandidateID,
		State:       result.State,
		MemoryID:    result.MemoryID,
	}
}

// requireHumanMemory enforces the personal-data boundary. It returns false after
// writing the contract refusal, so a handler never has to remember the order.
func (s *Server) requireHumanMemory(w http.ResponseWriter, r *http.Request, caller *identity.Identity) bool {
	if caller.Kind == "service" || caller.Method == identity.AuthMethodAPIKey {
		writeError(w, r, http.StatusForbidden, memory.CodePersonalScope,
			"personal memory belongs to human users; a service account cannot read or write it")
		return false
	}
	return true
}

// memoryListEnvelope is the contract's {items,next_cursor} shape for both lists.
type memoryListEnvelope[T any] struct {
	Items      []T     `json:"items"`
	NextCursor *string `json:"next_cursor"`
}

// createMemoryRequest is the contract's MemoryInput. `confirmed` is a pointer so
// a missing/false value is a located 422 instead of a silent default.
type createMemoryRequest struct {
	ApplicationID string `json:"application_id"`
	Text          string `json:"text"`
	Confirmed     *bool  `json:"confirmed"`
}

// handleCreateMemory is the explicit "remember this" path. It never creates a
// candidate: the human is the confirmer, so the memory is written directly.
func (s *Server) handleCreateMemory(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "")
	if !ok {
		return
	}
	if !s.requireHumanMemory(w, r, caller) {
		return
	}
	var request createMemoryRequest
	if err := decodeJSON(w, r, &request); err != nil {
		writeError(w, r, http.StatusBadRequest, "invalid_json", "request body is not valid JSON")
		return
	}
	confirmed := request.Confirmed != nil && *request.Confirmed
	entry, err := s.memory.Create(r.Context(), caller.PrincipalID, memory.CreateInput{
		ApplicationID: request.ApplicationID,
		Text:          request.Text,
		Confirmed:     confirmed,
	})
	if !s.writeMemoryError(w, r, err) {
		return
	}
	writeJSON(w, http.StatusCreated, viewOfMemory(*entry))
}

// handleListMemories is the real read model that replaces F01's honest empty
// list. Only the owner's active memories are returned.
func (s *Server) handleListMemories(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "")
	if !ok {
		return
	}
	if !s.requireHumanMemory(w, r, caller) {
		return
	}
	query := r.URL.Query()
	limit, ok := parseMemoryLimit(w, r)
	if !ok {
		return
	}
	items, next, listErr := s.memory.List(
		r.Context(), caller.PrincipalID, limit, query.Get("cursor"), query.Get("application_id"),
	)
	if !s.writeMemoryError(w, r, listErr) {
		return
	}
	views := make([]memoryView, 0, len(items))
	for _, entry := range items {
		views = append(views, viewOfMemory(entry))
	}
	writeJSON(w, http.StatusOK, memoryListEnvelope[memoryView]{Items: views, NextCursor: next})
}

// handleGetMemory returns one memory plus the strong ETag a later PATCH must
// echo. The ETag is the revision as a quoted opaque token (catalog.FormatETag),
// the same validator the draft editor uses, and Cache-Control: no-store comes
// from writeJSON so a stale revision can never be served from a cache.
func (s *Server) handleGetMemory(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "")
	if !ok {
		return
	}
	if !s.requireHumanMemory(w, r, caller) {
		return
	}
	entry, err := s.memory.Get(r.Context(), caller.PrincipalID, r.PathValue("memory_id"))
	if !s.writeMemoryError(w, r, err) {
		return
	}
	w.Header().Set("ETag", catalog.FormatETag(entry.Revision))
	writeJSON(w, http.StatusOK, viewOfMemory(*entry))
}

// handleListMemoryCandidates returns the owner's candidates. A pending candidate
// past its deadline is reported as expired by the server, never left for the page
// to infer.
func (s *Server) handleListMemoryCandidates(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "")
	if !ok {
		return
	}
	if !s.requireHumanMemory(w, r, caller) {
		return
	}
	query := r.URL.Query()
	limit, valid := parseMemoryLimit(w, r)
	if !valid {
		return
	}
	items, next, listErr := s.memory.ListCandidates(
		r.Context(), caller.PrincipalID, limit, query.Get("cursor"), query.Get("application_id"),
	)
	if !s.writeMemoryError(w, r, listErr) {
		return
	}
	views := make([]candidateView, 0, len(items))
	for _, entry := range items {
		views = append(views, viewOfCandidate(entry))
	}
	writeJSON(w, http.StatusOK, memoryListEnvelope[candidateView]{Items: views, NextCursor: next})
}

// decisionRequest is the contract's decideMemoryCandidate body.
type decisionRequest struct {
	Decision string `json:"decision"`
}

func (s *Server) handleDecideMemoryCandidate(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "")
	if !ok {
		return
	}
	if !s.requireHumanMemory(w, r, caller) {
		return
	}
	// The contract makes Idempotency-Key a required, non-empty header. A missing
	// one is a located 422 before any row is touched (the same shape T050 uses
	// for the approval decision).
	idempotencyKey := r.Header.Get("Idempotency-Key")
	if idempotencyKey == "" {
		writeErrorWithDetails(w, r, http.StatusUnprocessableEntity, "validation_failed",
			"Idempotency-Key is required", map[string]any{"field": "Idempotency-Key"})
		return
	}
	var request decisionRequest
	if err := decodeJSON(w, r, &request); err != nil {
		writeError(w, r, http.StatusBadRequest, "invalid_json", "request body is not valid JSON")
		return
	}
	result, err := s.memory.Decide(r.Context(), memory.DecisionRequest{
		CandidateID:    r.PathValue("candidate_id"),
		CallerID:       caller.PrincipalID,
		Decision:       request.Decision,
		IdempotencyKey: idempotencyKey,
		Path:           r.URL.Path,
	})
	if !s.writeMemoryError(w, r, err) {
		return
	}
	writeJSON(w, http.StatusOK, viewOfDecision(result))
}

// internalProposeMemoryCandidateRequest is the contract's proposeMemoryCandidate
// body. There is deliberately no application_id: the server derives the scope
// from the run's frozen snapshot.
type internalProposeMemoryCandidateRequest struct {
	AttemptToken string `json:"attempt_token"`
	LogicalKey   string `json:"logical_key"`
	Text         string `json:"text"`
}

// handleInternalProposeMemoryCandidate is worker-only mTLS. The live attempt
// token is re-proved against the run in the path by the service.
func (s *Server) handleInternalProposeMemoryCandidate(w http.ResponseWriter, r *http.Request) {
	if !requireInternalRole(w, r, InternalRoleWorker) {
		return
	}
	var request internalProposeMemoryCandidateRequest
	if err := decodeJSON(w, r, &request); err != nil {
		writeError(w, r, http.StatusBadRequest, "invalid_json", "request body is not valid JSON")
		return
	}
	candidate, err := s.memory.Propose(r.Context(), memory.ProposeRequest{
		RunID:        r.PathValue("run_id"),
		AttemptToken: request.AttemptToken,
		LogicalKey:   request.LogicalKey,
		Text:         request.Text,
	})
	if !s.writeMemoryError(w, r, err) {
		return
	}
	writeJSON(w, http.StatusCreated, viewOfCandidate(*candidate))
}

// parseMemoryLimit applies the contract's limit rule through the existing catalog
// parser so both lists reject a bad limit with the same located 422.
//
// A present-but-blank limit is a malformed value, not an omitted one: this is the
// rule the F01 memory boundary already documented, kept so replacing the empty
// list does not silently loosen an input check.
func parseMemoryLimit(w http.ResponseWriter, r *http.Request) (int, bool) {
	query := r.URL.Query()
	if query.Has("limit") && strings.TrimSpace(query.Get("limit")) == "" {
		writeErrorWithDetails(w, r, http.StatusUnprocessableEntity, "validation_failed",
			"limit must be an integer between 1 and 100", map[string]any{"field": "limit"})
		return 0, false
	}
	limit, err := catalog.ParseLimit(query.Get("limit"))
	if err != nil {
		writeErrorWithDetails(w, r, http.StatusUnprocessableEntity, "validation_failed",
			"limit must be an integer between 1 and 100", map[string]any{"field": "limit"})
		return 0, false
	}
	return limit, true
}

// writeMemoryError maps the memory vocabulary onto the contract envelope.
func (s *Server) writeMemoryError(w http.ResponseWriter, r *http.Request, err error) bool {
	if err == nil {
		return true
	}
	if typed, ok := memory.AsError(err); ok {
		var details map[string]any
		if typed.Field != "" {
			details = map[string]any{"field": typed.Field}
		}
		writeErrorWithDetails(w, r, typed.Status, typed.Code, typed.Message, details)
		return false
	}
	s.internalError(w, r, err)
	return false
}
