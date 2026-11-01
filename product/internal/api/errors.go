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
