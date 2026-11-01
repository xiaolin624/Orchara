package api

import (
	"errors"
	"log"
	"net/http"
	"path/filepath"
	"time"

	"orchara/platform/internal/config"
	"orchara/platform/internal/identity"
)

// Server serves the F01 identity endpoints and the static pages that drive them.
type Server struct {
	service      *identity.Service
	webDir       string
	secureCookie bool
	logger       *log.Logger
}

// New builds the HTTP surface. webDir is where admin.html, activate.html and
// assets/app.js are served from (ORCHARA_WEB_DIR).
func New(service *identity.Service, webDir string, profile config.Profile, logger *log.Logger) *Server {
	if logger == nil {
		logger = log.New(nullWriter{}, "", 0)
	}
	return &Server{
		service:      service,
		webDir:       webDir,
		secureCookie: SecureCookie(profile),
		logger:       logger,
	}
}

// Register adds the T015 routes to mux.
func (s *Server) Register(mux *http.ServeMux) {
	mux.HandleFunc("POST /api/v1/auth/login", s.handleLogin)
	mux.HandleFunc("POST /api/v1/auth/activate", s.handleActivate)
	mux.HandleFunc("POST /api/v1/admin/users", s.handleInviteUser)

	mux.HandleFunc("GET /admin", s.staticPage("admin.html"))
	mux.HandleFunc("GET /activate", s.staticPage("activate.html"))
	mux.HandleFunc("GET /assets/app.js", s.staticPage(filepath.Join("assets", "app.js")))
}

type loginRequest struct {
	Login    string `json:"login"`
	Password string `json:"password"`
}

type sessionResponse struct {
	PrincipalID string `json:"principal_id"`
	CSRFToken   string `json:"csrf_token"`
	ExpiresAt   string `json:"expires_at"`
}

func (s *Server) handleLogin(w http.ResponseWriter, r *http.Request) {
	var request loginRequest
	if err := decodeJSON(w, r, &request); err != nil {
		writeError(w, r, http.StatusBadRequest, "invalid_json", "request body is not valid JSON")
		return
	}

	session, err := s.service.Login(r.Context(), request.Login, request.Password)
	switch {
	case errors.Is(err, identity.ErrInvalidCredentials):
		// Same answer for an unknown login and a wrong password.
		writeError(w, r, http.StatusUnauthorized, "invalid_credentials", "login or password is incorrect")
		return
	case errors.Is(err, identity.ErrPrincipalDisabled):
		writeError(w, r, http.StatusForbidden, "principal_disabled", "this account is disabled")
		return
	case err != nil:
		s.internalError(w, r, err)
		return
	}

	http.SetCookie(w, newSessionCookie(session.Token, s.secureCookie))
	writeJSON(w, http.StatusOK, sessionResponse{
		PrincipalID: session.PrincipalID,
		CSRFToken:   session.CSRFToken,
		ExpiresAt:   session.ExpiresAt.UTC().Format(time.RFC3339),
	})
}

type inviteRequest struct {
	Login       string   `json:"login"`
	DisplayName string   `json:"display_name"`
	Roles       []string `json:"roles"`
}

type invitationResponse struct {
	PrincipalID     string `json:"principal_id"`
	ActivationToken string `json:"activation_token"`
	ExpiresAt       string `json:"expires_at"`
}

func (s *Server) handleInviteUser(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.requireIdentity(w, r)
	if !ok {
		return
	}
	if !caller.HasRole("admin") {
		writeError(w, r, http.StatusForbidden, "forbidden", "administrator role is required")
		return
	}

	var request inviteRequest
	if err := decodeJSON(w, r, &request); err != nil {
		writeError(w, r, http.StatusBadRequest, "invalid_json", "request body is not valid JSON")
		return
	}

	invitation, err := s.service.InviteUser(
		r.Context(), caller.PrincipalID, request.Login, request.DisplayName, request.Roles,
	)
	switch {
	case errors.Is(err, identity.ErrLoginTaken):
		writeError(w, r, http.StatusConflict, "login_taken", "that login is already taken")
		return
	case errors.Is(err, identity.ErrValidation):
		writeErrorWithDetails(w, r, http.StatusUnprocessableEntity,
			"validation_failed", err.Error(), validationDetails(err))
		return
	case err != nil:
		s.internalError(w, r, err)
		return
	}

	writeJSON(w, http.StatusCreated, invitationResponse{
		PrincipalID:     invitation.PrincipalID,
		ActivationToken: invitation.Token,
		ExpiresAt:       invitation.ExpiresAt.UTC().Format(time.RFC3339),
	})
}

