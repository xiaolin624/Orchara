package knowledge

// F10/T059: the tabular half of the parser seam.
//
// CSV and XLSX are one input family with one chunker: a chunk is a contiguous
// batch of source rows, and its citation carries the row range so a reader can
// locate the same rows in the sealed source. Nothing here evaluates content:
// values are text, never formulas.

import (
	"fmt"
	"sort"
)

// Tabular chunker revision. Row-batch chunking is a different algorithm from the
// paragraph chunker, so it is a different recorded revision.
const ChunkerVersionTableRows = "orchara-table-row-batch-chunker/1"

// TextRender names what the chunk character offsets index.
const (
	// TextRenderSourceUTF8 means Text is the decoded source bytes themselves, so
	// a span can be re-derived from the sealed artifact with no second renderer.
	TextRenderSourceUTF8 = "source_utf8"
	// TextRenderSheetCSV means Text is the deterministic CSV rendering of one
	// worksheet, produced by ParserVersionXLSX from the sealed workbook. The
	// recorded sheet name plus row range locate the same rows in the source.
	TextRenderSheetCSV = "sheet_csv/1"
)

// Tabular caps. They are enforced before content is trusted, so a table that
// exceeds them becomes a diagnosable failed document, never a ready one.
//
// The archive caps (entries, uncompressed bytes, compression ratio, path
// traversal, macros) are not repeated here: the XLSX parser imports the existing
// artifacts constants instead of inventing a second set (see parse_xlsx.go).
const (
	// MaxTableRows bounds data rows (the header row is not counted). It matches
	// the trusted sales_stats tool's platform cap.
	MaxTableRows = 10_000
	// MaxTableColumns bounds the columns of one table, so a row/header cannot
	// grow without bound.
	MaxTableColumns = 64
	// MaxTableCells bounds header + data cells across the sheet/table.
	MaxTableCells = (MaxTableRows + 1) * MaxTableColumns
	// MaxTableWorksheets bounds the worksheets listed by one XLSX workbook.
	MaxTableWorksheets = 64
	// MaxTableRowChars bounds one source row. A row is never split across
	// chunks, because a split would make the recorded row range describe a
	// fraction of a row; an oversized row is refused instead.
	MaxTableRowChars = 64 << 10
)

// Stable document failure codes for content refusals of the tabular family.
// They are the codes the document_ingest handler returns, so a failed document
// and its job carry the same safe code.
const (
	FailureIngestInvalidCSV          = "ingest_invalid_csv"
	FailureIngestInvalidXLSX         = "ingest_invalid_xlsx"
	FailureIngestFormulaNotSupported = "ingest_formula_not_supported"
	FailureIngestArchiveLimit        = "ingest_archive_limit_exceeded"
	FailureIngestArchivePath         = "ingest_archive_path_traversal"
	FailureIngestMacroPresent        = "ingest_macro_present"
	FailureIngestDTDNotSupported     = "ingest_dtd_not_supported"
	FailureIngestWorksheetLimit      = "ingest_worksheet_limit_exceeded"
	FailureIngestRowLimit            = "ingest_row_limit_exceeded"
	FailureIngestCellLimit           = "ingest_cell_limit_exceeded"
	FailureIngestColumnLimit         = "ingest_column_limit_exceeded"
	FailureIngestRowTooLarge         = "ingest_row_too_large"
)

// parseFailureCodes is the closed set of codes a *ParseError may carry. Keeping
// it closed lets the diagnostics read decide whether re-deriving a location from
// the immutable source is meaningful, and keeps a typo from inventing a code.
var parseFailureCodes = map[string]bool{
	FailureIngestInvalidCSV:          true,
	FailureIngestInvalidXLSX:         true,
	FailureIngestFormulaNotSupported: true,
	FailureIngestArchiveLimit:        true,
	FailureIngestArchivePath:         true,
	FailureIngestMacroPresent:        true,
	FailureIngestDTDNotSupported:     true,
	FailureIngestWorksheetLimit:      true,
	FailureIngestRowLimit:            true,
	FailureIngestCellLimit:           true,
	FailureIngestColumnLimit:         true,
	FailureIngestRowTooLarge:         true,
	// T060 PDF refusals carry their own stable codes and a re-derivable location
	// (page or text-layer threshold).
	FailurePDFInvalid:     true,
	FailurePDFEncrypted:   true,
	FailurePDFNoTextLayer: true,
}

// IsParseFailureCode reports whether code is a content refusal whose location
// can be re-derived from the source by running the recorded parser again.
func IsParseFailureCode(code string) bool { return parseFailureCodes[code] }

// ParseDetail is the safe, user-facing location of a content refusal. It never
// carries bytes, a path or a client-supplied name; every field is data the
// platform read from the source itself.
//
// Row semantics differ by family and are documented per parser:
//   - CSV: the 1-based logical record number, where the header is row 1.
//   - XLSX: the worksheet's own 1-based row number (the `r` attribute).
type ParseDetail struct {
	Sheet  string `json:"sheet,omitempty"`
	Row    int    `json:"row,omitempty"`
	Column string `json:"column,omitempty"`
	Cell   string `json:"cell,omitempty"`
	Part   string `json:"part,omitempty"`
	Limit  string `json:"limit,omitempty"`
	Value  int    `json:"value,omitempty"`
}

