package modelproxy

// F06/T122: the OpenAI-compatible chat-completions protocol adapter.
//
// The adapter is the one vendor protocol this build speaks. It is deliberately
// narrow and literal:
//
//   - The destination is deployment configuration (`ORCHARA_MODEL_ENDPOINT`),
//     never a request value. A candidate only chooses a *registered* destination
//     reference; an unregistered reference (or a URL stored in a row) is refused
//     before anything is dispatched.
//   - The credential is resolved inside Go from the candidate's `secret_ref`
//     (legacy `env:`/`file:` references, or a deployer catalog credential whose
//     entry names an environment variable). It is placed only in the outbound
//     `Authorization` header and never appears in Data, Output, an error string
//     or a ledger field.
//   - The registered `config.protocol` decides whether this adapter applies. The
//     only supported protocol is `chat_compatible`; anything else (including an
//     absent protocol) is `ErrAdapterUnavailable`, the honest "no adapter for
//     this protocol" answer.
//   - One `Call` is exactly one physical HTTP request. The adapter has no retry
//     loop of its own; the capability service owns the bounded retry and
//     compatible-fallback policy. Transport-level replays are disabled.
//   - The response is size-bounded, JSON-shape-checked and mapped to the same
//     ProviderResponse projection the stub produces (text, tool_calls,
//     structured, usage), then sealed as RFC 8785 canonical bytes.

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"net"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"

	"orchara/platform/internal/budget"
	"orchara/platform/internal/config"
	"orchara/platform/internal/modelregistry"
)

const (
	// chatCompletionsPath is appended to the deployed base endpoint unless the
	// endpoint already names the full path (then it is used verbatim).
	chatCompletionsPath = "/chat/completions"
	// maxProviderResponseBytes bounds one provider answer. It is a protocol
	// limit, not a business limit: a larger body is refused without retry.
	maxProviderResponseBytes = 8 << 20
	// maxSSELineBytes bounds one SSE line so a stream cannot grow an unbounded
	// buffer before the total limit is reached.
	maxSSELineBytes = 1 << 20
	// defaultProviderTimeout bounds one physical request when the registration
	// does not declare `config.timeout_ms`.
	defaultProviderTimeout = 30 * time.Second
	// minProviderTimeout/maxProviderTimeout clamp a registered timeout so a
	// misconfiguration cannot make a call unbounded (or instantly hopeless).
	minProviderTimeout = 50 * time.Millisecond
	maxProviderTimeout = 10 * time.Minute
)

// Errors below are non-transient, stable protocol refusals. They are declared
// next to the adapter because they describe its wire contract, not a vendor.
var (
	// ErrDestinationUnapproved means the deployed endpoint is absent/malformed or
	// the candidate names a destination the deployer catalog did not register.
	ErrDestinationUnapproved = errors.New("the model endpoint destination is not approved by the deployment catalog")
	// ErrResponseInvalid means the provider answered with something that is not
	// the chat-completions protocol (malformed JSON, no choices, bad tool call).
	ErrResponseInvalid = errors.New("the model provider response is not a valid chat-completions answer")
	// ErrResponseTooLarge means the provider body exceeded the 8 MiB protocol
	// limit. It is refused, never truncated and parsed.
	ErrResponseTooLarge = errors.New("the model provider response exceeds the protocol size limit")
	// ErrCallInvalid means the call carries neither chat messages nor a fixture
	// the adapter can turn into one; it is a platform-side mapping failure.
	ErrCallInvalid = errors.New("the model call cannot be mapped to the chat-completions protocol")
)

// ProviderStatusError is a non-transient HTTP refusal from the provider. Only
// the status code is kept: a provider body is never part of an error (it may
// echo request content or credential fragments).
type ProviderStatusError struct {
	StatusCode int
}

func (e *ProviderStatusError) Error() string {
	return fmt.Sprintf("the model provider refused the request: http status %d", e.StatusCode)
}

// ProviderCode is the stable, non-secret code of this refusal. It is the code
// the capability ledger records.
func (e *ProviderStatusError) ProviderCode() string {
	switch {
	case e.StatusCode == http.StatusUnauthorized, e.StatusCode == http.StatusForbidden:
		return "provider_permission_denied"
	case e.StatusCode == http.StatusNotFound:
		return "provider_model_not_found"
	case e.StatusCode >= 300 && e.StatusCode < 400:
		return "provider_redirected"
	case e.StatusCode >= 400 && e.StatusCode < 500:
		return "provider_request_rejected"
	default:
		return "provider_error"
	}
}

