// Package requests owns the F08 conversation/request aggregate: the conversation
// read surface, the one ordered logical slot per conversation, idempotent request
// submission, the explicit fixed-version dispatch path, cancellation and the
// user-visible message projection.
//
// The queue invariant is one sentence: the earliest unfinished request in a
// conversation holds the logical slot, and every other ordinary request waits in
// `queued`. "Unfinished" is
//
//	state NOT IN ('cancelled','failed')
//	AND (no Run OR the Run is not in a terminal state)
//
// and the slot is
//
//	a live top-level Run in the conversation
//	OR a pre-dispatch request in routing/awaiting_input/awaiting_selection.
//
// Both halves are needed because a dispatched request keeps its state after its
// Run ends (STATE-MACHINES.md §17). Two database facts back the invariant
// (`one_active_top_run_per_conversation`, `one_active_request_per_conversation`)
// and every mutating operation locks the conversation row first, so a concurrent
// submit loses the race by becoming `queued`, never by creating a second slot
// holder. AdvanceConversationQueueTx is the one seam T079/T082 call after they
// decide a request's fate.
package requests

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"strings"
	"time"
	"unicode/utf8"

	"orchara/platform/internal/background"
	"orchara/platform/internal/catalog"
	"orchara/platform/internal/jcs"
	"orchara/platform/internal/objectstore"
	"orchara/platform/internal/uuid"
)

// Request states: the contract's closed enum.
const (
	StateQueued            = "queued"
	StateRouting           = "routing"
	StateAwaitingInput     = "awaiting_input"
	StateAwaitingSelection = "awaiting_selection"
	StateDispatched        = "dispatched"
	StateCancelled         = "cancelled"
	StateFailed            = "failed"
)

// Interaction states: the contract's closed enum.
const (
	InteractionPending     = "pending"
	InteractionAnswered    = "answered"
	InteractionExpired     = "expired"
	InteractionInvalidated = "invalidated"
)

// Artifact purposes created by this package. Both are sealed, ready artifacts;
// the purpose is a non-contract diagnostic label, never an authorization input.
const (
	PurposeRequestInput   = "request_input"
	PurposeHandoffSummary = "handoff_summary"
)

// Contract limits (contracts/openapi.json RequestInput).
const (
	maxMessageRunes = 32000
	maxArtifacts    = 5
	maxSummaryRunes = 8000
)

// SubmitPath is the contract path idempotency receipts are scoped to.
const SubmitPath = "/api/v1/requests"

// InteractionTTL is how long a pending input/selection interaction waits before
// the queue admits the next request. The spec fixes no number; 15 minutes is the
// documented deployment default (T074 report/ADR). T079 creates the interactions
// with this TTL; this package expires them lazily before computing the slot.
const InteractionTTL = 15 * time.Minute

// Error is one located refusal in contract-envelope terms. Handlers map it
// directly; no handler invents a status or code of its own.
type Error struct {
	Status  int
	Code    string
	Message string
	Details map[string]any
}

func (e *Error) Error() string { return e.Message }

func failure(status int, code, message string) *Error {
	return &Error{Status: status, Code: code, Message: message}
}

func failureWithDetails(status int, code, message string, details map[string]any) *Error {
	return &Error{Status: status, Code: code, Message: message, Details: details}
}

// AsError reports whether err is a located refusal from this package.
func AsError(err error) (*Error, bool) {
	var typed *Error
	if errors.As(err, &typed) {
		return typed, true
	}
	return nil, false
}

// ErrIdempotencyConflict is the sentinel behind IdempotencyConflictError.
var ErrIdempotencyConflict = errors.New("the Idempotency-Key was used for a different request")

// IdempotencyConflictError means the key exists with a different canonical body.
type IdempotencyConflictError struct{}

func (e *IdempotencyConflictError) Error() string {
	return "this Idempotency-Key was already used for a different request"
}

func (e *IdempotencyConflictError) Unwrap() error { return ErrIdempotencyConflict }

// Service is the conversation/request aggregate over one database handle.
type Service struct {
	DB      *sql.DB
	Catalog *catalog.Reader
	Objects *objectstore.Store
	// CancelRun is the existing F03/T036 cancel path, injected so this package
	// does not depend on internal/execution's concrete service for cancellation.
	CancelRun func(ctx context.Context, callerID, runID string) error
}

