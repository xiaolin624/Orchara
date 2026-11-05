package artifacts

import (
	"archive/zip"
	"bytes"
	"fmt"
	"os"
	"strings"
	"testing"
)

// xlsxZip builds an in-memory zip archive. The real admission code opens the
// bytes with archive/zip, so the test fixtures are archives, not a mock.
func xlsxZip(t *testing.T, entries map[string][]byte) []byte {
	t.Helper()
	var buffer bytes.Buffer
	writer := zip.NewWriter(&buffer)
	for name, content := range entries {
		file, err := writer.Create(name)
		if err != nil {
			t.Fatalf("creating zip entry %q: %v", name, err)
		}
		if _, err := file.Write(content); err != nil {
			t.Fatalf("writing zip entry %q: %v", name, err)
		}
	}
	if err := writer.Close(); err != nil {
		t.Fatalf("closing the zip archive: %v", err)
	}
	return buffer.Bytes()
}

func validXLSX(t *testing.T) []byte {
	t.Helper()
	return xlsxZip(t, map[string][]byte{
		"[Content_Types].xml": []byte(`<?xml version="1.0"?><Types/>`),
		"xl/workbook.xml":     []byte(`<?xml version="1.0"?><workbook/>`),
		"xl/worksheets/sheet1.xml": []byte(
			`<?xml version="1.0"?><worksheet><sheetData/></worksheet>`,
		),
	})
}

func inspect(t *testing.T, mediaType string, content []byte) Admission {
	t.Helper()
	return InspectUpload(mediaType, content)
}

func assertRejected(t *testing.T, admission Admission, reason string) {
	t.Helper()
	if admission.Verdict != VerdictRejected {
		t.Fatalf("verdict = %q, want rejected (%+v)", admission.Verdict, admission)
	}
	if admission.Reason != reason {
		t.Fatalf("reason = %q, want %q", admission.Reason, reason)
	}
}

func TestSupportedUploadMediaTypesAreASmallExplicitAllowlist(t *testing.T) {
	for _, mediaType := range []string{
		"text/plain",
		"text/csv",
		"application/json",
		"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
		" TEXT/PLAIN ",
	} {
		if _, ok := SupportedUploadMediaType(mediaType); !ok {
			t.Fatalf("%q is not in the upload allowlist", mediaType)
		}
	}
	for _, mediaType := range []string{
		"", "application/pdf", "text/html", "application/zip", "image/png",
		"application/vnd.ms-excel", "text/plain; charset=utf-8",
	} {
		if _, ok := SupportedUploadMediaType(mediaType); ok {
			t.Fatalf("%q must not be accepted as a declared upload media type", mediaType)
		}
	}
}

func TestUploadInputValidationIsExact(t *testing.T) {
	valid := UploadInput{MediaType: "text/plain", SizeBytes: 1, Purpose: "task_input"}
	if _, _, err := NormalizeUploadInput(valid); err != nil {
		t.Fatalf("the minimal valid input was refused: %v", err)
	}

	cases := []struct {
		name  string
		input UploadInput
		field string
	}{
		{"unknown media type", UploadInput{MediaType: "application/pdf", SizeBytes: 1, Purpose: "task_input"}, "media_type"},
		{"blank media type", UploadInput{MediaType: "  ", SizeBytes: 1, Purpose: "task_input"}, "media_type"},
		{"zero size", UploadInput{MediaType: "text/plain", SizeBytes: 0, Purpose: "task_input"}, "size_bytes"},
		{"negative size", UploadInput{MediaType: "text/plain", SizeBytes: -1, Purpose: "task_input"}, "size_bytes"},
		{"over the 20 MiB ceiling", UploadInput{MediaType: "text/plain", SizeBytes: 20*1024*1024 + 1, Purpose: "task_input"}, "size_bytes"},
		{"unknown purpose", UploadInput{MediaType: "text/plain", SizeBytes: 1, Purpose: "report"}, "purpose"},
		{"blank purpose", UploadInput{MediaType: "text/plain", SizeBytes: 1, Purpose: " "}, "purpose"},
	}
	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			_, _, err := NormalizeUploadInput(testCase.input)
			validation, ok := err.(*ValidationError)
			if !ok {
				t.Fatalf("error = %v, want *ValidationError", err)
			}
			if validation.Field != testCase.field {
				t.Fatalf("field = %q, want %q", validation.Field, testCase.field)
			}
		})
	}
}

