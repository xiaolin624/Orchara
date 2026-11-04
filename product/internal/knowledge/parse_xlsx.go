package knowledge

// F10/T059 XLSX parser.
//
// The parser reads the OOXML package with the standard library only: the zip
// central directory, xl/workbook.xml, the workbook relationships,
// xl/sharedStrings.xml and the first worksheet. It is a data reader, never a
// workbook engine:
//
//   - a cell that carries a formula (<f>) is refused with formula_not_supported
//     and its row/column/cell location. The cached value is never used, and no
//     formula is evaluated;
//   - DTD/entity declarations are refused before any XML part is parsed (Go's
//     decoder does not expand them either, but a document that declares them is
//     outside what this reader accepts at all);
//   - the archive is bounded with the *existing* artifacts admission constants
//     (entries, uncompressed bytes, compression ratio, path traversal) plus the
//     workbook caps in table.go; content is not trusted before those pass;
//   - a worksheet is rendered to a deterministic CSV text (one line per table
//     row, header first) so character spans and row ranges can be recorded as
//     for any other chunk. Worksheet row numbers are the source's own row
//     numbers; a chunk citation carries them verbatim.

import (
	"archive/zip"
	"bytes"
	"encoding/csv"
	"encoding/xml"
	"errors"
	"fmt"
	"io"
	"math"
	"path"
	"strconv"
	"strings"

	"orchara/platform/internal/artifacts"
)

const (
	xlsxContentTypesPart  = "[Content_Types].xml"
	xlsxWorkbookPart      = "xl/workbook.xml"
	xlsxWorkbookRelsPart  = "xl/_rels/workbook.xml.rels"
	xlsxSharedStringsPart = "xl/sharedStrings.xml"
	// maxXLSXContentTypesBytes bounds the metadata entry admission also bounds.
	maxXLSXContentTypesBytes = 1 << 20
)

type xlsxParser struct{}

func (xlsxParser) MediaType() string { return MediaTypeXLSX }
func (xlsxParser) Version() string   { return ParserVersionXLSX }

func (xlsxParser) Parse(content []byte) (Parsed, error) {
	archive, err := zip.NewReader(bytes.NewReader(content), int64(len(content)))
	if err != nil {
		return Parsed{}, xlsxFailure(FailureIngestInvalidXLSX, "archive")
	}
	files, err := inspectXLSXArchive(archive)
	if err != nil {
		return Parsed{}, err
	}
	if err := rejectXLSXMacro(files); err != nil {
		return Parsed{}, err
	}

	workbookPayload, err := readXLSXPart(files, xlsxWorkbookPart, "workbook", true)
	if err != nil {
		return Parsed{}, err
	}
	sheets, err := parseXLSXWorkbook(workbookPayload)
	if err != nil {
		return Parsed{}, err
	}
	relsPayload, err := readXLSXPart(files, xlsxWorkbookRelsPart, "relationships", true)
	if err != nil {
		return Parsed{}, err
	}
	target, err := xlsxWorksheetTarget(relsPayload, sheets[0].relationshipID)
	if err != nil {
		return Parsed{}, err
	}
	target, ok := resolveXLSXTarget(target)
	if !ok {
		return Parsed{}, xlsxFailure(FailureIngestArchivePath, "worksheet_target")
	}
	if _, ok := files[target]; !ok {
		return Parsed{}, xlsxFailure(FailureIngestInvalidXLSX, "worksheet")
	}

	shared, err := xlsxSharedStrings(files)
	if err != nil {
		return Parsed{}, err
	}
	worksheetPayload, err := readXLSXPart(files, target, "worksheet", true)
	if err != nil {
		return Parsed{}, err
	}
	table, text, err := parseXLSXWorksheet(worksheetPayload, sheets[0].name, shared)
	if err != nil {
		return Parsed{}, err
	}
	return Parsed{
		Text:          text,
		ParserVersion: ParserVersionXLSX,
		Normalization: NormalizationNone,
		TextRender:    TextRenderSheetCSV,
		Table:         table,
	}, nil
}