// New builds the aggregate. Catalog and Objects are required for a submit that
// dispatches a fixed version; a nil object store fails closed with a 503 rather
// than creating a request whose input bytes do not exist.
func New(db *sql.DB, cat *catalog.Reader, objects *objectstore.Store) *Service {
	return &Service{DB: db, Catalog: cat, Objects: objects}
}

// IdempotencyScope is the receipt primary key (0014). Path is the concrete
// request path, so the same key cannot be borrowed by another operation.
type IdempotencyScope struct {
	PrincipalID string
	Method      string
	Path        string
	Key         string
}

// HandoffInput is the contract's confirmed handover object.
type HandoffInput struct {
	SourceRunIDs []string
	ArtifactIDs  []string
	Summary      string
	Confirmed    bool
}

// SubmitInput is one validated RequestInput body plus its canonical digest.
type SubmitInput struct {
	ConversationID       string
	Message              string
	ArtifactIDs          []string
	ApplicationVersionID string
	Handoff              *HandoffInput
	RequestSHA256        string
}

// Conversation is the contract Conversation projection.
type Conversation struct {
	ID        string
	CreatedAt time.Time
	Revision  int64
}

// Interaction is the non-contract `Request.interaction` projection. The contract
// leaves the object open; T079 fills kind/proposal.
type Interaction struct {
	ID            string
	CorrelationID string
	Kind          string
	ExpiresAt     time.Time
	Proposal      json.RawMessage
}

// Request is exactly the contract Request schema. ConversationID and
// RequestedVersionID are in-memory facts for headers/dispatch, never serialised.
type Request struct {
	ID          string           `json:"id"`
	State       string           `json:"state"`
	RunID       *string          `json:"run_id"`
	Interaction *InteractionView `json:"interaction"`
	ErrorCode   *string          `json:"error_code"`

	ConversationID     string    `json:"-"`
	RequestedVersionID string    `json:"-"`
	Revision           int64     `json:"-"`
	CreatedAt          time.Time `json:"-"`
}

// InteractionView is the JSON shape inside Request.interaction.
type InteractionView struct {
	InteractionID string          `json:"interaction_id"`
	CorrelationID string          `json:"correlation_id"`
	Kind          string          `json:"kind"`
	ExpiresAt     string          `json:"expires_at"`
	Proposal      json.RawMessage `json:"proposal,omitempty"`
}

// SubmitOutcome is what the handler returns: the Request body and whether it was
// replayed from a committed idempotency receipt.
type SubmitOutcome struct {
	Request  *Request
	Replayed bool
}

// MessageView is exactly the contract MessageView schema.
type MessageView struct {
	ID             string    `json:"id"`
	Role           string    `json:"role"`
	RequestID      string    `json:"request_id"`
	RunID          *string   `json:"run_id"`
	BodyArtifactID *string   `json:"body_artifact_id"`
	CreatedAt      time.Time `json:"created_at"`
	Kind           string    `json:"kind"`
}

// now is the package clock seam. Everything that compares timestamps inside SQL
// uses PostgreSQL's now(), so one process clock never races the database.
func now() time.Time { return time.Now().UTC() }

// ---------------------------------------------------------------------------
// request body parsing (the API hands the raw top-level fields here, like T027)
// ---------------------------------------------------------------------------

var submitFields = map[string]bool{
	"conversation_id":        true,
	"message":                true,
	"artifact_ids":           true,
	"application_version_id": true,
	"handoff":                true,
}

var handoffFields = map[string]bool{
	"source_run_ids": true,
	"artifact_ids":   true,
	"summary":        true,
	"confirmed":      true,
}

