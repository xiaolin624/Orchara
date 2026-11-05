package requests

import (
	"context"
	"database/sql"
	"encoding/base64"
	"errors"
	"fmt"
	"strings"
	"time"

	"orchara/platform/internal/audit"
	"orchara/platform/internal/catalog"
	"orchara/platform/internal/uuid"
)

// ErrInvalidCursor is a malformed continuation token on a conversation or
// message list. It maps to 400 invalid_cursor, like every other list.
var ErrInvalidCursor = errors.New("invalid cursor")

// CreateConversation creates one empty conversation for the caller. The contract
// declares no body and no Idempotency-Key: two POSTs are two conversations, which
// is why the page disables the button while a create is in flight (documented in
// the T074 report).
func (s *Service) CreateConversation(ctx context.Context, callerID string) (*Conversation, error) {
	owner, ok := requireUUID(callerID)
	if !ok {
		return nil, failure(401, "unauthenticated", "an authenticated human is required")
	}
	id := uuid.NewString()
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return nil, fmt.Errorf("starting the conversation transaction: %w", err)
	}
	defer func() { _ = tx.Rollback() }()
	var conversation Conversation
	if err := tx.QueryRowContext(ctx, `
		INSERT INTO platform.conversations (id, owner_id, revision)
		VALUES ($1::uuid, $2::uuid, 1)
		RETURNING id::text, created_at, revision`,
		id, owner,
	).Scan(&conversation.ID, &conversation.CreatedAt, &conversation.Revision); err != nil {
		return nil, fmt.Errorf("creating the conversation: %w", err)
	}
	if err := audit.Record(ctx, tx, &owner, "conversation.created", "conversation", &id, nil); err != nil {
		return nil, err
	}
	if err := tx.Commit(); err != nil {
		return nil, fmt.Errorf("committing the conversation: %w", err)
	}
	return &conversation, nil
}

// ListConversations returns one page of the caller's live conversations, newest
// first. A tombstoned conversation is absent immediately, before any physical
// cleanup runs.
func (s *Service) ListConversations(
	ctx context.Context, callerID, cursor string, limit int,
) ([]Conversation, *string, error) {
	owner, ok := requireUUID(callerID)
	if !ok {
		return nil, nil, failure(401, "unauthenticated", "an authenticated human is required")
	}
	if limit == 0 {
		limit = catalog.DefaultLimit
	}
	if limit < catalog.MinLimit || limit > catalog.MaxLimit {
		return nil, nil, failureWithDetails(422, "validation_failed",
			"limit must be an integer between 1 and 100", map[string]any{"field": "limit"})
	}
	var (
		cursorAt any
		cursorID any
	)
	if strings.TrimSpace(cursor) != "" {
		at, id, err := catalog.DecodeCursor(cursor)
		if err != nil {
			return nil, nil, ErrInvalidCursor
		}
		cursorAt, cursorID = at, id
	}
	rows, err := s.DB.QueryContext(ctx, `
		SELECT id::text, created_at, revision
		  FROM platform.conversations
		 WHERE owner_id = $1::uuid
		   AND deleted_at IS NULL
		   AND (
		        $2::timestamptz IS NULL
		        OR (created_at, id) < ($2::timestamptz, $3::uuid)
		       )
		 ORDER BY created_at DESC, id DESC
		 LIMIT $4`,
		owner, cursorAt, cursorID, limit+1,
	)
	if err != nil {
		return nil, nil, fmt.Errorf("listing conversations: %w", err)
	}
	defer rows.Close()

	items := make([]Conversation, 0, limit+1)
	for rows.Next() {
		var item Conversation
		if err := rows.Scan(&item.ID, &item.CreatedAt, &item.Revision); err != nil {
			return nil, nil, fmt.Errorf("listing conversations: %w", err)
		}
		items = append(items, item)
	}
	if err := rows.Err(); err != nil {
		return nil, nil, fmt.Errorf("listing conversations: %w", err)
	}
	var next *string
	if len(items) > limit {
		last := items[limit-1]
		token := catalog.EncodeCursor(last.CreatedAt, last.ID)
		next = &token
		items = items[:limit]
	}
	return items, next, nil
}

