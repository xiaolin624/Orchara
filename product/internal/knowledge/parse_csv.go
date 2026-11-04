package knowledge

// F10/T059 CSV parser.
//
// The parse is deliberately source-preserving: Parsed.Text is the decoded source
// bytes (one UTF-8 BOM may be stripped and recorded), and every row range is a
// rune span into that text. A citation can therefore be re-derived from the
// sealed artifact with no second renderer: read the bytes, decode UTF-8 after the
// same recorded normalization, slice the span, compare. Nothing is evaluated.
//
// Row numbering is the logical record number, 1-based, with the header as row 1
// and the first data row as row 2. Empty lines are skipped and do not consume a
// number. A quoted field may contain commas, doubled quotes and line breaks; the
// row's span then covers more than one physical line, which is why the row range
// is recorded separately from the (truthful) physical line range.

import (
	"errors"
	"strings"
)

// csvParser handles text/csv.
type csvParser struct{}

func (csvParser) MediaType() string { return MediaTypeCSV }
func (csvParser) Version() string   { return ParserVersionCSV }

func (csvParser) Parse(content []byte) (Parsed, error) {
	text, normalization, err := decodeText(content)
	if err != nil {
		return Parsed{}, err
	}
	// A NUL byte is not text. The upload gate already refuses it (T028 sniffs a
	// NUL byte as binary); the parser re-checks so a seeded artifact cannot
	// become a document whose text would later be truncated by a text column.
	if strings.IndexByte(text, 0) >= 0 {
		return Parsed{}, ErrInvalidEncoding
	}
	records, err := scanCSVRecords([]rune(text))
	if err != nil {
		return Parsed{}, err
	}
	if len(records) < 2 {
		// No data rows: a header alone is not indexable content.
		return Parsed{}, ErrEmptyContent
	}
	header := records[0].fields
	if len(header) > MaxTableColumns {
		return Parsed{}, csvLimitError(FailureIngestColumnLimit, 1, "columns", len(header))
	}
	table := &Table{HeaderRow: 1, Columns: header}
	table.Columns, table.ColumnsOmitted = columnsForCitation(header)
	for _, record := range records[1:] {
		if fields := len(record.fields); fields > MaxTableColumns {
			return Parsed{}, csvLimitError(FailureIngestColumnLimit, record.number, "columns", fields)
		}
		if size := record.end - record.start; size > MaxTableRowChars {
			return Parsed{}, csvLimitError(FailureIngestRowTooLarge, record.number, "row_chars", size)
		}
		table.Rows = append(table.Rows, TableRow{
			Number: record.number, StartChar: record.start, EndChar: record.end,
		})
	}
	if len(table.Rows) > MaxTableRows {
		return Parsed{}, csvLimitError(FailureIngestRowLimit, 0, "rows", len(table.Rows))
	}
	return Parsed{
		Text:          text,
		ParserVersion: ParserVersionCSV,
		Normalization: normalization,
		TextRender:    TextRenderSourceUTF8,
		Table:         table,
	}, nil
}

func csvLimitError(code string, row int, limit string, value int) *ParseError {
	detail := ParseDetail{Limit: limit, Value: value}
	if row > 0 {
		detail.Row = row
	}
	return &ParseError{FailureCode: code, Location: detail}
}

// csvRecord is one non-empty logical CSV record. start is the rune offset of its
// first character and end is one past its last character; the record terminator
// is not part of the span.
type csvRecord struct {
	number int
	start  int
	end    int
	fields []string
}

// scanCSVRecords parses RFC 4180-ish content: comma-separated fields, optional
// double quotes around a field, a doubled quote inside a quoted field, and CRLF,
// LF or a bare CR as the record terminator. Fully empty lines are skipped and do
// not consume a row number. Quoting rules are strict (a quote in an unquoted
// field or text after a closing quote is malformed) because a lenient recovery
// would record a row range that does not faithfully describe the source.
func scanCSVRecords(runes []rune) ([]csvRecord, error) {
	records := make([]csvRecord, 0, 16)
	index := 0
	for index < len(runes) {
		record, next, empty, err := scanCSVRecord(runes, index, len(records)+1)
		if err != nil {
			return nil, err
		}
		index = next
		if empty {
			continue
		}
		records = append(records, record)
	}
	return records, nil
}

func scanCSVRecord(runes []rune, at, number int) (csvRecord, int, bool, error) {
	start := at
	var (
		fields  []string
		field   strings.Builder
		inQuote bool
		quoted  bool
	)
	index := at
	for index < len(runes) {
		char := runes[index]
		if inQuote {
			if char == '"' {
				if index+1 < len(runes) && runes[index+1] == '"' {
					field.WriteRune('"')
					index += 2
					continue
				}
				inQuote = false
				quoted = true
				index++
				continue
			}
			field.WriteRune(char)
			index++
			continue
		}
		if quoted {
			// Only a delimiter or a record terminator may follow a closing quote.
			switch char {
			case ',':
				fields = append(fields, field.String())
				field.Reset()
				quoted = false
				index++
				continue
			case '\r', '\n':
				fields = append(fields, field.String())
				return finishCSVRecord(fields, start, index, number, runes, index)
			default:
				return csvRecord{}, 0, false, &ParseError{
					FailureCode: FailureIngestInvalidCSV,
					Location:    ParseDetail{Row: number, Part: "text_after_closing_quote"},
				}
			}
		}
		switch char {
		case ',':
			fields = append(fields, field.String())
			field.Reset()
			index++
		case '"':
			if field.Len() > 0 {
				return csvRecord{}, 0, false, &ParseError{
					FailureCode: FailureIngestInvalidCSV,
					Location:    ParseDetail{Row: number, Part: "quote_in_unquoted_field"},
				}
			}
			inQuote = true
			index++
		case '\r', '\n':
			fields = append(fields, field.String())
			return finishCSVRecord(fields, start, index, number, runes, index)
		default:
			field.WriteRune(char)
			index++
		}
	}
	if inQuote {
		return csvRecord{}, 0, false, &ParseError{
			FailureCode: FailureIngestInvalidCSV,
			Location:    ParseDetail{Row: number, Part: "unterminated_quote"},
		}
	}
	fields = append(fields, field.String())
	return csvRecord{number: number, start: start, end: len(runes), fields: fields}, len(runes),
		false, nil
}

// finishCSVRecord completes a record whose terminator starts at terminator and
// returns the index of the first rune after the terminator (CRLF counts as one).
func finishCSVRecord(
	fields []string, start, end, number int, runes []rune, terminator int,
) (csvRecord, int, bool, error) {
	next := terminator + 1
	if runes[terminator] == '\r' && next < len(runes) && runes[next] == '\n' {
		next++
	}
	// end is the terminator offset: the span excludes it. A record with a single
	// empty unquoted field is a blank line, not a row.
	empty := end == start && len(fields) == 1 && fields[0] == ""
	return csvRecord{number: number, start: start, end: end, fields: fields}, next, empty, nil
}

// asParseError is used by tests and diagnostics to keep the closed-code
// contract in one place.
func asParseError(err error) (*ParseError, bool) {
	var parseErr *ParseError
	if errors.As(err, &parseErr) {
		return parseErr, true
	}
	return nil, false
}
