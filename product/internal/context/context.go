// Package agentcontext (F06/T047) builds the bounded model input of one
// controlled agent-node turn.
//
// The builder exists so the agent loop never sends a hand-assembled, unbounded
// message list to a model capability. It owns four rules:
//
//  1. Authorized sources only. Inputs are the run's resolved start/python/tool
//     values; every input names the run-scoped node/artifact it came from and is
//     checked against the run's authorization scope. A source outside that scope
//     is a typed refusal (`context_source_unauthorized`), never a best-effort
//     inclusion.
//  2. Deterministic budget. The budget is exactly
//     `context_window - max_output_tokens` tokens, using the documented
//     byte/4 heuristic below. No provider tokenizer, clock, random value or
//     network read participates, so a retried attempt builds the same input.
//  3. Ordered truncation. Over-budget input loses, in order: low-relevance /
//     old history, then old tool-result detail, then verbose upstream text.
//     Protected facts (negations, source/citation references, approval
//     parameters, exact amounts/statistics) are copied verbatim and recorded in
//     the trace. A boundary that cannot hold the protected minimum fails with
//     `context_budget_exhausted`; protected facts are never silently trimmed.
//  4. Display-only summaries. A history summary is never a fact source: the
//     protected paths are collected only from the current authorized inputs and
//     the validated tool-result JSON, never from a summary message. A summary
//     cannot change an amount or an authorization decision because nothing in
//     this package reads one as data.
//
// The trace contains identifiers, reasons, key names and token counts only. It
// never contains message text, memory text, credentials or personal content.
package agentcontext

import (
	stdcontext "context"
	"encoding/json"
	"fmt"
	"sort"
	"strings"

	"orchara/platform/internal/jcs"
)

// Stable platform error codes. They are safe run error_code values: lowercase
// ASCII with underscores, never provider or model text.
const (
	// CodeInvalid is a structurally unusable context request (bad model config,
	// unknown source kind, non-object tool result, duplicate input label).
	CodeInvalid = "context_invalid"
	// CodeBudgetExhausted means even the protected minimum (the fixed system
	// message plus the protected facts of the current inputs) does not fit.
	CodeBudgetExhausted = "context_budget_exhausted"
	// CodeSourceUnauthorized is the typed refusal of a source outside the run's
	// authorization scope, including a memory row for another owner/application.
	CodeSourceUnauthorized = "context_source_unauthorized"
)

// Truncation reasons, in the order the builder applies them.
const (
	ReasonLowRelevanceHistory = "low_relevance_history"
	ReasonOldToolDetail       = "old_tool_detail"
	ReasonVerboseUpstream     = "verbose_upstream_text"
	ReasonDisplayOnlySummary  = "display_only_summary"
	ReasonOptionalMemory      = "optional_memory"
	ReasonProtectedMinimum    = "protected_minimum"
)

// MessageOverheadTokens is the fixed per-message framing allowance of the
// documented estimate.
const MessageOverheadTokens = 4

// bytesPerToken is the documented deterministic estimate: ceil(UTF-8 bytes / 4).
// It deliberately over-counts ASCII and under-counts wide characters relative to
// a provider tokenizer; a stable, conservative-enough number is the requirement,
// not a vendor-specific one.
const bytesPerToken = 4

// truncationMarker ends every shortened string. It is ASCII so its own token
// cost is stable.
const truncationMarker = "...[truncated]"

// MemoryState values reported in the trace.
const (
	MemoryNotRun      = "not_run"
	MemoryIncluded    = "included"
	MemoryEmpty       = "empty"
	MemoryUnavailable = "unavailable"
)

// Error is a typed context refusal. Code is the only part the run records.
type Error struct {
	Code    string
	Message string
}

func (e *Error) Error() string { return e.Code + ": " + e.Message }

func invalid(format string, args ...any) *Error {
	return &Error{Code: CodeInvalid, Message: fmt.Sprintf(format, args...)}
}

// ModelConfig is the slice of the passed model profile the budget needs.
type ModelConfig struct {
	ContextWindow   int64
	MaxOutputTokens int64
}

// Scope is the run-derived authorization scope. NodeIDs and ArtifactIDs are the
// only places an input may name. Empty means "nothing is authorized", never
// "everything".
type Scope struct {
	RunID         string
	OwnerID       string
	ApplicationID string
	NodeIDs       []string
	ArtifactIDs   []string
}

