package catalog

// F05/T033: the owner-only run artifact list.
//
//	GET /api/v1/runs/{run_id}/artifacts -> {items: Artifact[], next_cursor}
//
// The list is the Run owner's view of what the run produced. It shares getRun's
// exact visibility rule (owner only, administrators included; invisible == 404),
// the shared opaque (created_at, id) DESC cursor, and the contract's four-field
// Artifact shape, so it cannot leak an object key, owner id or provenance token.
//
// Decisions recorded in the T033 report:
//   - `log` purpose is deliberately NOT listed. WORKFLOW-SPEC §5 says
//     stdout/stderr is a capped log, not an output; mixing it into the run's data
//     artifacts would invite treating diagnostics as results. A log is still
//     reachable by its owner through GET /api/v1/artifacts/{id} when the run
//     detail names it (the sandbox panel does).
//   - Input artifacts (relation=input) are not listed either: they are the
//     caller's uploads and already visible through the attachment/artifact paths,
//     while this list answers "what did the run produce".

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"time"

	"orchara/platform/internal/uuid"
)

// Artifact is exactly the contract's Artifact schema (four fields,
// additionalProperties:false).
type Artifact struct {
	ID        string `json:"id"`
	State     string `json:"state"`
	MediaType string `json:"media_type"`
	SizeBytes int64  `json:"size_bytes"`
}

// ListRunArtifacts returns the run's produced artifacts, newest first.
func (r *Reader) ListRunArtifacts(
	ctx context.Context,
	ownerID, runID, cursor string,
	limit int,
) ([]Artifact, *string, error) {
	if !uuid.Valid(strings.TrimSpace(ownerID)) || !uuid.Valid(strings.TrimSpace(runID)) {
		return nil, nil, ErrNotVisible
	}
	if limit == 0 {
		limit = DefaultLimit
	}
	if limit < MinLimit || limit > MaxLimit {
		return nil, nil, ErrInvalidLimit
	}
	// getRun's visibility rule first, so a foreign/unknown run is the same 404
	// whether or not it has artifacts (no empty-list enumeration).
	if _, err := r.GetRun(ctx, ownerID, runID); err != nil {
		if errors.Is(err, ErrNotVisible) {
			return nil, nil, ErrNotVisible
		}
		return nil, nil, err
	}
	var (
		cursorAt any
		cursorID any
	)
	if strings.TrimSpace(cursor) != "" {
		at, id, err := DecodeCursor(cursor)
		if err != nil {
			return nil, nil, err
		}
		cursorAt, cursorID = at, id
	}

	type row struct {
		artifact  Artifact
		createdAt time.Time
	}
	rows, err := r.DB.QueryContext(ctx, `
		SELECT a.id::text, a.state, a.media_type, COALESCE(a.size_bytes, 0), a.created_at
		  FROM platform.run_artifacts AS ra
		  JOIN platform.artifacts AS a ON a.id = ra.artifact_id
		 WHERE ra.run_id = $1::uuid
		   AND ra.relation <> 'input'
		   AND a.deleted_at IS NULL
		   AND a.state <> 'deleted'
		   AND a.purpose <> 'log'
		   AND (
		        $2::timestamptz IS NULL
		        OR (a.created_at, a.id) < ($2::timestamptz, $3::uuid)
		       )
		 ORDER BY a.created_at DESC, a.id DESC
		 LIMIT $4`,
		runID, cursorAt, cursorID, limit+1)
	if err != nil {
		return nil, nil, fmt.Errorf("listing the run artifacts: %w", err)
	}
	defer rows.Close()

	entries := make([]row, 0, limit+1)
	for rows.Next() {
		var entry row
		if err := rows.Scan(
			&entry.artifact.ID, &entry.artifact.State, &entry.artifact.MediaType,
			&entry.artifact.SizeBytes, &entry.createdAt,
		); err != nil {
			return nil, nil, fmt.Errorf("listing the run artifacts: %w", err)
		}
		entries = append(entries, entry)
	}
	if err := rows.Err(); err != nil {
		return nil, nil, fmt.Errorf("listing the run artifacts: %w", err)
	}

	var next *string
	if len(entries) > limit {
		entries = entries[:limit]
		last := entries[len(entries)-1]
		value := EncodeCursor(last.createdAt, last.artifact.ID)
		next = &value
	}
	items := make([]Artifact, 0, len(entries))
	for _, entry := range entries {
		items = append(items, entry.artifact)
	}
	return items, next, nil
}
