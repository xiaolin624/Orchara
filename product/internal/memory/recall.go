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
//
// Recency alone is not a completeness rule. A user negation ("禁止外发") may be
// older than the newest `RecallRowLimit` rows, and dropping it before the context
// builder's protection detection runs would let the model act as if the user
// never stated the constraint. Recall is therefore protection-aware:
//
//  1. the newest `RecallRowLimit` rows are selected exactly as before (the
//     ordinary recall read, whose recency order is the relevance proxy);
//  2. older rows of exactly the same owner+application scope are then scanned in
//     `RecallScanBatch`-row keyset batches, up to `RecallScanRowLimit` rows, and
//     every row carrying a protection marker joins the read. The vocabulary is
//     the builder's own `IsProtectionSensitive`, not a second marker list;
//  3. if the scan stops at `RecallScanRowLimit` with the scope not exhausted, the
//     read reports `Scan.Truncated`; the context trace records
//     `memory_scan_truncated` with the scanned/limit counts. The bound is a hard
//     limit on purpose, and reaching it is visible, never silent.
//
// The scan also fails closed on an unreadable older body: a row that cannot be
// inspected might be the protected negation the scan exists to find, so skipping
// it would be the same silent loss in a different disguise.

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

const (
	// RecallRowLimit bounds one ordinary recall read. Personal memory is
	// user-visible prose (at most 4000 characters per row), and the context
	// builder applies its own budget afterwards; the limit exists so a
	// pathological account cannot make one agent turn read an unbounded result
	// set.
	RecallRowLimit = 200
	// RecallScanBatch is how many older rows one protection-scan query reads.
	RecallScanBatch = 200
	// RecallScanBatches is how many older-row batches one recall may scan. The
	// two constants document the hard bound together: 10 batches x 200 rows.
	RecallScanBatches = 10
	// RecallScanRowLimit is the hard bound of the older-row protection scan: at
	// most this many rows beyond the newest `RecallRowLimit` are examined per
	// recall. Reaching it without exhausting the scope is reported, not ignored.
	RecallScanRowLimit = RecallScanBatch * RecallScanBatches
)

// The newest-page read and the older keyset page share one row shape and one
// ordering `(confirmed_at DESC, id DESC)`, so the boundary of the newest page is
// exactly the first row the older scan must not repeat.
const (
	recallSelectColumns = `
		SELECT m.id::text, m.owner_id::text, m.application_id::text, m.revision,
		       m.confirmed_at, COALESCE(m.candidate_id::text, ''),
		       COALESCE(c.source_run_id::text, ''), a.object_key,
		       COALESCE(a.sha256, '')`
	recallFromTables = `
		  FROM platform.memories AS m
		  JOIN platform.artifacts AS a ON a.id = m.body_artifact_id
		  LEFT JOIN platform.memory_candidates AS c ON c.id = m.candidate_id`
	recallScopePredicate = `
		 WHERE m.owner_id = $1::uuid
		   AND m.application_id = $2::uuid
		   AND m.deleted_at IS NULL
		   AND a.state = 'ready'
		   AND a.deleted_at IS NULL`
	recallOrder = `
		 ORDER BY m.confirmed_at DESC, m.id DESC`

	recallNewestQuery = recallSelectColumns + recallFromTables + recallScopePredicate +
		recallOrder + `
		 LIMIT $3`
	recallOlderQuery = recallSelectColumns + recallFromTables + recallScopePredicate + `
		   AND (m.confirmed_at, m.id) < ($3::timestamptz, $4::uuid)` +
		recallOrder + `
		 LIMIT $5`
)

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

// recallRow is one row read from PostgreSQL before its sealed body is loaded.
// The body is not carried in the SQL result: the object is the content authority,
// and the row's sha256 is what verifies it.
type recallRow struct {
	memory    agentcontext.Memory
	objectKey string
	digest    string
}