func (s Scope) allowsNode(nodeID string) bool {
	value := strings.ToLower(strings.TrimSpace(nodeID))
	if value == "" {
		return false
	}
	for _, allowed := range s.NodeIDs {
		if strings.ToLower(strings.TrimSpace(allowed)) == value {
			return true
		}
	}
	return false
}

func (s Scope) allowsArtifact(artifactID string) bool {
	value := strings.ToLower(strings.TrimSpace(artifactID))
	if value == "" {
		return true
	}
	for _, allowed := range s.ArtifactIDs {
		if strings.ToLower(strings.TrimSpace(allowed)) == value {
			return true
		}
	}
	return false
}

// Source names where one authorized value came from. Kind is one of literal,
// start, python, tool or memory. NodeID/ArtifactID are empty for a literal.
type Source struct {
	Kind       string
	NodeID     string
	ArtifactID string
	Label      string
}

// ResolvedInput is one named value the executor resolved from the sealed node
// bindings and the current run's outputs.
type ResolvedInput struct {
	Label  string
	Source Source
	Value  any
}

// HistoryMessage is one prior conversation or tool message. Role is user,
// assistant or tool. Summary marks a display-only summary: it is never a fact
// source, is dropped first under pressure and is not scanned for protected
// facts.
type HistoryMessage struct {
	Role      string
	Content   any
	Source    Source
	Relevance int // higher survives longer; 0 is the lowest relevance
	Summary   bool
}

// DeclaredTool is one tool declaration passed to the model capability. The
// builder does not render it into messages; it is kept in the request so the
// resolved-input contract is explicit and testable.
type DeclaredTool struct {
	Name              string
	CapabilityVersion string
}

// Memory is one confirmed, active personal-memory row. Confirmed is always true
// for an includable row; State must be "active".
type Memory struct {
	ID            string
	OwnerID       string
	ApplicationID string
	State         string
	Confirmed     bool
	Text          string
}

// MemorySource is the F09 seam. The production source is NoMemorySource because
// the personal-memory storage does not exist in this batch; its absence is
// reported as not_run, never simulated with canned rows.
type MemorySource interface {
	ConfirmedMemories(ctx stdcontext.Context, ownerID, applicationID string) ([]Memory, error)
}

// NoMemorySource is the production memory seam: F09 storage is absent, so it
// reads nothing. It is a real implementation of "no storage", not fake memory.
type NoMemorySource struct{}

// ConfirmedMemories returns no rows: this build has no personal-memory store.
func (NoMemorySource) ConfirmedMemories(
	stdcontext.Context, string, string,
) ([]Memory, error) {
	return nil, nil
}

// Request is one turn's builder input.
type Request struct {
	Scope   Scope
	System  string
	Inputs  []ResolvedInput
	History []HistoryMessage
	Tools   []DeclaredTool
	Model   ModelConfig
	Memory  MemorySource
}

// TraceSource is one trace entry naming an authorized source, never its text.
type TraceSource struct {
	Kind            string `json:"kind"`
	NodeID          string `json:"node_id,omitempty"`
	ArtifactID      string `json:"artifact_id,omitempty"`
	Label           string `json:"label,omitempty"`
	EstimatedTokens int    `json:"estimated_tokens"`
	Protected       bool   `json:"protected,omitempty"`
}

// Truncation is one trace entry: which source lost tokens, why, and how many.
type Truncation struct {
	Source        string `json:"source"`
	Reason        string `json:"reason"`
	TokensRemoved int    `json:"tokens_removed"`
}

// Trace is the durable, secret-free record of one build.
type Trace struct {
	Sources         []TraceSource `json:"sources"`
	Truncations     []Truncation  `json:"truncations"`
	Protected       []string      `json:"protected"`
	EstimatedTokens int           `json:"estimated_tokens"`
	BudgetTokens    int           `json:"budget_tokens"`
	ReservedOutput  int           `json:"reserved_output_tokens"`
	MemoryState     string        `json:"memory_state"`
	// DisplayOnlySummaryCount counts history summaries the builder deliberately
	// kept out of the model input. A summary is display-only: it is never model
	// context and never a fact source.
	DisplayOnlySummaryCount int `json:"display_only_summary_count,omitempty"`
}

