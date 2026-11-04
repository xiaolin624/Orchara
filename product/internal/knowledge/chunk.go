package knowledge

import (
	"strings"
	"unicode"
)

// Chunking constants. They are part of the chunker revision: changing any of
// them is a new ChunkerVersion, because the same bytes must not silently produce
// different spans under the recorded revision.
const (
	// MaxChunkChars bounds one chunk body in runes.
	MaxChunkChars = 1200
	// OverlapChars is the tail of a hard-split oversized paragraph repeated at
	// the start of the next chunk.
	OverlapChars = 200
)

// Span is the SourceSpan of one chunk: rune (character) offsets into the decoded
// document, and a 1-based inclusive line range. StartChar is inclusive, EndChar
// exclusive.
type Span struct {
	StartChar int `json:"start_char"`
	EndChar   int `json:"end_char"`
	StartLine int `json:"start_line"`
	EndLine   int `json:"end_line"`
}

// Chunk is one deterministic chunk of a parsed document.
type Chunk struct {
	// No is the 0-based chunk number within the document revision.
	No int
	// Text is the exact substring of the parsed text identified by Span.
	Text string
	// Span is the traceable source range.
	Span Span
}

type segment struct {
	start int
	end   int
}

// ChunkText splits a parsed document into deterministic chunks.
//
// The algorithm is deliberately boring so a reviewer can reproduce it by hand:
// paragraphs are maximal runs of non-empty lines; consecutive paragraphs are
// packed into a chunk up to MaxChunkChars; a paragraph longer than that is split
// into hard windows of MaxChunkChars with an OverlapChars tail repeated in the
// next chunk. Every emitted chunk is trimmed of surrounding whitespace and its
// span is recomputed after trimming, so the documented invariant holds:
//
//	chunk.Text == string([]rune(parsed.Text)[span.StartChar:span.EndChar])
//
// Empty input yields an empty slice, never a synthetic chunk.
func ChunkText(text string) []Chunk {
	runes := []rune(text)
	lineStarts := lineOffsets(runes)
	segments := paragraphSegments(runes)
	if len(segments) == 0 {
		return nil
	}

	var (
		chunks []Chunk
		start  = -1
		end    = -1
	)
	emit := func(from, to int) {
		chunks = append(chunks, buildChunk(len(chunks), runes, lineStarts, from, to))
	}
	for _, current := range segments {
		if current.end-current.start > MaxChunkChars {
			// An oversized paragraph always gets its own overlapping windows:
			// packing it with neighbours would only hide the boundary.
			if start >= 0 {
				emit(start, end)
			}
			for _, window := range hardWindows(runes, current) {
				emit(window.start, window.end)
			}
			start, end = -1, -1
			continue
		}
		if start < 0 {
			start, end = current.start, current.end
			continue
		}
		if current.end-start <= MaxChunkChars {
			end = current.end
			continue
		}
		emit(start, end)
		start, end = current.start, current.end
	}
	if start >= 0 {
		emit(start, end)
	}
	return chunks
}

// paragraphSegments returns maximal runs of non-empty lines. A line is empty
// when it contains only Unicode whitespace; a paragraph's span starts at its
// first non-space rune and ends at the last non-space rune of its last line, so
// it includes the internal newlines between its lines.
func paragraphSegments(runes []rune) []segment {
	var (
		segments []segment
		current  *segment
	)
	lineStart := 0
	for lineStart <= len(runes) {
		lineEnd := lineStart
		for lineEnd < len(runes) && runes[lineEnd] != '\n' {
			lineEnd++
		}
		trimmedStart, trimmedEnd := trimSpaceBounds(runes, lineStart, lineEnd)
		if trimmedStart < trimmedEnd {
			if current == nil {
				current = &segment{start: trimmedStart, end: trimmedEnd}
			} else {
				current.end = trimmedEnd
			}
		} else if current != nil {
			segments = append(segments, *current)
			current = nil
		}
		if lineEnd >= len(runes) {
			break
		}
		lineStart = lineEnd + 1
	}
	if current != nil {
		segments = append(segments, *current)
	}
	return segments
}

// hardWindows splits one oversized segment into overlapping windows, snapping
// each next start forward to the first word start at or after the overlap point
// so a chunk begins at a word boundary when one is nearby.
func hardWindows(runes []rune, current segment) []segment {
	var windows []segment
	start := current.start
	for start < current.end {
		end := start + MaxChunkChars
		if end > current.end {
			end = current.end
		}
		trimmedStart, trimmedEnd := trimSpaceBounds(runes, start, end)
		if trimmedStart < trimmedEnd {
			windows = append(windows, segment{start: trimmedStart, end: trimmedEnd})
		}
		if end >= current.end {
			break
		}
		next := end - OverlapChars
		if next <= start {
			next = end
		}
		for next < current.end && next > start && !unicode.IsSpace(runes[next-1]) {
			next++
		}
		if next <= start {
			next = end
		}
		start = next
	}
	return windows
}

func buildChunk(no int, runes []rune, lineStarts []int, start, end int) Chunk {
	return Chunk{
		No:   no,
		Text: string(runes[start:end]),
		Span: Span{
			StartChar: start,
			EndChar:   end,
			StartLine: lineOf(lineStarts, start),
			EndLine:   lineOf(lineStarts, end-1),
		},
	}
}

// trimSpaceBounds trims leading/trailing Unicode whitespace from [start,end).
func trimSpaceBounds(runes []rune, start, end int) (int, int) {
	for start < end && unicode.IsSpace(runes[start]) {
		start++
	}
	for end > start && unicode.IsSpace(runes[end-1]) {
		end--
	}
	return start, end
}

// lineOffsets returns the rune offset at which each line starts.
func lineOffsets(runes []rune) []int {
	offsets := []int{0}
	for index, char := range runes {
		if char == '\n' {
			offsets = append(offsets, index+1)
		}
	}
	return offsets
}

// lineOf returns the 1-based line containing rune offset. lineStarts is sorted,
// so a linear scan is the simplest truthful implementation.
func lineOf(lineStarts []int, offset int) int {
	line := 1
	for index, start := range lineStarts {
		if start > offset {
			break
		}
		line = index + 1
	}
	return line
}

// Excerpt is a short single-line rendering of a chunk body for the query answer.
// It never invents text: it is a whitespace-collapsed prefix of the chunk.
func Excerpt(text string, maxRunes int) string {
	collapsed := strings.Join(strings.Fields(text), " ")
	runes := []rune(collapsed)
	if maxRunes <= 0 || len(runes) <= maxRunes {
		return collapsed
	}
	return string(runes[:maxRunes]) + "…"
}
