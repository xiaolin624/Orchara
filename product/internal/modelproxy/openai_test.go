package modelproxy

// F06/T122 protocol tests: the OpenAI-compatible chat-completions adapter is
// observed only through the public Provider/StreamProvider seams and a real
// loopback HTTP server (httptest). Nothing here mocks the transport: the adapter
// makes a real HTTP request and the fake provider records exactly what it saw.

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"orchara/platform/internal/config"
	"orchara/platform/internal/modelregistry"
)

const (
	t122EndpointID  = "fake-endpoint"
	t122Credential  = "fake-credential"
	t122Destination = "fake-local"
	t122SecretEnv   = "T122_MODEL_KEY"
	t122Secret      = "t122-secret-do-not-log"
)

// fakeCatalog builds the deployer catalog the adapter must approve destinations
// against; nothing secret is in it (the credential entry only names an env var).
func fakeCatalog(t *testing.T) *modelregistry.Catalog {
	t.Helper()
	endpoints := `[{"id":"` + t122EndpointID + `","data_destination":"` + t122Destination +
		`","credential_ref":"` + t122Credential + `","description":"t122 fake"}]`
	credentials := `[{"id":"` + t122Credential + `","description":"t122 fake key","env":"` + t122SecretEnv + `"}]`
	catalog, err := modelregistry.FromEnv(func(key string) string {
		switch key {
		case modelregistry.EnvModelEndpoints:
			return endpoints
		case modelregistry.EnvCredentialRefs:
			return credentials
		}
		return ""
	})
	if err != nil {
		t.Fatalf("building the fake catalog: %v", err)
	}
	return catalog
}

func secretLookup(key string) (string, bool) {
	if key == t122SecretEnv {
		return t122Secret, true
	}
	return "", false
}

// chatCandidate is a registration that declares the supported protocol.
func chatCandidate(endpointRef, secretRef string) *Candidate {
	raw, _ := json.Marshal(map[string]any{
		"kind":       "generation",
		"protocol":   "chat_compatible",
		"timeout_ms": 2000,
		"price": map[string]any{
			"currency":           "USD",
			"input_per_million":  "1.00",
			"output_per_million": "2.00",
		},
	})
	return &Candidate{
		ID:              "11111111-2222-4333-8444-555555555555",
		Provider:        "openai-compatible",
		ModelID:         "fake-vendor-model-1",
		EndpointRef:     endpointRef,
		SecretRef:       secretRef,
		Config:          raw,
		ValidationState: "pending",
	}
}

func candidateWithConfig(endpointRef, secretRef string, configValue map[string]any) *Candidate {
	candidate := chatCandidate(endpointRef, secretRef)
	raw, _ := json.Marshal(configValue)
	candidate.Config = raw
	return candidate
}

func realProviders(t *testing.T, endpoint string, lookup config.Lookup) *Providers {
	t.Helper()
	return NewProviders(config.ProfileDevStub, endpoint, "", lookup).WithCatalog(fakeCatalog(t))
}

func TestAvailableRequiresAnApprovedDestination(t *testing.T) {
	providers := realProviders(t, "https://models.internal.example/v1", secretLookup)
	provider, err := providers.For(chatCandidate(t122EndpointID, t122Credential))
	if err != nil {
		t.Fatalf("For: %v", err)
	}

	if err := provider.Available(chatCandidate("no-such-endpoint", t122Credential)); !errors.Is(err, ErrDestinationUnapproved) {
		t.Fatalf("an unregistered destination must be refused, got %v", err)
	}
	// An arbitrary URL is not a deployer-registered reference, even if a row
	// stores it: the adapter must never accept a request-visible destination.
	if err := provider.Available(chatCandidate("https://evil.example/v1", t122Credential)); !errors.Is(err, ErrDestinationUnapproved) {
		t.Fatalf("a URL destination must be refused, got %v", err)
	}
	if err := provider.Available(chatCandidate("", t122Credential)); !errors.Is(err, ErrDestinationUnapproved) {
		t.Fatalf("an empty destination must be refused, got %v", err)
	}
	if err := provider.Available(chatCandidate(t122EndpointID, t122Credential)); err != nil {
		t.Fatalf("a registered destination with a resolvable credential must be available, got %v", err)
	}
	// The legacy deployment reference shape is still accepted; the URL itself
	// always comes from deployment configuration, never from the reference.
	if err := provider.Available(chatCandidate("env:ORCHARA_MODEL_ENDPOINT", t122Credential)); err != nil {
		t.Fatalf("an env: destination reference must be available, got %v", err)
	}
}

func TestAvailableRequiresTheChatCompletionsProtocol(t *testing.T) {
	providers := realProviders(t, "https://models.internal.example/v1", secretLookup)
	provider, _ := providers.For(chatCandidate(t122EndpointID, t122Credential))

	for name, configValue := range map[string]map[string]any{
		"absent protocol":  {"kind": "generation"},
		"embedding":        {"kind": "embedding", "protocol": "embedding_compatible"},
		"unknown protocol": {"kind": "generation", "protocol": "grpc_v2"},
	} {
		if err := provider.Available(candidateWithConfig(t122EndpointID, t122Credential, configValue)); !errors.Is(err, ErrAdapterUnavailable) {
			t.Fatalf("%s: expected ErrAdapterUnavailable, got %v", name, err)
		}
	}
}

