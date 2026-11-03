package api

// F06/T039: model registration, reads, compatibility validation and the admin
// catalog reads.
//
//	POST /api/v1/models                     -> 201 Accepted             (registerModel, admin)
//	GET  /api/v1/models                     -> 200 {items,next_cursor}  (listModels, auth)
//	GET  /api/v1/models/{model_version_id}  -> 200 ModelProfile         (getModel, auth)
//	POST /api/v1/models/{model_version_id}/validate -> 202 EvaluationJob (validateModel, admin,
//	                                            Idempotency-Key required)
//	GET  /api/v1/admin/model-endpoints      -> 200 {items:[{id,data_destination}]} (listModelEndpoints)
//	GET  /api/v1/admin/credential-refs      -> 200 {items:[{id,description}]}      (listCredentialRefs)
//
// Every response body is exactly the contract projection. The catalog reads are
// contract paths, not extensions: their item schemas have no other fields, so
// the endpoint's credential reference (used only to choose a default in the UI)
// is never returned here.
//
// Authorization: registration and validation are administrator-only, and an
// API key is never an administrator (a service account has no roles), so a
// bearer key is refused even with a write scope. Reads use the same interim
// API-key scope mapping as T028/T037 (`application:read`); a session is not
// scope-limited. The service decides visibility (admin sees all, everyone else
// sees passed and non-revoked only), so a pending/failed model is a 404 for a
// non-admin and the id is not even discoverable.

import (
	"errors"
	"net/http"
	"strconv"
	"strings"

	"orchara/platform/internal/identity"
	"orchara/platform/internal/modelregistry"
	"orchara/platform/internal/objectstore"
)

type modelListResponse struct {
	Items      []modelregistry.ModelProfile `json:"items"`
	NextCursor *string                      `json:"next_cursor"`
}

// modelEndpointView is the contract's exact item: id + data_destination. No
// credential_ref, no description (additionalProperties is false).
type modelEndpointView struct {
	ID              string `json:"id"`
	DataDestination string `json:"data_destination"`
}

type credentialRefView struct {
	ID          string `json:"id"`
	Description string `json:"description"`
}

type modelEndpointListResponse struct {
	Items []modelEndpointView `json:"items"`
}

type credentialRefListResponse struct {
	Items []credentialRefView `json:"items"`
}

// writeModelFailure maps the registry's typed errors onto the contract envelope.
func (s *Server) writeModelFailure(w http.ResponseWriter, r *http.Request, err error) {
	var (
		validation *modelregistry.ValidationError
		conflict   *modelregistry.ConflictError
	)
	switch {
	case err == nil:
		return
	case errors.As(err, &validation):
		writeErrorWithDetails(w, r, http.StatusUnprocessableEntity, "validation_failed",
			validation.Error(), map[string]any{"field": validation.Field})
	case errors.As(err, &conflict):
		writeError(w, r, http.StatusConflict, conflict.Code, conflict.Message)
	case errors.Is(err, modelregistry.ErrNotFound):
		writeError(w, r, http.StatusNotFound, "resource_not_found", "that model profile does not exist")
	case errors.Is(err, modelregistry.ErrInvalidCursor):
		writeError(w, r, http.StatusBadRequest, "invalid_cursor", "the cursor is not recognised")
	case errors.Is(err, modelregistry.ErrIdempotencyKeyReuse):
		writeErrorWithDetails(w, r, http.StatusConflict, "idempotency_conflict",
			"this Idempotency-Key was already used for a different request",
			map[string]any{"header": "Idempotency-Key"})
	case errors.Is(err, objectstore.ErrNotConfigured):
		writeError(w, r, http.StatusServiceUnavailable, "object_store_unavailable",
			"the object store is not configured")
	default:
		s.internalError(w, r, err)
	}
}

// requireModelRegistry answers 503 when the registration surface was not wired
// (impossible for a normal process; a nil catalog must fail closed).
func (s *Server) requireModelRegistry(w http.ResponseWriter, r *http.Request) bool {
	if s.modelRegistry == nil {
		writeError(w, r, http.StatusServiceUnavailable, "model_registry_unconfigured",
			"model registration is not configured in this process")
		return false
	}
	return true
}

