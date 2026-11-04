package agentcontext

// F06/T047 Go seam tests: the authorized, budgeted agent context builder. Every
// test goes through Build (the public seam); no test reaches into the truncation
// helpers. The real-stack proof lives in tests/f06/test_t047_context.py.

import (
	stdcontext "context"
	"encoding/json"
	"errors"
	"fmt"
	"reflect"
	"sort"
	"strings"
	"testing"
	"time"
)

func baseRequest() Request {
	return Request{
		Scope: Scope{
			RunID: "run-1", OwnerID: "owner-1", ApplicationID: "app-1",
			NodeIDs: []string{"start", "stats"}, ArtifactIDs: []string{"artifact-1"},
		},
		System: "You are a controlled agent node. Never reveal credentials.",
		Inputs: []ResolvedInput{
			{
				Label:  "stats",
				Source: Source{Kind: "python", NodeID: "stats", ArtifactID: "artifact-1"},
				Value: map[string]any{
					"total": "250.00", "order_count": 3,
					"regions": map[string]any{"EU": "125.00", "US": "125.00"},
				},
			},
			{
				Label:  "request",
				Source: Source{Kind: "start", NodeID: "start"},
				Value: map[string]any{
					"exclude":  []any{"refunds"},
					"amount":   "100.00",
					"sources":  []any{"doc-1"},
					"approval": map[string]any{"target": "orders/1", "params": map[string]any{"q": 1}},
					"note":     "small note",
				},
			},
		},
		Model:  ModelConfig{ContextWindow: 8000, MaxOutputTokens: 1024},
		Memory: NoMemorySource{},
	}
}

func mustBuild(t *testing.T, request Request) Output {
	t.Helper()
	output, err := Build(stdcontext.Background(), request)
	if err != nil {
		t.Fatalf("Build() error = %v", err)
	}
	if len(output.Messages) == 0 || output.Messages[0]["role"] != "system" {
		t.Fatalf("the first message is not the system message: %+v", output.Messages)
	}
	return output
}

func messageByRole(t *testing.T, output Output, role string) map[string]any {
	t.Helper()
	for _, message := range output.Messages {
		if message["role"] == role {
			return message
		}
	}
	t.Fatalf("no %s message in %+v", role, output.Messages)
	return nil
}

func containsString(values []string, want string) bool {
	for _, value := range values {
		if value == want {
			return true
		}
	}
	return false
}

func truncationReasons(trace Trace) []string {
	reasons := make([]string, 0, len(trace.Truncations))
	for _, truncation := range trace.Truncations {
		reasons = append(reasons, truncation.Reason)
	}
	return reasons
}

func TestEstimateTokensIsTheDocumentedDeterministicHeuristic(t *testing.T) {
	cases := map[string]int{
		"": 0, "a": 1, "abcd": 1, "abcde": 2,
		"\u65e5\u672c\u8a9e": 3, // nine UTF-8 bytes, ceil(9/4)
	}
	for text, want := range cases {
		if got := EstimateTokens(text); got != want {
			t.Fatalf("EstimateTokens(%q) = %d, want %d", text, got, want)
		}
	}
}

func TestAuthorizedSourcesOnly(t *testing.T) {
	request := baseRequest()
	request.Inputs = append(request.Inputs, ResolvedInput{
		Label:  "foreign",
		Source: Source{Kind: "python", NodeID: "other-run-node"},
		Value:  map[string]any{"amount": "999.00"},
	})
	_, err := Build(stdcontext.Background(), request)
	var typed *Error
	if !errors.As(err, &typed) || typed.Code != CodeSourceUnauthorized {
		t.Fatalf("foreign node source error = %v, want %s", err, CodeSourceUnauthorized)
	}

	request = baseRequest()
	request.Inputs[0].Source.ArtifactID = "artifact-from-another-run"
	_, err = Build(stdcontext.Background(), request)
	if !errors.As(err, &typed) || typed.Code != CodeSourceUnauthorized {
		t.Fatalf("foreign artifact source error = %v, want %s", err, CodeSourceUnauthorized)
	}

	// A literal is authorized only when it names no node or artifact.
	request = baseRequest()
	request.Inputs = append(request.Inputs, ResolvedInput{
		Label: "literal", Source: Source{Kind: "literal"}, Value: "creator constant",
	})
	if _, err := Build(stdcontext.Background(), request); err != nil {
		t.Fatalf("literal input refused: %v", err)
	}
	request.Inputs[len(request.Inputs)-1].Source.NodeID = "stats"
	if _, err := Build(stdcontext.Background(), request); err == nil {
		t.Fatal("a literal naming a node was accepted")
	}
}