// Output is the model input plus its trace.
type Output struct {
	Messages []map[string]any
	Trace    Trace
}

// builtMessage is one message under construction: content plus the trace
// metadata that must never become model text.
type builtMessage struct {
	role      string
	content   string
	source    Source
	relevance int
	summary   bool
	tool      bool
	memory    bool
	label     string
}

// EstimateTokens is the documented deterministic estimate for one string:
// ceil(UTF-8 bytes / 4). It is exported so tests and reports can state the exact
// arithmetic the budget uses.
func EstimateTokens(text string) int {
	if text == "" {
		return 0
	}
	return (len(text) + bytesPerToken - 1) / bytesPerToken
}

func contentTokens(content string) int {
	return MessageOverheadTokens + EstimateTokens("content") + EstimateTokens(content)
}

// canonical renders deterministic JSON bytes: RFC 8785 via the platform's JCS
// package, so map iteration order can never move the budget.
func canonical(value any) (string, error) {
	raw, err := json.Marshal(value)
	if err != nil {
		return "", err
	}
	encoded, err := jcs.CanonicalizeBytes(raw)
	if err != nil {
		return "", err
	}
	return string(encoded), nil
}

// Build assembles one bounded message list. The returned Output always carries
// the trace built so far, including on a typed refusal.
func Build(ctx stdcontext.Context, request Request) (Output, error) {
	budget, budgetErr := budgetOf(request.Model)
	if budgetErr != nil {
		return Output{Trace: Trace{
			Sources: []TraceSource{}, Truncations: []Truncation{}, Protected: []string{},
			MemoryState: MemoryNotRun,
		}}, budgetErr
	}
	trace := Trace{
		Sources:      []TraceSource{},
		Truncations:  []Truncation{},
		Protected:    []string{},
		BudgetTokens: budget,
		MemoryState:  MemoryNotRun,
	}
	trace.ReservedOutput = int(request.Model.MaxOutputTokens)
	if strings.TrimSpace(request.System) == "" {
		return Output{Trace: trace}, invalid("the system message must not be empty")
	}

	inputs, inputErr := collectInputs(request.Scope, request.Inputs, &trace)
	if inputErr != nil {
		return Output{Trace: trace}, inputErr
	}
	memoryContent, memErr := loadMemory(ctx, request, &trace)
	if memErr != nil {
		return Output{Trace: trace}, memErr
	}

	protected := map[string]bool{}
	collectProtected(inputs, "", protected)
	for _, item := range request.History {
		if item.Summary || !isToolHistory(item) {
			continue
		}
		content, contentErr := contentValue(item.Content)
		if contentErr != nil {
			return Output{Trace: trace}, invalid("a tool history message cannot be encoded")
		}
		value, decodeErr := decodeJSON(content)
		if decodeErr != nil {
			return Output{Trace: trace}, invalid(
				"a tool history message is not validated JSON: %v", decodeErr)
		}
		collectProtected(value, "", protected)
		source := item.Source
		if strings.TrimSpace(source.Kind) == "" {
			source.Kind = "tool"
		}
		trace.Sources = append(trace.Sources, TraceSource{
			Kind: traceKind(source), NodeID: source.NodeID,
			ArtifactID: source.ArtifactID, Label: "tool_history",
			EstimatedTokens: EstimateTokens(content),
		})
	}
	trace.Protected = sortedKeys(protected)

	messages := make([]*builtMessage, 0, 2+len(request.History)+1)

	messages = append(messages, &builtMessage{
		role: "system", content: request.System, source: Source{Kind: "system"},
		label: "system",
	})

	inputJSON, err := canonical(inputs)
	if err != nil {
		return Output{Trace: trace}, invalid("the resolved agent inputs cannot be encoded")
	}
	messages = append(messages, &builtMessage{
		role: "user", content: inputJSON, source: Source{Kind: "inputs"},
		label: "inputs",
	})
	if memoryContent != "" {
		messages = append(messages, &builtMessage{
			role: "user", content: memoryContent, source: Source{Kind: "memory"},
			label: "memory", memory: true,
		})
	}
	for _, item := range request.History {
		if item.Summary {
			// Display-only: never sent to the model and never scanned for facts.
			trace.DisplayOnlySummaryCount++
			continue
		}
		role := strings.ToLower(strings.TrimSpace(item.Role))
		if role != "user" && role != "assistant" && role != "tool" {
			return Output{Trace: trace}, invalid("a history message role is not user, assistant or tool")
		}
		content, encodeErr := contentValue(item.Content)
		if encodeErr != nil {
			return Output{Trace: trace}, invalid("a history message content cannot be encoded")
		}
		messages = append(messages, &builtMessage{
			role:      role,
			content:   content,
			source:    item.Source,
			relevance: item.Relevance,
			summary:   item.Summary,
			tool:      role == "tool" && !item.Summary,
			label:     sourceName(item.Source),
		})
	}

	// Fail fast when the protected minimum alone cannot fit: this is the one
	// failure that must never be papered over by dropping protected facts.
	protectedJSON, err := protectedMinimum(inputs, protected)
	if err != nil {
		return Output{Trace: trace}, err
	}
	minimum := contentTokens(request.System) + contentTokens(protectedJSON)
	minimum += MessageOverheadTokens + EstimateTokens("role")
	if minimum > budget {
		trace.EstimatedTokens = minimum
		return Output{Trace: trace}, &Error{
			Code: CodeBudgetExhausted,
			Message: fmt.Sprintf(
				"the protected context minimum needs %d tokens but the budget is %d "+
					"(context_window %d minus max_output_tokens %d)",
				minimum, budget, request.Model.ContextWindow, request.Model.MaxOutputTokens),
		}
	}

	total := estimateMessages(messages)
	trace.EstimatedTokens = total

	// Step 1: drop display-only summaries, then low-relevance/old history chunks.
	dropped := make(map[*builtMessage]bool)
	for total > budget {
		candidate := nextHistoryToDrop(messages, dropped)
		if candidate == nil {
			break
		}
		before := contentTokens(candidate.content)
		dropped[candidate] = true
		total -= before
		reason := ReasonLowRelevanceHistory
		if candidate.summary {
			reason = ReasonDisplayOnlySummary
		}
		addTruncation(&trace, candidate, reason, before)
	}
	messages = compact(messages, dropped)

	// Step 2: reduce old tool-result detail, keeping every protected branch.
	total = estimateMessages(messages)
	for _, message := range toolMessages(messages, true) {
		if total <= budget {
			break
		}
		_, removed, reduceErr := reduceToolMessage(message, protected)
		if reduceErr != nil {
			return Output{Trace: trace}, reduceErr
		}
		if removed > 0 {
			total -= removed
			addTruncation(&trace, message, ReasonOldToolDetail, removed)
		}
	}
	// Step 3: shorten verbose upstream text (never a protected key).
	total = estimateMessages(messages)
	inputMessage := messages[1]
	for _, limit := range []int{1024, 256, 64, 0} {
		if total <= budget {
			break
		}
		changed, removed, truncErr := truncateUpstream(inputMessage, limit)
		if truncErr != nil {
			return Output{Trace: trace}, truncErr
		}
		if changed {
			total -= removed
			addTruncation(&trace, inputMessage, ReasonVerboseUpstream, removed)
		}
	}

	// Last resort, still in the fixed order: the newest tool detail, then any
	// remaining non-tool history, then optional memory, then non-protected
	// input skeleton. Protected branches survive every one of these steps.
	total = estimateMessages(messages)
	if total > budget {
		for _, message := range toolMessages(messages, false) {
			if total <= budget {
				break
			}
			_, removed, reduceErr := reduceToolMessage(message, protected)
			if reduceErr != nil {
				return Output{Trace: trace}, reduceErr
			}
			if removed > 0 {
				total -= removed
				addTruncation(&trace, message, ReasonOldToolDetail, removed)
			}
		}
	}
	total = estimateMessages(messages)
	if total > budget {
		for _, message := range messages {
			if total <= budget {
				break
			}
			if message.role == "system" || message.role == "user" && message.label == "inputs" {
				continue
			}
			if message.memory {
				before := contentTokens(message.content)
				message.content = ""
				total -= before
				addTruncation(&trace, message, ReasonOptionalMemory, before)
				dropped[message] = true
				continue
			}
			if message.tool {
				continue
			}
			before := contentTokens(message.content)
			dropped[message] = true
			total -= before
			addTruncation(&trace, message, ReasonLowRelevanceHistory, before)
		}
		messages = compact(messages, dropped)
	}
	total = estimateMessages(messages)
	if total > budget {
		changed, removed, truncErr := stripInputSkeleton(inputMessage)
		if truncErr != nil {
			return Output{Trace: trace}, truncErr
		}
		if changed {
			total -= removed
			addTruncation(&trace, inputMessage, ReasonProtectedMinimum, removed)
		}
	}
	total = estimateMessages(messages)
	if total > budget {
		trace.EstimatedTokens = total
		return Output{Trace: trace}, &Error{
			Code: CodeBudgetExhausted,
			Message: fmt.Sprintf(
				"the authorized context cannot fit the %d-token budget without trimming protected facts",
				budget),
		}
	}

	out := make([]map[string]any, 0, len(messages))
	for _, message := range messages {
		out = append(out, map[string]any{"role": message.role, "content": message.content})
	}
	trace.EstimatedTokens = total
	return Output{Messages: out, Trace: trace}, nil
}

