package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

// vector mirrors the shared golden-vector file used by the Python suite.
type vector struct {
	Name              string  `json:"name"`
	InputJSON         string  `json:"input_json"`
	ExpectedCanonical *string `json:"expected_canonical"`
	ExpectedSHA256    *string `json:"expected_sha256"`
	Error             *string `json:"error"`
}

func loadVectors(t *testing.T) []vector {
	t.Helper()
	// go test runs with the package directory (`go/`) as the working directory.
	path := filepath.Join("..", "vectors.json")
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("cannot read %s: %v", path, err)
	}
	var vectors []vector
	if err := json.Unmarshal(raw, &vectors); err != nil {
		t.Fatalf("cannot parse %s: %v", path, err)
	}
	return vectors
}

func TestCanonicalizeMatchesGoldenVectors(t *testing.T) {
	for _, tc := range loadVectors(t) {
		tc := tc
		t.Run(tc.Name, func(t *testing.T) {
			canonical, err := CanonicalizeBytes([]byte(tc.InputJSON))
			if tc.ExpectedCanonical == nil {
				if err == nil {
					t.Fatalf("expected rejection, got %s", canonical)
				}
				jcsErr, ok := err.(*JCSError)
				if !ok {
					t.Fatalf("expected *JCSError, got %T", err)
				}
				if tc.Error != nil && jcsErr.Code != *tc.Error {
					t.Fatalf("error code = %q, want %q", jcsErr.Code, *tc.Error)
				}
				return
			}
			if err != nil {
				t.Fatalf("unexpected error: %v", err)
			}
			if string(canonical) != *tc.ExpectedCanonical {
				t.Fatalf("canonical = %q, want %q", canonical, *tc.ExpectedCanonical)
			}
			// Matching the RFC-derived digest is what makes a shared bug fail.
			if tc.ExpectedSHA256 != nil {
				if got := SHA256Hex(canonical); got != *tc.ExpectedSHA256 {
					t.Fatalf("sha256 = %s, want %s", got, *tc.ExpectedSHA256)
				}
			}
		})
	}
}

func TestRFC8785SampleDigest(t *testing.T) {
	vectors := loadVectors(t)
	for _, tc := range vectors {
		if tc.Name != "rfc8785-3.2.2-sample" {
			continue
		}
		canonical, err := CanonicalizeBytes([]byte(tc.InputJSON))
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		const want = "2d5e01a318d0f0879ab568c4be289c8b1f64ef8921a53c6277d5e069978baacb"
		if got := SHA256Hex(canonical); got != want {
			t.Fatalf("sample digest = %s, want %s", got, want)
		}
		return
	}
	t.Fatal("rfc8785-3.2.2-sample vector is missing")
}
