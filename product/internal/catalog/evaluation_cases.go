package catalog

// F13A/T056: the per-case evaluation results read.
//
//	GET /api/v1/evaluation-reports/{report_id}/cases
//
// The contract's Report carries only four fields and has no place for per-case
// detail, so the sealed report artifact is the source of truth. This read is the
// documented non-contract surface (ADR 0013) that parses that sealed document and
// joins it with the real artifacts of each case's platform run:
//
//   - the report bytes are read from the object store and re-verified against the
//     artifact row's SHA-256 before parsing; an unreadable/tampered report is
//     never rendered as case data;
//   - the caller is the report's evaluation owner (or an administrator); every
//     other principal gets the same answer as a missing report, so a report id
//     cannot be enumerated;
//   - an attachment is only listed when its artifact row is ready and not
//     deleted, and it is linked to the case's run through the platform's own
//     rows (run_node_outputs / runs.output_artifact_id / run_artifacts). No
//     object key, staging key or seal path ever leaves this file.

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"sort"
	"strings"

	"orchara/platform/internal/background"
	"orchara/platform/internal/evaluation"
	"orchara/platform/internal/jcs"
	"orchara/platform/internal/objectstore"
	"orchara/platform/internal/uuid"
)

// EvaluationCaseAttachment is one downloadable artifact of one case's run. The
// response carries the artifact id only: a caller downloads it through the
// owner-only GET /api/v1/artifacts/{id}/download, so this read never mints a
// link and never leaks an object key.
type EvaluationCaseAttachment struct {
	ArtifactID string `json:"artifact_id"`
	Label      string `json:"label"`
	MediaType  string `json:"media_type"`
	SizeBytes  int64  `json:"size_bytes"`
	SHA256     string `json:"sha256"`
}

// EvaluationCaseResult is one case's parsed verdict plus its real attachments.
type EvaluationCaseResult struct {
	CaseID      string                     `json:"case_id"`
	Verdict     string                     `json:"verdict"`
	ReasonCode  string                     `json:"reason_code,omitempty"`
	Detail      string                     `json:"detail,omitempty"`
	Assertions  []evaluation.Outcome       `json:"assertions"`
	Facts       *evaluation.CaseFacts      `json:"facts,omitempty"`
	Attachments []EvaluationCaseAttachment `json:"attachments"`
}

// EvaluationCaseResults is the per-case view of one sealed report.
type EvaluationCaseResults struct {
	ReportID string                 `json:"report_id"`
	Status   string                 `json:"status"`
	Cases    []EvaluationCaseResult `json:"cases"`
}

