package capabilities

import (
	"context"
	"testing"

	"orchara/platform/internal/compiler"
)

// The manifest allowlist and the kind-ambiguity refusal are decided before any
// database/provider work, so they are unit-testable without a stack. These are
// the rules that stop a worker from inventing a capability identity.
func TestResolveCapabilityAllowlistAndAmbiguity(t *testing.T) {
	const (
		ambiguous = "11111111-1111-4111-8111-111111111111"
		modelOnly = "22222222-2222-4222-8222-222222222222"
		unknown   = "33333333-3333-4333-8333-333333333333"
	)
	run := &runContext{Manifest: &compiler.Manifest{
		ModelProfileVersions: []string{modelOnly, ambiguous},
		ToolArtifacts:        []compiler.ToolArtifact{{ID: ambiguous}},
		ConnectorPolicies:    []compiler.ConnectorPolicy{{VersionID: ambiguous, PolicySHA256: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}},
	}}
	service := &Service{}

	cases := []struct {
		name   string
		kind   string
		id     string
		status int
		code   string
	}{
		{"both sets", "model", ambiguous, 409, CodeCapabilityKindAmbiguous},
		{"model not in manifest", "model", unknown, 403, CodeCapabilityNotAllowed},
		{"tool not in manifest", "tool", unknown, 403, CodeCapabilityNotAllowed},
		{"model id as tool", "tool", modelOnly, 403, CodeCapabilityNotAllowed},
		{"connector not in manifest", "connector", modelOnly, 403, CodeCapabilityNotAllowed},
		{"all three sets", "connector", ambiguous, 409, CodeCapabilityKindAmbiguous},
	}
	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			_, failure := service.resolveCapability(context.Background(), run, Input{
				CapabilityVersion: testCase.id,
				CapabilityKind:    testCase.kind,
			})
			if failure == nil {
				t.Fatalf("%s was accepted", testCase.name)
			}
			if failure.Status != testCase.status || failure.Code != testCase.code {
				t.Fatalf("expected %d/%s, got %d/%s (%v)", testCase.status, testCase.code,
					failure.Status, failure.Code, failure)
			}
		})
	}
}
