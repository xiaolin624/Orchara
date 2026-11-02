package api

import (
	"errors"
	"log"
	"net/http"
	"path/filepath"
	"strconv"
	"time"

	"orchara/platform/internal/background"
	"orchara/platform/internal/catalog"
	"orchara/platform/internal/config"
	"orchara/platform/internal/identity"
	"orchara/platform/internal/objectstore"
	"orchara/platform/internal/policy"
)

// Server serves the F01 identity endpoints and the static pages that drive them.
type Server struct {
	service      *identity.Service
	policy       *policy.Authorizer
	catalog      *catalog.Reader
	jobs         *background.Controller
	webDir       string
	secureCookie bool
	logger       *log.Logger
	limiter      *loginLimiter
}

// New builds the HTTP surface. webDir is where admin.html, login.html,
// activate.html and assets/app.js are served from (ORCHARA_WEB_DIR).
//
// The policy authorizer, the catalog read model and the background controller
// share the identity service's database handle; constructing them here keeps
// cmd/api's wiring unchanged and makes the decision cache a per-process property
// of this server. objects may be nil: the server then starts but refuses any
// operation that would need object bytes (artifact_finalize activation and the
// handler's sealed write) instead of silently skipping verification.
func New(
	service *identity.Service,
	webDir string,
	profile config.Profile,
	logger *log.Logger,
	objects *objectstore.Store,
	retryBackoff time.Duration,
) *Server {
	if logger == nil {
		logger = log.New(nullWriter{}, "", 0)
	}
	authorizer := policy.New(service.DB)
	return &Server{
		service:      service,
		policy:       authorizer,
		catalog:      catalog.New(service.DB, authorizer),
		jobs:         background.New(service.DB, objects, retryBackoff),
		webDir:       webDir,
		secureCookie: SecureCookie(profile),
		logger:       logger,
		limiter:      newLoginLimiter(loginFailureLimit, loginFailureWindow, time.Now),
	}
}

// Register adds the identity routes to mux.
//
// Every authenticated route goes through Server.authorize, so the CSRF and role
// rules live in one place; /auth/login and /auth/activate are unauthenticated by
// contract and therefore have no session (and no CSRF token) to check.
func (s *Server) Register(mux *http.ServeMux) {
	mux.HandleFunc("POST /api/v1/auth/login", s.handleLogin)
	mux.HandleFunc("POST /api/v1/auth/logout", s.handleLogout)
	mux.HandleFunc("GET /api/v1/auth/me", s.handleMe)
	mux.HandleFunc("POST /api/v1/auth/activate", s.handleActivate)
	mux.HandleFunc("POST /api/v1/admin/users", s.handleInviteUser)

	// T020: named service accounts, scoped bearer keys and the personal-data
	// boundary of the memory endpoint F09 will implement.
	mux.HandleFunc("POST /api/v1/admin/service-accounts", s.handleCreateServiceAccount)
	mux.HandleFunc("POST /api/v1/admin/api-keys", s.handleCreateAPIKey)
	mux.HandleFunc("DELETE /api/v1/admin/api-keys/{key_id}", s.handleRevokeAPIKey)
	mux.HandleFunc("GET /api/v1/memories", s.handleListMemories)

	// T021: disable/enable a principal. Disabling revokes the principal's
	// sessions and API keys (including the keys of service accounts it is
	// responsible for), bumps the policy epoch and audits, in one transaction.
	mux.HandleFunc("PATCH /api/v1/admin/principals/{principal_id}", s.handleUpdatePrincipal)

	// T019: resource grants, role updates and the ACL-filtered applications read.
	mux.HandleFunc("POST /api/v1/admin/grants", s.handleGrant)
	mux.HandleFunc("DELETE /api/v1/admin/grants/{grant_id}", s.handleRevokeGrant)
	mux.HandleFunc("PUT /api/v1/admin/users/{principal_id}/roles", s.handleSetRoles)
	mux.HandleFunc("GET /api/v1/applications", s.handleListApplications)
	mux.HandleFunc("GET /api/v1/applications/{application_id}", s.handleGetApplication)

	// F02A/T023: the creator's draft path and the immutable version read model.
	// The version list/detail join applications and apply the same ACL as the
	// application read; the draft write is owner-only with ETag/If-Match.
	mux.HandleFunc("POST /api/v1/applications", s.handleCreateApplication)
	mux.HandleFunc("GET /api/v1/applications/{application_id}/draft", s.handleGetDraft)
	mux.HandleFunc("PUT /api/v1/applications/{application_id}/draft", s.handleSaveDraft)
	mux.HandleFunc("GET /api/v1/applications/{application_id}/versions", s.handleListVersions)
	mux.HandleFunc("GET /api/v1/versions/{version_id}", s.handleGetVersion)

	// F00B/T022: the redacted diagnostic read for a background job's owner or an
	// administrator. The internal claim/heartbeat/result routes live on the
	// separate mTLS listener (RegisterInternal).
	mux.HandleFunc("GET /api/v1/background-jobs/{job_id}", s.handleGetBackgroundJob)

	mux.HandleFunc("GET /login", s.staticPage("login.html"))
	mux.HandleFunc("GET /admin", s.staticPage("admin.html"))
	mux.HandleFunc("GET /activate", s.staticPage("activate.html"))
	mux.HandleFunc("GET /apps", s.staticPage("apps.html"))
	// F02A/T023: the editor is served per application so the page never needs a
	// query string (the path segment is the application id the editor edits).
	mux.HandleFunc("GET /apps/{application_id}/edit", s.staticPage("editor.html"))
	mux.HandleFunc("GET /assets/app.js", s.staticPage(filepath.Join("assets", "app.js")))
	mux.HandleFunc("GET /assets/editor.js", s.staticPage(filepath.Join("assets", "editor.js")))
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

	// Throttle before spending an argon2id verification. The key is
	// (lowercased login, peer address); a tripped key is refused even when the
	// password is correct, so the refusal itself carries no signal about the
	// password.
	throttleKey := loginKeyFor(request.Login, r.RemoteAddr)
	if blocked, retryAfter := s.limiter.blocked(throttleKey); blocked {
		w.Header().Set("Retry-After", strconv.Itoa(int(retryAfter/time.Second)))
		writeError(w, r, http.StatusTooManyRequests, "rate_limited",
			"too many failed sign-in attempts; try again later")
		return
	}

	session, err := s.service.Login(r.Context(), request.Login, request.Password)
	switch {
	case errors.Is(err, identity.ErrInvalidCredentials):
		s.limiter.fail(throttleKey)
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

	s.limiter.succeed(throttleKey)
	http.SetCookie(w, newSessionCookie(session.Token, s.secureCookie))
	writeJSON(w, http.StatusOK, sessionResponse{
		PrincipalID: session.PrincipalID,
		CSRFToken:   session.CSRFToken,
		ExpiresAt:   session.ExpiresAt.UTC().Format(time.RFC3339),
	})
}

// identityView is the contract's IdentityView: exactly these five fields, with
// csrf_token null for a credential that does not use browser CSRF.
type identityView struct {
	PrincipalID string   `json:"principal_id"`
	Kind        string   `json:"kind"`
	Roles       []string `json:"roles"`
	CSRFToken   *string  `json:"csrf_token"`
	ExpiresAt   string   `json:"expires_at"`
}

func (s *Server) handleMe(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "")
	if !ok {
		return
	}
	writeJSON(w, http.StatusOK, identityViewFor(caller))
}