// ProviderCode returns the stable code of a typed non-transient provider
// failure, or "" when the error carries none. Transient failures are classified
// by IsTransient, never by this code.
func ProviderCode(err error) string {
	if err == nil {
		return ""
	}
	var coded interface{ ProviderCode() string }
	if errors.As(err, &coded) {
		return coded.ProviderCode()
	}
	switch {
	case errors.Is(err, ErrResponseInvalid):
		return "provider_response_invalid"
	case errors.Is(err, ErrResponseTooLarge):
		return "provider_response_too_large"
	case errors.Is(err, ErrDestinationUnapproved):
		return "provider_destination_not_approved"
	case errors.Is(err, ErrCallInvalid):
		return "provider_call_invalid"
	case errors.Is(err, ErrAdapterUnavailable):
		return "adapter_unavailable"
	case errors.Is(err, ErrNotConfigured):
		return "provider_not_configured"
	case errors.Is(err, ErrCandidateUnavailable):
		return "candidate_unavailable"
	}
	return ""
}

// providerConfig is the registered slice this adapter needs. Unknown members
// are ignored: the registration owns the full config document, the adapter only
// reads its own protocol knobs.
type providerConfig struct {
	Protocol        string        `json:"protocol"`
	TimeoutMS       *int64        `json:"timeout_ms"`
	Temperature     *float64      `json:"temperature"`
	MaxOutputTokens *int64        `json:"max_output_tokens"`
	Price           *providerCost `json:"price"`
}

type providerCost struct {
	Currency         string `json:"currency"`
	InputPerMillion  string `json:"input_per_million"`
	OutputPerMillion string `json:"output_per_million"`
}

// parseProviderConfig reads the registered config. An unreadable config means
// the adapter cannot decide whether it applies, so the caller refuses closed;
// the raw config is never echoed (a hand-written row could contain anything).
func parseProviderConfig(raw json.RawMessage) (providerConfig, error) {
	trimmed := strings.TrimSpace(string(raw))
	if trimmed == "" || trimmed == "null" {
		return providerConfig{}, nil
	}
	var parsed providerConfig
	if err := json.Unmarshal([]byte(trimmed), &parsed); err != nil {
		return providerConfig{}, fmt.Errorf("%w: the registered model config is not usable", ErrAdapterUnavailable)
	}
	return parsed, nil
}

// supportsChatCompletions is the protocol gate. Only the single protocol this
// build implements is accepted.
func (c providerConfig) supportsChatCompletions() bool {
	return strings.EqualFold(strings.TrimSpace(c.Protocol), "chat_compatible")
}

func (c providerConfig) timeout() time.Duration {
	timeout := defaultProviderTimeout
	if c.TimeoutMS != nil {
		timeout = time.Duration(*c.TimeoutMS) * time.Millisecond
	}
	if timeout < minProviderTimeout {
		timeout = minProviderTimeout
	}
	if timeout > maxProviderTimeout {
		timeout = maxProviderTimeout
	}
	return timeout
}

// WithCatalog attaches the deployer catalog the adapter approves destination and
// credential references against. Without a catalog only the legacy `env:`/`file:`
// reference forms are accepted; a catalog-shaped reference is then refused.
func (p *Providers) WithCatalog(catalog *modelregistry.Catalog) *Providers {
	if p == nil {
		return nil
	}
	p.real.catalog = catalog
	return p
}

// checkDestination proves the candidate names an approved destination. An
// endpoint URL stored in a row is never accepted: only the deployment
// configuration names where bytes go.
func (p *realProvider) checkDestination(reference string) error {
	reference = strings.TrimSpace(reference)
	switch {
	case reference == "":
		return ErrDestinationUnapproved
	case strings.HasPrefix(reference, "env:") && len(reference) > len("env:"):
		// Legacy deployment reference (T024): the URL is still only deployment
		// configuration, so the reference itself cannot redirect egress.
		return nil
	case p.catalog != nil && p.catalog.HasEndpoint(reference):
		return nil
	default:
		return ErrDestinationUnapproved
	}
}

// resolveCredential reads the vendor credential inside Go.
//
// The returned value is the only place the credential exists; no error path
// includes it or the reference, so a caller may log the error.
func (p *realProvider) resolveCredential(rawRef string) (string, error) {
	reference := strings.TrimSpace(rawRef)
	if reference == "" {
		return "", ErrNotConfigured
	}
	if secret, err := resolveSecret(reference, p.secretDir, p.lookup); err == nil {
		return secret, nil
	}
	if p.catalog != nil {
		if name, ok := p.catalog.CredentialEnv(reference); ok && strings.TrimSpace(name) != "" {
			lookup := p.lookup
			if lookup == nil {
				lookup = config.FromEnv()
			}
			if value, found := lookup(strings.TrimSpace(name)); found && strings.TrimSpace(value) != "" {
				return strings.TrimSpace(value), nil
			}
		}
	}
	return "", ErrNotConfigured
}

