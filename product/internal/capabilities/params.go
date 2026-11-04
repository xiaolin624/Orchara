package capabilities

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"strings"

	"orchara/platform/internal/jcs"
	"orchara/platform/internal/uuid"
)

// defaultMaxOutputTokens bounds a model call when the profile config does not
// declare `max_output_tokens`. It is deliberately small: an undeclared cap must
// not mean "unbounded".
const defaultMaxOutputTokens = 4096

// maxPhysicalAttempts is the hard ceiling: the server retries a transient
// provider failure at most twice, so one operation never has more than three
// physical attempt rows. The worker adapter has no retry of its own.
const maxPhysicalAttempts = 3

// ModelProfile is the slice of platform.model_profile_versions a capability
// call needs. It is loaded by the service, never supplied by the worker.
type ModelProfile struct {
	ID              string
	Provider        string
	ModelID         string
	EndpointRef     string
	SecretRef       string
	Config          map[string]any
	ConfigJSON      json.RawMessage
	ValidationState string
	Revoked         bool
}

// ToolSpec is the slice of platform.tool_versions a capability call needs.
type ToolSpec struct {
	ID          string
	OwnerID     string
	State       string
	Approved    bool
	RiskClass   string
	InputSchema map[string]any
}

// modelTool is one tool declared in a model call's params.
type modelTool struct {
	Name    string
	Version string
	Spec    *ToolSpec
}

// ModelParams is the validated shape of a model capability's params.
type ModelParams struct {
	Messages          []map[string]any
	Tools             []modelTool
	ResponseSchema    map[string]any
	MaxOutputTokens   int64
	StubToolCalls     []any
	StubTransientFail int64
}

// allowedModelParamKeys is the closed params vocabulary of a model call. The two
// stub_* keys are documented dev-stub hints (they only do anything when the
// stub provider is the registered provider); every other key is a real request
// field.
var allowedModelParamKeys = map[string]bool{
	"messages":                true,
	"tools":                   true,
	"response_schema":         true,
	"max_output_tokens":       true,
	"task":                    true,
	"stub_tool_calls":         true,
	"stub_transient_failures": true,
	// T044 streaming-stub hints. They are labelled stub parameters and are only
	// meaningful when the registered provider is the dev stub.
	"stub_stream_chunks":               true,
	"stub_stream_fail_after":           true,
	"stub_stream_fail_attempts":        true,
	"stub_stream_incomplete_tool_call": true,
	"stub_stream_delay_ms":             true,
}

// canonicalParams renders the params as RFC 8785 JCS bytes. Those exact bytes
// are sealed as the params artifact and hashed; a retry or replay compares the
// hash, never a re-serialised approximation.
func canonicalParams(params map[string]any) ([]byte, string, error) {
	raw, err := json.Marshal(params)
	if err != nil {
		return nil, "", fmt.Errorf("encoding capability params: %w", err)
	}
	canonical, err := jcs.CanonicalizeBytes(raw)
	if err != nil {
		return nil, "", fmt.Errorf("canonicalising capability params: %w", err)
	}
	sum := sha256.Sum256(canonical)
	return canonical, hex.EncodeToString(sum[:]), nil
}

