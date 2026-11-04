package knowledge

// F10/T060: the plain-text PDF parser.
//
// Scope: PDFs whose pages carry a real text layer drawn with simple (single-byte
// encoded) fonts. The parser extracts the text of every page, keeps the page
// number as the source identity and never performs OCR: an image-only page
// contributes no characters, and a document with no text layer (or less than the
// documented character threshold) is a diagnosable failure, not an empty success.
//
// The extraction is deliberately conservative and deterministic:
//   - page text is reconstructed from the content stream's text operators
//     (Tj/TJ/'/") and the text matrix; a change of baseline starts a new line and
//     a vertical gap larger than 1.7x the font size records one blank line (a
//     paragraph break);
//   - a horizontal gap larger than a quarter em between two show-text operations
//     records one space, and a large negative TJ adjustment records one space;
//   - text is decoded with the font's own encoding (WinAnsi/MacRoman/pdfDoc or a
//     ToUnicode cmap), so the recorded text is the page's text, not raw bytes.
//
// Form XObjects are followed up to a small depth because they are a common
// container for page text. Inline images and Type0/CID *width* tables are not
// interpreted; a Type0 font still decodes through its ToUnicode cmap, which is
// the common case, while its glyph advances are approximated. Any structural
// defect is reported as pdf_invalid rather than guessed around.

import (
	"bytes"
	"fmt"
	"math"
	"strings"
	"unicode"

	"rsc.io/pdf"
)

// MediaTypePDF is the knowledge source media type admitted by T060.
const MediaTypePDF = "application/pdf"

// ParserVersionPDF is the stable parser revision recorded on the document and in
// every citation. The page-text rendering it produces is named by
// TextRenderPDFPages; a change to either is a new parser revision.
const ParserVersionPDF = "orchara-pdf-parser/1"

// ChunkerVersionPDF is the page-aware paragraph chunker revision. It differs from
// the plain paragraph chunker because chunk boundaries never cross a page.
const ChunkerVersionPDF = "orchara-pdf-page-paragraph-chunker/1"

// TextRenderPDFPages names what a PDF chunk's character offsets index: the
// deterministic page concatenation produced by ParserVersionPDF from the sealed
// bytes. It is not the PDF's own bytes (a PDF is not UTF-8 text).
const TextRenderPDFPages = "pdf_pages/1"

// MinPDFTextRunes is the documented character threshold for "this PDF has a text
// layer". It counts non-whitespace runes across all pages. A scanned document
// whose only text is a short stamp (for example "Scan from the office copier")
// must not be reported as ingested content.
const MinPDFTextRunes = 32

// maxPDFFormDepth bounds Form XObject recursion: a cyclic form reference must not
// loop forever.
const maxPDFFormDepth = 4

// Stable document failure codes for the PDF family. They are returned as a
// *ParseError so the document_ingest handler fails the document with the same
// code and the diagnostics read can re-derive the (page) location.
const (
	// FailurePDFInvalid means the bytes are not a readable PDF structure.
	FailurePDFInvalid = "pdf_invalid"
	// FailurePDFEncrypted means the PDF declares encryption; the platform does
	// not decrypt documents, so it refuses instead of guessing.
	FailurePDFEncrypted = "pdf_encrypted"
	// FailurePDFNoTextLayer means the PDF has no text layer, or its text layer is
	// below MinPDFTextRunes. OCR is never attempted, so this is never ready.
	FailurePDFNoTextLayer = "pdf_no_text_layer"
)

// PDFPage is one page's text range in Parsed.Text. StartChar/EndChar are rune
// offsets of the page's decoded text; an image-only page has StartChar == EndChar
// and is recorded in PDF.EmptyPages instead of being hidden.
type PDFPage struct {
	Number    int
	StartChar int
	EndChar   int
}

// PDF is the PDF parser output: the page map and the honestly recorded empty
// pages. Chunking is page-bounded so every chunk is attributed to exactly one
// page.
type PDF struct {
	PageCount  int
	Pages      []PDFPage
	EmptyPages []int
}

type pdfParser struct{}

func (pdfParser) MediaType() string { return MediaTypePDF }
func (pdfParser) Version() string   { return ParserVersionPDF }

