package evaluation

// F13A/T052: the deterministic checker.
//
// Every assertion is evaluated against independently collected platform truth
// (a terminal run's state, its sealed output artifact, the platform's real
// operation ledger and the citation sources recorded in that output), never
// against a model trace, a tool-call text or the page/stream that "looked
// finished". The five kinds are exactly the closed set of
// contracts/evaluation-case.schema.json.
//
// A fact that could not be collected (no terminal run, no readable output, no
// ledger read) is never "0". Each collector sets an explicit *Collected flag and
// an assertion whose fact is missing fails with a locating detail: an empty
// checker or an uncollected fact can never produce a pass.

import (
	"bytes"
	"encoding/json"
	"fmt"
	"math/big"
	"sort"
	"strings"
	"time"
)

// Facts is the independent truth collected for one case's run.
type Facts struct {
	// RunID and RunState are the platform's own run facts.
	RunID        string
	RunState     string
	RunErrorCode string
	// Output is the sealed final-output document of the run, if the run
	// produced one. OutputCollected separates "no output" from "an empty
	// output".
	Output          json.RawMessage
	OutputCollected bool
	// OutputArtifactID/OutputSHA256 identify the sealed artifact the output came
	// from, so the report's evidence is traceable to a real platform artifact.
	OutputArtifactID *string
	OutputSHA256     string
	// Effects is the platform's real *confirmed* side-effect ledger for this run,
	// keyed by the exact capability_version the platform recorded (a lowercase
	// UUID such as a tool version id; a case's `capability` names that same
	// value). Only platform.operations rows in state 'succeeded' are counted:
	// prepared/awaiting_approval/authorized/failed rows are not effects, and
	// unknown/dispatching rows make the whole evidence uncollected instead of
	// contributing an unconfirmed number. It is never counted from model text.
	Effects          map[string]int64
	EffectsCollected bool
	// EffectEvidence is the stable reason the effect evidence could not be
	// collected (for example "unconfirmed_unknown" or
	// "unconfirmed_dispatching"). It is empty exactly when the evidence was
	// collected, so an uncollected count can never be mistaken for zero.
	EffectEvidence string
	// Citations are the source ids the run's output declares. They are read
	// from the same sealed output document, under /citations.
	Citations          []string
	CitationsCollected bool
	// TaskInputArtifactID/TaskInputSHA256 identify the caller fixture the
	// platform bound to this run as its task_input artifact (F13A/T056). An empty
	// id means no fixture was bound; it is evidence, never an assertion by
	// itself.
	TaskInputArtifactID string
	TaskInputSHA256     string
	// Provenance is the frozen identity the run executed under.
	Provenance RunProvenance
	StartedAt  *time.Time
	FinishedAt *time.Time
}

// RunProvenance is the identity a collected run executed under. It is what
// makes a result traceable to a manifest and a dataset rather than to a claim.
type RunProvenance struct {
	SnapshotID         string `json:"snapshot_id,omitempty"`
	ManifestSHA256     string `json:"manifest_sha256,omitempty"`
	DatasetSHA256      string `json:"dataset_sha256,omitempty"`
	ApplicationID      string `json:"application_id,omitempty"`
	DraftRevision      int64  `json:"draft_revision,omitempty"`
	CompilerVersion    string `json:"compiler_version,omitempty"`
	WorkerImageDigest  string `json:"worker_image_digest,omitempty"`
	StateSchemaVersion string `json:"state_schema_version,omitempty"`
	WorkerID           string `json:"worker_id,omitempty"`
	ExecutionUnitID    string `json:"execution_unit_id,omitempty"`
}

// Outcome is one assertion's verdict, with the expected/actual evidence a
// curator needs to see why it passed or failed. It is written verbatim into the
// sealed report artifact (T056 reads it).
type Outcome struct {
	Kind       string `json:"kind"`
	Pointer    string `json:"pointer,omitempty"`
	Capability string `json:"capability,omitempty"`
	Expected   string `json:"expected"`
	Actual     string `json:"actual"`
	Passed     bool   `json:"passed"`
	Detail     string `json:"detail,omitempty"`
}

// KindFailureReason is the outcome kind of the T056 failure expectation. It is
// not a contract assertion kind: the expectation lives in the contract-legal
// reserved case-input member (input.expected_failure) because
// contracts/evaluation-case.schema.json has no assertion for a run's error code.
// It is evaluated by this same checker against the run's own platform state and
// is reported in the same assertions array, so a bad-input case's verdict cannot
// pass without the located business reason.
const KindFailureReason = "failure_reason"