// validateModelParams checks the closed model-call params vocabulary and returns
// its typed form. Every problem is located so the 422 detail names the field.
func validateModelParams(params map[string]any, profile *ModelProfile, tools map[string]*ToolSpec) (*ModelParams, *Error) {
	if params == nil {
		return nil, validationFailure("params", "params must be a JSON object")
	}
	for key := range params {
		if !allowedModelParamKeys[key] {
			return nil, validationFailure("params/"+key, "is not a known model capability parameter")
		}
	}
	validated := &ModelParams{MaxOutputTokens: int64(maxOutputTokensFor(profile))}
	if raw, present := params["task"]; present {
		task, ok := raw.(string)
		if !ok || task != "agent" {
			return nil, validationFailure("params/task", `task must be "agent" when present`)
		}
	}
	messages, err := validateMessages(params["messages"])
	if err != nil {
		return nil, err
	}
	validated.Messages = messages
	if raw, present := params["tools"]; present {
		declared, err := validateDeclaredTools(raw, tools)
		if err != nil {
			return nil, err
		}
		validated.Tools = declared
	}
	if raw, present := params["response_schema"]; present {
		schema, err := validateResponseSchema(raw)
		if err != nil {
			return nil, err
		}
		validated.ResponseSchema = schema
	}
	if raw, present := params["max_output_tokens"]; present {
		value, ok := jsonInteger(raw)
		if !ok || value < 1 {
			return nil, validationFailure("params/max_output_tokens", "must be a positive integer")
		}
		limit := int64(maxOutputTokensFor(profile))
		if value > limit {
			return nil, validationFailure("params/max_output_tokens",
				fmt.Sprintf("must not exceed the profile's %d-token output limit", limit))
		}
		validated.MaxOutputTokens = value
	}
	if raw, present := params["stub_tool_calls"]; present {
		calls, ok := raw.([]any)
		if !ok {
			return nil, validationFailure("params/stub_tool_calls", "must be an array")
		}
		if len(calls) > 16 {
			return nil, validationFailure("params/stub_tool_calls", "must contain at most 16 calls")
		}
		validated.StubToolCalls = calls
	}
	if raw, present := params["stub_transient_failures"]; present {
		value, ok := jsonInteger(raw)
		if !ok || value < 0 || value > maxPhysicalAttempts-1 {
			return nil, validationFailure("params/stub_transient_failures",
				fmt.Sprintf("must be an integer between 0 and %d", maxPhysicalAttempts-1))
		}
		validated.StubTransientFail = value
	}
	if raw, present := params["stub_stream_chunks"]; present {
		value, ok := jsonInteger(raw)
		if !ok || value < 1 || value > 64 {
			return nil, validationFailure("params/stub_stream_chunks",
				"must be an integer between 1 and 64")
		}
	}
	if raw, present := params["stub_stream_fail_after"]; present {
		value, ok := jsonInteger(raw)
		if !ok || value < 0 || value > 64 {
			return nil, validationFailure("params/stub_stream_fail_after",
				"must be an integer between 0 and 64")
		}
	}
	if raw, present := params["stub_stream_fail_attempts"]; present {
		value, ok := jsonInteger(raw)
		if !ok || value < 0 || value > maxPhysicalAttempts {
			return nil, validationFailure("params/stub_stream_fail_attempts",
				fmt.Sprintf("must be an integer between 0 and %d", maxPhysicalAttempts))
		}
	}
	if raw, present := params["stub_stream_incomplete_tool_call"]; present {
		if _, ok := raw.(bool); !ok {
			return nil, validationFailure("params/stub_stream_incomplete_tool_call",
				"must be a boolean")
		}
	}
	if raw, present := params["stub_stream_delay_ms"]; present {
		value, ok := jsonInteger(raw)
		if !ok || value < 0 || value > 2000 {
			return nil, validationFailure("params/stub_stream_delay_ms",
				"must be an integer between 0 and 2000")
		}
	}
	return validated, nil
}

func validateMessages(raw any) ([]map[string]any, *Error) {
	items, ok := raw.([]any)
	if !ok || len(items) == 0 {
		return nil, validationFailure("params/messages", "must be a non-empty array")
	}
	if len(items) > 256 {
		return nil, validationFailure("params/messages", "must contain at most 256 messages")
	}
	messages := make([]map[string]any, 0, len(items))
	for index, item := range items {
		field := fmt.Sprintf("params/messages/%d", index)
		message, ok := item.(map[string]any)
		if !ok {
			return nil, validationFailure(field, "must be an object")
		}
		if len(message) != 2 {
			return nil, validationFailure(field, "must contain exactly role and content")
		}
		role, ok := message["role"].(string)
		if !ok || (role != "system" && role != "user" && role != "assistant" && role != "tool") {
			return nil, validationFailure(field+"/role", "must be system, user, assistant or tool")
		}
		content, ok := message["content"].(string)
		if !ok {
			return nil, validationFailure(field+"/content", "must be a string")
		}
		if len(content) > 1<<20 {
			return nil, validationFailure(field+"/content", "exceeds the 1 MiB message limit")
		}
		messages = append(messages, map[string]any{"role": role, "content": content})
	}
	return messages, nil
}

func validateDeclaredTools(raw any, tools map[string]*ToolSpec) ([]modelTool, *Error) {
	items, ok := raw.([]any)
	if !ok {
		return nil, validationFailure("params/tools", "must be an array")
	}
	if len(items) > 32 {
		return nil, validationFailure("params/tools", "must contain at most 32 tools")
	}
	declared := make([]modelTool, 0, len(items))
	names := make(map[string]bool, len(items))
	for index, item := range items {
		field := fmt.Sprintf("params/tools/%d", index)
		entry, ok := item.(map[string]any)
		if !ok || len(entry) != 2 {
			return nil, validationFailure(field, "must be an object with exactly name and capability_version")
		}
		name, ok := entry["name"].(string)
		if !ok || !validToolName(name) {
			return nil, validationFailure(field+"/name", "must be 1..64 letters, digits, dot, dash or underscore")
		}
		version, ok := entry["capability_version"].(string)
		if !ok || !uuid.Valid(strings.TrimSpace(version)) {
			return nil, validationFailure(field+"/capability_version", "must be a UUID")
		}
		version = strings.ToLower(strings.TrimSpace(version))
		if names[name] {
			return nil, validationFailure(field+"/name", "declares a duplicate tool name")
		}
		names[name] = true
		spec, allowed := tools[version]
		if !allowed {
			return nil, validationFailure(field+"/capability_version",
				"is not a ready, approved tool version in this run's execution manifest")
		}
		declared = append(declared, modelTool{Name: name, Version: version, Spec: spec})
	}
	return declared, nil
}

