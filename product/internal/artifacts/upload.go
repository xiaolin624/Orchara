package artifacts

import (
	"fmt"
	"strings"

	"orchara/platform/internal/objectstore"
)

// MaxUploadBytes is the contract's UploadInput.size_bytes maximum. It is the
// same 20 MiB object ceiling the object store enforces.
const MaxUploadBytes int64 = objectstore.MaxObjectBytes

// UploadInput is the contract's createUpload request body. Unknown fields are
// refused by the HTTP decoder before this type is validated.
type UploadInput struct {
	MediaType string `json:"media_type"`
	SizeBytes int64  `json:"size_bytes"`
	Purpose   string `json:"purpose"`
}

// uploadPurposes is the contract's UploadInput purpose enum. These are
// user-upload purposes, deliberately distinct from the job-produced artifact
// purposes (report/log/receipt/parsed_data) that the T024 job-scoped upload
// endpoint writes.
var uploadPurposes = map[string]bool{
	"task_input":         true,
	"tool_source":        true,
	"knowledge":          true,
	"evaluation_dataset": true,
}

// IsUserUploadPurpose reports whether purpose names a user upload. The finalize
// handler uses it to decide whether the format-admission rules apply: artifacts
// produced by trusted server handlers (reports, logs, receipts) are not user
// content and keep their own shape rules.
func IsUserUploadPurpose(purpose string) bool {
	return uploadPurposes[strings.TrimSpace(purpose)]
}

// ValidationError names the request field that failed validation.
type ValidationError struct {
	Field   string
	Message string
}

func (e *ValidationError) Error() string { return fmt.Sprintf("%s: %s", e.Field, e.Message) }

// NormalizeUploadInput validates the contract's three fields and returns the
// canonical declaration (lowercase media type, trimmed purpose) plus the format
// it promises.
//
// The media type allowlist is deliberately closed: SECURITY §4 says unsupported
// formats are refused, and a parser for an unlisted format would be an
// unvalidated attack surface.
func NormalizeUploadInput(input UploadInput) (UploadInput, Format, error) {
	purpose := strings.TrimSpace(input.Purpose)
	if !uploadPurposes[purpose] {
		return UploadInput{}, "", &ValidationError{
			Field:   "purpose",
			Message: "purpose must be task_input, tool_source, knowledge or evaluation_dataset",
		}
	}
	mediaType := strings.ToLower(strings.TrimSpace(input.MediaType))
	format, supported := SupportedUploadMediaTypeForPurpose(purpose, mediaType)
	if !supported {
		return UploadInput{}, "", &ValidationError{
			Field: "media_type",
			Message: "media_type must be one of text/plain, text/csv, application/json or " +
				XLSXMediaType + " (text/markdown only for purpose=knowledge; application/zip " +
				"only for purpose=tool_source)",
		}
	}
	if input.SizeBytes < 1 || input.SizeBytes > MaxUploadBytes {
		return UploadInput{}, "", &ValidationError{
			Field:   "size_bytes",
			Message: fmt.Sprintf("size_bytes must be between 1 and %d", MaxUploadBytes),
		}
	}
	return UploadInput{MediaType: mediaType, SizeBytes: input.SizeBytes, Purpose: purpose}, format, nil
}
