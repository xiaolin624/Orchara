package api

import (
	"errors"
	"net"
	"net/http"
	"strings"

	"orchara/platform/internal/identity"
)

// authzFailure is one refusal in contract terms. Handlers never build these
// themselves: authorize writes the envelope, so every authenticated endpoint
// answers with the same status and code vocabulary.
type authzFailure struct {
	Status  int
	Code    string
	Message string
}

// checkAuthorization is the pure decision behind every authenticated endpoint:
// given the resolved identity, the request method, the presented CSRF header and
// an optional required role, may the request proceed?
//
// It is a pure function on purpose. The rules (which methods are unsafe, which
// credentials need CSRF, which role is required) are the part that must not
// drift between handlers, and this shape makes them testable without a database
// or a socket.
//
// Order is deliberate: CSRF is verified before the role check, so a forged
// request is refused as forged rather than being told anything about the
// caller's authorization.
func checkAuthorization(
	caller *identity.Identity,
	method, presentedCSRF, requiredRole string,
) *authzFailure {
	if requiresCSRF(method) && caller.Method == identity.AuthMethodSessionCookie {
		if !identity.VerifyCSRFToken(caller.CSRFHash, presentedCSRF) {
			return &authzFailure{
				Status:  http.StatusForbidden,
				Code:    "csrf_invalid",
				Message: "a valid X-CSRF-Token header is required for this request",
			}
		}
	}
	if requiredRole != "" && !caller.HasRole(requiredRole) {
		return &authzFailure{
			Status:  http.StatusForbidden,
			Code:    "forbidden",
			Message: requiredRole + " role is required",
		}
	}
	return nil
}

// requiresCSRF reports whether a method changes state. Everything except a
// read-only method needs a CSRF token when the credential is a cookie; a new
// unsafe method is therefore protected by default instead of by remembering to
// add it to a list.
func requiresCSRF(method string) bool {
	switch method {
	case http.MethodGet, http.MethodHead, http.MethodOptions, http.MethodTrace:
		return false
	default:
		return true
	}
}

// authorize is the single authorization path for every authenticated endpoint.
//
//	caller, ok := s.authorize(w, r, "admin")
//	if !ok {
//		return // the contract error is already written
//	}
//
// It performs, in order:
//
//  1. authentication: the session cookie (or, when an Authorization header is
//     present, a bearer API key) is resolved to an *identity.Identity* that
//     records how the caller authenticated (identity.AuthMethod). The rest of
//     this pipeline, and every handler's call site, is credential-agnostic.
//  2. CSRF: see checkAuthorization. A refused request never reaches the handler,
//     so it cannot change state.
//  3. the optional role requirement.
//
// A caller that authenticates but fails CSRF or the role check gets 403; a
// caller that cannot authenticate at all gets the 401/403 code from
// identity.Service.Authenticate (unauthenticated, session_expired,
// session_revoked, principal_disabled).
func (s *Server) authorize(w http.ResponseWriter, r *http.Request, requiredRole string) (*identity.Identity, bool) {
	caller, ok := s.authenticate(w, r)
	if !ok {
		return nil, false
	}
	if failure := checkAuthorization(caller, r.Method, r.Header.Get("X-CSRF-Token"), requiredRole); failure != nil {
		writeError(w, r, failure.Status, failure.Code, failure.Message)
		return nil, false
	}
	return caller, true
}

// requireScope enforces a scope for API-key credentials only.
//
// A session cookie authenticates a human and is not scope-limited: F01 defines
// scopes for keys. A key without the scope is refused before any catalog read,
// which is what makes "scope is checked before the ACL" observable: no grant can
// turn a write-scoped key into a reader.
func requireScope(caller *identity.Identity, scope string) *authzFailure {
	if caller == nil || caller.Method != identity.AuthMethodAPIKey {
		return nil
	}
	if !caller.HasScope(scope) {
		return &authzFailure{
			Status:  http.StatusForbidden,
			Code:    "insufficient_scope",
			Message: "the API key does not carry the " + scope + " scope",
		}
	}
	return nil
}

// parseBearerHeader extracts the secret from the Authorization header values.
//
// present distinguishes "no Authorization header" (fall back to the session
// cookie) from "a header that is not exactly one `Bearer <secret>`" (refused:
// a malformed header must never silently fall back to another credential). The
// scheme is case-insensitive; the secret must be one unbroken token.
func parseBearerHeader(values []string) (secret string, present bool, ok bool) {
	if len(values) == 0 {
		return "", false, false
	}
	if len(values) != 1 {
		// HTTP allows repeated headers; two Authorization values are ambiguous
		// and neither can be trusted to be "the" credential.
		return "", true, false
	}
	value := strings.TrimSpace(values[0])
	const scheme = "bearer "
	if len(value) <= len(scheme) || !strings.EqualFold(value[:len(scheme)], scheme) {
		return "", true, false
	}
	secret = value[len(scheme):]
	if secret == "" || strings.ContainsAny(secret, " \t\r\n") {
		return "", true, false
	}
	return secret, true, true
}

// authenticate resolves the request credential to an identity, writing the
// contract error and returning ok=false when it cannot.
//
// This is the one place a credential type is added. An Authorization header is
// tried first and is authoritative when present: an absent header keeps the
// session-cookie path exactly as before, while a malformed one is refused
// instead of falling back to the cookie. Auth method, CSRF fields and session
// hash come from the resolver, so no handler learns which credential type
// authenticated.
func (s *Server) authenticate(w http.ResponseWriter, r *http.Request) (*identity.Identity, bool) {
	if secret, present, ok := parseBearerHeader(r.Header.Values("Authorization")); present {
		if !ok {
			writeError(w, r, http.StatusUnauthorized, "invalid_key",
				"the Authorization header must be exactly 'Bearer <secret>'")
			return nil, false
		}
		caller, err := s.service.AuthenticateAPIKey(r.Context(), secret)
		switch {
		case errors.Is(err, identity.ErrInvalidKey):
			// Unknown, revoked and expired keys are indistinguishable.
			writeError(w, r, http.StatusUnauthorized, "invalid_key", "that API key is not recognised")
			return nil, false
		case errors.Is(err, identity.ErrPrincipalDisabled):
			writeError(w, r, http.StatusForbidden, "principal_disabled", "this service account is disabled")
			return nil, false
		case errors.Is(err, identity.ErrResponsibleDisabled):
			writeError(w, r, http.StatusForbidden, "responsible_disabled",
				"the responsible user for this service account is disabled")
			return nil, false
		case err != nil:
			s.internalError(w, r, err)
			return nil, false
		}
		return caller, true
	}

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

// remoteHost is the rate-limit key's address part: the accepted TCP peer.
//
// It is deliberately not X-Forwarded-For or any other client-supplied header: a
// caller that can choose its own rate-limit key has no rate limit. Behind a
// reverse proxy every client shares the proxy address until the deployment adds
// a trusted-proxy configuration; that limitation is recorded in the report.
func remoteHost(remoteAddr string) string {
	host, _, err := net.SplitHostPort(strings.TrimSpace(remoteAddr))
	if err != nil {
		return strings.TrimSpace(remoteAddr)
	}
	return host
}