func validateResponseSchema(raw any) (map[string]any, *Error) {
	schema, ok := raw.(map[string]any)
	if !ok {
		return nil, validationFailure("params/response_schema", "must be an object schema")
	}
	if kind, present := schema["type"]; !present || kind != "object" {
		return nil, validationFailure("params/response_schema/type", "must be object")
	}
	if properties, present := schema["properties"]; present {
		if _, ok := properties.(map[string]any); !ok {
			return nil, validationFailure("params/response_schema/properties", "must be an object")
		}
	}
	if required, present := schema["required"]; present {
		if _, ok := required.([]any); !ok {
			return nil, validationFailure("params/response_schema/required", "must be an array")
		}
	}
	return schema, nil
}

func validateToolParams(params map[string]any, schema map[string]any) *Error {
	if params == nil {
		return validationFailure("params", "params must be a JSON object of tool arguments")
	}
	if problems := ValidateValue(schema, params); len(problems) > 0 {
		return &Error{
			Status:  422,
			Code:    CodeParamsInvalid,
			Message: problems[0].Message,
			Field:   "params" + problems[0].Field,
		}
	}
	return nil
}

// validateProviderResponse validates what the provider returned before anything
// is sealed or shown to the worker. It returns the platform projection (the only
// shape a worker may see) and located problems.
type providerProjection struct {
	Text       string          `json:"text"`
	Structured map[string]any  `json:"structured,omitempty"`
	ToolCalls  []projectedCall `json:"tool_calls"`
	Usage      *projectedUsage `json:"usage,omitempty"`
	Stub       bool            `json:"stub"`
	TokensIn   int64
	TokensOut  int64
}

type projectedCall struct {
	ID        string         `json:"id"`
	Name      string         `json:"name"`
	Arguments map[string]any `json:"arguments"`
}

type projectedUsage struct {
	InputTokens  int64 `json:"input_tokens"`
	OutputTokens int64 `json:"output_tokens"`
}

func validateProviderResponse(data map[string]any, params *ModelParams) (*providerProjection, []SchemaProblem) {
	problems := make([]SchemaProblem, 0)
	projection := &providerProjection{Text: "", ToolCalls: []projectedCall{}}
	if data == nil {
		return nil, []SchemaProblem{{Field: "/", Message: "the provider returned no object"}}
	}
	if text, present := data["text"]; present {
		value, ok := text.(string)
		if !ok {
			problems = append(problems, SchemaProblem{Field: "/text", Message: "must be a string"})
		} else {
			projection.Text = value
		}
	}
	if stub, present := data["stub"]; present {
		value, ok := stub.(bool)
		if !ok {
			problems = append(problems, SchemaProblem{Field: "/stub", Message: "must be a boolean"})
		} else {
			projection.Stub = value
		}
	}
	if usage, present := data["usage"]; present {
		parsed, usageProblems := parseUsage(usage)
		problems = append(problems, usageProblems...)
		if parsed != nil {
			projection.Usage = parsed
			projection.TokensIn = parsed.InputTokens
			projection.TokensOut = parsed.OutputTokens
		}
	}
	declared := make(map[string]modelTool, len(params.Tools))
	for _, tool := range params.Tools {
		declared[tool.Name] = tool
	}
	if rawCalls, present := data["tool_calls"]; present {
		calls, ok := rawCalls.([]any)
		if !ok {
			problems = append(problems, SchemaProblem{Field: "/tool_calls", Message: "must be an array"})
		} else {
			seen := make(map[string]bool, len(calls))
			for index, raw := range calls {
				field := fmt.Sprintf("/tool_calls/%d", index)
				call, callProblems := validateToolCall(raw, field, declared, seen)
				problems = append(problems, callProblems...)
				if call != nil {
					projection.ToolCalls = append(projection.ToolCalls, *call)
				}
			}
		}
	}
	if rawStructured, present := data["structured"]; present {
		structured, ok := rawStructured.(map[string]any)
		if !ok {
			problems = append(problems, SchemaProblem{Field: "/structured", Message: "must be an object"})
		} else if params.ResponseSchema != nil {
			structuredProblems := ValidateValue(params.ResponseSchema, structured)
			for _, problem := range structuredProblems {
				problems = append(problems, SchemaProblem{
					Field: "/structured" + problem.Field, Message: problem.Message,
				})
			}
			projection.Structured = structured
		} else {
			// A structured answer was not requested. It is still a valid
			// platform-observable value, but the declared contract said text, so
			// refuse it rather than silently widening the call.
			problems = append(problems, SchemaProblem{
				Field: "/structured", Message: "was not requested by params/response_schema",
			})
		}
	} else if params.ResponseSchema != nil {
		problems = append(problems, SchemaProblem{
			Field: "/structured", Message: "is required because params/response_schema was declared",
		})
	}
	return projection, problems
}