// ConfirmedMemories returns the current confirmed memories of exactly one human
// owner and one application: the newest `RecallRowLimit` rows plus any older row
// of the same scope whose body carries a protection marker. The result is not the
// final model selection: the context builder owns the token budget and the
// truncation order.
func (s *RecallSource) ConfirmedMemories(
	ctx context.Context, ownerID, applicationID string,
) (agentcontext.MemoryRead, error) {
	owner := strings.TrimSpace(ownerID)
	application := strings.TrimSpace(applicationID)
	if !uuid.Valid(owner) || !uuid.Valid(application) {
		// An empty scope is not "everything": a mis-wired caller must fail
		// closed rather than read the wrong user's personal memory.
		return agentcontext.MemoryRead{}, errors.New(
			"memory recall requires this run's owner_id and application_id")
	}
	if s == nil || s.DB == nil {
		return agentcontext.MemoryRead{}, errors.New("the memory store is not configured")
	}
	newest, err := s.queryRows(ctx, recallNewestQuery, owner, application, RecallRowLimit)
	if err != nil {
		return agentcontext.MemoryRead{}, err
	}
	if err := loadRecallBodies(s.Objects, newest); err != nil {
		return agentcontext.MemoryRead{}, err
	}
	read := agentcontext.MemoryRead{
		Rows: memoriesOf(newest),
		Scan: agentcontext.MemoryScan{Limit: RecallScanRowLimit},
	}
	if len(newest) < RecallRowLimit {
		// Fewer rows than the recency limit came back, so the scope is
		// exhausted: there is no older row to protect-scan and nothing to hide.
		return read, nil
	}
	boundary := newest[len(newest)-1].memory
	exhausted := false
	for read.Scan.Scanned < RecallScanRowLimit {
		batch := RecallScanBatch
		if remaining := RecallScanRowLimit - read.Scan.Scanned; remaining < batch {
			batch = remaining
		}
		older, queryErr := s.queryRows(
			ctx, recallOlderQuery, owner, application, boundary.ConfirmedAt, boundary.ID, batch)
		if queryErr != nil {
			return agentcontext.MemoryRead{}, queryErr
		}
		if len(older) == 0 {
			exhausted = true
			break
		}
		read.Scan.Scanned += len(older)
		// A body that cannot be read might be the protected row this scan
		// exists to find; skipping it would be a silent loss, so fail closed.
		if bodyErr := loadRecallBodies(s.Objects, older); bodyErr != nil {
			return agentcontext.MemoryRead{}, bodyErr
		}
		for _, row := range older {
			if !agentcontext.IsProtectionSensitive(row.memory.Text) {
				continue
			}
			read.Scan.Protected++
			read.Rows = append(read.Rows, row.memory)
		}
		if len(older) < batch {
			exhausted = true
			break
		}
		boundary = older[len(older)-1].memory
	}
	read.Scan.Truncated = !exhausted
	return read, nil
}

// queryRows runs one of the two recall queries and scans its rows in order. The
// SQL is a package constant so the newest page and the older keyset page can
// never drift in columns or ordering.
func (s *RecallSource) queryRows(
	ctx context.Context, query string, args ...any,
) ([]recallRow, error) {
	rows, err := s.DB.QueryContext(ctx, query, args...)
	if err != nil {
		return nil, fmt.Errorf("reading the recalled memories: %w", err)
	}
	defer func() { _ = rows.Close() }()

	items := make([]recallRow, 0, 8)
	for rows.Next() {
		var row recallRow
		if err := rows.Scan(
			&row.memory.ID, &row.memory.OwnerID, &row.memory.ApplicationID,
			&row.memory.Revision, &row.memory.ConfirmedAt, &row.memory.CandidateID,
			&row.memory.SourceRunID, &row.objectKey, &row.digest,
		); err != nil {
			return nil, fmt.Errorf("scanning a recalled memory: %w", err)
		}
		row.memory.State = "active"
		row.memory.Confirmed = true
		items = append(items, row)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("reading the recalled memories: %w", err)
	}
	return items, nil
}

// loadRecallBodies reads and verifies every sealed body of the batch. One
// unreadable body fails the whole recall closed.
func loadRecallBodies(objects *objectstore.Store, rows []recallRow) error {
	for index := range rows {
		text, err := readSealedBody(objects, rows[index].objectKey, rows[index].digest)
		if err != nil {
			return fmt.Errorf("reading recalled memory %s: %w", rows[index].memory.ID, err)
		}
		rows[index].memory.Text = text
	}
	return nil
}

// memoriesOf strips the row metadata that only the body read needs.
func memoriesOf(rows []recallRow) []agentcontext.Memory {
	items := make([]agentcontext.Memory, 0, len(rows))
	for _, row := range rows {
		items = append(items, row.memory)
	}
	return items
}