func xlsxFailure(code, part string) *ParseError {
	return &ParseError{FailureCode: code, Location: ParseDetail{Part: part}}
}

// inspectXLSXArchive applies the archive limits to the central directory before
// any part is decompressed, and returns the entries by their stored name.
func inspectXLSXArchive(archive *zip.Reader) (map[string]*zip.File, error) {
	files := make(map[string]*zip.File, len(archive.File))
	var totalUncompressed, totalCompressed uint64
	for _, entry := range archive.File {
		if entry.Flags&0x1 != 0 {
			// An encrypted entry cannot be inspected, so it cannot be trusted.
			return nil, xlsxFailure(FailureIngestInvalidXLSX, "encrypted_entry")
		}
		if entry.Method != zip.Store && entry.Method != zip.Deflate {
			return nil, xlsxFailure(FailureIngestInvalidXLSX, "compression_method")
		}
		if !artifacts.SafeArchivePath(entry.Name) {
			return nil, xlsxFailure(FailureIngestArchivePath, "entry_name")
		}
		if code, detail := xlsxEntryArchiveDecision(entry.UncompressedSize64, entry.CompressedSize64); code != "" {
			return nil, &ParseError{FailureCode: code, Location: detail}
		}
		totalUncompressed = saturatingAdd(totalUncompressed, entry.UncompressedSize64)
		totalCompressed = saturatingAdd(totalCompressed, entry.CompressedSize64)
		files[entry.Name] = entry
	}
	if code, detail := xlsxArchiveDecision(len(archive.File), totalUncompressed, totalCompressed); code != "" {
		return nil, &ParseError{FailureCode: code, Location: detail}
	}
	return files, nil
}

// xlsxArchiveDecision is the pure whole-archive decision, shared with its unit
// test. A compression ratio above the admitted ceiling is a bomb, not a table.
func xlsxArchiveDecision(entries int, uncompressed, compressed uint64) (string, ParseDetail) {
	if entries > artifacts.MaxArchiveEntries {
		return FailureIngestArchiveLimit, ParseDetail{Limit: "entries", Value: entries}
	}
	if uncompressed > artifacts.MaxArchiveUncompressedBytes {
		return FailureIngestArchiveLimit, ParseDetail{
			Limit: "uncompressed_bytes", Value: clampToInt(uncompressed),
		}
	}
	if reason := xlsxRatioReason(uncompressed, compressed); reason != "" {
		return FailureIngestArchiveLimit, ParseDetail{
			Limit: reason, Value: clampToInt(uncompressed),
		}
	}
	return "", ParseDetail{}
}

// xlsxEntryArchiveDecision is the pure per-entry decision. It catches one
// highly-compressed entry hidden next to incompressible data, which a total-only
// ratio can miss.
func xlsxEntryArchiveDecision(uncompressed, compressed uint64) (string, ParseDetail) {
	if uncompressed > artifacts.MaxArchiveUncompressedBytes {
		return FailureIngestArchiveLimit, ParseDetail{
			Limit: "entry_uncompressed_bytes", Value: clampToInt(uncompressed),
		}
	}
	if reason := xlsxRatioReason(uncompressed, compressed); reason != "" {
		return FailureIngestArchiveLimit, ParseDetail{
			Limit: reason, Value: clampToInt(uncompressed),
		}
	}
	return "", ParseDetail{}
}

func xlsxRatioReason(uncompressed, compressed uint64) string {
	if uncompressed == 0 {
		return ""
	}
	if compressed == 0 || uncompressed/compressed > artifacts.MaxZipCompressionRatio {
		return "compression_ratio"
	}
	return ""
}

func saturatingAdd(left, right uint64) uint64 {
	if left > math.MaxUint64-right {
		return math.MaxUint64
	}
	return left + right
}

func clampToInt(value uint64) int {
	if value > math.MaxInt32 {
		return math.MaxInt32
	}
	return int(value)
}