func (s *Server) handleRegisterModel(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "admin")
	if !ok {
		return
	}
	if !s.requireModelRegistry(w, r) {
		return
	}
	var input modelregistry.ModelInput
	if err := decodeJSON(w, r, &input); err != nil {
		writeError(w, r, http.StatusBadRequest, "invalid_json", "request body is not valid JSON")
		return
	}
	profile, err := s.modelRegistry.Register(r.Context(), caller.PrincipalID, input)
	if err != nil {
		s.writeModelFailure(w, r, err)
		return
	}
	writeJSON(w, http.StatusCreated, acceptedResponse{ID: profile.ID, State: profile.ValidationState})
}

func (s *Server) handleListModels(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "")
	if !ok {
		return
	}
	if failure := requireScope(caller, identity.ScopeApplicationRead); failure != nil {
		writeError(w, r, failure.Status, failure.Code, failure.Message)
		return
	}
	if !s.requireModelRegistry(w, r) {
		return
	}
	limit := modelregistry.DefaultListLimit
	if raw := strings.TrimSpace(r.URL.Query().Get("limit")); raw != "" {
		parsed, err := strconv.Atoi(raw)
		if err != nil || parsed < 1 || parsed > modelregistry.MaxListLimit {
			writeErrorWithDetails(w, r, http.StatusUnprocessableEntity, "validation_failed",
				"limit must be between 1 and 100", map[string]any{"field": "limit"})
			return
		}
		limit = parsed
	}
	items, next, err := s.modelRegistry.List(
		r.Context(), caller.PrincipalID, caller.HasRole("admin"),
		strings.TrimSpace(r.URL.Query().Get("application_id")),
		r.URL.Query().Get("cursor"), limit,
	)
	if err != nil {
		s.writeModelFailure(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, modelListResponse{Items: items, NextCursor: next})
}

func (s *Server) handleGetModel(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "")
	if !ok {
		return
	}
	if failure := requireScope(caller, identity.ScopeApplicationRead); failure != nil {
		writeError(w, r, failure.Status, failure.Code, failure.Message)
		return
	}
	if !s.requireModelRegistry(w, r) {
		return
	}
	profile, err := s.modelRegistry.Get(
		r.Context(), r.PathValue("model_version_id"), caller.PrincipalID, caller.HasRole("admin"),
	)
	if err != nil {
		s.writeModelFailure(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, profile)
}

func (s *Server) handleValidateModel(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "admin")
	if !ok {
		return
	}
	if !s.requireModelRegistry(w, r) {
		return
	}
	// The Idempotency-Key handling mirrors the debug path exactly: a repeated
	// header is ambiguous, an empty one cannot identify an operation, and both
	// are a 400 before any row exists.
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
	evaluation, err := s.modelRegistry.BeginValidation(
		r.Context(), r.PathValue("model_version_id"), caller.PrincipalID,
		key, r.URL.Path,
	)
	if err != nil {
		s.writeModelFailure(w, r, err)
		return
	}
	writeJSON(w, http.StatusAccepted, evaluation)
}

func (s *Server) handleListModelEndpoints(w http.ResponseWriter, r *http.Request) {
	if _, ok := s.authorize(w, r, "admin"); !ok {
		return
	}
	if !s.requireModelRegistry(w, r) {
		return
	}
	catalog := s.modelRegistry.Catalog
	if catalog == nil {
		writeJSON(w, http.StatusOK, modelEndpointListResponse{Items: []modelEndpointView{}})
		return
	}
	endpoints := catalog.Endpoints()
	items := make([]modelEndpointView, 0, len(endpoints))
	for _, endpoint := range endpoints {
		items = append(items, modelEndpointView{
			ID:              endpoint.ID,
			DataDestination: endpoint.DataDestination,
		})
	}
	writeJSON(w, http.StatusOK, modelEndpointListResponse{Items: items})
}

func (s *Server) handleListCredentialRefs(w http.ResponseWriter, r *http.Request) {
	if _, ok := s.authorize(w, r, "admin"); !ok {
		return
	}
	if !s.requireModelRegistry(w, r) {
		return
	}
	catalog := s.modelRegistry.Catalog
	if catalog == nil {
		writeJSON(w, http.StatusOK, credentialRefListResponse{Items: []credentialRefView{}})
		return
	}
	credentials := catalog.Credentials()
	items := make([]credentialRefView, 0, len(credentials))
	for _, credential := range credentials {
		items = append(items, credentialRefView{
			ID:          credential.ID,
			Description: credential.Description,
		})
	}
	writeJSON(w, http.StatusOK, credentialRefListResponse{Items: items})
}