// chatEndpoint validates the deployed endpoint and returns the exact request
// URL. The path rule is explicit: an endpoint that already ends in
// `/chat/completions` is used verbatim, anything else gets the path appended.
func chatEndpoint(endpoint string) (string, error) {
	parsed, err := url.Parse(strings.TrimSpace(endpoint))
	if err != nil || parsed.Host == "" ||
		(parsed.Scheme != "http" && parsed.Scheme != "https") ||
		parsed.User != nil || parsed.RawQuery != "" || parsed.Fragment != "" {
		return "", fmt.Errorf("%w: the deployed model endpoint must be an absolute http(s) URL without credentials, query or fragment", ErrDestinationUnapproved)
	}
	path := strings.TrimRight(parsed.Path, "/")
	if !strings.HasSuffix(path, chatCompletionsPath) {
		path += chatCompletionsPath
	}
	parsed.Path = path
	return parsed.String(), nil
}

// readLimitedBody reads at most maxProviderResponseBytes+1 and refuses a larger
// answer instead of parsing a truncated one.
func readLimitedBody(reader io.Reader) ([]byte, error) {
	content, err := io.ReadAll(io.LimitReader(reader, maxProviderResponseBytes+1))
	if err != nil {
		return nil, err
	}
	if len(content) > maxProviderResponseBytes {
		return nil, ErrResponseTooLarge
	}
	return content, nil
}

// chatHTTPClient performs exactly one physical request per Call/Stream. It
// follows no redirect (a 3xx is a provider refusal, not a second destination)
// and has no client-level timeout: the per-call context carries the registered
// timeout so a streaming body is not cut off arbitrarily.
var chatHTTPClient = &http.Client{
	Transport: &http.Transport{
		Proxy:               http.ProxyFromEnvironment,
		MaxIdleConns:        8,
		MaxIdleConnsPerHost: 4,
		IdleConnTimeout:     30 * time.Second,
		TLSHandshakeTimeout: 10 * time.Second,
	},
	CheckRedirect: func(*http.Request, []*http.Request) error {
		return http.ErrUseLastResponse
	},
}

// classifyTransportError maps a client-side failure without ever echoing the
// error text (it can name the deployment URL). A caller cancellation is
// returned as-is; a timeout or a network fault is transient.
func classifyTransportError(parent context.Context, err error) error {
	if parent != nil {
		if parentErr := parent.Err(); parentErr != nil {
			return parentErr
		}
	}
	var netErr net.Error
	if errors.As(err, &netErr) && netErr.Timeout() {
		return &TransientError{Reason: "the model provider request timed out"}
	}
	if errors.Is(err, context.DeadlineExceeded) {
		return &TransientError{Reason: "the model provider request timed out"}
	}
	return &TransientError{Reason: "the model provider request failed at the network layer"}
}

// classifyStatus separates the retryable provider states (429, 408, 5xx) from
// terminal refusals (every other status, including redirects).
func classifyStatus(status int) error {
	if status == http.StatusTooManyRequests || status == http.StatusRequestTimeout ||
		status >= 500 {
		return &TransientError{Reason: fmt.Sprintf("http status %d", status)}
	}
	return &ProviderStatusError{StatusCode: status}
}

// send performs the one physical HTTP request of an attempt and returns the
// response together with the timeout context's cancel function (the caller
// owns both the body and the cancel).
func (p *realProvider) send(
	ctx context.Context,
	endpoint, secret string,
	body []byte,
	timeout time.Duration,
	accept string,
) (*http.Response, context.CancelFunc, error) {
	if ctx == nil {
		ctx = context.Background()
	}
	timeoutCtx, cancel := context.WithTimeout(ctx, timeout)
	request, err := http.NewRequestWithContext(timeoutCtx, http.MethodPost, endpoint, bytes.NewReader(body))
	if err != nil {
		cancel()
		return nil, nil, ErrCallInvalid
	}
	// The adapter makes one physical request: clearing GetBody removes the
	// standard library's ability to replay this POST on a broken reused
	// connection (vendors are never retried behind the server's back).
	request.GetBody = nil
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("Accept", accept)
	request.Header.Set("Authorization", "Bearer "+secret)
	response, err := chatHTTPClient.Do(request)
	if err != nil {
		cancel()
		return nil, nil, classifyTransportError(ctx, err)
	}
	return response, cancel, nil
}

