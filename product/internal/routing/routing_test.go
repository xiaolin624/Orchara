package routing

import (
	"encoding/json"
	"strings"
	"testing"

	"orchara/platform/internal/catalog"
)

func TestMessageTokensKeepOnlyBoundedLiteralTerms(t *testing.T) {
	tokens := messageTokens("Please build a quarterly sales report %_\\ for version " +
		"11111111-2222-4333-8444-555555555555")
	for _, stop := range []string{"please", "a", "for", "the"} {
		for _, token := range tokens {
			if token == stop {
				t.Fatalf("stopword %q must not be a search token: %v", stop, tokens)
			}
		}
	}
	found := map[string]bool{}
	for _, token := range tokens {
		found[token] = true
		if strings.ContainsAny(token, "%_\\") {
			t.Fatalf("token %q was not literal", token)
		}
	}
	for _, wanted := range []string{"quarterly", "sales", "report", "version"} {
		if !found[wanted] {
			t.Fatalf("missing token %q in %v", wanted, tokens)
		}
	}
	// The UUID is split by its hyphens; it is not a wildcard or a pattern.
	if !found["11111111"] {
		t.Fatalf("the UUID segment should be a literal token: %v", tokens)
	}
	if len(tokens) > 40 {
		t.Fatalf("token list is unbounded: %d", len(tokens))
	}
	if tokens := messageTokens("   "); tokens != nil {
		t.Fatalf("empty text must produce no tokens: %v", tokens)
	}

	// A Chinese sentence contributes its 2/3-grams so a Chinese card can match
	// without a segmenter.
	cjk := messageTokens("帮我做销售报表")
	cjkSeen := map[string]bool{}
	for _, token := range cjk {
		cjkSeen[token] = true
	}
	for _, wanted := range []string{"销售", "报表", "销售报"} {
		if !cjkSeen[wanted] {
			t.Fatalf("missing CJK n-gram %q in %v", wanted, cjk)
		}
	}
}

func TestMissingInputsCountsOneAttachmentPerRequiredInput(t *testing.T) {
	required := []string{"sales csv", "region mapping", "currency table"}
	if got := missingInputs(required, nil); len(got) != 3 || got[0] != "sales csv" {
		t.Fatalf("all inputs should be missing: %v", got)
	}
	if got := missingInputs(required, []string{"a", "b"}); len(got) != 1 || got[0] != "currency table" {
		t.Fatalf("one input should be missing: %v", got)
	}
	if got := missingInputs(required, []string{"a", "b", "c"}); got != nil {
		t.Fatalf("satisfied inputs must be nil: %v", got)
	}
	if got := missingInputs(nil, nil); got != nil {
		t.Fatalf("no required inputs must be nil: %v", got)
	}
	// Duplicate attachments never count as two distinct inputs.
	if got := missingInputs(required, []string{"a", "a"}); len(got) != 2 {
		t.Fatalf("duplicate attachments must not satisfy two inputs: %v", got)
	}
}

func TestParseDecisionAcceptsOnlyTheThreeDocumentedShapes(t *testing.T) {
	selected := "11111111-2222-4333-8444-555555555555"
	decision, err := parseDecision(map[string]any{
		"decision": "select", "candidate_version_id": selected,
	})
	if err != nil || decision.Decision != DecisionSelect ||
		decision.CandidateVersionID != selected {
		t.Fatalf("select: %v %+v", err, decision)
	}

	decision, err = parseDecision(map[string]any{
		"decision": "clarify", "question": "需要哪个月的销售数据？",
		"missing_fields": []any{"month", "currency"},
	})
	if err != nil || decision.Question == "" || len(decision.MissingFields) != 2 {
		t.Fatalf("clarify: %v %+v", err, decision)
	}

	decision, err = parseDecision(map[string]any{
		"decision": "no_match", "reason": "no application can do this",
	})
	if err != nil || decision.Reason == "" {
		t.Fatalf("no_match: %v %+v", err, decision)
	}

	for _, broken := range []map[string]any{
		nil,
		{},
		{"decision": "choose", "candidate_version_id": selected},
		{"decision": "select"},
		{"decision": "select", "candidate_version_id": "not-a-uuid"},
		{"decision": "clarify"},
		{"decision": "clarify", "question": "   "},
		{"decision": "clarify", "question": "q", "missing_fields": "not-an-array"},
		{"decision": "clarify", "question": "q", "missing_fields": []any{1}},
		{"decision": "no_match"},
		{"decision": "no_match", "reason": "  "},
	} {
		if _, err := parseDecision(broken); err == nil {
			t.Fatalf("decision %v should have been refused", broken)
		}
	}
}

