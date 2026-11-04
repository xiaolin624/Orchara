package connectors

// The controlled-egress guard. This file turns a validated policy plus one
// invocation into exactly one real HTTP request:
//
//	origin + path (+ schema-approved query)
//
// Every decision that could let a caller reach an unintended target happens
// before the socket is opened:
//
//  1. the path is a relative path under the registered prefix (no absolute URL,
//     no userinfo, no dot segment, no backslash, no fragment);
//  2. the origin host is resolved and *every* answer is classified; one refused
//     address refuses the call;
//  3. the validated address is pinned in a custom DialContext, so a second DNS
//     answer (rebinding) can never change the connection target; Host/SNI stay
//     the registered origin;
//  4. redirects are refused, not followed; the environment proxy is disabled;
//     request/response bytes and wall time are capped;
//  5. the credential is resolved inside Go and sent only in the registered
//     header; no error, response or receipt path can carry it.
//
// The client never retries. One invokeCapability is one physical HTTP call; the
// capability service records exactly that.

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"net"
	"net/http"
	"net/netip"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"time"

	"orchara/platform/internal/config"
	"orchara/platform/internal/jsonschema"
)

// Default caps. They are deliberately small: a connector response is validated
// data, not a file transfer (artifact upload/download has its own routes).
const (
	DefaultTimeout          = 10 * time.Second
	DefaultMaxResponseBytes = 1 << 20
	DefaultMaxRequestBytes  = 1 << 20
)

// Resolver resolves a registered hostname to every address the caller may be
// connected to. Production uses the process resolver; tests inject a fixed or
// rebinding answer.
type Resolver interface {
	LookupIP(ctx context.Context, host string) ([]netip.Addr, error)
}

// ResolverFunc adapts a function to Resolver.
type ResolverFunc func(ctx context.Context, host string) ([]netip.Addr, error)

// LookupIP implements Resolver.
func (f ResolverFunc) LookupIP(ctx context.Context, host string) ([]netip.Addr, error) {
	return f(ctx, host)
}

// systemResolver is the production resolver: the platform's own lookup, never a
// caller-supplied one.
type systemResolver struct{}

func (systemResolver) LookupIP(ctx context.Context, host string) ([]netip.Addr, error) {
	if addr, err := netip.ParseAddr(host); err == nil {
		return []netip.Addr{addr}, nil
	}
	return net.DefaultResolver.LookupNetIP(ctx, "ip", host)
}

// errRedirectRefused is the CheckRedirect sentinel; it aborts the hop before the
// second request is written.
var errRedirectRefused = errors.New("connector redirects are refused")

// Egress performs the guarded physical call.
//
// Lookup/SecretDir are the deployer credential plumbing (config.Lookup and the
// file-reference root). A nil Resolver means the process resolver. Timeout and
// the byte caps default when zero.
type Egress struct {
	Resolver         Resolver
	Lookup           config.Lookup
	SecretDir        string
	Timeout          time.Duration
	MaxResponseBytes int64
	MaxRequestBytes  int64
}

// Call is one validated invocation: a path under the registered prefix, an
// optional schema-approved query and an optional JSON body.
type Call struct {
	Path  string
	Query url.Values
	Body  any
}

// Response is the receipt-grade projection of the physical answer. Body is
// returned to the capability service for schema validation and sealing; Header
// holds only the allow-listed, non-secret response headers.
type Response struct {
	Status      int
	Header      map[string]string
	Body        []byte
	ContentType string
}

