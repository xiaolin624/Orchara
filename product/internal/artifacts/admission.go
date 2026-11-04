// Package artifacts owns the user-facing artifact lifecycle: creating an
// upload reservation, completing it into the finalize pipeline, reading the
// artifact, and minting download links or streaming the sealed bytes.
//
// The security boundary fixed by T028 is:
//
//   - the client only ever receives a signed PUT link to a random staging key
//     and a signed GET link to a sealed key; it never names a path and never
//     receives a PUT for a sealed key;
//   - the sealed key is preallocated on the artifact row and is written only by
//     the server, so overwriting staging after validation cannot change what was
//     verified (IMPLEMENTATION-DETAILS §9);
//   - activation remains the artifact_finalize completion guard's decision; the
//     finalize handler only copies/seals/admits and reports.
//
// This file is the pure content-admission half: it inspects the real bytes and
// never trusts the declared media type. It has no HTTP or database dependency so
// the classifier is unit-testable.
package artifacts

import (
	"archive/zip"
	"bytes"
	"encoding/csv"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"os"
	"path"
	"strings"
	"unicode/utf8"
)

// Format is the sniffed format the platform admits for user uploads.
type Format string

const (
	FormatText    Format = "text"
	FormatCSV     Format = "csv"
	FormatJSON    Format = "json"
	FormatXLSX    Format = "xlsx"
	FormatZip     Format = "zip"
	FormatBinary  Format = "binary"
	FormatUnknown Format = "unknown"
)

// XLSXMediaType is the one spreadsheet media type the first version admits.
// Legacy .xls (application/vnd.ms-excel) is deliberately absent: SECURITY §4
// says unsupported formats are refused rather than guessed at.
const XLSXMediaType = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

// MarkdownMediaType is the F10/T055 knowledge-source declaration. It is admitted
// only for purpose=knowledge: markdown is text for the knowledge parser, not a
// general task input, so the same declaration elsewhere stays refused.
const MarkdownMediaType = "text/markdown"

// ZipMediaType is the T037 build-source bundle. It is admitted **only** for
// purpose=tool_source: the bytes are an untrusted source archive that the
// sandboxed build unpacks, so the same archive for a task input (where no
// sandbox build consumes it) stays refused.
const ZipMediaType = "application/zip"

// Admission verdicts stored in platform.artifact_admissions.verdict.
const (
	VerdictAccepted = "accepted"
	VerdictRejected = "rejected"
)

// Safe rejection reason codes. They are stored verbatim and shown in the UI; a
// code never contains bytes, a path, a URL or a client-supplied name.
const (
	ReasonEmptyContent         = "empty_content"
	ReasonInvalidText          = "invalid_text_encoding"
	ReasonInvalidJSON          = "invalid_json"
	ReasonInvalidCSV           = "invalid_csv"
	ReasonContentTypeMismatch  = "content_type_mismatch"
	ReasonUnsupportedMediaType = "unsupported_media_type"
	ReasonXLSXMacro            = "xlsx_macro_present"
	ReasonXLSXEntryLimit       = "xlsx_entry_limit_exceeded"
	ReasonXLSXUncompressed     = "xlsx_uncompressed_limit_exceeded"
	ReasonXLSXPathTraversal    = "xlsx_path_traversal"
	ReasonXLSXMissingWorkbook  = "xlsx_missing_workbook"
	ReasonXLSXInvalidArchive   = "xlsx_invalid_archive"

	// T037 tool-source zip refusals. A tool_source zip is the build input, so the
	// archive limits are stricter than "a parser will open it": entry count,
	// total uncompressed bytes, compression ratio, unsafe paths, symlinks,
	// encrypted entries and unsupported compression are all refused before any
	// byte is extracted.
	ReasonZipInvalidArchive   = "zip_invalid_archive"
	ReasonZipPathTraversal    = "zip_path_traversal"
	ReasonZipSymlinkEntry     = "zip_symlink_entry"
	ReasonZipEncryptedEntry   = "zip_encrypted_entry"
	ReasonZipUnsupportedCodec = "zip_unsupported_compression"
	ReasonZipEntryLimit       = "zip_entry_limit_exceeded"
	ReasonZipUncompressed     = "zip_uncompressed_limit_exceeded"
	ReasonZipRatio            = "zip_ratio_exceeded"
	ReasonZipEmptyArchive     = "zip_empty_archive"
)

