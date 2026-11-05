package evaluation

// F13A/T052: the sealed evaluation dataset.
//
// A dataset artifact is one JSON document. The platform accepts exactly this
// shape (contracts/evaluation-case.schema.json, copied to
// product/contracts/evaluation-case.schema.json):
//
//	{"cases": [evaluation-case, ...]}
//
// A bare top-level array of cases is also accepted (documented in the T052 ADR
// and report) because a dataset curator who already has a case array should not
// have to wrap it. Nothing else is accepted: the parser is strict, so an
// unknown field, a wrong type, a duplicated case id or an assertion document
// that does not match one of the five kinds is a located refusal, never a
// silently ignored field.
//
// Two rules are load bearing and are enforced twice (here, and again by the
// completion guard that reads the sealed report):
//
//   - a case must carry at least one assertion. An empty checker is not a pass;
//     it is not even a valid case.
//   - a case id must be unique in the dataset. Per-case results are keyed by
//     case id, so a duplicate would silently merge two verdicts.
//
// The legacy `orchara.evaluation-dataset/1` document (the T026 synthetic
// placeholder, `{"schema": ..., "cases": [{"id","input","expected_sha256"}]}`)
// is deliberately NOT accepted here: T052 keeps that behaviour working by
// recognising it before this parser runs (IsLegacySyntheticDataset), so the two
// formats never have to be told apart by guesswork.

import (
	"bytes"
	"encoding/json"
	"fmt"
	"sort"
	"strings"

	"orchara/platform/internal/jcs"
)

const (
	// SchemaVersion is the only evaluation-case schema version this build
	// understands (contracts/evaluation-case.schema.json: const "1.0").
	SchemaVersion = "1.0"
	// MaxDatasetBytes bounds the dataset document the server will parse. A
	// dataset is a curated list of cases, not a data dump.
	MaxDatasetBytes int64 = 4 << 20
	// MaxCases bounds one dataset so one evaluation cannot queue unbounded
	// runs. The limit is recorded, not silently applied.
	MaxCases = 200
	// MaxAssertionsPerCase bounds one case's checker.
	MaxAssertionsPerCase = 100
	// MaxCaseIDLength mirrors the locating-read budget of a report case id.
	MaxCaseIDLength = 200
	// InteractionKey is the reserved case-input member the collector owns: it
	// names the predefined interactions the platform must perform while the
	// case's run is in flight. It is stripped from the run input before sealing
	// so a case's app never sees platform control data.
	InteractionKey = "interactions"
	// TaskInputKey is the reserved case-input member F13A/T056 adds: it names the
	// caller-uploaded fixture the platform must bind to the case's run as the
	// run's task_input artifact (by content, never by a path). Like InteractionKey
	// it is stripped from the sealed run input: binding an artifact is a platform
	// action, not application input.
	TaskInputKey = "task_input"
	// ExpectedFailureKey is the reserved case-input member F13A/T056 adds: the
	// business failure the case expects (for example missing_column). The
	// evaluation-case contract has no assertion kind for a run's error code, so
	// the expectation lives in the contract-legal `input` object and is evaluated
	// by the same checker; it is stripped from the run input and recorded as one
	// more per-case outcome.
	ExpectedFailureKey = "expected_failure"

	// MediaTypeCSV and MediaTypeXLSX are the only input media types the preset
	// sales tool accepts. They are repeated here (the tool package owns the tool
	// protocol) because the case-binding shape must be validatable without
	// importing the executor.
	MediaTypeCSV  = "text/csv"
	MediaTypeXLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)

// Dataset is one parsed, fully validated evaluation dataset.
type Dataset struct {
	Cases []Case
}

// Case is one evaluation case, already validated against the contract schema.
type Case struct {
	CaseID     string
	Category   string
	FamilyID   string
	Split      string
	Target     Target
	Input      json.RawMessage
	FixtureID  string
	Assertions []Assertion
	Provenance Provenance

	// T056 reserved case-input bindings. They are parsed from `input` members
	// the contract allows (additionalProperties:true) and are never part of the
	// sealed run input.
	TaskInput       *TaskInputBinding
	ExpectedFailure *FailureExpectation
}

// TaskInputBinding names the caller-uploaded fixture a case's run must receive as
// its task_input artifact. The artifact is addressed by content (its recorded
// SHA-256 and media type), never by object key or filesystem path: a committed
// dataset can therefore name a fixture without embedding a volatile artifact id,
// and the server resolves it among the evaluation owner's own ready artifacts.
type TaskInputBinding struct {
	SHA256    string
	MediaType string
}