// NIT-1: the refusal text must enumerate every media type this build admits.
// application/pdf is purpose-scoped for knowledge, so a caller who sends it
// with the wrong purpose must still see it in the list.
func TestUploadMediaTypeRefusalNamesEveryAdmittedType(t *testing.T) {
	_, _, err := NormalizeUploadInput(UploadInput{
		MediaType: "application/pdf", SizeBytes: 1, Purpose: "task_input",
	})
	validation, ok := err.(*ValidationError)
	if !ok {
		t.Fatalf("error = %v, want *ValidationError", err)
	}
	for _, mediaType := range []string{
		"text/plain", "text/csv", "application/json", XLSXMediaType,
		"text/markdown", PDFMediaType, "application/zip",
	} {
		if !strings.Contains(validation.Message, mediaType) {
			t.Fatalf("the media_type refusal omits %q: %s", mediaType, validation.Message)
		}
	}
}

// F10/T059 extends the parser registry, not the upload allowlist: a
// purpose=knowledge upload of a CSV or an XLSX declaration was already
// admitted before the tabular parsers existed. This test pins that fact so
// the ticket's "no admission change" claim stays verifiable.
func TestKnowledgeUploadsAlreadyAdmitCSVAndXLSXWithoutAnAdmissionChange(t *testing.T) {
	for _, mediaType := range []string{"text/csv", XLSXMediaType} {
		if _, ok := SupportedUploadMediaTypeForPurpose("knowledge", mediaType); !ok {
			t.Fatalf("purpose=knowledge must admit %q", mediaType)
		}
		normalized, _, err := NormalizeUploadInput(UploadInput{
			MediaType: mediaType, SizeBytes: 16, Purpose: "knowledge",
		})
		if err != nil {
			t.Fatalf("purpose=knowledge upload of %q was refused: %v", mediaType, err)
		}
		if normalized.MediaType != mediaType {
			t.Fatalf("normalized media type = %q, want %q", normalized.MediaType, mediaType)
		}
	}
	// The purpose-scoped markdown allowance and the closed allowlist are
	// unchanged: this ticket adds no new declaration.
	if _, ok := SupportedUploadMediaTypeForPurpose("task_input", "text/markdown"); ok {
		t.Fatal("text/markdown must stay knowledge-scoped")
	}
	if _, ok := SupportedUploadMediaTypeForPurpose("task_input", "application/pdf"); ok {
		t.Fatal("application/pdf must stay unadmitted")
	}
}

func TestTextAdmissionAcceptsPlainUTF8AndRejectsDeception(t *testing.T) {
	accepted := inspect(t, "text/plain", []byte("sales report\nregion,total\nEU,10\n"))
	if accepted.Verdict != VerdictAccepted {
		t.Fatalf("plain text was rejected: %+v", accepted)
	}
	if accepted.DetectedFormat != FormatText || accepted.SniffedMediaType != "text/plain" {
		t.Fatalf("unexpected sniff: %+v", accepted)
	}

	assertRejected(t, inspect(t, "text/plain", []byte{0x00, 0x01, 0x02, 0xff}), ReasonInvalidText)
	assertRejected(t, inspect(t, "text/plain", []byte("hello\x00world")), ReasonInvalidText)
	assertRejected(t, inspect(t, "text/plain", []byte("<!DOCTYPE html><html><script>x</script>")), ReasonContentTypeMismatch)
	assertRejected(t, inspect(t, "text/plain", []byte("PK\x03\x04not-really-a-zip")), ReasonContentTypeMismatch)
	assertRejected(t, inspect(t, "text/plain", []byte("")), ReasonEmptyContent)
}

