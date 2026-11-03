package toolversions

import (
	"encoding/json"
	"strings"
	"testing"

	"orchara/platform/internal/jcs"
)

func TestToolManifestIdentityIsContentAddressed(t *testing.T) {
	inputSchema := json.RawMessage(`{"type":"object","properties":{"b":{"type":"string"},"a":{"type":"number"}}}`)
	outputSchema := json.RawMessage(`{"type":"object"}`)
	lock := json.RawMessage(`{"requirements":[],"python":"3.12"}`)
	lockCanonical, err := canonicalObjectBytes("dependency_lock", lock)
	if err != nil {
		t.Fatalf("canonical lock: %v", err)
	}
	lockSHA := jcs.SHA256Hex(lockCanonical)

	hash, payload, err := toolManifestSHA("a"+strings.Repeat("b", 63), inputSchema, outputSchema, lockCanonical, lockSHA, "pure", nil)
	if err != nil {
		t.Fatalf("manifest hash: %v", err)
	}
	if len(hash) != 64 || !isSHA256Hex(hash) {
		t.Fatalf("hash = %q", hash)
	}
	if !strings.Contains(string(payload), `"connector_version_ids":[]`) {
		t.Fatalf("the manifest does not pin the empty connector set: %s", payload)
	}
	if !strings.Contains(string(payload), `"tool_manifest_version":"1.0"`) {
		t.Fatalf("the manifest version is missing: %s", payload)
	}

	same, _, err := toolManifestSHA("a"+strings.Repeat("b", 63), inputSchema, outputSchema, lockCanonical, lockSHA, "pure", nil)
	if err != nil || same != hash {
		t.Fatalf("the same content produced %q vs %q (%v)", hash, same, err)
	}
	// Key order inside a schema is canonicalised and must not change the identity.
	reordered := json.RawMessage(`{"properties":{"a":{"type":"number"},"b":{"type":"string"}},"type":"object"}`)
	reorderedCanonical, err := canonicalObjectBytes("input_schema", reordered)
	if err != nil {
		t.Fatal(err)
	}
	reorderedHash, _, _ := toolManifestSHA("a"+strings.Repeat("b", 63), reorderedCanonical, outputSchema, lockCanonical, lockSHA, "pure", nil)
	if reorderedHash != hash {
		t.Fatalf("reordered schema changed the content identity: %q vs %q", hash, reorderedHash)
	}

	changedRisk, _, _ := toolManifestSHA("a"+strings.Repeat("b", 63), inputSchema, outputSchema, lockCanonical, lockSHA, "write", nil)
	if changedRisk == hash {
		t.Fatal("risk_class did not change the content identity")
	}
	changedSource, _, _ := toolManifestSHA("c"+strings.Repeat("d", 63), inputSchema, outputSchema, lockCanonical, lockSHA, "pure", nil)
	if changedSource == hash {
		t.Fatal("source hash did not change the content identity")
	}
	changedLock, _, _ := toolManifestSHA(
		"a"+strings.Repeat("b", 63), inputSchema, outputSchema,
		[]byte(`{"requirements":["x"]}`), jcs.SHA256Hex([]byte(`{"requirements":["x"]}`)), "pure", nil,
	)
	if changedLock == hash {
		t.Fatal("dependency lock did not change the content identity")
	}
}

func TestCanonicalObjectsRejectNonObjectsAndDuplicateKeys(t *testing.T) {
	cases := map[string]json.RawMessage{
		"missing":       nil,
		"array":         json.RawMessage(`[]`),
		"string":        json.RawMessage(`"x"`),
		"null":          json.RawMessage(`null`),
		"duplicate key": json.RawMessage(`{"a":1,"a":2}`),
		"invalid":       json.RawMessage(`{"a":`),
	}
	for name, raw := range cases {
		if _, err := canonicalObjectBytes("input_schema", raw); err == nil {
			t.Fatalf("%s was accepted as a schema/lock object", name)
		}
	}
}
