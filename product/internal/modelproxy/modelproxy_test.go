package modelproxy

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"orchara/platform/internal/config"
)

func testCandidate(provider string) *Candidate {
	return &Candidate{
		ID:              "11111111-2222-4333-8444-555555555555",
		Provider:        provider,
		ModelID:         "test-model-1",
		EndpointRef:     "env:ORCHARA_MODEL_ENDPOINT",
		SecretRef:       "env:ORCHARA_MODEL_SECRET",
		Config:          json.RawMessage(`{}`),
		ValidationState: "pending",
	}
}

func testFixture(body string) []byte { return []byte(body) }

func stubProviderFor(t *testing.T, profile config.Profile) *Providers {
	t.Helper()
	return NewProviders(profile, "", "", func(string) (string, bool) { return "", false })
}

func TestTheStubIsLabelledDeterministicAndUsageBearing(t *testing.T) {
	providers := stubProviderFor(t, config.ProfileDevStub)
	candidate := testCandidate("stub")
	provider, err := providers.For(candidate)
	if err != nil {
		t.Fatalf("For: %v", err)
	}
	fixture := testFixture(`{"case":"ok","probe":"structured_output"}`)
	first, err := provider.Call(context.Background(), CallRequest{
		Candidate: candidate, Fixture: fixture, FixtureSHA256: fixtureSHA256Hex(fixture),
	})
	if err != nil {
		t.Fatalf("Call: %v", err)
	}
	second, err := provider.Call(context.Background(), CallRequest{
		Candidate: candidate, Fixture: fixture, FixtureSHA256: fixtureSHA256Hex(fixture),
	})
	if err != nil {
		t.Fatalf("Call: %v", err)
	}
	if !bytes.Equal(first.Output, second.Output) || first.TokensIn != second.TokensIn {
		t.Fatal("the stub must be deterministic for identical fixture bytes")
	}
	if first.Data["provider"] != "stub" || first.Data["stub"] != true {
		t.Fatalf("stub output is not labelled: %#v", first.Data)
	}
	if first.Data["fixture_sha256"] != fixtureSHA256Hex(fixture) {
		t.Fatalf("stub did not report the real fixture hash: %#v", first.Data)
	}
	if first.Data["fixture_bytes"] != len(fixture) {
		t.Fatalf("stub did not report the real fixture size: %#v", first.Data)
	}
	if first.TokensIn <= 0 || first.TokensOut <= 0 || first.ChargedUnits <= 0 {
		t.Fatalf("stub must report real usage: %#v", first)
	}
	check, ok := first.Data["check"].(map[string]any)
	if !ok || check["passed"] != true {
		t.Fatalf("expected a passing structured-output check: %#v", first.Data)
	}
}

func TestADifferentFixtureChangesTheStubAnswer(t *testing.T) {
	providers := stubProviderFor(t, config.ProfileDevStub)
	candidate := testCandidate("stub")
	provider, _ := providers.For(candidate)
	first, err := provider.Call(context.Background(), CallRequest{
		Candidate: candidate, Fixture: testFixture(`{"case":"ok"}`),
		FixtureSHA256: fixtureSHA256Hex(testFixture(`{"case":"ok"}`)),
	})
	if err != nil {
		t.Fatalf("Call: %v", err)
	}
	other := testFixture(`{"case":"ok","extra":"different bytes"}`)
	second, err := provider.Call(context.Background(), CallRequest{
		Candidate: candidate, Fixture: other, FixtureSHA256: fixtureSHA256Hex(other),
	})
	if err != nil {
		t.Fatalf("Call: %v", err)
	}
	if first.Data["fixture_sha256"] == second.Data["fixture_sha256"] {
		t.Fatal("the stub answer must depend on the fixture bytes")
	}
	if first.TokensIn == second.TokensIn {
		t.Fatal("stub usage must be derived from the fixture size")
	}
}

func TestTheStubReportsAFailedCompatibilityCaseWithoutFailing(t *testing.T) {
	providers := stubProviderFor(t, config.ProfileDevStub)
	candidate := testCandidate("stub")
	provider, _ := providers.For(candidate)
	fixture := testFixture(`{"case":"structured_output_invalid"}`)
	response, err := provider.Call(context.Background(), CallRequest{
		Candidate: candidate, Fixture: fixture, FixtureSHA256: fixtureSHA256Hex(fixture),
	})
	if err != nil {
		t.Fatalf("Call: %v", err)
	}
	check, _ := response.Data["check"].(map[string]any)
	if check["passed"] != false || check["reason"] != "structured_output" {
		t.Fatalf("expected a failed structured-output verdict: %#v", response.Data)
	}
}