// rejectXLSXMacro refuses macro-enabled packages before a worksheet is opened.
// Both the part names and the content-types declaration are checked, mirroring
// the upload admission rule.
func rejectXLSXMacro(files map[string]*zip.File) error {
	for name := range files {
		lower := strings.ToLower(name)
		if strings.Contains(lower, "vbaproject.bin") ||
			strings.HasPrefix(lower, "xl/macrosheets/") {
			return xlsxFailure(FailureIngestMacroPresent, "macro")
		}
	}
	if entry, ok := files[xlsxContentTypesPart]; ok {
		payload, err := readXLSXEntry(entry, maxXLSXContentTypesBytes)
		if err != nil {
			return xlsxFailure(FailureIngestInvalidXLSX, "content_types")
		}
		if strings.Contains(strings.ToLower(string(payload)), "macroenabled") {
			return xlsxFailure(FailureIngestMacroPresent, "macro")
		}
	}
	return nil
}

// readXLSXPart reads one XML part, refusing DTD/entity declarations first. label
// is a fixed, safe name for the part, never a client-supplied path.
func readXLSXPart(files map[string]*zip.File, name, label string, required bool) ([]byte, error) {
	entry, ok := files[name]
	if !ok {
		if required {
			return nil, xlsxFailure(FailureIngestInvalidXLSX, label)
		}
		return nil, nil
	}
	payload, err := readXLSXEntry(entry, artifacts.MaxArchiveUncompressedBytes)
	if err != nil {
		return nil, xlsxFailure(FailureIngestInvalidXLSX, label)
	}
	if xlsxHasDTD(payload) {
		return nil, xlsxFailure(FailureIngestDTDNotSupported, label)
	}
	return payload, nil
}

func xlsxHasDTD(payload []byte) bool {
	lower := bytes.ToLower(payload)
	return bytes.Contains(lower, []byte("<!doctype")) ||
		bytes.Contains(lower, []byte("<!entity"))
}

func readXLSXEntry(entry *zip.File, limit uint64) ([]byte, error) {
	reader, err := entry.Open()
	if err != nil {
		return nil, err
	}
	defer func() { _ = reader.Close() }()
	// The central directory sizes can lie; the read is bounded independently and
	// the zip reader verifies the CRC while decompressing.
	payload, err := io.ReadAll(io.LimitReader(reader, int64(limit)+1))
	if err != nil {
		return nil, err
	}
	if uint64(len(payload)) > limit {
		return nil, fmt.Errorf("archive entry exceeds %d bytes", limit)
	}
	return payload, nil
}

type xlsxSheetRef struct {
	name           string
	relationshipID string
}

// parseXLSXWorkbook lists the workbook's sheets in workbook order and bounds the
// count before any sheet is opened.
func parseXLSXWorkbook(payload []byte) ([]xlsxSheetRef, error) {
	decoder := xml.NewDecoder(bytes.NewReader(payload))
	sheets := make([]xlsxSheetRef, 0, 4)
	for {
		token, err := decoder.Token()
		if errors.Is(err, io.EOF) {
			break
		}
		if err != nil {
			return nil, xlsxFailure(FailureIngestInvalidXLSX, "workbook")
		}
		start, ok := token.(xml.StartElement)
		if !ok || start.Name.Local != "sheet" {
			continue
		}
		reference := xlsxSheetRef{}
		for _, attribute := range start.Attr {
			switch attribute.Name.Local {
			case "name":
				reference.name = attribute.Value
			case "id":
				reference.relationshipID = attribute.Value
			}
		}
		if reference.name == "" || reference.relationshipID == "" {
			return nil, xlsxFailure(FailureIngestInvalidXLSX, "sheet_reference")
		}
		sheets = append(sheets, reference)
		if len(sheets) > MaxTableWorksheets {
			return nil, &ParseError{
				FailureCode: FailureIngestWorksheetLimit,
				Location:    ParseDetail{Limit: "worksheets", Value: len(sheets)},
			}
		}
	}
	if len(sheets) == 0 {
		return nil, xlsxFailure(FailureIngestInvalidXLSX, "no_worksheets")
	}
	return sheets, nil
}