// FailureExpectation is the business failure a bad-input case expects. It is
// evaluated against the run's own platform state and error code, never against
// tool stdout or a log line.
type FailureExpectation struct {
	ErrorCode string
}

// Target names what the case runs against. F13A executes debug_snapshot
// targets; published_version has no publish path in this build and is recorded
// as an honest refusal by the runner.
type Target struct {
	Kind string
	ID   string
}

// Provenance is the contract's provenance object.
type Provenance struct {
	Origin        string
	Reference     string
	LicenseReview string
}

// Assertion is one declarative checker. Only the fields of its Kind are set.
type Assertion struct {
	Kind              string
	Pointer           string
	ExpectedJSON      json.RawMessage
	ExpectedDecimal   string
	ExpectedState     string
	Capability        string
	ExpectedCount     int64
	Source            string
	AllowedSourceIDs  []string
	RequireAtLeastOne bool
}

// Error is a located dataset refusal. Pointer is an RFC 6901 pointer into the
// dataset document, so a curator can be told exactly which case and which
// member is wrong. Code is a stable machine code.
type Error struct {
	Code    string
	Pointer string
	Message string
}

func (e *Error) Error() string {
	return fmt.Sprintf("%s at %s: %s", e.Code, pointerLabel(e.Pointer), e.Message)
}

func pointerLabel(pointer string) string {
	if pointer == "" {
		return "/"
	}
	return pointer
}

// Dataset error codes.
const (
	CodeDatasetInvalidJSON     = "dataset_invalid_json"
	CodeDatasetSchema          = "dataset_schema"
	CodeDatasetUnknownField    = "dataset_unknown_field"
	CodeDatasetMissingField    = "dataset_missing_field"
	CodeDatasetDuplicateCase   = "dataset_duplicate_case"
	CodeDatasetEmptyAssertions = "dataset_empty_assertions"
	CodeDatasetInvalidAssert   = "dataset_invalid_assertion"
	CodeDatasetInvalidPointer  = "dataset_json_pointer"
	CodeDatasetTooLarge        = "dataset_too_large"
	CodeDatasetTooManyCases    = "dataset_too_many_cases"
)

var (
	categories = map[string]bool{
		"sales": true, "routing": true, "rag": true, "memory": true,
		"delegation": true, "approval_effects": true, "lifecycle": true,
	}
	splits         = map[string]bool{"dev": true, "frozen_test": true}
	targetKinds    = map[string]bool{"published_version": true, "debug_snapshot": true}
	origins        = map[string]bool{"self_created": true, "adapted_external": true}
	licenseReviews = map[string]bool{"not_applicable": true, "pending": true, "reviewed": true}
	runStates      = map[string]bool{
		"succeeded": true, "failed": true, "cancelled": true,
		"needs_reconciliation": true, "recovery_pending": true,
	}
)

// IsLegacySyntheticDataset reports whether this document is the T026 synthetic
// placeholder (`{"schema": "...", "cases": [...]}`) rather than an F13A
// evaluation dataset. The distinction is the presence of a string `schema`
// member, which the evaluation-case schema itself forbids; the two formats are
// otherwise never guessed at.
func IsLegacySyntheticDataset(document []byte) bool {
	fields, ok := decodeObject(document)
	if !ok {
		return false
	}
	raw, present := fields["schema"]
	if !present {
		return false
	}
	var schema string
	if err := json.Unmarshal(raw, &schema); err != nil {
		return false
	}
	return strings.TrimSpace(schema) != ""
}

