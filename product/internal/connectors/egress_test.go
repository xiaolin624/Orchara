package connectors

import (
	"context"
	"encoding/json"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

// newLocalPolicy builds an allow_private policy against a real local HTTP
// server. The brief explicitly allows loopback for tests; production defaults
// to allow_private=false.
func newLocalPolicy(t *testing.T, serverURL, method, pathPrefix string) *Policy {
	t.Helper()
	normalized, problems := normalizePolicyInput(Input{
		Origin: serverURL, Method: method, PathPrefix: pathPrefix,
		SecretRef: "env:T048_TEST_SECRET", SecretHeader: "X-Connector-Secret",
		RiskClass: "read", AllowPrivate: true,
		ResponseSchema: map[string]any{
			"type": "object", "required": []any{"ok"},
			"properties": map[string]any{"ok": map[string]any{"type": "boolean"}},
		},
	})
	if len(problems) != 0 {
		t.Fatalf("policy setup: %v", problems)
	}
	policy := policyFromInput(normalized)
	approved := time.Now()
	policy.ApprovedAt = &approved
	policy.ID = "00000000-0000-4000-8000-000000000001"
	return policy
}

func testEgress() *Egress {
	return &Egress{
		Lookup: func(name string) (string, bool) {
			if name == "T048_TEST_SECRET" {
				return "s3cr3t-value", true
			}
			return "", false
		},
	}
}

// A real local server is reached, the secret is sent in exactly the configured
// header and never appears in the response projection.
func TestEgressCallsTheLocalServerAndSendsTheSecretOnlyInItsHeader(t *testing.T) {
	var seenSecret, seenHeader string
	var calls atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		seenSecret = r.Header.Get("X-Connector-Secret")
		seenHeader = r.Header.Get("Authorization")
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, `{"ok":true,"value":42}`)
	}))
	defer server.Close()

	policy := newLocalPolicy(t, server.URL, "GET", "/v1")
	response, err := testEgress().Execute(context.Background(), policy, Call{Path: "/v1/items"})
	if err != nil {
		t.Fatalf("egress: %v", err)
	}
	if calls.Load() != 1 || seenSecret != "s3cr3t-value" {
		t.Fatalf("server saw secret=%q calls=%d", seenSecret, calls.Load())
	}
	if seenHeader != "" {
		t.Fatalf("the secret leaked into an unconfigured header: %q", seenHeader)
	}
	if response.Status != 200 {
		t.Fatalf("status=%d", response.Status)
	}
	if strings.Contains(string(response.Body), "s3cr3t-value") {
		t.Fatal("the secret appears in the response body")
	}
}

// Without allow_private the loopback server must never be dialed: the refusal
// happens before the socket is opened, so the handler counter stays zero.
func TestEgressRefusesLoopbackBeforeDialingWhenPrivateIsNotAllowed(t *testing.T) {
	var calls atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
	}))
	defer server.Close()

	policy := newLocalPolicy(t, server.URL, "GET", "/v1")
	policy.AllowPrivate = false
	_, err := testEgress().Execute(context.Background(), policy, Call{Path: "/v1/items"})
	if err == nil {
		t.Fatal("loopback was reached with allow_private=false")
	}
	if code(err) != CodeTargetForbidden {
		t.Fatalf("code=%q, want %q", code(err), CodeTargetForbidden)
	}
	if calls.Load() != 0 {
		t.Fatalf("the server was dialed %d times; the guard must refuse before dialing", calls.Load())
	}
	if strings.Contains(err.Error(), "s3cr3t") {
		t.Fatal("the refusal leaked the secret")
	}
}

// The metadata address is refused even with allow_private=true.
func TestEgressAlwaysRefusesTheMetadataAddress(t *testing.T) {
	policy := newLocalPolicy(t, "http://169.254.169.254", "GET", "/latest")
	_, err := testEgress().Execute(context.Background(), policy, Call{Path: "/latest/meta-data"})
	if err == nil || code(err) != CodeTargetForbidden {
		t.Fatalf("metadata address was not refused: %v", err)
	}
}

