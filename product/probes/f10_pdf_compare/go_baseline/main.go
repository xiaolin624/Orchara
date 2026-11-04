// F10/T060 comparison baseline probe.
//
// This is the *registered* platform parser and chunker, exercised exactly as the
// real ingestion pipeline calls them (knowledge.ParserFor("application/pdf") ->
// Parse -> ChunkParsed -> ChunkerVersionFor). It prints one JSON record per
// fixture so the comparison harness can measure text coverage, page attribution,
// chunk shape and wall clock on frozen inputs without going through HTTP and the
// database for every run (the real end-to-end path is exercised by the pytest
// stack tests).
//
// Usage:
//
//	go run ./probes/f10_pdf_compare/go_baseline -out result.json fixture1.pdf ...
package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"os"
	"runtime"
	"time"

	"orchara/platform/internal/knowledge"
)

type chunkRecord struct {
	No            int    `json:"no"`
	Text          string `json:"text"`
	Chars         int    `json:"chars"`
	Page          int    `json:"page"`
	PageStart     int    `json:"page_start"`
	PageEnd       int    `json:"page_end"`
	PageCharStart int    `json:"page_char_start"`
	PageCharEnd   int    `json:"page_char_end"`
}

type fixtureRecord struct {
	File             string        `json:"file"`
	SHA256           string        `json:"sha256"`
	ParseNS          int64         `json:"parse_ns"`
	ChunkNS          int64         `json:"chunk_ns"`
	Iterations       int           `json:"iterations"`
	ComputeNSMean    int64         `json:"compute_ns_mean"`
	PageCount        int           `json:"page_count"`
	Pages            []string      `json:"pages"`
	PagesWithoutText []int         `json:"pages_without_text"`
	Chunks           []chunkRecord `json:"chunks"`
	ParseError       string        `json:"parse_error,omitempty"`
	ChunkerVersion   string        `json:"chunker_version,omitempty"`
}

type report struct {
	ParserVersion  string          `json:"parser_version"`
	ChunkerVersion string          `json:"chunker_version"`
	GoVersion      string          `json:"go_version"`
	Fixtures       []fixtureRecord `json:"fixtures"`
}

// benchmarkIterations is repeated because one PDF parse+chunk of a 2-3 page
// fixture can be below the Windows monotonic-clock resolution; the mean over a
// short fixed loop is a real measured duration instead of a rounded 0.
const benchmarkIterations = 20

func main() {
	out := flag.String("out", "", "write the JSON report here (default stdout)")
	flag.Parse()
	if flag.NArg() == 0 {
		fmt.Fprintln(os.Stderr, "usage: go_baseline [-out file] fixture.pdf ...")
		os.Exit(2)
	}
	parser, ok := knowledge.ParserFor(knowledge.MediaTypePDF)
	if !ok {
		fmt.Fprintln(os.Stderr, "no registered parser for application/pdf")
		os.Exit(1)
	}
	result := report{
		ParserVersion:  parser.Version(),
		ChunkerVersion: knowledge.ChunkerVersionPDF,
		GoVersion:      runtime.Version(),
		Fixtures:       make([]fixtureRecord, 0, flag.NArg()),
	}
	for _, path := range flag.Args() {
		content, err := os.ReadFile(path)
		if err != nil {
			fmt.Fprintf(os.Stderr, "reading %s: %v\n", path, err)
			os.Exit(1)
		}
		digest := sha256.Sum256(content)
		record := fixtureRecord{
			File:   path,
			SHA256: hex.EncodeToString(digest[:]),
		}
		start := time.Now()
		parsed, parseErr := parser.Parse(content)
		record.ParseNS = time.Since(start).Nanoseconds()
		record.Iterations = benchmarkIterations
		var totalNS int64
		for iteration := 0; iteration < benchmarkIterations; iteration++ {
			iterationStart := time.Now()
			iterationParsed, iterationErr := parser.Parse(content)
			if iterationErr == nil {
				_ = knowledge.ChunkParsed(iterationParsed)
			}
			totalNS += time.Since(iterationStart).Nanoseconds()
		}
		record.ComputeNSMean = totalNS / int64(benchmarkIterations)
		if parseErr != nil {
			record.ParseError = failureCode(parseErr)
			result.Fixtures = append(result.Fixtures, record)
			continue
		}
		start = time.Now()
		chunks := knowledge.ChunkParsed(parsed)
		record.ChunkNS = time.Since(start).Nanoseconds()
		record.ChunkerVersion = knowledge.ChunkerVersionFor(parsed)
		record.PageCount = parsed.PDF.PageCount
		record.Pages = make([]string, len(parsed.PDF.Pages))
		runes := []rune(parsed.Text)
		for index, page := range parsed.PDF.Pages {
			record.Pages[index] = string(runes[page.StartChar:page.EndChar])
		}
		record.PagesWithoutText = parsed.PDF.EmptyPages
		record.Chunks = make([]chunkRecord, 0, len(chunks))
		for _, chunk := range chunks {
			record.Chunks = append(record.Chunks, chunkRecord{
				No:            chunk.No,
				Text:          chunk.Text,
				Chars:         len([]rune(chunk.Text)),
				Page:          chunk.PDF.PageStart,
				PageStart:     chunk.PDF.PageStart,
				PageEnd:       chunk.PDF.PageEnd,
				PageCharStart: chunk.PDF.PageCharStart,
				PageCharEnd:   chunk.PDF.PageCharEnd,
			})
		}
		result.Fixtures = append(result.Fixtures, record)
	}
	payload, err := json.MarshalIndent(result, "", "  ")
	if err != nil {
		fmt.Fprintf(os.Stderr, "encoding the report: %v\n", err)
		os.Exit(1)
	}
	if *out != "" {
		if err := os.WriteFile(*out, append(payload, '\n'), 0o644); err != nil {
			fmt.Fprintf(os.Stderr, "writing %s: %v\n", *out, err)
			os.Exit(1)
		}
		return
	}
	fmt.Println(string(payload))
}

// failureCode extracts the stable document failure code from a parser error.
func failureCode(err error) string {
	var parseErr *knowledge.ParseError
	if errors.As(err, &parseErr) {
		return parseErr.FailureCode
	}
	switch {
	case errors.Is(err, knowledge.ErrEmptyContent):
		return "ingest_empty_content"
	default:
		return "pdf_invalid"
	}
}
