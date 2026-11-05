package evaluation

// F13A/T052: the sealed evaluation report document.
//
// The report is the evaluation's immutable completion product. The HTTP
// contract's Report carries only four fields (id/status/manifest_sha256/
// result_artifact_id) and has no place for per-case detail, so the per-case
// results, the per-assertion verdicts, the collected facts and the timings live
// here, inside the sealed result artifact that result_artifact_id names. T056
// reads this document to render per-case results; T065 reads the evaluation row
// plus this document for progress and business-vs-infrastructure failure.
//
// The document extends T026's shape additively. The five load-bearing fields
// (report_version/kind/status/checks/cases plus the two binding hashes) keep
// their exact meaning; the completion guard in internal/background remains the
// only writer of a report row.

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"strings"
	"time"
)

const (
	// ReportVersion is the only report document version this build writes.
	ReportVersion = 1
	// ReportKind is the document's self-declaration.
	ReportKind = "evaluation"
	// RunnerEvaluatorID identifies the real F13A runner. Only a report that
	// declares it is held to the per-case assertion-evidence rules below; the
	// T026 synthetic placeholder and the guard's crafted-report tests keep
	// their pre-existing rules.
	RunnerEvaluatorID = "orchara-platform-runner/1"
	// SyntheticEvaluatorID is the legacy T026 placeholder evaluator.
	SyntheticEvaluatorID = "synthetic-jcs-case/1"

	// StatusPassed means every case ran and every assertion passed.
	StatusPassed = "passed"
	// StatusFailed means at least one case did not run or did not pass. A
	// failed report is a *successful* evaluation job (a business verdict).
	StatusFailed = "failed"
	// StatusNotRun means nothing could be collected: no case produced a
	// verdict. It can never become passed.
	StatusNotRun = "not_run"

	// VerdictPassed/Failed/NotRun/Blocked are the per-case verdicts.
	VerdictPassed  = "passed"
	VerdictFailed  = "failed"
	VerdictNotRun  = "not_run"
	VerdictBlocked = "blocked"
)

// Report is the sealed report document. Unknown fields are refused by the
// guard's strict decoder, so this struct is the whole vocabulary.
type Report struct {
	ReportVersion     int          `json:"report_version"`
	Kind              string       `json:"kind"`
	EvaluationKind    string       `json:"evaluation_kind"`
	EvaluationID      string       `json:"evaluation_id"`
	DatasetArtifactID string       `json:"dataset_artifact_id"`
	DatasetSHA256     string       `json:"dataset_sha256"`
	ManifestSHA256    string       `json:"manifest_sha256"`
	Status            string       `json:"status"`
	Checks            []string     `json:"checks"`
	CaseCount         int          `json:"case_count"`
	Cases             []CaseReport `json:"cases"`
	Synthetic         bool         `json:"synthetic"`
	Evaluator         string       `json:"evaluator"`

	// F13A additions. They are additive; every one of them is optional so a
	// T026-shaped document still decodes and keeps its old rules.
	Quality    *Quality          `json:"quality,omitempty"`
	Provenance *ReportProvenance `json:"provenance,omitempty"`
	Limits     []string          `json:"limits,omitempty"`
	StartedAt  string            `json:"started_at,omitempty"`
	FinishedAt string            `json:"finished_at,omitempty"`
}

// CaseReport is one case's verdict with the evidence behind it.
type CaseReport struct {
	ID     string `json:"id"`
	Status string `json:"status"`
	// Verdict repeats Status explicitly for per-case readers (T056). The guard
	// requires the two to agree.
	Verdict    string `json:"verdict,omitempty"`
	ReasonCode string `json:"reason_code,omitempty"`
	Detail     string `json:"detail,omitempty"`
	// Legacy T026 synthetic evidence; kept so the placeholder's behaviour and
	// its tests are unchanged.
	ExpectedSHA256 string `json:"expected_sha256,omitempty"`
	ActualSHA256   string `json:"actual_sha256,omitempty"`
	// Assertions is the per-assertion outcome list (one per declared checker).
	Assertions []Outcome    `json:"assertions,omitempty"`
	Facts      *CaseFacts   `json:"facts,omitempty"`
	Timings    *CaseTimings `json:"timings,omitempty"`
}