func TestAvailableRequiresAConfiguredEndpointAndResolvableCredential(t *testing.T) {
	unconfigured := realProviders(t, "", secretLookup)
	provider, _ := unconfigured.For(chatCandidate(t122EndpointID, t122Credential))
	if err := provider.Available(chatCandidate(t122EndpointID, t122Credential)); !errors.Is(err, ErrNotConfigured) {
		t.Fatalf("no deployed endpoint must be provider_not_configured, got %v", err)
	}

	providers := realProviders(t, "https://models.internal.example/v1", func(string) (string, bool) { return "", false })
	provider, _ = providers.For(chatCandidate(t122EndpointID, t122Credential))
	if err := provider.Available(chatCandidate(t122EndpointID, t122Credential)); !errors.Is(err, ErrNotConfigured) {
		t.Fatalf("an unresolvable credential must be provider_not_configured, got %v", err)
	}
	if err := provider.Available(chatCandidate(t122EndpointID, "unregistered-credential")); !errors.Is(err, ErrNotConfigured) {
		t.Fatalf("an unregistered credential reference must be provider_not_configured, got %v", err)
	}

	// Legacy env: credential references resolve through the deployment lookup.
	envProviders := realProviders(t, "https://models.internal.example/v1", secretLookup)
	legacy := chatCandidate(t122EndpointID, "env:"+t122SecretEnv)
	envProvider, _ := envProviders.For(legacy)
	if err := envProvider.Available(legacy); err != nil {
		t.Fatalf("an env: credential must resolve, got %v", err)
	}

	// A deployed endpoint that is not an absolute http(s) URL without
	// credentials/query/fragment is not an approved protocol destination.
	for _, endpoint := range []string{
		"ftp://models.internal.example/v1",
		"https://user:pass@models.internal.example/v1",
		"https://models.internal.example/v1?key=value",
		"https://models.internal.example/v1#fragment",
		"not-a-url",
	} {
		bad := realProviders(t, endpoint, secretLookup)
		badProvider, _ := bad.For(chatCandidate(t122EndpointID, t122Credential))
		if err := badProvider.Available(chatCandidate(t122EndpointID, t122Credential)); !errors.Is(err, ErrDestinationUnapproved) {
			t.Fatalf("endpoint %q must be refused, got %v", endpoint, err)
		}
	}
}

// recordedRequest is exactly what the fake provider received.
type recordedRequest struct {
	Method        string
	Path          string
	Authorization string
	ContentType   string
	Accept        string
	Body          map[string]any
	Raw           []byte
}

type fakeProvider struct {
	mu      sync.Mutex
	records []recordedRequest
	server  *httptest.Server
}

func (f *fakeProvider) record(r *http.Request) map[string]any {
	raw, _ := io.ReadAll(io.LimitReader(r.Body, 1<<20))
	body := map[string]any{}
	_ = json.Unmarshal(raw, &body)
	f.mu.Lock()
	f.records = append(f.records, recordedRequest{
		Method:        r.Method,
		Path:          r.URL.Path,
		Authorization: r.Header.Get("Authorization"),
		ContentType:   r.Header.Get("Content-Type"),
		Accept:        r.Header.Get("Accept"),
		Body:          body,
		Raw:           raw,
	})
	f.mu.Unlock()
	return body
}

func (f *fakeProvider) requests() []recordedRequest {
	f.mu.Lock()
	defer f.mu.Unlock()
	return append([]recordedRequest(nil), f.records...)
}

func (f *fakeProvider) url() string { return f.server.URL }

// newFakeProvider starts a real loopback HTTP provider whose answer each test
// controls. Nothing about the transport is mocked.
func newFakeProvider(t *testing.T, respond func(w http.ResponseWriter, r *http.Request, body map[string]any)) *fakeProvider {
	t.Helper()
	fake := &fakeProvider{}
	fake.server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body := fake.record(r)
		respond(w, r, body)
	}))
	t.Cleanup(fake.server.Close)
	return fake
}

func writeJSON(w http.ResponseWriter, status int, payload string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_, _ = io.WriteString(w, payload)
}

func chatParams(messages ...map[string]any) map[string]any {
	items := make([]any, 0, len(messages))
	for _, message := range messages {
		items = append(items, message)
	}
	return map[string]any{"task": "agent", "messages": items}
}

