package api

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"io"
	"log"
	"net/http"
	"strings"
	"time"
)

type requestIDKey struct{}

// RequestID returns the id assigned to this request (empty when the middleware
// is not installed, e.g. in a unit test that calls a handler directly).
func RequestID(ctx context.Context) string {
	id, _ := ctx.Value(requestIDKey{}).(string)
	return id
}

// WithRequestLogging assigns the request id and logs one line per request.
//
// The line contains exactly method, path without the query string, status,
// duration and request id. Bodies, cookies, Authorization headers and tokens are
// never logged: a secret must not reach a log file.
func WithRequestLogging(logger *log.Logger, next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		start := time.Now()
		id := sanitizeRequestID(r.Header.Get("X-Request-ID"))
		if id == "" {
			id = newRequestID()
		}
		r = r.WithContext(context.WithValue(r.Context(), requestIDKey{}, id))

		recorder := &statusRecorder{ResponseWriter: w, status: http.StatusOK}
		next.ServeHTTP(recorder, r)

		// Drain whatever the handler did not read *before* the server closes a
		// Connection: close request. A rejected POST (401/403) never reads its
		// body; without this the close carries unread bytes and the client sees
		// a TCP reset instead of the error response it was sent.
		if r.Body != nil {
			_, _ = io.Copy(io.Discard, io.LimitReader(r.Body, maxRequestBody))
		}

		// r.URL.Path never includes the query string; that is the point. Signed
		// object links carry their capability in the path, so the token is
		// redacted before it can reach a log file.
		logger.Printf(
			"%s %s %d %s request_id=%s",
			r.Method,
			loggablePath(r.URL.Path),
			recorder.status,
			time.Since(start).Round(time.Microsecond),
			id,
		)
	})
}

// loggablePath removes capability material from a path before logging. Signed
// object links are bearer transfers: the token is the whole authority, so a log
// that recorded it would hand out a working link.
func loggablePath(path string) string {
	const prefix = "/object-links/"
	if strings.HasPrefix(path, prefix) {
		return prefix + "<redacted>"
	}
	return path
}

// WithRequestID echoes the request id in the response header.
func WithRequestID(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		id := RequestID(r.Context())
		if id == "" {
			id = newRequestID()
			r = r.WithContext(context.WithValue(r.Context(), requestIDKey{}, id))
		}
		w.Header().Set("X-Request-ID", id)
		next.ServeHTTP(w, r)
	})
}

// sanitizeRequestID accepts only short, log-safe ASCII. A client-supplied value
// with a newline would otherwise be able to forge a log line.
func sanitizeRequestID(raw string) string {
	if raw == "" || len(raw) > 128 {
		return ""
	}
	for _, char := range raw {
		switch {
		case char >= 'a' && char <= 'z',
			char >= 'A' && char <= 'Z',
			char >= '0' && char <= '9',
			char == '-', char == '_', char == '.', char == ':':
			continue
		default:
			return ""
		}
	}
	return raw
}

func newRequestID() string {
	buf := make([]byte, 16)
	if _, err := rand.Read(buf); err != nil {
		// Only used for correlation; a degraded fallback beats refusing traffic.
		return "unavailable"
	}
	return hex.EncodeToString(buf)
}

type statusRecorder struct {
	http.ResponseWriter
	status int
	wrote  bool
}

func (r *statusRecorder) WriteHeader(status int) {
	if !r.wrote {
		r.status = status
		r.wrote = true
	}
	r.ResponseWriter.WriteHeader(status)
}

func (r *statusRecorder) Write(body []byte) (int, error) {
	if !r.wrote {
		r.status = http.StatusOK
		r.wrote = true
	}
	return r.ResponseWriter.Write(body)
}