// ParseError is a deterministic content refusal: a stable document failure code
// plus the location that makes it diagnosable. It implements error and is
// detected with errors.As, so the handler never has to parse a message.
type ParseError struct {
	FailureCode string
	Location    ParseDetail
}

func (e *ParseError) Error() string {
	if e == nil {
		return "parse failure"
	}
	return fmt.Sprintf("%s (%s)", e.FailureCode, formatParseDetail(e.Location))
}

func formatParseDetail(detail ParseDetail) string {
	parts := make([]string, 0, 4)
	if detail.Sheet != "" {
		parts = append(parts, "sheet="+detail.Sheet)
	}
	if detail.Row > 0 {
		parts = append(parts, fmt.Sprintf("row=%d", detail.Row))
	}
	if detail.Column != "" {
		parts = append(parts, "column="+detail.Column)
	}
	if detail.Cell != "" {
		parts = append(parts, "cell="+detail.Cell)
	}
	if detail.Part != "" {
		parts = append(parts, "part="+detail.Part)
	}
	if detail.Limit != "" {
		parts = append(parts, "limit="+detail.Limit)
	}
	if len(parts) == 0 {
		return "no location"
	}
	sort.Strings(parts)
	return fmt.Sprint(parts)
}

// TableRow is one data row of a parsed table: its 1-based source row number and
// the rune offsets [StartChar, EndChar) that locate it in Parsed.Text.
type TableRow struct {
	Number    int
	StartChar int
	EndChar   int
}

// Table is the tabular parser output. Rows are data rows in source order; the
// header row is not part of a chunk (its number is recorded instead).
type Table struct {
	SheetName string
	HeaderRow int
	// Columns are the header values when they are small enough to repeat in
	// every citation; ColumnsOmitted records that they were too large and are
	// not duplicated there (the header row itself remains locatable).
	Columns        []string
	ColumnsOmitted bool
	Rows           []TableRow
}

// ChunkTable is the row range of one tabular chunk. It is copied verbatim into
// the chunk citation.
type ChunkTable struct {
	SheetName      string
	HeaderRow      int
	RowStart       int
	RowEnd         int
	RowCount       int
	Columns        []string
	ColumnsOmitted bool
}

// ChunkParsed dispatches a parsed document to the chunker that matches its
// family. It is the handler's single entry point; a parser never chunks.
func ChunkParsed(parsed Parsed) []Chunk {
	switch {
	case parsed.PDF != nil:
		return chunkPDFPages(parsed)
	case parsed.Table != nil:
		return chunkTableRows(parsed)
	default:
		return ChunkText(parsed.Text)
	}
}

// ChunkerVersionFor names the chunker revision that produced parsed's chunks.
func ChunkerVersionFor(parsed Parsed) string {
	switch {
	case parsed.PDF != nil:
		return ChunkerVersionPDF
	case parsed.Table != nil:
		return ChunkerVersionTableRows
	default:
		return ChunkerVersion
	}
}

// chunkTableRows packages consecutive rows into a chunk while the slice of
// Parsed.Text from the first row's start to the next row's end stays within
// MaxChunkChars. A single row is never split; an oversized one is refused by
// the parser (FailureIngestRowTooLarge), not silently divided.
func chunkTableRows(parsed Parsed) []Chunk {
	runes := []rune(parsed.Text)
	lineStarts := lineOffsets(runes)
	rows := parsed.Table.Rows
	chunks := make([]Chunk, 0, 1+len(rows)/2)
	for index := 0; index < len(rows); {
		end := index
		for end+1 < len(rows) && rows[end+1].EndChar-rows[index].StartChar <= MaxChunkChars {
			end++
		}
		startChar, endChar := rows[index].StartChar, rows[end].EndChar
		chunks = append(chunks, Chunk{
			No:   len(chunks),
			Text: string(runes[startChar:endChar]),
			Span: Span{
				StartChar: startChar,
				EndChar:   endChar,
				StartLine: lineOf(lineStarts, startChar),
				EndLine:   lineOf(lineStarts, endChar-1),
			},
			Table: &ChunkTable{
				SheetName:      parsed.Table.SheetName,
				HeaderRow:      parsed.Table.HeaderRow,
				RowStart:       rows[index].Number,
				RowEnd:         rows[end].Number,
				RowCount:       end - index + 1,
				Columns:        parsed.Table.Columns,
				ColumnsOmitted: parsed.Table.ColumnsOmitted,
			},
		})
		index = end + 1
	}
	return chunks
}

// columnsForCitation decides whether the header values are small enough to be
// repeated in every citation. The row itself is always locatable via HeaderRow,
// so omitting a huge header loses no traceability.
func columnsForCitation(columns []string) ([]string, bool) {
	total := 0
	for _, column := range columns {
		total += len([]rune(column)) + 1
		if total > MaxChunkChars {
			return nil, true
		}
	}
	return columns, false
}
