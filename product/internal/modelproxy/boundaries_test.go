package modelproxy

import (
	"bufio"
	"context"
	"errors"
	"net/http"
	"strings"
	"testing"
)

func TestSSELineLimitIncludesEveryFragment(t *testing.T) {
	for _, suffix := range []string{"\n", ""} {
		var total int64
		_, err := readSSELine(bufio.NewReaderSize(strings.NewReader(
			strings.Repeat("x", maxSSELineBytes+1)+suffix), 4096), &total)
		if !errors.Is(err, ErrResponseTooLarge) {
			t.Fatalf("oversized line suffix=%q: total=%d err=%v", suffix, total, err)
		}
	}
	// Small lines remain valid even after the cumulative size exceeds 1 MiB.
	var total int64 = maxSSELineBytes
	if line, err := readSSELine(bufio.NewReader(strings.NewReader("data: ok\n")), &total); err != nil || line != "data: ok" {
		t.Fatalf("short line: %q %v", line, err)
	}
}

func TestStreamErrorFramesKeepTerminalProviderSemantics(t *testing.T) {
	for _, tc := range []struct {
		payload, code string
	}{
		{`{"type":"permission_error","code":"permission_denied","status":403}`, "provider_permission_denied"},
		{`{"type":"invalid_request_error","code":"invalid_json_schema"}`, "provider_request_rejected"},
		{`{"code":"model_not_found"}`, "provider_model_not_found"},
	} {
		t.Run(tc.code, func(t *testing.T) {
			response, err := (&realProvider{}).readStream(context.Background(), CallRequest{
				Candidate: chatCandidate(t122EndpointID, t122Credential),
			}, providerConfig{}, strings.NewReader("data: {\"error\":"+tc.payload+"}\n\n"), func(Chunk) error { return nil })
			if IsTransient(err) || ProviderCode(err) != tc.code || response.Data != nil {
				t.Fatalf("code=%s transient=%v err=%v response=%#v", ProviderCode(err), IsTransient(err), err, response)
			}
		})
	}
}

func TestCompatibilityCannotPassFromAnArbitraryJSONObject(t *testing.T) {
	for _, check := range []string{"tool_call", "streaming", "cancellation"} {
		request := CallRequest{
			Candidate: chatCandidate(t122EndpointID, t122Credential),
			Params:    map[string]any{"task": "model_compatibility", "check": check},
			Fixture:   []byte(`{"check":"structured_output"}`),
		}
		data := map[string]any{"tool_calls": []any{}}
		applyFixtureMode(request, "{}", data)
		verdict, _ := data["check"].(map[string]any)
		if verdict["passed"] == true {
			t.Fatalf("%s passed without an observed capability: %#v", check, verdict)
		}
	}
}

func TestRealCompatibilityProbesExerciseTheirOwnWireProtocol(t *testing.T) {
	for _, check := range []string{"structured_output", "tool_call", "streaming", "cancellation"} {
		t.Run(check, func(t *testing.T) {
			fake := newFakeProvider(t, func(w http.ResponseWriter, _ *http.Request, body map[string]any) {
				switch check {
				case "structured_output":
					writeJSON(w, 200, `{"choices":[{"message":{"content":"{\"ok\":true}"}}],"usage":{"prompt_tokens":3,"completion_tokens":2}}`)
				case "tool_call":
					if body["tool_choice"] == nil || body["tools"] == nil || body["response_format"] != nil {
						t.Errorf("tool probe did not request the function: %#v", body)
					}
					writeJSON(w, 200, `{"choices":[{"message":{"tool_calls":[{"id":"probe","function":{"name":"orchara_probe","arguments":"{\"value\":\"ok\"}"}}]}}],"usage":{"prompt_tokens":3,"completion_tokens":2}}`)
				default:
					if body["stream"] != true {
						t.Error("stream/cancel probe did not request SSE")
					}
					sseFrames(t, w,
						`data: {"choices":[{"delta":{"content":"{\"ok\":true}"}}]}`,
						`data: {"choices":[{"delta":{},"finish_reason":"stop"}]}`,
						`data: {"choices":[],"usage":{"prompt_tokens":3,"completion_tokens":2}}`,
						"data: [DONE]",
					)
				}
			})
			candidate := chatCandidate(t122EndpointID, t122Credential)
			provider, _ := realProviders(t, fake.url(), secretLookup).For(candidate)
			response, err := provider.Call(context.Background(), CallRequest{
				Candidate: candidate, Fixture: []byte(`{"check":"structured_output"}`),
				Params: map[string]any{"task": "model_compatibility", "check": check},
			})
			if err != nil {
				t.Fatal(err)
			}
			verdict, _ := response.Data["check"].(map[string]any)
			if verdict["name"] != check || verdict["passed"] != true || len(fake.requests()) != 1 {
				t.Fatalf("probe verdict=%#v requests=%d", verdict, len(fake.requests()))
			}
			if check == "cancellation" && response.Data["usage_observed"] != false {
				t.Fatal("interrupted usage must remain unknown")
			}
		})
	}
}

func TestStructuredCompatibilityRequiresObservedUsage(t *testing.T) {
	fake := newFakeProvider(t, func(w http.ResponseWriter, _ *http.Request, _ map[string]any) {
		writeJSON(w, 200, `{"choices":[{"message":{"content":"{\"ok\":true}"}}]}`)
	})
	candidate := chatCandidate(t122EndpointID, t122Credential)
	provider, _ := realProviders(t, fake.url(), secretLookup).For(candidate)
	response, err := provider.Call(context.Background(), CallRequest{
		Candidate: candidate, Fixture: []byte(`{"check":"structured_output"}`),
		Params: map[string]any{"task": "model_compatibility", "check": "structured_output"},
	})
	if err != nil {
		t.Fatal(err)
	}
	if response.Data["check"].(map[string]any)["passed"] != false {
		t.Fatal("an unmetered response must not pass the usage compatibility gate")
	}
}
