package knowledge

// F10/T059: XLSX parsing, archive limits and formula refusal.
//
// The fixtures are real zip archives built from the OOXML parts a workbook is
// made of; the parser opens them with archive/zip exactly as it opens a sealed
// artifact. Expected values are the sheet's own row numbers and the literal cell
// values written into the fixture, not values read back from the parser.

import (
	"archive/zip"
	"bytes"
	"errors"
	"fmt"
	"math/rand"
	"strings"
	"testing"
)

// xlsxZip builds a real in-memory zip from the named parts.
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

func xlsxContentTypes(extra string) []byte {
	return []byte(`<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">` +
		`<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>` +
		extra + `</Types>`)
}

func xlsxWorkbookXML(sheets string) []byte {
	return []byte(`<?xml version="1.0" encoding="UTF-8"?>` +
		`<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" ` +
		`xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">` +
		`<sheets>` + sheets + `</sheets></workbook>`)
}

func xlsxRelsXML(targets ...string) []byte {
	var builder strings.Builder
	builder.WriteString(`<?xml version="1.0" encoding="UTF-8"?>` +
		`<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">`)
	for index, target := range targets {
		fmt.Fprintf(&builder,
			`<Relationship Id="rId%d" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="%s"/>`,
			index+1, target)
	}
	builder.WriteString(`</Relationships>`)
	return []byte(builder.String())
}

func xlsxSheetXML(body string) []byte {
	return []byte(`<?xml version="1.0" encoding="UTF-8"?>` +
		`<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>` +
		body + `</sheetData></worksheet>`)
}

func xlsxSharedStringsXML(values ...string) []byte {
	var builder strings.Builder
	builder.WriteString(`<?xml version="1.0" encoding="UTF-8"?>` +
		`<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">`)
	for _, value := range values {
		builder.WriteString(`<si><t>` + value + `</t></si>`)
	}
	builder.WriteString(`</sst>`)
	return []byte(builder.String())
}

// xlsxWorkbook assembles a complete one-sheet workbook the admission gate and the
// parser both accept.
func xlsxWorkbook(name, sheetBody string, shared ...string) []byte {
	return xlsxZipStatic(xlsxWorkbookEntries(name, sheetBody, shared...))
}

func xlsxWorkbookEntries(name, sheetBody string, shared ...string) map[string][]byte {
	entries := map[string][]byte{
		"[Content_Types].xml":        xlsxContentTypes(""),
		"xl/workbook.xml":            xlsxWorkbookXML(`<sheet name="` + name + `" sheetId="1" r:id="rId1"/>`),
		"xl/_rels/workbook.xml.rels": xlsxRelsXML("worksheets/sheet1.xml"),
		"xl/worksheets/sheet1.xml":   xlsxSheetXML(sheetBody),
	}
	if len(shared) > 0 {
		entries["xl/sharedStrings.xml"] = xlsxSharedStringsXML(shared...)
	}
	return entries
}

// xlsxIncompressiblePadding is an unused, incompressible part. A fixture that
// intentionally reaches a content cap must not be refused by the archive ratio
// limit first; real sheets carry varied cell text and do not compress 100x, so
// the padding restores that shape without weakening the cap under test.
func xlsxIncompressiblePadding(size int) []byte {
	source := rand.New(rand.NewSource(1))
	payload := make([]byte, size)
	for index := range payload {
		payload[index] = byte(source.Intn(256))
	}
	return payload
}

// xlsxNoiseText returns deterministic, hardly-compressible ASCII text. A cell
// whose text is one repeated character would trip the archive ratio limit before
// the cap under test, which is not the shape this test is about.
func xlsxNoiseText(size int) string {
	source := rand.New(rand.NewSource(7))
	const alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
	payload := make([]byte, size)
	for index := range payload {
		payload[index] = alphabet[source.Intn(len(alphabet))]
	}
	return string(payload)
}

func xlsxPadded(t *testing.T, entries map[string][]byte) []byte {
	t.Helper()
	entries["xl/media/padding.bin"] = xlsxIncompressiblePadding(1 << 20)
	return xlsxZip(t, entries)
}

func xlsxZipStatic(entries map[string][]byte) []byte {
	var buffer bytes.Buffer
	writer := zip.NewWriter(&buffer)
	for name, content := range entries {
		file, _ := writer.Create(name)
		_, _ = file.Write(content)
	}
	_ = writer.Close()
	return buffer.Bytes()
}