func TestTraceShapeNamesSourcesAndBudgetWithoutContent(t *testing.T) {
	output := mustBuild(t, baseRequest())
	trace := output.Trace
	if trace.BudgetTokens != 6976 || trace.ReservedOutput != 1024 {
		t.Fatalf("budget = %d reserved = %d, want 6976/1024",
			trace.BudgetTokens, trace.ReservedOutput)
	}
	if trace.EstimatedTokens <= 0 || trace.EstimatedTokens > trace.BudgetTokens {
		t.Fatalf("estimated tokens = %d, budget = %d", trace.EstimatedTokens, trace.BudgetTokens)
	}
	if trace.MemoryState != MemoryNotRun {
		t.Fatalf("memory state = %q, want not_run", trace.MemoryState)
	}
	if len(trace.Truncations) != 0 {
		t.Fatalf("a small context was truncated: %+v", trace.Truncations)
	}
	kinds := map[string]bool{}
	for _, source := range trace.Sources {
		kinds[source.Kind] = true
		if source.NodeID == "" && source.Kind != "literal" && source.Kind != "memory" {
			t.Fatalf("source has no node id: %+v", source)
		}
	}
	if !kinds["python"] || !kinds["start"] {
		t.Fatalf("source kinds = %v, want python and start", kinds)
	}
	if !sort.StringsAreSorted(trace.Protected) {
		t.Fatalf("protected paths are not sorted: %v", trace.Protected)
	}
	for _, path := range []string{
		"stats", "request.amount", "request.approval", "request.exclude", "request.sources",
	} {
		if !containsString(trace.Protected, path) {
			t.Fatalf("protected paths %v miss %q", trace.Protected, path)
		}
	}
	// The trace is metadata only: no message text and no raw values.
	encoded, err := json.Marshal(trace)
	if err != nil {
		t.Fatalf("encode trace: %v", err)
	}
	if strings.Contains(string(encoded), "250.00") || strings.Contains(string(encoded), "100.00") {
		t.Fatalf("the trace leaks a raw amount: %s", encoded)
	}
}

func TestBuildIsDeterministic(t *testing.T) {
	first := mustBuild(t, baseRequest())
	second := mustBuild(t, baseRequest())
	if !reflect.DeepEqual(first.Messages, second.Messages) {
		t.Fatalf("two builds differ:\n%+v\n%+v", first.Messages, second.Messages)
	}
	if !reflect.DeepEqual(first.Trace, second.Trace) {
		t.Fatalf("two traces differ:\n%+v\n%+v", first.Trace, second.Trace)
	}
}