// CaseFacts is the independently collected truth for one case's run. It is
// evidence, not a trace: a run state, an output artifact identity and hash, the
// real effect counts and the citation sources.
type CaseFacts struct {
	RunID            string           `json:"run_id,omitempty"`
	RunState         string           `json:"run_state,omitempty"`
	RunErrorCode     string           `json:"run_error_code,omitempty"`
	OutputArtifactID *string          `json:"output_artifact_id,omitempty"`
	OutputSHA256     string           `json:"output_sha256,omitempty"`
	EffectCounts     map[string]int64 `json:"effect_counts"`
	// EffectEvidence explains why effect_counts is null: a ledger row in
	// unknown/dispatching could mean an effect happened without a confirmed
	// outcome, so the evidence is uncollected rather than zero. Empty when the
	// counts were collected (the map may then legitimately be empty).
	EffectEvidence  string   `json:"effect_evidence,omitempty"`
	CitationSources []string `json:"citation_sources"`
	// F13A/T056: the caller fixture bound to the run as its task_input artifact,
	// by content. It is real platform evidence (run_artifacts -> artifacts), so a
	// per-case reader can show which input bytes produced the result.
	TaskInputArtifactID string         `json:"task_input_artifact_id,omitempty"`
	TaskInputSHA256     string         `json:"task_input_sha256,omitempty"`
	Provenance          *RunProvenance `json:"provenance,omitempty"`
}

// CaseTimings records how long the case's run and collection took, in
// milliseconds, on the platform's own clock.
type CaseTimings struct {
	RunStartedAt      string `json:"run_started_at,omitempty"`
	RunFinishedAt     string `json:"run_finished_at,omitempty"`
	RunDurationMS     int64  `json:"run_duration_ms,omitempty"`
	CollectDurationMS int64  `json:"collect_duration_ms,omitempty"`
}

// Quality is the *optional, informational only* quality score slot.
//
// There is no judge adapter in this build, so the real runner writes
// Adapter="not_installed" and no Score. The field exists so that a later ticket
// can add one without changing the report version, and it is deliberately
// incapable of gating the verdict: ComputeStatus never reads it, and the guard
// refuses a report whose status disagrees with its cases no matter what
// Quality says. A high judge score with no written success is still a failure.
type Quality struct {
	Informational bool     `json:"informational"`
	Adapter       string   `json:"adapter"`
	Score         *float64 `json:"score"`
	Note          string   `json:"note,omitempty"`
}

// ReportProvenance binds the report to the frozen identity it executed under:
// the snapshot, the application draft revision, and the compiler/worker/state
// identities the snapshot recorded. It is what makes "运行产物可追溯到
// manifest/dataset" a verifiable statement rather than a claim.
type ReportProvenance struct {
	SnapshotID         string `json:"snapshot_id"`
	ApplicationID      string `json:"application_id,omitempty"`
	DraftRevision      int64  `json:"draft_revision,omitempty"`
	CompilerVersion    string `json:"compiler_version,omitempty"`
	WorkerImageDigest  string `json:"worker_image_digest,omitempty"`
	StateSchemaVersion string `json:"state_schema_version,omitempty"`
	DatasetArtifactID  string `json:"dataset_artifact_id,omitempty"`
}

// ComputeStatus derives the report status from its cases alone.
//
// This is the only status calculation in the system: the handler calls it to
// build the document, and the completion guard calls it again to refuse any
// document whose declared status disagrees. Quality/judge scores are never an
// input.
//
//	no cases                    -> not_run
//	every case passed + typed   -> passed   (each passed case must carry >=1 assertion)
//	no case produced a verdict  -> not_run  (nothing was collected)
//	anything else               -> failed
func ComputeStatus(cases []CaseReport) string {
	if len(cases) == 0 {
		return StatusNotRun
	}
	allPassed := true
	anyCollected := false
	for _, item := range cases {
		switch item.Status {
		case VerdictPassed:
			if len(item.Assertions) == 0 {
				// An empty checker is not a pass.
				allPassed = false
			}
			anyCollected = true
		case VerdictNotRun, VerdictBlocked:
			allPassed = false
		default:
			allPassed = false
			anyCollected = true
		}
	}
	switch {
	case allPassed:
		return StatusPassed
	case !anyCollected:
		return StatusNotRun
	default:
		return StatusFailed
	}
}