// EvaluateFailureExpectation checks one case's declared business failure against
// the collected run truth. A nil expectation produces no outcome. An uncollected
// state or an empty error code never passes.
func EvaluateFailureExpectation(expectation *FailureExpectation, facts Facts) *Outcome {
	if expectation == nil {
		return nil
	}
	outcome := &Outcome{
		Kind:     KindFailureReason,
		Expected: expectation.ErrorCode,
		Actual:   facts.RunErrorCode,
	}
	switch {
	case strings.TrimSpace(facts.RunState) == "":
		outcome.Actual = "not_collected"
		outcome.Detail = "no terminal run state was collected for this case"
	case facts.RunState != "failed":
		outcome.Detail = "the run reached " + facts.RunState + "; a bad-input case must fail"
	case facts.RunErrorCode == "":
		outcome.Actual = "not_collected"
		outcome.Detail = "the failed run carries no error code to compare"
	case facts.RunErrorCode == expectation.ErrorCode:
		outcome.Passed = true
	default:
		outcome.Detail = "the run failed for a different reason than the case expects"
	}
	return outcome
}

// ValidDecimalString reports whether value is the contract's decimal string
// shape: ^-?\d+(\.\d+)?$. Money is a decimal string in the case format; binary
// float never enters an assertion.
func ValidDecimalString(value string) bool {
	if value == "" {
		return false
	}
	index := 0
	if value[0] == '-' {
		index = 1
	}
	digits := 0
	for index < len(value) && value[index] >= '0' && value[index] <= '9' {
		index++
		digits++
	}
	if digits == 0 {
		return false
	}
	if index == len(value) {
		return true
	}
	if value[index] != '.' {
		return false
	}
	index++
	fraction := 0
	for index < len(value) && value[index] >= '0' && value[index] <= '9' {
		index++
		fraction++
	}
	return fraction > 0 && index == len(value)
}

// NormalizeCapabilityKey normalises a capability-version key on both sides of
// the effect_count lookup. The platform records the exact capability_version the
// capability request named, lowercased when the writer validates it (a UUID);
// a case's `capability` member is that same version id. Trimming and lowercasing
// keeps a hand-written capitalised id from silently counting zero while still
// keying the count by capability rather than by the run's total.
func NormalizeCapabilityKey(value string) string {
	return strings.ToLower(strings.TrimSpace(value))
}

// EvaluateAssertions runs the declared checker for one case against the facts
// collected for its run. A case with no assertions yields no outcomes; the
// caller (and the completion guard) treats that as "no checker", never as a
// pass.
func EvaluateAssertions(assertions []Assertion, facts Facts) []Outcome {
	outcomes := make([]Outcome, 0, len(assertions))
	for _, assertion := range assertions {
		outcomes = append(outcomes, evaluateOne(assertion, facts))
	}
	return outcomes
}

