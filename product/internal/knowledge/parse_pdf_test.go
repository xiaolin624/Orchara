package knowledge

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"unicode"
)

// The PDF fixtures are committed and frozen. The reference page texts are the
// authored page as a reader sees it (hand-checked against a page render), not a
// parse produced by the code under test: the test compares the parser's page
// text with that independent reference.
const pdfFixturesDir = "../../tests/fixtures/f10"

type pdfReferenceFixture struct {
	SHA256           string   `json:"sha256"`
	PageCount        int      `json:"page_count"`
	Pages            []string `json:"pages"`
	PagesWithoutText []int    `json:"pages_without_text"`
}

type pdfReferenceFile struct {
	MinTextRunes int                            `json:"min_text_runes"`
	Fixtures     map[string]pdfReferenceFixture `json:"fixtures"`
}

func loadPDFReference(t *testing.T) pdfReferenceFile {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join(pdfFixturesDir, "t060_reference.json"))
	if err != nil {
		t.Fatalf("reading the committed PDF reference: %v", err)
	}
	var reference pdfReferenceFile
	if err := json.Unmarshal(raw, &reference); err != nil {
		t.Fatalf("decoding the committed PDF reference: %v", err)
	}
	if len(reference.Fixtures) == 0 {
		t.Fatal("the committed PDF reference has no fixtures")
	}
	return reference
}

func readPDFFixture(t *testing.T, name string) []byte {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join(pdfFixturesDir, name))
	if err != nil {
		t.Fatalf("reading the committed PDF fixture %q: %v", name, err)
	}
	return raw
}

func parsePDFFixture(t *testing.T, name string) (Parsed, error) {
	t.Helper()
	parser, ok := ParserFor(MediaTypePDF)
	if !ok {
		t.Fatal("no parser is registered for application/pdf")
	}
	return parser.Parse(readPDFFixture(t, name))
}

func TestPDFParserIsRegisteredAtItsOwnRevision(t *testing.T) {
	parser, ok := ParserFor(MediaTypePDF)
	if !ok {
		t.Fatal("application/pdf has no registered parser")
	}
	if parser.Version() != ParserVersionPDF {
		t.Fatalf("parser version = %q, want %q", parser.Version(), ParserVersionPDF)
	}
	if !strings.Contains(strings.Join(SupportedMediaTypes(), ","), MediaTypePDF) {
		t.Fatalf("supported media types = %v", SupportedMediaTypes())
	}
}

func TestPDFParserExtractsEveryPageWithItsOwnSpan(t *testing.T) {
	reference := loadPDFReference(t)
	expected := reference.Fixtures["t060_plain.pdf"]
	parsed, err := parsePDFFixture(t, "t060_plain.pdf")
	if err != nil {
		t.Fatalf("parsing the plain PDF: %v", err)
	}
	if parsed.ParserVersion != ParserVersionPDF {
		t.Fatalf("parser version = %q", parsed.ParserVersion)
	}
	if parsed.TextRender != TextRenderPDFPages {
		t.Fatalf("text render = %q, want %q", parsed.TextRender, TextRenderPDFPages)
	}
	if parsed.Normalization != NormalizationNone {
		t.Fatalf("normalization = %q", parsed.Normalization)
	}
	if parsed.PDF == nil {
		t.Fatal("the PDF parser did not report its page map")
	}
	if parsed.PDF.PageCount != expected.PageCount {
		t.Fatalf("page count = %d, want %d", parsed.PDF.PageCount, expected.PageCount)
	}
	if len(parsed.PDF.Pages) != expected.PageCount {
		t.Fatalf("page spans = %d, want %d", len(parsed.PDF.Pages), expected.PageCount)
	}
	runes := []rune(parsed.Text)
	for index, page := range parsed.PDF.Pages {
		if page.Number != index+1 {
			t.Fatalf("page %d number = %d, want %d", index, page.Number, index+1)
		}
		if page.StartChar < 0 || page.EndChar > len(runes) || page.StartChar > page.EndChar {
			t.Fatalf("page %d span [%d,%d) is outside the text", index, page.StartChar, page.EndChar)
		}
		got := string(runes[page.StartChar:page.EndChar])
		if got != expected.Pages[index] {
			t.Fatalf("page %d text = %q, want %q", index, got, expected.Pages[index])
		}
	}
	if len(parsed.PDF.EmptyPages) != 0 {
		t.Fatalf("empty pages = %v, want none", parsed.PDF.EmptyPages)
	}
	// The page spans are non-overlapping and in page order.
	for index := 1; index < len(parsed.PDF.Pages); index++ {
		if parsed.PDF.Pages[index].StartChar < parsed.PDF.Pages[index-1].EndChar {
			t.Fatalf("page spans overlap at page %d", index)
		}
	}
}

