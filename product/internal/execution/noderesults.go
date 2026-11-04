package execution

// F04A/T042: per-node result provenance and the owner-facing read.
//
//	POST /internal/v1/runs/{run_id}/node-results  recordNodeResults (mTLS, non-contract)
//	GET  /api/v1/runs/{run_id}/node-results       listRunNodeResults (owner only, non-contract)
//
// A python node produces three classes of result (data/file/log). The bytes are
// moved by the existing run-scoped artifact endpoints and registered as
// `node_output`/`log` artifacts; this file records only the mapping "which node
// produced which ready artifact as which class". Raw tool output never reaches
// this table: the caller may only point at an artifact the live attempt already
// completed and registered through the server-side path, which is the admission
// boundary issue #43 asks for.
//
// The two methods are deliberately narrow:
//   - RecordNodeResults re-proves the live attempt via resolveScope (the same
//     statement every run artifact call uses), checks every artifact is ready,
//     belongs to this run/attempt and carries the purpose matching the kind, then
//     inserts provenance rows insert-once (a replay is a no-op, not an update).
//   - ListNodeResults is a pure read projection of provenance joined to the
//     artifact facts. The HTTP layer applies getRun's owner-only 404 rule before
//     calling it; no content, object key or signed URL is returned.

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"regexp"
	"strings"

	"orchara/platform/internal/uuid"
)

const (
	// NodeResultKindData is the validated output.json.data document.
	NodeResultKindData = "data"
	// NodeResultKindFile is one admitted tool artifact.
	NodeResultKindFile = "file"
	// NodeResultKindLog is the capped stdout/stderr capture.
	NodeResultKindLog = "log"

	// MaxNodeResultsPerCall bounds one report batch.
	MaxNodeResultsPerCall = 64
	// maxNodeResultLabel bounds the display label.
	maxNodeResultLabel = 512
)

// nodeResultNodeIDPattern is exactly the sealed definition's node-id pattern.
var nodeResultNodeIDPattern = regexp.MustCompile(`^[A-Za-z][A-Za-z0-9_-]{0,63}$`)

// nodeResultKinds is the closed class enum.
var nodeResultKinds = map[string]bool{
	NodeResultKindData: true,
	NodeResultKindFile: true,
	NodeResultKindLog:  true,
}

// nodeResultPurposes is the artifact purpose each class must have been uploaded
// with. It is the "logs are not data" rule enforced at report time: a stdout
// capture cannot be reported as data even if the caller says so.
var nodeResultPurposes = map[string]string{
	NodeResultKindData: RunArtifactPurposeNodeOutput,
	NodeResultKindFile: RunArtifactPurposeNodeOutput,
	NodeResultKindLog:  RunArtifactPurposeLog,
}

// NodeResult is one {node, kind, artifact, label} row a worker reports.
type NodeResult struct {
	NodeID     string
	Kind       string
	ArtifactID string
	Label      string
}

// RunNodeResultView is one owner-visible node result. It carries artifact facts,
// never content or an object key.
type RunNodeResultView struct {
	NodeID     string `json:"node_id"`
	Kind       string `json:"kind"`
	ArtifactID string `json:"artifact_id"`
	Label      string `json:"label"`
	MediaType  string `json:"media_type"`
	SizeBytes  int64  `json:"size_bytes"`
	SHA256     string `json:"sha256"`
}

// validateNodeResults normalises and bounds a report batch before any row is
// read. A malformed entry is a located 422, never a partial write.
func validateNodeResults(results []NodeResult) ([]NodeResult, error) {
	if len(results) == 0 {
		return nil, &ValidationError{Field: "results", Message: "results must contain at least one entry"}
	}
	if len(results) > MaxNodeResultsPerCall {
		return nil, &ValidationError{
			Field:   "results",
			Message: fmt.Sprintf("results must contain at most %d entries", MaxNodeResultsPerCall),
		}
	}
	normalised := make([]NodeResult, 0, len(results))
	seen := make(map[string]bool, len(results))
	for index, result := range results {
		base := fmt.Sprintf("results[%d]", index)
		nodeID := strings.TrimSpace(result.NodeID)
		if !nodeResultNodeIDPattern.MatchString(nodeID) {
			return nil, &ValidationError{
				Field:   base + ".node_id",
				Message: "node_id must match ^[A-Za-z][A-Za-z0-9_-]{0,63}$",
			}
		}
		kind := strings.TrimSpace(result.Kind)
		if !nodeResultKinds[kind] {
			return nil, &ValidationError{
				Field:   base + ".kind",
				Message: "kind must be data, file or log",
			}
		}
		artifactID := strings.TrimSpace(result.ArtifactID)
		if !uuid.Valid(artifactID) {
			return nil, &ValidationError{
				Field:   base + ".artifact_id",
				Message: "artifact_id must be a UUID",
			}
		}
		label := strings.TrimSpace(result.Label)
		if label == "" || len(label) > maxNodeResultLabel || !isPrintable(label) {
			return nil, &ValidationError{
				Field:   base + ".label",
				Message: fmt.Sprintf("label must be printable and between 1 and %d characters", maxNodeResultLabel),
			}
		}
		key := nodeID + "\x00" + kind + "\x00" + artifactID
		if seen[key] {
			return nil, &ValidationError{
				Field:   base + ".artifact_id",
				Message: "the same node/kind/artifact appears more than once in this report",
			}
		}
		seen[key] = true
		normalised = append(normalised, NodeResult{
			NodeID: nodeID, Kind: kind, ArtifactID: artifactID, Label: label,
		})
	}
	return normalised, nil
}

