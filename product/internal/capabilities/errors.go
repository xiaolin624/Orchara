// Package capabilities is the platform's run-scoped controlled-capability
// boundary (F06/T041).
//
// Rules fixed by IMPLEMENTATION-DETAILS §2/§5 and the T041 brief:
//
//   - A worker never calls a model or a tool itself. It names the capability
//     version it believes it owns (a model profile version or a tool version
//     that the run's sealed execution manifest listed), one operation id and a
//     logical key; the server authorizes, meters and seals.
//   - Authorization happens entirely before a provider call: the run must be
//     running with a live attempt, the sealed manifest bytes must hash to the
//     recorded digest, and the capability version must be in the manifest's
//     model_profile_versions or tool_artifacts. An id in both sets is refused
//     (capability_kind_ambiguous) rather than guessed.
//   - Every logical call is one platform.operations row and every physical
//     dispatch is one platform.operation_attempts row. The Go side is the only
//     retry authority: a transient provider error may retry at most twice (three
//     physical attempts at most), schema/permission/validation errors never
//     retry. The worker adapter has no hidden retry of its own.
//   - Model responses are validated here (tool-call ids/names/arguments and the
//     optional structured response) and only then sealed as a receipt artifact.
//     A provider payload is never returned to the worker unvalidated.
//   - Tool execution belongs to the worker (T043). The server hands out a
//     scoped grant (operation id + params hash + tool version) and the worker
//     reports back through the non-contract capability-results endpoint. A tool
//     operation is only `succeeded` once a ready artifact owned by the run (or
//     the server-sealed reported data) exists.
//   - The stub provider is labeled stub everywhere: the projection carries
//     stub=true and the ledger read surfaces it. F06/T122 compiles the real
//     OpenAI-compatible chat-completions adapter; a real provider that is
//     unconfigured, names an unapproved destination or speaks another protocol
//     fails closed (provider_not_configured / provider_destination_not_approved /
//     adapter_unavailable) before any ledger row exists.
package capabilities

import (
	"errors"
	"fmt"
	"strings"

	"orchara/platform/internal/providercodes"
)

// Stable error codes. They are the vocabulary the HTTP layer maps to contract
// statuses and the owner-facing ledger shows (never a provider payload).
//
// The provider codes are aliases of internal/providercodes, the single source
// shared with the job-scoped internal endpoint and the background retry table
// (F06/T122; B2 review fix H3).
const (
	CodeRunNotFound             = "run_not_found"
	CodeRunNotRunning           = "run_not_running"
	CodeRunCancelled            = "run_cancelled"
	CodeStaleAttempt            = "stale_attempt"
	CodeCapabilityNotAllowed    = "capability_not_allowed"
	CodeCapabilityKindAmbiguous = "capability_kind_ambiguous"
	CodeKindUnsupported         = "capability_kind_unsupported"
	CodeOperationConflict       = "operation_conflict"
	CodeOperationNotFound       = "operation_not_found"
	CodeOperationInProgress     = "operation_in_progress"
	CodeOperationNotDispatching = "operation_not_dispatching"
	CodeModelUnavailable        = "model_profile_unavailable"
	CodeToolUnavailable         = "tool_not_available"
	CodeResultArtifactInvalid   = "result_artifact_invalid"
	CodeManifestUnavailable     = "manifest_unavailable"
	CodeProviderNotConfigured   = providercodes.NotConfigured
	CodeAdapterUnavailable      = providercodes.AdapterUnavailable
	CodeProviderTransient       = providercodes.Transient
	CodeProviderResponseInvalid = providercodes.ResponseInvalid
	// F06/T122 provider protocol vocabulary: the destination is not one the
	// deployer registered, or the provider answer exceeded the adapter's 8 MiB
	// protocol limit (the existing provider_response_invalid covers malformed
	// chat-completions and provider status refusals below).
	CodeProviderDestinationNotApproved = providercodes.DestinationNotApproved
	CodeProviderResponseTooLarge       = providercodes.ResponseTooLarge
	// CodeCallerCancelled is the stable ledger code for a capability call whose
	// caller went away (client disconnect or request deadline). It is a
	// cancellation, never a provider claim: the attempt is closed and its
	// reservation released, but the failure is not classified as a provider
	// error and never retried or failed over (B2 review fix H2). It is the
	// non-stream analogue of CodeStreamCancelled.
	CodeCallerCancelled = "caller_cancelled"
	CodeParamsInvalid   = "validation_failed"
	CodeInternal        = "internal_error"
	// F06/T046 budget vocabulary.
	CodeBudgetExhausted        = "budget_exhausted"
	CodeBudgetCurrencyMismatch = "budget_currency_mismatch"
	CodeBudgetPriceInvalid     = "budget_price_invalid"
	CodeUsageNotReserved       = "usage_not_reserved"
	// F06/T050 approval-resume vocabulary (review2 P1-4): an authorized write
	// whose approval row is missing or no longer usable fails closed.
	CodeApprovalUnavailable = "approval_unavailable"
)

// Error is a capability failure with a stable HTTP status and code. Field is
// set for one located validation problem and becomes the documented
// validation_failed details entry; Details carries the typed budget facts
// (remaining amount/currency) the caller needs. Message must never contain a
// provider body, URL or credential.
type Error struct {
	Status  int
	Code    string
	Message string
	Field   string
	Details map[string]any
}

func (e *Error) Error() string {
	if e.Field != "" {
		return e.Code + ": " + e.Field + ": " + e.Message
	}
	return e.Code + ": " + e.Message
}

// AsError extracts a *Error. It is a convenience for the HTTP mapping. A typed
// nil *Error (a successful call converted to the error interface) is not an
// error and reports false.
func AsError(err error) (*Error, bool) {
	var capabilityError *Error
	if errors.As(err, &capabilityError) && capabilityError != nil {
		return capabilityError, true
	}
	return nil, false
}

func capFailure(status int, code, message string) *Error {
	return &Error{Status: status, Code: code, Message: message}
}

func validationFailure(field, message string) *Error {
	return &Error{
		Status:  422,
		Code:    CodeParamsInvalid,
		Message: message,
		Field:   field,
	}
}

// safeCode bounds a worker-reported error code: lowercase letters, digits and
// underscore. It is a closed shape so a provider body can never be smuggled
// into the ledger through the result endpoint.
func safeCode(value string) (string, error) {
	value = strings.TrimSpace(value)
	if value == "" || len(value) > 128 {
		return "", fmt.Errorf("error_code must be 1..128 characters")
	}
	for _, char := range value {
		if (char < 'a' || char > 'z') && (char < '0' || char > '9') && char != '_' {
			return "", fmt.Errorf("error_code must be lowercase letters, digits and underscore")
		}
	}
	return value, nil
}