func TestCSVAdmissionParsesTheDeclaredShape(t *testing.T) {
	accepted := inspect(t, "text/csv", []byte("region,total\nEU,10\nUS,12\n"))
	if accepted.Verdict != VerdictAccepted || accepted.DetectedFormat != FormatCSV {
		t.Fatalf("valid CSV was rejected: %+v", accepted)
	}
	assertRejected(t, inspect(t, "text/csv", []byte("region,total\n\"unterminated\n")), ReasonInvalidCSV)
	assertRejected(t, inspect(t, "text/csv", []byte{0xff, 0xfe}), ReasonInvalidText)
	assertRejected(t, inspect(t, "text/csv", []byte("PK\x03\x04zip")), ReasonContentTypeMismatch)
}

func TestJSONAdmissionRequiresRealJSONForTheDeclaredType(t *testing.T) {
	accepted := inspect(t, "application/json", []byte(`{"orders":[{"id":1}]}`))
	if accepted.Verdict != VerdictAccepted || accepted.DetectedFormat != FormatJSON {
		t.Fatalf("valid JSON was rejected: %+v", accepted)
	}
	assertRejected(t, inspect(t, "application/json", []byte(`{"orders":`)), ReasonInvalidJSON)
	assertRejected(t, inspect(t, "application/json", []byte(`{"a":1}{"b":2}`)), ReasonInvalidJSON)
	assertRejected(t, inspect(t, "application/json", []byte{0xff, 0x00}), ReasonInvalidText)
	assertRejected(t, inspect(t, "application/json", []byte("PK\x03\x04zip")), ReasonContentTypeMismatch)
}

func TestXLSXAdmissionInspectsTheZipAndRejectsMacrosAndLimits(t *testing.T) {
	accepted := inspect(t, XLSXMediaType, validXLSX(t))
	if accepted.Verdict != VerdictAccepted || accepted.DetectedFormat != FormatXLSX {
		t.Fatalf("valid XLSX was rejected: %+v", accepted)
	}

	macro := xlsxZip(t, map[string][]byte{
		"[Content_Types].xml": []byte(`<?xml version="1.0"?><Types/>`),
		"xl/workbook.xml":     []byte(`<?xml version="1.0"?><workbook/>`),
		"xl/vbaProject.bin":   []byte("macro"),
	})
	assertRejected(t, inspect(t, XLSXMediaType, macro), ReasonXLSXMacro)

	macroSheet := xlsxZip(t, map[string][]byte{
		"[Content_Types].xml":       []byte(`<?xml version="1.0"?><Types/>`),
		"xl/workbook.xml":           []byte(`<?xml version="1.0"?><workbook/>`),
		"xl/macrosheets/sheet1.xml": []byte("<x/>"),
	})
	assertRejected(t, inspect(t, XLSXMediaType, macroSheet), ReasonXLSXMacro)

	traversal := xlsxZip(t, map[string][]byte{
		"[Content_Types].xml": []byte(`<?xml version="1.0"?><Types/>`),
		"xl/workbook.xml":     []byte(`<?xml version="1.0"?><workbook/>`),
		"../escape.xml":       []byte("<x/>"),
	})
	assertRejected(t, inspect(t, XLSXMediaType, traversal), ReasonXLSXPathTraversal)

	missingWorkbook := xlsxZip(t, map[string][]byte{
		"[Content_Types].xml": []byte(`<?xml version="1.0"?><Types/>`),
	})
	assertRejected(t, inspect(t, XLSXMediaType, missingWorkbook), ReasonXLSXMissingWorkbook)

	assertRejected(t, inspect(t, XLSXMediaType, []byte("not a zip")), ReasonContentTypeMismatch)
	assertRejected(t, inspect(t, XLSXMediaType, []byte("")), ReasonEmptyContent)
}

func TestXLSXEntryLimitIsEnforced(t *testing.T) {
	entries := map[string][]byte{
		"[Content_Types].xml": []byte(`<?xml version="1.0"?><Types/>`),
		"xl/workbook.xml":     []byte(`<?xml version="1.0"?><workbook/>`),
	}
	for index := 0; index < MaxArchiveEntries; index++ {
		entries[fmt.Sprintf("xl/worksheets/extra-%03d.xml", index)] = nil
	}
	assertRejected(t, inspect(t, XLSXMediaType, xlsxZip(t, entries)), ReasonXLSXEntryLimit)
}

