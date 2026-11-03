package modelregistry

// Unit tests for the catalog, manifest digest and request validation. The
// end-to-end behaviour (registration rows, validate job, report binding) is
// covered by the real-PostgreSQL tests in product/tests/f06.

import (
	"encoding/json"
	"errors"
	"strings"
	"testing"
)

func envLookup(values map[string]string) func(string) string {
	return func(key string) string { return values[key] }
}

func TestCatalogDefaultsToDevStub(t *testing.T) {
	catalog, err := FromEnv(envLookup(nil))
	if err != nil {
		t.Fatalf("FromEnv: %v", err)
	}
	endpoint, ok := catalog.Endpoint(DefaultEndpointID)
	if !ok {
		t.Fatalf("the dev-stub default endpoint %q is missing", DefaultEndpointID)
	}
	if endpoint.DataDestination != DefaultDestination || endpoint.CredentialRef != DefaultCredential {
		t.Fatalf("unexpected default endpoint: %+v", endpoint)
	}
	if _, ok := catalog.Credential(DefaultCredential); !ok {
		t.Fatalf("the dev-stub default credential %q is missing", DefaultCredential)
	}
}

func TestCatalogFailsClosedOnUnknownOrTrailingJSON(t *testing.T) {
	cases := map[string]map[string]string{
		"unknown field": {
			EnvModelEndpoints: `[{"id":"e1","data_destination":"d","credential_ref":"c1","url":"https://x"}]`,
			EnvCredentialRefs: `[{"id":"c1","description":"test"}]`,
		},
		"trailing data": {
			EnvModelEndpoints: `[{"id":"e1","data_destination":"d","credential_ref":"c1"}] []`,
			EnvCredentialRefs: `[{"id":"c1","description":"test"}]`,
		},
		"endpoint names unknown credential": {
			EnvModelEndpoints: `[{"id":"e1","data_destination":"d","credential_ref":"nope"}]`,
			EnvCredentialRefs: `[{"id":"c1","description":"test"}]`,
		},
		"only one variable": {
			EnvModelEndpoints: `[{"id":"e1","data_destination":"d","credential_ref":"c1"}]`,
		},
		"id looks like a path": {
			EnvModelEndpoints: `[{"id":"e1","data_destination":"d","credential_ref":"c1"}]`,
			EnvCredentialRefs: `[{"id":"/etc/passwd","description":"test"}]`,
		},
	}
	for name, values := range cases {
		t.Run(name, func(t *testing.T) {
			if _, err := FromEnv(envLookup(values)); err == nil {
				t.Fatalf("FromEnv accepted a malformed catalog")
			}
		})
	}
}

func TestCatalogFromEnvListsReferencesOnly(t *testing.T) {
	catalog, err := FromEnv(envLookup(map[string]string{
		EnvModelEndpoints: `[{"id":"prod-1","data_destination":"eu-cloud","credential_ref":"prod-cred"}]`,
		EnvCredentialRefs: `[{"id":"prod-cred","description":"production key"}]`,
	}))
	if err != nil {
		t.Fatalf("FromEnv: %v", err)
	}
	body, err := json.Marshal(catalog.Credentials())
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	if strings.Contains(strings.ToLower(string(body)), "secret") {
		t.Fatalf("a credential projection leaked a secret-shaped field: %s", body)
	}
	if len(catalog.Endpoints()) != 1 || catalog.Endpoints()[0].ID != "prod-1" {
		t.Fatalf("unexpected endpoint list: %+v", catalog.Endpoints())
	}
}

func validInput() ModelInput {
	contextWindow := int64(8192)
	maxOutput := int64(1024)
	return ModelInput{
		Name:        "candidate one",
		Provider:    "stub",
		ModelID:     "stub-model-1",
		EndpointRef: DefaultEndpointID,
		SecretRef:   DefaultCredential,
		Config: ModelConfig{
			Kind:            "generation",
			Protocol:        "chat_compatible",
			ContextWindow:   &contextWindow,
			MaxOutputTokens: &maxOutput,
			TimeoutMS:       30000,
			Price: ModelPrice{
				Currency:         "USD",
				InputPerMillion:  "0",
				OutputPerMillion: "0",
			},
		},
		DataDestination: DefaultDestination,
	}
}

