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

// Get returns one active memory owned by ownerID.
//
// A foreign id, an unknown id, a malformed id and a deleted memory all answer the
// same 404: the endpoint is addressed by id, so any distinguishable answer would
// let a caller enumerate another owner's memory ids. An administrator is not
// exempt (the owner filter is part of the row query, not a post-check).
func (s *Service) Get(ctx context.Context, ownerID, memoryID string) (*Memory, error) {
	if !uuid.Valid(strings.TrimSpace(ownerID)) {
		return nil, failure(401, "unauthenticated", "an authenticated human is required")
	}
	target := strings.TrimSpace(memoryID)
	if !uuid.Valid(target) {
		return nil, failure(404, CodeResourceNotFound, "that memory does not exist")
	}
	var (
		memory    Memory
		candidate sql.NullString
		objectKey string
		sha       sql.NullString
	)
	err := s.DB.QueryRowContext(ctx, `
		SELECT m.id::text, m.owner_id::text, m.application_id::text,
		       m.body_artifact_id::text, m.candidate_id::text, m.revision,
		       m.confirmed_at, m.confirmed_by::text, a.object_key, a.sha256
		  FROM platform.memories AS m
		  JOIN platform.artifacts AS a ON a.id = m.body_artifact_id
		 WHERE m.id = $1::uuid
		   AND m.owner_id = $2::uuid
		   AND m.deleted_at IS NULL`, target, ownerID).Scan(
		&memory.ID, &memory.OwnerID, &memory.ApplicationID,
		&memory.BodyArtifactID, &candidate, &memory.Revision,
		&memory.ConfirmedAt, &memory.ConfirmedBy, &objectKey, &sha,
	)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, failure(404, CodeResourceNotFound, "that memory does not exist")
	}
	if err != nil {
		return nil, fmt.Errorf("reading the memory: %w", err)
	}
	if candidate.Valid {
		memory.CandidateID = candidate.String
	}
	text, err := s.readBody(objectKey, sha.String)
	if err != nil {
		return nil, err
	}
	memory.Text = text
	return &memory, nil
}

// UpdateInput is the contract's updateMemory body plus the required If-Match.
//
// The contract body is exactly {text, confirmed:true}: a memory's application
// scope and owner are facts of the row, not of the request, so an update can
// never move a memory to another application or another human.
type UpdateInput struct {
	MemoryID  string
	Text      string
	Confirmed bool
	IfMatch   string
}

// Update writes a new body for one memory and bumps its revision.
//
// Order is structural and matches the draft PUT (the platform's existing
// optimistic-concurrency precedent): visibility (404) -> If-Match (428/412) ->
// body shape (422) -> the write itself. A caller who cannot see the memory learns
// nothing; a stale writer is told the current revision and the exact ETag to use;
// only then can a body problem be reported.
//
// The new body is a new sealed artifact written in the same transaction as the
// revision bump and the memory-scope cache-epoch bump. The superseded artifact is
// left in place (F07 owns physical purge); the memory row — the recall authority
// — no longer references it, and its revision can never be served as current.
func (s *Service) Update(
	ctx context.Context, ownerID string, input UpdateInput,
) (*Memory, error) {
	if !uuid.Valid(strings.TrimSpace(ownerID)) {
		return nil, failure(401, "unauthenticated", "an authenticated human is required")
	}
	target := strings.TrimSpace(input.MemoryID)
	if !uuid.Valid(target) {
		return nil, failure(404, CodeResourceNotFound, "that memory does not exist")
	}

	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return nil, fmt.Errorf("starting the memory update transaction: %w", err)
	}
	defer func() { _ = tx.Rollback() }()

	memory, err := lockOwnedMemory(ctx, tx, target, ownerID)
	if err != nil {
		return nil, err
	}

	// If-Match is required and must be exactly this server's strong ETag for the
	// revision the caller claims to have seen. Missing is 428 (not a silent
	// last-writer-wins), anything else that is not the current token is 412.
	if strings.TrimSpace(input.IfMatch) == "" {
		return nil, preconditionRequired()
	}
	claimed, ok := catalog.ParseETag(input.IfMatch)
	if !ok || claimed != memory.Revision {
		return nil, preconditionFailed(memory.Revision)
	}

	text, err := validateText("text", input.Text)
	if err != nil {
		return nil, err
	}
	if !input.Confirmed {
		return nil, fieldFailure(422, "validation_failed",
			"confirmed must be true; an updated memory is still a confirmed memory", "confirmed")
	}

	artifactID, sealedKey, err := s.sealText(ctx, tx, ownerID, PurposeMemory, text)
	if err != nil {
		return nil, err
	}
	var revision int64
	err = tx.QueryRowContext(ctx, `
		UPDATE platform.memories
		   SET body_artifact_id = $3::uuid,
		       revision         = revision + 1
		 WHERE id = $1::uuid
		   AND owner_id = $2::uuid
		   AND revision = $4
		   AND deleted_at IS NULL
		 RETURNING revision`,
		target, ownerID, artifactID, claimed,
	).Scan(&revision)
	if errors.Is(err, sql.ErrNoRows) {
		// The row lock already serialized writers, so this can only mean the row
		// was deleted between the lock and the update; either way the caller's
		// precondition no longer holds.
		s.discardObject(sealedKey)
		return nil, failure(404, CodeResourceNotFound, "that memory does not exist")
	}
	if err != nil {
		s.discardObject(sealedKey)
		return nil, fmt.Errorf("updating the memory: %w", err)
	}
	if err := bumpMemoryEpoch(ctx, tx, ownerID, memory.ApplicationID); err != nil {
		s.discardObject(sealedKey)
		return nil, err
	}
	if err := tx.Commit(); err != nil {
		s.discardObject(sealedKey)
		return nil, fmt.Errorf("committing the memory update: %w", err)
	}

	memory.BodyArtifactID = artifactID
	memory.Revision = revision
	memory.Text = text
	return memory, nil
}

// lockOwnedMemory reads and locks one active memory owned by ownerID. A foreign,
// unknown, malformed or deleted row is ErrMemoryNotVisible (404): the owner
// filter is in the same query as the id, so a foreign row can never be locked and
// the caller cannot learn that it exists.
func lockOwnedMemory(ctx context.Context, tx *sql.Tx, memoryID, ownerID string) (*Memory, error) {
	var (
		memory    Memory
		candidate sql.NullString
	)
	err := tx.QueryRowContext(ctx, `
		SELECT id::text, owner_id::text, application_id::text, body_artifact_id::text,
		       candidate_id::text, revision, confirmed_at, confirmed_by::text
		  FROM platform.memories
		 WHERE id = $1::uuid
		   AND owner_id = $2::uuid
		   AND deleted_at IS NULL
		 FOR UPDATE`,
		memoryID, ownerID,
	).Scan(
		&memory.ID, &memory.OwnerID, &memory.ApplicationID, &memory.BodyArtifactID,
		&candidate, &memory.Revision, &memory.ConfirmedAt, &memory.ConfirmedBy,
	)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, failure(404, CodeResourceNotFound, "that memory does not exist")
	}
	if err != nil {
		return nil, fmt.Errorf("reading the memory: %w", err)
	}
	if candidate.Valid {
		memory.CandidateID = candidate.String
	}
	return &memory, nil
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