// EvaluationCaseResults reads and authorizes one report's per-case view.
//
// The report must be the caller's own (or the caller must be an administrator);
// anything else answers exactly like a missing report. The body is the sealed
// report artifact's real bytes, re-verified against the recorded hash.
func (r *Reader) EvaluationCaseResults(
	ctx context.Context, reportID, principalID string, admin bool, objects *objectstore.Store,
) (*EvaluationCaseResults, error) {
	if !uuid.Valid(reportID) || !uuid.Valid(principalID) {
		return nil, ErrNotVisible
	}
	if objects == nil {
		return nil, objectstore.ErrNotConfigured
	}
	var (
		status     string
		artifactID string
		objectKey  string
		sha256     sql.NullString
		sizeBytes  sql.NullInt64
		state      string
		mediaType  string
		deletedAt  sql.NullTime
	)
	err := r.DB.QueryRowContext(ctx, `
		SELECT rep.status, rep.result_artifact_id::text, a.object_key, a.sha256,
		       a.size_bytes, a.state, a.media_type, a.deleted_at
		  FROM platform.evaluation_reports AS rep
		  JOIN platform.artifacts AS a ON a.id = rep.result_artifact_id
		 WHERE rep.id = $1::uuid
		   AND EXISTS (
		        SELECT 1 FROM platform.evaluation_jobs AS e
		         WHERE e.report_id = rep.id
		           AND (e.owner_id = $2::uuid OR $3::boolean)
		   )`, reportID, principalID, admin,
	).Scan(&status, &artifactID, &objectKey, &sha256, &sizeBytes, &state, &mediaType, &deletedAt)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrNotVisible
	}
	if err != nil {
		return nil, fmt.Errorf("reading the evaluation report for its cases: %w", err)
	}
	if deletedAt.Valid || state != "ready" || !sha256.Valid || !sizeBytes.Valid {
		return nil, ErrNotVisible
	}
	if mediaType != EvaluationDatasetMediaType || sizeBytes.Int64 <= 0 ||
		sizeBytes.Int64 > background.MaxEvaluationReportBytes {
		return nil, ErrNotVisible
	}

	document, err := readSealedReport(objects, objectKey, strings.ToLower(sha256.String), sizeBytes.Int64)
	if err != nil {
		return nil, err
	}

	results := &EvaluationCaseResults{
		ReportID: reportID,
		Status:   status,
		Cases:    make([]EvaluationCaseResult, 0, len(document.Cases)),
	}
	for _, item := range document.Cases {
		entry := EvaluationCaseResult{
			CaseID:      item.ID,
			Verdict:     item.Verdict,
			ReasonCode:  item.ReasonCode,
			Detail:      item.Detail,
			Assertions:  item.Assertions,
			Facts:       item.Facts,
			Attachments: []EvaluationCaseAttachment{},
		}
		if entry.Verdict == "" {
			entry.Verdict = item.Status
		}
		if entry.Assertions == nil {
			entry.Assertions = []evaluation.Outcome{}
		}
		if item.Facts != nil && strings.TrimSpace(item.Facts.RunID) != "" {
			attachments, err := r.caseAttachments(ctx, item.Facts.RunID)
			if err != nil {
				return nil, err
			}
			entry.Attachments = attachments
		}
		results.Cases = append(results.Cases, entry)
	}
	return results, nil
}

// readSealedReport reads the report bytes and proves they are the sealed bytes
// the database recorded. It never returns partial bytes.
func readSealedReport(
	store *objectstore.Store, objectKey, expectedSHA string, sizeBytes int64,
) (*evaluation.Report, error) {
	reader, _, err := store.Open(objectKey)
	if err != nil {
		return nil, fmt.Errorf("opening the sealed evaluation report: %w", err)
	}
	defer func() { _ = reader.Close() }()
	content, err := io.ReadAll(io.LimitReader(reader, background.MaxEvaluationReportBytes+1))
	if err != nil {
		return nil, fmt.Errorf("reading the sealed evaluation report: %w", err)
	}
	if int64(len(content)) != sizeBytes ||
		int64(len(content)) > background.MaxEvaluationReportBytes ||
		jcs.SHA256Hex(content) != expectedSHA {
		return nil, errors.New("the sealed evaluation report bytes do not match their recorded hash")
	}
	var document evaluation.Report
	if err := json.Unmarshal(content, &document); err != nil {
		return nil, errors.New("the sealed evaluation report is not valid JSON")
	}
	return &document, nil
}