func (pdfParser) Parse(content []byte) (Parsed, error) {
	if len(content) == 0 {
		return Parsed{}, ErrEmptyContent
	}
	if !bytes.HasPrefix(content, []byte("%PDF-")) {
		return Parsed{}, &ParseError{
			FailureCode: FailurePDFInvalid,
			Location:    ParseDetail{Part: "header"},
		}
	}
	pageTexts, failure := extractPDFPageTexts(content)
	if failure != nil {
		return Parsed{}, failure
	}
	textRunes := 0
	for _, text := range pageTexts {
		for _, char := range text {
			if !unicode.IsSpace(char) {
				textRunes++
			}
		}
	}
	if textRunes < MinPDFTextRunes {
		return Parsed{}, &ParseError{
			FailureCode: FailurePDFNoTextLayer,
			Location: ParseDetail{
				Part:  "text_layer",
				Limit: fmt.Sprintf("min_text_runes=%d", MinPDFTextRunes),
				Value: textRunes,
			},
		}
	}

	var (
		builder   strings.Builder
		pages     = make([]PDFPage, 0, len(pageTexts))
		empty     = make([]int, 0, 1)
		character int
	)
	for index, text := range pageTexts {
		if index > 0 {
			// Pages are separated by one blank line so a page boundary is visible
			// in the parsed text. The separator is inside no page span.
			builder.WriteString("\n\n")
			character += 2
		}
		start := character
		builder.WriteString(text)
		character += len([]rune(text))
		pages = append(pages, PDFPage{Number: index + 1, StartChar: start, EndChar: character})
		if text == "" {
			empty = append(empty, index+1)
		}
	}
	return Parsed{
		Text:          builder.String(),
		ParserVersion: ParserVersionPDF,
		Normalization: NormalizationNone,
		TextRender:    TextRenderPDFPages,
		PDF: &PDF{
			PageCount:  len(pageTexts),
			Pages:      pages,
			EmptyPages: empty,
		},
	}, nil
}

// extractPDFPageTexts opens the document and returns one text per page. It
// converts any library panic or structural refusal into a *ParseError: a corrupt
// PDF must end as a diagnosable failed document, never as a crash and never as an
// empty success.
func extractPDFPageTexts(content []byte) (texts []string, failure *ParseError) {
	defer func() {
		if recovered := recover(); recovered != nil {
			texts = nil
			failure = &ParseError{
				FailureCode: FailurePDFInvalid,
				Location:    ParseDetail{Part: "structure"},
			}
		}
	}()
	reader, err := pdf.NewReader(bytes.NewReader(content), int64(len(content)))
	if err != nil {
		// An encrypted document fails here for a non-empty password. The trailer
		// key is the honest classification; anything else is a structural defect.
		if bytes.Contains(content, []byte("/Encrypt")) {
			return nil, &ParseError{
				FailureCode: FailurePDFEncrypted,
				Location:    ParseDetail{Part: "trailer"},
			}
		}
		return nil, &ParseError{
			FailureCode: FailurePDFInvalid,
			Location:    ParseDetail{Part: "xref"},
		}
	}
	if !reader.Trailer().Key("Encrypt").IsNull() {
		// An empty-password encrypted document opens, but it is still encrypted:
		// refusing it is the honest boundary (no decryption in this ticket).
		return nil, &ParseError{
			FailureCode: FailurePDFEncrypted,
			Location:    ParseDetail{Part: "trailer"},
		}
	}
	pageCount := reader.NumPage()
	if pageCount <= 0 {
		return nil, &ParseError{
			FailureCode: FailurePDFInvalid,
			Location:    ParseDetail{Part: "pages"},
		}
	}
	texts = make([]string, 0, pageCount)
	for number := 1; number <= pageCount; number++ {
		page := reader.Page(number)
		if page.V.IsNull() {
			return nil, &ParseError{
				FailureCode: FailurePDFInvalid,
				Location:    ParseDetail{Part: "page", Row: number},
			}
		}
		texts = append(texts, extractPDFPageText(page))
	}
	return texts, nil
}

// extractPDFPageText reconstructs one page's text.
func extractPDFPageText(page pdf.Page) string {
	resources := page.Resources()
	state := &pdfTextState{
		lineThresholdRatio: 0.5,
		defaultGlyphWidth:  500,
		tm:                 pdfIdentity,
		tlm:                pdfIdentity,
		hScale:             1,
	}
	contents := page.V.Key("Contents")
	switch contents.Kind() {
	case pdf.Array:
		for index := 0; index < contents.Len(); index++ {
			state.interpret(contents.Index(index), resources)
		}
	case pdf.Stream:
		state.interpret(contents, resources)
	}
	state.finish()
	return strings.Join(state.lines, "\n")
}