// xlsxWorksheetTarget resolves the relationship id of the first sheet.
func xlsxWorksheetTarget(payload []byte, relationshipID string) (string, error) {
	decoder := xml.NewDecoder(bytes.NewReader(payload))
	for {
		token, err := decoder.Token()
		if errors.Is(err, io.EOF) {
			break
		}
		if err != nil {
			return "", xlsxFailure(FailureIngestInvalidXLSX, "relationships")
		}
		start, ok := token.(xml.StartElement)
		if !ok || start.Name.Local != "Relationship" {
			continue
		}
		var id, target string
		for _, attribute := range start.Attr {
			switch attribute.Name.Local {
			case "Id":
				id = attribute.Value
			case "Target":
				target = attribute.Value
			}
		}
		if id == relationshipID {
			if target == "" {
				return "", xlsxFailure(FailureIngestInvalidXLSX, "relationships")
			}
			return target, nil
		}
	}
	return "", xlsxFailure(FailureIngestInvalidXLSX, "relationships")
}

// resolveXLSXTarget resolves a relationship target against the workbook part's
// folder ("xl/") and refuses anything that is not a safe package-relative path.
func resolveXLSXTarget(target string) (string, bool) {
	resolved := strings.ReplaceAll(target, "\\", "/")
	if strings.HasPrefix(resolved, "/") {
		resolved = resolved[1:]
	} else {
		resolved = "xl/" + resolved
	}
	if !artifacts.SafeArchivePath(resolved) {
		return "", false
	}
	return path.Clean(resolved), true
}

// xlsxSharedStrings reads xl/sharedStrings.xml. Rich-text runs contribute their
// text runs; the count is bounded by the same cell ceiling as the worksheet.
func xlsxSharedStrings(files map[string]*zip.File) ([]string, error) {
	payload, err := readXLSXPart(files, xlsxSharedStringsPart, "shared_strings", false)
	if err != nil {
		return nil, err
	}
	if payload == nil {
		return nil, nil
	}
	decoder := xml.NewDecoder(bytes.NewReader(payload))
	values := make([]string, 0, 16)
	var (
		builder  strings.Builder
		inString bool
		inText   bool
	)
	for {
		token, err := decoder.Token()
		if errors.Is(err, io.EOF) {
			break
		}
		if err != nil {
			return nil, xlsxFailure(FailureIngestInvalidXLSX, "shared_strings")
		}
		switch value := token.(type) {
		case xml.StartElement:
			switch value.Name.Local {
			case "si":
				inString = true
				inText = false
				builder.Reset()
			case "t":
				if inString {
					inText = true
				}
			}
		case xml.CharData:
			if inString && inText {
				builder.Write(value)
			}
		case xml.EndElement:
			if !inString {
				continue
			}
			switch value.Name.Local {
			case "t":
				inText = false
			case "si":
				inString = false
				values = append(values, builder.String())
				if len(values) > MaxTableCells {
					return nil, &ParseError{
						FailureCode: FailureIngestCellLimit,
						Location:    ParseDetail{Limit: "shared_strings", Value: len(values)},
					}
				}
			}
		}
	}
	if inString {
		return nil, xlsxFailure(FailureIngestInvalidXLSX, "shared_strings")
	}
	return values, nil
}

// xlsxCell is one <c> element. A non-empty Formula slice means the cell carries
// a formula; its cached <v> is never read as content.
type xlsxCell struct {
	Ref     string        `xml:"r,attr"`
	Type    string        `xml:"t,attr"`
	Formula []xlsxFormula `xml:"f"`
	Value   string        `xml:"v"`
	Inline  *xlsxInline   `xml:"is"`
}

type xlsxFormula struct {
	Value string `xml:",chardata"`
}

type xlsxInline struct {
	Text string    `xml:"t"`
	Runs []xlsxRun `xml:"r"`
}

type xlsxRun struct {
	Text string `xml:"t"`
}

