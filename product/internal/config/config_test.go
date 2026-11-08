package config

import (
	"errors"
	"path/filepath"
	"runtime"
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

func TestEvaluationPoolDefaultsAndBounds(t *testing.T) {
	cfg, err := LoadMaintenance(lookupFrom(validMaintenanceEnv()))
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	// BACKGROUND-JOBS §3: artifact_finalize has its own system_io pool (2) and
	// everything else shares the work pool (1).
	if cfg.SystemIOSlots != 2 {
		t.Fatalf("system_io slots = %d", cfg.SystemIOSlots)
	}
	if cfg.WorkSlots != 1 {
		t.Fatalf("work slots = %d", cfg.WorkSlots)
	}
	if cfg.DatasetWaitTimeout != DefaultDatasetWaitTimeout {
		t.Fatalf("dataset wait = %s", cfg.DatasetWaitTimeout)
	}
	if cfg.EvaluationPollInterval != DefaultEvaluationPollInterval {
		t.Fatalf("evaluation poll = %s", cfg.EvaluationPollInterval)
	}
	if cfg.ReportWaitTimeout != DefaultReportWaitTimeout {
		t.Fatalf("report wait = %s", cfg.ReportWaitTimeout)
	}

	env := validMaintenanceEnv()
	env[EnvSystemIOSlots] = "4"
	env[EnvWorkSlots] = "3"
	env[EnvDatasetWaitTimeout] = "2s"
	env[EnvEvaluationPollInterval] = "25ms"
	env[EnvReportWaitTimeout] = "3s"
	cfg, err = LoadMaintenance(lookupFrom(env))
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if cfg.SystemIOSlots != 4 || cfg.WorkSlots != 3 {
		t.Fatalf("slot overrides ignored: %d/%d", cfg.SystemIOSlots, cfg.WorkSlots)
	}
	if cfg.DatasetWaitTimeout != 2*time.Second || cfg.ReportWaitTimeout != 3*time.Second {
		t.Fatalf("wait overrides ignored: %s/%s", cfg.DatasetWaitTimeout, cfg.ReportWaitTimeout)
	}
	if cfg.EvaluationPollInterval != 25*time.Millisecond {
		t.Fatalf("poll override ignored: %s", cfg.EvaluationPollInterval)
	}

	for key, raw := range map[string]string{
		EnvSystemIOSlots:          "0",
		EnvWorkSlots:              "99",
		EnvDatasetWaitTimeout:     "0s",
		EnvEvaluationPollInterval: "1ms",
		EnvReportWaitTimeout:      "48h",
	} {
		env := validMaintenanceEnv()
		env[key] = raw
		if _, err := LoadMaintenance(lookupFrom(env)); err == nil {
			t.Fatalf("%s=%q must be rejected", key, raw)
		}
	}
}

func TestPresetToolConfigurationDefaultsAndBounds(t *testing.T) {
	cfg, err := LoadMaintenance(lookupFrom(validMaintenanceEnv()))
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	wantPython := "python3"
	if runtime.GOOS == "windows" {
		wantPython = "python"
	}
	if cfg.PythonPath != wantPython {
		t.Fatalf("python path = %q, want %q", cfg.PythonPath, wantPython)
	}
	if cfg.ToolTimeout != DefaultToolRunTimeout {
		t.Fatalf("tool timeout = %s", cfg.ToolTimeout)
	}
	if cfg.ProductRoot == "" || !filepath.IsAbs(cfg.ProductRoot) {
		t.Fatalf("product root must default to an absolute path, got %q", cfg.ProductRoot)
	}

	env := validMaintenanceEnv()
	env[EnvPython] = "python3.12"
	env[EnvProductRoot] = `C:\orchara`
	env[EnvToolTimeout] = "2s"
	cfg, err = LoadMaintenance(lookupFrom(env))
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if cfg.PythonPath != "python3.12" || cfg.ToolTimeout != 2*time.Second ||
		!filepath.IsAbs(cfg.ProductRoot) {
		t.Fatalf("preset tool overrides ignored: %+v", cfg)
	}

	for _, raw := range []string{"0s", "11m", "soon", "-1s"} {
		env := validMaintenanceEnv()
		env[EnvToolTimeout] = raw
		if _, err := LoadMaintenance(lookupFrom(env)); err == nil {
			t.Fatalf("tool timeout %q must be rejected", raw)
		}
	}
}

func TestSweepConfigurationNeedsOnlyTheStoreAndDatabase(t *testing.T) {
	env := map[string]string{
		EnvObjectDir:      `C:\objects`,
		EnvMaintenanceDSN: "postgresql://orchara_owner:pw@127.0.0.1:55432/db",
	}
	cfg, err := LoadSweep(lookupFrom(env))
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if cfg.ObjectDir != `C:\objects` || cfg.DBDSN == "" {
		t.Fatalf("sweep config = %+v", cfg)
	}
	for _, missing := range []string{EnvObjectDir, EnvMaintenanceDSN} {
		env := map[string]string{
			EnvObjectDir:      `C:\objects`,
			EnvMaintenanceDSN: "postgresql://orchara_owner:pw@127.0.0.1:55432/db",
		}
		delete(env, missing)
		if _, err := LoadSweep(lookupFrom(env)); err == nil {
			t.Fatalf("missing %s must be rejected", missing)
		}
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

func TestRunEventStreamDefaultsAndBounds(t *testing.T) {
	env := validStubEnv()
	cfg, err := Load(lookupFrom(env))
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if cfg.RunEventRetention != 24*time.Hour {
		t.Fatalf("retention default = %s", cfg.RunEventRetention)
	}
	if cfg.RunEventPollInterval != 500*time.Millisecond {
		t.Fatalf("poll default = %s", cfg.RunEventPollInterval)
	}
	if cfg.RunEventHeartbeatInterval != 15*time.Second {
		t.Fatalf("heartbeat default = %s", cfg.RunEventHeartbeatInterval)
	}

	env[EnvRunEventRetention] = "50ms"
	env[EnvRunEventPollInterval] = "20ms"
	env[EnvRunEventHeartbeatInterval] = "100ms"
	cfg, err = Load(lookupFrom(env))
	if err != nil {
		t.Fatalf("small deployment knobs must be accepted: %v", err)
	}
	if cfg.RunEventRetention != 50*time.Millisecond ||
		cfg.RunEventPollInterval != 20*time.Millisecond ||
		cfg.RunEventHeartbeatInterval != 100*time.Millisecond {
		t.Fatalf("knobs were not loaded: %+v", cfg)
	}

	for _, bad := range []struct {
		name  string
		key   string
		value string
	}{
		{"zero retention", EnvRunEventRetention, "0s"},
		{"bad poll", EnvRunEventPollInterval, "soon"},
		{"negative heartbeat", EnvRunEventHeartbeatInterval, "-1s"},
		{"absurd retention", EnvRunEventRetention, "10000h"},
	} {
		t.Run(bad.name, func(t *testing.T) {
			invalidEnv := validStubEnv()
			invalidEnv[bad.key] = bad.value
			if _, err := Load(lookupFrom(invalidEnv)); err == nil {
				t.Fatalf("%s must be rejected", bad.name)
			}
		})
	}
}

// F09/T054: the memory candidate lifetime is a validated deployment knob.
func TestMemoryCandidateTTLDefaultsToSevenDays(t *testing.T) {
	cfg, err := Load(lookupFrom(validStubEnv()))
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if cfg.MemoryCandidateTTL != DefaultMemoryCandidateTTL {
		t.Fatalf("ttl = %s, want %s", cfg.MemoryCandidateTTL, DefaultMemoryCandidateTTL)
	}
	if DefaultMemoryCandidateTTL != 7*24*time.Hour {
		t.Fatalf("DefaultMemoryCandidateTTL = %s", DefaultMemoryCandidateTTL)
	}
}

func TestMemoryCandidateTTLAcceptsASaneOverride(t *testing.T) {
	env := validStubEnv()
	env[EnvMemoryCandidateTTL] = "90m"
	cfg, err := Load(lookupFrom(env))
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if cfg.MemoryCandidateTTL != 90*time.Minute {
		t.Fatalf("ttl = %s", cfg.MemoryCandidateTTL)
	}
}

func TestMemoryCandidateTTLRejectsAStillbornOrAbsurdWindow(t *testing.T) {
	for _, value := range []string{"0s", "-1h", "1s", "8761h", "not-a-duration"} {
		env := validStubEnv()
		env[EnvMemoryCandidateTTL] = value
		_, err := Load(lookupFrom(env))
		var invalidErr *ValidationError
		if !errors.As(err, &invalidErr) || invalidErr.Variable != EnvMemoryCandidateTTL {
			t.Fatalf("value %q: expected a validated %s error, got %v", value, EnvMemoryCandidateTTL, err)
		}
	}
}

func TestApprovalSweepIntervalDefaultsAndBounds(t *testing.T) {
	cfg, err := Load(lookupFrom(validStubEnv()))
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if cfg.ApprovalSweepInterval != DefaultApprovalSweepInterval {
		t.Fatalf("sweep interval = %s, want %s",
			cfg.ApprovalSweepInterval, DefaultApprovalSweepInterval)
	}
	for _, value := range []string{"0s", "-1s", "1ms", "6m", "soon"} {
		env := validStubEnv()
		env[EnvApprovalSweepInterval] = value
		if _, err := Load(lookupFrom(env)); err == nil {
			t.Fatalf("sweep interval %q must be rejected", value)
		}
	}
	env := validStubEnv()
	env[EnvApprovalSweepInterval] = "100ms"
	cfg, err = Load(lookupFrom(env))
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if cfg.ApprovalSweepInterval != 100*time.Millisecond {
		t.Fatalf("sweep interval = %s", cfg.ApprovalSweepInterval)
	}
}

// F06/T122: an explicitly configured real-model destination is read out under a
// stub profile too, so a deployment can exercise the compiled provider adapter
// against a controlled endpoint (a local fake provider in tests) while stub
// candidates keep behaving exactly as before.
func TestStubProfilesReadAnExplicitModelDestination(t *testing.T) {
	for _, profile := range []string{"dev-stub", "capacity-stub"} {
		env := validStubEnv()
		env[EnvProfile] = profile
		cfg, err := Load(lookupFrom(env))
		if err != nil {
			t.Fatalf("%s without a destination: %v", profile, err)
		}
		if cfg.ModelEndpoint != "" || cfg.ModelSecret != "" || !cfg.Stub() {
			t.Fatalf("%s defaults = %q/%q stub=%v",
				profile, cfg.ModelEndpoint, cfg.ModelSecret, cfg.Stub())
		}

		env[EnvModelEndpoint] = "http://127.0.0.1:18080/v1"
		env[EnvModelSecretRef] = "env:ORCHARA_MODEL_KEY"
		cfg, err = Load(lookupFrom(env))
		if err != nil {
			t.Fatalf("%s with a destination: %v", profile, err)
		}
		if cfg.ModelEndpoint != "http://127.0.0.1:18080/v1" {
			t.Fatalf("%s endpoint = %q", profile, cfg.ModelEndpoint)
		}
		if cfg.ModelSecret != "env:ORCHARA_MODEL_KEY" {
			t.Fatalf("%s secret ref = %q", profile, cfg.ModelSecret)
		}
		if !cfg.Stub() {
			t.Fatalf("%s must stay a stub profile even with a declared destination", profile)
		}
	}
}

func TestStubProfilesRefuseAHalfConfiguredDestination(t *testing.T) {
	env := validStubEnv()
	env[EnvModelEndpoint] = "http://127.0.0.1:18080/v1"
	_, err := Load(lookupFrom(env))
	var invalidErr *ValidationError
	if !errors.As(err, &invalidErr) || invalidErr.Variable != EnvModelSecretRef {
		t.Fatalf("an endpoint without its secret reference must be refused, got %v", err)
	}

	env = validStubEnv()
	env[EnvModelSecretRef] = "env:ORCHARA_MODEL_KEY"
	_, err = Load(lookupFrom(env))
	if !errors.As(err, &invalidErr) || invalidErr.Variable != EnvModelEndpoint {
		t.Fatalf("a secret reference without an endpoint must be refused, got %v", err)
	}

	env = validStubEnv()
	env[EnvModelEndpoint] = "http://127.0.0.1:18080/v1?key=value"
	env[EnvModelSecretRef] = "env:ORCHARA_MODEL_KEY"
	_, err = Load(lookupFrom(env))
	if !errors.As(err, &invalidErr) || invalidErr.Variable != EnvModelEndpoint {
		t.Fatalf("an endpoint with a query must be refused, got %v", err)
	}
}

// ---------------------------------------------------------------------------
// F13B/T114: the capacity-stub stand-in service time
// ---------------------------------------------------------------------------

func TestCapacityStubServiceDelayIsOnlyAcceptedForTheCapacityStubProfile(t *testing.T) {
	env := validStubEnv()
	env[EnvProfile] = string(ProfileCapacityStub)
	env[EnvCapacityStubServiceDelay] = "500ms"
	cfg, err := Load(lookupFrom(env))
	if err != nil {
		t.Fatalf("capacity-stub must accept the stand-in service time: %v", err)
	}
	if cfg.CapacityStubServiceDelay != 500*time.Millisecond {
		t.Fatalf("CapacityStubServiceDelay = %s", cfg.CapacityStubServiceDelay)
	}

	// The same deployment knob is a startup error in any other profile: the
	// stand-in time must never silently reach dev-stub or a real model.
	env[EnvProfile] = string(ProfileDevStub)
	if _, err := Load(lookupFrom(env)); err == nil {
		t.Fatal("dev-stub must reject the capacity stand-in knob")
	} else if !strings.Contains(err.Error(), EnvCapacityStubServiceDelay) {
		t.Fatalf("the error must name the variable: %v", err)
	}

	delete(env, EnvCapacityStubServiceDelay)
	cfg, err = Load(lookupFrom(env))
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if cfg.CapacityStubServiceDelay != 0 {
		t.Fatalf("unset must mean zero, got %s", cfg.CapacityStubServiceDelay)
	}

	env[EnvCapacityStubServiceDelay] = "1h"
	env[EnvProfile] = string(ProfileCapacityStub)
	if _, err := Load(lookupFrom(env)); err == nil {
		t.Fatal("an absurd stand-in service time must be rejected")
	}
}