func TestCallMapsTextUsageAndSealsCanonicalBytes(t *testing.T) {
	fake := newFakeProvider(t, func(w http.ResponseWriter, _ *http.Request, _ map[string]any) {
		writeJSON(w, http.StatusOK, `{"id":"chatcmpl-1","object":"chat.completion",`+
			`"choices":[{"index":0,"message":{"role":"assistant","content":"hello from the vendor"},`+
			`"finish_reason":"stop"}],`+
			`"usage":{"prompt_tokens":11,"completion_tokens":7,"total_tokens":18}}`)
	})
	candidate := chatCandidate(t122EndpointID, t122Credential)
	providers := realProviders(t, fake.url()+"/v1", secretLookup)
	provider, _ := providers.For(candidate)

	response, err := provider.Call(context.Background(), CallRequest{
		Candidate: candidate,
		Params:    chatParams(map[string]any{"role": "user", "content": "summarise the file"}),
	})
	if err != nil {
		t.Fatalf("Call: %v", err)
	}

	// Exactly one physical request, addressed by the documented append rule.
	requests := fake.requests()
	if len(requests) != 1 {
		t.Fatalf("expected exactly one physical request, got %d", len(requests))
	}
	request := requests[0]
	if request.Method != http.MethodPost || request.Path != "/v1/chat/completions" {
		t.Fatalf("request = %s %s", request.Method, request.Path)
	}
	if request.Authorization != "Bearer "+t122Secret {
		t.Fatalf("the credential must be sent only as a bearer header, got %q", request.Authorization)
	}
	if !strings.HasPrefix(request.ContentType, "application/json") {
		t.Fatalf("content type = %q", request.ContentType)
	}
	if request.Body["model"] != "fake-vendor-model-1" {
		t.Fatalf("the registered model id was not sent: %#v", request.Body)
	}
	messages, _ := request.Body["messages"].([]any)
	if len(messages) != 1 {
		t.Fatalf("messages = %#v", request.Body["messages"])
	}
	first, _ := messages[0].(map[string]any)
	if first["role"] != "user" || first["content"] != "summarise the file" {
		t.Fatalf("message = %#v", first)
	}

	if response.Data["text"] != "hello from the vendor" {
		t.Fatalf("text = %#v", response.Data["text"])
	}
	if response.Data["stub"] != false {
		t.Fatalf("a real provider answer must not be labelled stub: %#v", response.Data["stub"])
	}
	usage, _ := response.Data["usage"].(map[string]any)
	if usage["input_tokens"] != int64(11) || usage["output_tokens"] != int64(7) {
		t.Fatalf("usage = %#v", usage)
	}
	if response.TokensIn != 11 || response.TokensOut != 7 {
		t.Fatalf("tokens = %d/%d", response.TokensIn, response.TokensOut)
	}
	// 11 input tokens at 1.00/million + 7 output tokens at 2.00/million.
	if response.ChargedUnits != 2500 {
		t.Fatalf("charged units = %d, want 2500", response.ChargedUnits)
	}
	canonical, err := canonicalJSON(response.Data)
	if err != nil {
		t.Fatal(err)
	}
	if string(response.Output) != string(canonical) {
		t.Fatalf("Output must be the canonical bytes of Data:\n%s\n%s", response.Output, canonical)
	}
	for _, rendered := range []string{string(response.Output), fmt.Sprint(response.Data)} {
		if strings.Contains(rendered, t122Secret) {
			t.Fatalf("the credential leaked outside the Authorization header: %s", rendered)
		}
	}

	// The endpoint is used verbatim when it already names the completions path.
	full := newFakeProvider(t, func(w http.ResponseWriter, _ *http.Request, _ map[string]any) {
		writeJSON(w, http.StatusOK, `{"choices":[{"message":{"content":"ok"}}]}`)
	})
	fullProviders := realProviders(t, full.url()+"/v1/chat/completions", secretLookup)
	fullProvider, _ := fullProviders.For(candidate)
	if _, err := fullProvider.Call(context.Background(), CallRequest{
		Candidate: candidate, Params: chatParams(map[string]any{"role": "user", "content": "hi"}),
	}); err != nil {
		t.Fatalf("Call: %v", err)
	}
	fullRequests := full.requests()
	if len(fullRequests) != 1 || fullRequests[0].Path != "/v1/chat/completions" {
		t.Fatalf("the full path must not be appended twice: %#v", fullRequests)
	}
}

func TestCallMapsToolCallsAndStructuredOutput(t *testing.T) {
	fake := newFakeProvider(t, func(w http.ResponseWriter, _ *http.Request, _ map[string]any) {
		writeJSON(w, http.StatusOK, `{"choices":[{"message":{"role":"assistant",`+
			`"content":"{\"answer\":\"42\"}",`+
			`"tool_calls":[{"id":"call_1","type":"function","function":{`+
			`"name":"lookup","arguments":"{\"city\":\"Paris\"}"}}]},`+
			`"finish_reason":"tool_calls"}],`+
			`"usage":{"prompt_tokens":5,"completion_tokens":3}}`)
	})
	candidate := chatCandidate(t122EndpointID, t122Credential)
	providers := realProviders(t, fake.url(), secretLookup)
	provider, _ := providers.For(candidate)

	params := chatParams(map[string]any{"role": "user", "content": "look it up"})
	params["tools"] = []any{map[string]any{
		"name": "lookup", "capability_version": "11111111-1111-4111-8111-111111111111",
	}}
	params["response_schema"] = map[string]any{
		"type": "object", "additionalProperties": false, "required": []any{"answer"},
		"properties": map[string]any{"answer": map[string]any{"type": "string"}},
	}
	response, err := provider.Call(context.Background(), CallRequest{Candidate: candidate, Params: params})
	if err != nil {
		t.Fatalf("Call: %v", err)
	}
	calls, _ := response.Data["tool_calls"].([]any)
	if len(calls) != 1 {
		t.Fatalf("tool_calls = %#v", response.Data["tool_calls"])
	}
	call, _ := calls[0].(map[string]any)
	arguments, _ := call["arguments"].(map[string]any)
	if call["id"] != "call_1" || call["name"] != "lookup" || arguments["city"] != "Paris" {
		t.Fatalf("tool call mapping = %#v", call)
	}
	structured, _ := response.Data["structured"].(map[string]any)
	if structured["answer"] != "42" {
		t.Fatalf("structured = %#v", response.Data["structured"])
	}
	if response.TokensIn != 5 || response.TokensOut != 3 {
		t.Fatalf("tokens = %d/%d", response.TokensIn, response.TokensOut)
	}
}