func TestOverLimitOrderAndProtectedFactsSurvive(t *testing.T) {
	request := baseRequest()
	request.Model = ModelConfig{ContextWindow: 3072, MaxOutputTokens: 1024}
	request.Inputs = append(request.Inputs, ResolvedInput{
		Label:  "verbose",
		Source: Source{Kind: "python", NodeID: "stats"},
		Value:  strings.Repeat("verbatim upstream output ", 2000),
	})
	request.History = []HistoryMessage{
		{Role: "assistant", Relevance: 0, Content: strings.Repeat("old answer ", 300)},
		{Role: "assistant", Relevance: 1, Content: strings.Repeat("newer answer ", 300)},
		{
			Role: "tool", Relevance: 3,
			Source: Source{Kind: "tool", NodeID: "analysis"},
			Content: map[string]any{
				"total": "250.00", "sources": []any{"doc-1"},
				"detail": strings.Repeat("tool detail ", 500),
			},
		},
	}
	output := mustBuild(t, request)
	reasons := truncationReasons(output.Trace)
	if len(reasons) == 0 {
		t.Fatal("the over-limit build recorded no truncation")
	}
	position := map[string]int{}
	for index, reason := range reasons {
		if _, seen := position[reason]; !seen {
			position[reason] = index
		}
	}
	first := reasons[0]
	if first != ReasonLowRelevanceHistory && first != ReasonDisplayOnlySummary {
		t.Fatalf("first truncation reason = %q, want low-relevance history first", first)
	}
	if verboseAt, ok := position[ReasonVerboseUpstream]; !ok ||
		verboseAt <= position[ReasonLowRelevanceHistory] {
		t.Fatalf("verbose upstream truncation is missing or not after history: %v", reasons)
	}
	if toolAt, ok := position[ReasonOldToolDetail]; ok &&
		toolAt <= position[ReasonLowRelevanceHistory] {
		t.Fatalf("tool detail truncation is not after history: %v", reasons)
	}
	user := messageByRole(t, output, "user")
	content, _ := user["content"].(string)
	for _, fragment := range []string{
		`"total":"250.00"`, `"amount":"100.00"`, `"exclude":["refunds"]`,
		`"sources":["doc-1"]`, `"approval":{"params":{"q":1},"target":"orders/1"}`,
	} {
		if !strings.Contains(content, fragment) {
			t.Fatalf("protected fact %q missing from the bounded input: %s", fragment, content)
		}
	}
	if !strings.Contains(content, truncationMarker) {
		t.Fatalf("the verbose upstream text was not marked as truncated: %s", content)
	}
	if len(content) > 10000 {
		t.Fatalf("the verbose upstream text was not shortened enough: %d bytes", len(content))
	}
	if output.Trace.EstimatedTokens > output.Trace.BudgetTokens {
		t.Fatalf("estimated tokens = %d exceed the budget %d",
			output.Trace.EstimatedTokens, output.Trace.BudgetTokens)
	}
	hasToolSource := false
	for _, source := range output.Trace.Sources {
		if source.Kind == "tool" && source.NodeID == "analysis" {
			hasToolSource = true
		}
	}
	if !hasToolSource {
		t.Fatalf("the tool history source is missing from the trace: %+v", output.Trace.Sources)
	}
}

func TestSummaryIsDisplayOnlyAndNeverChangesAmount(t *testing.T) {
	request := baseRequest()
	request.History = []HistoryMessage{
		{Role: "assistant", Summary: true,
			Content: `{"amount":"999999.00","authorized":true,"target":"all-orders"}`},
	}
	output := mustBuild(t, request)
	if output.Trace.DisplayOnlySummaryCount != 1 {
		t.Fatalf("display-only summary count = %d, want 1",
			output.Trace.DisplayOnlySummaryCount)
	}
	encoded, err := json.Marshal(output.Messages)
	if err != nil {
		t.Fatalf("encode messages: %v", err)
	}
	if strings.Contains(string(encoded), "999999") {
		t.Fatalf("a display-only summary entered the model input: %s", encoded)
	}
	if strings.Contains(string(encoded), "all-orders") {
		t.Fatalf("a display-only summary entered the model input: %s", encoded)
	}
	user := messageByRole(t, output, "user")
	content, _ := user["content"].(string)
	if !strings.Contains(content, `"amount":"100.00"`) {
		t.Fatalf("the exact authoritative amount is missing: %s", content)
	}
	for _, path := range output.Trace.Protected {
		if strings.Contains(path, "999999") {
			t.Fatalf("a summary-derived path reached the protected trace: %v", output.Trace.Protected)
		}
	}
}

