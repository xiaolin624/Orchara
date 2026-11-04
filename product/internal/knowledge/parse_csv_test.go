package knowledge

// F10/T059: CSV parsing and row-batch chunking.
//
// The expected values below are independent of the implementation: they are the
// literal source text, offsets found by searching that text, and the numbers a
// reader would compute from the CSV itself. The parser is never asked what it
// thinks the row range is and then asked to confirm it.

import (
	"errors"
	"fmt"
	"strings"
	"testing"
)

const csvSample = "order_id,region,note,amount\r\n" +
	"A1,东区,\"quoted, with comma\",120.50\r\n" +
	"A2,西区,\"line1\r\nline2\",79.50\r\n" +
	"A3,南区,\"say \"\"hi\"\"\",=SUM(D1:D2)\r\n"

func csvRuneSlice(t *testing.T, text string, start, end int) string {
	t.Helper()
	runes := []rune(text)
	if start < 0 || end > len(runes) || start >= end {
		t.Fatalf("span %d..%d is outside %d runes", start, end, len(runes))
	}
	return string(runes[start:end])
}

func TestCSVParserKeepsSourceTextAndRecordsOneBasedRowRanges(t *testing.T) {
	parser, ok := ParserFor(MediaTypeCSV)
	if !ok {
		t.Fatal("no parser for text/csv")
	}
	if parser.Version() != ParserVersionCSV {
		t.Fatalf("version = %q", parser.Version())
	}
	content := append([]byte{0xEF, 0xBB, 0xBF}, []byte(csvSample)...)
	parsed, err := parser.Parse(content)
	if err != nil {
		t.Fatalf("parsing the sample: %v", err)
	}
	// Text is the decoded source itself, exactly as authored (BOM removed, CRLF
	// preserved): a recorded character span must locate bytes in the sealed file.
	if parsed.Text != csvSample {
		t.Fatalf("text = %q, want the source without the BOM", parsed.Text)
	}
	if parsed.Normalization != NormalizationBOMStripped {
		t.Fatalf("normalization = %q, want %q", parsed.Normalization, NormalizationBOMStripped)
	}
	if parsed.TextRender != TextRenderSourceUTF8 {
		t.Fatalf("text render = %q, want %q", parsed.TextRender, TextRenderSourceUTF8)
	}
	if parsed.Table == nil {
		t.Fatal("a CSV parse must expose its table rows")
	}
	table := parsed.Table
	if table.HeaderRow != 1 {
		t.Fatalf("header row = %d, want 1", table.HeaderRow)
	}
	if strings.Join(table.Columns, "|") != "order_id|region|note|amount" {
		t.Fatalf("columns = %v", table.Columns)
	}
	if len(table.Rows) != 3 {
		t.Fatalf("data rows = %d, want 3", len(table.Rows))
	}

	// Independent truth: the row literals as authored, located by search.
	expected := []struct {
		number int
		text   string
	}{
		{2, "A1,东区,\"quoted, with comma\",120.50"},
		{3, "A2,西区,\"line1\r\nline2\",79.50"},
		{4, "A3,南区,\"say \"\"hi\"\"\",=SUM(D1:D2)"},
	}
	for index, want := range expected {
		row := table.Rows[index]
		if row.Number != want.number {
			t.Fatalf("row %d number = %d, want %d", index, row.Number, want.number)
		}
		byteStart := strings.Index(parsed.Text, want.text)
		if byteStart < 0 {
			t.Fatalf("the expected row text %q is not in the parsed text", want.text)
		}
		start := len([]rune(parsed.Text[:byteStart]))
		if row.StartChar != start {
			t.Fatalf("row %d start = %d, want %d", want.number, row.StartChar, start)
		}
		if got := csvRuneSlice(t, parsed.Text, row.StartChar, row.EndChar); got != want.text {
			t.Fatalf("row %d span slices %q, want %q", want.number, got, want.text)
		}
	}
	if got := csvRuneSlice(t, parsed.Text, table.Rows[1].StartChar, table.Rows[1].EndChar); !strings.Contains(got, "\r\n") {
		t.Fatalf("the quoted CRLF row lost its internal newline: %q", got)
	}
	// A CSV has no formula metadata and nothing is ever evaluated: a cell that
	// looks like a spreadsheet formula stays literal text in the recorded span.
	if !strings.Contains(parsed.Text, "=SUM(D1:D2)") {
		t.Fatal("the formula-looking cell was altered; CSV content must never be evaluated")
	}
}

func TestCSVParserRefusesMalformedContentWithSafeDiagnosableCodes(t *testing.T) {
	parser, _ := ParserFor(MediaTypeCSV)

	cases := []struct {
		name string
		data []byte
		code string // "" means a sentinel error
		row  int
	}{
		{"unterminated quote", []byte("a,b\n1,\"x\n"), FailureIngestInvalidCSV, 2},
		{"bare quote in an unquoted field", []byte("a,b\n1,x\"y\n"), FailureIngestInvalidCSV, 2},
		{"header only", []byte("a,b\r\n"), "", 0},
		{"NUL byte", []byte("a,b\n1,\x00\n"), "", 0},
		{"invalid UTF-8", []byte("a,b\n1,\xff\n"), "", 0},
	}
	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			_, err := parser.Parse(testCase.data)
			if err == nil {
				t.Fatal("malformed content was accepted")
			}
			switch testCase.code {
			case "":
				if !errors.Is(err, ErrEmptyContent) && !errors.Is(err, ErrInvalidEncoding) {
					t.Fatalf("error = %v, want ErrEmptyContent or ErrInvalidEncoding", err)
				}
			default:
				var parseErr *ParseError
				if !errors.As(err, &parseErr) {
					t.Fatalf("error = %v, want *ParseError", err)
				}
				if parseErr.FailureCode != testCase.code {
					t.Fatalf("code = %q, want %q", parseErr.FailureCode, testCase.code)
				}
				if testCase.row != 0 && parseErr.Location.Row != testCase.row {
					t.Fatalf("row = %d, want %d", parseErr.Location.Row, testCase.row)
				}
			}
		})
	}

	// A code in a *ParseError is always a stable document failure code, so a
	// caller can never mistake a location for free-form text.
	var parseErr *ParseError
	if errors.As(mustParseError(t, parser, []byte("a,b\n1,\"x")), &parseErr) {
		if !IsParseFailureCode(parseErr.FailureCode) {
			t.Fatalf("%q is not registered as a parser failure code", parseErr.FailureCode)
		}
	}
}