// RecordNodeResults records provenance for artifacts the live attempt already
// uploaded, completed and registered. It returns the number of newly inserted
// rows; an idempotent replay reports zero and is not an error.
func (s *RunArtifactStore) RecordNodeResults(
	ctx context.Context, runID, token string, results []NodeResult,
) (int, error) {
	scope, err := s.resolveScope(ctx, runID, token)
	if err != nil {
		return 0, err
	}
	normalised, err := validateNodeResults(results)
	if err != nil {
		return 0, err
	}

	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return 0, fmt.Errorf("starting the node result transaction: %w", err)
	}
	defer func() { _ = tx.Rollback() }()

	inserted := 0
	for _, result := range normalised {
		var (
			state   string
			purpose string
			deleted sql.NullTime
		)
		err := tx.QueryRowContext(ctx, `
			SELECT a.state, a.purpose, a.deleted_at
			  FROM platform.artifacts AS a
			  JOIN platform.run_artifact_uploads AS u
			    ON u.artifact_id = a.id
			   AND u.run_id = $2::uuid
			   AND u.attempt_token = $3::uuid
			 WHERE a.id = $1::uuid`,
			result.ArtifactID, scope.RunID, scope.Token,
		).Scan(&state, &purpose, &deleted)
		if errors.Is(err, sql.ErrNoRows) {
			return 0, ErrRunArtifactNotFound
		}
		if err != nil {
			return 0, fmt.Errorf("reading the node result artifact: %w", err)
		}
		if deleted.Valid || state == "deleted" {
			return 0, ErrRunArtifactNotFound
		}
		if state != "ready" {
			return 0, ErrArtifactNotReady
		}
		if purpose != nodeResultPurposes[result.Kind] {
			return 0, &ValidationError{
				Field: "results.artifact_id",
				Message: fmt.Sprintf("a %s node result must reference a %s artifact, not %s",
					result.Kind, nodeResultPurposes[result.Kind], purpose),
			}
		}
		row, err := tx.ExecContext(ctx, `
			INSERT INTO platform.run_node_outputs
				(run_id, node_id, kind, artifact_id, label)
			VALUES ($1::uuid, $2, $3, $4::uuid, $5)
			ON CONFLICT (run_id, node_id, kind, artifact_id) DO NOTHING`,
			scope.RunID, result.NodeID, result.Kind, result.ArtifactID, result.Label,
		)
		if err != nil {
			return 0, fmt.Errorf("recording the node result: %w", err)
		}
		affected, err := row.RowsAffected()
		if err != nil {
			return 0, fmt.Errorf("reading the node result row count: %w", err)
		}
		inserted += int(affected)
	}
	if err := tx.Commit(); err != nil {
		return 0, fmt.Errorf("committing the node results: %w", err)
	}
	return inserted, nil
}

// ListNodeResults returns one run's provenance joined to the artifact facts. The
// caller has already applied the owner-only visibility rule.
func (s *RunArtifactStore) ListNodeResults(
	ctx context.Context, runID string,
) ([]RunNodeResultView, error) {
	if s == nil || s.DB == nil {
		return nil, fmt.Errorf("run artifact store is not configured")
	}
	if !uuid.Valid(strings.TrimSpace(runID)) {
		return nil, nil
	}
	rows, err := s.DB.QueryContext(ctx, `
		SELECT o.node_id, o.kind, o.artifact_id::text, o.label,
		       a.media_type, COALESCE(a.size_bytes, 0), COALESCE(a.sha256, '')
		  FROM platform.run_node_outputs AS o
		  JOIN platform.artifacts AS a ON a.id = o.artifact_id
		 WHERE o.run_id = $1::uuid
		   AND a.deleted_at IS NULL
		 ORDER BY o.created_at, o.node_id, o.kind, o.artifact_id`,
		runID,
	)
	if err != nil {
		return nil, fmt.Errorf("listing the node results: %w", err)
	}
	defer rows.Close()
	items := make([]RunNodeResultView, 0, 8)
	for rows.Next() {
		var item RunNodeResultView
		if err := rows.Scan(
			&item.NodeID, &item.Kind, &item.ArtifactID, &item.Label,
			&item.MediaType, &item.SizeBytes, &item.SHA256,
		); err != nil {
			return nil, fmt.Errorf("reading a node result: %w", err)
		}
		items = append(items, item)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("listing the node results: %w", err)
	}
	return items, nil
}
