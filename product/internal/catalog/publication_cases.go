package catalog

// F02B/T068: the publication author-case gate.
//
// The normative list is `docs/spec/EVALUATION.md` §6 ("发布硬门禁"): an application
// release needs at least five author cases covering 正常 (normal), 坏输入 (bad
// input), 歧义/越界 (ambiguity/out-of-bounds), 工具失败 (tool failure) and
// 输出检查 (output check). The specification names the five scenarios but does
// not define how a sealed report encodes them, so this file defines the minimum
// over the report's own per-case evidence and nothing more:
//
//   - a case is only considered when its verdict is `passed`; a report whose
//     status is `passed` has no other case status (evaluation.ComputeStatus), so
//     this is the same rule read from the evidence;
//   - normal:          the case's collected run state is `succeeded`;
//   - output check:    the case carries at least one passed `json_equals` or
//     `decimal_equals` assertion (a real output value was inspected);
//   - bad input / ambiguity-out-of-bounds / tool failure: the case carries a
//     passed `failure_reason` outcome (the T056 mechanism where the author
//     declares the business failure a bad-input case must observe). The
//     observed error code is classified with the closed rules below. These are
//     stable platform codes, never provider text.
//
// Classification of a `failure_reason` code:
//
//	tool_* / sandbox_*                         -> tool_failure
//	ambiguous_* / out_of_* / *_out_of_scope /
//	  limit_* / row_limit_* / over_limit /
//	  too_large / unsupported_input             -> ambiguity_or_out_of_bounds
//	anything else (missing_column, duplicate_
//	  order_id, negative_amount, ...)           -> bad_input
//
// The classification is deliberately a documented interpretation, recorded in
// ADR 0019 and in the T068 report: the server cannot re-run the author's checker
// at approval time without executing arbitrary user workflows, so it enforces a
// minimum over the evidence the trusted runner sealed. The report must also
// carry at least MinAuthorCases passed cases, so a single case satisfying
// several predicates cannot make the gate vacuous.

import (
	"sort"
	"strings"

	"orchara/platform/internal/evaluation"
)

// MinAuthorCases is the "至少 5 个作者案例" minimum from EVALUATION.md §6.
const MinAuthorCases = 5

// The five categories, in the specification's order.
const (
	AuthorCaseNormal      = "normal"
	AuthorCaseBadInput    = "bad_input"
	AuthorCaseOutOfBounds = "ambiguity_or_out_of_bounds"
	AuthorCaseToolFailure = "tool_failure"
	AuthorCaseOutputCheck = "output_check"
)

// AuthorCaseCategories is the fixed order the gate and the snapshot use.
var AuthorCaseCategories = []string{
	AuthorCaseNormal,
	AuthorCaseBadInput,
	AuthorCaseOutOfBounds,
	AuthorCaseToolFailure,
	AuthorCaseOutputCheck,
}

// AuthorCaseEvidence is one passed case and the categories its sealed evidence
// satisfies. It carries no user content: a case id, the categories and the
// stable failure code (when the case is a declared-failure case).
type AuthorCaseEvidence struct {
	CaseID        string   `json:"case_id"`
	Categories    []string `json:"categories"`
	FailureReason string   `json:"failure_reason,omitempty"`
}

// AuthorCaseCoverage is the gate's verdict plus the evidence used.
type AuthorCaseCoverage struct {
	// Satisfied is true only when every category is covered and at least
	// MinAuthorCases passed cases are present.
	Satisfied bool `json:"satisfied"`
	// CaseCount is the number of cases in the sealed report.
	CaseCount int `json:"case_count"`
	// MinimumCases is MinAuthorCases (recorded so an admin reads the policy).
	MinimumCases int `json:"minimum_cases"`
	// Categories are the satisfied categories, in AuthorCaseCategories order.
	Categories []string `json:"categories"`
	// Missing are the unsatisfied categories, in the same order.
	Missing []string `json:"missing"`
	// Evidence is one entry per passed case that satisfied at least one
	// category.
	Evidence []AuthorCaseEvidence `json:"evidence"`
}

// EvaluateAuthorCaseCoverage derives the publication gate from a sealed report
// document. A nil or non-passed document never satisfies it.
func EvaluateAuthorCaseCoverage(document *evaluation.Report) AuthorCaseCoverage {
	coverage := AuthorCaseCoverage{
		CaseCount:    0,
		MinimumCases: MinAuthorCases,
		Categories:   []string{},
		Missing:      append([]string(nil), AuthorCaseCategories...),
		Evidence:     []AuthorCaseEvidence{},
	}
	if document == nil {
		return coverage
	}
	coverage.CaseCount = len(document.Cases)
	if document.Status != evaluation.StatusPassed {
		return coverage
	}
	seen := make(map[string]bool, len(AuthorCaseCategories))
	for _, item := range document.Cases {
		if item.Status != evaluation.VerdictPassed {
			continue
		}
		evidence := AuthorCaseEvidence{CaseID: item.ID}
		mark := func(category string) {
			if !containsString(evidence.Categories, category) {
				evidence.Categories = append(evidence.Categories, category)
			}
		}
		if item.Facts != nil && item.Facts.RunState == "succeeded" {
			mark(AuthorCaseNormal)
		}
		for _, outcome := range item.Assertions {
			if !outcome.Passed {
				continue
			}
			switch outcome.Kind {
			case "json_equals", "decimal_equals":
				mark(AuthorCaseOutputCheck)
			case evaluation.KindFailureReason:
				if code := strings.ToLower(strings.TrimSpace(outcome.Actual)); code != "" {
					evidence.FailureReason = code
					mark(classifyFailureReason(code))
				}
			}
		}
		if len(evidence.Categories) == 0 {
			continue
		}
		sortCategories(evidence.Categories)
		for _, category := range evidence.Categories {
			seen[category] = true
		}
		coverage.Evidence = append(coverage.Evidence, evidence)
	}
	coverage.Categories = coverage.Categories[:0]
	coverage.Missing = coverage.Missing[:0]
	for _, category := range AuthorCaseCategories {
		if seen[category] {
			coverage.Categories = append(coverage.Categories, category)
		} else {
			coverage.Missing = append(coverage.Missing, category)
		}
	}
	coverage.Satisfied = len(coverage.Missing) == 0 && coverage.CaseCount >= MinAuthorCases
	return coverage
}

// classifyFailureReason maps one observed business failure code to the
// specification's author-case category. See the file comment for the closed
// rules; an unknown code is a bad-input case (the default business rejection).
func classifyFailureReason(code string) string {
	switch {
	case strings.HasPrefix(code, "tool_"), strings.HasPrefix(code, "sandbox_"):
		return AuthorCaseToolFailure
	case strings.HasPrefix(code, "ambiguous_"),
		strings.HasPrefix(code, "out_of_"),
		strings.HasSuffix(code, "_out_of_scope"),
		strings.HasPrefix(code, "limit_"),
		strings.HasPrefix(code, "row_limit_"),
		code == "over_limit" || code == "too_large" || code == "unsupported_input":
		return AuthorCaseOutOfBounds
	default:
		return AuthorCaseBadInput
	}
}

// sortCategories orders a case's category list by the specification's order so
// the sealed snapshot is deterministic.
func sortCategories(categories []string) {
	sort.SliceStable(categories, func(left, right int) bool {
		return categoryIndex(categories[left]) < categoryIndex(categories[right])
	})
}

func categoryIndex(category string) int {
	for index, candidate := range AuthorCaseCategories {
		if candidate == category {
			return index
		}
	}
	return len(AuthorCaseCategories)
}
