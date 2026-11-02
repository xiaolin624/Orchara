package workflow

import (
	"encoding/json"
	"os"
	"testing"

	"orchara/platform/internal/jcs"
)

// TestCorpusReplaysTheP05Expectations replays probes/p05_dsl/corpus.json through
// this package and compares every verdict, reason list, input hash, normal-form
// hash and canonical text with the pinned values.
//
// The corpus is the contract between the P05 implementations, so this is the
// evidence that the port did not change validation semantics while it moved out
// of probes/. The probes themselves are not modified.
func TestCorpusReplaysTheP05Expectations(t *testing.T) {
	raw, err := os.ReadFile("../../probes/p05_dsl/corpus.json")
	if err != nil {
		t.Fatalf("read the shared corpus: %v", err)
	}
	var payload any
	if err := json.Unmarshal(raw, &payload); err != nil {
		t.Fatalf("parse the shared corpus: %v", err)
	}
	if failures := CheckCorpusShape(payload); len(failures) > 0 {
		t.Fatalf("the shared corpus is not well formed: %v", failures)
	}
	corpus, err := ParseCorpus(payload)
	if err != nil {
		t.Fatalf("parse corpus cases: %v", err)
	}
	rules, err := DefaultRules()
	if err != nil {
		t.Fatalf("load the embedded rules: %v", err)
	}
	output, err := RunCorpus(corpus, jcs.SHA256Hex(raw), rules)
	if err != nil {
		t.Fatalf("run the corpus: %v", err)
	}
	if failures := CheckExpectations(corpus, output); len(failures) > 0 {
		t.Fatalf("the ported validator disagrees with the corpus:\n%v", failures)
	}
	if len(corpus.Cases) == 0 {
		t.Fatal("the shared corpus has no cases")
	}
	t.Logf("replayed %d cases against rules %s", len(corpus.Cases), rules.ProbeVersion)
}

// TestEmbeddedRulesMatchTheProbeSource fails when the embedded copy and the
// probe's rule table drift apart. The copy exists so binaries are self-contained;
// the probe stays the source of record, so a one-sided edit must fail loudly.
func TestEmbeddedRulesMatchTheProbeSource(t *testing.T) {
	probe, err := os.ReadFile("../../probes/p05_dsl/rules.json")
	if err != nil {
		t.Fatalf("read the probe rules: %v", err)
	}
	if string(probe) != string(embeddedRules) {
		t.Fatal("internal/workflow/rules.json differs from probes/p05_dsl/rules.json; " +
			"the embedded rule table must stay a verbatim copy")
	}
}

// TestDefaultRulesRejectAnUnknownReasonMeaning pins the helper the located
// errors use: known codes get the rules file's meaning, unknown codes stay
// distinguishable instead of silently sharing a generic message.
func TestDefaultRulesRejectAnUnknownReasonMeaning(t *testing.T) {
	rules, err := DefaultRules()
	if err != nil {
		t.Fatalf("load the embedded rules: %v", err)
	}
	if meaning := rules.ReasonMeaning("cycle"); meaning == "" {
		t.Fatal("the rules table has no meaning for the cycle code")
	}
	if meaning := rules.ReasonMeaning("not_a_real_code"); meaning != "" {
		t.Fatalf("unexpected meaning for an unknown code: %q", meaning)
	}
}