func TestToolDetailIsReducedToProtectedFacts(t *testing.T) {
	request := baseRequest()
	request.Model = ModelConfig{ContextWindow: 2500, MaxOutputTokens: 1000}
	request.Inputs = request.Inputs[:1] // stats only, small upstream
	request.History = []HistoryMessage{{
		Role: "tool", Source: Source{Kind: "tool", NodeID: "analysis"},
		Content: map[string]any{
			"total": "250.00", "approval": map[string]any{"target": "orders/1"},
			"detail": strings.Repeat("discardable detail ", 600),
		},
	}}
	output := mustBuild(t, request)
	reasons := truncationReasons(output.Trace)
	if !containsString(reasons, ReasonOldToolDetail) {
		t.Fatalf("truncation reasons = %v, want %s", reasons, ReasonOldToolDetail)
	}
	tool := messageByRole(t, output, "tool")
	content, _ := tool["content"].(string)
	var value map[string]any
	if err := json.Unmarshal([]byte(content), &value); err != nil {
		t.Fatalf("the reduced tool message is not JSON: %v", err)
	}
	if value["total"] != "250.00" {
		t.Fatalf("the protected total was lost: %v", value)
	}
	if _, present := value["detail"]; present {
		t.Fatalf("the discardable tool detail survived: %v", value)
	}
}

func TestProtectedMinimumOverBudgetIsATypedRefusal(t *testing.T) {
	request := baseRequest()
	request.System = strings.Repeat("platform constraint ", 1000)
	request.Model = ModelConfig{ContextWindow: 2000, MaxOutputTokens: 500}
	_, err := Build(stdcontext.Background(), request)
	var typed *Error
	if !errors.As(err, &typed) || typed.Code != CodeBudgetExhausted {
		t.Fatalf("error = %v, want %s", err, CodeBudgetExhausted)
	}
}

func TestInvalidModelConfigAndToolResult(t *testing.T) {
	for _, model := range []ModelConfig{
		{ContextWindow: 0, MaxOutputTokens: 1},
		{ContextWindow: 10, MaxOutputTokens: 0},
		{ContextWindow: 10, MaxOutputTokens: 10},
	} {
		request := baseRequest()
		request.Model = model
		_, err := Build(stdcontext.Background(), request)
		var typed *Error
		if !errors.As(err, &typed) || typed.Code != CodeInvalid {
			t.Fatalf("model %+v error = %v, want %s", model, err, CodeInvalid)
		}
	}
	request := baseRequest()
	request.History = []HistoryMessage{{
		Role: "tool", Source: Source{Kind: "tool", NodeID: "analysis"},
		Content: "not a json object",
	}}
	_, err := Build(stdcontext.Background(), request)
	var typed *Error
	if !errors.As(err, &typed) || typed.Code != CodeInvalid {
		t.Fatalf("non-JSON tool result error = %v, want %s", err, CodeInvalid)
	}
}

// fakeMemorySource is the F09 seam in tests. The production source is
// NoMemorySource, which reads nothing.
type fakeMemorySource struct {
	rows []Memory
	err  error
}

func (f fakeMemorySource) ConfirmedMemories(stdcontext.Context, string, string) ([]Memory, error) {
	return f.rows, f.err
}