func TestCallKeepsAMalformedToolCallForTheValidatorToRefuse(t *testing.T) {
	fake := newFakeProvider(t, func(w http.ResponseWriter, _ *http.Request, _ map[string]any) {
		writeJSON(w, http.StatusOK, `{"choices":[{"message":{"tool_calls":[{"id":"call_1",`+
			`"type":"function","function":{"name":"lookup","arguments":"{not json"}}]}}]}`)
	})
	candidate := chatCandidate(t122EndpointID, t122Credential)
	providers := realProviders(t, fake.url(), secretLookup)
	provider, _ := providers.For(candidate)
	params := map[string]any{"task": "agent", "messages": []any{
		map[string]any{"role": "user", "content": "hi"},
	}, "tools": []any{map[string]any{"name": "lookup", "capability_version": "11111111-1111-4111-8111-111111111111"}}}
	response, err := provider.Call(context.Background(), CallRequest{Candidate: candidate, Params: params})
	if err != nil {
		t.Fatalf("Call: %v", err)
	}
	calls, _ := response.Data["tool_calls"].([]any)
	if len(calls) != 1 {
		t.Fatalf("tool_calls = %#v", response.Data["tool_calls"])
	}
	call, _ := calls[0].(map[string]any)
	// The adapter never invents arguments: the raw fragment goes to the
	// capability validator, which refuses the call without retrying.
	if _, isObject := call["arguments"].(map[string]any); isObject {
		t.Fatalf("a malformed argument fragment must not become an object: %#v", call)
	}
	if call["arguments"] != "{not json" {
		t.Fatalf("arguments = %#v", call["arguments"])
	}
}

func TestCallFixtureModeProducesACompatibilityCheckAndRouteDecision(t *testing.T) {
	fake := newFakeProvider(t, func(w http.ResponseWriter, _ *http.Request, body map[string]any) {
		// The probe prompt is the server-built fixture; the fake answers with a
		// JSON object just like a compatible model would.
		writeJSON(w, http.StatusOK, `{"choices":[{"message":{"content":"{\"ok\":true}"}}],`+
			`"usage":{"prompt_tokens":4,"completion_tokens":2}}`)
	})
	candidate := chatCandidate(t122EndpointID, t122Credential)
	providers := realProviders(t, fake.url(), secretLookup)
	provider, _ := providers.For(candidate)

	fixture := []byte(`{"schema":"orchara.model-compat/1","case":"ok","check":"structured_output"}`)
	response, err := provider.Call(context.Background(), CallRequest{
		Candidate: candidate,
		Params:    map[string]any{"task": "model_compatibility", "fixture_sha256": "x"},
		Fixture:   fixture,
	})
	if err != nil {
		t.Fatalf("Call: %v", err)
	}
	check, _ := response.Data["check"].(map[string]any)
	if check["name"] != "structured_output" || check["passed"] != true {
		t.Fatalf("check = %#v", response.Data["check"])
	}
	request := fake.requests()[0]
	format, _ := request.Body["response_format"].(map[string]any)
	if format["type"] != "json_object" {
		t.Fatalf("a fixture call must ask for a JSON object: %#v", request.Body)
	}
	messages, _ := request.Body["messages"].([]any)
	if len(messages) != 2 {
		t.Fatalf("fixture messages = %#v", messages)
	}
	second, _ := messages[1].(map[string]any)
	if second["content"] != string(fixture) {
		t.Fatalf("the fixture bytes must be the user prompt, got %#v", second)
	}

	// A route control call lifts the documented decision fields out of the JSON
	// object so the existing routing validator can read them unchanged.
	route := newFakeProvider(t, func(w http.ResponseWriter, _ *http.Request, _ map[string]any) {
		writeJSON(w, http.StatusOK, `{"choices":[{"message":{"content":`+
			`"{\"decision\":\"select\",\"candidate_version_id\":\"33333333-3333-4333-8333-333333333333\"}"}}]}`)
	})
	routeProviders := realProviders(t, route.url(), secretLookup)
	routeProvider, _ := routeProviders.For(candidate)
	routed, err := routeProvider.Call(context.Background(), CallRequest{
		Candidate: candidate, Params: map[string]any{"task": "route"},
		Fixture: []byte(`{"message":"use the sales app","candidates":[{"application_version_id":"33333333-3333-4333-8333-333333333333"}]}`),
	})
	if err != nil {
		t.Fatalf("route Call: %v", err)
	}
	if routed.Data["decision"] != "select" ||
		routed.Data["candidate_version_id"] != "33333333-3333-4333-8333-333333333333" {
		t.Fatalf("route decision mapping = %#v", routed.Data)
	}

	// A call that is neither chat nor a fixture is a platform mapping failure.
	if _, err := provider.Call(context.Background(), CallRequest{
		Candidate: candidate, Params: map[string]any{"task": "agent"},
	}); !errors.Is(err, ErrCallInvalid) {
		t.Fatalf("expected ErrCallInvalid, got %v", err)
	}
}