// identityViewFor projects an identity onto the contract's IdentityView.
//
// csrf_token is the plaintext token only for a session cookie, where it is
// recomputed from the presented cookie inside Authenticate, so a page refresh
// restores a working identity without any client-side memory and without the
// plaintext ever being stored server-side. Any other credential (an API key,
// from T020) reports null: it is not attached by a browser and has no CSRF
// token to hand out.
func identityViewFor(caller *identity.Identity) identityView {
	var csrfToken *string
	if caller.Method == identity.AuthMethodSessionCookie && caller.CSRFToken != "" {
		token := caller.CSRFToken
		csrfToken = &token
	}
	roles := caller.Roles
	if roles == nil {
		// The contract fixes roles as an array; JSON null would be a different
		// type. A service principal simply has no roles.
		roles = []string{}
	}
	return identityView{
		PrincipalID: caller.PrincipalID,
		Kind:        caller.Kind,
		Roles:       roles,
		CSRFToken:   csrfToken,
		ExpiresAt:   caller.ExpiresAt.UTC().Format(time.RFC3339),
	}
}

func (s *Server) handleLogout(w http.ResponseWriter, r *http.Request) {
	// A forged logout is still a state change (it ends someone's session), so
	// it needs the same CSRF proof as any other write. authorize() writes 403
	// csrf_invalid and leaves the session untouched when the proof is missing.
	caller, ok := s.authorize(w, r, "")
	if !ok {
		return
	}

	// Only a browser session has a server-side row to revoke. An API key is not
	// a session: it has no CSRF token and its lifecycle is the admin revoke
	// operation, so this endpoint refuses a bearer caller instead of clearing a
	// cookie that was never used and pretending it revoked a credential.
	if caller.Method != identity.AuthMethodSessionCookie || caller.SessionHash == "" {
		writeError(w, r, http.StatusUnauthorized, "unauthenticated",
			"signing out requires a browser session; revoke an API key through the admin API")
		return
	}
	if _, err := s.service.Logout(r.Context(), caller.SessionHash, caller.PrincipalID); err != nil {
		s.internalError(w, r, err)
		return
	}
	http.SetCookie(w, clearedSessionCookie(s.secureCookie))
	// No body, but still auth data: the response must not be cached.
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(http.StatusNoContent)
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
	// Invitation is a state-changing admin endpoint: one helper enforces the
	// session, the CSRF proof and the admin role before anything is read or
	// written.
	caller, ok := s.authorize(w, r, "admin")
	if !ok {
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

func validationDetails(err error) map[string]any {
	var identityValidation *identity.ValidationError
	if errors.As(err, &identityValidation) && identityValidation.Field != "" {
		return map[string]any{"field": identityValidation.Field}
	}
	var policyValidation *policy.ValidationError
	if errors.As(err, &policyValidation) && policyValidation.Field != "" {
		return map[string]any{"field": policyValidation.Field}
	}
	var catalogValidation *catalog.ValidationError
	if errors.As(err, &catalogValidation) && catalogValidation.Field != "" {
		return map[string]any{"field": catalogValidation.Field}
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
