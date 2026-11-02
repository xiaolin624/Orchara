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
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"

	"orchara/platform/internal/compiler"
	"orchara/platform/internal/objectstore"
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
	EnvProfile  = profileDefaultEnvVar
	EnvHTTPAddr = "ORCHARA_HTTP_ADDR"
	// EnvPublicURL is the absolute base URL signed object links are minted
	// against (e.g. https://orchara.example). Default: http://<HTTPAddr>.
	EnvPublicURL      = "ORCHARA_PUBLIC_URL"
	EnvDBDSN          = "ORCHARA_DB_DSN"
	EnvModelEndpoint  = "ORCHARA_MODEL_ENDPOINT"
	EnvModelSecretRef = "ORCHARA_MODEL_SECRET_REF"
	EnvStubLedger     = "ORCHARA_STUB_LEDGER"
	EnvAllowScripts   = "ORCHARA_ALLOW_ARBITRARY_SCRIPTS"
	EnvWebDir         = "ORCHARA_WEB_DIR"

	// Object store (F00B/T022). Empty means "this process cannot touch object
	// bytes": every operation that needs them fails closed.
	EnvObjectDir = "ORCHARA_OBJECT_DIR"
	// Object links (F00B/T024): an HMAC key of at least 32 bytes signs short
	// lived PUT/GET URLs, and the TTL bounds how long one stays valid. An empty
	// key does not stop the process: signed-link traffic fails closed.
	EnvObjectSigningKey = "ORCHARA_OBJECT_SIGNING_KEY"
	EnvObjectLinkTTL    = "ORCHARA_OBJECT_LINK_TTL"
	// EnvSecretDir is the deployer-controlled directory `file:NAME` secret
	// references resolve inside. It never contains an inline credential.
	EnvSecretDir = "ORCHARA_SECRET_DIR"
	// EnvRetryBackoff is the base delay before the second attempt. A duration
	// ("5s", "50ms") so tests can make retries deterministic without waiting.
	EnvRetryBackoff = "ORCHARA_BACKGROUND_RETRY_BACKOFF"
	// EnvExecutionLeaseTTL is the F03 claim lease length. It is a deployment
	// knob bounded by the execution protocol (1s..1h), not a per-request value.
	EnvExecutionLeaseTTL = "ORCHARA_EXECUTION_LEASE_TTL"

	// Internal mTLS listener for maintenance/worker/supervisor traffic.
	EnvInternalAddr       = "ORCHARA_INTERNAL_ADDR"
	EnvInternalServerCert = "ORCHARA_INTERNAL_SERVER_CERT"
	EnvInternalServerKey  = "ORCHARA_INTERNAL_SERVER_KEY"
	EnvInternalClientCA   = "ORCHARA_INTERNAL_CLIENT_CA"

	// Execution manifest deployment inputs (F02A/T025). The worker image digest
	// is a placeholder until F04A/F15A build an image; a missing digest does not
	// stop the API (identity/draft work remains usable) but every manifest-
	// generating request answers 503 compiler_unconfigured. The runtime lock is
	// the P01 locked package set; its SHA-256 is part of the manifest.
	EnvWorkerImageDigest = compiler.EnvWorkerImageDigest
	EnvRuntimeLockPath   = compiler.EnvRuntimeLockPath

	// Internal mTLS client (cmd/maintenance).
	EnvInternalURL        = "ORCHARA_INTERNAL_URL"
	EnvInternalClientCert = "ORCHARA_INTERNAL_CLIENT_CERT"
	EnvInternalClientKey  = "ORCHARA_INTERNAL_CLIENT_KEY"
	EnvInternalCA         = "ORCHARA_INTERNAL_CA"

	// The maintenance process's own database identity. It is deliberately not
	// ORCHARA_DB_DSN: the maintenance role is not the API's role.
	EnvMaintenanceDSN = "ORCHARA_MAINTENANCE_DSN"
	// EnvHeartbeatInterval is how often a running handler renews its lease.
	EnvHeartbeatInterval = "ORCHARA_BACKGROUND_HEARTBEAT_INTERVAL"

	// F00B/T026 maintenance pool and handler-wait knobs. Every one is also a
	// `run` flag; the environment is the deployment default, the flag the
	// per-process override, and both are validated before the process starts.
	EnvSystemIOSlots          = "ORCHARA_MAINTENANCE_SYSTEM_IO_SLOTS"
	EnvWorkSlots              = "ORCHARA_MAINTENANCE_WORK_SLOTS"
	EnvDatasetWaitTimeout     = "ORCHARA_MAINTENANCE_DATASET_WAIT_TIMEOUT"
	EnvEvaluationPollInterval = "ORCHARA_MAINTENANCE_EVALUATION_POLL_INTERVAL"
	EnvReportWaitTimeout      = "ORCHARA_MAINTENANCE_REPORT_WAIT_TIMEOUT"
)

