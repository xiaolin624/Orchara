package modelproxy

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"orchara/platform/internal/config"
)

// realProvider is the fail-closed real-model path.
//
// It resolves the candidate's credential through `secret_ref` inside Go (env: or
// file: inside the deployer-controlled secret directory), and it never accepts
// an endpoint or a credential from a request. This batch compiles no vendor
// protocol adapter (F06 owns precise provider adapters, and inventing a wire
// format here would be untested protocol work that the ticket explicitly
// excludes), so a configured endpoint still answers with a stable
// `provider_adapter_unavailable` 503 — never a fabricated success and never a
// ledger row that claims a real model answered.
type realProvider struct {
	endpoint  string
	secretDir string
	lookup    config.Lookup
}

// Name is a label, not a URL and not a credential.
func (p *realProvider) Name() string { return "real" }

// Available answers the honest question before anything is billed: is a real
// endpoint deployed, can the candidate's credential be resolved inside Go, and
// is there a protocol adapter for it in this build?
//
// The third answer is "no" in this batch (F06 owns the vendor adapters), so a
// real candidate is refused here and never produces a ledger row. When F06 lands
// the adapter, only this method and Call change.
func (p *realProvider) Available(candidate *Candidate) error {
	if candidate == nil {
		return ErrCandidateUnavailable
	}
	if strings.TrimSpace(p.endpoint) == "" {
		return ErrNotConfigured
	}
	if _, err := resolveSecret(candidate.SecretRef, p.secretDir, p.lookup); err != nil {
		return ErrNotConfigured
	}
	return ErrAdapterUnavailable
}

func (p *realProvider) Call(_ context.Context, request CallRequest) (ProviderResponse, error) {
	if request.Candidate == nil {
		return ProviderResponse{}, ErrCandidateUnavailable
	}
	if err := p.Available(request.Candidate); err != nil {
		return ProviderResponse{}, err
	}
	return ProviderResponse{}, fmt.Errorf("%w: provider %q", ErrAdapterUnavailable, request.Candidate.Provider)
}

// parseReference validates the reference shape without contacting anything.
func parseReference(raw string) (string, error) {
	reference, err := config.ParseSecretRef(raw)
	if err != nil {
		return "", err
	}
	return string(reference), nil
}

// resolveSecret reads a deployer-registered credential.
//
// The returned value is the only place a credential exists in this package, and
// no error path includes it (or the reference): a caller may log the error.
func resolveSecret(rawRef, secretDir string, lookup config.Lookup) (string, error) {
	reference, err := config.ParseSecretRef(rawRef)
	if err != nil {
		return "", ErrNotConfigured
	}
	value := string(reference)
	switch {
	case strings.HasPrefix(value, "env:"):
		if lookup == nil {
			lookup = config.FromEnv()
		}
		name := strings.TrimPrefix(value, "env:")
		raw, ok := lookup(name)
		if !ok || strings.TrimSpace(raw) == "" {
			return "", ErrNotConfigured
		}
		return strings.TrimSpace(raw), nil
	case strings.HasPrefix(value, "file:"):
		root := strings.TrimSpace(secretDir)
		if root == "" {
			return "", ErrNotConfigured
		}
		name := strings.TrimPrefix(value, "file:")
		content, err := os.ReadFile(filepath.Join(root, name))
		if err != nil {
			// Deliberately not the path or the OS error: configuration facts are
			// not returned to a worker-facing endpoint.
			return "", ErrNotConfigured
		}
		secret := strings.TrimSpace(string(content))
		if secret == "" {
			return "", ErrNotConfigured
		}
		return secret, nil
	default:
		return "", ErrNotConfigured
	}
}