// buildChatRequest maps the validated capability params to one chat-completions
// body. No provider SDK and no hidden fields: the body is exactly this function.
func buildChatRequest(request CallRequest, registered providerConfig, stream bool) ([]byte, error) {
	if request.Candidate == nil {
		return nil, ErrCandidateUnavailable
	}
	messages, err := chatMessages(request)
	if err != nil {
		return nil, err
	}
	payload := map[string]any{
		"model":    request.Candidate.ModelID,
		"messages": messages,
	}
	fixture := isFixtureCall(request)
	if tools := chatTools(request.Params); len(tools) > 0 {
		// The capability params declare tool names, not schemas: the platform
		// re-validates every returned argument object against the registered
		// tool schema before it can be executed, so the wire schema is
		// deliberately permissive.
		payload["tools"] = tools
	}
	if schema, ok := request.Params["response_schema"].(map[string]any); ok && !fixture {
		payload["response_format"] = map[string]any{
			"type": "json_schema",
			"json_schema": map[string]any{
				"name":   "orchara_response",
				"schema": schema,
			},
		}
	} else if fixture {
		payload["response_format"] = map[string]any{"type": "json_object"}
	}
	if maxTokens := chatMaxTokens(request.Params, registered); maxTokens > 0 {
		payload["max_tokens"] = maxTokens
	}
	if registered.Temperature != nil {
		payload["temperature"] = *registered.Temperature
	}
	if stream {
		payload["stream"] = true
		payload["stream_options"] = map[string]any{"include_usage": true}
	}
	raw, err := json.Marshal(payload)
	if err != nil {
		return nil, ErrCallInvalid
	}
	return raw, nil
}

// isFixtureCall reports the job-scoped fixture mode: a server-built prompt
// (compatibility validation or routing control) instead of chat messages.
func isFixtureCall(request CallRequest) bool {
	_, hasMessages := request.Params["messages"]
	return !hasMessages && len(request.Fixture) > 0
}

// chatMessages projects the closed params vocabulary onto chat messages. In
// fixture mode the server-built prompt becomes a system/user pair.
func chatMessages(request CallRequest) ([]map[string]any, error) {
	if raw, present := request.Params["messages"]; present {
		items, ok := raw.([]any)
		if !ok {
			return nil, ErrCallInvalid
		}
		messages := make([]map[string]any, 0, len(items))
		for _, item := range items {
			message, ok := item.(map[string]any)
			if !ok {
				return nil, ErrCallInvalid
			}
			role, roleOK := message["role"].(string)
			content, contentOK := message["content"].(string)
			if !roleOK || !contentOK || role == "" {
				return nil, ErrCallInvalid
			}
			messages = append(messages, map[string]any{"role": role, "content": content})
		}
		if len(messages) == 0 {
			return nil, ErrCallInvalid
		}
		return messages, nil
	}
	if len(request.Fixture) > 0 {
		return fixtureMessages(request), nil
	}
	return nil, ErrCallInvalid
}

// fixtureMessages is the server-built prompt for a job-scoped fixture call. The
// routing and compatibility shapes are the two documented control-model calls;
// anything else gets the generic structured-answer instruction.
func fixtureMessages(request CallRequest) []map[string]any {
	task, _ := request.Params["task"].(string)
	switch task {
	case "route":
		return []map[string]any{
			{"role": "system", "content": "You are the Orchara routing control model. Read the JSON request and answer with exactly one JSON object: {\"decision\":\"select\",\"candidate_version_id\":\"<uuid>\"} to route to one of the listed candidate application versions, or {\"decision\":\"no_match\",\"reason\":\"<short reason>\"} when none fits. Use only candidate ids present in the request."},
			{"role": "user", "content": string(request.Fixture)},
		}
	default:
		return []map[string]any{
			{"role": "system", "content": "You are the Orchara model compatibility probe. Read the JSON fixture and answer with exactly one JSON object. Do not add prose outside the object."},
			{"role": "user", "content": string(request.Fixture)},
		}
	}
}

// chatTools maps the declared tool names to the wire tool shape. The declared
// capability_version is a platform reference and never leaves the platform.
func chatTools(params map[string]any) []any {
	raw, present := params["tools"]
	if !present {
		return nil
	}
	items, ok := raw.([]any)
	if !ok {
		return nil
	}
	tools := make([]any, 0, len(items))
	for _, item := range items {
		declared, ok := item.(map[string]any)
		if !ok {
			continue
		}
		name, _ := declared["name"].(string)
		if name == "" {
			continue
		}
		tools = append(tools, map[string]any{
			"type": "function",
			"function": map[string]any{
				"name":        name,
				"description": "",
				"parameters":  map[string]any{"type": "object", "additionalProperties": true},
			},
		})
	}
	return tools
}