func TestConfirmedMemoryIsIncludedOnlyForTheSameOwnerAndApplication(t *testing.T) {
	request := baseRequest()
	request.Memory = fakeMemorySource{rows: []Memory{{
		ID: "m1", OwnerID: "owner-1", ApplicationID: "app-1",
		State: "active", Confirmed: true, Text: "prefers EU totals",
	}}}
	output := mustBuild(t, request)
	if output.Trace.MemoryState != MemoryIncluded {
		t.Fatalf("memory state = %q, want included", output.Trace.MemoryState)
	}
	encoded, _ := json.Marshal(output.Messages)
	content := string(encoded)
	if !strings.Contains(content, "confirmed_memory") ||
		!strings.Contains(content, "prefers EU totals") {
		t.Fatalf("the confirmed memory is missing: %s", content)
	}

	// Another owner's row is refused, never silently filtered into a partial view.
	request = baseRequest()
	request.Memory = fakeMemorySource{rows: []Memory{{
		ID: "m2", OwnerID: "owner-2", ApplicationID: "app-1",
		State: "active", Confirmed: true, Text: "foreign memory",
	}}}
	_, err := Build(stdcontext.Background(), request)
	var typed *Error
	if !errors.As(err, &typed) || typed.Code != CodeSourceUnauthorized {
		t.Fatalf("foreign memory error = %v, want %s", err, CodeSourceUnauthorized)
	}

	// An unconfirmed or inactive row is never included.
	request = baseRequest()
	request.Memory = fakeMemorySource{rows: []Memory{{
		ID: "m3", OwnerID: "owner-1", ApplicationID: "app-1",
		State: "pending", Confirmed: false, Text: "unconfirmed",
	}}}
	output = mustBuild(t, request)
	if output.Trace.MemoryState != MemoryEmpty {
		t.Fatalf("memory state = %q, want empty", output.Trace.MemoryState)
	}

	// F09 storage absence is documented, not simulated.
	request = baseRequest()
	request.Memory = NoMemorySource{}
	output = mustBuild(t, request)
	if output.Trace.MemoryState != MemoryNotRun {
		t.Fatalf("memory state = %q, want not_run", output.Trace.MemoryState)
	}
	encoded, _ = json.Marshal(output.Messages)
	if strings.Contains(string(encoded), "confirmed_memory") {
		t.Fatalf("absent memory produced a memory message: %s", encoded)
	}

	// A memory backend error fails closed: no memory, trace says unavailable.
	request = baseRequest()
	request.Memory = fakeMemorySource{err: errors.New("memory store down")}
	output = mustBuild(t, request)
	if output.Trace.MemoryState != MemoryUnavailable {
		t.Fatalf("memory state = %q, want unavailable", output.Trace.MemoryState)
	}
}

func TestHistoryWithoutProtectedFactsIsDroppedBeforeTheInputIsTouched(t *testing.T) {
	request := baseRequest()
	request.Model = ModelConfig{ContextWindow: 3000, MaxOutputTokens: 1000}
	request.Inputs = request.Inputs[:1]
	request.History = []HistoryMessage{
		{Role: "assistant", Relevance: 0, Content: strings.Repeat("unimportant history ", 400)},
	}
	output := mustBuild(t, request)
	reasons := truncationReasons(output.Trace)
	if len(reasons) != 1 || reasons[0] != ReasonLowRelevanceHistory {
		t.Fatalf("truncation reasons = %v, want only %s", reasons, ReasonLowRelevanceHistory)
	}
	if len(output.Messages) != 2 {
		t.Fatalf("the dropped history message is still present: %+v", output.Messages)
	}
}

// ---------------------------------------------------------------------------
// F09/T063: confirmed personal memory inside the deterministic budget.
// ---------------------------------------------------------------------------

func memoryRowsForBudget() []Memory {
	base := time.Date(2026, 11, 4, 10, 0, 0, 0, time.UTC)
	rows := make([]Memory, 0, 5)
	for index := 1; index <= 5; index++ {
		rows = append(rows, Memory{
			ID: fmt.Sprintf("m%d", index), OwnerID: "owner-1", ApplicationID: "app-1",
			State: "active", Confirmed: true, Revision: int64(index),
			ConfirmedAt: base.Add(time.Duration(index) * time.Hour),
			Text:        fmt.Sprintf("memory-%d ", index) + strings.Repeat("value ", 200),
		})
	}
	return rows
}

func memorySourceIDs(trace Trace) []string {
	ids := make([]string, 0, 4)
	for _, source := range trace.Sources {
		if source.Kind == "memory" {
			ids = append(ids, source.MemoryID)
		}
	}
	return ids
}