func budgetOf(model ModelConfig) (int, *Error) {
	if model.ContextWindow < 1 {
		return 0, invalid("the model context_window must be positive")
	}
	if model.MaxOutputTokens < 1 {
		return 0, invalid("the model max_output_tokens must be positive")
	}
	if model.MaxOutputTokens >= model.ContextWindow {
		return 0, invalid(
			"the model max_output_tokens must be smaller than context_window")
	}
	return int(model.ContextWindow - model.MaxOutputTokens), nil
}

// collectInputs authorizes every named input and returns the deterministic value
// map. Duplicate labels would make the model message ambiguous, so they are a
// typed refusal.
func collectInputs(scope Scope, inputs []ResolvedInput, trace *Trace) (map[string]any, error) {
	result := make(map[string]any, len(inputs))
	for _, input := range inputs {
		label := strings.TrimSpace(input.Label)
		if label == "" {
			label = strings.TrimSpace(input.Source.Label)
		}
		if label == "" {
			return nil, invalid("every resolved agent input must carry a label")
		}
		if _, exists := result[label]; exists {
			return nil, invalid("the resolved agent inputs repeat the label %q", label)
		}
		if err := authorize(scope, input.Source); err != nil {
			return nil, err
		}
		result[label] = input.Value
		encoded, encodeErr := canonical(input.Value)
		if encodeErr != nil {
			return nil, invalid("the resolved input %q cannot be encoded", label)
		}
		protected := false
		paths := map[string]bool{}
		collectProtected(input.Value, label, paths)
		if len(paths) > 0 {
			protected = true
		}
		trace.Sources = append(trace.Sources, TraceSource{
			Kind: traceKind(input.Source), NodeID: input.Source.NodeID,
			ArtifactID: input.Source.ArtifactID, Label: label,
			EstimatedTokens: EstimateTokens(encoded), Protected: protected,
		})
	}
	return result, nil
}