func parseXLSXContent(t *testing.T, content []byte) (Parsed, error) {
	t.Helper()
	parser, ok := ParserFor(MediaTypeXLSX)
	if !ok {
		t.Fatal("no parser for the XLSX media type")
	}
	return parser.Parse(content)
}

func TestXLSXParserRendersTheFirstSheetWithSheetNameAndWorksheetRowNumbers(t *testing.T) {
	shared := []string{"order_id", "region", "amount", "A1", "东区", "华东,区"}
	body := `<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c><c r="C1" t="s"><v>2</v></c></row>` +
		`<row r="2"><c r="A2" t="s"><v>3</v></c><c r="B2" t="s"><v>4</v></c><c r="C2"><v>120.5</v></c></row>` +
		`<row r="3"><c r="A3" t="inlineStr"><is><t>A2</t></is></c><c r="B3" t="s"><v>5</v></c><c r="C3"><v>79.5</v></c></row>` +
		`<row r="5"><c r="A5" t="str"><v>A3</v></c><c r="B5" t="s"><v>4</v></c><c r="C5"><v>50</v></c></row>`
	content := xlsxWorkbook("Sales", body, shared...)

	parsed, err := parseXLSXContent(t, content)
	if err != nil {
		t.Fatalf("parsing the workbook: %v", err)
	}
	parser, _ := ParserFor(MediaTypeXLSX)
	if parser.Version() != ParserVersionXLSX {
		t.Fatalf("parser version = %q", parser.Version())
	}
	if parsed.TextRender != TextRenderSheetCSV {
		t.Fatalf("text render = %q, want %q", parsed.TextRender, TextRenderSheetCSV)
	}
	if parsed.Table == nil {
		t.Fatal("an XLSX parse must expose its table rows")
	}
	table := parsed.Table
	if table.SheetName != "Sales" {
		t.Fatalf("sheet name = %q, want Sales", table.SheetName)
	}
	if table.HeaderRow != 1 {
		t.Fatalf("header row = %d, want 1", table.HeaderRow)
	}
	if strings.Join(table.Columns, "|") != "order_id|region|amount" {
		t.Fatalf("columns = %v", table.Columns)
	}
	// The seeded artifact is a deterministic rendering: one CSV line per table
	// row, header first, verbatim stored cell values.
	wantText := "order_id,region,amount\n" +
		"A1,东区,120.5\n" +
		"A2,\"华东,区\",79.5\n" +
		"A3,东区,50\n"
	if parsed.Text != wantText {
		t.Fatalf("text = %q, want %q", parsed.Text, wantText)
	}
	// The worksheet's own row numbers are preserved (row 4 is absent).
	wantRows := []struct {
		number int
		text   string
	}{{2, "A1,东区,120.5"}, {3, "A2,\"华东,区\",79.5"}, {5, "A3,东区,50"}}
	if len(table.Rows) != len(wantRows) {
		t.Fatalf("data rows = %d, want %d", len(table.Rows), len(wantRows))
	}
	for index, want := range wantRows {
		row := table.Rows[index]
		if row.Number != want.number {
			t.Fatalf("row %d number = %d, want the worksheet row %d", index, row.Number, want.number)
		}
		if got := string([]rune(parsed.Text)[row.StartChar:row.EndChar]); got != want.text {
			t.Fatalf("row %d span slices %q, want %q", want.number, got, want.text)
		}
	}
	// The row-batch chunker keeps the worksheet row range in the chunk.
	chunks := ChunkParsed(parsed)
	if len(chunks) != 1 {
		t.Fatalf("chunks = %d, want 1 for four short rows", len(chunks))
	}
	chunk := chunks[0]
	if chunk.Table == nil || chunk.Table.RowStart != 2 || chunk.Table.RowEnd != 5 ||
		chunk.Table.RowCount != 3 || chunk.Table.SheetName != "Sales" || chunk.Table.HeaderRow != 1 {
		t.Fatalf("chunk row range = %+v", chunk.Table)
	}
	if got := string([]rune(parsed.Text)[chunk.Span.StartChar:chunk.Span.EndChar]); got != chunk.Text {
		t.Fatalf("chunk span does not slice back to the rendered body")
	}
}

