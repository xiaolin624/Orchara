package memory

// F09/T054: the confirmed personal-memory store and its owner read model.
//
// createMemory is the explicit "remember this" path: the human is the confirmer,
// so the memory is written immediately, without a candidate. The candidate
// confirm path in candidates.go converges on the same `platform.memories` row
// shape, so the list/recall side (T063) has exactly one source.
//
// listMemories is the real read model that replaces F01's honest empty list. It
// is owner-only: the query filters `owner_id` in the same WHERE as the cursor and
// LIMIT, so a foreign or deleted row can never consume a page slot. An
// administrator is not exempt — an administrator who does not own the memory sees
// an empty list here and 404 on every single-memory path in T058.

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"strings"

	"orchara/platform/internal/catalog"
	"orchara/platform/internal/uuid"
)

// CreateInput is the contract's MemoryInput (confirmed is required and must be
// the constant true; the zero value is rejected rather than accepted silently).
type CreateInput struct {
	ApplicationID string
	Text          string
	Confirmed     bool
}

// Create writes one memory directly (the explicit "remember" path).
func (s *Service) Create(ctx context.Context, ownerID string, input CreateInput) (*Memory, error) {
	if !uuid.Valid(strings.TrimSpace(ownerID)) {
		return nil, failure(401, "unauthenticated", "an authenticated human is required")
	}
	applicationID := strings.TrimSpace(input.ApplicationID)
	if !uuid.Valid(applicationID) {
		return nil, fieldFailure(422, "validation_failed",
			"application_id must be a UUID", "application_id")
	}
	text, err := validateText("text", input.Text)
	if err != nil {
		return nil, err
	}
	if !input.Confirmed {
		return nil, fieldFailure(422, "validation_failed",
			"confirmed must be true; a memory is only saved after the user confirms it", "confirmed")
	}
	// The application scope must be one the caller can actually see. This reuses
	// the F01 ACL (owner or an unexpired read grant); an unknown, archived or
	// foreign application is the same 404 so the endpoint cannot enumerate ids.
	if s.Applications == nil {
		return nil, failure(404, CodeResourceNotFound, "that application does not exist")
	}
	if _, err := s.Applications.GetVisible(ctx, ownerID, applicationID); err != nil {
		if errors.Is(err, catalog.ErrNotVisible) {
			return nil, failure(404, CodeResourceNotFound, "that application does not exist")
		}
		return nil, fmt.Errorf("checking the application scope: %w", err)
	}

	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return nil, fmt.Errorf("starting the memory transaction: %w", err)
	}
	defer func() { _ = tx.Rollback() }()

	artifactID, sealedKey, err := s.sealText(ctx, tx, ownerID, PurposeMemory, text)
	if err != nil {
		return nil, err
	}
	memory := &Memory{
		ID:             uuid.NewString(),
		OwnerID:        ownerID,
		ApplicationID:  applicationID,
		BodyArtifactID: artifactID,
		Revision:       1,
		ConfirmedBy:    ownerID,
		Text:           text,
	}
	if err := tx.QueryRowContext(ctx, `
		INSERT INTO platform.memories
		       (id, owner_id, application_id, body_artifact_id, candidate_id,
		        revision, confirmed_at, confirmed_by)
		VALUES ($1::uuid, $2::uuid, $3::uuid, $4::uuid, NULL, 1, now(), $2::uuid)
		RETURNING confirmed_at`,
		memory.ID, ownerID, applicationID, artifactID,
	).Scan(&memory.ConfirmedAt); err != nil {
		s.discardObject(sealedKey)
		return nil, fmt.Errorf("writing the memory: %w", err)
	}
	if err := bumpMemoryEpoch(ctx, tx, ownerID, applicationID); err != nil {
		s.discardObject(sealedKey)
		return nil, err
	}
	if err := tx.Commit(); err != nil {
		s.discardObject(sealedKey)
		return nil, fmt.Errorf("committing the memory: %w", err)
	}
	return memory, nil
}

// List returns the owner's active memories, newest first.
func (s *Service) List(
	ctx context.Context, ownerID string, limit int, cursor, applicationID string,
) ([]Memory, *string, error) {
	if !uuid.Valid(strings.TrimSpace(ownerID)) {
		return nil, nil, failure(401, "unauthenticated", "an authenticated human is required")
	}
	limit = normaliseLimit(limit)
	scope, position, err := decodeListInput(applicationID, cursor)
	if err != nil {
		return nil, nil, err
	}
	rows, err := s.DB.QueryContext(ctx, `
		SELECT m.id::text, m.owner_id::text, m.application_id::text,
		       m.body_artifact_id::text, m.candidate_id::text, m.revision,
		       m.confirmed_at, m.confirmed_by::text, a.object_key, a.sha256
		  FROM platform.memories AS m
		  JOIN platform.artifacts AS a ON a.id = m.body_artifact_id
		 WHERE m.owner_id = $1::uuid
		   AND m.deleted_at IS NULL
		   AND ($2::uuid IS NULL OR m.application_id = $2::uuid)
		   AND (
		        $3::timestamptz IS NULL
		        OR (m.confirmed_at, m.id) < ($3::timestamptz, $4::uuid)
		       )
		 ORDER BY m.confirmed_at DESC, m.id DESC
		 LIMIT $5`, ownerID, nullableUUID(scope), nullableTime(position), cursorID(position), limit+1)
	if err != nil {
		return nil, nil, fmt.Errorf("listing memories: %w", err)
	}
	defer func() { _ = rows.Close() }()

	items := make([]Memory, 0, limit+1)
	keys := make([]string, 0, limit+1)
	digests := make([]string, 0, limit+1)
	for rows.Next() {
		var (
			memory    Memory
			candidate sql.NullString
			objectKey string
			sha       sql.NullString
		)
		if err := rows.Scan(
			&memory.ID, &memory.OwnerID, &memory.ApplicationID,
			&memory.BodyArtifactID, &candidate, &memory.Revision,
			&memory.ConfirmedAt, &memory.ConfirmedBy, &objectKey, &sha,
		); err != nil {
			return nil, nil, fmt.Errorf("scanning the memory list: %w", err)
		}
		if candidate.Valid {
			memory.CandidateID = candidate.String
		}
		items = append(items, memory)
		keys = append(keys, objectKey)
		digests = append(digests, sha.String)
	}
	if err := rows.Err(); err != nil {
		return nil, nil, fmt.Errorf("listing memories: %w", err)
	}
	hasMore := len(items) > limit
	if hasMore {
		items = items[:limit]
		keys = keys[:limit]
		digests = digests[:limit]
	}
	for index := range items {
		text, err := s.readBody(keys[index], digests[index])
		if err != nil {
			return nil, nil, err
		}
		items[index].Text = text
	}
	var next *string
	if hasMore && len(items) > 0 {
		last := items[len(items)-1]
		next = nextCursor(last.ConfirmedAt, last.ID, true)
	}
	return items, next, nil
}
