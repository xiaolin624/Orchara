package api

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// The contract envelope belongs to the API surface only. A page path must keep
// the ordinary net/http answer; /api/ keeps the envelope. This pins the scope
// that T093's external-client review asked for (the previous wrapper covered
// every path on the public mux).
func TestContractErrorsForOnlyWrapsAPIPaths(t *testing.T) {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /login", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		_, _ = w.Write([]byte("<html>login</html>"))
	})
	mux.HandleFunc("GET /api/v1/known", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, http.StatusOK, map[string]string{"ok": "true"})
	})
	handler := WithRequestID(ContractErrorsFor([]string{"/api/"}, mux))

	cases := []struct {
		name        string
		path        string
		status      int
		contentType string
		json        bool
	}{
		{name: "page still works", path: "/login", status: http.StatusOK, contentType: "text/html"},
		{name: "api still works", path: "/api/v1/known", status: http.StatusOK, contentType: "application/json"},
		{name: "unknown page keeps net/http 404", path: "/no-such-page", status: http.StatusNotFound, contentType: "text/plain"},
		{name: "unknown api path is the envelope", path: "/api/v1/no-such", status: http.StatusNotFound, contentType: "application/json", json: true},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			recorder := httptest.NewRecorder()
			handler.ServeHTTP(recorder, httptest.NewRequest(http.MethodGet, tc.path, nil))
			response := recorder.Result()
			defer response.Body.Close()
			body, _ := io.ReadAll(response.Body)
			if response.StatusCode != tc.status {
				t.Fatalf("%s: status=%d want %d (body=%q)", tc.path, response.StatusCode, tc.status, body)
			}
			if got := response.Header.Get("Content-Type"); !strings.HasPrefix(got, tc.contentType) {
				t.Fatalf("%s: content-type=%q want prefix %q", tc.path, got, tc.contentType)
			}
			if tc.json {
				var payload struct {
					Error struct {
						Code      string `json:"code"`
						Message   string `json:"message"`
						RequestID string `json:"request_id"`
					} `json:"error"`
				}
				if err := json.Unmarshal(body, &payload); err != nil {
					t.Fatalf("%s: body is not the envelope: %v (%q)", tc.path, err, body)
				}
				if payload.Error.Code != "not_found" || payload.Error.RequestID == "" {
					t.Fatalf("%s: unexpected envelope %+v", tc.path, payload.Error)
				}
			}
			if tc.path == "/no-such-page" && strings.Contains(string(body), `"error"`) {
				t.Fatalf("page 404 was rewritten into the API envelope: %q", body)
			}
		})
	}
}

// A page method mismatch must also keep the mux's ordinary 405 response.
func TestContractErrorsForDoesNotRewritePageMethodMismatch(t *testing.T) {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /login", func(w http.ResponseWriter, r *http.Request) {})
	handler := ContractErrorsFor([]string{"/api/"}, mux)
	recorder := httptest.NewRecorder()
	handler.ServeHTTP(recorder, httptest.NewRequest(http.MethodPost, "/login", nil))
	response := recorder.Result()
	defer response.Body.Close()
	if response.StatusCode != http.StatusMethodNotAllowed {
		t.Fatalf("status=%d want 405", response.StatusCode)
	}
	if got := response.Header.Get("Content-Type"); strings.HasPrefix(got, "application/json") {
		t.Fatalf("page 405 was rewritten into the API envelope (content-type=%q)", got)
	}
}