// ParseDataset strictly parses and validates one dataset document.
func ParseDataset(document []byte) (*Dataset, error) {
	if int64(len(document)) > MaxDatasetBytes {
		return nil, &Error{
			Code:    CodeDatasetTooLarge,
			Pointer: "",
			Message: fmt.Sprintf("the dataset must be at most %d bytes", MaxDatasetBytes),
		}
	}
	var rawCases []json.RawMessage
	if fields, ok := decodeObject(document); ok {
		for key := range fields {
			if key != "cases" {
				return nil, &Error{
					Code:    CodeDatasetUnknownField,
					Pointer: "/" + escapePointerToken(key),
					Message: "the dataset accepts exactly one member, cases",
				}
			}
		}
		raw, present := fields["cases"]
		if !present {
			return nil, &Error{
				Code:    CodeDatasetMissingField,
				Pointer: "/cases",
				Message: "the dataset must carry a cases array",
			}
		}
		if err := json.Unmarshal(raw, &rawCases); err != nil || rawCases == nil {
			return nil, &Error{
				Code:    CodeDatasetSchema,
				Pointer: "/cases",
				Message: "cases must be an array of evaluation cases",
			}
		}
	} else if err := json.Unmarshal(document, &rawCases); err != nil || rawCases == nil {
		return nil, &Error{
			Code:    CodeDatasetInvalidJSON,
			Pointer: "",
			Message: "the dataset must be a JSON object with a cases array (or a bare array of cases)",
		}
	}

	if len(rawCases) == 0 {
		return nil, &Error{
			Code:    CodeDatasetSchema,
			Pointer: "/cases",
			Message: "the dataset must contain at least one case; an empty dataset cannot be evaluated",
		}
	}
	if len(rawCases) > MaxCases {
		return nil, &Error{
			Code:    CodeDatasetTooManyCases,
			Pointer: "/cases",
			Message: fmt.Sprintf("the dataset must contain at most %d cases", MaxCases),
		}
	}

	dataset := &Dataset{Cases: make([]Case, 0, len(rawCases))}
	seen := make(map[string]bool, len(rawCases))
	for index, raw := range rawCases {
		pointer := fmt.Sprintf("/cases/%d", index)
		item, err := parseCase(raw, pointer)
		if err != nil {
			return nil, err
		}
		if seen[item.CaseID] {
			return nil, &Error{
				Code:    CodeDatasetDuplicateCase,
				Pointer: pointer + "/case_id",
				Message: fmt.Sprintf("case id %q appears more than once; per-case verdicts are keyed by case id", item.CaseID),
			}
		}
		seen[item.CaseID] = true
		dataset.Cases = append(dataset.Cases, *item)
	}
	return dataset, nil
}

// parseCase validates one case object, locating every refusal.
func parseCase(raw json.RawMessage, pointer string) (*Case, error) {
	fields, ok := decodeObject(raw)
	if !ok {
		return nil, &Error{
			Code: CodeDatasetSchema, Pointer: pointer,
			Message: "an evaluation case must be a JSON object",
		}
	}
	allowed := map[string]bool{
		"schema_version": true, "case_id": true, "category": true, "family_id": true,
		"split": true, "target": true, "input": true, "fixture_id": true,
		"assertions": true, "provenance": true,
	}
	for key := range fields {
		if !allowed[key] {
			return nil, &Error{
				Code: CodeDatasetUnknownField, Pointer: pointer + "/" + escapePointerToken(key),
				Message: "the evaluation-case schema is additionalProperties:false; this member is not defined",
			}
		}
	}
	for _, key := range []string{
		"schema_version", "case_id", "category", "family_id", "split",
		"target", "input", "fixture_id", "assertions", "provenance",
	} {
		if _, present := fields[key]; !present {
			return nil, &Error{
				Code: CodeDatasetMissingField, Pointer: pointer + "/" + key,
				Message: "this required member is missing",
			}
		}
	}

	item := &Case{}
	var err error
	if item, err = parseCaseIdentity(fields, pointer, item); err != nil {
		return nil, err
	}

	target, err := parseTarget(fields["target"], pointer+"/target")
	if err != nil {
		return nil, err
	}
	item.Target = *target

	trimmedInput := bytes.TrimSpace(fields["input"])
	if len(trimmedInput) == 0 || trimmedInput[0] != '{' {
		return nil, &Error{
			Code: CodeDatasetSchema, Pointer: pointer + "/input",
			Message: "input must be a JSON object",
		}
	}
	canonical, err := jcs.CanonicalizeBytes(trimmedInput)
	if err != nil {
		return nil, &Error{
			Code: CodeDatasetSchema, Pointer: pointer + "/input",
			Message: "input must be canonicalisable JSON (RFC 8785 JCS): " + err.Error(),
		}
	}
	item.Input = canonical
	if err := parseReservedBindings(trimmedInput, pointer+"/input", item); err != nil {
		return nil, err
	}

	assertions, err := parseAssertions(fields["assertions"], pointer+"/assertions")
	if err != nil {
		return nil, err
	}
	item.Assertions = assertions

	provenance, err := parseProvenance(fields["provenance"], pointer+"/provenance")
	if err != nil {
		return nil, err
	}
	item.Provenance = *provenance
	return item, nil
}