// chatMaxTokens prefers the call's validated limit, then the registered one.
func chatMaxTokens(params map[string]any, registered providerConfig) int64 {
	if value, ok := jsonIntegerParam(params["max_output_tokens"]); ok && value > 0 {
		return value
	}
	if registered.MaxOutputTokens != nil && *registered.MaxOutputTokens > 0 {
		return *registered.MaxOutputTokens
	}
	return 0
}

// jsonIntegerParam accepts the JSON shapes a decoded params document can carry.
func jsonIntegerParam(raw any) (int64, bool) {
	switch value := raw.(type) {
	case float64:
		if value != float64(int64(value)) {
			return 0, false
		}
		return int64(value), true
	case int:
		return int64(value), true
	case int64:
		return value, true
	case json.Number:
		integer, err := value.Int64()
		return integer, err == nil
	default:
		return 0, false
	}
}

// chatUsage is the provider's usage object, mapped to the platform projection.
type chatUsage struct {
	InputTokens  int64
	OutputTokens int64
}

// chatUsagePayload is the wire usage object. Pointers distinguish absent from
// zero so a provider that omits usage is not recorded as a free call.
type chatUsagePayload struct {
	PromptTokens     *int64 `json:"prompt_tokens"`
	CompletionTokens *int64 `json:"completion_tokens"`
}

// decodeChatCompletion maps one 2xx body to ProviderResponse. Only the
// documented chat-completions members are read; unknown members are ignored.
func (p *realProvider) decodeChatCompletion(content []byte, request CallRequest, registered providerConfig) (ProviderResponse, error) {
	var answer struct {
		Choices []struct {
			Message struct {
				Content   json.RawMessage `json:"content"`
				ToolCalls json.RawMessage `json:"tool_calls"`
			} `json:"message"`
			FinishReason *string `json:"finish_reason"`
		} `json:"choices"`
		Usage *chatUsagePayload `json:"usage"`
	}
	if err := json.Unmarshal(content, &answer); err != nil {
		return ProviderResponse{}, ErrResponseInvalid
	}
	if len(answer.Choices) == 0 {
		return ProviderResponse{}, fmt.Errorf("%w: the answer carries no choices", ErrResponseInvalid)
	}
	choice := answer.Choices[0]
	text := ""
	if len(choice.Message.Content) > 0 && string(choice.Message.Content) != "null" {
		if err := json.Unmarshal(choice.Message.Content, &text); err != nil {
			return ProviderResponse{}, fmt.Errorf("%w: the answer content is not a string", ErrResponseInvalid)
		}
	}
	toolCalls, err := decodeToolCalls(choice.Message.ToolCalls)
	if err != nil {
		return ProviderResponse{}, err
	}
	return responseFrom(text, toolCalls, parseChatUsage(answer.Usage), request, registered)
}

// responseFrom builds the one ProviderResponse projection every path (chat,
// fixture, stream) uses. Output is the canonical bytes of Data.
func responseFrom(
	text string,
	toolCalls []any,
	usage *chatUsage,
	request CallRequest,
	registered providerConfig,
) (ProviderResponse, error) {
	if toolCalls == nil {
		toolCalls = []any{}
	}
	data := map[string]any{
		"provider":   request.Candidate.Provider,
		"model_id":   request.Candidate.ModelID,
		"stub":       false,
		"text":       text,
		"tool_calls": toolCalls,
	}
	if structured, ok := parseJSONObject(text); ok &&
		(isResponseSchemaRequested(request) || isFixtureCall(request)) {
		data["structured"] = structured
	}
	if isFixtureCall(request) {
		applyFixtureMode(request, text, data)
	}
	if usage != nil {
		data["usage"] = map[string]any{
			"input_tokens":  usage.InputTokens,
			"output_tokens": usage.OutputTokens,
		}
	}
	output, err := canonicalJSON(data)
	if err != nil {
		return ProviderResponse{}, fmt.Errorf("canonicalising the model answer: %w", err)
	}
	response := ProviderResponse{Data: data, Output: output}
	if usage != nil {
		response.TokensIn = usage.InputTokens
		response.TokensOut = usage.OutputTokens
		response.ChargedUnits = chargedUnits(registered, usage.InputTokens, usage.OutputTokens)
	}
	return response, nil
}

