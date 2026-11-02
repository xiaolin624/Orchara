package catalog

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"orchara/platform/internal/policy"
	"orchara/platform/internal/uuid"
	"orchara/platform/internal/workflow"
)

// Bounds and contract constants of the draft write path. They are deliberately
// small and explicit: a draft is configuration text, not an upload.
const (
	// MaxApplicationNameRunes bounds the human name. contracts/openapi.json
	// only says minLength 1; the upper bound is an implementation limit so a
	// multi-megabyte name cannot become a stored row.
	MaxApplicationNameRunes = 200
	// The three fields are independent documents; each has its own cap inside
	// the API's 1 MiB request-body cap.
	MaxDefinitionBytes     = 256 << 10
	MaxLayoutBytes         = 256 << 10
	MaxCapabilityCardBytes = 64 << 10
)

// draftCapabilityKeys is the exact DraftInput.capability_card shape. The
// contract requires all six keys (additionalProperties false) whenever the card
// is present; {} remains the honest "not filled in yet" state for a new draft.
var draftCapabilityKeys = []string{
	"purpose", "includes", "excludes", "required_inputs", "expected_outputs", "examples",
}

var (
	// ErrValidation is the sentinel behind a *ValidationError.
	ErrValidation = errors.New("validation failed")
	// ErrNotOwner is a visible application whose draft is being written by a
	// caller who is not its owner. A read grant is not a write grant.
	ErrNotOwner = errors.New("only the owner may write the draft")
	// ErrPreconditionRequired is a missing/empty If-Match (428).
	ErrPreconditionRequired = errors.New("If-Match is required")
	// ErrPreconditionFailed is a stale or malformed If-Match (412).
	ErrPreconditionFailed = errors.New("If-Match does not match the current revision")
)

// ValidationError names the draft field that failed structural validation.
// It is its own type (not identity's or policy's) because the API maps it by
// field and the message must not be confused with auth validation.
type ValidationError struct {
	Field   string
	Message string
}

func (e *ValidationError) Error() string { return e.Field + ": " + e.Message }

// Unwrap lets callers use errors.Is(err, ErrValidation).
func (e *ValidationError) Unwrap() error { return ErrValidation }

// PreconditionRequiredError is the 428 case: the caller did not send If-Match.
type PreconditionRequiredError struct{}

func (e *PreconditionRequiredError) Error() string { return "If-Match is required for a draft save" }

// Unwrap lets callers use errors.Is(err, ErrPreconditionRequired).
func (e *PreconditionRequiredError) Unwrap() error { return ErrPreconditionRequired }

// PreconditionFailedError is the 412 case and carries the current state the
// contract requires in `details` so a client can reconcile without a second
// request.
type PreconditionFailedError struct {
	CurrentRevision int64
	CurrentETag     string
}

func (e *PreconditionFailedError) Error() string {
	return "the draft has moved on; fetch it again and retry with the current ETag"
}

// Unwrap lets callers use errors.Is(err, ErrPreconditionFailed).
func (e *PreconditionFailedError) Unwrap() error { return ErrPreconditionFailed }

// Draft is one stored draft row. Definition/Layout/CapabilityCard are the raw
// jsonb text: the API returns exactly what the database holds (jsonb key order
// is not significant to JSON clients) and never re-serialises a number.
type Draft struct {
	ApplicationID     string
	Revision          int64
	ExecutionRevision int64
	Definition        json.RawMessage
	Layout            json.RawMessage
	CapabilityCard    json.RawMessage
	UpdatedAt         time.Time
}

// ETag is the strong ETag the contract requires: the revision in quotes.
func (d Draft) ETag() string { return FormatETag(d.Revision) }

// DraftInput is one structurally valid contract DraftInput. Construction proves
// the shape (see ValidateDraftInput), so the save path never stores an object
// the read path cannot serve.
//
// UnknownFields carries top-level request keys outside the contract schema. It
// is kept here (rather than being rejected by the JSON decoder) so the save path
// can enforce the documented order — visibility, ownership, If-Match, then body
// shape — and answer a 404/412 before a 422 when both would apply.
type DraftInput struct {
	Definition     json.RawMessage
	Layout         json.RawMessage
	CapabilityCard json.RawMessage
	UnknownFields  []string
}

// SaveResult is the post-save revision state returned to the API for the new
// ETag.
type SaveResult struct {
	Revision          int64
	ExecutionRevision int64
}