// F00B/T026 defaults. artifact_finalize gets a dedicated system_io pool (2
// slots, BACKGROUND-JOBS.md §3); everything else shares the work pool (1 slot).
const (
	DefaultSystemIOSlots          = 2
	DefaultWorkSlots              = 1
	DefaultDatasetWaitTimeout     = 60 * time.Second
	DefaultEvaluationPollInterval = 100 * time.Millisecond
	DefaultReportWaitTimeout      = 60 * time.Second

	maxMaintenanceSlots     = 32
	minEvaluationPollPeriod = 10 * time.Millisecond
	maxHandlerWait          = 24 * time.Hour
)

// Defaults fixed by BACKGROUND-JOBS.md §1 and the F00B plan.
const (
	// DefaultHeartbeatInterval is the documented 5s heartbeat against a 30s lease.
	DefaultHeartbeatInterval = 5 * time.Second
	// DefaultBackgroundRetryBackoff is the documented base retry delay.
	DefaultBackgroundRetryBackoff = 5 * time.Second
	// maxBackgroundRetryBackoff keeps a misconfigured base from parking work.
	maxBackgroundRetryBackoff = time.Hour

	// DefaultExecutionLeaseTTL is the documented claim lease. It matches
	// execution.DefaultLeaseTTL; a supervisor must heartbeat well inside it.
	DefaultExecutionLeaseTTL = 30 * time.Second
	// minExecutionLeaseTTL/maxExecutionLeaseTTL bound the deployment knob.
	minExecutionLeaseTTL = time.Second
	maxExecutionLeaseTTL = time.Hour
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
	// PublicURL is the absolute base URL signed object links are minted
	// against. It is not a secret, but it must be the URL a worker can reach.
	PublicURL string
	// ObjectDir is the object-store root. Empty is legal: a process that never
	// touches object bytes (an API instance without artifact work) starts, and
	// every operation that needs the store refuses instead of guessing a path.
	ObjectDir string
	// ObjectSigningKey signs short-lived object links. It is a secret and never
	// logged; empty means signed-link traffic is refused (the API still starts).
	ObjectSigningKey string
	// ObjectLinkTTL is how long a minted PUT/GET link stays valid.
	ObjectLinkTTL time.Duration
	// SecretDir is the deployer-controlled root for `file:NAME` secret refs.
	SecretDir string
	// RetryBackoff is the base exponential-retry delay for background jobs.
	RetryBackoff time.Duration
	// ExecutionLeaseTTL is the F03 claim lease length (30s by default).
	ExecutionLeaseTTL time.Duration
	// WorkerImageDigest is the deployer-provided image digest pinned into every
	// execution manifest. Empty is legal: the API starts, and every
	// manifest-generating request answers 503 compiler_unconfigured. This batch
	// has not built a worker image (F04A/F15A do), so the value is a placeholder.
	WorkerImageDigest string
	// RuntimeLockPath is the locked runtime file whose SHA-256 enters the
	// manifest. It defaults to the P01 lock (probes/agent_min/requirements.lock.txt);
	// a missing file makes manifest generation fail closed, not invent a hash.
	RuntimeLockPath string
	// Internal is the optional internal mTLS listener configuration. Nil means
	// the listener is disabled; a partially configured set is a startup error.
	Internal *InternalTLS
}

// InternalTLS is the API's internal listener configuration.
type InternalTLS struct {
	Addr       string
	ServerCert string
	ServerKey  string
	ClientCA   string
}

// MaintenanceConfig is everything cmd/maintenance run needs: a verified
// internal endpoint, the object store and the maintenance database identity.
// It deliberately does not load the API's model/profile configuration: a
// maintenance process has no model endpoint and no user-facing HTTP listener.
type MaintenanceConfig struct {
	InternalURL       string
	ClientCert        string
	ClientKey         string
	CA                string
	ObjectDir         string
	DBDSN             string
	HeartbeatInterval time.Duration
	// F00B/T026 pool shape and handler wait bounds. SystemIOSlots is how many
	// concurrent artifact_finalize workers run; WorkSlots is how many workers run
	// every other compiled kind. A waiting evaluation therefore consumes a work
	// slot while finalize keeps its own.
	SystemIOSlots          int
	WorkSlots              int
	DatasetWaitTimeout     time.Duration
	EvaluationPollInterval time.Duration
	ReportWaitTimeout      time.Duration
}

