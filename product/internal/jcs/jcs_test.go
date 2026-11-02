package jcs

import (
	"encoding/json"
	"errors"
	"os"
	"testing"
)

// TestRFC8785VectorsReplayTheT003Corpus replays probes/jcs/vectors.json through
// this package.
//
// The vectors are the RFC-derived golden set that P01/T003 made the contract
// between the Go and Python implementations. Replaying them here is what makes
// the copied serialiser a *port* rather than a similar-looking function: every
// canonical string and SHA-256 is compared with the pinned value, and every
// rejection is compared with the pinned machine-readable code.
func TestRFC8785VectorsReplayTheT003Corpus(t *testing.T) {
	raw, err := os.ReadFile("../../probes/jcs/vectors.json")
	if err != nil {
		t.Fatalf("read the shared vectors: %v", err)
	}
	var vectors []struct {
		Name              string  `json:"name"`
		InputJSON         string  `json:"input_json"`
		ExpectedCanonical string  `json:"expected_canonical"`
		ExpectedSHA256    string  `json:"expected_sha256"`
		Error             *string `json:"error"`
	}
	if err := json.Unmarshal(raw, &vectors); err != nil {
		t.Fatalf("parse the shared vectors: %v", err)
	}
	if len(vectors) == 0 {
		t.Fatal("the shared vector file has no cases")
	}

	for _, vector := range vectors {
		vector := vector
		t.Run(vector.Name, func(t *testing.T) {
			canonical, err := CanonicalizeBytes([]byte(vector.InputJSON))
			if vector.Error != nil {
				if err == nil {
					t.Fatalf("expected rejection %q, got canonical %s", *vector.Error, canonical)
				}
				var jcsErr *JCSError
				if !errors.As(err, &jcsErr) || jcsErr.Code != *vector.Error {
					t.Fatalf("expected error code %q, got %v", *vector.Error, err)
				}
				return
			}
			if err != nil {
				t.Fatalf("canonicalize: %v", err)
			}
			if string(canonical) != vector.ExpectedCanonical {
				t.Fatalf("canonical text differs:\n got: %s\nwant: %s", canonical, vector.ExpectedCanonical)
			}
			if got := SHA256Hex(canonical); got != vector.ExpectedSHA256 {
				t.Fatalf("SHA-256 differs: got %s, want %s", got, vector.ExpectedSHA256)
			}
		})
	}
}