func TestManifestDigestIsDeterministicAndContentBound(t *testing.T) {
	input := validInput()
	first, firstSHA, err := Manifest(input)
	if err != nil {
		t.Fatalf("Manifest: %v", err)
	}
	second, secondSHA, err := Manifest(input)
	if err != nil {
		t.Fatalf("Manifest: %v", err)
	}
	if firstSHA != secondSHA || string(first) != string(second) {
		t.Fatalf("the same registration produced two manifests")
	}
	if len(firstSHA) != 64 {
		t.Fatalf("manifest sha is not a hex sha256: %q", firstSHA)
	}
	changed := validInput()
	changed.ModelID = "stub-model-2"
	_, changedSHA, err := Manifest(changed)
	if err != nil {
		t.Fatalf("Manifest: %v", err)
	}
	if changedSHA == firstSHA {
		t.Fatalf("a changed model id kept the same manifest digest")
	}
	for _, forbidden := range []string{"created_at", "timestamp", "run_id"} {
		if strings.Contains(string(first), forbidden) {
			t.Fatalf("the manifest carries %q, which would break digest stability: %s", forbidden, first)
		}
	}
}

func TestValidateInputRejectsUnknownReferencesAndStubMisuse(t *testing.T) {
	service, err := New(nil, nil, nil)
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	cases := map[string]func(*ModelInput){
		"unknown endpoint": func(input *ModelInput) { input.EndpointRef = "not-registered" },
		"unknown secret":   func(input *ModelInput) { input.SecretRef = "not-registered" },
		"destination disagrees with endpoint": func(input *ModelInput) {
			input.DataDestination = "eu-cloud"
		},
		"stub outside stub destination": func(input *ModelInput) {
			input.SecretRef = "not-registered"
		},
		"empty name":                  func(input *ModelInput) { input.Name = " " },
		"missing context window":      func(input *ModelInput) { input.Config.ContextWindow = nil },
		"zero timeout":                func(input *ModelInput) { input.Config.TimeoutMS = 0 },
		"bad kind":                    func(input *ModelInput) { input.Config.Kind = "image" },
		"bad temperature":             func(input *ModelInput) { v := 2.5; input.Config.Temperature = &v },
		"short currency":              func(input *ModelInput) { input.Config.Price.Currency = "US" },
		"duplicate fallback ids":      func(input *ModelInput) { input.Config.FallbackProfileVersionIDs = []string{"not-a-uuid", "not-a-uuid"} },
		"embedding without dimension": func(input *ModelInput) { input.Config.Kind = "embedding" },
	}
	for name, mutate := range cases {
		t.Run(name, func(t *testing.T) {
			input := validInput()
			mutate(&input)
			var validation *ValidationError
			if err := service.ValidateInput(&input); !errors.As(err, &validation) {
				t.Fatalf("ValidateInput = %v, want a ValidationError", err)
			}
		})
	}
}

func TestValidateInputAcceptsCatalogReferences(t *testing.T) {
	service, err := New(nil, nil, nil)
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	input := validInput()
	if err := service.ValidateInput(&input); err != nil {
		t.Fatalf("ValidateInput: %v", err)
	}
}

func TestModelProfileProjectionHasNoSecret(t *testing.T) {
	reportID := "11111111-1111-4111-8111-111111111111"
	profile := ModelProfile{
		ID:                 "22222222-2222-4222-8222-222222222222",
		Name:               "candidate",
		ModelID:            "stub-model-1",
		ValidationState:    "passed",
		DataDestination:    DefaultDestination,
		EvaluationReportID: &reportID,
	}
	body, err := json.Marshal(profile)
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	for _, forbidden := range []string{"secret", "endpoint_ref", "credential"} {
		if strings.Contains(strings.ToLower(string(body)), forbidden) {
			t.Fatalf("the profile projection leaked %q: %s", forbidden, body)
		}
	}
}