// authorize re-checks the source against the run-derived scope. It is the only
// place a source may enter; labels alone never authorize data.
func authorize(scope Scope, source Source) error {
	kind := strings.ToLower(strings.TrimSpace(source.Kind))
	switch kind {
	case "literal":
		if strings.TrimSpace(source.NodeID) != "" || strings.TrimSpace(source.ArtifactID) != "" {
			return &Error{Code: CodeSourceUnauthorized,
				Message: "a literal input must not name a node or artifact"}
		}
		return nil
	case "start", "python", "tool":
		if !scope.allowsNode(source.NodeID) {
			return &Error{Code: CodeSourceUnauthorized,
				Message: "the input source node is outside this run's authorization scope"}
		}
		if !scope.allowsArtifact(source.ArtifactID) {
			return &Error{Code: CodeSourceUnauthorized,
				Message: "the input source artifact is outside this run's authorization scope"}
		}
		return nil
	default:
		return invalid("the input source kind is not one of literal, start, python or tool")
	}
}

func traceKind(source Source) string {
	kind := strings.ToLower(strings.TrimSpace(source.Kind))
	if kind == "" {
		return "literal"
	}
	return kind
}

// loadMemory reads the F09 seam. No storage is not a failure: the trace says
// not_run and no memory message is produced. A returned row is used only when it
// is confirmed, active and owned by exactly this run's owner+application; a
// foreign row is a typed refusal rather than silently filtered into a partial
// memory view.
func loadMemory(ctx stdcontext.Context, request Request, trace *Trace) (string, *Error) {
	if request.Memory == nil {
		trace.MemoryState = MemoryNotRun
		return "", nil
	}
	if _, absent := request.Memory.(NoMemorySource); absent {
		trace.MemoryState = MemoryNotRun
		return "", nil
	}
	rows, err := request.Memory.ConfirmedMemories(
		ctx, request.Scope.OwnerID, request.Scope.ApplicationID,
	)
	if err != nil {
		trace.MemoryState = MemoryUnavailable
		return "", nil
	}
	if len(rows) == 0 {
		trace.MemoryState = MemoryEmpty
		return "", nil
	}
	items := make([]map[string]any, 0, len(rows))
	for _, row := range rows {
		if row.State != "active" || !row.Confirmed || strings.TrimSpace(row.Text) == "" {
			continue
		}
		if row.OwnerID != request.Scope.OwnerID ||
			row.ApplicationID != request.Scope.ApplicationID {
			return "", &Error{Code: CodeSourceUnauthorized,
				Message: "the memory source returned a row for another owner or application"}
		}
		items = append(items, map[string]any{
			"id": row.ID, "text": row.Text,
		})
	}
	if len(items) == 0 {
		trace.MemoryState = MemoryEmpty
		return "", nil
	}
	encoded, encodeErr := canonical(map[string]any{"confirmed_memory": items})
	if encodeErr != nil {
		return "", invalid("the confirmed memory cannot be encoded")
	}
	trace.MemoryState = MemoryIncluded
	trace.Sources = append(trace.Sources, TraceSource{
		Kind: "memory", Label: "confirmed_memory",
		EstimatedTokens: EstimateTokens(encoded),
	})
	return encoded, nil
}