// parseCaseIdentity reads the scalar members of one case.
func parseCaseIdentity(fields map[string]json.RawMessage, pointer string, item *Case) (*Case, error) {
	schemaVersion, err := requireString(fields, "schema_version", pointer)
	if err != nil {
		return nil, err
	}
	if schemaVersion != SchemaVersion {
		return nil, &Error{
			Code: CodeDatasetSchema, Pointer: pointer + "/schema_version",
			Message: fmt.Sprintf("schema_version must be %q", SchemaVersion),
		}
	}
	caseID, err := requireString(fields, "case_id", pointer)
	if err != nil {
		return nil, err
	}
	caseID = strings.TrimSpace(caseID)
	if caseID == "" || len(caseID) > MaxCaseIDLength {
		return nil, &Error{
			Code: CodeDatasetSchema, Pointer: pointer + "/case_id",
			Message: fmt.Sprintf("case_id must be a non-empty string of at most %d characters", MaxCaseIDLength),
		}
	}
	item.CaseID = caseID

	category, err := requireString(fields, "category", pointer)
	if err != nil {
		return nil, err
	}
	if !categories[category] {
		return nil, &Error{
			Code: CodeDatasetSchema, Pointer: pointer + "/category",
			Message: "category must be one of the evaluation-case enum values",
		}
	}
	item.Category = category

	familyID, err := requireString(fields, "family_id", pointer)
	if err != nil {
		return nil, err
	}
	item.FamilyID = familyID

	split, err := requireString(fields, "split", pointer)
	if err != nil {
		return nil, err
	}
	if !splits[split] {
		return nil, &Error{
			Code: CodeDatasetSchema, Pointer: pointer + "/split",
			Message: `split must be "dev" or "frozen_test"`,
		}
	}
	item.Split = split

	fixtureID, err := requireString(fields, "fixture_id", pointer)
	if err != nil {
		return nil, err
	}
	item.FixtureID = fixtureID
	return item, nil
}

// parseTarget validates the case's target object.
func parseTarget(raw json.RawMessage, pointer string) (*Target, error) {
	fields, ok := decodeObject(raw)
	if !ok {
		return nil, &Error{
			Code: CodeDatasetSchema, Pointer: pointer,
			Message: "target must be an object with kind and id",
		}
	}
	for key := range fields {
		if key != "kind" && key != "id" {
			return nil, &Error{
				Code: CodeDatasetUnknownField, Pointer: pointer + "/" + escapePointerToken(key),
				Message: "target is additionalProperties:false; it accepts exactly kind and id",
			}
		}
	}
	kind, err := requireString(fields, "kind", pointer)
	if err != nil {
		return nil, err
	}
	id, err := requireString(fields, "id", pointer)
	if err != nil {
		return nil, err
	}
	if !targetKinds[kind] {
		return nil, &Error{
			Code: CodeDatasetSchema, Pointer: pointer + "/kind",
			Message: `target.kind must be "published_version" or "debug_snapshot"`,
		}
	}
	if strings.TrimSpace(id) == "" {
		return nil, &Error{
			Code: CodeDatasetSchema, Pointer: pointer + "/id",
			Message: "target.id must be a non-empty string",
		}
	}
	return &Target{Kind: kind, ID: id}, nil
}

// parseProvenance validates the provenance object.
func parseProvenance(raw json.RawMessage, pointer string) (*Provenance, error) {
	fields, ok := decodeObject(raw)
	if !ok {
		return nil, &Error{
			Code: CodeDatasetSchema, Pointer: pointer,
			Message: "provenance must be an object with origin, reference and license_review",
		}
	}
	for key := range fields {
		switch key {
		case "origin", "reference", "license_review":
		default:
			return nil, &Error{
				Code: CodeDatasetUnknownField, Pointer: pointer + "/" + escapePointerToken(key),
				Message: "provenance is additionalProperties:false",
			}
		}
	}
	origin, err := requireString(fields, "origin", pointer)
	if err != nil {
		return nil, err
	}
	if !origins[origin] {
		return nil, &Error{
			Code: CodeDatasetSchema, Pointer: pointer + "/origin",
			Message: `provenance.origin must be "self_created" or "adapted_external"`,
		}
	}
	reference, err := requireString(fields, "reference", pointer)
	if err != nil {
		return nil, err
	}
	review, err := requireString(fields, "license_review", pointer)
	if err != nil {
		return nil, err
	}
	if !licenseReviews[review] {
		return nil, &Error{
			Code: CodeDatasetSchema, Pointer: pointer + "/license_review",
			Message: `license_review must be "not_applicable", "pending" or "reviewed"`,
		}
	}
	return &Provenance{Origin: origin, Reference: reference, LicenseReview: review}, nil
}