// parseXLSXWorksheet renders the first worksheet and records its row ranges.
//
// Row numbers are the worksheet's own `r` attributes (1-based). Rows whose cells
// are all empty are skipped, as with CSV blank lines, and do not become chunks.
func parseXLSXWorksheet(payload []byte, sheetName string, shared []string) (*Table, string, error) {
	decoder := xml.NewDecoder(bytes.NewReader(payload))
	table := &Table{SheetName: sheetName}
	var (
		text        strings.Builder
		runeCount   int
		lastRow     int
		inSheetData bool
		headerSeen  bool
		cellCount   int
		rowNumber   int
		rowValues   []string
	)
	for {
		token, err := decoder.Token()
		if errors.Is(err, io.EOF) {
			break
		}
		if err != nil {
			return nil, "", xlsxFailure(FailureIngestInvalidXLSX, "worksheet")
		}
		switch value := token.(type) {
		case xml.StartElement:
			switch value.Name.Local {
			case "sheetData":
				inSheetData = true
			case "row":
				if !inSheetData {
					continue
				}
				rowNumber = lastRow + 1
				for _, attribute := range value.Attr {
					if attribute.Name.Local != "r" {
						continue
					}
					declared, err := strconv.Atoi(strings.TrimSpace(attribute.Value))
					if err != nil || declared < 1 || declared <= lastRow {
						return nil, "", xlsxFailure(FailureIngestInvalidXLSX, "row_number")
					}
					rowNumber = declared
				}
				rowValues = nil
			case "c":
				if !inSheetData {
					continue
				}
				cellCount++
				if cellCount > MaxTableCells {
					return nil, "", &ParseError{
						FailureCode: FailureIngestCellLimit,
						Location:    ParseDetail{Limit: "cells", Value: cellCount},
					}
				}
				var cell xlsxCell
				if err := decoder.DecodeElement(&cell, &value); err != nil {
					return nil, "", xlsxFailure(FailureIngestInvalidXLSX, "worksheet")
				}
				index, ok := xlsxCellColumn(cell.Ref, len(rowValues))
				if !ok {
					return nil, "", xlsxFailure(FailureIngestInvalidXLSX, "cell_reference")
				}
				if index >= MaxTableColumns {
					return nil, "", &ParseError{
						FailureCode: FailureIngestColumnLimit,
						Location: ParseDetail{
							Sheet: sheetName, Row: rowNumber,
							Column: xlsxColumnLetters(index), Limit: "columns", Value: index + 1,
						},
					}
				}
				if len(cell.Formula) > 0 {
					// A formula cell is refused with its location. Its cached
					// value is not used: an unevaluated formula's cache can be
					// stale or forged, so it is not content the platform verified.
					return nil, "", &ParseError{
						FailureCode: FailureIngestFormulaNotSupported,
						Location: ParseDetail{
							Sheet: sheetName, Row: rowNumber,
							Column: xlsxColumnLetters(index),
							Cell:   xlsxColumnLetters(index) + strconv.Itoa(rowNumber),
						},
					}
				}
				cellValue, err := xlsxCellText(cell, shared, sheetName, rowNumber, index)
				if err != nil {
					return nil, "", err
				}
				for len(rowValues) <= index {
					rowValues = append(rowValues, "")
				}
				rowValues[index] = cellValue
			}
		case xml.EndElement:
			switch value.Name.Local {
			case "row":
				if !inSheetData {
					continue
				}
				lastRow = rowNumber
				rendered, ok, err := xlsxRenderRow(rowValues)
				if err != nil {
					return nil, "", err
				}
				if !ok {
					continue
				}
				if len([]rune(rendered))-1 > MaxTableRowChars {
					return nil, "", &ParseError{
						FailureCode: FailureIngestRowTooLarge,
						Location:    ParseDetail{Sheet: sheetName, Row: rowNumber, Limit: "row_chars"},
					}
				}
				if !headerSeen {
					headerSeen = true
					table.HeaderRow = rowNumber
					table.Columns, table.ColumnsOmitted = columnsForCitation(rowValues)
				} else {
					if len(table.Rows) >= MaxTableRows {
						return nil, "", &ParseError{
							FailureCode: FailureIngestRowLimit,
							Location:    ParseDetail{Limit: "rows", Value: len(table.Rows) + 1},
						}
					}
					table.Rows = append(table.Rows, TableRow{
						Number: rowNumber, StartChar: runeCount, EndChar: runeCount + len([]rune(rendered)) - 1,
					})
				}
				text.WriteString(rendered)
				runeCount += len([]rune(rendered))
			case "sheetData":
				inSheetData = false
			}
		}
	}
	if !headerSeen || len(table.Rows) == 0 {
		// A workbook with nothing but a header has nothing to index.
		return nil, "", ErrEmptyContent
	}
	return table, text.String(), nil
}