func TestCallSendsDeclaredToolsAndResponseFormat(t *testing.T) {
	fake := newFakeProvider(t, func(w http.ResponseWriter, _ *http.Request, _ map[string]any) {
		writeJSON(w, http.StatusOK, `{"choices":[{"message":{"content":"{\"answer\":\"42\"}"}}],`+
			`"usage":{"prompt_tokens":3,"completion_tokens":2}}`)
	})
	candidate := chatCandidate(t122EndpointID, t122Credential)
	providers := realProviders(t, fake.url(), secretLookup)
	provider, _ := providers.For(candidate)

	params := chatParams(map[string]any{"role": "user", "content": "answer"})
	params["tools"] = []any{map[string]any{
		"name": "lookup", "capability_version": "11111111-1111-4111-8111-111111111111",
	}}
	params["response_schema"] = map[string]any{
		"type": "object", "additionalProperties": false, "required": []any{"answer"},
		"properties": map[string]any{"answer": map[string]any{"type": "string"}},
	}
	params["max_output_tokens"] = float64(256)
	if _, err := provider.Call(context.Background(), CallRequest{Candidate: candidate, Params: params}); err != nil {
		t.Fatalf("Call: %v", err)
	}
	request := fake.requests()[0]
	tools, _ := request.Body["tools"].([]any)
	if len(tools) != 1 {
		t.Fatalf("declared tools were not sent: %#v", request.Body)
	}
	tool, _ := tools[0].(map[string]any)
	function, _ := tool["function"].(map[string]any)
	if tool["type"] != "function" || function["name"] != "lookup" {
		t.Fatalf("tool mapping = %#v", tool)
	}
	format, _ := request.Body["response_format"].(map[string]any)
	if format["type"] != "json_schema" {
		t.Fatalf("response_format = %#v", format)
	}
	if request.Body["max_tokens"] != float64(256) {
		t.Fatalf("max_tokens = %#v", request.Body["max_tokens"])
	}
}

func TestCallClassifiesProviderStatuses(t *testing.T) {
	cases := []struct {
		name      string
		status    int
		body      string
		transient bool
		code      string
	}{
		{"rate limited", http.StatusTooManyRequests, `{"error":{"message":"slow down"}}`, true, ""},
		{"server error", http.StatusInternalServerError, `internal provider failure`, true, ""},
		{"bad gateway", http.StatusBadGateway, `bad gateway`, true, ""},
		{"request timeout status", http.StatusRequestTimeout, `timeout`, true, ""},
		{"unauthorized", http.StatusUnauthorized, `{"error":{"message":"invalid key ` + t122Secret + `"}}`, false, "provider_permission_denied"},
		{"forbidden", http.StatusForbidden, `forbidden`, false, "provider_permission_denied"},
		{"not found", http.StatusNotFound, `no such model`, false, "provider_model_not_found"},
		{"unprocessable", http.StatusUnprocessableEntity, `bad schema`, false, "provider_request_rejected"},
		{"bad request", http.StatusBadRequest, `bad request`, false, "provider_request_rejected"},
	}
	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			fake := newFakeProvider(t, func(w http.ResponseWriter, _ *http.Request, _ map[string]any) {
				writeJSON(w, testCase.status, testCase.body)
			})
			candidate := chatCandidate(t122EndpointID, t122Credential)
			providers := realProviders(t, fake.url(), secretLookup)
			provider, _ := providers.For(candidate)
			_, err := provider.Call(context.Background(), CallRequest{
				Candidate: candidate, Params: chatParams(map[string]any{"role": "user", "content": "hi"}),
			})
			if err == nil {
				t.Fatal("expected a provider failure")
			}
			if modelproxyIsTransient := IsTransient(err); modelproxyIsTransient != testCase.transient {
				t.Fatalf("IsTransient(%v) = %v, want %v", err, modelproxyIsTransient, testCase.transient)
			}
			if !testCase.transient && ProviderCode(err) != testCase.code {
				t.Fatalf("code = %q, want %q (%v)", ProviderCode(err), testCase.code, err)
			}
			if strings.Contains(err.Error(), t122Secret) {
				t.Fatalf("the provider error leaked the credential: %v", err)
			}
			if len(fake.requests()) != 1 {
				t.Fatalf("expected exactly one physical request, got %d", len(fake.requests()))
			}
		})
	}
}

