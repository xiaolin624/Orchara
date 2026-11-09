package capabilities

import (
	"context"
	"errors"
	"testing"

	"orchara/platform/internal/modelproxy"
)

// C2.01: retry and fallback are separate decisions. A typed transient failure is
// retryable (up to the fixed three-attempt ceiling); an availability-class
// adapter fact is not retryable but is fallback-eligible; a permission/Schema/
// tool-argument refusal is neither. The synchronous primary and its compatible
// fallback therefore route the same class of failure the same way.
func TestModelFailureClassificationSeparatesRetryFromFallback(t *testing.T) {
	cases := []struct {
		name             string
		err              error
		ctxDone          bool
		cancelled        bool
		retryable        bool
		fallbackEligible bool
		reason           string
		code             string
	}{
		{
			name: "typed transient", err: &modelproxy.TransientError{Reason: "503 from the provider"},
			retryable: true, fallbackEligible: true, reason: FallbackReasonAvailability,
			code: CodeProviderTransient,
		},
		{
			name: "rate limited transient", err: &modelproxy.TransientError{Reason: "rate limited (429)"},
			retryable: true, fallbackEligible: true, reason: FallbackReasonRateLimited,
			code: CodeProviderTransient,
		},
		{
			name: "adapter unavailable", err: modelproxy.ErrAdapterUnavailable,
			fallbackEligible: true, reason: FallbackReasonAdapterUnavailable,
			code: CodeAdapterUnavailable,
		},
		{
			name: "deployment not configured", err: modelproxy.ErrNotConfigured,
			fallbackEligible: true, reason: FallbackReasonAdapterUnavailable,
			code: CodeProviderNotConfigured,
		},
		{
			name: "permission refusal", err: errors.New("permission denied"),
			code: "provider_error",
		},
		{
			name: "schema refusal", err: errors.New("the stub provider rejected the response schema"),
			code: "provider_error",
		},
		{
			name: "tool-argument refusal", err: errors.New("tool arguments invalid"),
			code: "provider_error",
		},
		{
			name: "caller cancellation", err: context.Canceled,
			cancelled: true, code: CodeCallerCancelled,
		},
		{
			name: "stream consumer stop", err: errStreamConsumerStopped,
			cancelled: true, code: CodeStreamCancelled,
		},
		{
			name: "cancelled request with a transient error", err: &modelproxy.TransientError{},
			ctxDone: true, cancelled: true, code: CodeCallerCancelled,
		},
	}
	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			ctx := context.Background()
			if testCase.ctxDone {
				cancelled, cancel := context.WithCancel(ctx)
				cancel()
				ctx = cancelled
			}
			failure := classifyModelFailure(ctx, testCase.err)
			if failure.Cancelled != testCase.cancelled {
				t.Fatalf("cancelled = %v, want %v", failure.Cancelled, testCase.cancelled)
			}
			if failure.Code != testCase.code {
				t.Fatalf("code = %q, want %q", failure.Code, testCase.code)
			}
			if failure.Retryable != testCase.retryable {
				t.Fatalf("retryable = %v, want %v", failure.Retryable, testCase.retryable)
			}
			if failure.FallbackEligible != testCase.fallbackEligible {
				t.Fatalf("fallbackEligible = %v, want %v",
					failure.FallbackEligible, testCase.fallbackEligible)
			}
			if failure.Reason != testCase.reason {
				t.Fatalf("reason = %q, want %q", failure.Reason, testCase.reason)
			}
		})
	}
}

// C2.02: the bounded retry pause is the only place the driver re-checks a
// caller that vanished between attempts. A cancelled pause must report the
// cancellation so the driver closes the operation instead of dispatching again;
// a live pause still returns normally.
func TestBackoffStopsWhenTheCallerIsCancelled(t *testing.T) {
	cancelled, cancel := context.WithCancel(context.Background())
	cancel()
	if err := sleepBackoff(cancelled, 1); !errors.Is(err, context.Canceled) {
		t.Fatalf("a cancelled backoff returned %v, want context.Canceled", err)
	}
	if err := sleepBackoff(context.Background(), 1); err != nil {
		t.Fatalf("a live backoff returned %v", err)
	}
}
