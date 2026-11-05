package modelproxy

// F08/T079: the routing control model.
//
// A request must be routed to exactly one published application version before a
// Run exists. That decision is a model call, but it is not a Run-scoped tool call:
// IMPLEMENTATION-DETAILS.md §45 says it belongs to the `platform_model_calls`
// ledger with request/background-job attribution, and the ledger's XOR CHECK means
// the row carries the `route_request` job id (the request is reached through
// `background_jobs.target_id`). This file adds the two pieces the router needs:
//
//   - RouteControlCandidate resolves the deployment's routing control model. It is
//     a normal registered model profile version marked in its config with
//     `route_control: true`, not a hidden second registry. Unlike Candidate (the
//     T024 validation subject), a control model must be `passed`: it is used in
//     production, not being validated.
//   - The dev-stub provider answers a `task: "route"` call deterministically from
//     the server-built prompt, so a real-stack test can exercise the server-side
//     candidate/authorization validation without a vendor. It always labels itself
//     `provider: "stub"`, and it only exists under the dev-stub/capacity-stub
//     profile with a candidate registered as provider "stub" — the existing
//     profile gate in Providers.For is unchanged.

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"regexp"
	"strings"

	"orchara/platform/internal/uuid"
)

// RouteControlConfigKey is the model-profile config member that marks one
// candidate as the deployment's routing control model. A deployment registers it
// like any other candidate (provider/endpoint/credential/validation); the router
// never accepts a model id from a handler or a user.
const RouteControlConfigKey = "route_control"

// ErrRouteControlUnconfigured means no passed, unrevoked model profile version
// declares itself the routing control model. Routing then fails closed with a
// typed request failure; it never falls back to an unlabelled heuristic.
var ErrRouteControlUnconfigured = errors.New("no routing control model is registered")

// RouteControlCandidate returns the newest passed, unrevoked candidate whose
// config declares `route_control: true`.
func (s *Service) RouteControlCandidate(ctx context.Context) (*Candidate, error) {
	var candidateID string
	err := s.DB.QueryRowContext(ctx, `
		SELECT id::text
		  FROM platform.model_profile_versions
		 WHERE revoked_at IS NULL
		   AND validation_state = 'passed'
		   AND config->>'`+RouteControlConfigKey+`' = 'true'
		 ORDER BY created_at DESC, id DESC
		 LIMIT 1`,
	).Scan(&candidateID)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrRouteControlUnconfigured
	}
	if err != nil {
		return nil, fmt.Errorf("resolving the routing control model: %w", err)
	}
	return s.ControlCandidate(ctx, candidateID)
}

// ControlCandidate loads one candidate for a platform control call. It requires a
// passed, unrevoked row and the same deployer-registered endpoint/credential
// references Candidate enforces, so a control model cannot reach an arbitrary URL
// or secret.
func (s *Service) ControlCandidate(ctx context.Context, candidateID string) (*Candidate, error) {
	if !uuid.Valid(strings.TrimSpace(candidateID)) {
		return nil, ErrCandidateUnavailable
	}
	var (
		candidate Candidate
		configRaw []byte
		revoked   sql.NullTime
	)
	err := s.DB.QueryRowContext(ctx, `
		SELECT id::text, provider, model_id, endpoint_ref, secret_ref, config,
		       validation_state, revoked_at, manifest_artifact_id::text
		  FROM platform.model_profile_versions
		 WHERE id = $1::uuid`, candidateID,
	).Scan(
		&candidate.ID, &candidate.Provider, &candidate.ModelID, &candidate.EndpointRef,
		&candidate.SecretRef, &configRaw, &candidate.ValidationState, &revoked,
		&candidate.ManifestArtifactID,
	)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrCandidateUnavailable
	}
	if err != nil {
		return nil, fmt.Errorf("reading the routing control model: %w", err)
	}
	if revoked.Valid || candidate.ValidationState != "passed" {
		return nil, ErrCandidateUnavailable
	}
	candidate.Config = json.RawMessage(configRaw)
	if err := validateReferences(&candidate, s.Catalog); err != nil {
		return nil, err
	}
	return &candidate, nil
}

// routeUUIDPattern extracts an explicit UUID from the user's request text. The
// dev-stub router treats an explicitly named version id as the user's choice; the
// server still rejects it when it is outside the authorized candidate set, which
// is exactly the T079 AC1 scenario.
var routeUUIDPattern = regexp.MustCompile(
	`[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}`)

// stubRoutePrompt is the exact server-built prompt shape the deterministic stub
// reads. The server owns it (internal/routing.buildPrompt); the stub only consumes
// the message and the candidate ids, never invents a candidate.
type stubRoutePrompt struct {
	Message    string `json:"message"`
	Candidates []struct {
		ApplicationVersionID string `json:"application_version_id"`
	} `json:"candidates"`
}

// stubRouteResponse is the deterministic dev-stub routing answer. It is real code
// under the same profile gate as every other stub call, labelled `stub: true`; a
// production deployment registers a passed real candidate and the stub branch is
// unreachable because Providers.For refuses a real candidate under a stub profile
// and vice versa.
//
// Rule: a UUID written in the request text is suggested verbatim (the model
// echoing the user's explicit choice), otherwise the first authorized candidate is
// suggested, otherwise no_match. The server validates the suggestion; the stub
// never filters candidates or hides the ambiguity.
func stubRouteResponse(request CallRequest) (ProviderResponse, error) {
	var prompt stubRoutePrompt
	if err := json.Unmarshal(request.Fixture, &prompt); err != nil {
		return ProviderResponse{}, fmt.Errorf("stub route prompt is not JSON: %w", err)
	}
	data := map[string]any{"provider": "stub", "stub": true}
	if match := routeUUIDPattern.FindString(prompt.Message); match != "" {
		data["decision"] = "select"
		data["candidate_version_id"] = strings.ToLower(match)
	} else if len(prompt.Candidates) > 0 {
		data["decision"] = "select"
		data["candidate_version_id"] = strings.ToLower(prompt.Candidates[0].ApplicationVersionID)
	} else {
		data["decision"] = "no_match"
		data["reason"] = "no authorized candidate matched the request"
	}
	output, err := canonicalJSON(data)
	if err != nil {
		return ProviderResponse{}, fmt.Errorf("canonicalising the stub route answer: %w", err)
	}
	tokensIn := int64((len(request.Fixture) + 3) / 4)
	tokensOut := int64((len(output) + 3) / 4)
	return ProviderResponse{
		Data:         data,
		Output:       output,
		TokensIn:     tokensIn,
		TokensOut:    tokensOut,
		ChargedUnits: (tokensIn + tokensOut) * reservedUnitsPerInputToken,
	}, nil
}