// parseAssertions validates the case's assertions array: at least one, at most
// MaxAssertionsPerCase, every member exactly one of the five kinds.
func parseAssertions(raw json.RawMessage, pointer string) ([]Assertion, error) {
	var items []json.RawMessage
	if err := json.Unmarshal(raw, &items); err != nil || items == nil {
		return nil, &Error{
			Code: CodeDatasetSchema, Pointer: pointer,
			Message: "assertions must be an array of assertion documents",
		}
	}
	if len(items) == 0 {
		// An empty checker is not a pass and is not a case. This is the
		// contract's minItems:1, enforced with a locating refusal.
		return nil, &Error{
			Code: CodeDatasetEmptyAssertions, Pointer: pointer,
			Message: "a case must carry at least one assertion; an empty checker can never produce a verdict",
		}
	}
	if len(items) > MaxAssertionsPerCase {
		return nil, &Error{
			Code: CodeDatasetSchema, Pointer: pointer,
			Message: fmt.Sprintf("a case may carry at most %d assertions", MaxAssertionsPerCase),
		}
	}
	assertions := make([]Assertion, 0, len(items))
	for index, item := range items {
		assertion, err := parseAssertion(item, fmt.Sprintf("%s/%d", pointer, index))
		if err != nil {
			return nil, err
		}
		assertions = append(assertions, *assertion)
	}
	return assertions, nil
}

// parseAssertion validates one assertion against its kind's oneOf branch.
func parseAssertion(raw json.RawMessage, pointer string) (*Assertion, error) {
	fields, ok := decodeObject(raw)
	if !ok {
		return nil, &Error{
			Code: CodeDatasetInvalidAssert, Pointer: pointer,
			Message: "an assertion must be a JSON object",
		}
	}
	kind, err := requireString(fields, "kind", pointer)
	if err != nil {
		return nil, err
	}
	assertion := &Assertion{Kind: kind}

	switch kind {
	case "json_equals":
		if err := onlyKeys(fields, pointer, "kind", "pointer", "expected"); err != nil {
			return nil, err
		}
		if err := requirePointer(fields, pointer, assertion); err != nil {
			return nil, err
		}
		expected, present := fields["expected"]
		if !present {
			return nil, &Error{
				Code: CodeDatasetMissingField, Pointer: pointer + "/expected",
				Message: "json_equals requires expected (any JSON value, including null)",
			}
		}
		assertion.ExpectedJSON = append(json.RawMessage(nil), expected...)

	case "decimal_equals":
		if err := onlyKeys(fields, pointer, "kind", "pointer", "expected"); err != nil {
			return nil, err
		}
		if err := requirePointer(fields, pointer, assertion); err != nil {
			return nil, err
		}
		expected, err := requireString(fields, "expected", pointer)
		if err != nil {
			return nil, err
		}
		if !ValidDecimalString(expected) {
			return nil, &Error{
				Code: CodeDatasetInvalidAssert, Pointer: pointer + "/expected",
				Message: "decimal_equals expects a decimal string such as \"250.00\" (no exponent, no binary float)",
			}
		}
		assertion.ExpectedDecimal = expected

	case "run_state":
		if err := onlyKeys(fields, pointer, "kind", "expected"); err != nil {
			return nil, err
		}
		expected, err := requireString(fields, "expected", pointer)
		if err != nil {
			return nil, err
		}
		if !runStates[expected] {
			return nil, &Error{
				Code: CodeDatasetInvalidAssert, Pointer: pointer + "/expected",
				Message: "run_state expects one of succeeded, failed, cancelled, needs_reconciliation, recovery_pending",
			}
		}
		assertion.ExpectedState = expected

	case "effect_count":
		if err := onlyKeys(fields, pointer, "kind", "capability", "expected", "source"); err != nil {
			return nil, err
		}
		capability, err := requireString(fields, "capability", pointer)
		if err != nil {
			return nil, err
		}
		if strings.TrimSpace(capability) == "" {
			return nil, &Error{
				Code: CodeDatasetInvalidAssert, Pointer: pointer + "/capability",
				Message: "effect_count requires the capability it counts",
			}
		}
		assertion.Capability = capability
		var expected int64
		if rawExpected, present := fields["expected"]; !present {
			return nil, &Error{
				Code: CodeDatasetMissingField, Pointer: pointer + "/expected",
				Message: "effect_count requires expected (an integer >= 0)",
			}
		} else if err := json.Unmarshal(rawExpected, &expected); err != nil || expected < 0 {
			return nil, &Error{
				Code: CodeDatasetInvalidAssert, Pointer: pointer + "/expected",
				Message: "effect_count expects a whole number >= 0; expected 0 is a real assertion",
			}
		}
		assertion.ExpectedCount = expected
		source, err := requireString(fields, "source", pointer)
		if err != nil {
			return nil, err
		}
		if source != "stub_ledger" {
			return nil, &Error{
				Code: CodeDatasetInvalidAssert, Pointer: pointer + "/source",
				Message: `effect_count.source must be "stub_ledger"; the model trace is never the effect truth`,
			}
		}
		assertion.Source = source

	case "citation_sources":
		if err := onlyKeys(fields, pointer, "kind", "allowed_source_ids", "require_at_least_one"); err != nil {
			return nil, err
		}
		var allowed []string
		rawAllowed, present := fields["allowed_source_ids"]
		if !present {
			return nil, &Error{
				Code: CodeDatasetMissingField, Pointer: pointer + "/allowed_source_ids",
				Message: "citation_sources requires allowed_source_ids",
			}
		}
		if err := json.Unmarshal(rawAllowed, &allowed); err != nil || len(allowed) == 0 {
			return nil, &Error{
				Code: CodeDatasetInvalidAssert, Pointer: pointer + "/allowed_source_ids",
				Message: "allowed_source_ids must be a non-empty array of source ids",
			}
		}
		for index, id := range allowed {
			if strings.TrimSpace(id) == "" {
				return nil, &Error{
					Code:    CodeDatasetInvalidAssert,
					Pointer: fmt.Sprintf("%s/allowed_source_ids/%d", pointer, index),
					Message: "a source id must be non-empty",
				}
			}
		}
		assertion.AllowedSourceIDs = allowed
		var requireOne bool
		rawRequire, present := fields["require_at_least_one"]
		if !present {
			return nil, &Error{
				Code: CodeDatasetMissingField, Pointer: pointer + "/require_at_least_one",
				Message: "citation_sources requires require_at_least_one",
			}
		}
		if err := json.Unmarshal(rawRequire, &requireOne); err != nil {
			return nil, &Error{
				Code: CodeDatasetInvalidAssert, Pointer: pointer + "/require_at_least_one",
				Message: "require_at_least_one must be a boolean",
			}
		}
		assertion.RequireAtLeastOne = requireOne

	default:
		return nil, &Error{
			Code: CodeDatasetInvalidAssert, Pointer: pointer + "/kind",
			Message: "kind must be json_equals, decimal_equals, run_state, effect_count or citation_sources",
		}
	}
	return assertion, nil
}