// protectedKeys is the fixed protected-key vocabulary. It is intentionally
// explicit: a new protected shape must be added here and tested, never inferred
// from a model summary.
var protectedKeys = map[string]bool{
	// User negations/exclusions.
	"exclude": true, "excludes": true, "excluded": true, "exclusion": true,
	"must_not": true, "negative": true, "negatives": true, "negation": true,
	"negations": true,
	// Source/citation references and document ids.
	"sources": true, "source_refs": true, "citations": true, "citation": true,
	"documents": true, "document": true, "document_id": true,
	"document_ids": true, "references": true,
	// Approval parameters and writable targets.
	"approval": true, "approvals": true, "proposal": true, "proposals": true,
	"target": true, "targets": true, "params": true, "parameters": true,
	// Exact amounts/statistics.
	"amount": true, "amounts": true, "total": true, "totals": true,
	"currency": true, "quantity": true, "quantities": true, "qty": true,
	"price": true, "unit_price": true, "subtotal": true, "tax": true,
	"balance": true, "revenue": true, "cost": true, "sum": true,
	"count": true, "order_count": true, "stats": true, "statistics": true,
}

var protectedPrefixes = []string{
	"not_", "negative_", "exclude_", "excluded_", "must_not", "negation_",
	"amount_", "total_", "currency_", "quantity_", "price_", "approval_",
	"proposal_", "target_", "source_", "citation_", "document_",
}

var protectedSuffixes = []string{
	"_count", "_amount", "_total", "_currency", "_quantity", "_price",
	"_document_id", "_document_ids", "_citation", "_citations",
}

// isProtectedKey reports whether one object key is in the protected vocabulary.
// Matching is case-insensitive; underscores and dashes are equivalent.
func isProtectedKey(key string) bool {
	value := strings.ToLower(strings.TrimSpace(key))
	value = strings.ReplaceAll(value, "-", "_")
	if protectedKeys[value] {
		return true
	}
	for _, prefix := range protectedPrefixes {
		if strings.HasPrefix(value, prefix) {
			return true
		}
	}
	for _, suffix := range protectedSuffixes {
		if strings.HasSuffix(value, suffix) {
			return true
		}
	}
	return false
}