func TestXLSXDirectoryEntriesAreAllowed(t *testing.T) {
	// Some writers emit explicit directory entries; they are not path traversal.
	withDirectory := xlsxZip(t, map[string][]byte{
		"[Content_Types].xml": []byte(`<?xml version="1.0"?><Types/>`),
		"xl/":                 nil,
		"xl/workbook.xml":     []byte(`<?xml version="1.0"?><workbook/>`),
	})
	admission := inspect(t, XLSXMediaType, withDirectory)
	if admission.Verdict != VerdictAccepted {
		t.Fatalf("an XLSX with a directory entry was refused: %+v", admission)
	}
	if safeArchivePath("../x") || safeArchivePath("/abs") || safeArchivePath("xl/../x") {
		t.Fatal("a traversal or absolute archive path was accepted")
	}
}

func TestArchiveLimitsRejectUncompressedBombs(t *testing.T) {
	if reason := archiveLimitReason(1, MaxArchiveUncompressedBytes); reason != "" {
		t.Fatalf("an archive at the limit was refused: %s", reason)
	}
	if reason := archiveLimitReason(1, MaxArchiveUncompressedBytes+1); reason != ReasonXLSXUncompressed {
		t.Fatalf("an over-limit archive reason = %q", reason)
	}
	if reason := archiveLimitReason(MaxArchiveEntries+1, 1); reason != ReasonXLSXEntryLimit {
		t.Fatalf("an over-limit entry count reason = %q", reason)
	}
}

func TestAnUnsupportedDeclaredTypeIsRejectedWithASafeSniff(t *testing.T) {
	assertRejected(t, inspect(t, "application/pdf", []byte("%PDF-1.7")), ReasonUnsupportedMediaType)
	assertRejected(t, inspect(t, "image/png", []byte("\x89PNG\r\n")), ReasonUnsupportedMediaType)
}

// ---------------------------------------------------------------------------
// T060: application/pdf is admitted only for purpose=knowledge
// ---------------------------------------------------------------------------

func TestPDFAdmissionIsKnowledgeScopedAndSniffedFromMagicBytes(t *testing.T) {
	// The declaration is admitted for the knowledge purpose only. Every other
	// purpose keeps the pre-T060 refusal: a PDF is a knowledge source, not a
	// generic task input, tool bundle or evaluation dataset.
	if format, ok := SupportedUploadMediaTypeForPurpose("knowledge", "application/pdf"); !ok || format != FormatPDF {
		t.Fatalf("purpose=knowledge did not admit application/pdf as pdf: %q %v", format, ok)
	}
	if _, ok := SupportedUploadMediaTypeForPurpose("knowledge", " APPLICATION/PDF "); !ok {
		t.Fatal("the declared media type must be case/space insensitive")
	}
	for _, purpose := range []string{"task_input", "tool_source", "evaluation_dataset"} {
		if _, ok := SupportedUploadMediaTypeForPurpose(purpose, "application/pdf"); ok {
			t.Fatalf("purpose %q must not admit application/pdf", purpose)
		}
	}

	// The declaration is accepted at reservation time (the 20 MiB object
	// ceiling is unchanged) but refused at completion unless the real bytes
	// start with the PDF magic header.
	if _, _, err := NormalizeUploadInput(UploadInput{
		MediaType: "application/pdf", SizeBytes: 1024, Purpose: "knowledge",
	}); err != nil {
		t.Fatalf("a knowledge PDF declaration was refused: %v", err)
	}
	_, _, err := NormalizeUploadInput(UploadInput{
		MediaType: "application/pdf", SizeBytes: MaxUploadBytes + 1, Purpose: "knowledge",
	})
	if validation, ok := err.(*ValidationError); !ok || validation.Field != "size_bytes" {
		t.Fatalf("over-ceiling PDF error = %v, want size_bytes validation", err)
	}

	accepted := InspectUploadForPurpose("application/pdf", "knowledge", []byte("%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj\n"))
	if accepted.Verdict != VerdictAccepted || accepted.DetectedFormat != FormatPDF {
		t.Fatalf("valid PDF bytes were refused: %+v", accepted)
	}
	if accepted.SniffedMediaType != "application/pdf" {
		t.Fatalf("sniffed media type = %q, want application/pdf", accepted.SniffedMediaType)
	}

	// A PDF declaration whose bytes are text, a zip, or whose header is not at
	// offset zero is a content-type mismatch, not a guessed PDF.
	assertRejected(t, InspectUploadForPurpose("application/pdf", "knowledge", []byte("hello, this is not a pdf\n")), ReasonContentTypeMismatch)
	assertRejected(t, InspectUploadForPurpose("application/pdf", "knowledge", []byte("PK\x03\x04zip")), ReasonContentTypeMismatch)
	assertRejected(t, InspectUploadForPurpose("application/pdf", "knowledge", []byte("junk%PDF-1.7\n")), ReasonContentTypeMismatch)
	assertRejected(t, InspectUploadForPurpose("application/pdf", "knowledge", []byte("")), ReasonEmptyContent)

	// The base allowlist stays closed: an unscoped declaration is still refused.
	assertRejected(t, InspectUpload("application/pdf", []byte("%PDF-1.7\n")), ReasonUnsupportedMediaType)
}

