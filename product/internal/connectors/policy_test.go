package connectors

import (
	"strings"
	"testing"
)

// The policy validator is the administrator-facing gate: it must reject every
// shape that could later smuggle a host, a query, an unapproved method or a
// credential out of the registered origin. These are the fixed rules from the
// T048 brief §2/§4.
func TestValidatePolicyRejectsAnOriginThatIsNotExactlySchemeHostPort(t *testing.T) {
	bad := []string{
		"",
		"http://",
		"http://user:pass@example.test",
		"http://example.test/path",
		"http://example.test?q=1",
		"http://example.test#frag",
		"ftp://example.test",
		"//example.test",
		"http://exa mple.test",
		"http://example.test:notaport",
	}
	for _, origin := range bad {
		if _, problems := normalizePolicyInput(Input{
			Origin: origin, Method: "GET", PathPrefix: "/v1", SecretRef: "env:T048_KEY",
			SecretHeader: "Authorization", RiskClass: "read",
		}); len(problems) == 0 {
			t.Errorf("origin %q was accepted; it is not exactly scheme://host[:port]", origin)
		}
	}
}

func TestValidatePolicyNormalisesTheOriginAndMethod(t *testing.T) {
	normalized, problems := normalizePolicyInput(Input{
		Origin: " HTTPS://Connector.Example.TEST:443 ", Method: "get", PathPrefix: "/v1/",
		SecretRef: "env:T048_KEY", SecretHeader: "Authorization", RiskClass: "read",
	})
	if len(problems) != 0 {
		t.Fatalf("valid policy rejected: %v", problems)
	}
	if normalized.Origin != "https://connector.example.test:443" {
		t.Errorf("origin was not normalised: %q", normalized.Origin)
	}
	if normalized.Method != "GET" {
		t.Errorf("method was not normalised: %q", normalized.Method)
	}
	if normalized.PathPrefix != "/v1" {
		t.Errorf("path prefix was not normalised: %q", normalized.PathPrefix)
	}
}

func TestValidatePolicyRejectsUnsafePathPrefixesAndMethodsAndSchemas(t *testing.T) {
	base := Input{
		Origin: "https://connector.example.test", Method: "GET", PathPrefix: "/v1",
		SecretRef: "env:T048_KEY", SecretHeader: "Authorization", RiskClass: "read",
	}
	cases := map[string]func(Input) Input{
		"method":           func(in Input) Input { in.Method = "TRACE"; return in },
		"dot segment":      func(in Input) Input { in.PathPrefix = "/v1/../admin"; return in },
		"backslash":        func(in Input) Input { in.PathPrefix = `/v1\admin`; return in },
		"double slash":     func(in Input) Input { in.PathPrefix = "//v1"; return in },
		"no leading slash": func(in Input) Input { in.PathPrefix = "v1"; return in },
		"query":            func(in Input) Input { in.PathPrefix = "/v1?x=1"; return in },
		"secret value":     func(in Input) Input { in.SecretRef = "sk-live-123"; return in },
		"secret file path": func(in Input) Input { in.SecretRef = "file:../../etc/passwd"; return in },
		"secret header":    func(in Input) Input { in.SecretHeader = "X Bad"; return in },
		"forbidden header": func(in Input) Input { in.SecretHeader = "Host"; return in },
		"risk class":       func(in Input) Input { in.RiskClass = "pure"; return in },
		"schema type": func(in Input) Input {
			in.RequestSchema = map[string]any{"type": "object", "properties": "not-an-object"}
			return in
		},
	}
	for name, mutate := range cases {
		if _, problems := normalizePolicyInput(mutate(base)); len(problems) == 0 {
			t.Errorf("%s: the unsafe policy was accepted", name)
		}
	}
}

