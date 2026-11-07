package evaluation

// F13B/T104: the imported tau2-bench subset must be consumable by the product's
// own strict dataset parser.
//
// The Python suite (tests/f13b/test_t104_tau2_import.py) proves the import is
// pinned, licence-reviewed, schema-valid and refuses empty checkers. This test
// proves the other half: the committed adapted cases load through ParseDataset
// unchanged, are labelled adapted_external, carry a non-empty checker and name
// the pinned upstream source. It executes nothing: a parsed case is a frozen
// input, not a verdict, and no tau2 result is claimed.

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestF13BT104ExternalTasksLoadThroughTheStrictDatasetParser(t *testing.T) {
	path := filepath.Join("..", "..", "tests", "f13b", "external", "tau2", "tasks.json")
	document, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	dataset, err := ParseDataset(document)
	if err != nil {
		t.Fatalf("the imported tasks do not load through ParseDataset: %v", err)
	}
	if len(dataset.Cases) != 3 {
		t.Fatalf("expected the three-task subset, found %d cases", len(dataset.Cases))
	}
	seen := map[string]bool{}
	for _, parsed := range dataset.Cases {
		if parsed.Provenance.Origin != "adapted_external" {
			t.Errorf("case %q origin is %q, want adapted_external", parsed.CaseID, parsed.Provenance.Origin)
		}
		if parsed.Provenance.LicenseReview != "reviewed" {
			t.Errorf("case %q licence review is %q, want reviewed", parsed.CaseID, parsed.Provenance.LicenseReview)
		}
		if !strings.Contains(parsed.Provenance.Reference, "da0835f6c67baef58bdda18f2a3b037dfb3a1754") {
			t.Errorf("case %q reference does not pin the tau2 commit: %q", parsed.CaseID, parsed.Provenance.Reference)
		}
		if !strings.Contains(parsed.Provenance.Reference, "#task=") {
			t.Errorf("case %q reference does not name the upstream task id: %q", parsed.CaseID, parsed.Provenance.Reference)
		}
		if len(parsed.Assertions) == 0 {
			t.Errorf("case %q carries no assertion; an empty checker is not a case", parsed.CaseID)
		}
		if parsed.FixtureID == "" {
			t.Errorf("case %q names no environment fixture", parsed.CaseID)
		}
		if parsed.Category != "approval_effects" || parsed.Split != "frozen_test" {
			t.Errorf("case %q is %s/%s, want approval_effects/frozen_test", parsed.CaseID, parsed.Category, parsed.Split)
		}
		if seen[parsed.CaseID] {
			t.Errorf("case id %q appears more than once", parsed.CaseID)
		}
		seen[parsed.CaseID] = true
	}
}
