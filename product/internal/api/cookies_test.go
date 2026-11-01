package api

import (
	"net/http"
	"strings"
	"testing"
	"time"

	"orchara/platform/internal/config"
	"orchara/platform/internal/identity"
)

func TestSecureCookieFollowsTheProfile(t *testing.T) {
	cases := map[config.Profile]bool{
		config.ProfileLinuxSandbox: true,
		config.ProfileRealModel:    true,
		config.ProfileDevStub:      false,
		config.ProfileCapacityStub: false,
		// Fail safe: a profile nobody taught us about must not silently disable
		// transport protection.
		config.Profile("unknown-profile"): true,
	}
	for profile, want := range cases {
		if got := SecureCookie(profile); got != want {
			t.Fatalf("SecureCookie(%q) = %v, want %v", profile, got, want)
		}
	}
}

func TestSessionCookieAttributes(t *testing.T) {
	cookie := newSessionCookie("opaque-value", false)
	if cookie.Name != identity.SessionCookieName {
		t.Fatalf("cookie name = %q, want %q", cookie.Name, identity.SessionCookieName)
	}
	if identity.SessionCookieName != "platform_session" {
		t.Fatalf("cookie name %q drifted from the contract value", identity.SessionCookieName)
	}
	if cookie.Value != "opaque-value" {
		t.Fatalf("cookie value = %q", cookie.Value)
	}
	if cookie.Path != "/" {
		t.Fatalf("cookie path = %q", cookie.Path)
	}
	if !cookie.HttpOnly {
		t.Fatal("the session cookie must be HttpOnly")
	}
	if cookie.SameSite != http.SameSiteLaxMode {
		t.Fatalf("SameSite = %v, want Lax", cookie.SameSite)
	}
	if cookie.Secure {
		t.Fatal("dev-stub must not set Secure: plain HTTP would drop the cookie")
	}
	if want := int((24 * time.Hour) / time.Second); cookie.MaxAge != want {
		t.Fatalf("MaxAge = %d, want %d", cookie.MaxAge, want)
	}

	header := cookie.String()
	for _, attribute := range []string{"HttpOnly", "SameSite=Lax", "Path=/", "Max-Age=86400"} {
		if !strings.Contains(header, attribute) {
			t.Fatalf("Set-Cookie %q lacks %q", header, attribute)
		}
	}
	if strings.Contains(header, "Secure") {
		t.Fatalf("Set-Cookie %q unexpectedly carries Secure", header)
	}

	secureHeader := newSessionCookie("opaque-value", true).String()
	if !strings.Contains(secureHeader, "Secure") {
		t.Fatalf("a production cookie must carry Secure: %q", secureHeader)
	}
}
