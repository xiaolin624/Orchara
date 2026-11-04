package knowledge

// F10/T060: the page-aware PDF chunker.
//
// The plain paragraph chunker is deliberately reused for the *inside* of a page:
// paragraphs are maximal runs of non-empty lines, consecutive paragraphs are
// packed up to MaxChunkChars, an oversized paragraph is hard-split with the
// documented overlap, and every span is trimmed and recomputed. The one
// difference is that the rune slice is one page at a time, so a chunk never
// crosses a page boundary: the page number in the citation is exact rather than a
// guess, and a paragraph that spans a page break is cited twice (once per page).
//
// The page-local offsets are recorded alongside the global rune offsets: a reader
// can slice the page text by (page, page_char_start, page_char_end) or the parsed
// document by (start_char, end_char), and both give the chunk body.

// ChunkPDF is one PDF chunk's page attribution. PageStart == PageEnd for this
// page-bounded chunker; the range shape is kept so a later chunker may allow a
// chunk to cross pages without changing the citation.
type ChunkPDF struct {
	// PageStart/PageEnd are the 1-based inclusive page numbers.
	PageStart int
	PageEnd   int
	// PageCharStart/PageCharEnd are rune offsets inside the page's text
	// (PageEnd's text for a cross-page chunk; equal to PageStart here).
	PageCharStart int
	PageCharEnd   int
}

// chunkPDFPages turns parsed page text into page-bounded chunks.
func chunkPDFPages(parsed Parsed) []Chunk {
	if parsed.PDF == nil {
		return nil
	}
	allRunes := []rune(parsed.Text)
	lineStarts := lineOffsets(allRunes)
	var chunks []Chunk
	for _, page := range parsed.PDF.Pages {
		if page.StartChar < 0 || page.EndChar > len(allRunes) ||
			page.StartChar >= page.EndChar {
			// An empty page contributes no chunk; it is recorded honestly in
			// PDF.EmptyPages instead of being treated as text.
			continue
		}
		pageRunes := allRunes[page.StartChar:page.EndChar]
		localChunks := chunkSegments(pageRunes, lineOffsets(pageRunes), paragraphSegments(pageRunes))
		for _, local := range localChunks {
			start := page.StartChar + local.Span.StartChar
			end := page.StartChar + local.Span.EndChar
			chunks = append(chunks, Chunk{
				No:   len(chunks),
				Text: local.Text,
				Span: Span{
					StartChar: start,
					EndChar:   end,
					StartLine: lineOf(lineStarts, start),
					EndLine:   lineOf(lineStarts, end-1),
				},
				PDF: &ChunkPDF{
					PageStart:     page.Number,
					PageEnd:       page.Number,
					PageCharStart: local.Span.StartChar,
					PageCharEnd:   local.Span.EndChar,
				},
			})
		}
	}
	return chunks
}
