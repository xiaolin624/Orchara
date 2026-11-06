// Package api implements the platform's HTTP surface.
//
// Two rules are structural rather than per-handler:
//
//   - every error response is the contract envelope
//     {"error":{"code","message","request_id"}};
//   - auth responses are never cacheable and a request log line contains only
//     method, path (without query), status, duration and request id.
package api

import (
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"strings"
)

const maxRequestBody = 1 << 20 // 1 MiB: no handler here accepts uploads

type errorEnvelope struct {
	Error errorBody `json:"error"`
}

type errorBody struct {
	Code      string         `json:"code"`
	Message   string         `json:"message"`
	RequestID string         `json:"request_id"`
	Details   map[string]any `json:"details,omitempty"`
}

// ContractErrors normalises the mux's own 404/405 response into the contract
// error envelope.
//
// net/http answers an unmatched path (404) and a method mismatch (405) with a
// plain-text body. API.md §1 fixes one error shape for every API response, and
// T093 audits that shape from an external client, so this wrapper intercepts
// only those two statuses when the handler has not already produced a JSON
// body. A handler's own writeError response carries
// Content-Type: application/json and is left untouched; the rewritten response
// keeps the status and, for 405, the Allow header the mux set.
func ContractErrors(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		next.ServeHTTP(&contractErrorWriter{ResponseWriter: w, request: r}, r)
	})
}

type contractErrorWriter struct {
	http.ResponseWriter
	request     *http.Request
	wroteHeader bool
	rewritten   bool
}

func (w *contractErrorWriter) WriteHeader(status int) {
	if w.wroteHeader {
		return
	}
	w.wroteHeader = true
	muxOwnError := status == http.StatusNotFound || status == http.StatusMethodNotAllowed
	if !muxOwnError || strings.HasPrefix(w.Header().Get("Content-Type"), "application/json") {
		w.ResponseWriter.WriteHeader(status)
		return
	}
	w.rewritten = true
	w.Header().Del("Content-Length")
	w.Header().Del("X-Content-Type-Options")
	code, message := "not_found", "no route matches this request"
	if status == http.StatusMethodNotAllowed {
		code, message = "method_not_allowed", "this method is not allowed for the path"
	}
	writeError(w.ResponseWriter, w.request, status, code, message)
}

func (w *contractErrorWriter) Write(body []byte) (int, error) {
	if w.rewritten {
		return len(body), nil
	}
	if !w.wroteHeader {
		w.WriteHeader(http.StatusOK)
	}
	return w.ResponseWriter.Write(body)
}

// Flush keeps the SSE handlers working through the wrapper.
func (w *contractErrorWriter) Flush() {
	if !w.wroteHeader {
		w.WriteHeader(http.StatusOK)
	}
	if flusher, ok := w.ResponseWriter.(http.Flusher); ok {
		flusher.Flush()
	}
}

// writeJSON writes an auth-response body. Auth data is always no-store: a
// session response, an invitation token or an error must not sit in a cache.
func writeJSON(w http.ResponseWriter, status int, payload any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(payload)
}

func writeError(w http.ResponseWriter, r *http.Request, status int, code, message string) {
	writeErrorWithDetails(w, r, status, code, message, nil)
}

func writeErrorWithDetails(
	w http.ResponseWriter,
	r *http.Request,
	status int,
	code, message string,
	details map[string]any,
) {
	writeJSON(w, status, errorEnvelope{Error: errorBody{
		Code:      code,
		Message:   message,
		RequestID: RequestID(r.Context()),
		Details:   details,
	}})
}

// decodeJSON enforces the request bodies declared by the contract:
// application/json, no unknown fields, no trailing data and a size cap.
func decodeJSON(w http.ResponseWriter, r *http.Request, target any) error {
	if r.Body == nil {
		return errors.New("request body is missing")
	}
	body := http.MaxBytesReader(w, r.Body, maxRequestBody)
	decoder := json.NewDecoder(body)
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(target); err != nil {
		return err
	}
	if err := decoder.Decode(&struct{}{}); !errors.Is(err, io.EOF) {
		return errors.New("request body contains trailing data")
	}
	return nil
}
