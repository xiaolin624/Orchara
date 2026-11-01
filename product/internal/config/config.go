// Package config loads and validates the platform's runtime configuration for a
// declared environment profile.
//
// Two rules drive the design:
//
//   - **Missing required configuration refuses startup.** A process that starts
//     without a database or model endpoint and only fails later looks healthy
//     while being useless.
//   - **A secret reference is a reference, never an inline value and never an
//     arbitrary URL or path.** Accepting either would let a business input
//     redirect where credentials are read from.
package config

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"
)

// Profile names the environment contract. The set mirrors BOOTSTRAP.md §5.
type Profile string

const (
	ProfileDevStub       Profile = "dev-stub"
	ProfileLinuxSandbox  Profile = "linux-sandbox"
	ProfileRealModel     Profile = "real-model"
	ProfileCapacityStub  Profile = "capacity-stub"
	profileDefaultEnvVar         = "ORCHARA_PROFILE"
)

var knownProfiles = map[Profile]bool{
	ProfileDevStub:      true,
	ProfileLinuxSandbox: true,
	ProfileRealModel:    true,
	ProfileCapacityStub: true,
}

// Variables the loader reads. Kept as constants so error messages and tests
// cannot drift from the implementation.
const (
	EnvProfile        = profileDefaultEnvVar
	EnvHTTPAddr       = "ORCHARA_HTTP_ADDR"
	EnvDBDSN          = "ORCHARA_DB_DSN"
	EnvModelEndpoint  = "ORCHARA_MODEL_ENDPOINT"
	EnvModelSecretRef = "ORCHARA_MODEL_SECRET_REF"
	EnvStubLedger     = "ORCHARA_STUB_LEDGER"
	EnvAllowScripts   = "ORCHARA_ALLOW_ARBITRARY_SCRIPTS"
	EnvWebDir         = "ORCHARA_WEB_DIR"
)

// SecretRef is a validated reference to a credential the deployer registered.
// The value itself never travels through business configuration.
type SecretRef string

var (
	envRefPattern  = regexp.MustCompile(`^env:[A-Z][A-Z0-9_]*$`)
	fileRefPattern = regexp.MustCompile(`^file:[A-Za-z0-9._-]+$`)
)

// ParseSecretRef accepts only `env:NAME` or `file:NAME` (a bare name inside the
// deployer-controlled secret directory). URLs, absolute paths, and anything
// containing a separator are rejected: they would let a caller choose where a
// secret is read from.
func ParseSecretRef(raw string) (SecretRef, error) {
	value := strings.TrimSpace(raw)
	if value == "" {
		return "", errors.New("secret reference is empty")
	}
	if strings.Contains(value, "://") {
		return "", fmt.Errorf("secret reference must not be a URL: %q", value)
	}
	if envRefPattern.MatchString(value) || fileRefPattern.MatchString(value) {
		return SecretRef(value), nil
	}
	if filepath.IsAbs(value) || strings.ContainsAny(value, `/\`) {
		return "", fmt.Errorf("secret reference must not be a path: %q", value)
	}
	return "", fmt.Errorf("secret reference must be env:NAME or file:NAME, got %q", value)
}

// Config is the validated runtime configuration.
type Config struct {
	Profile       Profile
	HTTPAddr      string
	DBDSN         string
	WebDir        string
	ModelEndpoint string
	ModelSecret   SecretRef
	StubLedger    string
	AllowScripts  bool
}

// ValidationError names the variable that made startup impossible.
type ValidationError struct {
	Variable string
	Reason   string
}

func (e *ValidationError) Error() string {
	if e.Variable == "" {
		return e.Reason
	}
	return fmt.Sprintf("%s: %s", e.Variable, e.Reason)
}

func invalid(variable, reason string) error {
	return &ValidationError{Variable: variable, Reason: reason}
}

// Lookup reads a variable, allowing tests to inject a map instead of the process
// environment.
type Lookup func(string) (string, bool)

// FromEnv returns a Lookup over the real environment.
func FromEnv() Lookup {
	return func(key string) (string, bool) { return os.LookupEnv(key) }
}

// Load validates the configuration for the declared profile.
func Load(lookup Lookup) (*Config, error) {
	profileRaw, ok := lookup(EnvProfile)
	if !ok || strings.TrimSpace(profileRaw) == "" {
		return nil, invalid(EnvProfile, "is required; set it to one of dev-stub, linux-sandbox, real-model, capacity-stub")
	}
	profile := Profile(strings.TrimSpace(profileRaw))
	if !knownProfiles[profile] {
		return nil, invalid(EnvProfile, fmt.Sprintf("unknown profile %q", profile))
	}

	cfg := &Config{
		Profile:  profile,
		HTTPAddr: orDefault(lookup, EnvHTTPAddr, "127.0.0.1:8088"),
		DBDSN:    value(lookup, EnvDBDSN),
		WebDir:   orDefault(lookup, EnvWebDir, filepath.Join("apps", "web", "src")),
	}

	if cfg.DBDSN == "" {
		return nil, invalid(EnvDBDSN, "is required; a process without its database cannot be ready")
	}

	// Arbitrary user scripts are only ever acceptable on a real isolation host.
	allowScripts, err := parseBool(lookup, EnvAllowScripts)
	if err != nil {
		return nil, err
	}
	cfg.AllowScripts = allowScripts
	if allowScripts && profile != ProfileLinuxSandbox {
		return nil, invalid(
			EnvAllowScripts,
			fmt.Sprintf("may only be enabled with profile %q; %q has no verified isolation", ProfileLinuxSandbox, profile),
		)
	}

	switch profile {
	case ProfileDevStub, ProfileCapacityStub:
		// A stub profile must be able to say where its stub results are recorded,
		// otherwise stub output can be mistaken for a real result later.
		cfg.StubLedger = orDefault(lookup, EnvStubLedger, "reports/p01/stub-ledger.jsonl")
	case ProfileRealModel:
		endpoint := value(lookup, EnvModelEndpoint)
		if endpoint == "" {
			return nil, invalid(EnvModelEndpoint, "is required for the real-model profile")
		}
		if strings.Contains(endpoint, "?") || strings.Contains(endpoint, "#") {
			return nil, invalid(EnvModelEndpoint, "must be a base endpoint without query or fragment")
		}
		ref, refErr := ParseSecretRef(value(lookup, EnvModelSecretRef))
		if refErr != nil {
			return nil, invalid(EnvModelSecretRef, refErr.Error())
		}
		cfg.ModelEndpoint = endpoint
		cfg.ModelSecret = ref
	case ProfileLinuxSandbox:
		// Nothing extra is required for the skeleton; the sandbox runtime is
		// verified by its own probe, not by a configuration flag.
	}

	return cfg, nil
}

// Stub reports whether this profile produces stub results that must be labelled.
func (c *Config) Stub() bool {
	return c.Profile == ProfileDevStub || c.Profile == ProfileCapacityStub
}

func value(lookup Lookup, key string) string {
	raw, _ := lookup(key)
	return strings.TrimSpace(raw)
}

func orDefault(lookup Lookup, key, fallback string) string {
	if raw := value(lookup, key); raw != "" {
		return raw
	}
	return fallback
}

func parseBool(lookup Lookup, key string) (bool, error) {
	raw := strings.ToLower(value(lookup, key))
	switch raw {
	case "", "false", "0":
		return false, nil
	case "true", "1":
		return true, nil
	default:
		return false, invalid(key, fmt.Sprintf("must be true or false, got %q", raw))
	}
}