// ---------------------------------------------------------------------------
// T037: application/zip is admitted only for purpose=tool_source
// ---------------------------------------------------------------------------

func toolSourceZip(t *testing.T, entries map[string][]byte) []byte {
	t.Helper()
	return xlsxZip(t, entries)
}

func inspectToolSource(t *testing.T, content []byte) Admission {
	t.Helper()
	return InspectUploadForPurpose("application/zip", "tool_source", content)
}

func TestZipIsAllowedOnlyForTheToolSourcePurpose(t *testing.T) {
	if _, ok := SupportedUploadMediaTypeForPurpose("tool_source", "application/zip"); !ok {
		t.Fatal("tool_source did not admit application/zip")
	}
	for _, purpose := range []string{"task_input", "knowledge", "evaluation_dataset"} {
		if _, ok := SupportedUploadMediaTypeForPurpose(purpose, "application/zip"); ok {
			t.Fatalf("purpose %q must not admit application/zip", purpose)
		}
	}
	if _, _, err := NormalizeUploadInput(UploadInput{
		MediaType: "application/zip", SizeBytes: 10, Purpose: "tool_source",
	}); err != nil {
		t.Fatalf("a tool_source zip was refused at declaration time: %v", err)
	}
	if _, _, err := NormalizeUploadInput(UploadInput{
		MediaType: "application/zip", SizeBytes: 10, Purpose: "task_input",
	}); err == nil {
		t.Fatal("a task_input zip was admitted at declaration time")
	}
}

func TestToolSourceZipAdmissionAcceptsARealSourceBundle(t *testing.T) {
	admission := inspectToolSource(t, toolSourceZip(t, map[string][]byte{
		"tool/__init__.py": []byte("VALUE = 1\n"),
		"tool/main.py":     []byte("from . import VALUE\nprint(VALUE)\n"),
		"setup.py":         []byte("print('build')\n"),
	}))
	if admission.Verdict != VerdictAccepted || admission.DetectedFormat != FormatZip {
		t.Fatalf("a real source bundle was refused: %+v", admission)
	}
	if admission.SniffedMediaType != "application/zip" {
		t.Fatalf("sniffed media type = %q", admission.SniffedMediaType)
	}
}