// xlsxRenderRow renders one row as a CSV line (terminated by "\n"). ok is false
// for a row whose cells are all empty: formatting residue is not a table row.
func xlsxRenderRow(values []string) (string, bool, error) {
	nonEmpty := false
	for _, value := range values {
		if value != "" {
			nonEmpty = true
			break
		}
	}
	if !nonEmpty {
		return "", false, nil
	}
	var buffer bytes.Buffer
	writer := csv.NewWriter(&buffer)
	if err := writer.Write(values); err != nil {
		return "", false, xlsxFailure(FailureIngestInvalidXLSX, "worksheet")
	}
	writer.Flush()
	if err := writer.Error(); err != nil {
		return "", false, xlsxFailure(FailureIngestInvalidXLSX, "worksheet")
	}
	return buffer.String(), true, nil
}

// xlsxCellText resolves one cell's display text. Only text is extracted; no
// number format, date conversion or calculation is applied.
func xlsxCellText(cell xlsxCell, shared []string, sheetName string, row, column int) (string, error) {
	switch cell.Type {
	case "", "n", "str", "b", "e", "d":
		return cell.Value, nil
	case "s":
		if strings.TrimSpace(cell.Value) == "" {
			return "", nil
		}
		index, err := strconv.Atoi(strings.TrimSpace(cell.Value))
		if err != nil || index < 0 || index >= len(shared) {
			return "", &ParseError{
				FailureCode: FailureIngestInvalidXLSX,
				Location: ParseDetail{
					Sheet: sheetName, Row: row, Column: xlsxColumnLetters(column),
					Cell: xlsxColumnLetters(column) + strconv.Itoa(row), Part: "shared_string",
				},
			}
		}
		return shared[index], nil
	case "inlineStr":
		if cell.Inline == nil {
			return "", nil
		}
		var builder strings.Builder
		builder.WriteString(cell.Inline.Text)
		for _, run := range cell.Inline.Runs {
			builder.WriteString(run.Text)
		}
		return builder.String(), nil
	default:
		return "", &ParseError{
			FailureCode: FailureIngestInvalidXLSX,
			Location: ParseDetail{
				Sheet: sheetName, Row: row, Column: xlsxColumnLetters(column),
				Cell: xlsxColumnLetters(column) + strconv.Itoa(row), Part: "cell_type",
			},
		}
	}
}

// xlsxCellColumn resolves a cell reference ("B12") or falls back to the next
// position when the reference is absent.
func xlsxCellColumn(reference string, nextPosition int) (int, bool) {
	if reference == "" {
		return nextPosition, true
	}
	letters := 0
	index := 0
	for letters < len(reference) {
		char := reference[letters]
		if char >= 'a' && char <= 'z' {
			char -= 'a' - 'A'
		}
		if char < 'A' || char > 'Z' {
			break
		}
		if letters >= 3 {
			// Spreadsheet column references are at most three letters (XFD).
			return 0, false
		}
		index = index*26 + int(char-'A'+1)
		letters++
	}
	if letters == 0 {
		return 0, false
	}
	return index - 1, true
}

// xlsxColumnLetters renders a zero-based column index as spreadsheet letters.
func xlsxColumnLetters(index int) string {
	letters := ""
	value := index + 1
	for value > 0 {
		value--
		letters = string(rune('A'+value%26)) + letters
		value /= 26
	}
	return letters
}
