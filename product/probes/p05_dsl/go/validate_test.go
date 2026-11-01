package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

// ---------------------------------------------------------------------------
// fixtures
// ---------------------------------------------------------------------------
func mustRead(t *testing.T, path string) []byte {
	t.Helper()
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("cannot read %s: %v", path, err)
	}
	return raw
}

func loadRulesForTest(t *testing.T) (*Rules, string) {
	t.Helper()
	// go test runs with the package directory (go/) as the working directory.
	path := filepath.Join("..", "rules.json")
	return mustLoadRules(t, path), path
}

func mustLoadRules(t *testing.T, path string) *Rules {
	t.Helper()
	rules, err := LoadRules(path)
	if err != nil {
		t.Fatalf("cannot load rules: %v", err)
	}
	return rules
}

func loadCorpusForTest(t *testing.T) (*Corpus, string, []byte) {
	t.Helper()
	path := filepath.Join("..", "corpus.json")
	raw := mustRead(t, path)
	var payload any
	if err := json.Unmarshal(raw, &payload); err != nil {
		t.Fatalf("cannot parse corpus: %v", err)
	}
	if failures := CheckCorpusShape(payload); len(failures) > 0 {
		t.Fatalf("corpus is not well formed: %v", failures)
	}
	corpus, err := ParseCorpus(payload)
	if err != nil {
		t.Fatalf("cannot parse corpus: %v", err)
	}
	return corpus, path, raw
}

// ---------------------------------------------------------------------------
// the canonicaliser is not allowed to drift from T003's shared vectors
// ---------------------------------------------------------------------------
type jcsVector struct {
	Name              string  `json:"name"`
	InputJSON         string  `json:"input_json"`
	ExpectedCanonical *string `json:"expected_canonical"`
	ExpectedSHA256    *string `json:"expected_sha256"`
}

func TestCanonicalizerMatchesSharedJCSVectors(t *testing.T) {
	path := filepath.Join("..", "..", "jcs", "vectors.json")
	var vectors []jcsVector
	if err := json.Unmarshal(mustRead(t, path), &vectors); err != nil {
		t.Fatalf("cannot parse %s: %v", path, err)
	}
	checked := 0
	for _, vector := range vectors {
		if vector.ExpectedCanonical == nil {
			// Lexer-level rejection cases belong to T003's implementation.
			continue
		}
		checked++
		canonical, err := CanonicalizeDocument([]byte(vector.InputJSON))
		if err != nil {
			t.Fatalf("%s: unexpected error: %v", vector.Name, err)
		}
		if string(canonical) != *vector.ExpectedCanonical {
			t.Fatalf("%s: canonical = %q, want %q", vector.Name, canonical, *vector.ExpectedCanonical)
		}
		if vector.ExpectedSHA256 != nil {
			if got := SHA256Hex(canonical); got != *vector.ExpectedSHA256 {
				t.Fatalf("%s: sha256 = %s, want %s", vector.Name, got, *vector.ExpectedSHA256)
			}
		}
	}
	if checked < 20 {
		t.Fatalf("only %d usable JCS vectors; the drift check would be too weak", checked)
	}
}

// ---------------------------------------------------------------------------
// the corpus
// ---------------------------------------------------------------------------
func TestCorpusMatchesEveryPinnedExpectation(t *testing.T) {
	corpus, _, raw := loadCorpusForTest(t)
	rules, _ := loadRulesForTest(t)
	output, err := RunCorpus(corpus, SHA256Hex(raw), rules)
	if err != nil {
		t.Fatalf("RunCorpus: %v", err)
	}
	for _, failure := range CheckExpectations(corpus, output) {
		t.Error(failure)
	}
	valid, invalid := 0, 0
	for _, entry := range corpus.Cases {
		if entry.Verdict == "valid" {
			valid++
		} else {
			invalid++
		}
	}
	if valid < 5 || invalid < 20 {
		t.Fatalf("corpus is too thin: %d valid / %d invalid", valid, invalid)
	}
	t.Logf("%d cases (%d valid, %d invalid) matched verdict, reason list and digest", len(corpus.Cases), valid, invalid)
}

