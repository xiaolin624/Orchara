package config

// F05/T033: configuration for `cmd/supervisor exec --once`.
//
// The executor is an internal-protocol client, so it needs the same mTLS
// material as maintenance plus a *second* identity: the contract's artifact
// routes are worker-capacity routes, and the supervisor certificate is refused
// there on purpose. Two certificates in one process is the documented bridge
// until F04A splits the supervisor and the worker into separate processes.
//
// Everything is fail-closed: without the internal URL, both client identities,
// the CA and a read-only supervisor database identity the process refuses to
// start. The sandbox profile is validated by internal/sandbox when it is built,
// not here, because a missing rootfs is a per-run failure with a specific code,
// not a startup condition that would make the operator think the executor is
// misconfigured (it is the deployment that is).

import (
	"fmt"
	"net/url"
	"time"
)

// Environment variables owned by the T033 executor.
const (
	EnvSupervisorClientCert = "ORCHARA_SUPERVISOR_CLIENT_CERT"
	EnvSupervisorClientKey  = "ORCHARA_SUPERVISOR_CLIENT_KEY"
	EnvWorkerClientCert     = "ORCHARA_WORKER_CLIENT_CERT"
	EnvWorkerClientKey      = "ORCHARA_WORKER_CLIENT_KEY"
	EnvSupervisorDSN        = "ORCHARA_SUPERVISOR_DSN"
	EnvExecResultWait       = "ORCHARA_EXEC_RESULT_WAIT"
	EnvExecPollInterval     = "ORCHARA_EXEC_POLL_INTERVAL"
)

// Defaults for the executor waits.
const (
	DefaultExecResultWait   = 60 * time.Second
	DefaultExecPollInterval = 100 * time.Millisecond
	maxExecResultWait       = 10 * time.Minute
	minExecPollInterval     = 10 * time.Millisecond
)

// SupervisorExecConfig is everything `cmd/supervisor exec --once` needs to talk
// to the internal API and to read its own claimed run's input artifacts.
type SupervisorExecConfig struct {
	InternalURL    string
	CA             string
	SupervisorCert string
	SupervisorKey  string
	WorkerCert     string
	WorkerKey      string
	DBDSN          string
	Heartbeat      time.Duration
	ResultWait     time.Duration
	PollInterval   time.Duration
}

// LoadSupervisorExec validates the executor's configuration.
func LoadSupervisorExec(lookup Lookup) (*SupervisorExecConfig, error) {
	rawURL := value(lookup, EnvInternalURL)
	if rawURL == "" {
		return nil, invalid(EnvInternalURL, "is required; the supervisor talks to the internal mTLS API")
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
	if parsed.User != nil || parsed.RawQuery != "" || parsed.Fragment != "" ||
		(parsed.Path != "" && parsed.Path != "/") {
		return nil, invalid(EnvInternalURL, "must not carry credentials, a path, a query or a fragment")
	}
	required := []struct {
		name  string
		value string
	}{
		{EnvInternalClientCA, value(lookup, EnvInternalCA)},
		{EnvSupervisorClientCert, value(lookup, EnvSupervisorClientCert)},
		{EnvSupervisorClientKey, value(lookup, EnvSupervisorClientKey)},
		{EnvWorkerClientCert, value(lookup, EnvWorkerClientCert)},
		{EnvWorkerClientKey, value(lookup, EnvWorkerClientKey)},
		{EnvSupervisorDSN, value(lookup, EnvSupervisorDSN)},
	}
	for _, entry := range required {
		if entry.value == "" {
			return nil, invalid(entry.name, "is required for cmd/supervisor exec")
		}
	}
	heartbeat, err := parseDuration(lookup, EnvHeartbeatInterval, DefaultHeartbeatInterval)
	if err != nil {
		return nil, err
	}
	if heartbeat < minExecPollInterval {
		return nil, invalid(EnvHeartbeatInterval, fmt.Sprintf("must be at least %s", minExecPollInterval))
	}
	resultWait, err := parseDuration(lookup, EnvExecResultWait, DefaultExecResultWait)
	if err != nil {
		return nil, err
	}
	if resultWait > maxExecResultWait {
		return nil, invalid(EnvExecResultWait, fmt.Sprintf("must be at most %s", maxExecResultWait))
	}
	poll, err := parseDuration(lookup, EnvExecPollInterval, DefaultExecPollInterval)
	if err != nil {
		return nil, err
	}
	if poll < minExecPollInterval {
		return nil, invalid(EnvExecPollInterval, fmt.Sprintf("must be at least %s", minExecPollInterval))
	}
	return &SupervisorExecConfig{
		InternalURL:    rawURL,
		CA:             value(lookup, EnvInternalCA),
		SupervisorCert: value(lookup, EnvSupervisorClientCert),
		SupervisorKey:  value(lookup, EnvSupervisorClientKey),
		WorkerCert:     value(lookup, EnvWorkerClientCert),
		WorkerKey:      value(lookup, EnvWorkerClientKey),
		DBDSN:          value(lookup, EnvSupervisorDSN),
		Heartbeat:      heartbeat,
		ResultWait:     resultWait,
		PollInterval:   poll,
	}, nil
}