func TestCallRefusesMalformedOversizedAndRedirectedAnswers(t *testing.T) {
	malformed := newFakeProvider(t, func(w http.ResponseWriter, _ *http.Request, _ map[string]any) {
		writeJSON(w, http.StatusOK, `{"choices":[{"message":{"content":`)
	})
	candidate := chatCandidate(t122EndpointID, t122Credential)
	providers := realProviders(t, malformed.url(), secretLookup)
	provider, _ := providers.For(candidate)
	_, err := provider.Call(context.Background(), CallRequest{
		Candidate: candidate, Params: chatParams(map[string]any{"role": "user", "content": "hi"}),
	})
	if !errors.Is(err, ErrResponseInvalid) || IsTransient(err) {
		t.Fatalf("malformed JSON must be a non-transient ErrResponseInvalid, got %v", err)
	}
	if ProviderCode(err) != "provider_response_invalid" {
		t.Fatalf("code = %q", ProviderCode(err))
	}
	if len(malformed.requests()) != 1 {
		t.Fatalf("malformed JSON must not be retried by the adapter, got %d requests", len(malformed.requests()))
	}

	oversized := newFakeProvider(t, func(w http.ResponseWriter, _ *http.Request, _ map[string]any) {
		writeJSON(w, http.StatusOK, `{"choices":[{"message":{"content":"`+
			strings.Repeat("x", maxProviderResponseBytes+1)+`"}}]}`)
	})
	oversizedProviders := realProviders(t, oversized.url(), secretLookup)
	oversizedProvider, _ := oversizedProviders.For(candidate)
	_, err = oversizedProvider.Call(context.Background(), CallRequest{
		Candidate: candidate, Params: chatParams(map[string]any{"role": "user", "content": "hi"}),
	})
	if !errors.Is(err, ErrResponseTooLarge) || IsTransient(err) {
		t.Fatalf("an oversized answer must be a non-transient ErrResponseTooLarge, got %v", err)
	}
	if ProviderCode(err) != "provider_response_too_large" {
		t.Fatalf("code = %q", ProviderCode(err))
	}
	if len(oversized.requests()) != 1 {
		t.Fatalf("an oversized answer must not be retried, got %d requests", len(oversized.requests()))
	}

	redirected := newFakeProvider(t, func(w http.ResponseWriter, _ *http.Request, _ map[string]any) {
		w.Header().Set("Location", "https://elsewhere.invalid/v1/chat/completions")
		w.WriteHeader(http.StatusFound)
	})
	redirectedProviders := realProviders(t, redirected.url(), secretLookup)
	redirectedProvider, _ := redirectedProviders.For(candidate)
	_, err = redirectedProvider.Call(context.Background(), CallRequest{
		Candidate: candidate, Params: chatParams(map[string]any{"role": "user", "content": "hi"}),
	})
	if IsTransient(err) || ProviderCode(err) != "provider_redirected" {
		t.Fatalf("a redirect must be a terminal provider refusal, got %v (%q)", err, ProviderCode(err))
	}
	if len(redirected.requests()) != 1 {
		t.Fatalf("a redirect must not be followed, got %d requests", len(redirected.requests()))
	}
}

func TestCallTimeoutIsTransient(t *testing.T) {
	release := make(chan struct{})
	fake := newFakeProvider(t, func(w http.ResponseWriter, _ *http.Request, _ map[string]any) {
		<-release
		writeJSON(w, http.StatusOK, `{"choices":[{"message":{"content":"late"}}]}`)
	})
	t.Cleanup(func() { close(release) })
	candidate := candidateWithConfig(t122EndpointID, t122Credential, map[string]any{
		"kind": "generation", "protocol": "chat_compatible", "timeout_ms": 50,
	})
	providers := realProviders(t, fake.url(), secretLookup)
	provider, _ := providers.For(candidate)
	started := time.Now()
	_, err := provider.Call(context.Background(), CallRequest{
		Candidate: candidate, Params: chatParams(map[string]any{"role": "user", "content": "hi"}),
	})
	if !IsTransient(err) {
		t.Fatalf("a request timeout must be transient, got %v", err)
	}
	if elapsed := time.Since(started); elapsed > 2*time.Second {
		t.Fatalf("the registered timeout was not applied: %s", elapsed)
	}
}

func TestCallCancellationIsNotReportedAsATransientProviderFailure(t *testing.T) {
	release := make(chan struct{})
	fake := newFakeProvider(t, func(w http.ResponseWriter, _ *http.Request, _ map[string]any) {
		<-release
		writeJSON(w, http.StatusOK, `{"choices":[{"message":{"content":"late"}}]}`)
	})
	t.Cleanup(func() { close(release) })
	candidate := chatCandidate(t122EndpointID, t122Credential)
	providers := realProviders(t, fake.url(), secretLookup)
	provider, _ := providers.For(candidate)
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	_, err := provider.Call(ctx, CallRequest{
		Candidate: candidate, Params: chatParams(map[string]any{"role": "user", "content": "hi"}),
	})
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("a cancelled call must return the caller's cancellation, got %v", err)
	}
	if IsTransient(err) {
		t.Fatal("a caller cancellation must not be labelled a transient provider failure")
	}
}

// sseFrames writes the documented SSE framing to a real response writer.
func sseFrames(t *testing.T, w http.ResponseWriter, frames ...string) {
	t.Helper()
	w.Header().Set("Content-Type", "text/event-stream")
	w.WriteHeader(http.StatusOK)
	for _, frame := range frames {
		if _, err := io.WriteString(w, frame+"\n\n"); err != nil {
			return
		}
		if flusher, ok := w.(http.Flusher); ok {
			flusher.Flush()
		}
	}
}

