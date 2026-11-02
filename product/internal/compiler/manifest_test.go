package compiler

import (
	"bytes"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"orchara/platform/internal/jcs"
)

func resolvedFor(t *testing.T) Resolved {
	t.Helper()
	return Resolved{
		WorkerImageDigest: "sha256:" + strings.Repeat("a", 64),
		RuntimeLockSHA256: strings.Repeat("f", 64),
	}
}

func sampleDefinition(t *testing.T, raw string) map[string]any {
	t.Helper()
	var definition map[string]any
	if err := json.Unmarshal([]byte(raw), &definition); err != nil {
		t.Fatalf("parse the sample definition: %v", err)
	}
	return definition
}

func sampleCard() map[string]any {
	return map[string]any{
		"purpose":          "summarise sales",
		"includes":         []any{"aggregation"},
		"excludes":         []any{"external writes"},
		"required_inputs":  []any{"sales csv"},
		"expected_outputs": []any{"report"},
		"examples":         []any{"monthly report", "weekly digest"},
	}
}

func validBuildArgs(t *testing.T) (map[string]any, map[string]any) {
	t.Helper()
	return sampleDefinition(t, `{"dsl_version":"1.0"}`), sampleCard()
}

func TestBuildMinimalManifestHasExactlyTheSchemaFields(t *testing.T) {
	definition, card := validBuildArgs(t)
	built, err := Build(resolvedFor(t), "33333333-3333-4333-8333-333333333333", 1,
		definition, card, nil, nil, nil)
	if err != nil {
		t.Fatalf("Build: %v", err)
	}
	if built.Manifest.ManifestVersion != ManifestVersion ||
		built.Manifest.CompilerVersion != CompilerVersion ||
		built.Manifest.StateSchemaVersion != StateSchemaVersion {
		t.Fatalf("fixed constants missing: %+v", built.Manifest)
	}
	if built.Manifest.ToolArtifacts == nil || built.Manifest.ModelProfileVersions == nil ||
		built.Manifest.SubappVersions == nil || built.Manifest.ConnectorPolicies == nil {
		t.Fatal("manifest arrays must be present as empty arrays, never null")
	}
	var decoded map[string]json.RawMessage
	if err := json.Unmarshal(built.Bytes, &decoded); err != nil {
		t.Fatalf("decode the canonical bytes: %v", err)
	}
	if len(decoded) != len(manifestKeys) {
		t.Fatalf("manifest has %d fields, schema has %d: %v", len(decoded), len(manifestKeys), decoded)
	}
	for _, key := range manifestKeys {
		if _, present := decoded[key]; !present {
			t.Fatalf("manifest is missing schema field %q", key)
		}
	}
	if strings.Contains(string(built.Bytes), `"layout"`) {
		t.Fatal("layout leaked into the manifest")
	}
	if built.SHA256 != jcs.SHA256Hex(built.Bytes) {
		t.Fatal("SHA256 is not the digest of the canonical bytes")
	}
}

func TestBuildIsStableAcrossJSONMapOrdering(t *testing.T) {
	first := sampleDefinition(t, `{"dsl_version":"1.0","input_schema":{"type":"object","properties":{"a":{"type":"string"}}},"nodes":[]}`)
	second := sampleDefinition(t, `{"nodes":[],"input_schema":{"properties":{"a":{"type":"string"}},"type":"object"},"dsl_version":"1.0"}`)
	inputs := resolvedFor(t)
	card := sampleCard()
	one, err := Build(inputs, "33333333-3333-4333-8333-333333333333", 2, first, card, nil, nil, nil)
	if err != nil {
		t.Fatalf("Build first: %v", err)
	}
	two, err := Build(inputs, "33333333-3333-4333-8333-333333333333", 2, second, sampleCard(), nil, nil, nil)
	if err != nil {
		t.Fatalf("Build second: %v", err)
	}
	if !bytes.Equal(one.Bytes, two.Bytes) || one.SHA256 != two.SHA256 {
		t.Fatalf("canonical bytes depend on Go map order:\n%s\n%s", one.Bytes, two.Bytes)
	}
}

