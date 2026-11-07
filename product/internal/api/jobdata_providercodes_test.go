package api

// F06/T122 B2 review fix H3: the job-scoped internal model endpoint answers with
// the shared provider vocabulary (internal/providercodes), not local literals.
// This test drives the real writer and reads the JSON code back, so a renamed or
// dropped code cannot pass unnoticed.

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"orchara/platform/internal/modelproxy"
	"orchara/platform/internal/providercodes"
)

func TestJobDataErrorUsesTheSharedProviderVocabulary(t *testing.T) {
	cases := []struct {
		name string
		err  error
		want string
	}{
		{"not configured", modelproxy.ErrNotConfigured, providercodes.NotConfigured},
		{"adapter unavailable", modelproxy.ErrAdapterUnavailable, providercodes.DispatchAdapterUnavailable},
		{"destination not approved", modelproxy.ErrDestinationUnapproved, providercodes.DestinationNotApproved},
		{"transient", &modelproxy.TransientError{Reason: "http status 503"}, providercodes.Transient},
		{"response too large", modelproxy.ErrResponseTooLarge, providercodes.ResponseTooLarge},
		{"response invalid", modelproxy.ErrResponseInvalid, providercodes.ResponseInvalid},
		{"permission denied", &modelproxy.ProviderStatusError{StatusCode: http.StatusForbidden}, providercodes.PermissionDenied},
	}
	server := &Server{}
	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			recorder := httptest.NewRecorder()
			request := httptest.NewRequest(http.MethodPost,
				"/internal/v1/background/jobs/11111111-1111-4111-8111-111111111111/model-calls", nil)
			if handled := server.writeJobDataError(recorder, request, testCase.err); handled {
				t.Fatalf("a provider error must be answered as an error, not as success")
			}
			if recorder.Code != http.StatusServiceUnavailable {
				t.Fatalf("status = %d, want 503 (body=%s)", recorder.Code, recorder.Body.String())
			}
			var envelope struct {
				Error struct {
					Code string `json:"code"`
				} `json:"error"`
			}
			if err := json.Unmarshal(recorder.Body.Bytes(), &envelope); err != nil {
				t.Fatalf("decoding the error envelope: %v (%s)", err, recorder.Body.String())
			}
			if envelope.Error.Code != testCase.want {
				t.Fatalf("error code = %q, want %q", envelope.Error.Code, testCase.want)
			}
			if !providercodes.Known(envelope.Error.Code) {
				t.Fatalf("code %q is not in the shared provider vocabulary", envelope.Error.Code)
			}
		})
	}
}