func TestNormalisedDataMatchesPinnedCanonicalText(t *testing.T) {
	corpus, _, _ := loadCorpusForTest(t)
	rules, _ := loadRulesForTest(t)
	anchors := 0
	for _, entry := range corpus.Cases {
		if entry.Verdict != "valid" {
			continue
		}
		result, err := Validate(entry.Input, rules, ValidationOptions{})
		if err != nil {
			t.Fatalf("%s: %v", entry.Name, err)
		}
		if result.Verdict() != "valid" {
			t.Fatalf("%s: rejected with %v", entry.Name, result.Problems)
		}
		if result.Data.SHA256 != entry.ExpectedHash {
			t.Fatalf("%s: data digest = %s, want %s", entry.Name, result.Data.SHA256, entry.ExpectedHash)
		}
		if entry.ExpectedCanonical != "" {
			anchors++
			if string(result.Data.Canonical) != entry.ExpectedCanonical {
				t.Fatalf("%s: normal form drifted:\n got %s\nwant %s",
					entry.Name, result.Data.Canonical, entry.ExpectedCanonical)
			}
		}
	}
	if anchors == 0 {
		t.Fatal("no hand-written canonical anchor was checked")
	}
}

func TestReasonCodesAreDeclaredAndCovered(t *testing.T) {
	corpus, _, _ := loadCorpusForTest(t)
	rules, _ := loadRulesForTest(t)
	declared := map[string]bool{}
	for _, entry := range rules.Reasons {
		if declared[entry.Code] {
			t.Fatalf("duplicate reason code %q in rules.json", entry.Code)
		}
		declared[entry.Code] = true
	}
	seen := map[string]bool{}
	for _, entry := range corpus.Cases {
		for _, code := range entry.Reasons {
			if !declared[code] {
				t.Fatalf("%s: reason %q is not declared in rules.json", entry.Name, code)
			}
			seen[code] = true
		}
	}
	for _, entry := range rules.Reasons {
		if !seen[entry.Code] {
			t.Fatalf("reason %q has no negative case in the corpus", entry.Code)
		}
	}
	t.Logf("%d reason codes, all declared and all exercised", len(declared))
}

// ---------------------------------------------------------------------------
// missing values never turn into null
// ---------------------------------------------------------------------------
func caseByName(t *testing.T, corpus *Corpus, name string) *CorpusCase {
	t.Helper()
	for index := range corpus.Cases {
		if corpus.Cases[index].Name == name {
			return &corpus.Cases[index]
		}
	}
	t.Fatalf("corpus case %q is missing", name)
	return nil
}

func TestMissingValueIsNotImplicitNull(t *testing.T) {
	corpus, _, _ := loadCorpusForTest(t)
	rules, _ := loadRulesForTest(t)

	// A required application output that end does not map is rejected ..., and
	// no normalised document (with a null in its place) is produced.
	entry := caseByName(t, corpus, "end-missing-required-output")
	result, err := Validate(copyValue(entry.Input), rules, ValidationOptions{})
	if err != nil {
		t.Fatalf("validate: %v", err)
	}
	if result.Reason() != "missing_value" {
		t.Fatalf("reason = %q, want missing_value", result.Reason())
	}
	if result.Data != nil {
		t.Fatal("a rejected document must not produce normalised data")
	}
	if result.Artifact != nil {
		t.Fatal("a rejected document must not produce an artifact")
	}
	wire := WireResult(result, entry.Name)
	for _, key := range []string{"data_sha256", "data_canonical", "artifact_sha256"} {
		if _, present := wire[key]; present {
			t.Fatalf("rejected verdict carries %q; absence must stay absent", key)
		}
	}

	// ... and an explicit null is a *value*, not an absence: it is rejected as
	// the wrong container type rather than silently treated as {}.
	minimal := caseByName(t, corpus, "anchor-minimal-linear")
	broken := copyValue(minimal.Input).(map[string]any)
	nodes := broken["nodes"].([]any)
	start := nodes[0].(map[string]any)
	start["inputs"] = nil
	nulled, err := Validate(broken, rules, ValidationOptions{})
	if err != nil {
		t.Fatalf("validate: %v", err)
	}
	if nulled.Verdict() != "invalid" || nulled.Reason() != "bad_document" {
		t.Fatalf("inputs:null must be a shape error, got %v / %v", nulled.Verdict(), nulled.Reason())
	}

	// Deleting the key is missing, and missing is rejected too: nothing is
	// defaulted on the caller's behalf.
	absent := copyValue(minimal.Input).(map[string]any)
	delete(absent["nodes"].([]any)[0].(map[string]any), "inputs")
	missing, err := Validate(absent, rules, ValidationOptions{})
	if err != nil {
		t.Fatalf("validate: %v", err)
	}
	if missing.Verdict() != "invalid" || missing.Reason() != "bad_document" {
		t.Fatalf("absent inputs must be rejected, got %v / %v", missing.Verdict(), missing.Reason())
	}

	// Valid documents keep the same rule on the wire: no "reason" key at all.
	valid, err := Validate(copyValue(minimal.Input), rules, ValidationOptions{})
	if err != nil {
		t.Fatalf("validate: %v", err)
	}
	validWire := WireResult(valid, minimal.Name)
	if _, present := validWire["reason"]; present {
		t.Fatal("a valid verdict must not carry a reason key")
	}
}