func mustParseError(t *testing.T, parser Parser, content []byte) error {
	t.Helper()
	_, err := parser.Parse(content)
	if err == nil {
		t.Fatal("expected a parse error")
	}
	return err
}

func TestCSVParserEnforcesRowColumnAndRowSizeCaps(t *testing.T) {
	parser, _ := ParserFor(MediaTypeCSV)

	manyColumns := make([]string, MaxTableColumns+1)
	for index := range manyColumns {
		manyColumns[index] = "c" + fmt.Sprint(index)
	}
	_, err := parser.Parse([]byte(strings.Join(manyColumns, ",") + "\n1\n"))
	var parseErr *ParseError
	if !errors.As(err, &parseErr) || parseErr.FailureCode != FailureIngestColumnLimit {
		t.Fatalf("column cap error = %v, want %q", err, FailureIngestColumnLimit)
	}

	var rows strings.Builder
	rows.WriteString("a,b\n")
	for index := 0; index <= MaxTableRows; index++ {
		rows.WriteString("x,y\n")
	}
	if _, err := parser.Parse([]byte(rows.String())); !errors.As(err, &parseErr) ||
		parseErr.FailureCode != FailureIngestRowLimit {
		t.Fatalf("row cap error = %v, want %q", err, FailureIngestRowLimit)
	}

	longRow := "a,b\n" + strings.Repeat("x", MaxTableRowChars+1) + ",y\n"
	if _, err := parser.Parse([]byte(longRow)); !errors.As(err, &parseErr) ||
		parseErr.FailureCode != FailureIngestRowTooLarge {
		t.Fatalf("row size error = %v, want %q", err, FailureIngestRowTooLarge)
	}
}

func TestCSVRowBatchChunkerKeepsEveryRowExactlyOnce(t *testing.T) {
	var source strings.Builder
	source.WriteString("order_id,region,amount\n")
	for index := 0; index < 500; index++ {
		fmt.Fprintf(&source, "A%03d,区域%d,%d.50\n", index, index%7, 100+index)
	}
	parser, _ := ParserFor(MediaTypeCSV)
	parsed, err := parser.Parse([]byte(source.String()))
	if err != nil {
		t.Fatalf("parsing the generated CSV: %v", err)
	}
	chunks := ChunkParsed(parsed)
	if len(chunks) < 2 {
		t.Fatalf("chunks = %d, want at least 2", len(chunks))
	}
	if got := ChunkerVersionFor(parsed); got != ChunkerVersionTableRows {
		t.Fatalf("chunker version = %q, want %q", got, ChunkerVersionTableRows)
	}
	expectedRow := 2
	for index, chunk := range chunks {
		if chunk.No != index {
			t.Fatalf("chunk No = %d, want %d", chunk.No, index)
		}
		if chunk.Table == nil {
			t.Fatalf("chunk %d has no table row range", index)
		}
		if chunk.Table.RowStart != expectedRow {
			t.Fatalf("chunk %d row_start = %d, want %d", index, chunk.Table.RowStart, expectedRow)
		}
		if chunk.Table.RowEnd < chunk.Table.RowStart {
			t.Fatalf("chunk %d row range = %d..%d", index, chunk.Table.RowStart, chunk.Table.RowEnd)
		}
		if chunk.Table.RowCount != chunk.Table.RowEnd-chunk.Table.RowStart+1 {
			t.Fatalf("chunk %d row_count = %d for range %d..%d",
				index, chunk.Table.RowCount, chunk.Table.RowStart, chunk.Table.RowEnd)
		}
		if got := csvRuneSlice(t, parsed.Text, chunk.Span.StartChar, chunk.Span.EndChar); got != chunk.Text {
			t.Fatalf("chunk %d span does not slice back to its body", index)
		}
		if runes := len([]rune(chunk.Text)); runes > MaxChunkChars && chunk.Table.RowCount > 1 {
			t.Fatalf("chunk %d has %d runes across %d rows, over the batch bound",
				index, runes, chunk.Table.RowCount)
		}
		expectedRow = chunk.Table.RowEnd + 1
	}
	if expectedRow != 502 {
		t.Fatalf("chunks stop at row %d, want 501 covered", expectedRow-1)
	}

	// One row longer than the batch bound is not split: splitting it would make
	// the recorded row range describe a fraction of a row.
	parser2 := csvParser{}
	long := "a,b\n" + strings.Repeat("x", MaxChunkChars+100) + ",y\n"
	parsedLong, err := parser2.Parse([]byte(long))
	if err != nil {
		t.Fatalf("parsing a long row: %v", err)
	}
	longChunks := ChunkParsed(parsedLong)
	if len(longChunks) != 1 {
		t.Fatalf("long row chunks = %d, want 1", len(longChunks))
	}
	if longChunks[0].Table.RowStart != 2 || longChunks[0].Table.RowEnd != 2 {
		t.Fatalf("long row range = %d..%d", longChunks[0].Table.RowStart, longChunks[0].Table.RowEnd)
	}
}