func TestBuildNormalisesSetOrderAndDeduplicates(t *testing.T) {
	definition, card := validBuildArgs(t)
	inputs := resolvedFor(t)
	toolA := ToolArtifact{ID: "11111111-1111-4111-8111-111111111111", ImageDigest: "sha256:" + strings.Repeat("b", 64)}
	toolB := ToolArtifact{ID: "44444444-4444-4444-8444-444444444444", ImageDigest: "sha256:" + strings.Repeat("c", 64)}
	model := "22222222-2222-4222-8222-222222222222"
	subapp := "55555555-5555-4555-8555-555555555555"
	one, err := Build(inputs, "33333333-3333-4333-8333-333333333333", 1, definition, card,
		[]ToolArtifact{toolB, toolA}, []string{model, model}, []string{subapp, subapp})
	if err != nil {
		t.Fatalf("Build one: %v", err)
	}
	two, err := Build(inputs, "33333333-3333-4333-8333-333333333333", 1, definition, card,
		[]ToolArtifact{toolA, toolB}, []string{model}, []string{subapp})
	if err != nil {
		t.Fatalf("Build two: %v", err)
	}
	if !bytes.Equal(one.Bytes, two.Bytes) {
		t.Fatalf("iterating a set in a different order changed the manifest")
	}
	manifest := one.Manifest
	if len(manifest.ToolArtifacts) != 2 || manifest.ToolArtifacts[0].ID != toolA.ID || manifest.ToolArtifacts[1].ID != toolB.ID {
		t.Fatalf("tool_artifacts are not sorted by id: %+v", manifest.ToolArtifacts)
	}
	if len(manifest.ModelProfileVersions) != 1 || len(manifest.SubappVersions) != 1 {
		t.Fatalf("sets were not deduplicated: %+v / %+v", manifest.ModelProfileVersions, manifest.SubappVersions)
	}
}

func TestBuildRejectsInvalidManifestContent(t *testing.T) {
	definition, card := validBuildArgs(t)
	cases := map[string]func() (*Built, error){
		"bad worker digest": func() (*Built, error) {
			return Build(Resolved{WorkerImageDigest: "sha256:XYZ", RuntimeLockSHA256: strings.Repeat("f", 64)},
				"33333333-3333-4333-8333-333333333333", 1, definition, card, nil, nil, nil)
		},
		"bad lock hash": func() (*Built, error) {
			return Build(Resolved{WorkerImageDigest: "sha256:" + strings.Repeat("a", 64), RuntimeLockSHA256: "nothex"},
				"33333333-3333-4333-8333-333333333333", 1, definition, card, nil, nil, nil)
		},
		"bad application id": func() (*Built, error) {
			return Build(resolvedFor(t), "not-a-uuid", 1, definition, card, nil, nil, nil)
		},
		"zero revision": func() (*Built, error) {
			return Build(resolvedFor(t), "33333333-3333-4333-8333-333333333333", 0, definition, card, nil, nil, nil)
		},
		"nil definition": func() (*Built, error) {
			return Build(resolvedFor(t), "33333333-3333-4333-8333-333333333333", 1, nil, card, nil, nil, nil)
		},
		"empty card": func() (*Built, error) {
			return Build(resolvedFor(t), "33333333-3333-4333-8333-333333333333", 1, definition, map[string]any{}, nil, nil, nil)
		},
		"partial card": func() (*Built, error) {
			return Build(resolvedFor(t), "33333333-3333-4333-8333-333333333333", 1, definition,
				map[string]any{"purpose": "only a purpose"}, nil, nil, nil)
		},
		"bad tool id": func() (*Built, error) {
			return Build(resolvedFor(t), "33333333-3333-4333-8333-333333333333", 1, definition, card,
				[]ToolArtifact{{ID: "nope", ImageDigest: "sha256:" + strings.Repeat("b", 64)}}, nil, nil)
		},
		"bad tool digest": func() (*Built, error) {
			return Build(resolvedFor(t), "33333333-3333-4333-8333-333333333333", 1, definition, card,
				[]ToolArtifact{{ID: "11111111-1111-4111-8111-111111111111", ImageDigest: "latest"}}, nil, nil)
		},
		"conflicting tool digest": func() (*Built, error) {
			return Build(resolvedFor(t), "33333333-3333-4333-8333-333333333333", 1, definition, card,
				[]ToolArtifact{
					{ID: "11111111-1111-4111-8111-111111111111", ImageDigest: "sha256:" + strings.Repeat("b", 64)},
					{ID: "11111111-1111-4111-8111-111111111111", ImageDigest: "sha256:" + strings.Repeat("c", 64)},
				}, nil, nil)
		},
		"bad model id": func() (*Built, error) {
			return Build(resolvedFor(t), "33333333-3333-4333-8333-333333333333", 1, definition, card, nil, []string{"latest"}, nil)
		},
	}
	for name, build := range cases {
		t.Run(name, func(t *testing.T) {
			if _, err := build(); !errors.Is(err, ErrInvalidInput) {
				t.Fatalf("expected ErrInvalidInput, got %v", err)
			}
		})
	}
}

