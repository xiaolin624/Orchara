package artifacts

import (
	"archive/zip"
	"bytes"
	"fmt"
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