// Archive limits. They are the "decompression/entry limits" BACKGROUND-JOBS §2
// and SECURITY §4 require before an XLSX is admitted.
const (
	MaxArchiveEntries           = 512
	MaxArchiveUncompressedBytes = 64 << 20
	maxContentTypesBytes        = 1 << 20

	// MaxZipCompressionRatio bounds total-uncompressed / total-compressed for a
	// tool source bundle. A real source tree compresses far below this; a zip
	// bomb does not.
	MaxZipCompressionRatio = 100
)

// supportedUploadMediaTypes is the closed allowlist. The map value is the
// format the declared type promises, which is exactly what the sniffed content
// must match.
var supportedUploadMediaTypes = map[string]Format{
	"text/plain":       FormatText,
	"text/csv":         FormatCSV,
	"application/json": FormatJSON,
	XLSXMediaType:      FormatXLSX,
}

// SupportedUploadMediaType reports whether raw is an admitted declared media
// type and returns the format it promises. The check is case-insensitive but
// exact: a parameterised type such as "text/plain; charset=utf-8" is a different
// declaration and is refused.
func SupportedUploadMediaType(raw string) (Format, bool) {
	format, ok := supportedUploadMediaTypes[strings.ToLower(strings.TrimSpace(raw))]
	return format, ok
}

// SupportedUploadMediaTypeForPurpose is SupportedUploadMediaType plus the one
// purpose-scoped allowance: application/zip for tool_source. The purpose is
// already closed by NormalizeUploadInput; a caller that passes an arbitrary
// string gets the default (restricted) allowlist.
func SupportedUploadMediaTypeForPurpose(purpose, raw string) (Format, bool) {
	mediaType := strings.ToLower(strings.TrimSpace(raw))
	switch {
	case strings.TrimSpace(purpose) == "tool_source" && mediaType == ZipMediaType:
		return FormatZip, true
	case strings.TrimSpace(purpose) == "knowledge" && mediaType == MarkdownMediaType:
		return FormatText, true
	}
	return SupportedUploadMediaType(mediaType)
}

// Admission is the durable evidence for one artifact's real bytes.
type Admission struct {
	// DeclaredMediaType is the canonical (lowercase, trimmed) declaration.
	DeclaredMediaType string
	// SniffedMediaType is what the bytes look like; empty when no byte is
	// inspectable (for example a missing staging object).
	SniffedMediaType string
	// DetectedFormat is the sniffed format; empty when no byte is inspectable.
	DetectedFormat Format
	// Verdict is VerdictAccepted or VerdictRejected.
	Verdict string
	// Reason is a safe reason code for a rejection and empty on acceptance.
	Reason string
}

func accepted(declared string, format Format) Admission {
	return Admission{
		DeclaredMediaType: declared,
		SniffedMediaType:  sniffedMediaTypeFor(format),
		DetectedFormat:    format,
		Verdict:           VerdictAccepted,
	}
}

// sniffedMediaTypeFor is the canonical media type recorded for an accepted
// format. It is derived from the bytes, not copied from the declaration, so the
// admission row remains meaningful if the declared string is ever widened.
func sniffedMediaTypeFor(format Format) string {
	switch format {
	case FormatText:
		return "text/plain"
	case FormatCSV:
		return "text/csv"
	case FormatJSON:
		return "application/json"
	case FormatXLSX:
		return XLSXMediaType
	case FormatZip:
		return ZipMediaType
	default:
		return "application/octet-stream"
	}
}

func rejected(declared, sniffed string, format Format, reason string) Admission {
	return Admission{
		DeclaredMediaType: declared,
		SniffedMediaType:  sniffed,
		DetectedFormat:    format,
		Verdict:           VerdictRejected,
		Reason:            reason,
	}
}