// pdfMatrix is a PDF text matrix in row-vector form: [a b 0; c d 0; e f 1].
type pdfMatrix [6]float64

var pdfIdentity = pdfMatrix{1, 0, 0, 1, 0, 0}

func (m pdfMatrix) multiply(n pdfMatrix) pdfMatrix {
	return pdfMatrix{
		m[0]*n[0] + m[1]*n[2], m[0]*n[1] + m[1]*n[3],
		m[2]*n[0] + m[3]*n[2], m[2]*n[1] + m[3]*n[3],
		m[4]*n[0] + m[5]*n[2] + n[4], m[4]*n[1] + m[5]*n[3] + n[5],
	}
}

func pdfTranslate(tx, ty float64) pdfMatrix {
	return pdfMatrix{1, 0, 0, 1, tx, ty}
}

// pdfTextState accumulates the page text while the content stream is interpreted.
type pdfTextState struct {
	lines []string
	line  strings.Builder
	// Current line identity.
	inLine   bool
	lineY    float64
	lineSize float64
	lineEndX float64
	// Previous flushed line, used to record a paragraph break.
	haveLine   bool
	lastLineY  float64
	lastLineSz float64
	// Text state.
	font        pdf.Font
	fontSize    float64
	leading     float64
	charSpacing float64
	wordSpacing float64
	hScale      float64
	tm          pdfMatrix
	tlm         pdfMatrix
	depth       int
	// Heuristics.
	lineThresholdRatio float64
	defaultGlyphWidth  float64
}

func (s *pdfTextState) interpret(stream pdf.Value, resources pdf.Value) {
	if stream.Kind() != pdf.Stream {
		return
	}
	pdf.Interpret(stream, func(stack *pdf.Stack, operator string) {
		count := stack.Len()
		args := make([]pdf.Value, count)
		for index := count - 1; index >= 0; index-- {
			args[index] = stack.Pop()
		}
		switch operator {
		case "BT":
			s.tm = pdfIdentity
			s.tlm = pdfIdentity
		case "Tf":
			if len(args) == 2 {
				s.fontSize = args[1].Float64()
				s.font = pdf.Font{V: resources.Key("Font").Key(args[0].Name())}
			}
		case "Td":
			if len(args) == 2 {
				s.tlm = pdfTranslate(args[0].Float64(), args[1].Float64()).multiply(s.tlm)
				s.tm = s.tlm
			}
		case "TD":
			if len(args) == 2 {
				s.leading = -args[1].Float64()
				s.tlm = pdfTranslate(args[0].Float64(), args[1].Float64()).multiply(s.tlm)
				s.tm = s.tlm
			}
		case "Tm":
			if len(args) == 6 {
				s.tm = pdfMatrix{
					args[0].Float64(), args[1].Float64(),
					args[2].Float64(), args[3].Float64(),
					args[4].Float64(), args[5].Float64(),
				}
				s.tlm = s.tm
			}
		case "T*":
			s.nextLine()
		case "TL":
			if len(args) == 1 {
				s.leading = args[0].Float64()
			}
		case "Tc":
			if len(args) == 1 {
				s.charSpacing = args[0].Float64()
			}
		case "Tw":
			if len(args) == 1 {
				s.wordSpacing = args[0].Float64()
			}
		case "Tz":
			if len(args) == 1 {
				s.hScale = args[0].Float64() / 100
			}
		case "Tj":
			if len(args) == 1 {
				s.showString(args[0].RawString())
			}
		case "TJ":
			if len(args) == 1 {
				values := args[0]
				for index := 0; index < values.Len(); index++ {
					value := values.Index(index)
					if value.Kind() == pdf.String {
						s.showString(value.RawString())
						continue
					}
					adjustment := value.Float64()
					if adjustment <= -150 && s.inLine && !strings.HasSuffix(s.line.String(), " ") {
						// A large negative kerning step is a word gap in PDFs that do
						// not put a literal space in the string.
						s.line.WriteByte(' ')
					}
					s.advance(-adjustment / 1000 * s.fontSize * s.hScale)
				}
			}
		case "'":
			if len(args) == 1 {
				s.nextLine()
				s.showString(args[0].RawString())
			}
		case "\"":
			if len(args) == 3 {
				s.wordSpacing = args[0].Float64()
				s.charSpacing = args[1].Float64()
				s.nextLine()
				s.showString(args[2].RawString())
			}
		case "Do":
			s.doXObject(args, resources)
		}
	})
}

