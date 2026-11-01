package config

import (
	"errors"
	"strings"
	"testing"
)

func lookupFrom(pairs map[string]string) Lookup {
	return func(key string) (string, bool) {
		value, ok := pairs[key]
		return value, ok
	}
}

func validStubEnv() map[string]string {
	return map[string]string{
		EnvProfile:  "dev-stub",
		EnvDBDSN:    "postgresql://user:pw@127.0.0.1:5432/db",
		EnvHTTPAddr: "127.0.0.1:9999",
	}
}

func TestLoadAcceptsADevStubProfile(t *testing.T) {
	cfg, err := Load(lookupFrom(validStubEnv()))
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if cfg.Profile != ProfileDevStub {
		t.Fatalf("profile = %q", cfg.Profile)
	}
	if !cfg.Stub() {
		t.Fatal("dev-stub must be marked as producing stub results")
	}
	if cfg.StubLedger == "" {
		t.Fatal("a stub profile must declare where its results are recorded")
	}
}

func TestMissingProfileRefusesStartup(t *testing.T) {
	env := validStubEnv()
	delete(env, EnvProfile)
	_, err := Load(lookupFrom(env))
	var invalidErr *ValidationError
	if !errors.As(err, &invalidErr) {
		t.Fatalf("expected ValidationError, got %v", err)
	}
	if invalidErr.Variable != EnvProfile {
		t.Fatalf("blamed %q", invalidErr.Variable)
	}
}

func TestUnknownProfileIsRejected(t *testing.T) {
	env := validStubEnv()
	env[EnvProfile] = "staging"
	if _, err := Load(lookupFrom(env)); err == nil {
		t.Fatal("unknown profile must be rejected")
	}
}

func TestMissingDatabaseRefusesStartup(t *testing.T) {
	env := validStubEnv()
	delete(env, EnvDBDSN)
	_, err := Load(lookupFrom(env))
	var invalidErr *ValidationError
	if !errors.As(err, &invalidErr) || invalidErr.Variable != EnvDBDSN {
		t.Fatalf("expected a missing-DSN error, got %v", err)
	}
}

func TestArbitraryScriptsAreOnlyAllowedWithTheSandboxProfile(t *testing.T) {
	env := validStubEnv()
	env[EnvAllowScripts] = "true"
	if _, err := Load(lookupFrom(env)); err == nil {
		t.Fatal("stub profile must refuse to enable arbitrary scripts")
	} else if !strings.Contains(err.Error(), EnvAllowScripts) {
		t.Fatalf("error should name the variable: %v", err)
	}

	env[EnvProfile] = string(ProfileLinuxSandbox)
	if _, err := Load(lookupFrom(env)); err != nil {
		t.Fatalf("linux-sandbox should accept the flag: %v", err)
	}
}

func TestRealModelRequiresEndpointAndSecretReference(t *testing.T) {
	env := map[string]string{
		EnvProfile: "real-model",
		EnvDBDSN:   "postgresql://user:pw@127.0.0.1:5432/db",
	}
	if _, err := Load(lookupFrom(env)); err == nil {
		t.Fatal("real-model without an endpoint must be rejected")
	}

	env[EnvModelEndpoint] = "https://models.internal.example/v1"
	if _, err := Load(lookupFrom(env)); err == nil {
		t.Fatal("real-model without a secret reference must be rejected")
	}

	env[EnvModelSecretRef] = "env:ORCHARA_MODEL_KEY"
	cfg, err := Load(lookupFrom(env))
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if cfg.ModelSecret != "env:ORCHARA_MODEL_KEY" {
		t.Fatalf("secret ref = %q", cfg.ModelSecret)
	}
	if cfg.Stub() {
		t.Fatal("real-model must not be marked as stub")
	}
}

func TestSecretReferenceRejectsURLsAndPaths(t *testing.T) {
	rejected := []string{
		"https://example.com/key",
		"file:///etc/passwd",
		"/etc/passwd",
		`C:\secrets\key.txt`,
		"../secrets/key",
		"env:lowercase",
		"plainvalue123",
	}
	for _, raw := range rejected {
		if _, err := ParseSecretRef(raw); err == nil {
			t.Fatalf("secret ref %q must be rejected", raw)
		}
	}

	accepted := []string{"env:ORCHARA_MODEL_KEY", "file:model_key.txt"}
	for _, raw := range accepted {
		if _, err := ParseSecretRef(raw); err != nil {
			t.Fatalf("secret ref %q should be accepted: %v", raw, err)
		}
	}
}

func TestSecretReferenceIsNotInlinedIntoConfig(t *testing.T) {
	// The config keeps the reference; the value is resolved later by the capable
	// process, so it never travels through ordinary configuration.
	env := map[string]string{
		EnvProfile:        "real-model",
		EnvDBDSN:          "postgresql://user:pw@127.0.0.1:5432/db",
		EnvModelEndpoint:  "https://models.internal.example/v1",
		EnvModelSecretRef: "env:ORCHARA_MODEL_KEY",
	}
	cfg, err := Load(lookupFrom(env))
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	rendered := cfg.ModelEndpoint + string(cfg.ModelSecret)
	if strings.Contains(rendered, "sk-") || strings.Contains(rendered, "password") {
		t.Fatalf("config must not carry a credential value: %q", rendered)
	}
}

func TestBadBooleanIsRejected(t *testing.T) {
	env := validStubEnv()
	env[EnvAllowScripts] = "yes"
	if _, err := Load(lookupFrom(env)); err == nil {
		t.Fatal("a non-boolean flag value must be rejected")
	}
}