// InspectUpload classifies content against the media type the uploader
// declared. It never returns an error: a refusal is an Admission with
// VerdictRejected and a safe reason, which the handler records and the
// completion guard turns into a quarantined artifact.
func InspectUpload(declaredMediaType string, content []byte) Admission {
	return InspectUploadForPurpose(declaredMediaType, "", content)
}

// InspectUploadForPurpose is InspectUpload plus the purpose-scoped tool_source
// zip allowance. The finalize handler must use this form: the purpose is stored
// on the artifact row and is the authority for "may this declaration be a zip".
func InspectUploadForPurpose(declaredMediaType, purpose string, content []byte) Admission {
	declared := strings.ToLower(strings.TrimSpace(declaredMediaType))
	if len(content) == 0 {
		return rejected(declared, "application/octet-stream", FormatUnknown, ReasonEmptyContent)
	}
	format, supported := SupportedUploadMediaTypeForPurpose(purpose, declared)
	sniffed, sniffedFormat := sniffContent(content)
	if !supported {
		return rejected(declared, sniffed, sniffedFormat, ReasonUnsupportedMediaType)
	}

	switch format {
	case FormatText:
		if sniffedFormat == FormatBinary {
			return rejected(declared, sniffed, sniffedFormat, ReasonInvalidText)
		}
		if sniffedFormat != FormatText || strings.HasPrefix(sniffed, "text/html") {
			return rejected(declared, sniffed, sniffedFormat, ReasonContentTypeMismatch)
		}
		return accepted(declared, FormatText)
	case FormatCSV:
		if sniffedFormat == FormatBinary {
			return rejected(declared, sniffed, sniffedFormat, ReasonInvalidText)
		}
		if sniffedFormat != FormatText || strings.HasPrefix(sniffed, "text/html") {
			return rejected(declared, sniffed, sniffedFormat, ReasonContentTypeMismatch)
		}
		if !validCSV(content) {
			return rejected(declared, "text/plain", FormatText, ReasonInvalidCSV)
		}
		return accepted(declared, FormatCSV)
	case FormatJSON:
		if sniffedFormat == FormatBinary {
			return rejected(declared, sniffed, sniffedFormat, ReasonInvalidText)
		}
		if sniffedFormat != FormatText || strings.HasPrefix(sniffed, "text/html") {
			return rejected(declared, sniffed, sniffedFormat, ReasonContentTypeMismatch)
		}
		if !json.Valid(content) {
			return rejected(declared, "text/plain", FormatText, ReasonInvalidJSON)
		}
		return accepted(declared, FormatJSON)
	case FormatXLSX:
		return inspectXLSX(declared, content)
	case FormatZip:
		return inspectToolSourceZip(declared, content)
	default:
		return rejected(declared, sniffed, sniffedFormat, ReasonUnsupportedMediaType)
	}
}

// sniffContent classifies the bytes without trusting the client. Detection is
// deliberately coarse: the admitted formats are text/CSV/JSON/XLSX, so a zip
// or a non-text encoding for a text declaration is a mismatch, not a guess.
func sniffContent(content []byte) (string, Format) {
	if isZip(content) {
		return "application/zip", FormatZip
	}
	if !utf8.Valid(content) || bytes.IndexByte(content, 0) >= 0 {
		return "application/octet-stream", FormatBinary
	}
	detected := http.DetectContentType(content)
	if strings.HasPrefix(detected, "text/html") {
		return "text/html", FormatText
	}
	if strings.HasPrefix(detected, "text/xml") || strings.HasPrefix(detected, "application/xml") {
		return "text/plain", FormatText
	}
	if strings.HasPrefix(detected, "text/") {
		return "text/plain", FormatText
	}
	return detected, FormatBinary
}

func isZip(content []byte) bool {
	return bytes.HasPrefix(content, []byte{'P', 'K', 0x03, 0x04}) ||
		bytes.HasPrefix(content, []byte{'P', 'K', 0x05, 0x06}) ||
		bytes.HasPrefix(content, []byte{'P', 'K', 0x07, 0x08})
}