func TestPDFParserRecordsAPartialTextLayerHonestly(t *testing.T) {
	reference := loadPDFReference(t)
	expected := reference.Fixtures["t060_partial.pdf"]
	parsed, err := parsePDFFixture(t, "t060_partial.pdf")
	if err != nil {
		t.Fatalf("parsing the partial PDF: %v", err)
	}
	if parsed.PDF == nil {
		t.Fatal("no page map")
	}
	if got := parsed.PDF.EmptyPages; len(got) != 1 || got[0] != 2 {
		t.Fatalf("empty pages = %v, want [2]", got)
	}
	if len(expected.PagesWithoutText) != 1 || expected.PagesWithoutText[0] != 2 {
		t.Fatalf("reference empty pages = %v", expected.PagesWithoutText)
	}
	runes := []rune(parsed.Text)
	for index, page := range parsed.PDF.Pages {
		got := string(runes[page.StartChar:page.EndChar])
		if got != expected.Pages[index] {
			t.Fatalf("page %d text = %q, want %q", index, got, expected.Pages[index])
		}
	}
	if parsed.PDF.Pages[1].StartChar != parsed.PDF.Pages[1].EndChar {
		t.Fatalf("the image-only page has a non-empty span: %+v", parsed.PDF.Pages[1])
	}
}

func TestPDFParserRefusesAScannedPDFAsNoTextLayer(t *testing.T) {
	reference := loadPDFReference(t)
	if reference.MinTextRunes != MinPDFTextRunes {
		t.Fatalf("reference threshold = %d, code = %d", reference.MinTextRunes, MinPDFTextRunes)
	}
	for _, name := range []string{"t060_scanned.pdf", "t060_short.pdf"} {
		t.Run(name, func(t *testing.T) {
			parsed, err := parsePDFFixture(t, name)
			if err == nil {
				t.Fatalf("%s parsed successfully (%d text runes); a scanned PDF must not look ingested", name, len([]rune(parsed.Text)))
			}
			parseErr, ok := asParseError(err)
			if !ok {
				t.Fatalf("%s error = %v, want *ParseError", name, err)
			}
			if parseErr.FailureCode != FailurePDFNoTextLayer {
				t.Fatalf("%s failure = %q, want %q", name, parseErr.FailureCode, FailurePDFNoTextLayer)
			}
			if parseErr.Location.Part != "text_layer" {
				t.Fatalf("%s detail = %+v", name, parseErr.Location)
			}
			if parseErr.Location.Limit != "min_text_runes=32" {
				t.Fatalf("%s limit = %q", name, parseErr.Location.Limit)
			}
			if parsed.Text != "" || parsed.PDF != nil {
				t.Fatalf("%s returned partial content alongside the refusal: %+v", name, parsed)
			}
		})
	}
	// The short fixture has a real (small) text layer: the refusal records how
	// much non-whitespace text was found rather than pretending there was none.
	_, err := parsePDFFixture(t, "t060_short.pdf")
	parseErr, _ := asParseError(err)
	shortText := "Scan from the office copier"
	expectedRunes := 0
	for _, char := range shortText {
		if !unicode.IsSpace(char) {
			expectedRunes++
		}
	}
	if parseErr != nil && parseErr.Location.Value != expectedRunes {
		t.Fatalf("short fixture value = %d, want %d", parseErr.Location.Value, expectedRunes)
	}
	if expectedRunes >= MinPDFTextRunes {
		t.Fatalf("the short fixture (%d runes) is not below the %d-rune threshold", expectedRunes, MinPDFTextRunes)
	}
}

func TestPDFParserRefusesInvalidAndEncryptedPDFs(t *testing.T) {
	parser, _ := ParserFor(MediaTypePDF)
	cases := []struct {
		name   string
		bytes  []byte
		code   string
		detail string
	}{
		{"not a pdf", []byte("this is not a pdf at all"), FailurePDFInvalid, "header"},
		{"truncated", []byte("%PDF-1.4\n1 0 obj\n"), FailurePDFInvalid, ""},
		{"encrypted trailer", encryptTrailerPDF(t), FailurePDFEncrypted, "trailer"},
	}
	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			parsed, err := parser.Parse(testCase.bytes)
			if err == nil {
				t.Fatalf("parsed invalid input: %+v", parsed)
			}
			parseErr, ok := asParseError(err)
			if !ok {
				t.Fatalf("error = %v, want *ParseError", err)
			}
			if parseErr.FailureCode != testCase.code {
				t.Fatalf("failure = %q, want %q (error %v)", parseErr.FailureCode, testCase.code, err)
			}
			if testCase.detail != "" && parseErr.Location.Part != testCase.detail {
				t.Fatalf("detail = %+v, want part %q", parseErr.Location, testCase.detail)
			}
		})
	}
}

