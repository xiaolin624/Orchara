package memory

// F09/T063: runtime recall of the current application's confirmed memories.
//
// Recall reads PostgreSQL directly (never a cache): the authoritative row is
// `platform.memories`, and the authoritative body is the sealed artifact its
// `body_artifact_id` currently names. The query is one owner+application scope
// and never leaves it:
//
//   - `owner_id`/`application_id` are in the same WHERE as the LIMIT, so another
//     user's or another application's row can never consume a recall slot;
//   - `deleted_at IS NULL` is the T058 tombstone: a deleted memory stops being
//     recalled immediately, without waiting for F07's physical purge;
//   - `body_artifact_id` is the current revision's body. The revision is reported
//     with the row (T058's strong-ETag authority) so a later retrieval cache
//     (T067) can key entries by `(memory_id, memory_revision)` and re-check the
//     current revision before serving;
//   - no candidate is ever a recall source. A pending or expired candidate has no
//     `platform.memories` row at all, and a confirmed one is read only through the
//     memory row it created.
//
// "Not expired" needs no column here: T054 created memory rows only after a human
// decision, and a memory has no deadline. Expiry is a candidate lifecycle fact,
// so only candidates carry `expires_at`.
//
// The body is read and digest-verified before a row is returned. One unreadable
// body fails the whole recall closed: the context trace then reports
// `memory_state=unavailable` and injects nothing. A partial memory view would be
// indistinguishable from "the user never told me that", which is the failure this
// ticket exists to prevent.

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"strings"

	agentcontext "orchara/platform/internal/context"
	"orchara/platform/internal/objectstore"
	"orchara/platform/internal/uuid"
)

// RecallRowLimit bounds one recall read. Personal memory is user-visible prose
// (at most 4000 characters per row), and the context builder applies its own
// budget afterwards; the limit exists so a pathological account cannot make one
// agent turn read an unbounded result set.
const RecallRowLimit = 200

// RecallSource is the production F09 MemorySource. It reads the current row and
// sealed body from PostgreSQL/the object store on every call (no cache), which is
// why the `policy_epochs(memory:<owner>:<application>)` invalidation seam cannot
// be skipped: there is nothing stale to invalidate.
type RecallSource struct {
	DB      *sql.DB
	Objects *objectstore.Store
}

// NewRecallSource builds the recall source. A nil object store is "no bytes
// readable here": rows are found but every recall fails closed with a located
// error, which the builder reports as unavailable.
func NewRecallSource(db *sql.DB, objects *objectstore.Store) *RecallSource {
	return &RecallSource{DB: db, Objects: objects}
}

// ConfirmedMemories returns the current confirmed memories of exactly one human
// owner and one application, newest first. The result is not the final model
// selection: the context builder owns the token budget and the truncation order.
func (s *RecallSource) ConfirmedMemories(
	ctx context.Context, ownerID, applicationID string,
) ([]agentcontext.Memory, error) {
	owner := strings.TrimSpace(ownerID)
	application := strings.TrimSpace(applicationID)
	if !uuid.Valid(owner) || !uuid.Valid(application) {
		// An empty scope is not "everything": a mis-wired caller must fail
		// closed rather than read the wrong user's personal memory.
		return nil, errors.New(
			"memory recall requires this run's owner_id and application_id")
	}
	if s == nil || s.DB == nil {
		return nil, errors.New("the memory store is not configured")
	}
	rows, err := s.DB.QueryContext(ctx, `
		SELECT m.id::text, m.owner_id::text, m.application_id::text, m.revision,
		       m.confirmed_at, COALESCE(m.candidate_id::text, ''),
		       COALESCE(c.source_run_id::text, ''), a.object_key,
		       COALESCE(a.sha256, '')
		  FROM platform.memories AS m
		  JOIN platform.artifacts AS a ON a.id = m.body_artifact_id
		  LEFT JOIN platform.memory_candidates AS c ON c.id = m.candidate_id
		 WHERE m.owner_id = $1::uuid
		   AND m.application_id = $2::uuid
		   AND m.deleted_at IS NULL
		   AND a.state = 'ready'
		   AND a.deleted_at IS NULL
		 ORDER BY m.confirmed_at DESC, m.id DESC
		 LIMIT $3`, owner, application, RecallRowLimit)
	if err != nil {
		return nil, fmt.Errorf("reading the recalled memories: %w", err)
	}
	defer func() { _ = rows.Close() }()

	items := make([]agentcontext.Memory, 0, 8)
	keys := make([]string, 0, 8)
	digests := make([]string, 0, 8)
	for rows.Next() {
		var (
			memory    agentcontext.Memory
			sha       string
			objectKey string
		)
		if err := rows.Scan(
			&memory.ID, &memory.OwnerID, &memory.ApplicationID, &memory.Revision,
			&memory.ConfirmedAt, &memory.CandidateID, &memory.SourceRunID,
			&objectKey, &sha,
		); err != nil {
			return nil, fmt.Errorf("scanning a recalled memory: %w", err)
		}
		memory.State = "active"
		memory.Confirmed = true
		items = append(items, memory)
		keys = append(keys, objectKey)
		digests = append(digests, sha)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("reading the recalled memories: %w", err)
	}
	for index := range items {
		text, readErr := readSealedBody(s.Objects, keys[index], digests[index])
		if readErr != nil {
			return nil, fmt.Errorf("reading recalled memory %s: %w", items[index].ID, readErr)
		}
		items[index].Text = text
	}
	return items, nil
}
