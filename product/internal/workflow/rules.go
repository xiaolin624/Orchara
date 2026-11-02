package workflow

import (
	_ "embed"
	"sync"
)

// rules.json is the rule table this package validates with. It is a verbatim
// copy of probes/p05_dsl/rules.json; validate_corpus_test.go fails the build's
// tests if the copy drifts from the probe. Embedding it keeps the validator
// self-contained for every binary (no repository path at run time) while the
// probe file stays the source of record.
//
//go:embed rules.json
var embeddedRules []byte

var (
	defaultRulesOnce sync.Once
	defaultRules     *Rules
	defaultRulesErr  error
)

// DefaultRules returns the compiled embedded rule table. The result is shared
// and read-only: callers must not mutate it (all its maps are compiled at load).
// A load failure is a broken build input, not a data condition; it is returned
// rather than panicking so a caller can answer 500 instead of crashing.
func DefaultRules() (*Rules, error) {
	defaultRulesOnce.Do(func() {
		defaultRules, defaultRulesErr = parseRules(embeddedRules, "internal/workflow/rules.json")
	})
	return defaultRules, defaultRulesErr
}
