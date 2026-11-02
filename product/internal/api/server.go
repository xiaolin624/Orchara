package api

import (
	"errors"
	"log"
	"net/http"
	"path/filepath"
	"strconv"
	"time"

	"orchara/platform/internal/artifacts"
	"orchara/platform/internal/background"
	"orchara/platform/internal/catalog"
	"orchara/platform/internal/compiler"
	"orchara/platform/internal/config"
	"orchara/platform/internal/execution"
	"orchara/platform/internal/identity"
	"orchara/platform/internal/modelproxy"
	"orchara/platform/internal/objectstore"
	"orchara/platform/internal/policy"
)

// Server serves the F01 identity endpoints, the F02A draft/version paths, the
// F00B background-job surface and the static pages that drive them.
type Server struct {
	service   *identity.Service
	policy    *policy.Authorizer
	catalog   *catalog.Reader
	jobs      *background.Controller
	artifacts *background.ArtifactStore
	// execution owns the F03 claim/heartbeat/attempt-exit primitive.
	execution *execution.Service
	// userArtifacts is the T028 user-facing artifact service (distinct from the
	// job-scoped ArtifactStore above: different purposes, different ACL).
	userArtifacts *artifacts.Service
	models        *modelproxy.Service
	objects       *objectstore.Store
	signer        *objectstore.Signer
	// manifest carries the deployment inputs (worker image digest, runtime lock
	// path) the validate endpoint needs. Absent inputs are not a startup error;
	// the endpoint answers 503 compiler_unconfigured instead.
	manifest     compiler.Inputs
	webDir       string
	secureCookie bool
	logger       *log.Logger
	limiter      *loginLimiter
}

// Deps is everything the HTTP surface needs from the process that owns it.
//
// It is a struct rather than a positional argument list because the surface has
// grown with each ticket (identity, catalog, background jobs, object links,
// model proxy, manifest inputs) and a ten-argument constructor makes a swapped
// pair of same-typed arguments compile silently. Named fields keep the wiring
// legible and let a caller opt out of optional pieces explicitly.
type Deps struct {
	// Service is the identity service; required.
	Service *identity.Service
	// WebDir is where the static pages and assets are served from (ORCHARA_WEB_DIR).
	WebDir string
	// Profile selects cookie security and is recorded for the process profile.
	Profile config.Profile
	// Logger receives request-independent diagnostics; nil means discard.
	Logger *log.Logger
	// Objects/Signer may be nil: every operation that needs bytes or a signed
	// link then fails closed instead of skipping verification.
	Objects *objectstore.Store
	Signer  *objectstore.Signer
	// Providers is the model-proxy provider set built from deployment configuration.
	Providers *modelproxy.Providers
	// PublicURL is the absolute base URL signed links are minted against.
	PublicURL string
	// RetryBackoff is the background-job retry base delay.
	RetryBackoff time.Duration
	// ExecutionLeaseTTL is the F03 claim lease length. Zero falls back to
	// execution.DefaultLeaseTTL (30s); a value outside the documented bounds is
	// normalised by the execution service.
	ExecutionLeaseTTL time.Duration
	// Manifest carries the deployment inputs the validate endpoint needs; absent
	// inputs are a 503, not a startup error.
	Manifest compiler.Inputs
	// Notifier is the best-effort background wakeup hook. Nil means NopNotifier:
	// losing every notification is a supported state because claim polls PostgreSQL.
	Notifier background.Notifier
}