// A DNS name that resolves to a private address is refused with
// allow_private=false, and accepted (against the resolved address) when the
// administrator opted in. One resolver call proves the address set is pinned.
func TestEgressResolvesEveryAddressAndPinsTheValidatedOne(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !strings.HasPrefix(r.Host, "connector.test:") {
			t.Errorf("Host was not preserved: %q", r.Host)
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, `{"ok":true}`)
	}))
	defer server.Close()
	port := server.Listener.Addr().(*net.TCPAddr).Port
	origin := "http://connector.test:" + itoa(port)

	var resolverCalls atomic.Int32
	egress := testEgress()
	egress.Resolver = ResolverFunc(func(ctx context.Context, host string) ([]netipAddr, error) {
		resolverCalls.Add(1)
		return []netipAddr{mustAddr(t, "127.0.0.1")}, nil
	})

	refused := newLocalPolicy(t, origin, "GET", "/v1")
	refused.AllowPrivate = false
	if _, err := egress.Execute(context.Background(), refused, Call{Path: "/v1/items"}); err == nil ||
		code(err) != CodeTargetForbidden {
		t.Fatalf("private DNS answer was not refused: %v", err)
	}
	if resolverCalls.Load() != 1 {
		t.Fatalf("resolver calls=%d, want 1", resolverCalls.Load())
	}

	allowed := newLocalPolicy(t, origin, "GET", "/v1")
	if _, err := egress.Execute(context.Background(), allowed, Call{Path: "/v1/items"}); err != nil {
		t.Fatalf("allow_private policy did not reach the pinned address: %v", err)
	}
	if resolverCalls.Load() != 2 {
		t.Fatalf("resolver calls=%d, want 2", resolverCalls.Load())
	}
}

// A redirect is refused and never followed to an internal host. The handler
// sees exactly one request and the second (metadata) server is never dialed.
func TestEgressRefusesRedirectsInsteadOfFollowingThem(t *testing.T) {
	var metadataCalls atomic.Int32
	metadata := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		metadataCalls.Add(1)
	}))
	defer metadata.Close()
	var redirects atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		redirects.Add(1)
		http.Redirect(w, r, metadata.URL+"/latest", http.StatusFound)
	}))
	defer server.Close()

	policy := newLocalPolicy(t, server.URL, "GET", "/v1")
	_, err := testEgress().Execute(context.Background(), policy, Call{Path: "/v1/items"})
	if err == nil || code(err) != CodeRedirectRefused {
		t.Fatalf("redirect was not refused: %v", err)
	}
	if redirects.Load() != 1 || metadataCalls.Load() != 0 {
		t.Fatalf("redirects=%d metadata_calls=%d; the hop must not be followed", redirects.Load(), metadataCalls.Load())
	}
}

// The response schema is enforced before the body can become a receipt.
func TestEgressValidatesTheResponseSchemaAndCapsTheBody(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = io.WriteString(w, `{"ok":"not-a-boolean"}`)
	}))
	defer server.Close()
	policy := newLocalPolicy(t, server.URL, "GET", "/v1")
	if _, err := testEgress().Execute(context.Background(), policy, Call{Path: "/v1/items"}); err == nil ||
		code(err) != CodeResponseInvalid {
		t.Fatalf("invalid response schema not refused: %v", err)
	}

	big := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = io.WriteString(w, `{"ok":true,"pad":"`+strings.Repeat("x", 4096)+`"}`)
	}))
	defer big.Close()
	tight := newLocalPolicy(t, big.URL, "GET", "/v1")
	egress := testEgress()
	egress.MaxResponseBytes = 128
	if _, err := egress.Execute(context.Background(), tight, Call{Path: "/v1/items"}); err == nil ||
		code(err) != CodeResponseTooLarge {
		t.Fatalf("oversized response not refused: %v", err)
	}
}

func mustJSON(t *testing.T, raw []byte) any {
	t.Helper()
	var value any
	if err := json.Unmarshal(raw, &value); err != nil {
		t.Fatalf("invalid JSON: %v", err)
	}
	return value
}
