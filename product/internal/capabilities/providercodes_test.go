package capabilities

// F06/T122 B2 review fixes H2 and H3.
//
// H3 (Shotgun Surgery): the provider failure vocabulary is single-sourced in
// internal/providercodes. This file asserts that every typed modelproxy error
// maps to exactly that vocabulary and that no capability-only fallback silently
// becomes provider_error for a known failure.
//
// H2 (caller cancellation): a cancelled request/deadline is a cancellation, not
// a provider failure. The classification must short-circuit before the retry
// loop, must not be recorded as provider_error and must never be retried or
// failed over.

import (
	"context"
	"errors"
	"testing"
	"time"

	"orchara/platform/internal/modelproxy"
	"orchara/platform/internal/providercodes"
)

// TestProviderCodeVocabularyIsSingleSourced is the drift guard for H3: every
// provider error the adapter can return has one stable code from
// internal/providercodes, and every vocabulary member is reachable from a
// mapping (adding/renaming a code without updating a seam fails here).
func TestProviderCodeVocabularyIsSingleSourced(t *testing.T) {
	status := func(code int) error { return &modelproxy.ProviderStatusError{StatusCode: code} }
	cases := []struct {
		name string
		err  error
		want string
	}{
		{"not configured", modelproxy.ErrNotConfigured, providercodes.NotConfigured},
		{"adapter unavailable", modelproxy.ErrAdapterUnavailable, providercodes.AdapterUnavailable},
		{"destination not approved", modelproxy.ErrDestinationUnapproved, providercodes.DestinationNotApproved},
		{"response invalid", modelproxy.ErrResponseInvalid, providercodes.ResponseInvalid},
		{"response too large", modelproxy.ErrResponseTooLarge, providercodes.ResponseTooLarge},
		{"call invalid", modelproxy.ErrCallInvalid, providercodes.CallInvalid},
		{"candidate unavailable", modelproxy.ErrCandidateUnavailable, providercodes.CandidateUnavailable},
		{"provider 401", status(401), providercodes.PermissionDenied},
		{"provider 403", status(403), providercodes.PermissionDenied},
		{"provider 404", status(404), providercodes.ModelNotFound},
		{"provider 302", status(302), providercodes.Redirected},
		{"provider 400", status(400), providercodes.RequestRejected},
	}
	reached := make(map[string]bool, len(cases)+2)
	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			got := providerErrorCode(testCase.err)
			if got != testCase.want {
				t.Fatalf("providerErrorCode(%v) = %q, want %q", testCase.err, got, testCase.want)
			}
			if !providercodes.Known(got) {
				t.Fatalf("code %q is not in the shared provider vocabulary %v", got, providercodes.Vocabulary)
			}
			reached[testCase.want] = true
		})
	}
	// The transient code is chosen by modelCallFailureCode for a typed transient
	// error, and the unclassified code is the documented fallback; both are part
	// of the vocabulary. DispatchAdapterUnavailable is the job-surface spelling
	// derived from AdapterUnavailable (its reachability is asserted in
	// internal/api's mapping test).
	reached[providercodes.Transient] = true
	reached[providercodes.Unclassified] = true
	reached[providercodes.DispatchAdapterUnavailable] = true
	for _, code := range providercodes.Vocabulary {
		if !reached[code] {
			t.Fatalf("provider code %q has no mapping that produces it (vocabulary drift)", code)
		}
	}
	if providercodes.DispatchAdapterUnavailable != "provider_"+providercodes.AdapterUnavailable {
		t.Fatalf("the job-surface code is no longer derived from the ledger code: %q",
			providercodes.DispatchAdapterUnavailable)
	}
}

// TestCallerCancellationIsNotAProviderFailure is the H2 regression: a caller
// cancellation (an already-cancelled context, an expired deadline, or the
// context error itself) is classified as the cancellation path, never as
// provider_error, and never reaches the retry/fallback classification.
func TestCallerCancellationIsNotAProviderFailure(t *testing.T) {
	cancelled, cancel := context.WithCancel(context.Background())
	cancel()
	expired, expire := context.WithDeadline(context.Background(), time.Now().Add(-time.Second))
	defer expire()

	cases := []struct {
		name      string
		ctx       context.Context
		err       error
		wantCode  string
		cancelled bool
	}{
		{"already cancelled context", cancelled, context.Canceled, CodeCallerCancelled, true},
		{"expired deadline context", expired, context.DeadlineExceeded, CodeCallerCancelled, true},
		{"cancellation error without a cancelled context", context.Background(), context.Canceled, CodeCallerCancelled, true},
		{"cancelled context with an unrelated error", cancelled, errors.New("boom"), CodeCallerCancelled, true},
		{"transient provider failure", context.Background(), &modelproxy.TransientError{Reason: "http status 503"}, CodeProviderTransient, false},
		{"unclassified provider failure", context.Background(), errors.New("boom"), providercodes.Unclassified, false},
		{"not configured", context.Background(), modelproxy.ErrNotConfigured, CodeProviderNotConfigured, false},
	}
	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			code, isCancelled := modelCallFailureCode(testCase.ctx, testCase.err)
			if code != testCase.wantCode || isCancelled != testCase.cancelled {
				t.Fatalf("modelCallFailureCode = (%q, %v), want (%q, %v)",
					code, isCancelled, testCase.wantCode, testCase.cancelled)
			}
			if testCase.cancelled {
				if code == providercodes.Unclassified {
					t.Fatal("a caller cancellation must never be classified as an unclassified provider failure")
				}
			}
			if errors.Is(testCase.err, context.Canceled) || errors.Is(testCase.err, context.DeadlineExceeded) {
				// The error itself is a cancellation: the ledger mapping must not
				// turn it into the generic provider failure either.
				if got := providerErrorCode(testCase.err); got != CodeCallerCancelled {
					t.Fatalf("providerErrorCode(%v) = %q, want %q (cancellation)", testCase.err, got, CodeCallerCancelled)
				}
			}
		})
	}
}