// decodeToolCalls maps the wire tool calls verbatim. A malformed arguments
// fragment is kept as the raw string: the capability validator refuses the call
// (non-retryable) and the adapter never invents a parsed object.
func decodeToolCalls(raw json.RawMessage) ([]any, error) {
	calls := make([]any, 0)
	trimmed := strings.TrimSpace(string(raw))
	if trimmed == "" || trimmed == "null" {
		return calls, nil
	}
	var wire []struct {
		ID       string `json:"id"`
		Type     string `json:"type"`
		Function struct {
			Name      string `json:"name"`
			Arguments string `json:"arguments"`
		} `json:"function"`
	}
	if err := json.Unmarshal([]byte(trimmed), &wire); err != nil {
		return nil, fmt.Errorf("%w: tool_calls is not an array", ErrResponseInvalid)
	}
	for _, call := range wire {
		var arguments any = map[string]any{}
		if fragment := strings.TrimSpace(call.Function.Arguments); fragment != "" {
			var parsed any
			if json.Unmarshal([]byte(fragment), &parsed) == nil {
				arguments = parsed
			} else {
				arguments = call.Function.Arguments
			}
		}
		calls = append(calls, map[string]any{
			"id":        call.ID,
			"name":      call.Function.Name,
			"arguments": arguments,
		})
	}
	return calls, nil
}

// parseJSONObject accepts exactly one JSON object (no prose, no array).
func parseJSONObject(text string) (map[string]any, bool) {
	trimmed := strings.TrimSpace(text)
	if trimmed == "" {
		return nil, false
	}
	var object map[string]any
	if err := json.Unmarshal([]byte(trimmed), &object); err != nil || object == nil {
		return nil, false
	}
	return object, true
}

func isResponseSchemaRequested(request CallRequest) bool {
	_, ok := request.Params["response_schema"].(map[string]any)
	return ok
}

// applyFixtureMode maps a job-scoped fixture answer. The compatibility probe
// reports whether the vendor answered the platform protocol with a JSON object;
// the routing control call lifts its three documented decision fields so the
// existing routing validator reads them unchanged.
func applyFixtureMode(request CallRequest, text string, data map[string]any) {
	task, _ := request.Params["task"].(string)
	if task == "route" {
		object, ok := parseJSONObject(text)
		if !ok {
			return
		}
		for _, key := range []string{"decision", "candidate_version_id", "reason"} {
			if value, ok := object[key].(string); ok {
				data[key] = value
			}
		}
		return
	}
	check := map[string]any{
		"name":   fixtureCheckName(request.Fixture),
		"passed": false,
		"reason": "structured_output",
	}
	if _, ok := parseJSONObject(text); ok {
		check["passed"] = true
		check["reason"] = ""
	}
	data["check"] = check
}

// fixtureCheckName reads the check the server-chosen fixture asks for.
func fixtureCheckName(fixture []byte) string {
	var document map[string]any
	if json.Unmarshal(fixture, &document) == nil {
		if name, ok := document["check"].(string); ok && strings.TrimSpace(name) != "" {
			return name
		}
	}
	return "structured_output"
}

// parseChatUsage returns nil for a missing/malformed usage object. A provider
// that claimed usage the platform cannot read leaves the conservative
// reservation in place (the existing usage_missing settlement) rather than
// inventing a cost.
func parseChatUsage(raw *chatUsagePayload) *chatUsage {
	if raw == nil || raw.PromptTokens == nil || raw.CompletionTokens == nil {
		return nil
	}
	if *raw.PromptTokens < 0 || *raw.CompletionTokens < 0 {
		return nil
	}
	return &chatUsage{InputTokens: *raw.PromptTokens, OutputTokens: *raw.CompletionTokens}
}

// chargedUnits applies the registered price to the actual usage in the ledger's
// 1e-8 currency units. A profile without a price (or with an unusable one)
// charges 0 and the call records the tokens it actually used.
func chargedUnits(registered providerConfig, tokensIn, tokensOut int64) int64 {
	if registered.Price == nil {
		return 0
	}
	price := budget.Price{
		Currency:         strings.TrimSpace(registered.Price.Currency),
		InputPerMillion:  strings.TrimSpace(registered.Price.InputPerMillion),
		OutputPerMillion: strings.TrimSpace(registered.Price.OutputPerMillion),
	}
	if _, err := budget.ParsePrice(price); err != nil {
		return 0
	}
	amount, err := budget.ActualCharge(price, tokensIn, tokensOut)
	if err != nil {
		return 0
	}
	units, err := decimalUnits(amount)
	if err != nil {
		return 0
	}
	return units
}