// encryptTrailerPDF declares /Encrypt in the trailer of an otherwise valid PDF.
// It is the exact structure the parser refuses before reading any page: a real
// encrypted document carries the same trailer key.
func encryptTrailerPDF(t *testing.T) []byte {
	t.Helper()
	plain := readPDFFixture(t, "t060_plain.pdf")
	if !bytes.Contains(plain, []byte("/Root 1 0 R")) {
		t.Fatal("the plain fixture has no trailer to adapt")
	}
	return bytes.Replace(
		plain,
		[]byte("/Root 1 0 R"),
		[]byte("/Root 1 0 R /Encrypt 99 0 R"),
		1,
	)
}

func TestPDFChunkerKeepsEachChunkOnOnePageAndSlicesThePageText(t *testing.T) {
	reference := loadPDFReference(t)
	expected := reference.Fixtures["t060_plain.pdf"]
	parsed, err := parsePDFFixture(t, "t060_plain.pdf")
	if err != nil {
		t.Fatalf("parsing: %v", err)
	}
	chunks := ChunkParsed(parsed)
	if len(chunks) == 0 {
		t.Fatal("no chunks")
	}
	if version := ChunkerVersionFor(parsed); version != ChunkerVersionPDF {
		t.Fatalf("chunker version = %q, want %q", version, ChunkerVersionPDF)
	}
	runes := []rune(parsed.Text)
	for index, chunk := range chunks {
		if chunk.No != index {
			t.Fatalf("chunk %d No = %d", index, chunk.No)
		}
		if chunk.PDF == nil {
			t.Fatalf("chunk %d has no page attribution", index)
		}
		if chunk.PDF.PageStart != chunk.PDF.PageEnd {
			t.Fatalf("chunk %d spans pages %d..%d", index, chunk.PDF.PageStart, chunk.PDF.PageEnd)
		}
		page := parsed.PDF.Pages[chunk.PDF.PageStart-1]
		pageRunes := runes[page.StartChar:page.EndChar]
		if chunk.PDF.PageCharStart < 0 || chunk.PDF.PageCharEnd > len(pageRunes) ||
			chunk.PDF.PageCharStart >= chunk.PDF.PageCharEnd {
			t.Fatalf("chunk %d page offsets [%d,%d) are outside page %d",
				index, chunk.PDF.PageCharStart, chunk.PDF.PageCharEnd, chunk.PDF.PageStart)
		}
		if got := string(pageRunes[chunk.PDF.PageCharStart:chunk.PDF.PageCharEnd]); got != chunk.Text {
			t.Fatalf("chunk %d page slice = %q, body = %q", index, got, chunk.Text)
		}
		if got := string(runes[chunk.Span.StartChar:chunk.Span.EndChar]); got != chunk.Text {
			t.Fatalf("chunk %d source slice = %q, body = %q", index, got, chunk.Text)
		}
		if len([]rune(chunk.Text)) > MaxChunkChars {
			t.Fatalf("chunk %d is too large: %d", index, len([]rune(chunk.Text)))
		}
	}
	// Page text is cited by page number and the citation is the page's own text.
	for _, page := range parsed.PDF.Pages {
		pageText := expected.Pages[page.Number-1]
		covered := false
		for _, chunk := range chunks {
			if chunk.PDF.PageStart == page.Number {
				covered = true
				if !strings.Contains(pageText, strings.TrimSpace(chunk.Text)) {
					t.Fatalf("chunk on page %d is not a substring of the page text", page.Number)
				}
			}
		}
		if !covered {
			t.Fatalf("page %d has text but no chunk", page.Number)
		}
	}
}

func TestPDFChunkerSplitsAnOversizedPageWithOverlap(t *testing.T) {
	long := strings.Repeat("word ", 900) // 4500 runes: four overlapping windows
	parsed := Parsed{
		Text:          long,
		ParserVersion: ParserVersionPDF,
		TextRender:    TextRenderPDFPages,
		PDF: &PDF{
			PageCount: 1,
			Pages:     []PDFPage{{Number: 1, StartChar: 0, EndChar: len([]rune(long))}},
		},
	}
	chunks := ChunkParsed(parsed)
	if len(chunks) < 3 {
		t.Fatalf("chunks = %d, want at least 3", len(chunks))
	}
	runes := []rune(long)
	for _, chunk := range chunks {
		if chunk.PDF == nil || chunk.PDF.PageStart != 1 {
			t.Fatalf("chunk %d page attribution = %+v", chunk.No, chunk.PDF)
		}
		if got := string(runes[chunk.Span.StartChar:chunk.Span.EndChar]); got != chunk.Text {
			t.Fatalf("chunk %d does not slice back", chunk.No)
		}
	}
	// The overlap constant is part of the chunker revision: consecutive windows
	// of one oversized paragraph share text.
	if chunks[1].Span.StartChar >= chunks[0].Span.EndChar {
		t.Fatalf("consecutive chunks do not overlap: %d..%d then %d..%d",
			chunks[0].Span.StartChar, chunks[0].Span.EndChar,
			chunks[1].Span.StartChar, chunks[1].Span.EndChar)
	}
}