// The invocation path is appended to the registered origin, so it must be a
// relative path under path_prefix, never an absolute URL or a dot-segment trick.
func TestResolveInvocationPathEnforcesThePrefixAndRefusesSmuggledTargets(t *testing.T) {
	good := map[string]string{
		"/v1":          "/v1",
		"/v1/items":    "/v1/items",
		"/v1/items/42": "/v1/items/42",
		"/v1/a%20b":    "/v1/a%20b",
	}
	for input, expected := range good {
		path, _, err := resolveInvocationPath("/v1", input)
		if err != nil {
			t.Errorf("path %q was refused: %v", input, err)
			continue
		}
		if path != expected {
			t.Errorf("path %q became %q, want %q", input, path, expected)
		}
	}
	bad := []string{
		"/v2/items", "/v1/../admin", "/v1/%2e%2e/admin", "/v1//admin",
		"//evil.test/v1", "http://evil.test/v1", "/v1\\admin",
		"/v1/items#frag", "/v1/items\nHost: evil", "",
		// A path prefix is a segment namespace: /v1 must not authorize a sibling
		// whose text merely starts with /v1.
		"/v10/items", "/v1beta/items", "/v1beta",
	}
	for _, input := range bad {
		if _, _, err := resolveInvocationPath("/v1", input); err == nil {
			t.Errorf("path %q was accepted", input)
		}
	}
	// A policy row is stored normalized, but the resolver trims a trailing slash
	// itself so a hand-built policy cannot turn the namespace check into a string
	// prefix.
	if _, _, err := resolveInvocationPath("/v1/", "/v1/items"); err != nil {
		t.Errorf("a trailing-slash prefix refused an in-namespace path: %v", err)
	}
	if _, _, err := resolveInvocationPath("/v1/", "/v10/items"); err == nil {
		t.Error("a trailing-slash prefix accepted a sibling path")
	}
}

func TestResolveInvocationPathSplitsTheQueryWithoutAllowingItByDefault(t *testing.T) {
	path, query, err := resolveInvocationPath("/v1", "/v1/items?a=1&b=two")
	if err != nil {
		t.Fatalf("plain query was refused: %v", err)
	}
	if path != "/v1/items" || query.Get("a") != "1" || query.Get("b") != "two" {
		t.Errorf("query was not split safely: path=%q query=%v", path, query)
	}
	// A smuggled absolute target or dot segment in the query value must not
	// pass through to the URL builder.
	if _, _, err := resolveInvocationPath("/v1", "/v1/items?next=//evil.test"); err == nil {
		t.Error("a query value that starts with // was accepted")
	}
}

// Address classification is the SSRF core. Even allow_private must never permit
// the cloud metadata address, link-local, multicast or unspecified targets.
func TestAddressPolicyRefusesMetadataEvenWhenPrivateIsAllowed(t *testing.T) {
	alwaysBlocked := []string{
		"169.254.169.254", "169.254.1.1", "::ffff:169.254.169.254",
		"fe80::1", "ff02::1", "0.0.0.0", "::", "224.0.0.1",
	}
	for _, raw := range alwaysBlocked {
		addr := mustAddr(t, raw)
		if reason := addressRefusal(addr, true); reason == "" {
			t.Errorf("address %s was accepted with allow_private=true; it must always be refused", raw)
		}
	}
	private := []string{"127.0.0.1", "::1", "10.0.0.5", "172.16.0.1", "192.168.1.10", "fd00::1"}
	for _, raw := range private {
		addr := mustAddr(t, raw)
		if reason := addressRefusal(addr, false); reason == "" {
			t.Errorf("address %s was accepted with allow_private=false", raw)
		}
		if reason := addressRefusal(addr, true); reason != "" {
			t.Errorf("address %s was refused with allow_private=true: %s", raw, reason)
		}
	}
	for _, raw := range []string{"93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946"} {
		addr := mustAddr(t, raw)
		if reason := addressRefusal(addr, false); reason != "" {
			t.Errorf("public address %s was refused: %s", raw, reason)
		}
	}
}