// parseReservedBindings strictly validates the two reserved case-input members
// T056 adds. They are optional; when present, a wrong shape is a located refusal
// at submit time rather than a silent no-op at collection time.
func parseReservedBindings(raw json.RawMessage, pointer string, item *Case) error {
	fields, ok := decodeObject(raw)
	if !ok {
		// parseCase already refused a non-object input.
		return nil
	}
	if binding, present := fields[TaskInputKey]; present {
		parsed, err := parseTaskInputBinding(binding, pointer+"/"+TaskInputKey)
		if err != nil {
			return err
		}
		item.TaskInput = parsed
	}
	if expectation, present := fields[ExpectedFailureKey]; present {
		parsed, err := parseFailureExpectation(expectation, pointer+"/"+ExpectedFailureKey)
		if err != nil {
			return err
		}
		item.ExpectedFailure = parsed
	}
	return nil
}

// parseTaskInputBinding validates one content-addressed fixture binding.
func parseTaskInputBinding(raw json.RawMessage, pointer string) (*TaskInputBinding, error) {
	fields, ok := decodeObject(raw)
	if !ok {
		return nil, &Error{
			Code: CodeDatasetSchema, Pointer: pointer,
			Message: "task_input must be an object with sha256 and media_type",
		}
	}
	for key := range fields {
		if key != "sha256" && key != "media_type" {
			return nil, &Error{
				Code: CodeDatasetUnknownField, Pointer: pointer + "/" + escapePointerToken(key),
				Message: "task_input is additionalProperties:false; it accepts exactly sha256 and media_type",
			}
		}
	}
	sha, err := requireString(fields, "sha256", pointer)
	if err != nil {
		return nil, err
	}
	sha = strings.ToLower(strings.TrimSpace(sha))
	if !validSHA256Hex(sha) {
		return nil, &Error{
			Code: CodeDatasetSchema, Pointer: pointer + "/sha256",
			Message: "task_input.sha256 must be the 64-hex-character SHA-256 of the uploaded fixture",
		}
	}
	mediaType, err := requireString(fields, "media_type", pointer)
	if err != nil {
		return nil, err
	}
	if mediaType != MediaTypeCSV && mediaType != MediaTypeXLSX {
		return nil, &Error{
			Code: CodeDatasetSchema, Pointer: pointer + "/media_type",
			Message: "task_input.media_type must be text/csv or the XLSX media type; the preset tool accepts nothing else",
		}
	}
	return &TaskInputBinding{SHA256: sha, MediaType: mediaType}, nil
}