// decimalUnits parses the canonical numeric(20,8) rendering to 1e-8 units. An
// amount beyond int64 units is refused (the ledger projection is int64).
func decimalUnits(value string) (int64, error) {
	whole, fraction, _ := strings.Cut(strings.TrimSpace(value), ".")
	if fraction == "" {
		fraction = "00000000"
	}
	if len(fraction) > 8 {
		fraction = fraction[:8]
	}
	for len(fraction) < 8 {
		fraction += "0"
	}
	integer, err := strconv.ParseInt(whole, 10, 64)
	if err != nil {
		return 0, err
	}
	if integer > math.MaxInt64/100000000 {
		return 0, errors.New("amount exceeds the int64 unit range")
	}
	frac, err := strconv.ParseInt(fraction, 10, 64)
	if err != nil {
		return 0, err
	}
	return integer*100000000 + frac, nil
}

// Stream implements StreamProvider with exactly one physical SSE request per
// attempt.
//
// The mapping is the documented chat-completions stream: `delta.content` is a
// text delta, `delta.tool_calls` fragments are buffered by index, the usage-only
// frame is captured, `finish_reason` and `[DONE]` mark completion. A stream that
// ends before either marker (a half answer, a dropped connection) is a transient
// failure and returns no partial ProviderResponse: the caller must never seal or
// concatenate a truncated answer.
func (p *realProvider) Stream(
	ctx context.Context,
	request CallRequest,
	emit func(Chunk) error,
) (ProviderResponse, error) {
	if request.Candidate == nil {
		return ProviderResponse{}, ErrCandidateUnavailable
	}
	if emit == nil {
		return ProviderResponse{}, ErrCallInvalid
	}
	if err := p.Available(request.Candidate); err != nil {
		return ProviderResponse{}, err
	}
	registered, err := parseProviderConfig(request.Candidate.Config)
	if err != nil {
		return ProviderResponse{}, err
	}
	secret, err := p.resolveCredential(request.Candidate.SecretRef)
	if err != nil {
		return ProviderResponse{}, err
	}
	endpoint, err := chatEndpoint(p.endpoint)
	if err != nil {
		return ProviderResponse{}, err
	}
	body, err := buildChatRequest(request, registered, true)
	if err != nil {
		return ProviderResponse{}, err
	}
	response, cancel, err := p.send(ctx, endpoint, secret, body, registered.timeout(), "text/event-stream")
	if err != nil {
		return ProviderResponse{}, err
	}
	defer cancel()
	defer func() { _ = response.Body.Close() }()
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		_, _ = io.Copy(io.Discard, io.LimitReader(response.Body, 4<<10))
		return ProviderResponse{}, classifyStatus(response.StatusCode)
	}
	if contentType := strings.ToLower(response.Header.Get("Content-Type")); !strings.Contains(contentType, "text/event-stream") {
		return ProviderResponse{}, fmt.Errorf("%w: the streaming answer is not text/event-stream", ErrResponseInvalid)
	}
	return p.readStream(ctx, request, registered, response.Body, emit)
}

// streamChunk is one `data:` frame of the chat-completions stream.
type streamChunk struct {
	Choices []struct {
		Delta struct {
			Content   *string `json:"content"`
			ToolCalls []struct {
				Index    *int   `json:"index"`
				ID       string `json:"id"`
				Function struct {
					Name      string `json:"name"`
					Arguments string `json:"arguments"`
				} `json:"function"`
			} `json:"tool_calls"`
		} `json:"delta"`
		FinishReason *string `json:"finish_reason"`
	} `json:"choices"`
	Usage *chatUsagePayload `json:"usage"`
	Error json.RawMessage   `json:"error"`
}

// streamCall buffers one tool call's fragments. Identity may arrive in any
// fragment; arguments are concatenated in order.
type streamCall struct {
	id        string
	name      string
	arguments strings.Builder
}

