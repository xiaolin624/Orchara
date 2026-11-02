package compiler

import (
	"bytes"
	"encoding/json"
	"flag"
	"os"
	"path/filepath"
	"regexp"
	"testing"

	"orchara/platform/internal/jcs"
)

// -update rewrites the golden snapshot files in tests/contracts/snapshots. They
// are checked in, so a normal test run proves the bytes do not drift when the
// runtime lock, compiler constants, canonicaliser or manifest composition change.
var updateGolden = flag.Bool("update", false, "rewrite the execution-manifest golden snapshots")

const (
	snapshotsRoot  = "../../tests/contracts/snapshots"
	runtimeLockRel = "../../probes/agent_min/requirements.lock.txt"
)

// goldenIdentity is the fixed deployment identity the snapshots pin.
type goldenIdentity struct {
	ApplicationID        string         `json:"application_id"`
	SourceRevision       int64          `json:"source_revision"`
	WorkerImageDigest    string         `json:"worker_image_digest"`
	ToolArtifacts        []ToolArtifact `json:"tool_artifacts"`
	ModelProfileVersions []string       `json:"model_profile_versions"`
	SubappVersions       []string       `json:"subapp_versions"`
}

type goldenCase struct {
	Name           string
	Definition     map[string]any
	CapabilityCard map[string]any
	Layout         map[string]any
	Built          *Built
}

func readJSONFile(t *testing.T, path string, target any) {
	t.Helper()
	payload, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	if err := json.Unmarshal(payload, target); err != nil {
		t.Fatalf("parse %s: %v", path, err)
	}
}

func goldenCases(t *testing.T, identity goldenIdentity, resolved Resolved) []goldenCase {
	t.Helper()
	build := func(name string) goldenCase {
		directory := filepath.Join(snapshotsRoot, name)
		entry := goldenCase{Name: name}
		readJSONFile(t, filepath.Join(directory, "definition.json"), &entry.Definition)
		readJSONFile(t, filepath.Join(directory, "capability_card.json"), &entry.CapabilityCard)
		readJSONFile(t, filepath.Join(directory, "layout.json"), &entry.Layout)
		built, err := Build(
			resolved,
			identity.ApplicationID,
			identity.SourceRevision,
			entry.Definition,
			entry.CapabilityCard,
			identity.ToolArtifacts,
			identity.ModelProfileVersions,
			identity.SubappVersions,
		)
		if err != nil {
			t.Fatalf("build the %s manifest: %v", name, err)
		}
		entry.Built = built
		return entry
	}
	return []goldenCase{build("sales"), build("layout-only")}
}

// TestGoldenSnapshotsMatchTheBuilder regenerates the snapshot bytes in memory
// and fails when the checked-in bytes drift. Run with -update to rewrite them.
func TestGoldenSnapshotsMatchTheBuilder(t *testing.T) {
	var identity goldenIdentity
	readJSONFile(t, filepath.Join(snapshotsRoot, "inputs.json"), &identity)
	lockSHA, err := RuntimeLockSHA256(runtimeLockRel)
	if err != nil {
		t.Fatalf("hash the P01 runtime lock: %v", err)
	}
	resolved := Resolved{WorkerImageDigest: identity.WorkerImageDigest, RuntimeLockSHA256: lockSHA}
	cases := goldenCases(t, identity, resolved)

	sales, twin := cases[0], cases[1]
	if !bytes.Equal(sales.Built.Bytes, twin.Built.Bytes) {
		t.Fatalf("a layout-only twin produced different manifest bytes:\n%s\n%s",
			sales.Built.Bytes, twin.Built.Bytes)
	}
	salesLayout, _ := json.Marshal(sales.Layout)
	twinLayout, _ := json.Marshal(twin.Layout)
	if bytes.Equal(salesLayout, twinLayout) {
		t.Fatal("the layout-only twin has the same layout; the twin must differ only in layout")
	}

	pretty, err := json.MarshalIndent(sales.Built.Manifest, "", "  ")
	if err != nil {
		t.Fatalf("encode the pretty manifest: %v", err)
	}
	pretty = append(pretty, '\n')

	for _, entry := range cases {
		directory := filepath.Join(snapshotsRoot, entry.Name)
		want := map[string][]byte{
			"manifest.json":   pretty,
			"manifest.jcs":    entry.Built.Bytes,
			"manifest.sha256": []byte(entry.Built.SHA256 + "\n"),
		}
		for name, expected := range want {
			path := filepath.Join(directory, name)
			if *updateGolden {
				if err := os.WriteFile(path, expected, 0o644); err != nil {
					t.Fatalf("write %s: %v", path, err)
				}
			}
			actual, err := os.ReadFile(path)
			if err != nil {
				t.Fatalf("read %s (run go test ./internal/compiler -run TestGoldenSnapshotsMatchTheBuilder -update): %v", path, err)
			}
			if !bytes.Equal(actual, expected) {
				t.Fatalf("%s has drifted from the builder output (regenerate with -update if the change is intended)\ngot:  %q\nwant: %q",
					path, truncate(actual), truncate(expected))
			}
		}
	}

	// The checked-in sha file must hash the checked-in JCS bytes.
	snapshotJCS, err := os.ReadFile(filepath.Join(snapshotsRoot, "sales", "manifest.jcs"))
	if err != nil {
		t.Fatalf("read the sales JCS bytes: %v", err)
	}
	if got := jcs.SHA256Hex(snapshotJCS); got != sales.Built.SHA256 {
		t.Fatalf("manifest.sha256 = %s, bytes hash to %s", sales.Built.SHA256, got)
	}
	if !regexp.MustCompile(`^[0-9a-f]{64}$`).MatchString(sales.Built.SHA256) {
		t.Fatalf("manifest_sha256 is not lowercase hex: %q", sales.Built.SHA256)
	}
}

func truncate(payload []byte) string {
	const limit = 160
	if len(payload) <= limit {
		return string(payload)
	}
	return string(payload[:limit]) + "..."
}