func TestXLSXParserFollowsWorkbookOrderAndResolvesAbsoluteSheetTargets(t *testing.T) {
	// The first sheet's relationship target is absolute ("/xl/..."), which a
	// writer may emit; the parser must resolve it against the package root.
	entries := map[string][]byte{
		"[Content_Types].xml": xlsxContentTypes(""),
		"xl/workbook.xml": xlsxWorkbookXML(
			`<sheet name="Second" sheetId="1" r:id="rId1"/>` +
				`<sheet name="First" sheetId="2" r:id="rId2"/>`),
		"xl/_rels/workbook.xml.rels": xlsxRelsXML(
			"/xl/worksheets/sheet9.xml", "worksheets/sheet1.xml"),
		"xl/worksheets/sheet9.xml": xlsxSheetXML(
			`<row r="1"><c r="A1" t="inlineStr"><is><t>picked</t></is></c></row>` +
				`<row r="2"><c r="A2" t="inlineStr"><is><t>second-sheet</t></is></c></row>`),
		"xl/worksheets/sheet1.xml": xlsxSheetXML(
			`<row r="1"><c r="A1" t="inlineStr"><is><t>ignored</t></is></c></row>` +
				`<row r="2"><c r="A2" t="inlineStr"><is><t>first-sheet</t></is></c></row>`),
	}
	parsed, err := parseXLSXContent(t, xlsxZip(t, entries))
	if err != nil {
		t.Fatalf("parsing the workbook: %v", err)
	}
	if parsed.Table.SheetName != "Second" {
		t.Fatalf("sheet name = %q, want the workbook's first sheet Second", parsed.Table.SheetName)
	}
	if parsed.Text != "picked\nsecond-sheet\n" {
		t.Fatalf("text = %q, want the absolute-target sheet", parsed.Text)
	}
}

func TestXLSXParserRefusesFormulaCellsWithTheirLocation(t *testing.T) {
	body := `<row r="1"><c r="A1" t="inlineStr"><is><t>order_id</t></is></c>` +
		`<c r="B1" t="inlineStr"><is><t>amount</t></is></c></row>` +
		`<row r="2"><c r="A2" t="inlineStr"><is><t>A1</t></is></c>` +
		`<c r="B2"><f>SUM(C2:C3)</f><v>999</v></c></row>`
	content := xlsxZip(t, map[string][]byte{
		"[Content_Types].xml":        xlsxContentTypes(""),
		"xl/workbook.xml":            xlsxWorkbookXML(`<sheet name="Orders" sheetId="1" r:id="rId1"/>`),
		"xl/_rels/workbook.xml.rels": xlsxRelsXML("worksheets/sheet1.xml"),
		"xl/worksheets/sheet1.xml":   xlsxSheetXML(body),
	})
	_, err := parseXLSXContent(t, content)
	var parseErr *ParseError
	if !errors.As(err, &parseErr) {
		t.Fatalf("formula content error = %v, want *ParseError", err)
	}
	if parseErr.FailureCode != FailureIngestFormulaNotSupported {
		t.Fatalf("code = %q, want %q", parseErr.FailureCode, FailureIngestFormulaNotSupported)
	}
	want := ParseDetail{Sheet: "Orders", Row: 2, Column: "B", Cell: "B2"}
	if parseErr.Location != want {
		t.Fatalf("location = %+v, want %+v", parseErr.Location, want)
	}
}

func TestXLSXParserRejectsDTDAndEntityDeclarationsBeforeParsing(t *testing.T) {
	for _, testCase := range []struct {
		name  string
		part  string
		value []byte
	}{
		{"doctype in the workbook", "workbook",
			[]byte(`<?xml version="1.0"?><!DOCTYPE workbook [<!ENTITY x "y">]>` +
				`<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheets/></workbook>`)},
		{"entity in shared strings", "shared_strings",
			[]byte(`<?xml version="1.0"?><!ENTITY boom "&boom;">` +
				`<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><si><t>x</t></si></sst>`)},
	} {
		t.Run(testCase.name, func(t *testing.T) {
			entries := map[string][]byte{
				"[Content_Types].xml":        xlsxContentTypes(""),
				"xl/workbook.xml":            xlsxWorkbookXML(`<sheet name="S" sheetId="1" r:id="rId1"/>`),
				"xl/_rels/workbook.xml.rels": xlsxRelsXML("worksheets/sheet1.xml"),
				"xl/worksheets/sheet1.xml":   xlsxSheetXML(`<row r="1"><c r="A1" t="inlineStr"><is><t>h</t></is></c></row>`),
				"xl/sharedStrings.xml":       xlsxSharedStringsXML("unused"),
			}
			target := "xl/workbook.xml"
			if testCase.part == "shared_strings" {
				target = "xl/sharedStrings.xml"
			}
			entries[target] = testCase.value
			_, err := parseXLSXContent(t, xlsxZip(t, entries))
			var parseErr *ParseError
			if !errors.As(err, &parseErr) || parseErr.FailureCode != FailureIngestDTDNotSupported {
				t.Fatalf("error = %v, want %q", err, FailureIngestDTDNotSupported)
			}
			if parseErr.Location.Part != testCase.part {
				t.Fatalf("part = %q, want %q", parseErr.Location.Part, testCase.part)
			}
		})
	}
}

