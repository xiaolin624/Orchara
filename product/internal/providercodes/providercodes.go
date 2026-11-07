// Package providercodes is the one vocabulary of stable model-provider failure
// codes (F06/T122; added by the B2 review fix H3, "Shotgun Surgery").
//
// The codes cross three seams that used to spell them out independently:
//
//   - the run-scoped capability ledger (internal/capabilities),
//   - the job-scoped internal model endpoint and its HTTP answer (internal/api),
//   - the background handler retry table (internal/background/handlers).
//
// Keeping the strings in this leaf package means a new provider failure cannot
// leave one seam recognising a code the others do not know about. A Go test in
// internal/capabilities asserts that every typed modelproxy error maps to a
// member of Vocabulary and that every member is reachable, so an added or
// renamed code fails a build instead of drifting.
package providercodes

const (
	// NotConfigured means the deployment has no usable endpoint or credential.
	NotConfigured = "provider_not_configured"
	// AdapterUnavailable means the registered protocol has no compiled adapter.
	AdapterUnavailable = "adapter_unavailable"
	// DispatchAdapterUnavailable is the job-scoped internal endpoint's historical
	// spelling of AdapterUnavailable (the T024 job retry table). It is derived
	// from AdapterUnavailable, never a second literal, so the two surfaces cannot
	// diverge.
	DispatchAdapterUnavailable = "provider_" + AdapterUnavailable
	// Transient means a retryable provider outage (429, 408, 5xx, timeout).
	Transient = "provider_transient"
	// DestinationNotApproved means the candidate names an endpoint the deployer
	// catalog did not register.
	DestinationNotApproved = "provider_destination_not_approved"
	// ResponseTooLarge means the answer exceeded the adapter's protocol limit.
	ResponseTooLarge = "provider_response_too_large"
	// ResponseInvalid means the answer was not the registered protocol.
	ResponseInvalid = "provider_response_invalid"
	// CallInvalid means the platform could not map the call to the protocol.
	CallInvalid = "provider_call_invalid"
	// PermissionDenied is a provider 401/403.
	PermissionDenied = "provider_permission_denied"
	// ModelNotFound is a provider 404.
	ModelNotFound = "provider_model_not_found"
	// RequestRejected is a terminal provider 4xx.
	RequestRejected = "provider_request_rejected"
	// Redirected is a provider 3xx; it is never followed.
	Redirected = "provider_redirected"
	// CandidateUnavailable marks a candidate row that cannot be dispatched.
	CandidateUnavailable = "candidate_unavailable"
	// Unclassified is the fallback an unrecognised provider failure records. It
	// is deliberately not a provider claim: it means the platform could not
	// classify the failure.
	Unclassified = "provider_error"
)

// Vocabulary is the closed list of codes a provider failure may carry. It is
// the drift guard's input: every typed provider error must map into this list,
// and every member must be reachable from at least one mapping.
var Vocabulary = []string{
	NotConfigured,
	AdapterUnavailable,
	DispatchAdapterUnavailable,
	Transient,
	DestinationNotApproved,
	ResponseTooLarge,
	ResponseInvalid,
	CallInvalid,
	PermissionDenied,
	ModelNotFound,
	RequestRejected,
	Redirected,
	CandidateUnavailable,
	Unclassified,
}

// Known reports whether code is a member of the provider vocabulary.
func Known(code string) bool {
	for _, candidate := range Vocabulary {
		if candidate == code {
			return true
		}
	}
	return false
}