// parseFailureExpectation validates the expected business failure code.
func parseFailureExpectation(raw json.RawMessage, pointer string) (*FailureExpectation, error) {
	fields, ok := decodeObject(raw)
	if !ok {
		return nil, &Error{
			Code: CodeDatasetSchema, Pointer: pointer,
			Message: "expected_failure must be an object with error_code",
		}
	}
	for key := range fields {
		if key != "error_code" {
			return nil, &Error{
				Code: CodeDatasetUnknownField, Pointer: pointer + "/" + escapePointerToken(key),
				Message: "expected_failure is additionalProperties:false; it accepts exactly error_code",
			}
		}
	}
	code, err := requireString(fields, "error_code", pointer)
	if err != nil {
		return nil, err
	}
	if !validErrorCode(code) {
		return nil, &Error{
			Code: CodeDatasetSchema, Pointer: pointer + "/error_code",
			Message: `expected_failure.error_code must be a stable lowercase code such as "missing_column"`,
		}
	}
	return &FailureExpectation{ErrorCode: code}, nil
}

// validSHA256Hex reports whether value is 64 hexadecimal characters.
func validSHA256Hex(value string) bool {
	if len(value) != 64 {
		return false
	}
	for index := 0; index < len(value); index++ {
		char := value[index]
		if (char < '0' || char > '9') && (char < 'a' || char > 'f') {
			return false
		}
	}
	return true
}

// validErrorCode reports whether value is a stable machine code: lowercase
// letters/digits/underscore, starting with a letter, at most 64 characters.
func validErrorCode(value string) bool {
	if value == "" || len(value) > 64 || value[0] < 'a' || value[0] > 'z' {
		return false
	}
	for index := 1; index < len(value); index++ {
		char := value[index]
		if (char < 'a' || char > 'z') && (char < '0' || char > '9') && char != '_' {
			return false
		}
	}
	return true
}

// Interactions returns the case's predefined interactions, validated. They live
// in the reserved case-input member (input.interactions) because the case
// schema itself is additionalProperties:false.
func (c *Case) Interactions() ([]Interaction, error) {
	var input map[string]json.RawMessage
	if err := json.Unmarshal(c.Input, &input); err != nil {
		return nil, nil
	}
	raw, present := input[InteractionKey]
	if !present {
		return nil, nil
	}
	var items []json.RawMessage
	if err := json.Unmarshal(raw, &items); err != nil {
		return nil, &Error{
			Code: CodeDatasetSchema, Pointer: "/cases/" + c.CaseID + "/input/" + InteractionKey,
			Message: "interactions must be an array",
		}
	}
	interactions := make([]Interaction, 0, len(items))
	for index, item := range items {
		fields, ok := decodeObject(item)
		if !ok {
			return nil, &Error{
				Code:    CodeDatasetSchema,
				Pointer: fmt.Sprintf("/cases/%s/input/%s/%d", c.CaseID, InteractionKey, index),
				Message: "an interaction must be an object with kind",
			}
		}
		for key := range fields {
			if key != "kind" {
				return nil, &Error{
					Code: CodeDatasetUnknownField,
					Pointer: fmt.Sprintf("/cases/%s/input/%s/%d/%s", c.CaseID, InteractionKey, index,
						escapePointerToken(key)),
					Message: "an interaction accepts exactly one member, kind",
				}
			}
		}
		kind, err := requireString(fields, "kind",
			fmt.Sprintf("/cases/%s/input/%s/%d", c.CaseID, InteractionKey, index))
		if err != nil {
			return nil, err
		}
		if !InteractionKinds[kind] {
			return nil, &Error{
				Code:    CodeDatasetSchema,
				Pointer: fmt.Sprintf("/cases/%s/input/%s/%d/kind", c.CaseID, InteractionKey, index),
				Message: "this build performs exactly one predefined interaction, cancel",
			}
		}
		interactions = append(interactions, Interaction{Kind: kind})
	}
	return interactions, nil
}

