package handlers

// F06/T122 B2 review fix H3: the model_validate retry table classifies the
// shared provider vocabulary (internal/providercodes), not its own literals.
// This test drives the real outcomeFor mapping and asserts each provider code
// lands in the documented retry class.

import (
	"errors"
	"testing"

	"orchara/platform/internal/background"
	"orchara/platform/internal/providercodes"
)

type codedHandlerError struct{ code string }

func (e codedHandlerError) Error() string     { return "internal API 503 " + e.code }
func (e codedHandlerError) ErrorCode() string { return e.code }

func TestModelValidateOutcomeUsesTheSharedProviderVocabulary(t *testing.T) {
	cases := []struct {
		name string
		err  error
		want background.Outcome
	}{
		{
			"provider not configured",
			codedHandlerError{code: providercodes.NotConfigured},
			background.Failed(background.CodeProviderNotConfigured),
		},
		{
			"adapter unavailable",
			codedHandlerError{code: providercodes.DispatchAdapterUnavailable},
			background.Failed(background.CodeProviderAdapterUnavailable),
		},
		{
			"transient",
			codedHandlerError{code: providercodes.Transient},
			background.Failed(background.CodeTransientInternal),
		},
		{
			"destination not approved",
			codedHandlerError{code: providercodes.DestinationNotApproved},
			background.Failed(background.CodeModelCallRefused),
		},
		{
			"response too large",
			codedHandlerError{code: providercodes.ResponseTooLarge},
			background.Failed(background.CodeModelCallRefused),
		},
		{
			"response invalid",
			codedHandlerError{code: providercodes.ResponseInvalid},
			background.Failed(background.CodeModelCallRefused),
		},
		{
			"permission denied",
			codedHandlerError{code: providercodes.PermissionDenied},
			background.Failed(background.CodeModelCallRefused),
		},
		{
			"unclassified provider error stays retryable",
			codedHandlerError{code: providercodes.Unclassified},
			background.Failed(background.CodeTransientInternal),
		},
		{
			"raw error",
			errors.New("transport"),
			background.Failed(background.CodeTransientInternal),
		},
	}
	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			if got := outcomeFor(testCase.err); got != testCase.want {
				t.Fatalf("outcomeFor(%v) = %+v, want %+v", testCase.err, got, testCase.want)
			}
		})
	}
}