// New builds the HTTP surface. See Deps for each dependency's contract.
func New(deps Deps) *Server {
	logger := deps.Logger
	if logger == nil {
		logger = log.New(nullWriter{}, "", 0)
	}
	notifier := deps.Notifier
	if notifier == nil {
		notifier = background.NopNotifier{}
	}
	authorizer := policy.New(deps.Service.DB)
	return &Server{
		service:       deps.Service,
		policy:        authorizer,
		catalog:       catalog.New(deps.Service.DB, authorizer),
		jobs:          background.New(deps.Service.DB, deps.Objects, deps.RetryBackoff, notifier),
		artifacts:     background.NewArtifactStore(deps.Service.DB, deps.Objects, deps.Signer, deps.PublicURL, notifier),
		execution:     execution.New(deps.Service.DB, deps.Objects, deps.Signer, deps.PublicURL, deps.ExecutionLeaseTTL),
		userArtifacts: artifacts.New(deps.Service.DB, deps.Objects, deps.Signer, deps.PublicURL),
		models:        modelproxy.NewService(deps.Service.DB, deps.Objects, deps.Providers),
		objects:       deps.Objects,
		signer:        deps.Signer,
		manifest:      deps.Manifest,
		webDir:        deps.WebDir,
		secureCookie:  SecureCookie(deps.Profile),
		logger:        logger,
		limiter:       newLoginLimiter(loginFailureLimit, loginFailureWindow, time.Now),
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

	// F02A/T025: compile the current draft, resolve its fixed dependency closure
	// and return located errors. A valid draft also returns the server-side
	// manifest hash in X-Manifest-SHA256 (the 200 schema has no field for it; the
	// extension is documented in ADR 0004 §26) and seals the canonical bytes.
	mux.HandleFunc("POST /api/v1/applications/{application_id}/validate", s.handleValidateApplication)

	// F02A/T027: private debug submission (owner-only, Idempotency-Key required,
	// one frozen snapshot + queued run + ready start job) and the owner-only run
	// read. The fixed manifest hash travels in X-Manifest-SHA256 on both, because
	// the contract Run schema has no field for it (ADR 0004 §38).
	mux.HandleFunc("POST /api/v1/applications/{application_id}/debug-runs", s.handleDebugDraft)
	mux.HandleFunc("GET /api/v1/runs", s.handleListRuns)
	mux.HandleFunc("GET /api/v1/runs/{run_id}", s.handleGetRun)

	// F00B/T022: the redacted diagnostic read for a background job's owner or an
	// administrator. The internal claim/heartbeat/result routes live on the
	// separate mTLS listener (RegisterInternal).
	mux.HandleFunc("GET /api/v1/background-jobs/{job_id}", s.handleGetBackgroundJob)

	// F00B/T026: the evaluation business state and its immutable report. Both
	// are owner-or-admin (everyone else 404) and project only contract fields;
	// the report read never returns the report body or an artifact path.
	mux.HandleFunc("GET /api/v1/evaluations/{evaluation_id}", s.handleGetEvaluation)
	mux.HandleFunc("GET /api/v1/evaluation-reports/{report_id}", s.handleGetEvaluationReport)

	// F00B/T024: presigned object transfers. There is deliberately no session
	// requirement here (the HMAC token is the whole authority) and no list
	// endpoint: PUT can only fill a reserved staging key, GET can only read a
	// sealed object, and every bad link is a 404.
	mux.HandleFunc("PUT /object-links/{token}", s.handleObjectLinkPut)
	mux.HandleFunc("GET /object-links/{token}", s.handleObjectLinkGet)

	// F05/T028: user uploads and the owner-only artifact reads. The sealed key
	// is preallocated on the row and never a PUT target; completion only moves
	// uploading -> scanning and enqueues the shared finalize handler.
	mux.HandleFunc("POST /api/v1/artifacts/uploads", s.handleCreateUpload)
	mux.HandleFunc("POST /api/v1/artifacts/{artifact_id}/complete", s.handleCompleteUpload)
	mux.HandleFunc("GET /api/v1/artifacts/{artifact_id}", s.handleGetArtifact)
	mux.HandleFunc("GET /api/v1/artifacts/{artifact_id}/download", s.handleDownloadArtifact)
	mux.HandleFunc("GET /api/v1/artifacts/{artifact_id}/content", s.handleArtifactContent)

	mux.HandleFunc("GET /login", s.staticPage("login.html"))
	mux.HandleFunc("GET /admin", s.staticPage("admin.html"))
	mux.HandleFunc("GET /activate", s.staticPage("activate.html"))
	mux.HandleFunc("GET /apps", s.staticPage("apps.html"))
	// F05/T028: the attachment upload page. Like every static route, the path is
	// a compile-time constant and no request input participates in it.
	mux.HandleFunc("GET /attachments", s.staticPage("attachments.html"))
	// F02A/T023: the editor is served per application so the page never needs a
	// query string (the path segment is the application id the editor edits).
	mux.HandleFunc("GET /apps/{application_id}/edit", s.staticPage("editor.html"))
	// F02A/T027: the debug entry is served per application for the same reason.
	mux.HandleFunc("GET /apps/{application_id}/debug", s.staticPage("debug.html"))
	// F03/T029: the owner-only runs view. Like every static route, the path is a
	// compile-time constant and no request input participates in it.
	mux.HandleFunc("GET /runs", s.staticPage("runs.html"))
	mux.HandleFunc("GET /assets/app.js", s.staticPage(filepath.Join("assets", "app.js")))
	mux.HandleFunc("GET /assets/editor.js", s.staticPage(filepath.Join("assets", "editor.js")))
	mux.HandleFunc("GET /assets/debug.js", s.staticPage(filepath.Join("assets", "debug.js")))
	mux.HandleFunc("GET /assets/attachments.js", s.staticPage(filepath.Join("assets", "attachments.js")))
	mux.HandleFunc("GET /assets/runs.js", s.staticPage(filepath.Join("assets", "runs.js")))
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