func evaluateOne(assertion Assertion, facts Facts) Outcome {
	switch assertion.Kind {
	case "json_equals":
		return evaluateJSONEquals(assertion, facts)
	case "decimal_equals":
		return evaluateDecimalEquals(assertion, facts)
	case "run_state":
		outcome := Outcome{
			Kind:     assertion.Kind,
			Expected: assertion.ExpectedState,
			Actual:   facts.RunState,
		}
		switch {
		case strings.TrimSpace(facts.RunState) == "":
			outcome.Detail = "no terminal run state was collected for this case"
		case facts.RunState == assertion.ExpectedState:
			outcome.Passed = true
		default:
			outcome.Detail = "the run reached " + facts.RunState + ", not the expected state"
			if facts.RunErrorCode != "" {
				outcome.Detail += " (error_code=" + facts.RunErrorCode + ")"
			}
		}
		return outcome

	case "effect_count":
		outcome := Outcome{
			Kind:       assertion.Kind,
			Capability: assertion.Capability,
			Expected:   fmt.Sprintf("%d", assertion.ExpectedCount),
			Actual:     "not_collected",
		}
		if !facts.EffectsCollected {
			outcome.Detail = "the platform operation ledger for this run was not collected; an uncollected count is never 0"
			if facts.EffectEvidence != "" {
				// Locate why: an unknown/dispatching row could mean an effect
				// happened without a confirmed outcome, so no number is honest.
				outcome.Detail += " (effect_evidence=" + facts.EffectEvidence + ")"
			}
			return outcome
		}
		count := facts.Effects[NormalizeCapabilityKey(assertion.Capability)]
		outcome.Actual = fmt.Sprintf("%d", count)
		if count == assertion.ExpectedCount {
			outcome.Passed = true
			if count == 0 {
				outcome.Detail = "the real ledger recorded no effect for this capability; expected 0 is asserted, not assumed"
			}
			return outcome
		}
		outcome.Detail = "the real platform ledger recorded a different number of effects than expected"
		return outcome

	case "citation_sources":
		outcome := Outcome{
			Kind:     assertion.Kind,
			Expected: strings.Join(assertion.AllowedSourceIDs, ","),
			Actual:   "not_collected",
		}
		if !facts.CitationsCollected {
			outcome.Detail = "the run's output document was not collected; citations cannot be verified"
			return outcome
		}
		outcome.Actual = strings.Join(facts.Citations, ",")
		unexpected := SourcesOutsideAllowed(facts.Citations, assertion.AllowedSourceIDs)
		switch {
		case len(unexpected) > 0:
			outcome.Detail = "the run cited sources outside the allowed set: " + strings.Join(unexpected, ",")
		case assertion.RequireAtLeastOne && len(facts.Citations) == 0:
			outcome.Detail = "the case requires at least one citation and the run produced none"
		case assertion.RequireAtLeastOne:
			outcome.Passed = true
		default:
			// Every citation (possibly none) is allowed.
			outcome.Passed = true
		}
		return outcome

	default:
		return Outcome{
			Kind:   assertion.Kind,
			Detail: "this assertion kind is not supported by the checker; an unsupported checker is never a pass",
		}
	}
}

func evaluateJSONEquals(assertion Assertion, facts Facts) Outcome {
	outcome := Outcome{
		Kind:     assertion.Kind,
		Pointer:  assertion.Pointer,
		Expected: compactJSON(assertion.ExpectedJSON),
		Actual:   "not_collected",
	}
	if !facts.OutputCollected {
		outcome.Detail = "the run's output document was not collected"
		return outcome
	}
	document, err := decodeJSONValue(facts.Output)
	if err != nil {
		outcome.Detail = "the run's output document is not valid JSON"
		return outcome
	}
	actual, found := ResolvePointer(document, assertion.Pointer)
	if !found {
		outcome.Detail = "the output document has no value at this JSON Pointer"
		return outcome
	}
	outcome.Actual = compactJSON(mustEncode(actual))
	expected, err := decodeJSONValue(assertion.ExpectedJSON)
	if err != nil {
		outcome.Detail = "the expected value is not valid JSON"
		return outcome
	}
	if jsonValuesEqual(expected, actual) {
		outcome.Passed = true
		return outcome
	}
	outcome.Detail = "the value at this JSON Pointer is not equal to the expected value"
	return outcome
}

func evaluateDecimalEquals(assertion Assertion, facts Facts) Outcome {
	outcome := Outcome{
		Kind:     assertion.Kind,
		Pointer:  assertion.Pointer,
		Expected: assertion.ExpectedDecimal,
		Actual:   "not_collected",
	}
	if !facts.OutputCollected {
		outcome.Detail = "the run's output document was not collected"
		return outcome
	}
	document, err := decodeJSONValue(facts.Output)
	if err != nil {
		outcome.Detail = "the run's output document is not valid JSON"
		return outcome
	}
	actual, found := ResolvePointer(document, assertion.Pointer)
	if !found {
		outcome.Detail = "the output document has no value at this JSON Pointer"
		return outcome
	}
	literal, ok := decimalLiteral(actual)
	if !ok {
		outcome.Actual = compactJSON(mustEncode(actual))
		outcome.Detail = "the value at this JSON Pointer is not a decimal string or number"
		return outcome
	}
	outcome.Actual = literal
	actualRat, ok := new(big.Rat).SetString(literal)
	if !ok {
		outcome.Detail = "the collected value is not an exact decimal"
		return outcome
	}
	expectedRat, ok := new(big.Rat).SetString(assertion.ExpectedDecimal)
	if !ok {
		outcome.Detail = "the expected value is not an exact decimal"
		return outcome
	}
	if actualRat.Cmp(expectedRat) == 0 {
		outcome.Passed = true
		return outcome
	}
	outcome.Detail = "the decimal value at this JSON Pointer differs from the expected decimal"
	return outcome
}