// P1-2: a registered schema bound that cannot be evaluated must be refused at
// registration, so an unusable policy can never become a run's frozen contract.
func TestValidatePolicyEnforcesSchemaBoundShapes(t *testing.T) {
	base := Input{
		Origin: "https://connector.example.test", Method: "POST", PathPrefix: "/v1",
		SecretRef: "env:T048_KEY", SecretHeader: "Authorization", RiskClass: "read",
	}
	property := func(bounds map[string]any) map[string]any {
		schema := map[string]any{"type": "number"}
		for keyword, value := range bounds {
			schema[keyword] = value
		}
		return map[string]any{
			"type":       "object",
			"properties": map[string]any{"amount": schema},
		}
	}
	bad := map[string]map[string]any{
		"minimum is a string":       property(map[string]any{"minimum": "1"}),
		"maximum is a bool":         property(map[string]any{"maximum": true}),
		"minimum above maximum":     property(map[string]any{"minimum": 10, "maximum": 1}),
		"minLength is negative":     property(map[string]any{"type": "string", "minLength": -1}),
		"minLength is fractional":   property(map[string]any{"type": "string", "minLength": 1.5}),
		"maxLength is a string":     property(map[string]any{"type": "string", "maxLength": "3"}),
		"minLength above maxLength": property(map[string]any{"type": "string", "minLength": 5, "maxLength": 3}),
	}
	for name, schema := range bad {
		input := base
		input.RequestSchema = schema
		if _, problems := normalizePolicyInput(input); len(problems) == 0 {
			t.Errorf("%s: the unusable bound was accepted", name)
		}
	}

	good := base
	good.RequestSchema = map[string]any{
		"type": "object",
		"properties": map[string]any{
			"amount": map[string]any{
				"type": "number", "minimum": 1, "maximum": 10,
			},
			"label": map[string]any{
				"type": "string", "minLength": 2, "maxLength": 4,
			},
		},
	}
	good.ResponseSchema = map[string]any{
		"type": "object",
		"properties": map[string]any{
			"note": map[string]any{"type": "string", "maxLength": 500},
		},
	}
	if _, problems := normalizePolicyInput(good); len(problems) != 0 {
		t.Fatalf("a well-formed bounded schema was refused: %v", problems)
	}
}

// A policy body is sealed as canonical JCS; the hash is the version identity.
// The body must not contain the secret value or the artifact id.
func TestPolicyBodyIsCanonicalAndHashStable(t *testing.T) {
	input := Input{
		Origin: "https://connector.example.test", Method: "POST", PathPrefix: "/v1",
		RequestSchema:  map[string]any{"type": "object", "properties": map[string]any{"a": map[string]any{"type": "string"}}},
		ResponseSchema: map[string]any{"type": "object"},
		SecretRef:      "env:T048_KEY", SecretHeader: "Authorization", RiskClass: "write",
		AllowPrivate: true,
	}
	normalized, problems := normalizePolicyInput(input)
	if len(problems) != 0 {
		t.Fatalf("valid policy rejected: %v", problems)
	}
	first, err := policyBodyBytes(normalized)
	if err != nil {
		t.Fatalf("policy body: %v", err)
	}
	second, err := policyBodyBytes(normalized)
	if err != nil {
		t.Fatalf("policy body: %v", err)
	}
	if string(first) != string(second) {
		t.Fatal("policy body bytes are not stable")
	}
	if strings.Contains(string(first), "Authorization") == false {
		// secret_header is policy content; the secret VALUE is not.
		t.Fatal("the sealed body should keep the header name")
	}
	if strings.Contains(string(first), "sk-") {
		t.Fatal("the sealed body contains secret-looking material")
	}
	if strings.Contains(string(first), "policy_artifact") {
		t.Fatal("the sealed body must not contain the artifact id")
	}
}

func mustAddr(t *testing.T, raw string) (addr netipAddr) {
	t.Helper()
	return parseAddr(t, raw)
}
