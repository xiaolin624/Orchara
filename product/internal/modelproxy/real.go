package modelproxy

import (
	"context"
	"errors"
	"io"
	"os"
	"path/filepath"
	"strings"

	"orchara/platform/internal/config"
	"orchara/platform/internal/modelregistry"
)

// realProvider is the fail-closed real-model path.
//
// It resolves the candidate's credential through `secret_ref` inside Go (env: or
// file: inside the deployer-controlled secret directory, or a deployer catalog
// credential whose entry names an environment variable), and it never accepts
// an endpoint or a credential from a request. The wire protocol is the single
// OpenAI-compatible chat-completions adapter in openai.go; an unregistered
// destination, a protocol this build does not speak, or an unresolvable
// credential answers with a stable typed error before anything is dispatched or
// billed.
type realProvider struct {
	endpoint  string
	secretDir string
	lookup    config.Lookup
	catalog   *modelregistry.Catalog
}

// Name is a label, not a URL and not a credential.
func (p *realProvider) Name() string { return "real" }

// Available answers the honest question before anything is billed: is a real
// endpoint deployed, does the candidate name an approved destination, does the
// registration declare the chat-completions protocol, and can the candidate's
// credential be resolved inside Go?
func (p *realProvider) Available(candidate *Candidate) error {
	if candidate == nil {
		return ErrCandidateUnavailable
	}
	if strings.TrimSpace(p.endpoint) == "" {
		return ErrNotConfigured
	}
	if _, err := chatEndpoint(p.endpoint); err != nil {
		return err
	}
	if err := p.checkDestination(candidate.EndpointRef); err != nil {
		return err
	}
	registered, err := parseProviderConfig(candidate.Config)
	if err != nil {
		return err
	}
	if !registered.supportsChatCompletions() {
		return ErrAdapterUnavailable
	}
	if _, err := p.resolveCredential(candidate.SecretRef); err != nil {
		return err
	}
	return nil
}

func (p *realProvider) Call(ctx context.Context, request CallRequest) (ProviderResponse, error) {
	if request.Candidate == nil {
		return ProviderResponse{}, ErrCandidateUnavailable
	}
	if err := p.Available(request.Candidate); err != nil {
		return ProviderResponse{}, err
	}
	registered, err := parseProviderConfig(request.Candidate.Config)
	if err != nil {
		return ProviderResponse{}, err
	}
	secret, err := p.resolveCredential(request.Candidate.SecretRef)
	if err != nil {
		return ProviderResponse{}, err
	}
	endpoint, err := chatEndpoint(p.endpoint)
	if err != nil {
		return ProviderResponse{}, err
	}
	body, err := buildChatRequest(request, registered, false)
	if err != nil {
		return ProviderResponse{}, err
	}
	response, cancel, err := p.send(ctx, endpoint, secret, body, registered.timeout(), "application/json")
	if err != nil {
		return ProviderResponse{}, err
	}
	defer cancel()
	defer func() { _ = response.Body.Close() }()
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		// The body is never part of an error or a log line: it is drained only
		// so the connection can be reused.
		_, _ = io.Copy(io.Discard, io.LimitReader(response.Body, 4<<10))
		return ProviderResponse{}, classifyStatus(response.StatusCode)
	}
	content, err := readLimitedBody(response.Body)
	if err != nil {
		if errors.Is(err, ErrResponseTooLarge) {
			return ProviderResponse{}, err
		}
		return ProviderResponse{}, &TransientError{Reason: "the model provider response ended before it could be read"}
	}
	return p.decodeChatCompletion(content, request, registered)
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