// ParseSubmit validates the raw top-level fields of POST /api/v1/requests. It
// returns located 422 errors (never a generic decode failure) so the client can
// point at the offending member, matching the debug/validate vocabulary.
func ParseSubmit(fields map[string]json.RawMessage) (*SubmitInput, *Error) {
	unknown := make([]string, 0)
	for key := range fields {
		if !submitFields[key] {
			unknown = append(unknown, key)
		}
	}
	if len(unknown) > 0 {
		return nil, failureWithDetails(422, "validation_failed",
			"the request body contains unknown fields", map[string]any{"fields": sorted(unknown)})
	}

	input := &SubmitInput{}
	conversationID, err := stringValue(fields["conversation_id"])
	if err != nil || !uuid.Valid(conversationID) {
		return nil, located("conversation_id", "a valid conversation_id UUID is required")
	}
	input.ConversationID = strings.ToLower(conversationID)

	message, err := stringValue(fields["message"])
	if err != nil {
		return nil, located("message", "message must be a string")
	}
	if utf8.RuneCountInString(message) < 1 {
		return nil, located("message", "message must not be empty")
	}
	if utf8.RuneCountInString(message) > maxMessageRunes {
		return nil, located("message", "message must be at most 32000 characters")
	}
	input.Message = message

	rawArtifacts, ok := fields["artifact_ids"]
	if !ok {
		return nil, located("artifact_ids", "artifact_ids is required (send [] for no attachments)")
	}
	artifacts, parseErr := uuidList(rawArtifacts, "artifact_ids", maxArtifacts)
	if parseErr != nil {
		return nil, parseErr
	}
	input.ArtifactIDs = artifacts

	if raw, present := fields["application_version_id"]; present {
		versionID, err := stringValue(raw)
		if err != nil || !uuid.Valid(versionID) {
			return nil, located("application_version_id", "application_version_id must be a UUID")
		}
		input.ApplicationVersionID = strings.ToLower(versionID)
	}

	if raw, present := fields["handoff"]; present {
		handoff, parseErr := parseHandoff(raw)
		if parseErr != nil {
			return nil, parseErr
		}
		input.Handoff = handoff
	}

	digest, err := submitDigest(input)
	if err != nil {
		return nil, failure(422, "validation_failed", "the request body cannot be canonicalised: "+err.Error())
	}
	input.RequestSHA256 = digest
	return input, nil
}

func parseHandoff(raw json.RawMessage) (*HandoffInput, *Error) {
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(raw, &fields); err != nil {
		return nil, located("handoff", "handoff must be an object")
	}
	unknown := make([]string, 0)
	for key := range fields {
		if !handoffFields[key] {
			unknown = append(unknown, key)
		}
	}
	if len(unknown) > 0 {
		return nil, located("handoff", "handoff contains unknown members: "+strings.Join(sorted(unknown), ", "))
	}
	handoff := &HandoffInput{}
	var err *Error
	// source_run_ids is required and must be a non-empty array: the user must name
	// at least one concrete Run of this conversation, never "all history".
	rawSources, present := fields["source_run_ids"]
	if !present {
		return nil, located("handoff.source_run_ids", "handoff.source_run_ids is required")
	}
	handoff.SourceRunIDs, err = uuidList(rawSources, "handoff.source_run_ids", 50)
	if err != nil {
		return nil, err
	}
	if len(handoff.SourceRunIDs) == 0 {
		return nil, located("handoff.source_run_ids", "handoff must name at least one source Run")
	}
	rawArtifacts, present := fields["artifact_ids"]
	if !present {
		return nil, located("handoff.artifact_ids", "handoff.artifact_ids is required (send [] for none)")
	}
	handoff.ArtifactIDs, err = uuidList(rawArtifacts, "handoff.artifact_ids", 50)
	if err != nil {
		return nil, err
	}
	summary, summaryErr := stringValue(fields["summary"])
	if summaryErr != nil {
		return nil, located("handoff.summary", "handoff.summary must be a string")
	}
	if utf8.RuneCountInString(summary) > maxSummaryRunes {
		return nil, located("handoff.summary", "handoff.summary must be at most 8000 characters")
	}
	handoff.Summary = summary
	rawConfirmed, present := fields["confirmed"]
	if !present {
		return nil, located("handoff.confirmed", "handoff.confirmed is required")
	}
	var confirmed bool
	if err := json.Unmarshal(rawConfirmed, &confirmed); err != nil {
		return nil, located("handoff.confirmed", "handoff.confirmed must be true")
	}
	if !confirmed {
		return nil, failureWithDetails(422, "handoff_not_confirmed",
			"the handover summary and attachments must be explicitly confirmed before they cross an application boundary",
			map[string]any{"field": "handoff.confirmed"})
	}
	handoff.Confirmed = true
	return handoff, nil
}

