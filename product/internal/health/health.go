// Package health implements the liveness and readiness endpoints for the
// platform API.
//
// The distinction matters: a process can be alive while its dependencies are
// unusable. Reporting readiness as "ok" because the process runs would hide a
// dead database, so readiness always executes the dependency checks and fails
// closed when one is missing.
package health

import (
	"context"
	"encoding/json"
	"net/http"
	"time"
)

// Check reports whether one dependency is usable.
type Check func(ctx context.Context) error

// CheckResult is the per-dependency outcome exposed to operators.
type CheckResult struct {
	Status string `json:"status"`
	Detail string `json:"detail,omitempty"`
}

// Reporter answers liveness and readiness.
type Reporter struct {
	checks    map[string]Check
	timeout   time.Duration
	startedAt time.Time
	version   string
	profile   string
	stub      bool
}

// New builds a Reporter. A nil check is treated as "dependency not configured"
// rather than being skipped, so a missing database never looks ready.
func New(checks map[string]Check, timeout time.Duration) *Reporter {
	if timeout <= 0 {
		timeout = 2 * time.Second
	}
	return &Reporter{checks: checks, timeout: timeout, startedAt: time.Now(), version: "0.0.1-skeleton"}
}

// WithVersion overrides the reported build version.
func (r *Reporter) WithVersion(version string) *Reporter {
	r.version = version
	return r
}

// WithProfile records the environment profile and whether its results are stub
// output, so a status page can never imply real-model behaviour.
func (r *Reporter) WithProfile(profile string, stub bool) *Reporter {
	r.profile = profile
	r.stub = stub
	return r
}

// Evaluate runs every check and returns whether all of them passed.
func (r *Reporter) Evaluate(ctx context.Context) (bool, map[string]CheckResult) {
	results := make(map[string]CheckResult, len(r.checks))
	ready := true
	for name, check := range r.checks {
		if check == nil {
			results[name] = CheckResult{Status: "not_configured", Detail: "dependency is not configured"}
			ready = false
			continue
		}
		checkCtx, cancel := context.WithTimeout(ctx, r.timeout)
		err := check(checkCtx)
		cancel()
		if err != nil {
			// The detail is deliberately coarse: connection errors can contain
			// DSNs and therefore credentials.
			results[name] = CheckResult{Status: "unavailable", Detail: "dependency check failed"}
			ready = false
			continue
		}
		results[name] = CheckResult{Status: "ok"}
	}
	return ready, results
}

type statusPayload struct {
	Status      string                 `json:"status"`
	Service     string                 `json:"service"`
	Version     string                 `json:"version"`
	Profile     string                 `json:"profile,omitempty"`
	StubResults bool                   `json:"stub_results"`
	UptimeSecs  int64                  `json:"uptime_seconds"`
	Checks      map[string]CheckResult `json:"checks,omitempty"`
}

func (r *Reporter) payload(status string, checks map[string]CheckResult) statusPayload {
	return statusPayload{
		Status:      status,
		Service:     "orchara-api",
		Version:     r.version,
		Profile:     r.profile,
		StubResults: r.stub,
		UptimeSecs:  int64(time.Since(r.startedAt).Seconds()),
		Checks:      checks,
	}
}

// Live is the liveness handler: it answers 200 while the process can serve.
func (r *Reporter) Live(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, r.payload("alive", nil))
}

// Ready is the readiness handler: 200 only when every dependency check passes.
func (r *Reporter) Ready(w http.ResponseWriter, req *http.Request) {
	ready, results := r.Evaluate(req.Context())
	status := "ready"
	code := http.StatusOK
	if !ready {
		status = "not_ready"
		code = http.StatusServiceUnavailable
	}
	writeJSON(w, code, r.payload(status, results))
}

// Status is the machine-readable aggregate used by the console page.
func (r *Reporter) Status(w http.ResponseWriter, req *http.Request) {
	ready, results := r.Evaluate(req.Context())
	status := "ready"
	if !ready {
		status = "not_ready"
	}
	writeJSON(w, http.StatusOK, r.payload(status, results))
}

func writeJSON(w http.ResponseWriter, code int, payload any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(code)
	_ = json.NewEncoder(w).Encode(payload)
}