func validCSV(content []byte) bool {
	reader := csv.NewReader(bytes.NewReader(content))
	// A CSV export need not have a uniform field count; the shape check is
	// about quoting/encoding, not about a schema this ticket does not have.
	reader.FieldsPerRecord = -1
	for {
		if _, err := reader.Read(); errors.Is(err, io.EOF) {
			return true
		} else if err != nil {
			return false
		}
	}
}

func inspectXLSX(declared string, content []byte) Admission {
	if !isZip(content) {
		sniffed, format := sniffContent(content)
		return rejected(declared, sniffed, format, ReasonContentTypeMismatch)
	}
	reader, err := zip.NewReader(bytes.NewReader(content), int64(len(content)))
	if err != nil {
		return rejected(declared, "application/zip", FormatZip, ReasonXLSXInvalidArchive)
	}
	total := uint64(0)
	hasContentTypes := false
	hasWorkbook := false
	for _, entry := range reader.File {
		name := entry.Name
		if !safeArchivePath(name) {
			return rejected(declared, "application/zip", FormatZip, ReasonXLSXPathTraversal)
		}
		lower := strings.ToLower(name)
		if strings.Contains(lower, "vbaproject.bin") || strings.HasPrefix(lower, "xl/macrosheets/") {
			return rejected(declared, "application/zip", FormatZip, ReasonXLSXMacro)
		}
		if entry.UncompressedSize64 > MaxArchiveUncompressedBytes ||
			total > MaxArchiveUncompressedBytes-entry.UncompressedSize64 {
			return rejected(declared, "application/zip", FormatZip, ReasonXLSXUncompressed)
		}
		total += entry.UncompressedSize64
		switch name {
		case "[Content_Types].xml":
			hasContentTypes = true
			macroEnabled, err := archiveEntryContains(entry, "macroenabled")
			if err != nil {
				return rejected(declared, "application/zip", FormatZip, ReasonXLSXInvalidArchive)
			}
			if macroEnabled {
				return rejected(declared, "application/zip", FormatZip, ReasonXLSXMacro)
			}
		case "xl/workbook.xml":
			hasWorkbook = true
		}
	}
	if reason := archiveLimitReason(len(reader.File), total); reason != "" {
		return rejected(declared, "application/zip", FormatZip, reason)
	}
	if !hasContentTypes || !hasWorkbook {
		return rejected(declared, "application/zip", FormatZip, ReasonXLSXMissingWorkbook)
	}
	return accepted(declared, FormatXLSX)
}

// inspectToolSourceZip admits a build source bundle. It never extracts bytes:
// it reads the central directory only, rejects the unsafe shapes up front and
// bounds entries/uncompressed size/ratio. The sandboxed build script repeats
// every check while extracting (a zip header can lie about its sizes), so this
// is the admission gate, not the whole enforcement.
func inspectToolSourceZip(declared string, content []byte) Admission {
	if !isZip(content) {
		sniffed, format := sniffContent(content)
		return rejected(declared, sniffed, format, ReasonContentTypeMismatch)
	}
	reader, err := zip.NewReader(bytes.NewReader(content), int64(len(content)))
	if err != nil {
		return rejected(declared, ZipMediaType, FormatZip, ReasonZipInvalidArchive)
	}
	var (
		totalUncompressed uint64
		totalCompressed   uint64
		files             int
	)
	for _, entry := range reader.File {
		if !safeArchivePath(entry.Name) {
			return rejected(declared, ZipMediaType, FormatZip, ReasonZipPathTraversal)
		}
		if entry.Flags&0x1 != 0 {
			return rejected(declared, ZipMediaType, FormatZip, ReasonZipEncryptedEntry)
		}
		if entry.Mode()&os.ModeSymlink != 0 {
			return rejected(declared, ZipMediaType, FormatZip, ReasonZipSymlinkEntry)
		}
		if reason := zipCompressionReason(&entry.FileHeader); reason != "" {
			return rejected(declared, ZipMediaType, FormatZip, reason)
		}
		if entry.UncompressedSize64 > MaxArchiveUncompressedBytes ||
			totalUncompressed > MaxArchiveUncompressedBytes-entry.UncompressedSize64 {
			return rejected(declared, ZipMediaType, FormatZip, ReasonZipUncompressed)
		}
		totalUncompressed += entry.UncompressedSize64
		if entry.CompressedSize64 > ^uint64(0)-totalCompressed {
			return rejected(declared, ZipMediaType, FormatZip, ReasonZipRatio)
		}
		totalCompressed += entry.CompressedSize64
		if !strings.HasSuffix(entry.Name, "/") && !entry.FileInfo().IsDir() {
			files++
		}
	}
	if files == 0 {
		return rejected(declared, ZipMediaType, FormatZip, ReasonZipEmptyArchive)
	}
	if reason := zipLimitReason(len(reader.File), totalUncompressed, totalCompressed); reason != "" {
		return rejected(declared, ZipMediaType, FormatZip, reason)
	}
	return accepted(declared, FormatZip)
}