// caseAttachments lists the ready artifacts of one case's run: the node's data and
// file outputs, the run's final output, and the safe diagnostics document a failed
// tool run publishes. Logs are deliberately excluded (they are operator evidence,
// not run data, exactly like the run artifact list).
func (r *Reader) caseAttachments(
	ctx context.Context, runID string,
) ([]EvaluationCaseAttachment, error) {
	if !uuid.Valid(runID) {
		return []EvaluationCaseAttachment{}, nil
	}
	seen := make(map[string]bool)
	attachments := make([]EvaluationCaseAttachment, 0, 4)

	rows, err := r.DB.QueryContext(ctx, `
		SELECT o.artifact_id::text, o.kind, o.label, a.media_type,
		       COALESCE(a.size_bytes, 0), COALESCE(a.sha256, '')
		  FROM platform.run_node_outputs AS o
		  JOIN platform.artifacts AS a ON a.id = o.artifact_id
		 WHERE o.run_id = $1::uuid
		   AND o.kind IN ('data', 'file')
		   AND a.state = 'ready'
		   AND a.deleted_at IS NULL
		 ORDER BY o.kind, o.label, o.artifact_id`, runID)
	if err != nil {
		return nil, fmt.Errorf("reading the case run outputs: %w", err)
	}
	defer rows.Close()
	for rows.Next() {
		var attachment EvaluationCaseAttachment
		var kind string
		if err := rows.Scan(&attachment.ArtifactID, &kind, &attachment.Label,
			&attachment.MediaType, &attachment.SizeBytes, &attachment.SHA256); err != nil {
			return nil, fmt.Errorf("reading the case run outputs: %w", err)
		}
		if kind == "data" && attachment.Label == "data" {
			attachment.Label = "summary.json"
		}
		seen[attachment.ArtifactID] = true
		attachments = append(attachments, attachment)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("reading the case run outputs: %w", err)
	}

	var (
		outputID   sql.NullString
		outputType string
		outputSize sql.NullInt64
		outputSHA  sql.NullString
	)
	err = r.DB.QueryRowContext(ctx, `
		SELECT r.output_artifact_id::text, a.media_type, a.size_bytes, a.sha256
		  FROM platform.runs AS r
		  JOIN platform.artifacts AS a ON a.id = r.output_artifact_id
		 WHERE r.id = $1::uuid
		   AND a.state = 'ready'
		   AND a.deleted_at IS NULL
		   AND r.deleted_at IS NULL`, runID,
	).Scan(&outputID, &outputType, &outputSize, &outputSHA)
	if err != nil && !errors.Is(err, sql.ErrNoRows) {
		return nil, fmt.Errorf("reading the case run output: %w", err)
	}
	if err == nil && outputID.Valid && !seen[outputID.String] {
		seen[outputID.String] = true
		attachments = append(attachments, EvaluationCaseAttachment{
			ArtifactID: outputID.String,
			Label:      "output",
			MediaType:  outputType,
			SizeBytes:  outputSize.Int64,
			SHA256:     outputSHA.String,
		})
	}

	rows, err = r.DB.QueryContext(ctx, `
		SELECT a.id::text, a.purpose, a.media_type, COALESCE(a.size_bytes, 0),
		       COALESCE(a.sha256, '')
		  FROM platform.run_artifacts AS ra
		  JOIN platform.artifacts AS a ON a.id = ra.artifact_id
		 WHERE ra.run_id = $1::uuid
		   AND ra.relation IN ('artifact', 'log', 'output')
		   AND a.state = 'ready'
		   AND a.deleted_at IS NULL
		 ORDER BY a.created_at, a.id`, runID)
	if err != nil {
		return nil, fmt.Errorf("reading the case run artifacts: %w", err)
	}
	defer rows.Close()
	for rows.Next() {
		var (
			artifactID string
			purpose    string
			mediaType  string
			sizeBytes  int64
			sha256     string
		)
		if err := rows.Scan(&artifactID, &purpose, &mediaType, &sizeBytes, &sha256); err != nil {
			return nil, fmt.Errorf("reading the case run artifacts: %w", err)
		}
		if seen[artifactID] || purpose == "log" {
			continue
		}
		label := "diagnostics"
		if purpose == "final_output" {
			label = "output"
		}
		seen[artifactID] = true
		attachments = append(attachments, EvaluationCaseAttachment{
			ArtifactID: artifactID,
			Label:      label,
			MediaType:  mediaType,
			SizeBytes:  sizeBytes,
			SHA256:     sha256,
		})
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("reading the case run artifacts: %w", err)
	}

	sort.SliceStable(attachments, func(left, right int) bool {
		if attachments[left].Label != attachments[right].Label {
			return attachments[left].Label < attachments[right].Label
		}
		return attachments[left].ArtifactID < attachments[right].ArtifactID
	})
	return attachments, nil
}