// GetConversation returns one live conversation to its owner. Missing, foreign,
// malformed and tombstoned are the same 404.
func (s *Service) GetConversation(ctx context.Context, callerID, conversationID string) (*Conversation, error) {
	owner, ok := requireUUID(callerID)
	if !ok {
		return nil, failure(401, "unauthenticated", "an authenticated human is required")
	}
	id, ok := requireUUID(conversationID)
	if !ok {
		return nil, conversationNotFound()
	}
	var conversation Conversation
	err := s.DB.QueryRowContext(ctx, `
		SELECT id::text, created_at, revision
		  FROM platform.conversations
		 WHERE id = $1::uuid AND owner_id = $2::uuid AND deleted_at IS NULL`,
		id, owner,
	).Scan(&conversation.ID, &conversation.CreatedAt, &conversation.Revision)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, conversationNotFound()
	}
	if err != nil {
		return nil, fmt.Errorf("reading the conversation: %w", err)
	}
	return &conversation, nil
}

// conversationNotFound is the one located 404 for every conversation/request id
// the caller may not see: unknown, malformed, foreign or tombstoned.
func conversationNotFound() *Error {
	return failure(404, "resource_not_found", "that conversation does not exist")
}

func requestNotFound() *Error {
	return failure(404, "resource_not_found", "that request does not exist")
}

// conversationAliveTx locks one conversation row for the caller. Every mutating
// request operation starts here, so the slot decision and the tombstone cannot
// interleave. A deleted conversation is absent (404), never "read-only".
func (s *Service) conversationAliveTx(
	ctx context.Context, tx *sql.Tx, callerID, conversationID string,
) (*Conversation, error) {
	var conversation Conversation
	err := tx.QueryRowContext(ctx, `
		SELECT id::text, created_at, revision
		  FROM platform.conversations
		 WHERE id = $1::uuid AND owner_id = $2::uuid AND deleted_at IS NULL
		 FOR UPDATE`,
		conversationID, callerID,
	).Scan(&conversation.ID, &conversation.CreatedAt, &conversation.Revision)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, conversationNotFound()
	}
	if err != nil {
		return nil, fmt.Errorf("locking the conversation: %w", err)
	}
	return &conversation, nil
}

// conversationAlive reports whether a live conversation exists for the owner;
// the read paths use it so a request read after deletion is the same 404.
func (s *Service) conversationAlive(ctx context.Context, callerID, conversationID string) (bool, error) {
	var exists bool
	err := s.DB.QueryRowContext(ctx, `
		SELECT EXISTS (
		    SELECT 1 FROM platform.conversations
		     WHERE id = $1::uuid AND owner_id = $2::uuid AND deleted_at IS NULL
		)`, conversationID, callerID).Scan(&exists)
	if err != nil {
		return false, fmt.Errorf("reading the conversation: %w", err)
	}
	return exists, nil
}

// ---------------------------------------------------------------------------
// message projection (contract listMessages)
// ---------------------------------------------------------------------------

const messageColumns = `id, role, request_id::text, run_id::text, body_artifact_id::text, created_at, kind`