func TestToolSourceZipAdmissionRejectsUnsafeEntries(t *testing.T) {
	cases := map[string]struct {
		entries map[string][]byte
		reason  string
	}{
		"path traversal": {
			entries: map[string][]byte{"../escape.py": []byte("x")},
			reason:  ReasonZipPathTraversal,
		},
		"absolute path": {
			entries: map[string][]byte{"/etc/passwd": []byte("x")},
			reason:  ReasonZipPathTraversal,
		},
		"backslash path": {
			entries: map[string][]byte{`tool\main.py`: []byte("x")},
			reason:  ReasonZipPathTraversal,
		},
		"no files": {
			entries: map[string][]byte{"tool/": nil},
			reason:  ReasonZipEmptyArchive,
		},
	}
	for name, testCase := range cases {
		t.Run(name, func(t *testing.T) {
			assertRejected(t, inspectToolSource(t, toolSourceZip(t, testCase.entries)), testCase.reason)
		})
	}

	// A symlink entry is refused even though the name is harmless.
	var buffer bytes.Buffer
	writer := zip.NewWriter(&buffer)
	header := &zip.FileHeader{Name: "tool/link.py", Method: zip.Store}
	header.SetMode(os.ModeSymlink | 0o777)
	file, err := writer.CreateHeader(header)
	if err != nil {
		t.Fatalf("creating the symlink entry: %v", err)
	}
	if _, err := file.Write([]byte("../../etc/passwd")); err != nil {
		t.Fatalf("writing the symlink entry: %v", err)
	}
	if err := writer.Close(); err != nil {
		t.Fatalf("closing the symlink archive: %v", err)
	}
	assertRejected(t, inspectToolSource(t, buffer.Bytes()), ReasonZipSymlinkEntry)

	// An encrypted entry is refused before anything tries to decompress it.
	buffer.Reset()
	writer = zip.NewWriter(&buffer)
	encrypted := &zip.FileHeader{Name: "tool/main.py", Method: zip.Deflate}
	encrypted.Flags |= 0x1
	file, err = writer.CreateHeader(encrypted)
	if err != nil {
		t.Fatalf("creating the encrypted entry: %v", err)
	}
	if _, err := file.Write([]byte("x")); err != nil {
		t.Fatalf("writing the encrypted entry: %v", err)
	}
	if err := writer.Close(); err != nil {
		t.Fatalf("closing the encrypted archive: %v", err)
	}
	assertRejected(t, inspectToolSource(t, buffer.Bytes()), ReasonZipEncryptedEntry)

	// Unsupported compression methods are refused rather than guessed at. The
	// standard writer refuses to emit an unknown method, so the pure check is
	// asserted directly (the reader path uses the same helper).
	unsupported := &zip.FileHeader{Name: "tool/main.py", Method: 99}
	if reason := zipCompressionReason(unsupported); reason != ReasonZipUnsupportedCodec {
		t.Fatalf("unsupported method reason = %q", reason)
	}
}

func TestToolSourceZipAdmissionEnforcesRatioAndLimits(t *testing.T) {
	// 8 MiB of zeros compresses to a few KiB: the ratio cap must fire before
	// anything is decompressed into memory.
	bomb := toolSourceZip(t, map[string][]byte{"tool/blob.bin": make([]byte, 8<<20)})
	assertRejected(t, inspectToolSource(t, bomb), ReasonZipRatio)

	if reason := zipLimitReason(1, MaxArchiveUncompressedBytes, MaxArchiveUncompressedBytes); reason != "" {
		t.Fatalf("an archive at the limits was refused: %s", reason)
	}
	if reason := zipLimitReason(MaxArchiveEntries+1, 1, 1); reason != ReasonZipEntryLimit {
		t.Fatalf("entry limit reason = %q", reason)
	}
	if reason := zipLimitReason(1, MaxArchiveUncompressedBytes+1, MaxArchiveUncompressedBytes); reason != ReasonZipUncompressed {
		t.Fatalf("uncompressed limit reason = %q", reason)
	}
	if reason := zipLimitReason(1, 1<<20, 1); reason != ReasonZipRatio {
		t.Fatalf("ratio reason = %q", reason)
	}

	// A declared zip whose bytes are not a zip is a content-type mismatch.
	assertRejected(t, inspectToolSource(t, []byte("not a zip")), ReasonContentTypeMismatch)
	// An empty body stays the generic empty-content refusal.
	assertRejected(t, inspectToolSource(t, []byte("")), ReasonEmptyContent)
}