// FormatETag renders the strong ETag for a revision.
func FormatETag(revision int64) string {
	return `"` + strconv.FormatInt(revision, 10) + `"`
}

// ParseETag parses the exact strong ETag this server issues. Anything else is
// rejected: weak validators (W/"1"), lists, "*" and unquoted numbers are not
// the contract token, and accepting them would make "garbled" indistinguishable
// from "stale".
func ParseETag(raw string) (int64, bool) {
	trimmed := strings.TrimSpace(raw)
	if len(trimmed) < 3 || trimmed[0] != '"' || trimmed[len(trimmed)-1] != '"' {
		return 0, false
	}
	inner := trimmed[1 : len(trimmed)-1]
	if inner == "" || strings.TrimLeft(inner, "0123456789") != "" {
		return 0, false
	}
	revision, err := strconv.ParseInt(inner, 10, 64)
	if err != nil || revision < 1 {
		return 0, false
	}
	return revision, true
}

// ValidateApplicationName trims the name and enforces the non-empty/limit rule.
func ValidateApplicationName(name string) (string, error) {
	trimmed := strings.TrimSpace(name)
	if trimmed == "" {
		return "", &ValidationError{Field: "name", Message: "name must not be empty"}
	}
	if utf8.RuneCountInString(trimmed) > MaxApplicationNameRunes {
		return "", &ValidationError{
			Field:   "name",
			Message: fmt.Sprintf("name must be at most %d characters", MaxApplicationNameRunes),
		}
	}
	return trimmed, nil
}

