package evaluation

// F13B/T102: the frozen seven-category corpus must be consumable by the
// product's own strict dataset parser.
//
// The Python suite (tests/f13b/test_t102_corpus.py) proves the corpus is
// schema-valid, traceable and split-isolated. This test proves the other half:
// every committed document loads through ParseDataset unchanged, with the
// seven categories and both splits present and case ids unique across files.
// It executes nothing: a parsed case is a frozen input, not a verdict.

import (
	"os"
	"path/filepath"
	"testing"
)

func TestF13BCorpusLoadsThroughTheStrictDatasetParser(t *testing.T) {
	corpusDir := filepath.Join("..", "..", "tests", "f13b", "corpus")
	paths, err := filepath.Glob(filepath.Join(corpusDir, "cases.*.json"))
	if err != nil {
		t.Fatalf("glob corpus: %v", err)
	}
	if len(paths) != 7 {
		t.Fatalf("expected seven category documents, found %d: %v", len(paths), paths)
	}
	seenIDs := map[string]string{}
	splitsPerCategory := map[string]map[string]bool{}
	for _, path := range paths {
		document, err := os.ReadFile(path)
		if err != nil {
			t.Fatalf("read %s: %v", path, err)
		}
		dataset, err := ParseDataset(document)
		if err != nil {
			t.Fatalf("%s does not load through ParseDataset: %v", filepath.Base(path), err)
		}
		if len(dataset.Cases) == 0 {
			t.Fatalf("%s parsed zero cases", filepath.Base(path))
		}
		for _, parsed := range dataset.Cases {
			if previous, ok := seenIDs[parsed.CaseID]; ok {
				t.Fatalf("case id %q appears in %s and %s", parsed.CaseID, previous,
					filepath.Base(path))
			}
			seenIDs[parsed.CaseID] = filepath.Base(path)
			if len(parsed.Assertions) == 0 {
				t.Fatalf("case %q carries no assertion; an empty checker is not a case",
					parsed.CaseID)
			}
			if splitsPerCategory[parsed.Category] == nil {
				splitsPerCategory[parsed.Category] = map[string]bool{}
			}
			splitsPerCategory[parsed.Category][parsed.Split] = true
		}
	}
	if len(splitsPerCategory) != 7 {
		t.Fatalf("expected seven categories, found %d: %v", len(splitsPerCategory),
			splitsPerCategory)
	}
	for category, splits := range splitsPerCategory {
		for _, split := range []string{"dev", "frozen_test"} {
			if !splits[split] {
				t.Errorf("category %s has no %s case", category, split)
			}
		}
	}
}