// decimalLiteral renders an exact decimal literal for a JSON string (which must
// itself be a decimal string) or a JSON number, without ever going through
// float64.
func decimalLiteral(value any) (string, bool) {
	switch typed := value.(type) {
	case json.Number:
		literal := typed.String()
		if _, ok := new(big.Rat).SetString(literal); !ok {
			return "", false
		}
		return literal, true
	case string:
		if !ValidDecimalString(typed) {
			return "", false
		}
		return typed, true
	default:
		return "", false
	}
}

// SourcesOutsideAllowed returns the cited ids that are not in the allowed set,
// de-duplicated and sorted so the report detail is deterministic.
func SourcesOutsideAllowed(cited, allowed []string) []string {
	permitted := make(map[string]bool, len(allowed))
	for _, id := range allowed {
		permitted[id] = true
	}
	outside := make([]string, 0)
	seen := make(map[string]bool, len(cited))
	for _, id := range cited {
		if permitted[id] || seen[id] {
			continue
		}
		seen[id] = true
		outside = append(outside, id)
	}
	sort.Strings(outside)
	return outside
}

// ResolvePointer evaluates an RFC 6901 JSON Pointer against a decoded document.
func ResolvePointer(document any, pointer string) (any, bool) {
	if pointer == "" {
		return document, true
	}
	if !strings.HasPrefix(pointer, "/") {
		return nil, false
	}
	current := document
	for _, raw := range strings.Split(pointer[1:], "/") {
		token := strings.ReplaceAll(strings.ReplaceAll(raw, "~1", "/"), "~0", "~")
		switch typed := current.(type) {
		case map[string]any:
			value, present := typed[token]
			if !present {
				return nil, false
			}
			current = value
		case []any:
			index, ok := arrayIndex(token, len(typed))
			if !ok {
				return nil, false
			}
			current = typed[index]
		default:
			return nil, false
		}
	}
	return current, true
}

// arrayIndex parses an RFC 6901 array index: digits only, no leading zero
// unless the token is exactly "0", and inside the array.
func arrayIndex(token string, length int) (int, bool) {
	if token == "" || (len(token) > 1 && token[0] == '0') {
		return 0, false
	}
	index := 0
	for _, digit := range token {
		if digit < '0' || digit > '9' {
			return 0, false
		}
		index = index*10 + int(digit-'0')
		if index > length {
			return 0, false
		}
	}
	if index >= length {
		return 0, false
	}
	return index, true
}

// decodeJSONValue decodes with json.Number so no decimal literal is ever
// widened to a binary float.
func decodeJSONValue(raw []byte) (any, error) {
	decoder := json.NewDecoder(strings.NewReader(string(raw)))
	decoder.UseNumber()
	var value any
	if err := decoder.Decode(&value); err != nil {
		return nil, err
	}
	return value, nil
}

func jsonValuesEqual(left, right any) bool {
	switch leftTyped := left.(type) {
	case map[string]any:
		rightTyped, ok := right.(map[string]any)
		if !ok || len(leftTyped) != len(rightTyped) {
			return false
		}
		for key, value := range leftTyped {
			other, present := rightTyped[key]
			if !present || !jsonValuesEqual(value, other) {
				return false
			}
		}
		return true
	case []any:
		rightTyped, ok := right.([]any)
		if !ok || len(leftTyped) != len(rightTyped) {
			return false
		}
		for index := range leftTyped {
			if !jsonValuesEqual(leftTyped[index], rightTyped[index]) {
				return false
			}
		}
		return true
	case json.Number:
		rightTyped, ok := right.(json.Number)
		return ok && string(leftTyped) == string(rightTyped)
	default:
		return left == right
	}
}

func mustEncode(value any) []byte {
	encoded, err := json.Marshal(value)
	if err != nil {
		return []byte("null")
	}
	return encoded
}

func compactJSON(raw []byte) string {
	var buffer bytes.Buffer
	if err := json.Compact(&buffer, raw); err != nil {
		return strings.TrimSpace(string(raw))
	}
	return buffer.String()
}
