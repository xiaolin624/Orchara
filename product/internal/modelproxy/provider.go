package modelproxy

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"

	"orchara/platform/internal/config"
	"orchara/platform/internal/jcs"
)

// Provider is the seam between the ledger and one model backend. It receives
// only what the server resolved for this job: the candidate row, its params and
// the fixture bytes. It never receives a worker-supplied endpoint, URL or
// credential.
type Provider interface {
	// Name is a stable label used in error messages and reports. It is never a
	// secret or a URL.
	Name() string
	// Available reports whether this provider can dispatch at all right now. It
	// is checked before a ledger attempt is reserved, so an unconfigured
	// provider leaves no row that could later be read as a charge.
	Available(candidate *Candidate) error
	// Call performs one physical attempt.
	Call(ctx context.Context, request CallRequest) (ProviderResponse, error)
}

// CallRequest is one dispatched attempt.
type CallRequest struct {
	Candidate     *Candidate
	Params        map[string]any
	Fixture       []byte
	FixtureSHA256 string
	Attempt       int
}

// ProviderResponse is a provider's answer. Output is the canonical bytes that
// are sealed as the call's result artifact; Data is the sanitized projection
// returned to the handler (it must never carry a credential).
type ProviderResponse struct {
	Data         map[string]any
	Output       []byte
	TokensIn     int64
	TokensOut    int64
	ChargedUnits int64 // 1e-8 currency units
}

// Providers chooses the provider for a candidate.
//
// The stub is only usable under a stub profile AND for a candidate explicitly
// registered with provider "stub"; a real candidate under a stub profile still
// takes the real path and therefore fails closed. That is what keeps stub output
// from being mistaken for a real model.
type Providers struct {
	profile   config.Profile
	endpoint  string
	secretDir string
	lookup    config.Lookup
	stub      *stubProvider
	real      *realProvider
}

// NewProviders builds the provider set. lookup resolves `env:NAME` references;
// nil means the process environment.
func NewProviders(
	profile config.Profile,
	endpoint, secretDir string,
	lookup config.Lookup,
) *Providers {
	if lookup == nil {
		lookup = config.FromEnv()
	}
	return &Providers{
		profile:   profile,
		endpoint:  strings.TrimSpace(endpoint),
		secretDir: secretDir,
		lookup:    lookup,
		stub:      &stubProvider{},
		real:      &realProvider{endpoint: strings.TrimSpace(endpoint), secretDir: secretDir, lookup: lookup},
	}
}

// For returns the provider a candidate is allowed to use.
func (p *Providers) For(candidate *Candidate) (Provider, error) {
	if candidate == nil {
		return nil, ErrCandidateUnavailable
	}
	switch strings.ToLower(strings.TrimSpace(candidate.Provider)) {
	case "stub":
		if p.profile != config.ProfileDevStub && p.profile != config.ProfileCapacityStub {
			// A stub profile is the explicit registration of a fake model; a
			// real-model environment must never route a candidate here.
			return nil, ErrNotConfigured
		}
		return p.stub, nil
	default:
		return p.real, nil
	}
}

// stubProvider is the deterministic development provider.
//
// Its output is derived from the actual fixture bytes it was handed: the
// reported fixture SHA-256 and byte count change when the file changes, and the
// compatibility verdict is driven by an explicit case marker in the fixture. It
// always labels itself `provider: "stub"`, so a caller can never mistake the
// answer for a real model's.
type stubProvider struct{}

func (s *stubProvider) Name() string { return "stub" }

// Available: the stub is always dispatchable once the profile gate in For has
// accepted it.
func (s *stubProvider) Available(candidate *Candidate) error {
	if candidate == nil {
		return ErrCandidateUnavailable
	}
	return nil
}

func (s *stubProvider) Call(_ context.Context, request CallRequest) (ProviderResponse, error) {
	if request.Candidate == nil {
		return ProviderResponse{}, ErrCandidateUnavailable
	}
	fixture := request.Fixture
	if request.FixtureSHA256 != "" && request.FixtureSHA256 != fixtureSHA256Hex(fixture) {
		return ProviderResponse{}, fmt.Errorf("%w: fixture hash does not match the bytes", ErrNotConfigured)
	}

	checkName := "structured_output"
	passed := true
	reason := ""
	var document map[string]any
	if err := json.Unmarshal(fixture, &document); err != nil {
		passed = false
		reason = "fixture_invalid_json"
	} else {
		if caseName, ok := document["case"].(string); ok {
			switch caseName {
			case "structured_output_invalid":
				checkName, passed, reason = "structured_output", false, "structured_output"
			case "tool_call_invalid":
				checkName, passed, reason = "tool_call", false, "tool_call"
			}
		}
		if requested, ok := document["check"].(string); ok && requested != "" && requested != "structured_output" && requested != "tool_call" {
			// Unknown requested check: report it honestly instead of silently
			// passing a check the fixture asked for.
			checkName, passed, reason = requested, false, "unsupported_check"
		}
	}
	fixtureHash := fixtureSHA256Hex(fixture)
	data := map[string]any{
		"provider":       "stub",
		"stub":           true,
		"model_id":       request.Candidate.ModelID,
		"fixture_sha256": fixtureHash,
		"fixture_bytes":  len(fixture),
		"check": map[string]any{
			"name":   checkName,
			"passed": passed,
			"reason": reason,
		},
	}
	output, err := canonicalJSON(data)
	if err != nil {
		return ProviderResponse{}, fmt.Errorf("canonicalising the stub answer: %w", err)
	}
	tokensIn := int64((len(fixture) + 3) / 4)
	tokensOut := int64((len(output) + 3) / 4)
	return ProviderResponse{
		Data:         data,
		Output:       output,
		TokensIn:     tokensIn,
		TokensOut:    tokensOut,
		ChargedUnits: (tokensIn + tokensOut) * reservedUnitsPerInputToken,
	}, nil
}

// canonicalJSON renders a server-built value as RFC 8785 bytes. It marshals
// first because jcs.Canonicalize only accepts already-JSON-shaped values
// (float64, not int), and the bytes must be exactly what is hashed and sealed.
func canonicalJSON(value any) ([]byte, error) {
	raw, err := json.Marshal(value)
	if err != nil {
		return nil, err
	}
	return jcs.CanonicalizeBytes(raw)
}
