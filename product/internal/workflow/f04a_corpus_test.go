package workflow

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

// TestF04ACorpusParity pins the Go authority's verdict, reason code and pointer
// for every shared case of the F04A linear corpus. The Python defensive copy
// (worker/platform_worker/compiler/validate.py) asserts the same recorded
// expectations in product/tests/f04a/test_validate.py, so a change to either
// side that breaks parity fails a gate even though this repository has no
// cross-language test process.
func TestF04ACorpusParity(t *testing.T) {
	path, err := filepath.Abs(filepath.Join("..", "..", "tests", "f04a", "corpus", "linear_f04a.json"))
	if err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var payload struct {
		Cases []struct {
			Name   string `json:"name"`
			Scope  string `json:"scope"`
			Expect struct {
				Verdict  string `json:"verdict"`
				Problems []struct {
					Code    string `json:"code"`
					Pointer string `json:"pointer"`
				} `json:"problems"`
			} `json:"expect"`
			Document map[string]any `json:"document"`
		} `json:"cases"`
	}
	if err := json.Unmarshal(raw, &payload); err != nil {
		t.Fatal(err)
	}
	rules, err := DefaultRules()
	if err != nil {
		t.Fatal(err)
	}
	seen := 0
	for _, c := range payload.Cases {
		if c.Scope != "shared" {
			continue
		}
		seen++
		result, err := Validate(c.Document, rules, ValidationOptions{})
		if err != nil {
			t.Fatalf("%s: %v", c.Name, err)
		}
		if got := result.Verdict(); got != c.Expect.Verdict {
			t.Fatalf("%s: verdict = %q, want %q", c.Name, got, c.Expect.Verdict)
		}
		if len(result.Problems) != len(c.Expect.Problems) {
			t.Fatalf("%s: %d problems %v, want %d %v", c.Name, len(result.Problems),
				result.Problems, len(c.Expect.Problems), c.Expect.Problems)
		}
		for index, want := range c.Expect.Problems {
			got := result.Problems[index]
			if got.Code != want.Code || got.Pointer != want.Pointer {
				t.Fatalf("%s: problem %d = (%q,%q), want (%q,%q)", c.Name, index,
					got.Code, got.Pointer, want.Code, want.Pointer)
			}
		}
	}
	if seen == 0 {
		t.Fatal("the F04A corpus has no shared cases")
	}
	if seen < 10 {
		t.Fatalf("only %d shared F04A corpus cases; the corpus is expected to pin the linear subset", seen)
	}
}