// doXObject follows a Form XObject's text operators. Image XObjects are ignored:
// an image contributes no text layer.
func (s *pdfTextState) doXObject(args []pdf.Value, resources pdf.Value) {
	if len(args) != 1 || s.depth >= maxPDFFormDepth {
		return
	}
	xobject := resources.Key("XObject").Key(args[0].Name())
	if xobject.Kind() != pdf.Stream || xobject.Key("Subtype").Name() != "Form" {
		return
	}
	formResources := xobject.Key("Resources")
	if formResources.Kind() != pdf.Dict {
		formResources = resources
	}
	s.depth++
	s.interpret(xobject, formResources)
	s.depth--
}

// showString appends one shown string at the current text position and advances
// the text matrix by the string's advance width.
func (s *pdfTextState) showString(raw string) {
	if raw == "" {
		return
	}
	decoded := s.font.Encoder().Decode(raw)
	x, y := s.tm[4], s.tm[5]
	threshold := math.Max(s.fontSize*s.lineThresholdRatio, 1)
	if s.inLine && math.Abs(y-s.lineY) > threshold {
		s.endLine()
	}
	if !s.inLine {
		s.beginLine(y, s.fontSize)
	}
	if x > s.lineEndX+math.Max(0.25*s.fontSize, 0.5) &&
		s.line.Len() > 0 && !strings.HasSuffix(s.line.String(), " ") && !startsWithSpace(decoded) {
		s.line.WriteByte(' ')
	}
	s.line.WriteString(decoded)

	advance := 0.0
	for index := 0; index < len(raw); index++ {
		code := int(raw[index])
		width := s.font.Width(code)
		if width <= 0 {
			width = s.defaultGlyphWidth
		}
		advance += width / 1000 * s.fontSize
		if raw[index] == ' ' {
			advance += s.wordSpacing
		}
		advance += s.charSpacing
	}
	advance *= s.hScale
	s.advance(advance)
}

// advance moves the text matrix along its x axis by tx text-space units.
func (s *pdfTextState) advance(tx float64) {
	s.tm = pdfTranslate(tx, 0).multiply(s.tm)
	if s.inLine {
		s.lineEndX = s.tm[4]
	}
}

// nextLine applies the leading (T*).
func (s *pdfTextState) nextLine() {
	s.tlm = pdfTranslate(0, -s.leading).multiply(s.tlm)
	s.tm = s.tlm
}

func (s *pdfTextState) beginLine(y, size float64) {
	if s.haveLine && s.lastLineY-y > 1.7*math.Max(s.lastLineSz, size) {
		// A vertical gap visibly larger than the leading is a paragraph break.
		s.lines = append(s.lines, "")
	}
	s.inLine = true
	s.lineY = y
	s.lineSize = size
	s.lineEndX = s.tm[4]
	s.line.Reset()
}

func (s *pdfTextState) endLine() {
	if !s.inLine {
		return
	}
	text := strings.Join(strings.Fields(s.line.String()), " ")
	if text != "" {
		s.lines = append(s.lines, text)
	}
	s.haveLine = true
	s.lastLineY = s.lineY
	s.lastLineSz = s.lineSize
	s.inLine = false
	s.line.Reset()
}

func (s *pdfTextState) finish() {
	s.endLine()
	// Drop a leading/trailing blank line and collapse repeated paragraph breaks.
	cleaned := make([]string, 0, len(s.lines))
	for _, line := range s.lines {
		if line == "" {
			if len(cleaned) == 0 || cleaned[len(cleaned)-1] == "" {
				continue
			}
		}
		cleaned = append(cleaned, line)
	}
	for len(cleaned) > 0 && cleaned[len(cleaned)-1] == "" {
		cleaned = cleaned[:len(cleaned)-1]
	}
	s.lines = cleaned
}

func startsWithSpace(text string) bool {
	trimmed := strings.TrimLeftFunc(text, unicode.IsSpace)
	return trimmed != text
}
