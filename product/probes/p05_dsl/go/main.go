// Command p05_dsl validates the shared workflow corpus and prints the wire
// envelope, or checks the corpus against its pinned expectations.
//
// Output contract (shared with python/validate.py so tests can compare the
// two byte for byte):
//
//	canonical JSON envelope        exit 0
//	"OK: N cases"                  exit 0   (-check only)
//	diagnostics on stderr          exit 1
package main

import (
	"encoding/json"
	"fmt"
	"os"
	"strings"
)

func main() {
	if err := run(os.Args[1:]); err != nil {
		fmt.Fprintf(os.Stderr, "p05_dsl: %v\n", err)
		os.Exit(1)
	}
}

func run(args []string) error {
	var corpusPath, rulesPath string
	var check bool
	for index := 0; index < len(args); index++ {
		switch args[index] {
		case "-corpus", "--corpus":
			index++
			if index >= len(args) {
				return fmt.Errorf("-corpus needs a path")
			}
			corpusPath = args[index]
		case "-rules", "--rules":
			index++
			if index >= len(args) {
				return fmt.Errorf("-rules needs a path")
			}
			rulesPath = args[index]
		case "-check", "--check":
			check = true
		case "-h", "--help":
			return fmt.Errorf("usage: p05_dsl -corpus <corpus.json> [-rules <rules.json>] [-check]")
		default:
			return fmt.Errorf("unknown argument %q", args[index])
		}
	}
	if corpusPath == "" {
		return fmt.Errorf("usage: p05_dsl -corpus <corpus.json> [-rules <rules.json>] [-check]")
	}
	rulesPath = ResolveRulesPath(corpusPath, rulesPath)

	raw, err := os.ReadFile(corpusPath)
	if err != nil {
		return err
	}
	var payload any
	if err := json.Unmarshal(raw, &payload); err != nil {
		return fmt.Errorf("corpus %s: %w", corpusPath, err)
	}
	if failures := CheckCorpusShape(payload); len(failures) > 0 {
		return fmt.Errorf("corpus is not well formed:\n  %s", strings.Join(failures, "\n  "))
	}
	corpus, err := ParseCorpus(payload)
	if err != nil {
		return err
	}
	rules, err := LoadRules(rulesPath)
	if err != nil {
		return err
	}
	corpusSHA := SHA256Hex(raw)
	output, err := RunCorpus(corpus, corpusSHA, rules)
	if err != nil {
		return err
	}
	if check {
		if failures := CheckExpectations(corpus, output); len(failures) > 0 {
			return fmt.Errorf("corpus disagreement:\n  %s", strings.Join(failures, "\n  "))
		}
		fmt.Printf("OK: %d cases\n", len(corpus.Cases))
		return nil
	}
	canonical, err := Canonicalize(output)
	if err != nil {
		return err
	}
	os.Stdout.WriteString(canonical + "\n")
	return nil
}