func TestProposalsCarryOrdinalRanksAndNoScores(t *testing.T) {
	card := json.RawMessage(`{"purpose":"sales report","includes":["aggregation"],` +
		`"excludes":["writes"],"required_inputs":["sales csv"],` +
		`"expected_outputs":["report"],"examples":["monthly report","weekly digest"]}`)
	candidates := []Candidate{
		{VersionID: "11111111-2222-4333-8444-555555555555",
			ApplicationID:   "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
			ApplicationName: "alpha", VersionNo: 2, Card: card, Rank: 1},
		{VersionID: "66666666-7777-4888-9999-aaaaaaaaaaaa",
			ApplicationID:   "bbbbbbbb-cccc-4ddd-8eee-ffffffffffff",
			ApplicationName: "beta", VersionNo: 1, Card: card, Rank: 2},
	}
	raw, err := selectionProposalJSON(candidates)
	if err != nil {
		t.Fatal(err)
	}
	var proposal struct {
		Kind       string              `json:"kind"`
		Candidates []proposalCandidate `json:"candidates"`
	}
	if err := json.Unmarshal(raw, &proposal); err != nil {
		t.Fatal(err)
	}
	if proposal.Kind != "selection" || len(proposal.Candidates) != 2 {
		t.Fatalf("unexpected proposal: %s", raw)
	}
	if proposal.Candidates[0].Rank != 1 || proposal.Candidates[1].Rank != 2 {
		t.Fatalf("ranks are not ordinals: %s", raw)
	}
	text := strings.ToLower(string(raw))
	for _, forbidden := range []string{"score", "probability", "confidence"} {
		if strings.Contains(text, forbidden) {
			t.Fatalf("proposal leaks %q: %s", forbidden, raw)
		}
	}

	prompt, err := buildPrompt("please build a sales report", candidates)
	if err != nil {
		t.Fatal(err)
	}
	var payload promptPayload
	if err := json.Unmarshal(prompt, &payload); err != nil {
		t.Fatal(err)
	}
	if payload.Task != "route" || len(payload.Candidates) != 2 ||
		payload.Candidates[0].Rank != 1 || payload.Candidates[1].Rank != 2 {
		t.Fatalf("unexpected prompt: %s", prompt)
	}
	if strings.Contains(strings.ToLower(string(prompt)), "score") {
		t.Fatalf("prompt leaks a score: %s", prompt)
	}
}

func TestVersionRefusalCodesAreStable(t *testing.T) {
	cases := map[error]string{
		catalog.ErrVersionRevoked:  "version_revoked",
		catalog.ErrVersionArchived: "version_archived",
		catalog.ErrNotVisible:      "version_unavailable",
	}
	for input, wanted := range cases {
		code, ok := versionRefusalCode(input)
		if !ok || code != wanted {
			t.Fatalf("%v -> %q,%v (want %q)", input, code, ok, wanted)
		}
	}
	if _, ok := versionRefusalCode(errNotAVersion); ok {
		t.Fatal("an unknown error must not be mapped to a version code")
	}
}

var errNotAVersion = &plainError{"not a version refusal"}

type plainError struct{ message string }

func (e *plainError) Error() string { return e.message }