func (p *realProvider) readStream(
	parent context.Context,
	request CallRequest,
	registered providerConfig,
	body io.Reader,
	emit func(Chunk) error,
) (ProviderResponse, error) {
	reader := bufio.NewReaderSize(io.LimitReader(body, maxProviderResponseBytes+1), maxSSELineBytes)
	var (
		text     strings.Builder
		calls    = map[int]*streamCall{}
		order    []int
		usage    *chatUsage
		finished bool
		done     bool
		read     int64
	)
	for {
		line, err := readSSELine(reader, &read)
		if errors.Is(err, io.EOF) {
			break
		}
		if err != nil {
			if errors.Is(err, ErrResponseTooLarge) {
				return ProviderResponse{}, err
			}
			if parent != nil && parent.Err() != nil {
				return ProviderResponse{}, parent.Err()
			}
			return ProviderResponse{}, &TransientError{Reason: "the model provider stream was interrupted"}
		}
		trimmed := strings.TrimSpace(line)
		if trimmed == "" || strings.HasPrefix(trimmed, ":") || strings.HasPrefix(trimmed, "event:") {
			continue
		}
		if !strings.HasPrefix(trimmed, "data:") {
			continue
		}
		payload := strings.TrimSpace(strings.TrimPrefix(trimmed, "data:"))
		if payload == "[DONE]" {
			done = true
			break
		}
		var chunk streamChunk
		if err := json.Unmarshal([]byte(payload), &chunk); err != nil {
			// A corrupt frame is a protocol failure, not a vendor outage: it is
			// never retried and no partial answer is returned.
			return ProviderResponse{}, fmt.Errorf("%w: the stream frame is not JSON", ErrResponseInvalid)
		}
		if len(chunk.Error) > 0 && strings.TrimSpace(string(chunk.Error)) != "null" {
			return ProviderResponse{}, &TransientError{Reason: "the model provider stream sent an error frame"}
		}
		if chunk.Usage != nil {
			if parsed := parseChatUsage(chunk.Usage); parsed != nil {
				usage = parsed
			}
		}
		if len(chunk.Choices) == 0 {
			continue
		}
		choice := chunk.Choices[0]
		if choice.FinishReason != nil {
			finished = true
		}
		if choice.Delta.Content != nil && *choice.Delta.Content != "" {
			text.WriteString(*choice.Delta.Content)
			if err := emit(Chunk{Type: StreamDelta, Text: *choice.Delta.Content}); err != nil {
				// The consumer stopped: stop reading, return no answer.
				return ProviderResponse{}, err
			}
		}
		for _, wireCall := range choice.Delta.ToolCalls {
			index := 0
			if wireCall.Index != nil {
				index = *wireCall.Index
			}
			if index < 0 {
				continue
			}
			call := calls[index]
			if call == nil {
				call = &streamCall{}
				calls[index] = call
				order = append(order, index)
			}
			if call.id == "" {
				call.id = wireCall.ID
			}
			if call.name == "" {
				call.name = wireCall.Function.Name
			}
			call.arguments.WriteString(wireCall.Function.Arguments)
			if wireCall.ID == "" && wireCall.Function.Name == "" && wireCall.Function.Arguments == "" {
				continue
			}
			if err := emit(Chunk{Type: StreamToolDelta, ToolCalls: []ToolCallDelta{{
				Index:             index,
				ID:                wireCall.ID,
				Name:              wireCall.Function.Name,
				ArgumentsFragment: wireCall.Function.Arguments,
			}}}); err != nil {
				return ProviderResponse{}, err
			}
		}
	}
	if !finished && !done {
		// EOF before finish_reason/[DONE]: a half answer. It is transient, and
		// the empty response proves no truncated content is handed back.
		return ProviderResponse{}, &TransientError{Reason: "the model provider stream ended before completion"}
	}
	return responseFrom(text.String(), completeStreamCalls(calls, order), usage, request, registered)
}

// completeStreamCalls keeps only calls whose identity is present and whose
// fragments form a complete JSON object, exactly like the capability stream
// buffer: an incomplete call is dropped, never executed.
func completeStreamCalls(calls map[int]*streamCall, order []int) []any {
	completed := make([]any, 0, len(order))
	for _, index := range order {
		call := calls[index]
		if call == nil || strings.TrimSpace(call.id) == "" || strings.TrimSpace(call.name) == "" {
			continue
		}
		raw := strings.TrimSpace(call.arguments.String())
		var arguments map[string]any
		if raw == "" || json.Unmarshal([]byte(raw), &arguments) != nil {
			continue
		}
		completed = append(completed, map[string]any{
			"id": call.id, "name": call.name, "arguments": arguments,
		})
	}
	return completed
}

// readSSELine reads one line and enforces both the per-line and the total body
// limits. A line without a trailing newline before EOF is still returned once.
func readSSELine(reader *bufio.Reader, total *int64) (string, error) {
	line, err := reader.ReadString('\n')
	*total += int64(len(line))
	if *total > maxProviderResponseBytes {
		return "", ErrResponseTooLarge
	}
	switch {
	case err == nil:
		return strings.TrimRight(line, "\r\n"), nil
	case errors.Is(err, io.EOF):
		if line != "" {
			return strings.TrimRight(line, "\r\n"), nil
		}
		return "", io.EOF
	case errors.Is(err, bufio.ErrBufferFull):
		return "", ErrResponseTooLarge
	default:
		return "", err
	}
}