// ComputeLegacyStatus is the pre-F13A status rule, kept byte-compatible for the
// T026 synthetic placeholder and for the completion guard's crafted-report
// tests: it only looks at case statuses and does not require assertion
// evidence. It is never used for a report that declares the real runner.
func ComputeLegacyStatus(cases []CaseReport) string {
	if len(cases) == 0 {
		return StatusNotRun
	}
	passed, failed := 0, 0
	for _, item := range cases {
		switch item.Status {
		case VerdictPassed:
			passed++
		default:
			failed++
		}
	}
	switch {
	case failed == 0 && passed > 0:
		return StatusPassed
	case failed > 0:
		return StatusFailed
	default:
		return StatusNotRun
	}
}

// ValidateShape applies the report's structural and verdict rules. It returns a
// short reason ("" when the document is acceptable) so the caller can map it to
// its own stable error code. It never trusts a declared status: the status is
// recomputed from the cases and compared.
func ValidateShape(document *Report) string {
	if document.ReportVersion != ReportVersion || document.Kind != ReportKind {
		return "report_version/kind is not the current report document"
	}
	if document.EvaluationKind != "model" && document.EvaluationKind != "application" {
		return "evaluation_kind must be model or application"
	}
	if len(document.Checks) == 0 {
		return "the report declares no checks; an evaluation without a check is not a report"
	}
	for _, check := range document.Checks {
		if strings.TrimSpace(check) == "" {
			return "the report declares a blank check name"
		}
	}
	if document.CaseCount < 0 || document.CaseCount != len(document.Cases) {
		return "case_count disagrees with the cases array"
	}
	seen := make(map[string]bool, len(document.Cases))
	for _, item := range document.Cases {
		if strings.TrimSpace(item.ID) == "" {
			return "a case result has no id"
		}
		if seen[item.ID] {
			return "a case id appears more than once"
		}
		seen[item.ID] = true
		switch item.Status {
		case VerdictPassed, VerdictFailed, VerdictNotRun, VerdictBlocked:
		default:
			return "a case result carries an unknown status"
		}
		if item.Verdict != "" && item.Verdict != item.Status {
			return "a case's verdict disagrees with its status"
		}
		if item.Status == VerdictPassed {
			if item.ReasonCode != "" {
				return "a passed case carries a refusal reason"
			}
			if document.Evaluator == RunnerEvaluatorID {
				// The real runner's evidence rule: a pass must be backed by at
				// least one executed assertion and every one of them must have
				// passed. This is the "empty checker is not a pass" guarantee at
				// report time, not just at submit time.
				if len(item.Assertions) == 0 {
					return "a passed case carries no assertion evidence; an empty checker is not a pass"
				}
				for _, outcome := range item.Assertions {
					if !outcome.Passed {
						return "a passed case carries a failed assertion"
					}
				}
			}
		}
	}
	if document.Evaluator == RunnerEvaluatorID && document.Provenance == nil {
		return "the runner's report does not declare its provenance"
	}
	expected := ComputeLegacyStatus(document.Cases)
	if document.Evaluator == RunnerEvaluatorID {
		expected = ComputeStatus(document.Cases)
	}
	if expected != document.Status {
		return "the declared status disagrees with the cases (" + expected + ")"
	}
	return ""
}

// DecodeReport strictly decodes one sealed report document. Unknown fields,
// trailing data or invalid JSON are a malformed report, never an accepted one.
//
// It is the single decoder used by the completion guard (which first writes the
// report row) and by F02B's publication gate (which re-reads that row's sealed
// bytes and must reach the same verdict). A second decoder would be a place for
// the two acceptances to drift.
func DecodeReport(content []byte) (*Report, error) {
	decoder := json.NewDecoder(bytes.NewReader(content))
	decoder.DisallowUnknownFields()
	var document Report
	if err := decoder.Decode(&document); err != nil {
		return nil, fmt.Errorf("decoding the report document: %w", err)
	}
	// A second value after the document is trailing garbage.
	var trailing any
	if err := decoder.Decode(&trailing); !errors.Is(err, io.EOF) {
		return nil, errors.New("the report document has trailing data")
	}
	return &document, nil
}

// FormatTime renders a platform timestamp for the report. Zero times are
// omitted rather than rendered as the zero instant.
func FormatTime(value *time.Time) string {
	if value == nil || value.IsZero() {
		return ""
	}
	return value.UTC().Format(time.RFC3339Nano)
}