// CreateApplication inserts one application and its revision=1 empty draft in a
// single transaction. The draft is honestly empty: definition {}, layout {},
// capability_card {} — no fabricated examples and no invented workflow, because
// the ticket explicitly forbids inventing capability-card content.
func (r *Reader) CreateApplication(
	ctx context.Context,
	ownerID, name string,
) (*Application, error) {
	trimmed, err := ValidateApplicationName(name)
	if err != nil {
		return nil, err
	}
	if !uuid.Valid(ownerID) {
		return nil, &ValidationError{Field: "owner_id", Message: "owner_id must be a UUID"}
	}
	tx, err := r.DB.BeginTx(ctx, nil)
	if err != nil {
		return nil, fmt.Errorf("starting the application transaction: %w", err)
	}
	defer func() { _ = tx.Rollback() }()

	application := &Application{Name: trimmed}
	if err := tx.QueryRowContext(ctx, `
		INSERT INTO platform.applications (id, owner_id, name)
		VALUES (gen_random_uuid(), $1, $2)
		RETURNING id`,
		ownerID, trimmed,
	).Scan(&application.ID); err != nil {
		return nil, fmt.Errorf("creating the application: %w", err)
	}
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO platform.application_drafts
		       (application_id, revision, execution_revision, definition, layout, capability_card)
		VALUES ($1, 1, 1, '{}'::jsonb, '{}'::jsonb, '{}'::jsonb)`,
		application.ID,
	); err != nil {
		return nil, fmt.Errorf("creating the empty draft: %w", err)
	}
	if err := tx.Commit(); err != nil {
		return nil, fmt.Errorf("committing the application: %w", err)
	}
	return application, nil
}

// planDraftRead is the shared row read for GetDraft and SaveDraft. It returns
// the draft plus the application owner so the caller can run one authorization
// decision before any other check.
func (r *Reader) loadDraft(
	ctx context.Context, applicationID string,
) (*Draft, string, error) {
	if !uuid.Valid(applicationID) {
		return nil, "", ErrNotVisible
	}
	var (
		draft                              *Draft = &Draft{}
		owner                              string
		definition, layout, capabilityCard string
	)
	err := r.DB.QueryRowContext(ctx, `
		SELECT d.application_id::text, d.revision, d.execution_revision,
		       d.definition::text, d.layout::text, d.capability_card::text,
		       d.updated_at, a.owner_id::text
		  FROM platform.application_drafts AS d
		  JOIN platform.applications AS a ON a.id = d.application_id
		 WHERE d.application_id = $1
		   AND a.archived_at IS NULL`,
		applicationID,
	).Scan(
		&draft.ApplicationID, &draft.Revision, &draft.ExecutionRevision,
		&definition, &layout, &capabilityCard,
		&draft.UpdatedAt, &owner,
	)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, "", ErrNotVisible
	}
	if err != nil {
		return nil, "", fmt.Errorf("reading the draft: %w", err)
	}
	// The pgx stdlib returns jsonb as a string; scan it explicitly instead of
	// asking database/sql to convert a driver string into json.RawMessage.
	draft.Definition = json.RawMessage(definition)
	draft.Layout = json.RawMessage(layout)
	draft.CapabilityCard = json.RawMessage(capabilityCard)
	return draft, owner, nil
}

// readAllowed applies the default-deny ACL used by every application read. A
// nil authorizer fails closed.
func (r *Reader) readAllowed(
	ctx context.Context, callerID, applicationID, ownerID string,
) (bool, error) {
	if r.Authorizer == nil {
		return false, nil
	}
	return r.Authorizer.Allow(ctx, policy.PrincipalRef{ID: callerID}, policy.ResourceRef{
		Type:    policy.ResourceApplication,
		ID:      applicationID,
		OwnerID: ownerID,
	}, policy.ActionRead)
}

// GetDraft returns the draft when the caller owns the application or holds an
// unexpired read grant. Everything else is ErrNotVisible (404), so the endpoint
// cannot enumerate ids.
func (r *Reader) GetDraft(
	ctx context.Context, callerID, applicationID string,
) (*Draft, error) {
	draft, ownerID, err := r.loadDraft(ctx, applicationID)
	if err != nil {
		return nil, err
	}
	allow, err := r.readAllowed(ctx, callerID, applicationID, ownerID)
	if err != nil {
		return nil, err
	}
	if !allow {
		return nil, ErrNotVisible
	}
	return draft, nil
}

// SaveDraft performs the optimistic-concurrency write.
//
// Order is structural: visibility (404) -> ownership (403) -> If-Match
// (428/412) -> body shape (422) -> CAS update. A caller that cannot read the
// draft learns nothing; a reader who is not the owner is told the write is
// forbidden (they already know the draft exists); a stale writer is told the
// current revision; only then can a body problem be reported. The update itself
// is conditional on the revision the caller proved it had seen, so two writers
// holding the same ETag cannot both succeed.
func (r *Reader) SaveDraft(
	ctx context.Context,
	callerID, applicationID, ifMatch string,
	input DraftInput,
) (*SaveResult, error) {
	draft, ownerID, err := r.loadDraft(ctx, applicationID)
	if err != nil {
		return nil, err
	}
	allow, err := r.readAllowed(ctx, callerID, applicationID, ownerID)
	if err != nil {
		return nil, err
	}
	if !allow {
		return nil, ErrNotVisible
	}
	if !strings.EqualFold(callerID, ownerID) {
		// A read grant is sufficient to read and insufficient to write. The
		// caller can already see the draft, so 403 reveals no new existence.
		return nil, ErrNotOwner
	}
	if strings.TrimSpace(ifMatch) == "" {
		return nil, &PreconditionRequiredError{}
	}
	claimedRevision, ok := ParseETag(ifMatch)
	if !ok || claimedRevision != draft.Revision {
		return nil, &PreconditionFailedError{
			CurrentRevision: draft.Revision,
			CurrentETag:     draft.ETag(),
		}
	}
	if err := ValidateDraftInput(input); err != nil {
		return nil, err
	}

	var result SaveResult
	err = r.DB.QueryRowContext(ctx, `
		UPDATE platform.application_drafts
		   SET definition         = $3::jsonb,
		       layout             = $4::jsonb,
		       capability_card    = $5::jsonb,
		       revision           = revision + 1,
		       execution_revision = execution_revision
		           + CASE
		                 WHEN definition IS DISTINCT FROM $3::jsonb
		                   OR capability_card IS DISTINCT FROM $5::jsonb
		                 THEN 1
		                 ELSE 0
		             END,
		       updated_at         = now()
		 WHERE application_id = $1
		   AND revision = $2
		 RETURNING revision, execution_revision`,
		applicationID, claimedRevision,
		string(input.Definition), string(input.Layout), string(input.CapabilityCard),
	).Scan(&result.Revision, &result.ExecutionRevision)
	if errors.Is(err, sql.ErrNoRows) {
		// A concurrent save won between the read and the conditional update.
		current, currentErr := r.currentRevision(ctx, applicationID)
		if currentErr != nil {
			return nil, currentErr
		}
		return nil, &PreconditionFailedError{
			CurrentRevision: current,
			CurrentETag:     FormatETag(current),
		}
	}
	if err != nil {
		return nil, fmt.Errorf("saving the draft: %w", err)
	}
	return &result, nil
}

// currentRevision re-reads the revision after a lost CAS race. A vanished row is
// ErrNotVisible, the same answer the first read would have given.
func (r *Reader) currentRevision(ctx context.Context, applicationID string) (int64, error) {
	var revision int64
	err := r.DB.QueryRowContext(ctx,
		`SELECT revision FROM platform.application_drafts WHERE application_id = $1`,
		applicationID,
	).Scan(&revision)
	if errors.Is(err, sql.ErrNoRows) {
		return 0, ErrNotVisible
	}
	if err != nil {
		return 0, fmt.Errorf("re-reading the draft revision: %w", err)
	}
	return revision, nil
}

// ValidateDraftInput performs the structural DraftInput validation the save
// endpoint promises: all three keys, objects (not arrays/null/strings), size
// caps, and the full capability-card shape for any non-empty card.
//
// It deliberately does NOT validate the DSL. PLAN §2 D6 and DESIGN §3 are
// explicit that a structurally valid but semantically invalid workflow can be
// saved; only debug/submit (T025/T027) must refuse it, through CheckExecutable.
func ValidateDraftInput(input DraftInput) error {
	if len(input.UnknownFields) > 0 {
		// Deterministic order so the reported field is stable across requests.
		unknown := append([]string(nil), input.UnknownFields...)
		sort.Strings(unknown)
		return &ValidationError{
			Field:   unknown[0],
			Message: "DraftInput accepts exactly definition, layout and capability_card",
		}
	}
	if err := validateObjectField(input.Definition, "definition", MaxDefinitionBytes); err != nil {
		return err
	}
	if err := validateObjectField(input.Layout, "layout", MaxLayoutBytes); err != nil {
		return err
	}
	if err := validateObjectField(input.CapabilityCard, "capability_card", MaxCapabilityCardBytes); err != nil {
		return err
	}
	return validateCapabilityCard(input.CapabilityCard)
}

// validateObjectField checks one DraftInput object field: present, within its
// cap, and a JSON object.
func validateObjectField(raw json.RawMessage, field string, maxBytes int) error {
	if len(bytes.TrimSpace(raw)) == 0 {
		return &ValidationError{Field: field, Message: field + " is required"}
	}
	if len(raw) > maxBytes {
		return &ValidationError{
			Field:   field,
			Message: fmt.Sprintf("%s must be at most %d bytes", field, maxBytes),
		}
	}
	trimmed := bytes.TrimSpace(raw)
	if len(trimmed) == 0 || trimmed[0] != '{' {
		return &ValidationError{Field: field, Message: field + " must be a JSON object"}
	}
	var object map[string]json.RawMessage
	if err := json.Unmarshal(trimmed, &object); err != nil || object == nil {
		return &ValidationError{Field: field, Message: field + " must be a JSON object"}
	}
	return nil
}

// validateCapabilityCard accepts exactly two states: {} (a new, not-yet-filled
// draft) and the complete six-key DraftInput shape with at least two examples.
// The empty value is the documented contract gap: contracts/openapi.json marks
// every card field required and examples minItems=2, so a fresh draft could not
// be represented at all otherwise. A partial card is refused instead of being
// rounded up or padded with fabricated strings.
func validateCapabilityCard(raw json.RawMessage) error {
	var card map[string]json.RawMessage
	if err := json.Unmarshal(raw, &card); err != nil {
		return &ValidationError{Field: "capability_card", Message: "capability_card must be a JSON object"}
	}
	if len(card) == 0 {
		return nil
	}
	for key := range card {
		if !containsString(draftCapabilityKeys, key) {
			return &ValidationError{
				Field:   "capability_card." + key,
				Message: "capability_card has an unknown field; the card must be {} or the full DraftInput shape",
			}
		}
	}
	for _, key := range draftCapabilityKeys {
		if _, present := card[key]; !present {
			return &ValidationError{
				Field:   "capability_card." + key,
				Message: "a non-empty capability_card requires every field (purpose, includes, excludes, required_inputs, expected_outputs, examples)",
			}
		}
	}
	var purpose string
	if bytes.Equal(bytes.TrimSpace(card["purpose"]), []byte("null")) {
		return &ValidationError{Field: "capability_card.purpose", Message: "purpose must be a string"}
	}
	if err := json.Unmarshal(card["purpose"], &purpose); err != nil {
		return &ValidationError{Field: "capability_card.purpose", Message: "purpose must be a string"}
	}
	for _, key := range []string{"includes", "excludes", "required_inputs", "expected_outputs", "examples"} {
		if _, err := decodeStringArray(card[key], "capability_card."+key); err != nil {
			return err
		}
	}
	examples, err := decodeStringArray(card["examples"], "capability_card.examples")
	if err != nil {
		return err
	}
	if len(examples) < 2 {
		return &ValidationError{
			Field:   "capability_card.examples",
			Message: "a non-empty capability_card requires at least 2 examples",
		}
	}
	return nil
}

// decodeStringArray decodes one card array and keeps null distinguishable from
// an empty array (the contract requires an array, not null).
func decodeStringArray(raw json.RawMessage, field string) ([]string, error) {
	if bytes.Equal(bytes.TrimSpace(raw), []byte("null")) {
		return nil, &ValidationError{Field: field, Message: field + " must be an array of strings"}
	}
	var values []string
	if err := json.Unmarshal(raw, &values); err != nil {
		return nil, &ValidationError{Field: field, Message: field + " must be an array of strings"}
	}
	if values == nil {
		values = []string{}
	}
	return values, nil
}

func containsString(values []string, wanted string) bool {
	for _, value := range values {
		if value == wanted {
			return true
		}
	}
	return false
}

// ExecutableError is one located reason a draft cannot be executed. It is the
// shape T025 (validate) and T027 (debug) turn into their response errors; T023
// exposes and tests it but implements neither endpoint.
type ExecutableError struct {
	NodeID  string `json:"node_id,omitempty"`
	Code    string `json:"code"`
	Pointer string `json:"pointer"`
	Message string `json:"message"`
}

// CheckExecutable runs the P05-derived validator over a draft's definition and
// returns located errors. It is a pure function of the stored draft: no
// database, no policy decision.
//
// The second return value is an infrastructure error (the embedded rule table
// failed to compile); an invalid workflow is not an error — it is the first
// return value. T023 does not call it from an HTTP endpoint (validate/debug are
// T025/T027), which is why the ticket records those acceptance items as partial.
func CheckExecutable(draft Draft) ([]ExecutableError, error) {
	definition := bytes.TrimSpace(draft.Definition)
	if len(definition) == 0 || definition[0] != '{' {
		return []ExecutableError{{
			Code:    "bad_document",
			Pointer: "",
			Message: "the definition must be a JSON object",
		}}, nil
	}
	var document map[string]any
	if err := json.Unmarshal(definition, &document); err != nil || document == nil {
		return []ExecutableError{{
			Code:    "bad_document",
			Pointer: "",
			Message: "the definition must be a JSON object",
		}}, nil
	}
	rules, err := workflow.DefaultRules()
	if err != nil {
		return nil, fmt.Errorf("loading the workflow rules: %w", err)
	}
	result, err := workflow.Validate(document, rules, workflow.ValidationOptions{})
	if err != nil {
		return nil, fmt.Errorf("validating the draft definition: %w", err)
	}
	located := make([]ExecutableError, 0, len(result.Problems))
	for _, problem := range result.Problems {
		message := rules.ReasonMeaning(problem.Code)
		if message == "" {
			message = "the definition violates the " + problem.Code + " rule"
		}
		located = append(located, ExecutableError{
			NodeID:  nodeIDAt(document, problem.Pointer),
			Code:    problem.Code,
			Pointer: problem.Pointer,
			Message: message,
		})
	}
	return located, nil
}

// nodeIDAt resolves a `/nodes/<index>/...` pointer to the node's id. Other
// pointers (document-level errors such as a cycle) have no single node, so the
// node_id stays empty rather than being guessed.
func nodeIDAt(document map[string]any, pointer string) string {
	const prefix = "/nodes/"
	if !strings.HasPrefix(pointer, prefix) {
		return ""
	}
	rest := pointer[len(prefix):]
	indexText, _, _ := strings.Cut(rest, "/")
	index, err := strconv.Atoi(indexText)
	if err != nil || index < 0 {
		return ""
	}
	nodes, ok := document["nodes"].([]any)
	if !ok || index >= len(nodes) {
		return ""
	}
	node, ok := nodes[index].(map[string]any)
	if !ok {
		return ""
	}
	id, _ := node["id"].(string)
	return id
}