func memoryTruncationIDs(trace Trace) []string {
	ids := make([]string, 0, 4)
	for _, truncation := range trace.Truncations {
		if truncation.Reason == ReasonOptionalMemory {
			ids = append(ids, truncation.MemoryID)
		}
	}
	return ids
}

func TestMemorySelectionIsBudgetedDeterministicAndDropsLowestRelevanceFirst(t *testing.T) {
	request := baseRequest()
	request.Model = ModelConfig{ContextWindow: 4096, MaxOutputTokens: 1024} // budget 3072
	request.Memory = fakeMemorySource{rows: memoryRowsForBudget()}
	output := mustBuild(t, request)

	if output.Trace.MemoryState != MemoryIncluded {
		t.Fatalf("memory state = %q, want included", output.Trace.MemoryState)
	}
	if output.Trace.MemoryBudgetTokens != 768 {
		t.Fatalf("memory budget = %d, want 768 (a quarter of 3072)",
			output.Trace.MemoryBudgetTokens)
	}
	if output.Trace.MemoryTokens <= 0 || output.Trace.MemoryTokens > 768 {
		t.Fatalf("memory tokens = %d, want 1..768", output.Trace.MemoryTokens)
	}
	// The two newest rows fit; recency is the documented relevance proxy.
	ids := memorySourceIDs(output.Trace)
	if !reflect.DeepEqual(ids, []string{"m5", "m4"}) {
		t.Fatalf("included memory ids = %v, want [m5 m4]", ids)
	}
	for _, source := range output.Trace.Sources {
		if source.Kind != "memory" {
			continue
		}
		if source.EstimatedTokens <= 0 || source.MemoryRevision <= 0 ||
			source.MemoryConfirmedAt == "" {
			t.Fatalf("memory source lacks its provenance: %+v", source)
		}
	}
	// The three oldest rows are dropped lowest-relevance first.
	truncated := memoryTruncationIDs(output.Trace)
	if !reflect.DeepEqual(truncated, []string{"m1", "m2", "m3"}) {
		t.Fatalf("truncated memory ids = %v, want [m1 m2 m3]", truncated)
	}
	for _, truncation := range output.Trace.Truncations {
		if truncation.Reason == ReasonOptionalMemory && truncation.TokensRemoved <= 0 {
			t.Fatalf("a memory truncation removed no tokens: %+v", truncation)
		}
	}
	encoded, _ := json.Marshal(output.Messages)
	if !strings.Contains(string(encoded), "memory-5") ||
		!strings.Contains(string(encoded), "memory-4") {
		t.Fatalf("the newest memories are missing: %s", encoded)
	}
	for _, dropped := range []string{"memory-1", "memory-2", "memory-3"} {
		if strings.Contains(string(encoded), dropped) {
			t.Fatalf("the truncated memory %q reached the model: %s", dropped, encoded)
		}
	}

	// A retried attempt builds exactly the same input and trace.
	second := mustBuild(t, request)
	firstEncoded, _ := json.Marshal(output)
	secondEncoded, _ := json.Marshal(second)
	if string(firstEncoded) != string(secondEncoded) {
		t.Fatalf("the memory selection is not deterministic:\n%s\n%s", firstEncoded, secondEncoded)
	}
}

