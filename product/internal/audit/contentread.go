package audit

// F11/T073: the one helper every diagnostic content read uses.
//
// A content read is the most sensitive row this platform writes: it records who
// looked at another user's body, under which grant, for which purpose, and at
// which run/attempt/operation. It must therefore be a *closed typed* record, not a
// `map[string]any` a caller can stuff a body into. The caller passes positional,
// validated fields; the helper builds the metadata map itself and refuses the
// write when any field is out of its documented shape.
//
// The metadata whitelist is exactly these ten keys (missing correlations are
// JSON null, never omitted, so the schema is stable for the audit reader):
//
//	grant_id, purpose, run_id, resource_type, resource_id,
//	operation_id, attempt_token, request_id, outcome, error_code
//
// No content, no query text, no path, no provider body and no credential can be
// expressed through this shape.

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"regexp"
)

// ActionContentRead is the audit action of every diagnostic content read. The
// resource type/id of the audit row are the object's; the grant and the
// correlation live in the metadata.
const ActionContentRead = "diagnostic.content_read"

// Outcomes of one content-read attempt. "refused" is a policy refusal before any
// byte was opened; "failed" is an infrastructure failure after the policy allowed
// the read. Both are audit evidence, never content.
const (
	OutcomeServed  = "served"
	OutcomeRefused = "refused"
	OutcomeFailed  = "failed"
)

var (
	// The same bounded purpose vocabulary as internal/diagnostics; duplicated here
	// so the audit package keeps depending on database/sql only (no import cycle).
	contentReadTokenPattern = regexp.MustCompile(`^[a-z][a-z0-9_-]{0,63}$`)
	contentReadTypePattern  = regexp.MustCompile(`^[a-z][a-z0-9_]{0,31}$`)
	contentReadIDPattern    = regexp.MustCompile(`^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$`)
	contentReadReqIDPattern = regexp.MustCompile(`^[A-Za-z0-9._:-]{1,128}$`)
)

// ErrInvalidContentRead is the fail-closed refusal: a malformed content-read
// record is never written, because writing it could smuggle a body into the audit
// trail or produce an unattributable row.
var ErrInvalidContentRead = errors.New("invalid diagnostic content-read audit record")

// ContentRead is one typed content-read record. Empty operation/attempt/request
// fields are recorded as null; actor is nil for a system read (none exists today:
// every diagnostic read is an operator action).
type ContentRead struct {
	ActorID      *string
	GrantID      string
	Purpose      string
	RunID        string
	ResourceType string
	ResourceID   string
	OperationID  string
	AttemptToken string
	RequestID    string
	Outcome      string
	ErrorCode    string
}

// RecordContentRead validates and appends one content-read audit row inside the
// caller's transaction. The caller must not serve a byte when this returns an
// error: an unrecorded read is not allowed to happen.
func RecordContentRead(ctx context.Context, tx *sql.Tx, read ContentRead) error {
	if err := ValidateContentRead(read); err != nil {
		return err
	}
	metadata := contentReadMetadata(read)
	return Record(
		ctx, tx, read.ActorID, ActionContentRead, read.ResourceType, &read.ResourceID, metadata,
	)
}

// ValidateContentRead is the closed shape check. It is exported so a unit test can
// prove the fail-closed matrix without a database.
func ValidateContentRead(read ContentRead) error {
	if read.ActorID != nil && !contentReadIDPattern.MatchString(*read.ActorID) {
		return fmt.Errorf("%w: actor_id", ErrInvalidContentRead)
	}
	if !contentReadIDPattern.MatchString(read.GrantID) {
		return fmt.Errorf("%w: grant_id", ErrInvalidContentRead)
	}
	if !contentReadIDPattern.MatchString(read.RunID) {
		return fmt.Errorf("%w: run_id", ErrInvalidContentRead)
	}
	if !contentReadTokenPattern.MatchString(read.Purpose) {
		return fmt.Errorf("%w: purpose", ErrInvalidContentRead)
	}
	if !contentReadTypePattern.MatchString(read.ResourceType) {
		return fmt.Errorf("%w: resource_type", ErrInvalidContentRead)
	}
	if !contentReadIDPattern.MatchString(read.ResourceID) {
		return fmt.Errorf("%w: resource_id", ErrInvalidContentRead)
	}
	if read.OperationID != "" && !contentReadIDPattern.MatchString(read.OperationID) {
		return fmt.Errorf("%w: operation_id", ErrInvalidContentRead)
	}
	if read.AttemptToken != "" && !contentReadIDPattern.MatchString(read.AttemptToken) {
		return fmt.Errorf("%w: attempt_token", ErrInvalidContentRead)
	}
	if read.RequestID != "" && !contentReadReqIDPattern.MatchString(read.RequestID) {
		return fmt.Errorf("%w: request_id", ErrInvalidContentRead)
	}
	switch read.Outcome {
	case OutcomeServed:
		if read.ErrorCode != "" {
			return fmt.Errorf("%w: a served read has no error_code", ErrInvalidContentRead)
		}
	case OutcomeRefused, OutcomeFailed:
		if !contentReadTokenPattern.MatchString(read.ErrorCode) {
			return fmt.Errorf("%w: error_code", ErrInvalidContentRead)
		}
	default:
		return fmt.Errorf("%w: outcome", ErrInvalidContentRead)
	}
	return nil
}

// contentReadMetadata builds the one map shape. It is the only place a content
// read's metadata is assembled.
func contentReadMetadata(read ContentRead) map[string]any {
	nullable := func(value string) any {
		if value == "" {
			return nil
		}
		return value
	}
	return map[string]any{
		"grant_id":      read.GrantID,
		"purpose":       read.Purpose,
		"run_id":        read.RunID,
		"resource_type": read.ResourceType,
		"resource_id":   read.ResourceID,
		"operation_id":  nullable(read.OperationID),
		"attempt_token": nullable(read.AttemptToken),
		"request_id":    nullable(read.RequestID),
		"outcome":       read.Outcome,
		"error_code":    nullable(read.ErrorCode),
	}
}
