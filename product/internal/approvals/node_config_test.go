package approvals

// Review 2 / finding 2: the node approval's human-facing title and expiry come
// from the sealed definition, never from the worker request.

import (
	"encoding/json"
	"testing"
)

func TestApprovalNodeSettingsComeFromTheSealedDefinition(t *testing.T) {
	nodes := []nodeDefinitionNode{{
		ID:   "approve1",
		Type: "approval",
		Config: json.RawMessage(
			`{"title":"Approve the batch","expires_seconds":600}`),
	}}
	title, expires, err := approvalNodeSettings(nodes, "approve1")
	if err != nil {
		t.Fatalf("approvalNodeSettings: %v", err)
	}
	if title != "Approve the batch" || expires != 600 {
		t.Fatalf("settings = %q/%d", title, expires)
	}
}

func TestApprovalNodeSettingsRefuseAnInvalidSealedConfig(t *testing.T) {
	cases := map[string]string{
		"empty title":     `{"title":"  ","expires_seconds":600}`,
		"long title":      `{"title":"` + longTitle() + `","expires_seconds":600}`,
		"short expiry":    `{"title":"ok","expires_seconds":59}`,
		"null config":     `null`,
		"non-object json": `[]`,
	}
	for name, raw := range cases {
		nodes := []nodeDefinitionNode{{
			ID: "approve1", Type: "approval", Config: json.RawMessage(raw),
		}}
		if _, _, err := approvalNodeSettings(nodes, "approve1"); err == nil {
			t.Fatalf("%s: invalid sealed config was accepted", name)
		}
	}
}

func TestApprovalNodeSettingsRequireTheNodeInTheSealedDefinition(t *testing.T) {
	nodes := []nodeDefinitionNode{{
		ID: "start", Type: "start", Config: json.RawMessage(`{}`),
	}}
	if _, _, err := approvalNodeSettings(nodes, "approve1"); err == nil {
		t.Fatal("a node outside the sealed definition must be refused")
	}
}

func longTitle() string {
	runes := make([]rune, MaxNodeTitleRunes+1)
	for index := range runes {
		runes[index] = 'x'
	}
	return string(runes)
}