func TestStreamMapsDeltasFinishUsageAndToolCallFragments(t *testing.T) {
	fake := newFakeProvider(t, func(w http.ResponseWriter, _ *http.Request, _ map[string]any) {
		sseFrames(t, w,
			`data: {"choices":[{"delta":{"role":"assistant","content":"Hel"}}]}`,
			`data: {"choices":[{"delta":{"content":"lo"}}]}`,
			`data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1","function":{"name":"lookup","arguments":"{\"city\":"}}]}}]}`,
			`data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\"Paris\"}"}}]}}]}`,
			`data: {"choices":[{"delta":{"content":""},"finish_reason":"tool_calls"}]}`,
			`data: {"choices":[],"usage":{"prompt_tokens":9,"completion_tokens":4}}`,
			`data: [DONE]`,
		)
	})
	candidate := chatCandidate(t122EndpointID, t122Credential)
	providers := realProviders(t, fake.url(), secretLookup)
	provider, err := providers.StreamingFor(candidate)
	if err != nil {
		t.Fatalf("StreamingFor: %v", err)
	}

	params := chatParams(map[string]any{"role": "user", "content": "look it up"})
	params["tools"] = []any{map[string]any{
		"name": "lookup", "capability_version": "11111111-1111-4111-8111-111111111111",
	}}
	chunks, response, err := collectStream(t, provider, CallRequest{Candidate: candidate, Params: params})
	if err != nil {
		t.Fatalf("Stream: %v", err)
	}
	var text strings.Builder
	var toolDeltas int
	for _, chunk := range chunks {
		switch chunk.Type {
		case StreamDelta:
			text.WriteString(chunk.Text)
		case StreamToolDelta:
			toolDeltas += len(chunk.ToolCalls)
		}
	}
	if text.String() != "Hello" || response.Data["text"] != "Hello" {
		t.Fatalf("streamed text = %q, answer text = %#v", text.String(), response.Data["text"])
	}
	if toolDeltas != 2 {
		t.Fatalf("tool deltas = %d", toolDeltas)
	}
	calls, _ := response.Data["tool_calls"].([]any)
	if len(calls) != 1 {
		t.Fatalf("tool_calls = %#v", response.Data["tool_calls"])
	}
	call, _ := calls[0].(map[string]any)
	arguments, _ := call["arguments"].(map[string]any)
	if call["id"] != "call_1" || call["name"] != "lookup" || arguments["city"] != "Paris" {
		t.Fatalf("tool call from the stream = %#v", call)
	}
	usage, _ := response.Data["usage"].(map[string]any)
	if usage["input_tokens"] != int64(9) || usage["output_tokens"] != int64(4) {
		t.Fatalf("usage = %#v", response.Data["usage"])
	}
	if response.TokensIn != 9 || response.TokensOut != 4 || response.ChargedUnits != 1700 {
		t.Fatalf("response usage = %d/%d/%d", response.TokensIn, response.TokensOut, response.ChargedUnits)
	}

	request := fake.requests()[0]
	if request.Accept != "text/event-stream" || request.Body["stream"] != true {
		t.Fatalf("stream request = %#v / %#v", request.Accept, request.Body["stream"])
	}
	options, _ := request.Body["stream_options"].(map[string]any)
	if options["include_usage"] != true {
		t.Fatalf("stream_options = %#v", request.Body["stream_options"])
	}
}

func TestStreamRefusesANonSSEAnswer(t *testing.T) {
	fake := newFakeProvider(t, func(w http.ResponseWriter, _ *http.Request, _ map[string]any) {
		writeJSON(w, http.StatusOK, `{"choices":[{"message":{"content":"not a stream"}}]}`)
	})
	candidate := chatCandidate(t122EndpointID, t122Credential)
	providers := realProviders(t, fake.url(), secretLookup)
	provider, _ := providers.StreamingFor(candidate)
	_, _, err := collectStream(t, provider, CallRequest{
		Candidate: candidate, Params: chatParams(map[string]any{"role": "user", "content": "hi"}),
	})
	if !errors.Is(err, ErrResponseInvalid) || IsTransient(err) {
		t.Fatalf("a JSON answer to a stream request must be a terminal protocol refusal, got %v", err)
	}
}

// The stub path must be unchanged by the adapter: a stub candidate still only
// routes to the stub provider, and a real candidate under a stub profile takes
// the real path (never the stub).
func TestTheAdapterDoesNotChangeTheStubRouting(t *testing.T) {
	providers := realProviders(t, "https://models.internal.example/v1", secretLookup)
	stubProvider, err := providers.For(&Candidate{Provider: "stub", ModelID: "stub-model"})
	if err != nil {
		t.Fatalf("stub candidate: %v", err)
	}
	if stubProvider.Name() != "stub" {
		t.Fatalf("stub candidate routed to %q", stubProvider.Name())
	}
	realProvider, err := providers.For(chatCandidate(t122EndpointID, t122Credential))
	if err != nil {
		t.Fatalf("real candidate: %v", err)
	}
	if realProvider.Name() != "real" || strings.Contains(realProvider.Name(), t122Secret) {
		t.Fatalf("real candidate routed to %q", realProvider.Name())
	}
}

// hijackAndClose closes the raw connection after the frames written so far: the
// real transport sees EOF mid-stream (a half answer), not a finished response.
func hijackAndClose(t *testing.T, w http.ResponseWriter) {
	t.Helper()
	hijacker, ok := w.(http.Hijacker)
	if !ok {
		t.Fatal("the test server cannot hijack the connection")
	}
	connection, _, err := hijacker.Hijack()
	if err != nil {
		t.Fatalf("hijacking the connection: %v", err)
	}
	_ = connection.Close()
}

