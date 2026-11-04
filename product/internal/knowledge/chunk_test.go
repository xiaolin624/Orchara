package knowledge

import (
	"errors"
	"strings"
	"testing"
)

func TestTextParserRejectsInvalidEncodingAndEmptyContent(t *testing.T) {
	parser, ok := ParserFor(MediaTypeText)
	if !ok {
		t.Fatal("no parser for text/plain")
	}
	if _, err := parser.Parse([]byte{0xff, 0xfe, 0x00}); !errors.Is(err, ErrInvalidEncoding) {
		t.Fatalf("invalid UTF-8 error = %v, want ErrInvalidEncoding", err)
	}
	if _, err := parser.Parse([]byte(" \n\t\n ")); !errors.Is(err, ErrEmptyContent) {
		t.Fatalf("blank content error = %v, want ErrEmptyContent", err)
	}
	parsed, err := parser.Parse([]byte("\n\nhello\n\n"))
	if err != nil {
		t.Fatalf("valid content: %v", err)
	}
	if parsed.Text != "\n\nhello\n\n" {
		t.Fatalf("text = %q, want the bytes unchanged", parsed.Text)
	}
	if parsed.ParserVersion != ParserVersionText {
		t.Fatalf("version = %q", parsed.ParserVersion)
	}
	if parsed.Normalization != NormalizationNone {
		t.Fatalf("normalization = %q", parsed.Normalization)
	}
}

func TestTextParserStripsOneBOMAndRecordsIt(t *testing.T) {
	parser, _ := ParserFor(MediaTypeText)
	content := append([]byte{0xEF, 0xBB, 0xBF}, []byte("hello\n")...)
	parsed, err := parser.Parse(content)
	if err != nil {
		t.Fatalf("BOM content: %v", err)
	}
	if parsed.Text != "hello\n" {
		t.Fatalf("text = %q", parsed.Text)
	}
	if parsed.Normalization != NormalizationBOMStripped {
		t.Fatalf("normalization = %q", parsed.Normalization)
	}
}

func TestMarkdownParserIsRegisteredWithItsOwnRevision(t *testing.T) {
	parser, ok := ParserFor(MediaTypeMarkdown)
	if !ok {
		t.Fatal("no parser for text/markdown")
	}
	if parser.Version() != ParserVersionMarkdown {
		t.Fatalf("version = %q", parser.Version())
	}
	if _, ok := ParserFor("text/csv"); !ok {
		t.Fatal("CSV must be registered by T059")
	}
	if _, ok := ParserFor(MediaTypeXLSX); !ok {
		t.Fatal("XLSX must be registered by T059")
	}
	if _, ok := ParserFor("application/pdf"); ok {
		t.Fatal("PDF must stay unregistered (T060)")
	}
}

func TestChunkTextIsDeterministicAndSpansSliceBackExactly(t *testing.T) {
	long := strings.Repeat("x", 700)
	document := strings.Join([]string{
		"# Heading",
		"",
		"First paragraph line one.",
		"First paragraph line two.",
		"",
		"Second paragraph with 中文 mixed in.",
		"",
		"Final paragraph.",
		"",
		long,
		"",
		long,
	}, "\n")
	first := ChunkText(document)
	second := ChunkText(document)
	if len(first) != len(second) || len(first) == 0 {
		t.Fatalf("chunks = %d, want a stable non-empty count", len(first))
	}
	runes := []rune(document)
	for index := range first {
		if first[index] != second[index] {
			t.Fatalf("chunk %d differs between runs", index)
		}
		if got := string(runes[first[index].Span.StartChar:first[index].Span.EndChar]); got != first[index].Text {
			t.Fatalf("chunk %d text = %q, span slices %q", index, first[index].Text, got)
		}
		if first[index].No != index {
			t.Fatalf("chunk No = %d, want %d", first[index].No, index)
		}
		if first[index].Span.StartLine < 1 || first[index].Span.EndLine < first[index].Span.StartLine {
			t.Fatalf("chunk %d line range = %d..%d", index, first[index].Span.StartLine, first[index].Span.EndLine)
		}
	}
	if len(first) < 2 {
		t.Fatalf("chunks = %d, want at least 2 so meaningful boundaries are asserted", len(first))
	}
	if first[0].Span.StartLine != 1 {
		t.Fatalf("first chunk starts on line %d, want 1", first[0].Span.StartLine)
	}
	if first[len(first)-1].Span.EndLine != 12 {
		t.Fatalf("last chunk end line = %d, want 12", first[len(first)-1].Span.EndLine)
	}
}

func TestChunkTextSplitsOversizedParagraphWithOverlap(t *testing.T) {
	words := make([]string, 0, 900)
	for index := 0; index < 900; index++ {
		words = append(words, "word")
	}
	document := strings.Join(words, " ")
	chunks := ChunkText(document)
	if len(chunks) < 2 {
		t.Fatalf("chunks = %d, want at least 2", len(chunks))
	}
	for index, chunk := range chunks {
		if len([]rune(chunk.Text)) > MaxChunkChars {
			t.Fatalf("chunk %d has %d runes, limit %d", index, len([]rune(chunk.Text)), MaxChunkChars)
		}
	}
	for index := 1; index < len(chunks); index++ {
		if chunks[index].Span.StartChar >= chunks[index-1].Span.EndChar {
			t.Fatalf("chunk %d does not overlap its predecessor", index)
		}
	}
	if chunks[0].Span.StartChar != 0 {
		t.Fatalf("first chunk starts at %d", chunks[0].Span.StartChar)
	}
	if chunks[len(chunks)-1].Span.EndChar != len([]rune(document)) {
		t.Fatalf("last chunk ends at %d, want %d", chunks[len(chunks)-1].Span.EndChar, len([]rune(document)))
	}
}

func TestChunkTextPacksShortParagraphsAndSkipsBlankOnlyText(t *testing.T) {
	chunks := ChunkText("one\n\ntwo\n\nthree\n")
	if len(chunks) != 1 {
		t.Fatalf("chunks = %d, want 1", len(chunks))
	}
	if chunks[0].Text != "one\n\ntwo\n\nthree" {
		t.Fatalf("text = %q", chunks[0].Text)
	}
	if got := ChunkText(" \n\n\t\n"); len(got) != 0 {
		t.Fatalf("blank text produced %d chunks", len(got))
	}
}

func TestExcerptCollapsesWhitespaceAndBoundsRunes(t *testing.T) {
	if got := Excerpt("a\n\nb\t c", 10); got != "a b c" {
		t.Fatalf("excerpt = %q", got)
	}
	got := Excerpt("你好世界", 2)
	if got != "你好…" {
		t.Fatalf("bounded excerpt = %q", got)
	}
}