// Interaction is one predefined platform interaction the collector performs
// while a case's run is in flight. This build supports exactly "cancel" (a real
// POST /runs/{id}/cancel as the evaluation owner), which is what makes a
// cancelled-run case a genuinely executed case rather than a claim.
type Interaction struct {
	Kind string
}

// InteractionKinds is the closed set of interactions this build performs.
var InteractionKinds = map[string]bool{"cancel": true}

// RunInput returns the canonical bytes the case's run receives: the case input
// with the collector's reserved members removed. The removal is deliberate and
// documented: platform control data (predefined interactions, the task_input
// binding and the expected failure) is not part of the application's input, and
// the sealed bytes must be the exact bytes the platform used.
func (c *Case) RunInput() ([]byte, error) {
	var input map[string]json.RawMessage
	if err := json.Unmarshal(c.Input, &input); err != nil {
		return nil, err
	}
	delete(input, InteractionKey)
	delete(input, TaskInputKey)
	delete(input, ExpectedFailureKey)
	encoded, err := json.Marshal(input)
	if err != nil {
		return nil, err
	}
	return jcs.CanonicalizeBytes(encoded)
}

// ---------------------------------------------------------------------------
// small strict-decoding helpers
// ---------------------------------------------------------------------------

func decodeObject(raw []byte) (map[string]json.RawMessage, bool) {
	trimmed := bytes.TrimSpace(raw)
	if len(trimmed) == 0 || trimmed[0] != '{' {
		return nil, false
	}
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(trimmed, &fields); err != nil || fields == nil {
		return nil, false
	}
	return fields, true
}

func requireString(fields map[string]json.RawMessage, key, pointer string) (string, error) {
	raw, present := fields[key]
	if !present {
		return "", &Error{
			Code: CodeDatasetMissingField, Pointer: pointer + "/" + key,
			Message: "this required member is missing",
		}
	}
	var value string
	if err := json.Unmarshal(raw, &value); err != nil {
		return "", &Error{
			Code: CodeDatasetSchema, Pointer: pointer + "/" + key,
			Message: key + " must be a string",
		}
	}
	return value, nil
}

func onlyKeys(fields map[string]json.RawMessage, pointer string, allowed ...string) error {
	permitted := make(map[string]bool, len(allowed))
	for _, key := range allowed {
		permitted[key] = true
	}
	unknown := make([]string, 0)
	for key := range fields {
		if !permitted[key] {
			unknown = append(unknown, key)
		}
	}
	if len(unknown) == 0 {
		return nil
	}
	sort.Strings(unknown)
	return &Error{
		Code: CodeDatasetUnknownField, Pointer: pointer + "/" + escapePointerToken(unknown[0]),
		Message: "this assertion kind is additionalProperties:false; " + unknown[0] + " is not defined",
	}
}

func requirePointer(fields map[string]json.RawMessage, pointer string, assertion *Assertion) error {
	value, err := requireString(fields, "pointer", pointer)
	if err != nil {
		return err
	}
	if !ValidJSONPointer(value) {
		return &Error{
			Code: CodeDatasetInvalidPointer, Pointer: pointer + "/pointer",
			Message: `pointer must be an RFC 6901 JSON Pointer ("" for the whole document, otherwise starting with "/")`,
		}
	}
	assertion.Pointer = value
	return nil
}

// ValidJSONPointer reports whether value is an RFC 6901 JSON Pointer.
func ValidJSONPointer(value string) bool {
	if value == "" {
		return true
	}
	if !strings.HasPrefix(value, "/") {
		return false
	}
	for _, token := range strings.Split(value[1:], "/") {
		for index := 0; index < len(token); index++ {
			if token[index] != '~' {
				continue
			}
			if index+1 >= len(token) || (token[index+1] != '0' && token[index+1] != '1') {
				return false
			}
			index++
		}
	}
	return true
}

// escapePointerToken applies RFC 6901 escaping to one reference token.
func escapePointerToken(token string) string {
	return strings.ReplaceAll(strings.ReplaceAll(token, "~", "~0"), "/", "~1")
}
