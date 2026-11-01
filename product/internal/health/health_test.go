package health

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func TestLiveAlwaysReportsAlive(t *testing.T) {
	reporter := New(map[string]Check{"database": nil}, time.Second)
	recorder := httptest.NewRecorder()
	reporter.Live(recorder, httptest.NewRequest(http.MethodGet, "/healthz", nil))

	if recorder.Code != http.StatusOK {
		t.Fatalf("liveness must not depend on dependencies, got %d", recorder.Code)
	}
	var payload map[string]any
	if err := json.Unmarshal(recorder.Body.Bytes(), &payload); err != nil {
		t.Fatalf("invalid JSON: %v", err)
	}
	if payload["status"] != "alive" {
		t.Fatalf("expected alive, got %v", payload["status"])
	}
}

func TestReadyFailsWhenDependencyIsDown(t *testing.T) {
	reporter := New(map[string]Check{
		"database": func(context.Context) error { return errors.New("connection refused") },
	}, time.Second)
	recorder := httptest.NewRecorder()
	reporter.Ready(recorder, httptest.NewRequest(http.MethodGet, "/readyz", nil))

	if recorder.Code != http.StatusServiceUnavailable {
		t.Fatalf("expected 503 when the database is down, got %d", recorder.Code)
	}
	if !strings.Contains(recorder.Body.String(), "not_ready") {
		t.Fatalf("expected not_ready status, got %s", recorder.Body.String())
	}
}

func TestReadySucceedsWhenDependencyRecovers(t *testing.T) {
	var down = true
	reporter := New(map[string]Check{
		"database": func(context.Context) error {
			if down {
				return errors.New("connection refused")
			}
			return nil
		},
	}, time.Second)

	failing := httptest.NewRecorder()
	reporter.Ready(failing, httptest.NewRequest(http.MethodGet, "/readyz", nil))
	if failing.Code != http.StatusServiceUnavailable {
		t.Fatalf("expected 503 while down, got %d", failing.Code)
	}

	down = false
	recovered := httptest.NewRecorder()
	reporter.Ready(recovered, httptest.NewRequest(http.MethodGet, "/readyz", nil))
	if recovered.Code != http.StatusOK {
		t.Fatalf("expected 200 after recovery, got %d", recovered.Code)
	}
}

func TestNilCheckIsNeverReady(t *testing.T) {
	// A dependency that was not configured must fail closed.
	reporter := New(map[string]Check{"database": nil}, time.Second)
	recorder := httptest.NewRecorder()
	reporter.Ready(recorder, httptest.NewRequest(http.MethodGet, "/readyz", nil))

	if recorder.Code != http.StatusServiceUnavailable {
		t.Fatalf("unconfigured dependency must not report ready, got %d", recorder.Code)
	}
	if !strings.Contains(recorder.Body.String(), "not_configured") {
		t.Fatalf("expected not_configured detail, got %s", recorder.Body.String())
	}
}

func TestErrorsDoNotLeakCredentials(t *testing.T) {
	secret := "postgresql://user:sup3rs3cret@127.0.0.1:55432/db"
	reporter := New(map[string]Check{
		"database": func(context.Context) error { return errors.New("failed to connect: " + secret) },
	}, time.Second)
	recorder := httptest.NewRecorder()
	reporter.Ready(recorder, httptest.NewRequest(http.MethodGet, "/readyz", nil))

	body := recorder.Body.String()
	for _, leaked := range []string{"sup3rs3cret", "postgresql://", "user:"} {
		if strings.Contains(body, leaked) {
			t.Fatalf("response leaked %q: %s", leaked, body)
		}
	}
}

func TestCheckTimeoutIsApplied(t *testing.T) {
	reporter := New(map[string]Check{
		"database": func(ctx context.Context) error {
			select {
			case <-ctx.Done():
				return ctx.Err()
			case <-time.After(5 * time.Second):
				return nil
			}
		},
	}, 50*time.Millisecond)

	start := time.Now()
	recorder := httptest.NewRecorder()
	reporter.Ready(recorder, httptest.NewRequest(http.MethodGet, "/readyz", nil))
	if elapsed := time.Since(start); elapsed > 2*time.Second {
		t.Fatalf("check was not bounded by the timeout: %s", elapsed)
	}
	if recorder.Code != http.StatusServiceUnavailable {
		t.Fatalf("expected 503 on timeout, got %d", recorder.Code)
	}
}