// SweepConfig is everything `cmd/maintenance sweep` needs: the object store to
// inspect and the maintenance database identity to resolve references. It has
// no internal-API/TLS requirement, because sweeping is a local maintenance
// operation and never claims work.
type SweepConfig struct {
	ObjectDir string
	DBDSN     string
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
	profile, err := parseProfile(lookup)
	if err != nil {
		return nil, err
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

	publicURL := value(lookup, EnvPublicURL)
	if publicURL == "" {
		publicURL = "http://" + cfg.HTTPAddr
	}
	parsedPublic, err := url.Parse(publicURL)
	if err != nil || parsedPublic.Host == "" ||
		(parsedPublic.Scheme != "http" && parsedPublic.Scheme != "https") ||
		parsedPublic.RawQuery != "" || parsedPublic.Fragment != "" || parsedPublic.User != nil {
		return nil, invalid(EnvPublicURL,
			"must be an absolute http(s) base URL without credentials, query or fragment")
	}
	cfg.PublicURL = strings.TrimSuffix(publicURL, "/")

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

	// F00B/T022 runtime knobs. They are profile-independent: every profile that
	// runs the API or maintenance can carry background jobs.
	cfg.ObjectDir = value(lookup, EnvObjectDir)
	signingKey := value(lookup, EnvObjectSigningKey)
	if signingKey != "" && len(signingKey) < objectstore.MinSigningKeyBytes {
		return nil, invalid(EnvObjectSigningKey,
			fmt.Sprintf("must be at least %d bytes when set", objectstore.MinSigningKeyBytes))
	}
	cfg.ObjectSigningKey = signingKey
	linkTTL, err := parseDuration(lookup, EnvObjectLinkTTL, objectstore.DefaultLinkTTL)
	if err != nil {
		return nil, err
	}
	if linkTTL > objectstore.MaxLinkTTL {
		return nil, invalid(EnvObjectLinkTTL,
			fmt.Sprintf("must be at most %s", objectstore.MaxLinkTTL))
	}
	cfg.ObjectLinkTTL = linkTTL
	cfg.SecretDir = value(lookup, EnvSecretDir)
	retryBackoff, err := parseDuration(lookup, EnvRetryBackoff, DefaultBackgroundRetryBackoff)
	if err != nil {
		return nil, err
	}
	if retryBackoff > maxBackgroundRetryBackoff {
		return nil, invalid(EnvRetryBackoff,
			fmt.Sprintf("must be at most %s", maxBackgroundRetryBackoff))
	}
	cfg.RetryBackoff = retryBackoff
	// F03/T029: the claim lease is a deployment knob, but an absurd value is a
	// configuration error rather than a silently normalised lease.
	leaseTTL, err := parseDuration(lookup, EnvExecutionLeaseTTL, DefaultExecutionLeaseTTL)
	if err != nil {
		return nil, err
	}
	if leaseTTL < minExecutionLeaseTTL || leaseTTL > maxExecutionLeaseTTL {
		return nil, invalid(EnvExecutionLeaseTTL,
			fmt.Sprintf("must be between %s and %s", minExecutionLeaseTTL, maxExecutionLeaseTTL))
	}
	cfg.ExecutionLeaseTTL = leaseTTL
	// F02A/T025 deployment inputs. A malformed digest is a configuration error
	// (refuse to start); an absent one is the honest "no worker image yet"
	// state that manifest endpoints answer with 503 compiler_unconfigured.
	cfg.WorkerImageDigest = value(lookup, EnvWorkerImageDigest)
	if cfg.WorkerImageDigest != "" && !compiler.ValidWorkerImageDigest(cfg.WorkerImageDigest) {
		return nil, invalid(EnvWorkerImageDigest,
			"must be sha256:<64 lowercase hex> when set; the compiler never invents an image digest")
	}
	cfg.RuntimeLockPath = orDefault(lookup, EnvRuntimeLockPath, compiler.DefaultRuntimeLockPath)
	internalTLS, err := loadInternalTLS(lookup)
	if err != nil {
		return nil, err
	}
	cfg.Internal = internalTLS

	return cfg, nil
}

// loadInternalTLS validates the internal listener's four variables as a set.
// An address without its certificate material, or material without an address,
// is a startup error: a listener must never come up without verifying clients,
// and unused key material must not sit in the environment looking intentional.
func loadInternalTLS(lookup Lookup) (*InternalTLS, error) {
	addr := value(lookup, EnvInternalAddr)
	cert := value(lookup, EnvInternalServerCert)
	key := value(lookup, EnvInternalServerKey)
	ca := value(lookup, EnvInternalClientCA)
	if addr == "" {
		if cert != "" || key != "" || ca != "" {
			return nil, invalid(EnvInternalAddr, "is required when internal TLS material is configured")
		}
		return nil, nil
	}
	for _, required := range []struct {
		name  string
		value string
	}{
		{EnvInternalServerCert, cert},
		{EnvInternalServerKey, key},
		{EnvInternalClientCA, ca},
	} {
		if required.value == "" {
			return nil, invalid(required.name,
				fmt.Sprintf("is required when %s is set; the internal listener fails closed without mTLS", EnvInternalAddr))
		}
	}
	return &InternalTLS{
		Addr:       addr,
		ServerCert: cert,
		ServerKey:  key,
		ClientCA:   ca,
	}, nil
}

// LoadMaintenance validates what cmd/maintenance run needs. The internal URL
// must be https with no path/query/credentials: it is an mTLS endpoint, not a
// generic webhook, and nothing about it may be redirected by configuration.
func LoadMaintenance(lookup Lookup) (*MaintenanceConfig, error) {
	rawURL := value(lookup, EnvInternalURL)
	if rawURL == "" {
		return nil, invalid(EnvInternalURL, "is required; maintenance talks to the internal mTLS API")
	}
	parsed, err := url.Parse(rawURL)
	if err != nil {
		return nil, invalid(EnvInternalURL, "is not a valid URL")
	}
	if parsed.Scheme != "https" {
		return nil, invalid(EnvInternalURL, "must use https://; the internal API requires mTLS")
	}
	if parsed.Host == "" {
		return nil, invalid(EnvInternalURL, "must name a host and port")
	}
	if parsed.User != nil {
		return nil, invalid(EnvInternalURL, "must not carry credentials")
	}
	if parsed.Path != "" && parsed.Path != "/" {
		return nil, invalid(EnvInternalURL, "must not carry a path; the client appends contract paths")
	}
	if parsed.RawQuery != "" || parsed.Fragment != "" {
		return nil, invalid(EnvInternalURL, "must not carry a query or fragment")
	}

	values := map[string]string{
		EnvInternalClientCert: value(lookup, EnvInternalClientCert),
		EnvInternalClientKey:  value(lookup, EnvInternalClientKey),
		EnvInternalCA:         value(lookup, EnvInternalCA),
		EnvObjectDir:          value(lookup, EnvObjectDir),
		EnvMaintenanceDSN:     value(lookup, EnvMaintenanceDSN),
	}
	for _, required := range []struct {
		name  string
		value string
	}{
		{EnvInternalClientCert, values[EnvInternalClientCert]},
		{EnvInternalClientKey, values[EnvInternalClientKey]},
		{EnvInternalCA, values[EnvInternalCA]},
		{EnvObjectDir, values[EnvObjectDir]},
		{EnvMaintenanceDSN, values[EnvMaintenanceDSN]},
	} {
		if required.value == "" {
			return nil, invalid(required.name, "is required for cmd/maintenance run")
		}
	}
	heartbeat, err := parseDuration(lookup, EnvHeartbeatInterval, DefaultHeartbeatInterval)
	if err != nil {
		return nil, err
	}
	systemIOSlots, err := parseSlots(lookup, EnvSystemIOSlots, DefaultSystemIOSlots)
	if err != nil {
		return nil, err
	}
	workSlots, err := parseSlots(lookup, EnvWorkSlots, DefaultWorkSlots)
	if err != nil {
		return nil, err
	}
	datasetWait, err := parseHandlerDuration(lookup, EnvDatasetWaitTimeout, DefaultDatasetWaitTimeout)
	if err != nil {
		return nil, err
	}
	pollInterval, err := parseHandlerDuration(lookup, EnvEvaluationPollInterval, DefaultEvaluationPollInterval)
	if err != nil {
		return nil, err
	}
	if pollInterval < minEvaluationPollPeriod {
		return nil, invalid(EnvEvaluationPollInterval,
			fmt.Sprintf("must be at least %s", minEvaluationPollPeriod))
	}
	reportWait, err := parseHandlerDuration(lookup, EnvReportWaitTimeout, DefaultReportWaitTimeout)
	if err != nil {
		return nil, err
	}
	return &MaintenanceConfig{
		InternalURL:            strings.TrimSuffix(rawURL, "/"),
		ClientCert:             values[EnvInternalClientCert],
		ClientKey:              values[EnvInternalClientKey],
		CA:                     values[EnvInternalCA],
		ObjectDir:              values[EnvObjectDir],
		DBDSN:                  values[EnvMaintenanceDSN],
		HeartbeatInterval:      heartbeat,
		SystemIOSlots:          systemIOSlots,
		WorkSlots:              workSlots,
		DatasetWaitTimeout:     datasetWait,
		EvaluationPollInterval: pollInterval,
		ReportWaitTimeout:      reportWait,
	}, nil
}

// LoadSweep validates the two inputs the orphan sweep needs. A missing object
// root or database identity is a startup error: the sweep must never guess a
// directory or run without the authority to resolve references.
func LoadSweep(lookup Lookup) (*SweepConfig, error) {
	objectDir := value(lookup, EnvObjectDir)
	if objectDir == "" {
		return nil, invalid(EnvObjectDir, "is required for cmd/maintenance sweep")
	}
	dsn := value(lookup, EnvMaintenanceDSN)
	if dsn == "" {
		return nil, invalid(EnvMaintenanceDSN, "is required for cmd/maintenance sweep")
	}
	return &SweepConfig{ObjectDir: objectDir, DBDSN: dsn}, nil
}

// parseSlots reads a positive worker-slot count with a hard upper bound: a typo
// must not start hundreds of claim loops against one database.
func parseSlots(lookup Lookup, key string, fallback int) (int, error) {
	raw := value(lookup, key)
	if raw == "" {
		return fallback, nil
	}
	value, err := strconv.Atoi(raw)
	if err != nil {
		return 0, invalid(key, fmt.Sprintf("must be an integer, got %q", raw))
	}
	if value < 1 || value > maxMaintenanceSlots {
		return 0, invalid(key, fmt.Sprintf("must be between 1 and %d", maxMaintenanceSlots))
	}
	return value, nil
}

// parseHandlerDuration is parseDuration plus the upper bound a handler wait may
// have; an accidental "24h" would park a work slot for a day.
func parseHandlerDuration(lookup Lookup, key string, fallback time.Duration) (time.Duration, error) {
	duration, err := parseDuration(lookup, key, fallback)
	if err != nil {
		return 0, err
	}
	if duration > maxHandlerWait {
		return 0, invalid(key, fmt.Sprintf("must be at most %s", maxHandlerWait))
	}
	return duration, nil
}

func parseDuration(lookup Lookup, key string, fallback time.Duration) (time.Duration, error) {
	raw := value(lookup, key)
	if raw == "" {
		return fallback, nil
	}
	duration, err := time.ParseDuration(raw)
	if err != nil {
		return 0, invalid(key, fmt.Sprintf("must be a duration such as 5s, got %q", raw))
	}
	if duration <= 0 {
		return 0, invalid(key, "must be greater than zero")
	}
	return duration, nil
}

// DatabaseConfig is the configuration subset an administrative command needs
// when it only touches the database. A migration must not require a model
// endpoint it will never call, but it must still refuse an unknown profile.
type DatabaseConfig struct {
	Profile Profile
	DBDSN   string
}

// LoadDatabase validates the profile and the database DSN for cmd/admin.
func LoadDatabase(lookup Lookup) (*DatabaseConfig, error) {
	profile, err := parseProfile(lookup)
	if err != nil {
		return nil, err
	}
	dsn := value(lookup, EnvDBDSN)
	if dsn == "" {
		return nil, invalid(EnvDBDSN, "is required; the command cannot reach the platform database")
	}
	return &DatabaseConfig{Profile: profile, DBDSN: dsn}, nil
}

func parseProfile(lookup Lookup) (Profile, error) {
	profileRaw, ok := lookup(EnvProfile)
	if !ok || strings.TrimSpace(profileRaw) == "" {
		return "", invalid(EnvProfile, "is required; set it to one of dev-stub, linux-sandbox, real-model, capacity-stub")
	}
	profile := Profile(strings.TrimSpace(profileRaw))
	if !knownProfiles[profile] {
		return "", invalid(EnvProfile, fmt.Sprintf("unknown profile %q", profile))
	}
	return profile, nil
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