func TestXLSXArchiveDecisionUsesTheExistingAdmissionLimits(t *testing.T) {
	totalCases := []struct {
		name         string
		entries      int
		uncompressed uint64
		compressed   uint64
		wantLimit    string
	}{
		{"too many entries", 513, 100, 100, "entries"},
		{"uncompressed total", 2, 64<<20 + 1, 1 << 20, "uncompressed_bytes"},
		{"total compression ratio", 2, 10 << 20, 1 << 10, "compression_ratio"},
		{"zero compressed bytes with data is an infinite ratio", 2, 100, 0, "compression_ratio"},
		{"accepted", 4, 1 << 20, 1 << 19, ""},
	}
	for _, testCase := range totalCases {
		t.Run(testCase.name, func(t *testing.T) {
			_, detail := xlsxArchiveDecision(
				testCase.entries, testCase.uncompressed, testCase.compressed)
			if detail.Limit != testCase.wantLimit {
				t.Fatalf("limit = %q, want %q", detail.Limit, testCase.wantLimit)
			}
		})
	}
	entryCases := []struct {
		name         string
		uncompressed uint64
		compressed   uint64
		wantLimit    string
	}{
		{"entry over the uncompressed ceiling", 64<<20 + 1, 1 << 20, "entry_uncompressed_bytes"},
		{"entry ratio", 1 << 20, 1 << 10, "compression_ratio"},
		{"entry with no compressed bytes", 100, 0, "compression_ratio"},
		{"accepted entry", 1 << 20, 1 << 19, ""},
	}
	for _, testCase := range entryCases {
		t.Run(testCase.name, func(t *testing.T) {
			_, detail := xlsxEntryArchiveDecision(testCase.uncompressed, testCase.compressed)
			if detail.Limit != testCase.wantLimit {
				t.Fatalf("limit = %q, want %q", detail.Limit, testCase.wantLimit)
			}
		})
	}
}