type activateRequest struct {
	ActivationToken string `json:"activation_token"`
	Password        string `json:"password"`
}

type acceptedResponse struct {
	ID    string `json:"id"`
	State string `json:"state"`
}

func (s *Server) handleActivate(w http.ResponseWriter, r *http.Request) {
	var request activateRequest
	if err := decodeJSON(w, r, &request); err != nil {
		writeError(w, r, http.StatusBadRequest, "invalid_json", "request body is not valid JSON")
		return
	}

	principalID, err := s.service.Activate(r.Context(), request.ActivationToken, request.Password)
	switch {
	case errors.Is(err, identity.ErrValidation):
		writeErrorWithDetails(w, r, http.StatusUnprocessableEntity,
			"validation_failed", err.Error(), validationDetails(err))
		return
	case errors.Is(err, identity.ErrActivationConsumed):
		writeError(w, r, http.StatusConflict, "activation_consumed",
			"that activation token was already used")
		return
	case errors.Is(err, identity.ErrActivationExpired):
		writeError(w, r, http.StatusConflict, "activation_expired",
			"that activation token has expired")
		return
	case errors.Is(err, identity.ErrActivationInvalid):
		writeError(w, r, http.StatusConflict, "activation_invalid",
			"that activation token is not recognised")
		return
	case err != nil:
		s.internalError(w, r, err)
		return
	}

	writeJSON(w, http.StatusOK, acceptedResponse{ID: principalID, State: "activated"})
}

// requireIdentity resolves the session cookie, writing the contract error when
// it cannot. It returns ok=false when a response has already been written.
func (s *Server) requireIdentity(w http.ResponseWriter, r *http.Request) (*identity.Identity, bool) {
	cookie, err := r.Cookie(identity.SessionCookieName)
	if err != nil {
		writeError(w, r, http.StatusUnauthorized, "unauthenticated", "a session cookie is required")
		return nil, false
	}
	caller, err := s.service.Authenticate(r.Context(), cookie.Value)
	switch {
	case errors.Is(err, identity.ErrUnauthenticated):
		writeError(w, r, http.StatusUnauthorized, "unauthenticated", "the session is not recognised")
		return nil, false
	case errors.Is(err, identity.ErrSessionExpired):
		writeError(w, r, http.StatusUnauthorized, "session_expired", "the session has expired")
		return nil, false
	case errors.Is(err, identity.ErrSessionRevoked):
		writeError(w, r, http.StatusUnauthorized, "session_revoked", "the session was revoked")
		return nil, false
	case errors.Is(err, identity.ErrPrincipalDisabled):
		writeError(w, r, http.StatusForbidden, "principal_disabled", "this account is disabled")
		return nil, false
	case err != nil:
		s.internalError(w, r, err)
		return nil, false
	}
	return caller, true
}

func validationDetails(err error) map[string]any {
	var validation *identity.ValidationError
	if errors.As(err, &validation) && validation.Field != "" {
		return map[string]any{"field": validation.Field}
	}
	return nil
}

func (s *Server) internalError(w http.ResponseWriter, r *http.Request, err error) {
	s.logger.Printf("request_id=%s internal error: %v", RequestID(r.Context()), err)
	writeError(w, r, http.StatusInternalServerError, "internal_error", "internal error")
}

// staticPage serves one known file from ORCHARA_WEB_DIR. The relative path is a
// compile-time constant, so no request input can escape the web directory.
func (s *Server) staticPage(relative string) http.HandlerFunc {
	path := filepath.Join(s.webDir, relative)
	return func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Cache-Control", "no-store")
		http.ServeFile(w, r, path)
	}
}

type nullWriter struct{}

func (nullWriter) Write(body []byte) (int, error) { return len(body), nil }