// collectProtected records dotted paths of protected branches. A protected key
// protects its whole value verbatim, so the traversal stops there.
func collectProtected(value any, path string, into map[string]bool) {
	switch typed := value.(type) {
	case map[string]any:
		for key, child := range typed {
			childPath := key
			if path != "" {
				childPath = path + "." + key
			}
			if isProtectedKey(key) {
				into[childPath] = true
				continue
			}
			collectProtected(child, childPath, into)
		}
	case []any:
		for index, child := range typed {
			collectProtected(child, fmt.Sprintf("%s[%d]", path, index), into)
		}
	}
}

func sortedKeys(values map[string]bool) []string {
	keys := make([]string, 0, len(values))
	for key := range values {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	return keys
}

// protectedMinimum is the smallest input message that still carries every
// protected fact: only protected branches, canonicalised.
func protectedMinimum(inputs map[string]any, protected map[string]bool) (string, error) {
	if len(protected) == 0 {
		return "{}", nil
	}
	kept := keepProtected(inputs)
	encoded, err := canonical(kept)
	if err != nil {
		return "", invalid("the protected input minimum cannot be encoded")
	}
	return encoded, nil
}

// keepProtected returns the protected branches of a value and drops everything
// else. A scalar is kept only when its parent key was protected, which cannot
// happen here because collectProtected stops at that key; therefore scalars at
// the root are dropped.
func keepProtected(value any) any {
	switch typed := value.(type) {
	case map[string]any:
		out := make(map[string]any, len(typed))
		for key, child := range typed {
			if isProtectedKey(key) {
				out[key] = child
				continue
			}
			kept := keepProtected(child)
			if kept != nil {
				out[key] = kept
			}
		}
		if len(out) == 0 {
			return nil
		}
		return out
	case []any:
		out := make([]any, 0, len(typed))
		for _, child := range typed {
			kept := keepProtected(child)
			if kept != nil {
				out = append(out, kept)
			}
		}
		if len(out) == 0 {
			return nil
		}
		return out
	default:
		return nil
	}
}

func estimateMessages(messages []*builtMessage) int {
	total := 0
	for _, message := range messages {
		total += contentTokens(message.content)
	}
	return total
}

func nextHistoryToDrop(messages []*builtMessage, dropped map[*builtMessage]bool) *builtMessage {
	var best *builtMessage
	bestSummary := false
	for _, message := range messages {
		if message.role == "system" || message.label == "inputs" || message.memory ||
			message.tool || dropped[message] {
			continue
		}
		if best == nil {
			best = message
			bestSummary = message.summary
			continue
		}
		if message.summary != bestSummary {
			if message.summary {
				best, bestSummary = message, true
			}
			continue
		}
		if message.relevance < best.relevance {
			best = message
		}
	}
	return best
}

func toolMessages(messages []*builtMessage, oldestOnly bool) []*builtMessage {
	var tools []*builtMessage
	for _, message := range messages {
		if message.tool {
			tools = append(tools, message)
		}
	}
	if oldestOnly && len(tools) > 1 {
		return tools[:len(tools)-1]
	}
	return tools
}

// reduceToolMessage replaces a validated tool-result JSON object with only its
// protected branches. The model keeps the exact amount/source/approval facts and
// loses low-value detail; the trace records the removed token count. A non-object
// tool result would hide protected facts, so it is refused rather than guessed.
func reduceToolMessage(message *builtMessage, protected map[string]bool) (string, int, *Error) {
	var value any
	if err := json.Unmarshal([]byte(message.content), &value); err != nil {
		return "", 0, invalid("a tool history message is not validated JSON")
	}
	if _, ok := value.(map[string]any); !ok {
		return "", 0, invalid("a tool history message is not a JSON object")
	}
	kept := keepProtected(value)
	encoded, err := canonical(kept)
	if err != nil {
		return "", 0, invalid("a reduced tool history message cannot be encoded")
	}
	before := contentTokens(message.content)
	after := contentTokens(encoded)
	if after >= before {
		return message.content, 0, nil
	}
	message.content = encoded
	return encoded, before - after, nil
}

// truncateUpstream shortens non-protected strings inside the current inputs
// message. Protected branches are never traversed or re-encoded.
func truncateUpstream(message *builtMessage, limit int) (bool, int, *Error) {
	var value any
	if err := json.Unmarshal([]byte(message.content), &value); err != nil {
		return false, 0, invalid("the input message is not a JSON object")
	}
	value, changed := truncateValue(value, limit)
	if !changed {
		return false, 0, nil
	}
	encoded, err := canonical(value)
	if err != nil {
		return false, 0, invalid("the truncated input cannot be encoded")
	}
	before := contentTokens(message.content)
	after := contentTokens(encoded)
	if after >= before {
		return false, 0, nil
	}
	message.content = encoded
	return true, before - after, nil
}

// truncateValue recursively shortens non-protected strings. Protected keys are
// never traversed. It returns the possibly replaced value and whether anything
// changed.
func truncateValue(value any, limit int) (any, bool) {
	switch typed := value.(type) {
	case map[string]any:
		changed := false
		for key, child := range typed {
			if isProtectedKey(key) {
				continue
			}
			replacement, childChanged := truncateValue(child, limit)
			if !childChanged {
				continue
			}
			if text, ok := replacement.(string); ok && len(text) == 0 {
				delete(typed, key)
			} else {
				typed[key] = replacement
			}
			changed = true
		}
		return typed, changed
	case []any:
		changed := false
		for index, child := range typed {
			replacement, childChanged := truncateValue(child, limit)
			if !childChanged {
				continue
			}
			typed[index] = replacement
			changed = true
		}
		return typed, changed
	case string:
		if len(typed) <= limit {
			return typed, false
		}
		return truncateString(typed, limit), true
	default:
		return value, false
	}
}

func truncateString(value string, limit int) string {
	if limit <= 0 {
		return ""
	}
	if len(value) <= limit {
		return value
	}
	cut := limit
	for cut > 0 && (value[cut]&0xc0) == 0x80 {
		cut--
	}
	return value[:cut] + truncationMarker
}

// stripInputSkeleton is the very last step: it keeps only protected branches of
// the current inputs. It cannot remove a protected fact by construction.
func stripInputSkeleton(message *builtMessage) (bool, int, *Error) {
	var value any
	if err := json.Unmarshal([]byte(message.content), &value); err != nil {
		return false, 0, invalid("the input message is not a JSON object")
	}
	kept := keepProtected(value)
	encoded, err := canonical(kept)
	if err != nil {
		return false, 0, invalid("the protected input skeleton cannot be encoded")
	}
	before := contentTokens(message.content)
	after := contentTokens(encoded)
	if after >= before {
		return false, 0, nil
	}
	message.content = encoded
	return true, before - after, nil
}

func addTruncation(trace *Trace, message *builtMessage, reason string, removed int) {
	if removed <= 0 {
		return
	}
	source := message.label
	if source == "" {
		source = sourceName(message.source)
	}
	trace.Truncations = append(trace.Truncations, Truncation{
		Source: source, Reason: reason, TokensRemoved: removed,
	})
}

func sourceName(source Source) string {
	kind := traceKind(source)
	parts := []string{kind}
	if source.NodeID != "" {
		parts = append(parts, source.NodeID)
	}
	if source.ArtifactID != "" {
		parts = append(parts, source.ArtifactID)
	}
	if source.Label != "" && source.Label != source.NodeID {
		parts = append(parts, source.Label)
	}
	return strings.Join(parts, ":")
}

func compact(messages []*builtMessage, dropped map[*builtMessage]bool) []*builtMessage {
	out := messages[:0]
	for _, message := range messages {
		if dropped[message] {
			continue
		}
		out = append(out, message)
	}
	return out
}

func isToolHistory(item HistoryMessage) bool {
	kind := strings.ToLower(strings.TrimSpace(item.Source.Kind))
	return strings.ToLower(strings.TrimSpace(item.Role)) == "tool" || kind == "tool"
}

func contentValue(content any) (string, error) {
	switch typed := content.(type) {
	case string:
		return typed, nil
	default:
		return canonical(content)
	}
}

func decodeJSON(content string) (any, error) {
	var value any
	if err := json.Unmarshal([]byte(content), &value); err != nil {
		return nil, err
	}
	return value, nil
}