func TestXLSXParserRefusesUnsafeArchivesWithSpecificCodes(t *testing.T) {
	shell := func(extra map[string][]byte) map[string][]byte {
		entries := map[string][]byte{
			"[Content_Types].xml":        xlsxContentTypes(""),
			"xl/workbook.xml":            xlsxWorkbookXML(`<sheet name="S" sheetId="1" r:id="rId1"/>`),
			"xl/_rels/workbook.xml.rels": xlsxRelsXML("worksheets/sheet1.xml"),
			"xl/worksheets/sheet1.xml":   xlsxSheetXML(`<row r="1"><c r="A1" t="inlineStr"><is><t>h</t></is></c></row>`),
		}
		for name, content := range extra {
			entries[name] = content
		}
		return entries
	}
	cases := []struct {
		name     string
		content  []byte
		wantCode string
		part     string
	}{
		{"not a zip", []byte("PK\x03\x04not really a zip"), FailureIngestInvalidXLSX, "archive"},
		{"missing workbook", xlsxZip(t, map[string][]byte{"[Content_Types].xml": xlsxContentTypes("")}), FailureIngestInvalidXLSX, "workbook"},
		{"path traversal entry", xlsxZip(t, shell(map[string][]byte{"../evil.xml": []byte("<x/>")})), FailureIngestArchivePath, "entry_name"},
		{"macro part", xlsxZip(t, shell(map[string][]byte{"xl/vbaProject.bin": []byte("mac")})), FailureIngestMacroPresent, "macro"},
		{"macro-enabled content types", xlsxZip(t, shell(map[string][]byte{
			"[Content_Types].xml": xlsxContentTypes(
				`<Override PartName="/xl/workbook.xml" ContentType="application/vnd.ms-excel.sheet.macroEnabled.main+xml"/>`),
		})), FailureIngestMacroPresent, "macro"},
	}
	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			_, err := parseXLSXContent(t, testCase.content)
			var parseErr *ParseError
			if !errors.As(err, &parseErr) || parseErr.FailureCode != testCase.wantCode {
				t.Fatalf("error = %v, want %q", err, testCase.wantCode)
			}
			if parseErr.Location.Part != testCase.part {
				t.Fatalf("part = %q, want %q", parseErr.Location.Part, testCase.part)
			}
		})
	}

	// A compression bomb is refused by the parser because admission keeps the
	// upload gate at format shape, not content ratio; the document must fail
	// with a specific code instead of becoming ready.
	bomb := shell(map[string][]byte{
		"xl/worksheets/sheet1.xml": xlsxSheetXML(
			`<row r="1"><c r="A1" t="inlineStr"><is><t>h</t></is></c></row>` +
				`<row r="2"><c r="A2" t="inlineStr"><is><t>` + strings.Repeat("x", 1<<20) + `</t></is></c></row>`),
	})
	_, err := parseXLSXContent(t, xlsxZip(t, bomb))
	var parseErr *ParseError
	if !errors.As(err, &parseErr) || parseErr.FailureCode != FailureIngestArchiveLimit {
		t.Fatalf("ratio bomb error = %v, want %q", err, FailureIngestArchiveLimit)
	}
	if parseErr.Location.Limit != "compression_ratio" {
		t.Fatalf("limit = %q, want compression_ratio", parseErr.Location.Limit)
	}
}

func TestXLSXParserEnforcesWorksheetRowColumnCellAndRowSizeCaps(t *testing.T) {
	sheet := func(name string) string {
		return fmt.Sprintf(`<sheet name="%s" sheetId="1" r:id="rId1"/>`, name)
	}
	// 65 worksheets: refused before reading any sheet.
	sheets := make([]string, 0, MaxTableWorksheets+1)
	for index := 0; index <= MaxTableWorksheets; index++ {
		sheets = append(sheets, sheet(fmt.Sprintf("S%d", index)))
	}
	_, err := parseXLSXContent(t, xlsxZip(t, map[string][]byte{
		"[Content_Types].xml": xlsxContentTypes(""),
		"xl/workbook.xml":     xlsxWorkbookXML(strings.Join(sheets, "")),
	}))
	var parseErr *ParseError
	if !errors.As(err, &parseErr) || parseErr.FailureCode != FailureIngestWorksheetLimit {
		t.Fatalf("worksheet cap error = %v, want %q", err, FailureIngestWorksheetLimit)
	}

	// Too many data rows.
	var manyRows strings.Builder
	manyRows.WriteString(`<row r="1"><c r="A1" t="inlineStr"><is><t>a</t></is></c></row>`)
	for index := 0; index <= MaxTableRows; index++ {
		fmt.Fprintf(&manyRows, `<row r="%d"><c r="A%d" t="inlineStr"><is><t>x</t></is></c></row>`, index+2, index+2)
	}
	_, err = parseXLSXContent(t, xlsxPadded(t, xlsxWorkbookEntries("Rows", manyRows.String())))
	if !errors.As(err, &parseErr) || parseErr.FailureCode != FailureIngestRowLimit {
		t.Fatalf("row cap error = %v, want %q", err, FailureIngestRowLimit)
	}

	// Too many columns in one row: the location names the row and the letter.
	var manyColumns strings.Builder
	manyColumns.WriteString(`<row r="1">`)
	for index := 0; index <= MaxTableColumns; index++ {
		fmt.Fprintf(&manyColumns,
			`<c r="%s1" t="inlineStr"><is><t>c</t></is></c>`, columnLetters(index))
	}
	manyColumns.WriteString(`</row>`)
	_, err = parseXLSXContent(t, xlsxWorkbook("Columns", manyColumns.String()))
	if !errors.As(err, &parseErr) || parseErr.FailureCode != FailureIngestColumnLimit {
		t.Fatalf("column cap error = %v, want %q", err, FailureIngestColumnLimit)
	}
	if parseErr.Location.Row != 1 || parseErr.Location.Column != columnLetters(MaxTableColumns) {
		t.Fatalf("column cap location = %+v", parseErr.Location)
	}

	// Too many cells: empty rows still carry cells, so the cell cap is reachable
	// without the data-row cap firing first.
	var manyCells strings.Builder
	for row := 1; row <= 10002; row++ {
		fmt.Fprintf(&manyCells, `<row r="%d">`, row)
		for column := 0; column < MaxTableColumns; column++ {
			fmt.Fprintf(&manyCells, `<c r="%s%d"/>`, columnLetters(column), row)
		}
		manyCells.WriteString(`</row>`)
	}
	_, err = parseXLSXContent(t, xlsxPadded(t, xlsxWorkbookEntries("Cells", manyCells.String())))
	if !errors.As(err, &parseErr) || parseErr.FailureCode != FailureIngestCellLimit {
		t.Fatalf("cell cap error = %v, want %q", err, FailureIngestCellLimit)
	}

	// One row is never split, so an oversized row is refused, with its number.
	_, err = parseXLSXContent(t, xlsxPadded(t, xlsxWorkbookEntries("Long",
		`<row r="1"><c r="A1" t="inlineStr"><is><t>h</t></is></c></row>`+
			`<row r="2"><c r="A2" t="inlineStr"><is><t>`+
			xlsxNoiseText(MaxTableRowChars+1)+`</t></is></c></row>`)))
	if !errors.As(err, &parseErr) || parseErr.FailureCode != FailureIngestRowTooLarge {
		t.Fatalf("row size error = %v, want %q", err, FailureIngestRowTooLarge)
	}
	if parseErr.Location.Row != 2 {
		t.Fatalf("row size location = %+v", parseErr.Location)
	}
}