// Execute performs exactly one guarded HTTP request for policy.
func (e *Egress) Execute(ctx context.Context, policy *Policy, call Call) (*Response, error) {
	if policy == nil {
		return nil, failure(422, CodePolicyUnavailable, "the connector policy is not available")
	}
	if policy.Revoked() {
		return nil, failure(403, CodePolicyRevoked, "the connector policy is revoked")
	}
	if !policy.Approved() {
		return nil, failure(422, CodePolicyUnavailable, "the connector policy is not approved")
	}

	origin, err := url.Parse(policy.Origin)
	if err != nil || origin.Host == "" {
		return nil, failure(500, CodePolicyUnavailable, "the connector policy origin is not usable")
	}
	path, inlineQuery, err := resolveInvocationPath(policy.PathPrefix, call.Path)
	if err != nil {
		return nil, validationProblem(Problem{Field: "path", Message: err.Error()})
	}
	query := url.Values{}
	for name, values := range inlineQuery {
		for _, value := range values {
			query.Add(name, value)
		}
	}
	for name, values := range call.Query {
		for _, value := range values {
			query.Add(name, value)
		}
	}
	if len(query) > 0 {
		if !queryAllowed(policy.RequestSchema) {
			return nil, failure(422, CodeRequestInvalid,
				"the connector policy does not declare a query schema; a query may not be sent")
		}
		queryObject := map[string]any{}
		for name, values := range query {
			queryObject[name] = values[len(values)-1]
		}
		querySchema := policy.RequestSchema["properties"].(map[string]any)["query"].(map[string]any)
		if problems := jsonschema.ValidateValue(querySchema, queryObject); len(problems) > 0 {
			return nil, &Error{
				Status: 422, Code: CodeRequestInvalid,
				Message: "the connector query failed validation: " + problems[0].Message,
				Field:   "query" + problems[0].Field,
			}
		}
	}

	target := *origin
	target.Path = path
	target.RawQuery = query.Encode()
	target.Fragment = ""
	target.User = nil

	host := target.Hostname()
	port := target.Port()
	if port == "" {
		if target.Scheme == "https" {
			port = "443"
		} else {
			port = "80"
		}
	}
	pinned, err := e.resolveSafe(ctx, host, policy.AllowPrivate)
	if err != nil {
		return nil, err
	}

	secret, err := resolveSecret(policy.SecretRef, e.SecretDir, e.Lookup)
	if err != nil {
		return nil, failure(503, CodeSecretUnavailable,
			"the connector credential reference could not be resolved")
	}

	body, contentType, err := encodeBody(call.Body, e.maxRequestBytes())
	if err != nil {
		return nil, err
	}
	request, err := http.NewRequestWithContext(ctx, policy.Method, target.String(), body)
	if err != nil {
		return nil, failure(422, CodeRequestInvalid, "the connector request could not be built")
	}
	if contentType != "" {
		request.Header.Set("Content-Type", contentType)
	}
	request.Header.Set("Accept", "application/json")
	// The credential exists only here, in the one configured header.
	request.Header.Set(policy.SecretHeader, secret)

	client := e.httpClient(pinned, port)
	response, err := client.Do(request)
	if err != nil {
		if errors.Is(err, errRedirectRefused) {
			return nil, failure(422, CodeRedirectRefused,
				"the connector response was a redirect; redirects are refused and never followed")
		}
		if errors.Is(err, context.DeadlineExceeded) || errors.Is(err, context.Canceled) {
			return nil, failure(504, "connector_timeout", "the connector call timed out")
		}
		// Deliberately not the transport error text: it can contain an internal
		// address. The code is the whole owner-facing fact.
		return nil, failure(502, "connector_transport_error", "the connector connection failed")
	}
	defer func() { _ = response.Body.Close() }()

	limited := io.LimitReader(response.Body, e.maxResponseBytes()+1)
	payload, err := io.ReadAll(limited)
	if err != nil {
		return nil, failure(502, "connector_transport_error", "the connector response could not be read")
	}
	if int64(len(payload)) > e.maxResponseBytes() {
		return nil, failure(502, CodeResponseTooLarge, "the connector response exceeded the byte cap")
	}
	contentType = strings.ToLower(strings.TrimSpace(response.Header.Get("Content-Type")))
	if status := response.StatusCode; status < 200 || status > 299 {
		observed, _ := AsError(failure(502, CodeHTTPError, "the connector endpoint returned a non-success status"))
		observed.Details = map[string]any{"status": status}
		return nil, observed
	}
	if len(policy.ResponseSchema) > 0 {
		if !strings.Contains(contentType, "json") {
			return nil, failure(422, CodeResponseInvalid,
				"the connector policy requires a JSON response but the endpoint returned another content type")
		}
		var decoded any
		decoder := json.NewDecoder(bytes.NewReader(payload))
		decoder.UseNumber()
		if err := decoder.Decode(&decoded); err != nil {
			return nil, failure(422, CodeResponseInvalid,
				"the connector response is not valid JSON")
		}
		if problems := jsonschema.ValidateValue(policy.ResponseSchema, decoded); len(problems) > 0 {
			return nil, failure(422, CodeResponseInvalid,
				"the connector response failed its schema: "+problems[0].Message)
		}
	}
	return &Response{
		Status:      response.StatusCode,
		Header:      selectedHeaders(response.Header),
		Body:        payload,
		ContentType: contentType,
	}, nil
}

// Execute performs the guarded call through the service's configured egress
// seam (the production guard unless a test injected one).
func (s *Service) Execute(ctx context.Context, policy *Policy, call Call) (*Response, error) {
	return s.egress().Execute(ctx, policy, call)
}

// resolveSafe resolves the host and refuses the call if any answer is a
// forbidden target. The returned address is the connection pin.
func (e *Egress) resolveSafe(ctx context.Context, host string, allowPrivate bool) ([]netip.Addr, error) {
	resolver := e.Resolver
	if resolver == nil {
		resolver = systemResolver{}
	}
	addresses, err := resolver.LookupIP(ctx, host)
	if err != nil || len(addresses) == 0 {
		return nil, failure(422, CodeTargetForbidden,
			"the connector origin could not be resolved to a permitted address")
	}
	for _, address := range addresses {
		if reason := addressRefusal(address, allowPrivate); reason != "" {
			return nil, &Error{
				Status: 422, Code: CodeTargetForbidden,
				Message: "the connector origin resolves to a forbidden address (" + reason + ")",
			}
		}
	}
	return addresses, nil
}

