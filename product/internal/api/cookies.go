package api

import (
	"net/http"
	"time"

	"orchara/platform/internal/config"
	"orchara/platform/internal/identity"
)

// SecureCookie reports whether the session cookie must carry the Secure
// attribute for this profile.
//
// The two production-like profiles terminate TLS; dev-stub and capacity-stub
// speak plain HTTP on loopback for development, where a Secure cookie would be
// dropped by the browser and login would silently never work. An unknown
// profile fails safe (Secure on).
func SecureCookie(profile config.Profile) bool {
	switch profile {
	case config.ProfileDevStub, config.ProfileCapacityStub:
		return false
	case config.ProfileLinuxSandbox, config.ProfileRealModel:
		return true
	default:
		return true
	}
}

// newSessionCookie builds the session cookie. The value is the raw opaque
// token; the database only holds its SHA-256.
func newSessionCookie(value string, secure bool) *http.Cookie {
	return &http.Cookie{
		Name:     identity.SessionCookieName,
		Value:    value,
		Path:     "/",
		HttpOnly: true,
		SameSite: http.SameSiteLaxMode,
		Secure:   secure,
		MaxAge:   int(identity.SessionTTL / time.Second),
	}
}

// clearedSessionCookie deletes the session cookie in the browser.
//
// MaxAge=-1 removes it now; the 1970 Expires does the same for a client that
// only understands Expires. The remaining attributes must match the cookie being
// removed (notably Path=/), otherwise the browser keeps the stale cookie and a
// "logout" silently leaves the session attached.
func clearedSessionCookie(secure bool) *http.Cookie {
	return &http.Cookie{
		Name:     identity.SessionCookieName,
		Value:    "",
		Path:     "/",
		HttpOnly: true,
		SameSite: http.SameSiteLaxMode,
		Secure:   secure,
		MaxAge:   -1,
		Expires:  time.Unix(0, 0).UTC(),
	}
}