func TestXLSXParserRefusesMalformedPartsAndBadReferences(t *testing.T) {
	cases := []struct {
		name     string
		entries  map[string][]byte
		wantCode string
	}{
		{"workbook is not xml", map[string][]byte{
			"[Content_Types].xml": xlsxContentTypes(""),
			"xl/workbook.xml":     []byte("<<<not xml"),
		}, FailureIngestInvalidXLSX},
		{"relationship target missing", map[string][]byte{
			"[Content_Types].xml":        xlsxContentTypes(""),
			"xl/workbook.xml":            xlsxWorkbookXML(`<sheet name="S" sheetId="1" r:id="rId1"/>`),
			"xl/_rels/workbook.xml.rels": xlsxRelsXML("worksheets/missing.xml"),
		}, FailureIngestInvalidXLSX},
		{"shared string index out of range", map[string][]byte{
			"[Content_Types].xml":        xlsxContentTypes(""),
			"xl/workbook.xml":            xlsxWorkbookXML(`<sheet name="S" sheetId="1" r:id="rId1"/>`),
			"xl/_rels/workbook.xml.rels": xlsxRelsXML("worksheets/sheet1.xml"),
			"xl/sharedStrings.xml":       xlsxSharedStringsXML("only-one"),
			"xl/worksheets/sheet1.xml": xlsxSheetXML(
				`<row r="1"><c r="A1" t="s"><v>0</v></c></row>` +
					`<row r="2"><c r="A2" t="s"><v>9</v></c></row>`),
		}, FailureIngestInvalidXLSX},
		{"sheet is not xml", map[string][]byte{
			"[Content_Types].xml":        xlsxContentTypes(""),
			"xl/workbook.xml":            xlsxWorkbookXML(`<sheet name="S" sheetId="1" r:id="rId1"/>`),
			"xl/_rels/workbook.xml.rels": xlsxRelsXML("worksheets/sheet1.xml"),
			"xl/worksheets/sheet1.xml":   []byte("<worksheet><sheetData><row>"),
		}, FailureIngestInvalidXLSX},
	}
	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			_, err := parseXLSXContent(t, xlsxZip(t, testCase.entries))
			var parseErr *ParseError
			if !errors.As(err, &parseErr) || parseErr.FailureCode != testCase.wantCode {
				t.Fatalf("error = %v, want %q", err, testCase.wantCode)
			}
		})
	}
}

// columnLetters renders a zero-based column index as spreadsheet letters.
func columnLetters(index int) string {
	letters := ""
	value := index + 1
	for value > 0 {
		value--
		letters = string(rune('A'+value%26)) + letters
		value /= 26
	}
	return letters
}