// submitDigest is the canonical identity of one RequestInput body. Sets are
// sorted and the optional members are omitted when absent, so the same intention
// always hashes the same bytes (the debug path's T027 rule).
func submitDigest(input *SubmitInput) (string, error) {
	canonical := map[string]any{
		"conversation_id": input.ConversationID,
		"message":         input.Message,
		"artifact_ids":    input.ArtifactIDs,
	}
	if input.ApplicationVersionID != "" {
		canonical["application_version_id"] = input.ApplicationVersionID
	}
	if input.Handoff != nil {
		canonical["handoff"] = map[string]any{
			"source_run_ids": input.Handoff.SourceRunIDs,
			"artifact_ids":   input.Handoff.ArtifactIDs,
			"summary":        input.Handoff.Summary,
			"confirmed":      true,
		}
	}
	text, err := json.Marshal(canonical)
	if err != nil {
		return "", err
	}
	// JCS parses JSON values itself; encoding/json only supplies the ordinary
	// Go-to-JSON shape (the same two-step the debug path uses for its input).
	canonicalBytes, err := jcs.CanonicalizeBytes(text)
	if err != nil {
		return "", err
	}
	return jcs.SHA256Hex(canonicalBytes), nil
}

func stringValue(raw json.RawMessage) (string, error) {
	if len(raw) == 0 {
		return "", errors.New("missing")
	}
	var value string
	if err := json.Unmarshal(raw, &value); err != nil {
		return "", err
	}
	return value, nil
}

// uuidList parses an array of UUID strings, lower-cases and sorts it (the
// canonical request digest sorts sets) and refuses duplicates beyond max entries.
// An empty array is valid; a missing/!array value is a located 422.
func uuidList(raw json.RawMessage, field string, max int) ([]string, *Error) {
	if len(raw) == 0 {
		return nil, located(field, field+" is required")
	}
	var values []string
	if err := json.Unmarshal(raw, &values); err != nil {
		return nil, located(field, field+" must be an array of UUID strings")
	}
	if len(values) > max {
		return nil, located(field, fmt.Sprintf("%s must contain at most %d entries", field, max))
	}
	seen := make(map[string]bool, len(values))
	normalized := make([]string, 0, len(values))
	for index, value := range values {
		if !uuid.Valid(value) {
			return nil, located(fmt.Sprintf("%s/%d", field, index), "must be a UUID")
		}
		lower := strings.ToLower(value)
		if seen[lower] {
			return nil, located(fmt.Sprintf("%s/%d", field, index), "duplicate entry")
		}
		seen[lower] = true
		normalized = append(normalized, lower)
	}
	sortStrings(normalized)
	return normalized, nil
}

func located(field, message string) *Error {
	return failureWithDetails(422, "validation_failed", message, map[string]any{"field": field})
}

func sorted(values []string) []string {
	out := append([]string(nil), values...)
	sort.Strings(out)
	return out
}

func sortStrings(values []string) { sort.Strings(values) }

// requireUUID converts a caller/session id into the lower-case UUID form every
// predicate compares against.
func requireUUID(value string) (string, bool) {
	trimmed := strings.TrimSpace(value)
	if !uuid.Valid(trimmed) {
		return "", false
	}
	return strings.ToLower(trimmed), true
}

// enqueueRouteJob records the one durable route_request intention for a request.
// The dedup key is the request id, so a retried promotion cannot reset the
// control budget (background_jobs UNIQUE(kind, dedup_key)).
func (s *Service) enqueueRouteJobTx(ctx context.Context, tx *sql.Tx, requestID, ownerID string, revision int64) error {
	_, _, err := background.EnqueueTx(ctx, tx, background.EnqueueParams{
		Kind:             background.KindRouteRequest,
		TargetID:         requestID,
		OwnerID:          ownerID,
		DedupKey:         requestID,
		ExpectedRevision: &revision,
	})
	if err != nil {
		return fmt.Errorf("enqueueing the route_request job: %w", err)
	}
	return nil
}
