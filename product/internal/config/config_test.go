package config

import (
	"errors"
	"strings"
	"testing"
	"time"
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

func TestBackgroundDefaultsAndObjectDirectory(t *testing.T) {
	env := validStubEnv()
	cfg, err := Load(lookupFrom(env))
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if cfg.ObjectDir != "" {
		t.Fatalf("object dir = %q, want the empty (fail-closed) default", cfg.ObjectDir)
	}
	if cfg.RetryBackoff != DefaultBackgroundRetryBackoff {
		t.Fatalf("retry backoff = %s", cfg.RetryBackoff)
	}
	if cfg.Internal != nil {
		t.Fatalf("internal listener = %+v, want disabled", cfg.Internal)
	}

	env[EnvObjectDir] = `C:\objects`
	env[EnvRetryBackoff] = "50ms"
	cfg, err = Load(lookupFrom(env))
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if cfg.ObjectDir != `C:\objects` {
		t.Fatalf("object dir = %q", cfg.ObjectDir)
	}
	if cfg.RetryBackoff != 50*time.Millisecond {
		t.Fatalf("retry backoff = %s", cfg.RetryBackoff)
	}
}

func TestRetryBackoffMustBeAPositiveDuration(t *testing.T) {
	for _, raw := range []string{"soon", "0s", "-1s", "2h"} {
		env := validStubEnv()
		env[EnvRetryBackoff] = raw
		_, err := Load(lookupFrom(env))
		var invalidErr *ValidationError
		if !errors.As(err, &invalidErr) || invalidErr.Variable != EnvRetryBackoff {
			t.Fatalf("%q: expected a retry-backoff validation error, got %v", raw, err)
		}
	}
}

func TestInternalTLSMaterialMustBeComplete(t *testing.T) {
	env := validStubEnv()
	env[EnvInternalAddr] = "127.0.0.1:0"
	if _, err := Load(lookupFrom(env)); err == nil {
		t.Fatal("an internal address without certificates must be rejected")
	}

	env[EnvInternalServerCert] = "server.pem"
	env[EnvInternalServerKey] = "server-key.pem"
	env[EnvInternalClientCA] = "ca.pem"
	cfg, err := Load(lookupFrom(env))
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if cfg.Internal == nil || cfg.Internal.Addr != "127.0.0.1:0" ||
		cfg.Internal.ServerCert != "server.pem" || cfg.Internal.ClientCA != "ca.pem" {
		t.Fatalf("internal TLS config = %+v", cfg.Internal)
	}

	// Material without an address is a half-configured listener, not an
	// invitation to start one somewhere else.
	env = validStubEnv()
	env[EnvInternalServerCert] = "server.pem"
	if _, err := Load(lookupFrom(env)); err == nil {
		t.Fatal("certificate material without an address must be rejected")
	}
}

func validMaintenanceEnv() map[string]string {
	return map[string]string{
		EnvInternalURL:        "https://127.0.0.1:9443",
		EnvInternalClientCert: "client.pem",
		EnvInternalClientKey:  "client-key.pem",
		EnvInternalCA:         "ca.pem",
		EnvObjectDir:          `C:\objects`,
		EnvMaintenanceDSN:     "postgresql://orchara_owner:pw@127.0.0.1:55432/db",
	}
}

func TestMaintenanceConfigurationIsFailClosed(t *testing.T) {
	cfg, err := LoadMaintenance(lookupFrom(validMaintenanceEnv()))
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if cfg.HeartbeatInterval != DefaultHeartbeatInterval {
		t.Fatalf("heartbeat interval = %s", cfg.HeartbeatInterval)
	}
	if cfg.InternalURL != "https://127.0.0.1:9443" {
		t.Fatalf("internal URL = %q", cfg.InternalURL)
	}

	for _, missing := range []string{
		EnvInternalURL, EnvInternalClientCert, EnvInternalClientKey,
		EnvInternalCA, EnvObjectDir, EnvMaintenanceDSN,
	} {
		env := validMaintenanceEnv()
		delete(env, missing)
		_, err := LoadMaintenance(lookupFrom(env))
		var invalidErr *ValidationError
		if !errors.As(err, &invalidErr) || invalidErr.Variable != missing {
			t.Fatalf("missing %s: expected a validation error, got %v", missing, err)
		}
	}
}

func TestMaintenanceURLMustBePlainHTTPS(t *testing.T) {
	for _, raw := range []string{
		"http://127.0.0.1:9443",
		"https://user:secret@127.0.0.1:9443",
		"https://127.0.0.1:9443/prefix",
		"https://127.0.0.1:9443?x=1",
		"not-a-url",
	} {
		env := validMaintenanceEnv()
		env[EnvInternalURL] = raw
		if _, err := LoadMaintenance(lookupFrom(env)); err == nil {
			t.Fatalf("maintenance URL %q must be rejected", raw)
		}
	}
}

func TestHeartbeatIntervalMustBePositive(t *testing.T) {
	env := validMaintenanceEnv()
	env[EnvHeartbeatInterval] = "0s"
	if _, err := LoadMaintenance(lookupFrom(env)); err == nil {
		t.Fatal("a zero heartbeat interval must be rejected")
	}
	env[EnvHeartbeatInterval] = "100ms"
	cfg, err := LoadMaintenance(lookupFrom(env))
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if cfg.HeartbeatInterval != 100*time.Millisecond {
		t.Fatalf("heartbeat interval = %s", cfg.HeartbeatInterval)
	}
}

func TestObjectLinkConfigurationIsValidated(t *testing.T) {
	env := validStubEnv()
	// Empty is legal: the API starts and refuses signed-link traffic. A short
	// key is not, because it would weaken every minted link.
	if cfg, err := Load(lookupFrom(env)); err != nil || cfg.ObjectSigningKey != "" {
		t.Fatalf("an empty signing key must be accepted: %v", err)
	}
	env[EnvObjectSigningKey] = strings.Repeat("k", 16)
	if _, err := Load(lookupFrom(env)); err == nil {
		t.Fatal("a short signing key must be rejected")
	}
	env[EnvObjectSigningKey] = strings.Repeat("k", 32)
	env[EnvObjectLinkTTL] = "2h"
	if _, err := Load(lookupFrom(env)); err == nil {
		t.Fatal("a TTL above the one-hour ceiling must be rejected")
	}
	env[EnvObjectLinkTTL] = "5m"
	cfg, err := Load(lookupFrom(env))
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if cfg.ObjectLinkTTL != 5*time.Minute {
		t.Fatalf("link TTL = %s", cfg.ObjectLinkTTL)
	}
	if cfg.PublicURL != "http://127.0.0.1:9999" {
		t.Fatalf("default public URL = %q", cfg.PublicURL)
	}
	env[EnvPublicURL] = "https://orchara.example/"
	cfg, err = Load(lookupFrom(env))
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if cfg.PublicURL != "https://orchara.example" {
		t.Fatalf("public URL was not normalised: %q", cfg.PublicURL)
	}
	env[EnvPublicURL] = "ftp://orchara.example"
	if _, err := Load(lookupFrom(env)); err == nil {
		t.Fatal("a non-HTTP public URL must be rejected")
	}
}

func TestWorkerImageDigestAndRuntimeLockAreDeploymentInputs(t *testing.T) {
	env := validStubEnv()
	// Absent is legal: manifest endpoints answer 503 compiler_unconfigured. The
	// runtime lock path defaults to the P01 lock.
	cfg, err := Load(lookupFrom(env))
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if cfg.WorkerImageDigest != "" {
		t.Fatalf("worker digest = %q; an absent digest must stay absent", cfg.WorkerImageDigest)
	}
	if cfg.RuntimeLockPath != "probes/agent_min/requirements.lock.txt" {
		t.Fatalf("runtime lock default = %q", cfg.RuntimeLockPath)
	}

	env[EnvWorkerImageDigest] = "sha256:" + strings.Repeat("a", 64)
	cfg, err = Load(lookupFrom(env))
	if err != nil {
		t.Fatalf("a valid digest must be accepted: %v", err)
	}
	if cfg.WorkerImageDigest != env[EnvWorkerImageDigest] {
		t.Fatalf("worker digest = %q", cfg.WorkerImageDigest)
	}

	for _, bad := range []string{"latest", "sha256:xyz", "sha256:" + strings.Repeat("A", 64), "sha512:" + strings.Repeat("a", 64)} {
		env[EnvWorkerImageDigest] = bad
		if _, err := Load(lookupFrom(env)); err == nil {
			t.Fatalf("digest %q must be rejected", bad)
		}
	}

	env[EnvWorkerImageDigest] = "sha256:" + strings.Repeat("a", 64)
	env[EnvRuntimeLockPath] = "custom/runtime.lock.txt"
	cfg, err = Load(lookupFrom(env))
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if cfg.RuntimeLockPath != "custom/runtime.lock.txt" {
		t.Fatalf("runtime lock override = %q", cfg.RuntimeLockPath)
	}
}