// addressRefusal is the SSRF classification. It returns a short reason for a
// refused address and "" for a permitted one. allow_private only widens the
// loopback/RFC1918 set: link-local/metadata, multicast, unspecified and
// 0.0.0.0/8-style reserved answers are always refused.
func addressRefusal(address netip.Addr, allowPrivate bool) string {
	if !address.IsValid() {
		return "not a valid address"
	}
	address = address.Unmap()
	if address.IsUnspecified() {
		return "unspecified"
	}
	if address.IsMulticast() {
		return "multicast"
	}
	if address.IsLinkLocalUnicast() || address.IsLinkLocalMulticast() {
		return "link-local"
	}
	if address.IsInterfaceLocalMulticast() {
		return "multicast"
	}
	if isMetadataAddress(address) {
		return "cloud metadata"
	}
	if address.IsLoopback() || address.IsPrivate() {
		if allowPrivate {
			return ""
		}
		return "private or loopback address"
	}
	if !address.IsGlobalUnicast() {
		return "reserved address"
	}
	return ""
}

// isMetadataAddress matches the well-known cloud metadata endpoints. The
// link-local check already covers 169.254.169.254, but naming it explicitly
// keeps the rule visible and covers IPv6 metadata forms.
func isMetadataAddress(address netip.Addr) bool {
	return address.Is4() && address == netip.MustParseAddr("169.254.169.254")
}

// httpClient builds a client whose dialer ignores DNS and connects to the pinned
// address. Proxy is nil: the environment's HTTP_PROXY must not decide where a
// connector call goes. Redirects abort on the first 3xx.
func (e *Egress) httpClient(pinned []netip.Addr, port string) *http.Client {
	dialer := &net.Dialer{Timeout: 5 * time.Second, KeepAlive: 30 * time.Second}
	pinnedAddresses := make([]string, 0, len(pinned))
	for _, address := range pinned {
		pinnedAddresses = append(pinnedAddresses, net.JoinHostPort(address.String(), port))
	}
	transport := &http.Transport{
		Proxy: nil,
		DialContext: func(ctx context.Context, network, _ string) (net.Conn, error) {
			// Every address was validated before this dialer was built; try them in
			// the resolver's order without a second DNS lookup (rebinding is impossible).
			var lastErr error
			for _, address := range pinnedAddresses {
				connection, err := dialer.DialContext(ctx, "tcp", address)
				if err == nil {
					return connection, nil
				}
				lastErr = err
			}
			return nil, lastErr
		},
		TLSHandshakeTimeout: 5 * time.Second,
		DisableKeepAlives:   true,
		ForceAttemptHTTP2:   false,
	}
	timeout := e.Timeout
	if timeout <= 0 {
		timeout = DefaultTimeout
	}
	return &http.Client{
		Transport: transport,
		Timeout:   timeout,
		CheckRedirect: func(*http.Request, []*http.Request) error {
			return errRedirectRefused
		},
	}
}

func (e *Egress) maxResponseBytes() int64 {
	if e.MaxResponseBytes > 0 {
		return e.MaxResponseBytes
	}
	return DefaultMaxResponseBytes
}

func (e *Egress) maxRequestBytes() int64 {
	if e.MaxRequestBytes > 0 {
		return e.MaxRequestBytes
	}
	return DefaultMaxRequestBytes
}