func TestValidateCapabilityCardLocatesProblems(t *testing.T) {
	if problems := ValidateCapabilityCard(nil); len(problems) != 1 || problems[0].Field != "" {
		t.Fatalf("nil card must be one whole-card problem: %+v", problems)
	}
	card := sampleCard()
	card["examples"] = []any{"only one"}
	problems := ValidateCapabilityCard(card)
	if len(problems) != 1 || problems[0].Field != "examples" {
		t.Fatalf("one example must be located at examples: %+v", problems)
	}
	card = sampleCard()
	delete(card, "excludes")
	problems = ValidateCapabilityCard(card)
	if len(problems) != 1 || problems[0].Field != "excludes" {
		t.Fatalf("a missing field must be located: %+v", problems)
	}
	card = sampleCard()
	card["extra"] = "no"
	problems = ValidateCapabilityCard(card)
	if len(problems) != 1 || problems[0].Field != "extra" {
		t.Fatalf("an unknown field must be located: %+v", problems)
	}
	card = sampleCard()
	card["purpose"] = 7
	problems = ValidateCapabilityCard(card)
	if len(problems) != 1 || problems[0].Field != "purpose" {
		t.Fatalf("a non-string purpose must be located: %+v", problems)
	}
}

func TestResolveFailsClosedOnDeploymentInputs(t *testing.T) {
	if _, err := (Inputs{RuntimeLockPath: "does-not-matter"}).Resolve(); !errors.Is(err, ErrUnconfigured) {
		t.Fatalf("a missing worker digest must be ErrUnconfigured, got %v", err)
	}
	var unconfigured *UnconfiguredError
	if _, err := (Inputs{WorkerImageDigest: "sha256:xyz"}).Resolve(); !errors.As(err, &unconfigured) {
		t.Fatalf("an invalid worker digest must be an UnconfiguredError, got %v", err)
	}
	if unconfigured.Variable != "ORCHARA_WORKER_IMAGE_DIGEST" {
		t.Fatalf("UnconfiguredError names %q", unconfigured.Variable)
	}
	missing := filepath.Join(t.TempDir(), "missing.lock")
	_, err := (Inputs{WorkerImageDigest: "sha256:" + strings.Repeat("a", 64), RuntimeLockPath: missing}).Resolve()
	if !errors.As(err, &unconfigured) || unconfigured.Variable != "ORCHARA_RUNTIME_LOCK_PATH" {
		t.Fatalf("a missing runtime lock must be an UnconfiguredError, got %v", err)
	}
}

func TestResolveHashesTheRuntimeLockFile(t *testing.T) {
	path := filepath.Join(t.TempDir(), "requirements.lock.txt")
	if err := os.WriteFile(path, []byte("abc"), 0o600); err != nil {
		t.Fatalf("write the lock file: %v", err)
	}
	resolved, err := (Inputs{WorkerImageDigest: "sha256:" + strings.Repeat("a", 64), RuntimeLockPath: path}).Resolve()
	if err != nil {
		t.Fatalf("Resolve: %v", err)
	}
	// sha256("abc")
	if resolved.RuntimeLockSHA256 != "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad" {
		t.Fatalf("runtime lock digest = %s", resolved.RuntimeLockSHA256)
	}
}
