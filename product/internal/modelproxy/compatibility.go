package modelproxy

// These are synthetic, job-scoped probes. Each probe has its own logical key,
// ledger row and exactly one physical request. A provider's JSON declaration
// about its abilities is never evidence that those abilities were exercised.

import (
	"context"
	"errors"
)

func compatibilityCheck(request CallRequest) string {
	if !isFixtureCall(request) || request.Params["task"] != "model_compatibility" {
		return ""
	}
	if check, ok := request.Params["check"].(string); ok && check != "" {
		return check
	}
	return fixtureCheckName(request.Fixture)
}

func (p *realProvider) compatibilityStream(ctx context.Context, request CallRequest) (ProviderResponse, error) {
	check := compatibilityCheck(request)
	if check == "streaming" {
		observed := false
		response, err := p.Stream(ctx, request, func(chunk Chunk) error {
			observed = observed || (chunk.Type == StreamDelta && chunk.Text != "")
			return nil
		})
		if err != nil {
			return ProviderResponse{}, err
		}
		verdict, _ := response.Data["check"].(map[string]any)
		// Only this transport-owned observation can certify a streamed delta.
		verdict["passed"] = observed && response.Data["usage"] != nil
		if verdict["passed"] == true {
			verdict["reason"] = ""
		}
		response.Output, err = canonicalJSON(response.Data)
		return response, err
	}
	probeCtx, cancel := context.WithCancel(ctx)
	defer cancel()
	observed := false
	_, err := p.Stream(probeCtx, request, func(chunk Chunk) error {
		if chunk.Type == StreamDelta && chunk.Text != "" {
			observed = true
			cancel()
			return probeCtx.Err()
		}
		return nil
	})
	if ctx.Err() != nil {
		return ProviderResponse{}, ctx.Err()
	}
	if err != nil && (!observed || !errors.Is(err, context.Canceled)) {
		return ProviderResponse{}, err
	}
	// The result certifies transport cancellation after a real delta, not
	// vendor-side cessation or zero cost. Usage was interrupted and is unknown.
	data := map[string]any{
		"provider": request.Candidate.Provider, "model_id": request.Candidate.ModelID,
		"stub": false, "usage_observed": false,
		"check": map[string]any{
			"name": "cancellation", "passed": observed && errors.Is(err, context.Canceled),
			"reason": "cancellation_not_observed",
		},
	}
	if data["check"].(map[string]any)["passed"] == true {
		data["check"].(map[string]any)["reason"] = ""
	}
	output, encodeErr := canonicalJSON(data)
	return ProviderResponse{Data: data, Output: output}, encodeErr
}

func compatibilityTool() []any {
	return []any{map[string]any{
		"type": "function",
		"function": map[string]any{
			"name":        "orchara_probe",
			"description": "Return the supplied probe value.",
			"parameters": map[string]any{
				"type": "object", "properties": map[string]any{"value": map[string]any{
					"type": "string", "enum": []string{"ok"},
				}},
				"required": []string{"value"}, "additionalProperties": false,
			},
		},
	}}
}

func compatibilityToolPassed(data map[string]any) bool {
	calls, _ := data["tool_calls"].([]any)
	if len(calls) != 1 || data["usage"] == nil {
		return false
	}
	call, _ := calls[0].(map[string]any)
	arguments, _ := call["arguments"].(map[string]any)
	id, _ := call["id"].(string)
	return id != "" && call["name"] == "orchara_probe" && len(arguments) == 1 && arguments["value"] == "ok"
}