func TestMemoryNegationIsIncludedAndNeverTruncated(t *testing.T) {
	request := baseRequest()
	request.Model = ModelConfig{ContextWindow: 4096, MaxOutputTokens: 1024} // budget 3072
	base := time.Date(2026, 11, 4, 10, 0, 0, 0, time.UTC)
	rows := []Memory{{
		ID: "m-old", OwnerID: "owner-1", ApplicationID: "app-1",
		State: "active", Confirmed: true, Revision: 7,
		ConfirmedAt: base,
		Text:        "The user must not receive marketing email.",
	}}
	for _, row := range memoryRowsForBudget() {
		rows = append(rows, row)
	}
	request.Memory = fakeMemorySource{rows: rows}
	output := mustBuild(t, request)
	ids := memorySourceIDs(output.Trace)
	if !containsString(ids, "m-old") {
		t.Fatalf("the negation memory was dropped: %v", ids)
	}
	found := false
	for _, source := range output.Trace.Sources {
		if source.Kind == "memory" && source.MemoryID == "m-old" {
			found = true
			if !source.Protected {
				t.Fatalf("the negation memory is not marked protected: %+v", source)
			}
		}
	}
	if !found {
		t.Fatalf("the negation memory has no trace source: %+v", output.Trace.Sources)
	}
	encoded, _ := json.Marshal(output.Messages)
	if !strings.Contains(string(encoded), "must not receive marketing email") {
		t.Fatalf("the protected negation is missing from the model input: %s", encoded)
	}
	for _, truncation := range output.Trace.Truncations {
		if truncation.Reason == ReasonOptionalMemory && truncation.MemoryID == "m-old" {
			t.Fatalf("the protected negation was truncated: %+v", truncation)
		}
	}
}

func TestMemoryIsReducedPerItemWhenTheWholeContextIsOverBudget(t *testing.T) {
	request := baseRequest()
	request.System = strings.Repeat("platform constraint ", 160) // ~3 KiB
	request.Model = ModelConfig{ContextWindow: 1600, MaxOutputTokens: 600}
	request.Memory = fakeMemorySource{rows: []Memory{{
		ID: "m-big", OwnerID: "owner-1", ApplicationID: "app-1",
		State: "active", Confirmed: true, Revision: 3,
		ConfirmedAt: time.Date(2026, 11, 4, 10, 0, 0, 0, time.UTC),
		Text:        "big-memory " + strings.Repeat("detail ", 100),
	}}}
	output := mustBuild(t, request)
	if output.Trace.MemoryState != MemoryTruncated {
		t.Fatalf("memory state = %q, want truncated", output.Trace.MemoryState)
	}
	if output.Trace.MemoryTokens != 0 {
		t.Fatalf("memory tokens = %d, want 0 after the drop", output.Trace.MemoryTokens)
	}
	encoded, _ := json.Marshal(output.Messages)
	if strings.Contains(string(encoded), "confirmed_memory") {
		t.Fatalf("the over-budget memory message survived: %s", encoded)
	}
	ids := memoryTruncationIDs(output.Trace)
	if !reflect.DeepEqual(ids, []string{"m-big"}) {
		t.Fatalf("truncated memory ids = %v, want [m-big]", ids)
	}
}

// TestEncodedMemoryMatchesTheCanonicalDocument pins the linear encoding the
// budget arithmetic relies on to the canonical JSON document the model receives.
func TestEncodedMemoryMatchesTheCanonicalDocument(t *testing.T) {
	items := []memoryItem{
		{ID: "m1", Text: "plain text"},
		{ID: "m2", Text: "quotes \" and \n newline and 日本語"},
	}
	encoded, typed := encodeMemory(items)
	if typed != nil {
		t.Fatalf("encodeMemory error = %v", typed)
	}
	payload := make([]map[string]any, 0, len(items))
	for _, item := range items {
		payload = append(payload, map[string]any{"id": item.ID, "text": item.Text})
	}
	want, err := canonical(map[string]any{"confirmed_memory": payload})
	if err != nil {
		t.Fatalf("canonical error = %v", err)
	}
	if encoded != want {
		t.Fatalf("encoded memory differs from the canonical document:\n%s\n%s", encoded, want)
	}
	if got, wantTokens := contentTokens(encoded), memoryContentTokensOf(
		[]string{
			`{"id":"m1","text":"plain text"}`,
			`{"id":"m2","text":"quotes \" and \n newline and 日本語"}`,
		}, []bool{true, true},
	); got != wantTokens {
		t.Fatalf("memoryContentTokensOf = %d, contentTokens = %d", wantTokens, got)
	}
}