func TestAbsentEdgePortMaterialisesTheDocumentedDefaultOnly(t *testing.T) {
	corpus, _, _ := loadCorpusForTest(t)
	rules, _ := loadRulesForTest(t)
	entry := caseByName(t, corpus, "anchor-minimal-linear")
	result, err := Validate(copyValue(entry.Input), rules, ValidationOptions{})
	if err != nil {
		t.Fatalf("validate: %v", err)
	}
	edges := result.Data.Document["edges"].([]any)
	for _, item := range edges {
		edge := item.(map[string]any)
		port, present := edge["port"]
		if !present {
			t.Fatal("normalised edge is missing its port")
		}
		if port != "default" {
			t.Fatalf("port = %v, want the documented default", port)
		}
	}
}

// ---------------------------------------------------------------------------
// data and artifact are separate objects with separate digests
// ---------------------------------------------------------------------------
func TestDataAndArtifactAreSeparate(t *testing.T) {
	corpus, _, _ := loadCorpusForTest(t)
	rules, path := loadRulesForTest(t)
	entry := caseByName(t, corpus, "linear-sales")
	result, err := Validate(copyValue(entry.Input), rules, ValidationOptions{})
	if err != nil {
		t.Fatalf("validate: %v", err)
	}
	if result.Verdict() != "valid" {
		t.Fatalf("anchor case rejected: %v", result.Problems)
	}
	if result.Data.SHA256 == result.Artifact.SHA256 {
		t.Fatal("data and artifact must not share a digest")
	}
	if result.Data.Canonical == nil || string(result.Data.Canonical) == string(result.Artifact.Canonical) {
		t.Fatal("data and artifact must not share a serialisation")
	}
	provenance := BuildProvenance(rules, result.InputSHA256, result.Data.SHA256, "corpus-sha", mustSHA(t, path))
	artifact, err := Publish(result.Data, provenance)
	if err != nil {
		t.Fatalf("publish: %v", err)
	}
	if artifact.Descriptor["data_sha256"] != result.Data.SHA256 {
		t.Fatal("the artifact must pin the data digest it publishes")
	}
	// Changing provenance changes the artifact, never the data.
	other := BuildProvenance(rules, result.InputSHA256, result.Data.SHA256, "different-corpus-sha", mustSHA(t, path))
	republished, err := Publish(result.Data, other)
	if err != nil {
		t.Fatalf("publish: %v", err)
	}
	if republished.SHA256 == artifact.SHA256 {
		t.Fatal("artifact digest ignored a provenance change")
	}
	if result.Data.SHA256 != entry.ExpectedHash {
		t.Fatal("republishing must not touch the data digest")
	}
}

func mustSHA(t *testing.T, path string) string {
	t.Helper()
	return SHA256Hex(mustRead(t, path))
}

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------
func copyValue(value any) any {
	switch typed := value.(type) {
	case map[string]any:
		clone := make(map[string]any, len(typed))
		for key, item := range typed {
			clone[key] = copyValue(item)
		}
		return clone
	case []any:
		clone := make([]any, len(typed))
		for index, item := range typed {
			clone[index] = copyValue(item)
		}
		return clone
	default:
		return value
	}
}