// zipCompressionReason admits only Store and Deflate. Anything else (a codec
// the platform has no reader for) is refused rather than guessed at.
func zipCompressionReason(entry *zip.FileHeader) string {
	switch entry.Method {
	case zip.Store, zip.Deflate:
		return ""
	default:
		return ReasonZipUnsupportedCodec
	}
}

// zipLimitReason is the pure entry/uncompressed/ratio decision, shared by the
// inspector and its unit test. A compressed size of zero with uncompressed data
// is an infinite ratio and is refused.
func zipLimitReason(entries int, uncompressed, compressed uint64) string {
	if entries > MaxArchiveEntries {
		return ReasonZipEntryLimit
	}
	if uncompressed > MaxArchiveUncompressedBytes {
		return ReasonZipUncompressed
	}
	if uncompressed == 0 {
		return ""
	}
	if compressed == 0 || uncompressed/compressed > MaxZipCompressionRatio {
		return ReasonZipRatio
	}
	return ""
}

// archiveLimitReason is the pure entry/uncompressed-count decision, shared by
// the inspector and its unit test.
func archiveLimitReason(entries int, uncompressed uint64) string {
	if entries > MaxArchiveEntries {
		return ReasonXLSXEntryLimit
	}
	if uncompressed > MaxArchiveUncompressedBytes {
		return ReasonXLSXUncompressed
	}
	return ""
}

// SafeArchivePath reports whether name is a safe archive entry path. It is the
// exact predicate admission uses for XLSX/zip entries, exported so a content
// parser that opens the same archive applies the same rule instead of inventing
// a second one.
func SafeArchivePath(name string) bool { return safeArchivePath(name) }

// safeArchivePath rejects empty names, absolute paths, backslashes and any
// traversal segment. A trailing "/" is a directory entry (some XLSX writers
// emit them) and is allowed; an XLSX is opened by parsers, never extracted here,
// but a stored archive must not carry a path that would escape if it ever is.
func safeArchivePath(name string) bool {
	if name == "" || strings.Contains(name, "\\") || strings.Contains(name, "\x00") {
		return false
	}
	if strings.HasPrefix(name, "/") {
		return false
	}
	trimmed := strings.TrimSuffix(name, "/")
	if trimmed == "" {
		return false
	}
	cleaned := path.Clean(trimmed)
	if cleaned == "." || cleaned == ".." || strings.HasPrefix(cleaned, "../") {
		return false
	}
	for _, segment := range strings.Split(trimmed, "/") {
		if segment == ".." || segment == "." || segment == "" {
			return false
		}
	}
	return true
}

// archiveEntryContains reads a small metadata entry (bounded, never a data
// sheet) and reports whether it contains needle, case-insensitively.
func archiveEntryContains(entry *zip.File, needle string) (bool, error) {
	reader, err := entry.Open()
	if err != nil {
		return false, err
	}
	defer func() { _ = reader.Close() }()
	payload, err := io.ReadAll(io.LimitReader(reader, maxContentTypesBytes+1))
	if err != nil {
		return false, err
	}
	if len(payload) > maxContentTypesBytes {
		return false, nil
	}
	return strings.Contains(strings.ToLower(string(payload)), needle), nil
}