// resolveInvocationPath validates that raw is a relative path under prefix and
// splits any inline query. It refuses everything that could name another host or
// escape the namespace: absolute URLs, "//host", backslashes, fragments, dot
// segments (including percent-encoded ones) and control characters.
//
// The prefix is a path *segment* namespace, not a string prefix: `/v1` admits
// `/v1` and `/v1/...`, but never `/v10/...` or `/v1beta/...`. Without that
// boundary a policy registered for one API path would silently authorize a
// sibling path it never declared.
func resolveInvocationPath(prefix, raw string) (string, url.Values, error) {
	prefix = strings.TrimSpace(prefix)
	if prefix == "" {
		return "", nil, errors.New("the policy has no registered path prefix")
	}
	for len(prefix) > 1 && strings.HasSuffix(prefix, "/") {
		prefix = strings.TrimSuffix(prefix, "/")
	}
	path := strings.TrimSpace(raw)
	if path == "" {
		return "", nil, errors.New("path is required")
	}
	if !strings.HasPrefix(path, "/") || strings.HasPrefix(path, "//") {
		return "", nil, errors.New("path must be a relative path starting with a single /")
	}
	if strings.ContainsAny(path, "\\#\t\r\n") {
		return "", nil, errors.New("path may not contain a backslash, fragment or control character")
	}
	pathPart, rawQuery, hasQuery := strings.Cut(path, "?")
	decoded, err := url.PathUnescape(pathPart)
	if err != nil {
		return "", nil, errors.New("path is not valid URL encoding")
	}
	if containsDotSegment(decoded) || strings.Contains(decoded, "\\") {
		return "", nil, errors.New("path may not contain dot segments")
	}
	if strings.Contains(decoded, "//") {
		return "", nil, errors.New("path may not contain //")
	}
	if !pathWithinPrefix(decoded, prefix) {
		return "", nil, errors.New("path is outside the registered path prefix")
	}
	if len(decoded) > len(prefix)+4096 {
		return "", nil, errors.New("path is too long")
	}
	query := url.Values{}
	if hasQuery {
		if rawQuery == "" {
			return "", nil, errors.New("path has an empty query")
		}
		parsed, err := url.ParseQuery(rawQuery)
		if err != nil {
			return "", nil, errors.New("path query is not valid URL encoding")
		}
		for name := range parsed {
			if strings.ContainsAny(name, " \t\r\n") {
				return "", nil, errors.New("path query name is not printable")
			}
		}
		for _, values := range parsed {
			for _, value := range values {
				if strings.HasPrefix(value, "//") || strings.ContainsAny(value, "\t\r\n") {
					return "", nil, errors.New("path query value is not a safe relative value")
				}
			}
		}
		query = parsed
	}
	return pathPart, query, nil
}

// pathWithinPrefix enforces the segment boundary. The registered `/v1`
// namespace owns `/v1` itself and `/v1/<child>`, never a sibling whose text
// merely starts with the same characters (`/v10`, `/v1beta`). A root prefix
// owns the whole single-slash namespace (the `//` forms were refused already).
func pathWithinPrefix(path, prefix string) bool {
	if prefix == "/" {
		return strings.HasPrefix(path, "/")
	}
	return path == prefix || strings.HasPrefix(path, prefix+"/")
}

// encodeBody marshals a JSON body under the request byte cap. A GET/DELETE body
// is refused by the caller's method allowlist; this function only encodes.
func encodeBody(body any, maxBytes int64) (io.Reader, string, error) {
	if body == nil {
		return nil, "", nil
	}
	encoded, err := json.Marshal(body)
	if err != nil {
		return nil, "", failure(422, CodeRequestInvalid, "the connector request body is not JSON-serialisable")
	}
	if int64(len(encoded)) > maxBytes {
		return nil, "", failure(422, CodeRequestInvalid, "the connector request body exceeded the byte cap")
	}
	return bytes.NewReader(encoded), "application/json", nil
}

// selectedHeaders keeps only non-secret, useful response headers. Location is
// excluded deliberately (a redirect is already refused).
func selectedHeaders(header http.Header) map[string]string {
	allowed := []string{"Content-Type", "Content-Length", "Date", "Cache-Control", "X-Request-Id"}
	selected := make(map[string]string, len(allowed))
	for _, name := range allowed {
		if value := strings.TrimSpace(header.Get(name)); value != "" {
			selected[strings.ToLower(name)] = value
		}
	}
	return selected
}

// resolveSecret reads a deployer-registered credential. This is the only place
// a credential value exists in the package; no error path includes it or the
// reference.
func resolveSecret(rawRef, secretDir string, lookup config.Lookup) (string, error) {
	reference, err := config.ParseSecretRef(rawRef)
	if err != nil {
		return "", errors.New("invalid credential reference")
	}
	value := string(reference)
	switch {
	case strings.HasPrefix(value, "env:"):
		if lookup == nil {
			lookup = config.FromEnv()
		}
		raw, ok := lookup(strings.TrimPrefix(value, "env:"))
		if !ok || strings.TrimSpace(raw) == "" {
			return "", errors.New("credential is not configured")
		}
		return strings.TrimSpace(raw), nil
	case strings.HasPrefix(value, "file:"):
		root := strings.TrimSpace(secretDir)
		if root == "" {
			return "", errors.New("credential file root is not configured")
		}
		content, err := os.ReadFile(filepath.Join(root, strings.TrimPrefix(value, "file:")))
		if err != nil {
			return "", errors.New("credential file is not readable")
		}
		secret := strings.TrimSpace(string(content))
		if secret == "" {
			return "", errors.New("credential is empty")
		}
		return secret, nil
	default:
		return "", errors.New("unsupported credential reference")
	}
}

// invocationPathForTarget is a small helper the API uses to describe the safe
// target in the owner ledger: origin + path prefix, never params or the secret.
func invocationPathForTarget(policy *Policy) string {
	return policy.Origin + policy.PathPrefix
}
