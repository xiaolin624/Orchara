package api

import (
	"bytes"
	"log"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// logFor runs one request through the middleware chain and returns the captured
// log plus the recorder.
func logFor(t *testing.T, method, target string, headers http.Header) (string, *httptest.ResponseRecorder) {
	t.Helper()
	var buffer bytes.Buffer
	logger := log.New(&buffer, "", 0)
	handler := WithRequestLogging(logger, WithRequestID(http.HandlerFunc(
		func(w http.ResponseWriter, r *http.Request) {
			w.WriteHeader(http.StatusAccepted)
			_, _ = w.Write([]byte("ok"))
		},
	)))
	request := httptest.NewRequest(method, target, nil)
	for name, values := range headers {
		for _, value := range values {
			request.Header.Add(name, value)
		}
	}
	recorder := httptest.NewRecorder()
	handler.ServeHTTP(recorder, request)
	return buffer.String(), recorder
}

func TestRequestLogHasOnlyTheAllowedFields(t *testing.T) {
	line, recorder := logFor(t, http.MethodPost, "/api/v1/auth/login?token=super-secret-value", nil)

	if !strings.HasPrefix(line, "POST /api/v1/auth/login 202 ") {
		t.Fatalf("unexpected log line: %q", line)
	}
	if strings.Contains(line, "super-secret-value") || strings.Contains(line, "?") {
		t.Fatalf("the log line carries the query string: %q", line)
	}
	if !strings.Contains(line, "request_id=") {
		t.Fatalf("the log line has no request id: %q", line)
	}
	if id := recorder.Header().Get("X-Request-ID"); id == "" {
		t.Fatal("the response did not echo X-Request-ID")
	}
}

func TestRequestLogNeverContainsBodiesCookiesOrHeaders(t *testing.T) {
	headers := http.Header{}
	headers.Set("Authorization", "Bearer header-secret-value")
	headers.Set("Cookie", "platform_session=cookie-secret-value")
	line, _ := logFor(t, http.MethodPost, "/api/v1/admin/users", headers)

	for _, secret := range []string{"header-secret-value", "cookie-secret-value"} {
		if strings.Contains(line, secret) {
			t.Fatalf("the log line leaked %q: %q", secret, line)
		}
	}
}

func TestSignedObjectLinkTokensAreRedactedFromTheLog(t *testing.T) {
	line, _ := logFor(t, http.MethodPut, "/object-links/djF8cHV0fHN0YWdpbmcvYWJj.YWJj", nil)
	if strings.Contains(line, "djF8cHV0fHN0YWdpbmcvYWJj") {
		t.Fatalf("the request log carries a working signed link: %q", line)
	}
	if !strings.Contains(line, "PUT /object-links/<redacted> 202 ") {
		t.Fatalf("unexpected redacted log line: %q", line)
	}
}

func TestClientSuppliedRequestIDIsEchoed(t *testing.T) {
	headers := http.Header{}
	headers.Set("X-Request-ID", "t015-trace-42")
	line, recorder := logFor(t, http.MethodGet, "/api/v1/auth/login", headers)

	if got := recorder.Header().Get("X-Request-ID"); got != "t015-trace-42" {
		t.Fatalf("X-Request-ID = %q, want t015-trace-42", got)
	}
	if !strings.Contains(line, "request_id=t015-trace-42") {
		t.Fatalf("the log line did not use the supplied id: %q", line)
	}
}

func TestLogInjectionInRequestIDIsRejected(t *testing.T) {
	headers := http.Header{}
	headers.Set("X-Request-ID", "line-one\nFORGED POST /admin 200")
	line, recorder := logFor(t, http.MethodGet, "/api/v1/auth/login", headers)

	if strings.Contains(line, "FORGED") || strings.Count(line, "\n") != 1 {
		t.Fatalf("a newline request id forged a log line: %q", line)
	}
	if got := recorder.Header().Get("X-Request-ID"); strings.ContainsAny(got, "\r\n") {
		t.Fatalf("the response echoed an unsafe request id: %q", got)
	}
}

func TestRequestIDFallsBackToStringWhenUnavailable(t *testing.T) {
	if got := newRequestID(); len(got) < 16 {
		t.Fatalf("generated request id %q is too short", got)
	}
}