// ListMessages projects the user-visible timeline of one conversation. It is a
// projection over the authoritative request/run/interaction rows, never a second
// body copy (IMPLEMENTATION-DETAILS.md §71): a request message points at the sealed
// request input artifact, a result message at the Run's output artifact, a status
// message at no body, and a pending interaction at its sealed proposal artifact.
func (s *Service) ListMessages(
	ctx context.Context, callerID, conversationID, cursor string, limit int,
) ([]MessageView, *string, error) {
	owner, ok := requireUUID(callerID)
	if !ok {
		return nil, nil, failure(401, "unauthenticated", "an authenticated human is required")
	}
	conversation, ok := requireUUID(conversationID)
	if !ok {
		return nil, nil, conversationNotFound()
	}
	alive, err := s.conversationAlive(ctx, owner, conversation)
	if err != nil {
		return nil, nil, err
	}
	if !alive {
		return nil, nil, conversationNotFound()
	}
	if limit == 0 {
		limit = catalog.DefaultLimit
	}
	if limit < catalog.MinLimit || limit > catalog.MaxLimit {
		return nil, nil, failureWithDetails(422, "validation_failed",
			"limit must be an integer between 1 and 100", map[string]any{"field": "limit"})
	}
	var (
		cursorAt any
		cursorID any
	)
	if strings.TrimSpace(cursor) != "" {
		at, id, err := decodeMessageCursor(cursor)
		if err != nil {
			return nil, nil, ErrInvalidCursor
		}
		cursorAt, cursorID = at, id
	}
	rows, err := s.DB.QueryContext(ctx, `
		SELECT `+messageColumns+`
		  FROM (
		    SELECT 'request:' || r.id::text AS id, 'user' AS role, r.id AS request_id,
		           u.id AS run_id, r.input_artifact_id AS body_artifact_id,
		           r.created_at AS created_at, 'request' AS kind
		      FROM platform.requests AS r
		      LEFT JOIN platform.runs AS u ON u.request_id = r.id AND u.deleted_at IS NULL
		     WHERE r.conversation_id = $1::uuid
		    UNION ALL
		    SELECT 'result:' || u.id::text, 'assistant', r.id, u.id, u.output_artifact_id,
		           COALESCE(u.finished_at, u.created_at), 'result'
		      FROM platform.runs AS u
		      JOIN platform.requests AS r ON r.id = u.request_id
		     WHERE r.conversation_id = $1::uuid
		       AND u.output_artifact_id IS NOT NULL
		       AND u.finished_at IS NOT NULL
		    UNION ALL
		    SELECT 'status:' || r.id::text, 'system', r.id, u.id, NULL,
		           r.updated_at, 'status'
		      FROM platform.requests AS r
		      LEFT JOIN platform.runs AS u ON u.request_id = r.id AND u.deleted_at IS NULL
		     WHERE r.conversation_id = $1::uuid
		       AND r.state IN ('cancelled', 'failed')
		    UNION ALL
		    SELECT 'interaction:' || i.id::text, 'assistant', r.id, u.id,
		           i.proposal_artifact_id, i.created_at, 'interaction'
		      FROM platform.interactions AS i
		      JOIN platform.requests AS r ON r.id = i.request_id
		      LEFT JOIN platform.runs AS u ON u.request_id = r.id AND u.deleted_at IS NULL
		     WHERE r.conversation_id = $1::uuid AND i.state = 'pending'
		  ) AS m
		 WHERE (
		        $2::timestamptz IS NULL
		        OR (m.created_at, m.id) < ($2::timestamptz, $3::text)
		       )
		 ORDER BY m.created_at DESC, m.id DESC
		 LIMIT $4`,
		conversation, cursorAt, cursorID, limit+1,
	)
	if err != nil {
		return nil, nil, fmt.Errorf("listing conversation messages: %w", err)
	}
	defer rows.Close()

	items := make([]MessageView, 0, limit+1)
	for rows.Next() {
		var (
			item   MessageView
			runID  sql.NullString
			bodyID sql.NullString
		)
		if err := rows.Scan(&item.ID, &item.Role, &item.RequestID, &runID, &bodyID,
			&item.CreatedAt, &item.Kind); err != nil {
			return nil, nil, fmt.Errorf("listing conversation messages: %w", err)
		}
		item.RunID = nullableString(runID)
		item.BodyArtifactID = nullableString(bodyID)
		items = append(items, item)
	}
	if err := rows.Err(); err != nil {
		return nil, nil, fmt.Errorf("listing conversation messages: %w", err)
	}
	var next *string
	if len(items) > limit {
		last := items[limit-1]
		token := encodeMessageCursor(last.CreatedAt, last.ID)
		next = &token
		items = items[:limit]
	}
	return items, next, nil
}

// messageCursorPrefix versions the message keyset token. The id is a composite
// projection id (`request:<uuid>`), not a bare UUID, so the conversation cursor
// encoder cannot be reused.
const messageCursorPrefix = "m1"

func encodeMessageCursor(createdAt time.Time, id string) string {
	payload := messageCursorPrefix + "|" + createdAt.UTC().Format(time.RFC3339Nano) + "|" + id
	return base64.RawURLEncoding.EncodeToString([]byte(payload))
}

func decodeMessageCursor(raw string) (time.Time, string, error) {
	decoded, err := base64.RawURLEncoding.DecodeString(strings.TrimSpace(raw))
	if err != nil {
		return time.Time{}, "", ErrInvalidCursor
	}
	parts := strings.SplitN(string(decoded), "|", 3)
	if len(parts) != 3 || parts[0] != messageCursorPrefix || parts[2] == "" {
		return time.Time{}, "", ErrInvalidCursor
	}
	createdAt, err := time.Parse(time.RFC3339Nano, parts[1])
	if err != nil {
		return time.Time{}, "", ErrInvalidCursor
	}
	return createdAt.UTC(), parts[2], nil
}

func nullableString(value sql.NullString) *string {
	if !value.Valid {
		return nil
	}
	copied := value.String
	return &copied
}