func TestTheStubIsRefusedOutsideStubProfiles(t *testing.T) {
	for _, profile := range []config.Profile{config.ProfileRealModel, config.ProfileLinuxSandbox} {
		providers := stubProviderFor(t, profile)
		if _, err := providers.For(testCandidate("stub")); !errors.Is(err, ErrNotConfigured) {
			t.Fatalf("%s: a stub candidate must be refused, got %v", profile, err)
		}
	}
}

func TestARealCandidateIsNotConfiguredWithoutAnEndpoint(t *testing.T) {
	providers := stubProviderFor(t, config.ProfileDevStub)
	candidate := testCandidate("openai")
	provider, err := providers.For(candidate)
	if err != nil {
		t.Fatalf("For: %v", err)
	}
	if _, err := provider.Call(context.Background(), CallRequest{Candidate: candidate}); !errors.Is(err, ErrNotConfigured) {
		t.Fatalf("expected ErrNotConfigured, got %v", err)
	}
}

func TestAConfiguredEndpointWithoutACompiledAdapterFailsClosedAndHidesTheSecret(t *testing.T) {
	secret := "t024-model-secret-" + strings.Repeat("x", 20)
	lookup := func(key string) (string, bool) {
		if key == "ORCHARA_MODEL_SECRET" {
			return secret, true
		}
		return "", false
	}
	providers := NewProviders(config.ProfileRealModel, "https://model.invalid", "", lookup)
	candidate := testCandidate("openai")
	provider, err := providers.For(candidate)
	if err != nil {
		t.Fatalf("For: %v", err)
	}
	_, callErr := provider.Call(context.Background(), CallRequest{Candidate: candidate})
	if !errors.Is(callErr, ErrAdapterUnavailable) {
		t.Fatalf("expected ErrAdapterUnavailable, got %v", callErr)
	}
	if strings.Contains(callErr.Error(), secret) || strings.Contains(callErr.Error(), "ORCHARA_MODEL_SECRET") {
		t.Fatalf("the provider error leaked credential material: %v", callErr)
	}
}

func TestResolveSecretReadsTheReferenceNeverTheWire(t *testing.T) {
	directory := t.TempDir()
	if err := os.WriteFile(filepath.Join(directory, "model.key"), []byte("file-secret\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	lookup := func(key string) (string, bool) {
		if key == "MODEL_KEY" {
			return "env-secret", true
		}
		return "", false
	}
	if value, err := resolveSecret("env:MODEL_KEY", "", lookup); err != nil || value != "env-secret" {
		t.Fatalf("env ref: %q %v", value, err)
	}
	if value, err := resolveSecret("file:model.key", directory, lookup); err != nil || value != "file-secret" {
		t.Fatalf("file ref: %q %v", value, err)
	}
	if _, err := resolveSecret("file:missing.key", directory, lookup); !errors.Is(err, ErrNotConfigured) {
		t.Fatalf("missing file: %v", err)
	}
	if _, err := resolveSecret("env:MISSING", "", lookup); !errors.Is(err, ErrNotConfigured) {
		t.Fatalf("missing env: %v", err)
	}
	if _, err := resolveSecret("https://user:pass@example.com", "", lookup); !errors.Is(err, ErrNotConfigured) {
		t.Fatalf("a URL must not be accepted as a secret reference: %v", err)
	}
}

func TestFormatUnitsNeverUsesFloats(t *testing.T) {
	cases := map[int64]string{
		0:          "0.00000000",
		1:          "0.00000001",
		100:        "0.00000100",
		123456789:  "1.23456789",
		1000000000: "10.00000000",
	}
	for units, want := range cases {
		if got := formatUnits(units); got != want {
			t.Errorf("formatUnits(%d) = %q, want %q", units, got, want)
		}
	}
	if got := estimateReservedUnits(8); got != 2*reservedUnitsPerInputToken {
		t.Errorf("estimateReservedUnits(8) = %d", got)
	}
}