func TestStreamHalfwayFailureIsTransientAndReturnsNoPartialResponse(t *testing.T) {
	fake := newFakeProvider(t, func(w http.ResponseWriter, _ *http.Request, _ map[string]any) {
		w.Header().Set("Content-Type", "text/event-stream")
		w.WriteHeader(http.StatusOK)
		flusher, _ := w.(http.Flusher)
		for _, frame := range []string{
			`data: {"choices":[{"delta":{"content":"partial-"}}]}`,
			`data: {"choices":[{"delta":{"content":"answer"}}]}`,
		} {
			_, _ = io.WriteString(w, frame+"\n\n")
			if flusher != nil {
				flusher.Flush()
			}
		}
		hijackAndClose(t, w)
	})
	candidate := chatCandidate(t122EndpointID, t122Credential)
	providers := realProviders(t, fake.url(), secretLookup)
	provider, _ := providers.StreamingFor(candidate)

	chunks, response, err := collectStream(t, provider, CallRequest{
		Candidate: candidate, Params: chatParams(map[string]any{"role": "user", "content": "hi"}),
	})
	if !IsTransient(err) {
		t.Fatalf("a half stream must be transient, got %v", err)
	}
	if len(chunks) != 2 {
		t.Fatalf("the temporary deltas were emitted before the failure: %#v", chunks)
	}
	if response.Data != nil || response.Output != nil {
		t.Fatalf("a half stream must not return a partial answer: %#v", response)
	}
}

func TestStreamErrorFrameIsTransient(t *testing.T) {
	fake := newFakeProvider(t, func(w http.ResponseWriter, _ *http.Request, _ map[string]any) {
		sseFrames(t, w,
			`data: {"choices":[{"delta":{"content":"before"}}]}`,
			`data: {"error":{"message":"provider overloaded"}}`,
		)
	})
	candidate := chatCandidate(t122EndpointID, t122Credential)
	providers := realProviders(t, fake.url(), secretLookup)
	provider, _ := providers.StreamingFor(candidate)
	_, response, err := collectStream(t, provider, CallRequest{
		Candidate: candidate, Params: chatParams(map[string]any{"role": "user", "content": "hi"}),
	})
	if !IsTransient(err) {
		t.Fatalf("an error frame must be transient, got %v", err)
	}
	if response.Data != nil {
		t.Fatalf("an error frame must not produce an answer: %#v", response)
	}
}

func TestStreamStopsWhenTheConsumerStops(t *testing.T) {
	stop := errors.New("consumer stopped")
	fake := newFakeProvider(t, func(w http.ResponseWriter, _ *http.Request, _ map[string]any) {
		sseFrames(t, w,
			`data: {"choices":[{"delta":{"content":"one"}}]}`,
			`data: {"choices":[{"delta":{"content":"two"}}]}`,
			`data: [DONE]`,
		)
	})
	candidate := chatCandidate(t122EndpointID, t122Credential)
	providers := realProviders(t, fake.url(), secretLookup)
	provider, _ := providers.StreamingFor(candidate)
	var chunks []Chunk
	response, err := provider.Stream(context.Background(), CallRequest{
		Candidate: candidate, Params: chatParams(map[string]any{"role": "user", "content": "hi"}),
	}, func(chunk Chunk) error {
		chunks = append(chunks, chunk)
		return stop
	})
	if !errors.Is(err, stop) {
		t.Fatalf("the consumer error must be returned unchanged, got %v", err)
	}
	if len(chunks) != 1 {
		t.Fatalf("the adapter must stop at the first consumer error, got %#v", chunks)
	}
	if response.Data != nil {
		t.Fatalf("a stopped stream must not produce an answer: %#v", response)
	}
}

func TestStreamCancellationIsTheCallerCancellation(t *testing.T) {
	release := make(chan struct{})
	fake := newFakeProvider(t, func(w http.ResponseWriter, _ *http.Request, _ map[string]any) {
		w.Header().Set("Content-Type", "text/event-stream")
		w.WriteHeader(http.StatusOK)
		flusher, _ := w.(http.Flusher)
		_, _ = io.WriteString(w, `data: {"choices":[{"delta":{"content":"tick"}}]}`+"\n\n")
		if flusher != nil {
			flusher.Flush()
		}
		<-release
	})
	t.Cleanup(func() { close(release) })
	candidate := chatCandidate(t122EndpointID, t122Credential)
	providers := realProviders(t, fake.url(), secretLookup)
	provider, _ := providers.StreamingFor(candidate)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	var chunks []Chunk
	_, err := provider.Stream(ctx, CallRequest{
		Candidate: candidate, Params: chatParams(map[string]any{"role": "user", "content": "hi"}),
	}, func(chunk Chunk) error {
		chunks = append(chunks, chunk)
		cancel()
		return nil
	})
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("a cancelled stream must return the caller cancellation, got %v", err)
	}
	if len(chunks) != 1 {
		t.Fatalf("only the pre-cancel delta may be emitted, got %#v", chunks)
	}
	if IsTransient(err) {
		t.Fatal("a caller cancellation must not be labelled a transient provider failure")
	}
}