func validateToolCall(raw any, field string, declared map[string]modelTool, seen map[string]bool) (*projectedCall, []SchemaProblem) {
	problems := make([]SchemaProblem, 0)
	call, ok := raw.(map[string]any)
	if !ok || len(call) != 3 {
		return nil, []SchemaProblem{{Field: field, Message: "must be an object with exactly id, name and arguments"}}
	}
	id, ok := call["id"].(string)
	if !ok || strings.TrimSpace(id) == "" || len(id) > 128 {
		problems = append(problems, SchemaProblem{Field: field + "/id", Message: "must be a non-empty string of at most 128 characters"})
	} else if seen[id] {
		problems = append(problems, SchemaProblem{Field: field + "/id", Message: "duplicates a tool-call id in this response"})
	} else {
		seen[id] = true
	}
	name, ok := call["name"].(string)
	if !ok || name == "" {
		problems = append(problems, SchemaProblem{Field: field + "/name", Message: "must be a non-empty string"})
	}
	tool, known := declared[name]
	if !known && name != "" {
		problems = append(problems, SchemaProblem{Field: field + "/name", Message: "is not a tool declared by this call"})
	}
	arguments, ok := call["arguments"].(map[string]any)
	if !ok {
		problems = append(problems, SchemaProblem{Field: field + "/arguments", Message: "must be a JSON object"})
	} else if known {
		for _, problem := range ValidateValue(tool.Spec.InputSchema, arguments) {
			problems = append(problems, SchemaProblem{
				Field: field + "/arguments" + problem.Field, Message: problem.Message,
			})
		}
	}
	if len(problems) > 0 {
		return nil, problems
	}
	return &projectedCall{ID: id, Name: name, Arguments: arguments}, nil
}

func parseUsage(raw any) (*projectedUsage, []SchemaProblem) {
	problems := make([]SchemaProblem, 0)
	object, ok := raw.(map[string]any)
	if !ok {
		return nil, []SchemaProblem{{Field: "/usage", Message: "must be an object"}}
	}
	usage := &projectedUsage{}
	for key, target := range map[string]*int64{"input_tokens": &usage.InputTokens, "output_tokens": &usage.OutputTokens} {
		value, present := object[key]
		if !present {
			problems = append(problems, SchemaProblem{Field: "/usage/" + key, Message: "is required"})
			continue
		}
		integer, ok := jsonInteger(value)
		if !ok || integer < 0 {
			problems = append(problems, SchemaProblem{Field: "/usage/" + key, Message: "must be a non-negative integer"})
			continue
		}
		*target = integer
	}
	if len(problems) > 0 {
		return nil, problems
	}
	return usage, nil
}

func maxOutputTokensFor(profile *ModelProfile) int64 {
	if profile == nil || profile.Config == nil {
		return defaultMaxOutputTokens
	}
	if value, present := profile.Config["max_output_tokens"]; present {
		if integer, ok := jsonInteger(value); ok && integer > 0 {
			return integer
		}
	}
	return defaultMaxOutputTokens
}

func jsonInteger(raw any) (int64, bool) {
	switch value := raw.(type) {
	case float64:
		if value < -1e18 || value > 1e18 || value != float64(int64(value)) {
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

func validToolName(name string) bool {
	if len(name) == 0 || len(name) > 64 {
		return false
	}
	for _, char := range name {
		if (char >= 'a' && char <= 'z') || (char >= 'A' && char <= 'Z') ||
			(char >= '0' && char <= '9') || char == '_' || char == '-' || char == '.' {
			continue
		}
		return false
	}
	return true
}
