package execution

import (
	"strings"
	"testing"
)

// TestValidateNodeResultsAcceptsTheClosedVocabulary is the unit seam for the
// per-node result report: a data/file/log row must name a DSL node id, a known
// kind and a UUID artifact before any database read happens.
func TestValidateNodeResultsAcceptsTheClosedVocabulary(t *testing.T) {
	const artifactID = "11111111-2222-4333-8444-555555555555"
	normalised, err := validateNodeResults([]NodeResult{
		{NodeID: "stats", Kind: NodeResultKindData, ArtifactID: artifactID, Label: "data"},
		{NodeID: "stats", Kind: NodeResultKindFile, ArtifactID: artifactID, Label: "summary.json"},
		{NodeID: "stats", Kind: NodeResultKindLog, ArtifactID: artifactID, Label: "log"},
	})
	if err != nil {
		t.Fatalf("a legal batch was refused: %v", err)
	}
	if len(normalised) != 3 {
		t.Fatalf("normalised %d rows, want 3", len(normalised))
	}
	if normalised[1].Label != "summary.json" {
		t.Fatalf("label was not preserved: %+v", normalised[1])
	}
}

// TestValidateNodeResultsRefusesMalformedRows proves each located refusal: the
// report endpoint must answer 422 per field, never a partial write.
func TestValidateNodeResultsRefusesMalformedRows(t *testing.T) {
	const artifactID = "11111111-2222-4333-8444-555555555555"
	cases := []struct {
		name  string
		rows  []NodeResult
		field string
	}{
		{"empty", nil, "results"},
		{"unknown kind", []NodeResult{{NodeID: "stats", Kind: "raw", ArtifactID: artifactID, Label: "x"}}, "results[0].kind"},
		{"bad node id", []NodeResult{{NodeID: "1 node", Kind: NodeResultKindData, ArtifactID: artifactID, Label: "x"}}, "results[0].node_id"},
		{"bad artifact", []NodeResult{{NodeID: "stats", Kind: NodeResultKindData, ArtifactID: "nope", Label: "x"}}, "results[0].artifact_id"},
		{"empty label", []NodeResult{{NodeID: "stats", Kind: NodeResultKindData, ArtifactID: artifactID, Label: "  "}}, "results[0].label"},
		{"newline label", []NodeResult{{NodeID: "stats", Kind: NodeResultKindData, ArtifactID: artifactID, Label: "a\nb"}}, "results[0].label"},
		{"duplicate row", []NodeResult{
			{NodeID: "stats", Kind: NodeResultKindData, ArtifactID: artifactID, Label: "a"},
			{NodeID: "stats", Kind: NodeResultKindData, ArtifactID: artifactID, Label: "b"},
		}, "results[1].artifact_id"},
	}
	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			_, err := validateNodeResults(testCase.rows)
			if err == nil {
				t.Fatal("a malformed batch was accepted")
			}
			validation, ok := err.(*ValidationError)
			if !ok {
				t.Fatalf("want *ValidationError, got %T: %v", err, err)
			}
			if validation.Field != testCase.field {
				t.Fatalf("field = %q, want %q", validation.Field, testCase.field)
			}
		})
	}
}

// TestNodeResultKindsMapOntoTheUploadPurpose is the "logs are never data" rule at
// report time: stdout/stderr must be uploaded with purpose=log and every node
// output with purpose=node_output.
func TestNodeResultKindsMapOntoTheUploadPurpose(t *testing.T) {
	if nodeResultPurposes[NodeResultKindData] != RunArtifactPurposeNodeOutput ||
		nodeResultPurposes[NodeResultKindFile] != RunArtifactPurposeNodeOutput {
		t.Fatalf("data/file must map to node_output: %v", nodeResultPurposes)
	}
	if nodeResultPurposes[NodeResultKindLog] != RunArtifactPurposeLog {
		t.Fatalf("log must map to the log purpose: %v", nodeResultPurposes)
	}
	for kind := range nodeResultKinds {
		if _, ok := nodeResultPurposes[kind]; !ok {
			t.Fatalf("kind %q has no purpose mapping", kind)
		}
	}
}

// TestNodeResultLabelIsBounded keeps the display label printable and bounded.
func TestNodeResultLabelIsBounded(t *testing.T) {
	const artifactID = "11111111-2222-4333-8444-555555555555"
	_, err := validateNodeResults([]NodeResult{{
		NodeID: "stats", Kind: NodeResultKindFile, ArtifactID: artifactID,
		Label: strings.Repeat("a", maxNodeResultLabel+1),
	}})
	if err == nil {
		t.Fatal("an oversized label was accepted")
	}
}
