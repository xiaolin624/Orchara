// Package knowledge is the F10/T055 knowledge-ingestion domain.
//
// It owns four things:
//
//   - **parser seam**: a closed registry from an admitted source media type to a
//     deterministic parser (`text/plain` -> txt, `text/markdown` -> md,
//     `text/csv` and the OOXML spreadsheet type -> the tabular family). PDF
//     (T060 #61) registers its parser here later.
//   - **chunker**: deterministic paragraph/overlap chunking with a SourceSpan
//     (character offsets and a 1-based inclusive line range).
//   - **service**: owner-scoped knowledge bases/documents, the document-ingest
//     enqueue, the non-contract query and chunk-citation reads.
//   - **citation**: the traceability document stored on every chunk.
//
// The service never decides that a document is ready. Only the document_ingest
// completion guard in internal/background may activate a revision, and it does so
// only when chunk rows for that revision exist and every one of them has a ready
// body artifact and a complete embedding.
package knowledge

import (
	"bytes"
	"errors"
	"strings"
	"unicode/utf8"
)

// Media types the F10/T055 slice accepts as source documents. F10/T059 adds the
// tabular family (`text/csv` and the OOXML spreadsheet media type). F10/T060 adds
// `application/pdf` for plain-text PDFs (no OCR promise).
const (
	MediaTypeText     = "text/plain"
	MediaTypeMarkdown = "text/markdown"
	MediaTypeCSV      = "text/csv"
	MediaTypeXLSX     = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)

// Parser versions recorded on the document and in every citation.
const (
	ParserVersionText     = "orchara-text-parser/1"
	ParserVersionMarkdown = "orchara-markdown-parser/1"
	ParserVersionCSV      = "orchara-csv-parser/1"
	ParserVersionXLSX     = "orchara-xlsx-parser/1"
)

// ChunkerVersion is the deterministic chunker revision recorded in citations.
const ChunkerVersion = "orchara-paragraph-overlap-chunker/1"

// Errors a parser returns. They are mapped to stable document failure codes by
// the document_ingest handler.
var (
	// ErrUnsupportedMediaType means no parser is registered for the source
	// artifact's declared media type.
	ErrUnsupportedMediaType = errors.New("no parser is registered for this media type")
	// ErrInvalidEncoding means the bytes are not valid UTF-8 text.
	ErrInvalidEncoding = errors.New("the document is not valid UTF-8 text")
	// ErrEmptyContent means the parser produced no non-whitespace text.
	ErrEmptyContent = errors.New("the document has no non-empty content")
)

// TextNormalization names what a parser did to the source bytes before spans
// were measured. It is recorded in the citation so a reader knows exactly what
// the character offsets refer to.
const (
	NormalizationNone        = "none"
	NormalizationBOMStripped = "utf8_bom_stripped"
)

// Parsed is the parser output: the decoded text and the facts needed to cite it.
type Parsed struct {
	// Text is the decoded document. Character offsets in every chunk span index
	// this string as runes, after the recorded normalization.
	Text string
	// ParserVersion is the stable parser revision.
	ParserVersion string
	// Normalization records the only transformation applied to the bytes.
	Normalization string
	// TextRender names what Text is when it is not simply the source bytes:
	// empty for txt/md (source text), TextRenderSourceUTF8 for CSV,
	// TextRenderSheetCSV for an XLSX worksheet rendering. It is recorded in the
	// citation so a reader knows what a character span indexes.
	TextRender string
	// Table is set by a tabular parser; it carries the row ranges the row-batch
	// chunker turns into citations. Nil for plain text.
	Table *Table
	// PDF is set by the PDF parser; it carries the page map (rune offsets per
	// page) and the pages that had no text layer. Nil for every other family.
	PDF *PDF
}

// Parser is the seam later formats extend. Parse must be deterministic and must
// never consult a model, a network or the clock.
type Parser interface {
	// MediaType is the declared source media type this parser handles.
	MediaType() string
	// Version is the stable parser revision recorded on the document.
	Version() string
	Parse(content []byte) (Parsed, error)
}

var parserRegistry = map[string]Parser{
	MediaTypeText:     textParser{},
	MediaTypeMarkdown: markdownParser{},
	MediaTypeCSV:      csvParser{},
	MediaTypeXLSX:     xlsxParser{},
	MediaTypePDF:      pdfParser{},
}

// ParserFor returns the registered parser for a declared media type.
func ParserFor(mediaType string) (Parser, bool) {
	parser, ok := parserRegistry[strings.ToLower(strings.TrimSpace(mediaType))]
	return parser, ok
}

// SupportedMediaTypes lists the registered parser media types (stable order).
func SupportedMediaTypes() []string {
	return []string{MediaTypeText, MediaTypeMarkdown, MediaTypeCSV, MediaTypeXLSX, MediaTypePDF}
}

type textParser struct{}

func (textParser) MediaType() string { return MediaTypeText }
func (textParser) Version() string   { return ParserVersionText }

func (textParser) Parse(content []byte) (Parsed, error) {
	text, normalization, err := decodeText(content)
	if err != nil {
		return Parsed{}, err
	}
	if strings.TrimSpace(text) == "" {
		return Parsed{}, ErrEmptyContent
	}
	return Parsed{Text: text, ParserVersion: ParserVersionText, Normalization: normalization}, nil
}

// markdownParser shares the byte decoding but keeps its own version so a later
// markdown-specific treatment is a new revision rather than a silent change of
// meaning for already-ingested documents.
type markdownParser struct{}

func (markdownParser) MediaType() string { return MediaTypeMarkdown }
func (markdownParser) Version() string   { return ParserVersionMarkdown }

func (markdownParser) Parse(content []byte) (Parsed, error) {
	text, normalization, err := decodeText(content)
	if err != nil {
		return Parsed{}, err
	}
	if strings.TrimSpace(text) == "" {
		return Parsed{}, ErrEmptyContent
	}
	return Parsed{
		Text: text, ParserVersion: ParserVersionMarkdown, Normalization: normalization,
	}, nil
}

// decodeText validates UTF-8 and strips exactly one leading BOM. No other
// transformation is applied: line endings stay as authored, so the recorded span
// is a truthful offset into the decoded source.
func decodeText(content []byte) (string, string, error) {
	if !utf8.Valid(content) {
		return "", "", ErrInvalidEncoding
	}
	normalization := NormalizationNone
	trimmed := content
	if bytes.HasPrefix(content, []byte{0xEF, 0xBB, 0xBF}) {
		trimmed = content[3:]
		normalization = NormalizationBOMStripped
	}
	return string(trimmed), normalization, nil
}
