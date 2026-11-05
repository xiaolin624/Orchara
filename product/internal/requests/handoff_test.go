package requests

import (
	"database/sql"
	"strings"
	"testing"
	"time"
)

// F08/T082: the preview summary is metadata-only and bounded. These unit tests
// cover the generator's hard bound and its determinism; the end-to-end HTTP/DB
// behaviour lives in tests/f08/test_t082_handover.py.

func hoffRun(id, name string, versionNo int64, at time.Time) handoffRunFact {
	return handoffRunFact{
		RunID:           id,
		ApplicationID:   "00000000-0000-4000-8000-000000000001",
		ApplicationName: name,
		VersionID:       sql.NullString{String: id, Valid: true},
		VersionNo:       versionNo,
		CreatedAt:       at,
	}
}

func TestHandoffSummaryIsBoundedAndDeterministic(t *testing.T) {
	at := time.Date(2026, 11, 5, 10, 0, 0, 0, time.UTC)
	huge := strings.Repeat("长", 5000)
	runs := []handoffRunFact{
		hoffRun("aaaaaaaa-1111-4111-8111-111111111111", huge, 1, at),
		hoffRun("bbbbbbbb-2222-4222-8222-222222222222", huge, 2, at.Add(time.Second)),
		hoffRun("cccccccc-3333-4333-8333-333333333333", huge, 3, at.Add(2*time.Second)),
		hoffRun("dddddddd-4444-4444-8444-444444444444", huge, 4, at.Add(3*time.Second)),
		hoffRun("eeeeeeee-5555-4555-8555-555555555555", huge, 5, at.Add(4*time.Second)),
	}
	artifacts := make([]HandoffArtifact, 0, 5)
	for index := 0; index < 5; index++ {
		artifacts = append(artifacts, HandoffArtifact{
			ArtifactID: "ffffffff-6666-4666-8666-666666666666",
			Purpose:    strings.Repeat("purpose", 1000),
			MediaType:  strings.Repeat("type", 1000),
			SizeBytes:  int64(index),
			SHA256:     strings.Repeat("a", 64),
		})
	}
	first := buildHandoffSummary(runs, artifacts)
	second := buildHandoffSummary(runs, artifacts)
	if first != second {
		t.Fatal("the handover summary is not deterministic")
	}
	if len([]rune(first)) > MaxHandoffSummaryRunes {
		t.Fatalf("summary has %d runes, limit %d", len([]rune(first)), MaxHandoffSummaryRunes)
	}
	for _, wanted := range []string{"本次交接摘要", "来源 Run（5 个）", "附件（5 个）", "交接边界"} {
		if !strings.Contains(first, wanted) {
			t.Fatalf("summary is missing %q: %s", wanted, first)
		}
	}
	// The generator has no access to message/memory bytes: a summary built from
	// those facts cannot contain any marker that only exists in such a body.
	if strings.Contains(first, "OLD-INTERNAL-HISTORY-MARKER") ||
		strings.Contains(first, "OLD-PERSONAL-MEMORY-MARKER") {
		t.Fatalf("summary leaked a body marker: %s", first)
	}
}

func TestHandoffNewestVersionUsesTheNewestRun(t *testing.T) {
	at := time.Date(2026, 11, 5, 10, 0, 0, 0, time.UTC)
	facts := []handoffRunFact{
		hoffRun("aaaaaaaa-1111-4111-8111-111111111111", "old", 1, at),
		hoffRun("bbbbbbbb-2222-4222-8222-222222222222", "new", 2, at.Add(time.Minute)),
	}
	newest := handoffNewestVersion(facts)
	if newest == nil || *newest != "bbbbbbbb-2222-4222-8222-222222222222" {
		t.Fatalf("newest version = %v", newest)
	}
}
