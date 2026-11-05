package deletion

// F11/T072: the independently retainable, replayable backup deletion ledger.
//
// `platform.deletion_jobs` is the live work queue; it is mutable and it lives in
// the same backup as the deleted content. This file owns the *ledger*: an
// append-only, hash-chained record of the deletion decisions and of every copy
// class outcome, so the restore procedure can replay the decisions before reads
// open (OPERATIONS.md §46, DATA-MODEL.md §62, DATA-POLICY.md §15).
//
// Format contract (shared with `cmd/admin deletion export|replay` and T076):
//
//	entry_hash = sha256_hex( RFC 8785 canonical JSON of {
//	    sequence, deletion_job_id, resource_type, resource_id,
//	    deletion_revision, entry_kind, scope, copy_class, scope_key,
//	    record, prev_sha256 } )
//
// `prev_sha256` is the previous entry's hash (null for sequence 1) and
// `record_sha256` is this entry's hash. Sequence numbers are allocated under a
// transaction-scoped advisory lock and included in the hash, so deleting,
// reordering or editing an exported line is detected by VerifyLedger.
//
// The ledger never carries content: `record` is built by the callers from ids,
// object keys, hashes, sizes, counts, safe error codes and reasons.

import (
	"bufio"
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"sort"
	"strings"
	"time"

	"orchara/platform/internal/jcs"
	"orchara/platform/internal/uuid"
)

// LedgerFormat is the exported file format identifier.
const LedgerFormat = "orchara.deletion-ledger.v1"

// LedgerAdvisoryLockKey serialises ledger appends (and replay against one
// database). The value is arbitrary but must be stable forever; it is not a
// credential and it differs from the migration advisory lock.
const LedgerAdvisoryLockKey int64 = 0x4f52_4348_5f4c_4544 // "ORCH_LED"

// Ledger entry kinds, the migration's closed vocabulary.
const (
	EntryTombstone     = "tombstone"
	EntryScopeCleared  = "scope_cleared"
	EntryScopeAbsent   = "scope_absent"
	EntryScopeFailed   = "scope_failed"
	EntryScopeExternal = "scope_external"
	EntryJobCompleted  = "job_completed"
)

// LedgerEntry is one immutable ledger record. Field values are exactly the
// hashed payload; `RecordSHA256`/`CreatedAt` are the stored/derived facts.
type LedgerEntry struct {
	Sequence         int64          `json:"sequence"`
	DeletionJobID    string         `json:"deletion_job_id"`
	ResourceType     string         `json:"resource_type"`
	ResourceID       string         `json:"resource_id"`
	DeletionRevision int64          `json:"deletion_revision"`
	EntryKind        string         `json:"entry_kind"`
	Scope            *string        `json:"scope"`
	CopyClass        *string        `json:"copy_class"`
	ScopeKey         *string        `json:"scope_key"`
	Record           map[string]any `json:"record"`
	PrevSHA256       *string        `json:"prev_sha256"`
	RecordSHA256     string         `json:"record_sha256"`
	CreatedAt        string         `json:"created_at,omitempty"`
}

// LedgerHeader is the first line of an exported ledger file. It binds the file to
// a count and a head hash so truncation/extension is detected even when the
// remaining entries form a valid prefix.
type LedgerHeader struct {
	Format     string `json:"format"`
	ExportedAt string `json:"exported_at"`
	Database   string `json:"database"`
	Count      int    `json:"count"`
	HeadSHA256 string `json:"head_sha256"`
}

// LedgerAppend describes one entry to append. The sequence and prev hash are
// assigned by AppendLedgerTx under the advisory lock.
type LedgerAppend struct {
	DeletionJobID    string
	ResourceType     string
	ResourceID       string
	DeletionRevision int64
	EntryKind        string
	Scope            *string
	CopyClass        *string
	ScopeKey         *string
	Record           map[string]any
}

// ledgerHashPayload normalises the hashed fields to the types the RFC 8785
// canonicaliser accepts (numbers are float64; integers here are far below 2^53).
func ledgerHashPayload(
	sequence int64,
	jobID, resourceType, resourceID string,
	revision int64,
	kind string,
	scope, copyClass, scopeKey *string,
	record map[string]any,
	prev *string,
) map[string]any {
	recordValue := any(map[string]any{})
	if record != nil {
		recordValue = record
	}
	return map[string]any{
		"sequence":          float64(sequence),
		"deletion_job_id":   jobID,
		"resource_type":     resourceType,
		"resource_id":       resourceID,
		"deletion_revision": float64(revision),
		"entry_kind":        kind,
		"scope":             nullable(scope),
		"copy_class":        nullable(copyClass),
		"scope_key":         nullable(scopeKey),
		"record":            recordValue,
		"prev_sha256":       nullable(prev),
	}
}

func nullable(value *string) any {
	if value == nil {
		return nil
	}
	return *value
}

func ledgerEntryHash(entry *LedgerEntry) (string, error) {
	payload := ledgerHashPayload(
		entry.Sequence, entry.DeletionJobID, entry.ResourceType, entry.ResourceID,
		entry.DeletionRevision, entry.EntryKind, entry.Scope, entry.CopyClass,
		entry.ScopeKey, entry.Record, entry.PrevSHA256,
	)
	canonical, err := jcs.Canonicalize(jsonNative(payload))
	if err != nil {
		return "", fmt.Errorf("canonicalising the ledger entry: %w", err)
	}
	return jcs.SHA256Hex([]byte(canonical)), nil
}

// jsonNative round-trips a value through encoding/json so the RFC 8785
// canonicaliser sees only its supported types (float64, string, bool, nil,
// []any, map[string]any). Without it a []string in a closed evidence record
// would be rejected as an unsupported value type even though it is valid JSON.
func jsonNative(value any) any {
	raw, err := json.Marshal(value)
	if err != nil {
		return value
	}
	var native any
	if err := json.Unmarshal(raw, &native); err != nil {
		return value
	}
	return native
}

// VerifyLedger proves the chain is complete and untampered: sequence 1..N with no
// gap, genesis prev null, every prev equal to the previous hash, every recorded
// hash equal to the recomputed hash.
func VerifyLedger(entries []LedgerEntry) error {
	if len(entries) == 0 {
		return nil
	}
	var previous *string
	for index := range entries {
		entry := &entries[index]
		expectedSequence := int64(index + 1)
		if entry.Sequence != expectedSequence {
			return fmt.Errorf(
				"ledger hash chain invalid: sequence %d where %d was expected",
				entry.Sequence, expectedSequence)
		}
		if index == 0 {
			if entry.PrevSHA256 != nil {
				return errors.New("ledger hash chain invalid: the genesis entry carries prev_sha256")
			}
		} else if entry.PrevSHA256 == nil || previous == nil || *entry.PrevSHA256 != *previous {
			return fmt.Errorf(
				"ledger hash chain broken before sequence %d: prev_sha256 does not match sequence %d",
				entry.Sequence, entry.Sequence-1)
		}
		computed, err := ledgerEntryHash(entry)
		if err != nil {
			return err
		}
		if computed != entry.RecordSHA256 {
			return fmt.Errorf(
				"ledger hash chain invalid: sequence %d hashes to %s but records %s",
				entry.Sequence, computed, entry.RecordSHA256)
		}
		hash := entry.RecordSHA256
		previous = &hash
	}
	return nil
}

// AppendLedgerTx appends one entry inside the caller's transaction. The
// transaction-scoped advisory lock serialises appends, so the read of the head
// and the insert of its successor cannot interleave. A table trigger makes the
// ledger append-only for every other writer.
func AppendLedgerTx(ctx context.Context, tx *sql.Tx, request LedgerAppend) (*LedgerEntry, error) {
	if strings.TrimSpace(request.DeletionJobID) == "" || !uuid.Valid(request.DeletionJobID) {
		return nil, errors.New("a ledger entry needs a deletion job id")
	}
	if strings.TrimSpace(request.ResourceType) == "" || !uuid.Valid(request.ResourceID) {
		return nil, errors.New("a ledger entry needs a resource type and id")
	}
	if request.DeletionRevision < 1 {
		return nil, errors.New("a ledger entry needs a positive deletion revision")
	}
	switch request.EntryKind {
	case EntryTombstone, EntryScopeCleared, EntryScopeAbsent, EntryScopeFailed,
		EntryScopeExternal, EntryJobCompleted:
	default:
		return nil, fmt.Errorf("unknown ledger entry kind %q", request.EntryKind)
	}
	if _, err := tx.ExecContext(ctx, "SELECT pg_advisory_xact_lock($1)", LedgerAdvisoryLockKey); err != nil {
		return nil, fmt.Errorf("serialising the ledger append: %w", err)
	}
	var (
		lastSequence sql.NullInt64
		lastHash     sql.NullString
	)
	if err := tx.QueryRowContext(ctx, `
		SELECT sequence, record_sha256
		  FROM platform.deletion_ledger
		 ORDER BY sequence DESC
		 LIMIT 1`).Scan(&lastSequence, &lastHash); err != nil && !errors.Is(err, sql.ErrNoRows) {
		return nil, fmt.Errorf("reading the ledger head: %w", err)
	}
	entry := &LedgerEntry{
		Sequence:         lastSequence.Int64 + 1,
		DeletionJobID:    request.DeletionJobID,
		ResourceType:     request.ResourceType,
		ResourceID:       request.ResourceID,
		DeletionRevision: request.DeletionRevision,
		EntryKind:        request.EntryKind,
		Scope:            request.Scope,
		CopyClass:        request.CopyClass,
		ScopeKey:         request.ScopeKey,
		Record:           request.Record,
	}
	if lastHash.Valid {
		previous := lastHash.String
		entry.PrevSHA256 = &previous
	}
	hash, err := ledgerEntryHash(entry)
	if err != nil {
		return nil, err
	}
	entry.RecordSHA256 = hash
	encodedRecord, err := json.Marshal(entry.Record)
	if err != nil {
		return nil, fmt.Errorf("encoding the ledger record: %w", err)
	}
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO platform.deletion_ledger
		       (sequence, deletion_job_id, resource_type, resource_id,
		        deletion_revision, entry_kind, scope, copy_class, scope_key,
		        record, record_sha256, prev_sha256)
		VALUES ($1, $2::uuid, $3, $4::uuid, $5, $6, $7, $8, $9, $10::jsonb, $11, $12)`,
		entry.Sequence, entry.DeletionJobID, entry.ResourceType, entry.ResourceID,
		entry.DeletionRevision, entry.EntryKind, entry.Scope, entry.CopyClass,
		entry.ScopeKey, string(encodedRecord), entry.RecordSHA256, entry.PrevSHA256,
	); err != nil {
		return nil, fmt.Errorf("appending to the deletion ledger: %w", err)
	}
	return entry, nil
}

// ReadLedger loads the whole ledger in sequence order.
func ReadLedger(ctx context.Context, db *sql.DB) ([]LedgerEntry, error) {
	rows, err := db.QueryContext(ctx, `
		SELECT sequence, deletion_job_id::text, resource_type, resource_id::text,
		       deletion_revision, entry_kind, scope, copy_class, scope_key,
		       record, record_sha256, prev_sha256,
		       to_char(created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')
		  FROM platform.deletion_ledger
		 ORDER BY sequence`)
	if err != nil {
		return nil, fmt.Errorf("reading the deletion ledger: %w", err)
	}
	defer rows.Close()
	entries := make([]LedgerEntry, 0, 64)
	for rows.Next() {
		var (
			entry       LedgerEntry
			rawRecord   []byte
			scope       sql.NullString
			copyClass   sql.NullString
			scopeKey    sql.NullString
			prevSHA256  sql.NullString
			createdAt   string
			resourceID  string
			deletionJob string
		)
		if err := rows.Scan(
			&entry.Sequence, &deletionJob, &entry.ResourceType, &resourceID,
			&entry.DeletionRevision, &entry.EntryKind, &scope, &copyClass,
			&scopeKey, &rawRecord, &entry.RecordSHA256, &prevSHA256, &createdAt,
		); err != nil {
			return nil, fmt.Errorf("scanning a deletion ledger entry: %w", err)
		}
		entry.DeletionJobID = deletionJob
		entry.ResourceID = resourceID
		entry.CreatedAt = createdAt
		if scope.Valid {
			entry.Scope = &scope.String
		}
		if copyClass.Valid {
			entry.CopyClass = &copyClass.String
		}
		if scopeKey.Valid {
			entry.ScopeKey = &scopeKey.String
		}
		if prevSHA256.Valid {
			entry.PrevSHA256 = &prevSHA256.String
		}
		if err := json.Unmarshal(rawRecord, &entry.Record); err != nil {
			return nil, fmt.Errorf("decoding a deletion ledger record: %w", err)
		}
		if entry.Record == nil {
			entry.Record = map[string]any{}
		}
		entries = append(entries, entry)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("reading the deletion ledger: %w", err)
	}
	return entries, nil
}

// ExportLedger verifies the chain and writes the JSONL export to w. The first
// line is the header; every following line is one entry. It returns the header
// and the verified entries.
func ExportLedger(ctx context.Context, db *sql.DB, w io.Writer, database string) (LedgerHeader, []LedgerEntry, error) {
	entries, err := ReadLedger(ctx, db)
	if err != nil {
		return LedgerHeader{}, nil, err
	}
	if err := VerifyLedger(entries); err != nil {
		return LedgerHeader{}, nil, err
	}
	header := LedgerHeader{
		Format:     LedgerFormat,
		ExportedAt: time.Now().UTC().Format(time.RFC3339Nano),
		Database:   database,
		Count:      len(entries),
	}
	if len(entries) > 0 {
		header.HeadSHA256 = entries[len(entries)-1].RecordSHA256
	}
	encoder := json.NewEncoder(w)
	if err := encoder.Encode(header); err != nil {
		return LedgerHeader{}, nil, fmt.Errorf("writing the ledger header: %w", err)
	}
	for index := range entries {
		if err := encoder.Encode(&entries[index]); err != nil {
			return LedgerHeader{}, nil, fmt.Errorf("writing ledger entry %d: %w", entries[index].Sequence, err)
		}
	}
	return header, entries, nil
}

// ParseLedgerFile reads and verifies one exported ledger file. The header count
// and head hash must agree with the entries; the chain must verify. Nothing is
// returned for a tampered file.
func ParseLedgerFile(r io.Reader) (LedgerHeader, []LedgerEntry, error) {
	scanner := bufio.NewScanner(r)
	scanner.Buffer(make([]byte, 0, 64*1024), 16*1024*1024)
	var header LedgerHeader
	entries := make([]LedgerEntry, 0, 64)
	line := 0
	for scanner.Scan() {
		line++
		raw := strings.TrimSpace(scanner.Text())
		if raw == "" {
			continue
		}
		if line == 1 {
			if err := json.Unmarshal([]byte(raw), &header); err != nil {
				return LedgerHeader{}, nil, fmt.Errorf("reading the ledger header: %w", err)
			}
			if header.Format != LedgerFormat {
				return LedgerHeader{}, nil, fmt.Errorf(
					"unsupported ledger format %q (expected %q)", header.Format, LedgerFormat)
			}
			continue
		}
		var entry LedgerEntry
		if err := json.Unmarshal([]byte(raw), &entry); err != nil {
			return LedgerHeader{}, nil, fmt.Errorf("reading ledger entry on line %d: %w", line, err)
		}
		if entry.Record == nil {
			entry.Record = map[string]any{}
		}
		entries = append(entries, entry)
	}
	if err := scanner.Err(); err != nil {
		return LedgerHeader{}, nil, fmt.Errorf("reading the ledger file: %w", err)
	}
	if line == 0 {
		return LedgerHeader{}, nil, errors.New("the ledger file is empty")
	}
	if header.Count != len(entries) {
		return LedgerHeader{}, nil, fmt.Errorf(
			"ledger file rejected: header records %d entries but the file carries %d",
			header.Count, len(entries))
	}
	if err := VerifyLedger(entries); err != nil {
		return LedgerHeader{}, nil, err
	}
	head := ""
	if len(entries) > 0 {
		head = entries[len(entries)-1].RecordSHA256
	}
	if header.HeadSHA256 != head {
		return LedgerHeader{}, nil, fmt.Errorf(
			"ledger file rejected: header head_sha256 does not match the last entry")
	}
	return header, entries, nil
}

// ReplayResult is what `orchara-admin deletion replay` reports.
type ReplayResult struct {
	Status         string `json:"status"`
	Entries        int    `json:"entries"`
	Replayed       int    `json:"replayed"`
	AlreadyDeleted int    `json:"already_deleted"`
	Missing        int    `json:"missing"`
	WouldReplay    int    `json:"would_replay"`
	CheckOnly      bool   `json:"check_only"`
	HeadSHA256     string `json:"head_sha256"`
}

// ReplayLedger re-applies every tombstone decision that is still alive in the
// target database. Every applied decision and its new ledger row commit in one
// transaction; a second replay finds every decision already applied and appends
// nothing. With checkOnly it only verifies and counts (no transaction writes).
func ReplayLedger(
	ctx context.Context, db *sql.DB, entries []LedgerEntry, checkOnly bool,
) (ReplayResult, error) {
	result := ReplayResult{
		Status:     "ok",
		Entries:    len(entries),
		CheckOnly:  checkOnly,
		HeadSHA256: entries[len(entries)-1].RecordSHA256,
	}
	if len(entries) == 0 {
		return result, nil
	}
	for index := range entries {
		entry := &entries[index]
		if entry.EntryKind != EntryTombstone {
			continue
		}
		if checkOnly {
			state, err := tombstoneState(ctx, db, entry)
			if err != nil {
				return result, err
			}
			switch state {
			case tombstoneAlive:
				result.WouldReplay++
			case tombstoneAlready:
				result.AlreadyDeleted++
			case tombstoneMissing:
				result.Missing++
			}
			continue
		}
		outcome, err := replayTombstone(ctx, db, entry)
		if err != nil {
			return result, err
		}
		switch outcome {
		case tombstoneApplied:
			result.Replayed++
		case tombstoneAlready:
			result.AlreadyDeleted++
		case tombstoneMissing:
			result.Missing++
		}
	}
	return result, nil
}

type tombstoneOutcome int

const (
	tombstoneMissing tombstoneOutcome = iota
	tombstoneAlready
	tombstoneAlive
	tombstoneApplied
)

// tombstoneState is the read-only check used by --check-only.
func tombstoneState(ctx context.Context, db *sql.DB, entry *LedgerEntry) (tombstoneOutcome, error) {
	query, args := tombstoneProbe(entry)
	var deletedAt sql.NullTime
	err := db.QueryRowContext(ctx, query, args...).Scan(&deletedAt)
	if errors.Is(err, sql.ErrNoRows) {
		return tombstoneMissing, nil
	}
	if err != nil {
		return tombstoneMissing, fmt.Errorf("checking a replayed tombstone: %w", err)
	}
	if deletedAt.Valid {
		return tombstoneAlready, nil
	}
	return tombstoneAlive, nil
}

func tombstoneProbe(entry *LedgerEntry) (string, []any) {
	switch entry.ResourceType {
	case string(ResourceArtifact):
		return `SELECT deleted_at FROM platform.artifacts WHERE id = $1::uuid`, []any{entry.ResourceID}
	case string(ResourceMemory):
		return `SELECT deleted_at FROM platform.memories WHERE id = $1::uuid`, []any{entry.ResourceID}
	case string(ResourceDocument):
		return `SELECT deleted_at FROM platform.knowledge_documents WHERE id = $1::uuid`, []any{entry.ResourceID}
	case string(ResourceConversation):
		return `SELECT deleted_at FROM platform.conversations WHERE id = $1::uuid`, []any{entry.ResourceID}
	default:
		return `SELECT NULL::timestamptz WHERE false`, nil
	}
}

// replayTombstone applies one decision in its own transaction and appends the
// replay evidence entry to the restored database's ledger in the same commit.
func replayTombstone(ctx context.Context, db *sql.DB, entry *LedgerEntry) (tombstoneOutcome, error) {
	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return tombstoneMissing, fmt.Errorf("starting the ledger replay transaction: %w", err)
	}
	defer func() { _ = tx.Rollback() }()

	applied, err := applyTombstoneTx(ctx, tx, entry)
	if err != nil {
		return tombstoneMissing, err
	}
	if applied == tombstoneApplied {
		record := map[string]any{
			"source":            "replay",
			"original_sequence": float64(entry.Sequence),
			"resource_type":     entry.ResourceType,
			"resource_id":       entry.ResourceID,
			"deletion_revision": float64(entry.DeletionRevision),
		}
		if derived, ok := entry.Record["derived_document_ids"]; ok {
			record["derived_document_ids"] = derived
		}
		if _, err := AppendLedgerTx(ctx, tx, LedgerAppend{
			DeletionJobID:    entry.DeletionJobID,
			ResourceType:     entry.ResourceType,
			ResourceID:       entry.ResourceID,
			DeletionRevision: entry.DeletionRevision,
			EntryKind:        EntryTombstone,
			Record:           record,
		}); err != nil {
			return tombstoneMissing, err
		}
	}
	if err := tx.Commit(); err != nil {
		return tombstoneMissing, fmt.Errorf("committing the replayed tombstone: %w", err)
	}
	return applied, nil
}

// applyTombstoneTx re-applies one logical deletion. The update only fires on a
// row that is still alive (`deleted_at IS NULL`), so a second replay changes
// nothing; `GREATEST` keeps a newer revision from ever moving backwards.
func applyTombstoneTx(ctx context.Context, tx *sql.Tx, entry *LedgerEntry) (tombstoneOutcome, error) {
	var (
		revision int64
		owner    string
		app      string
	)
	var err error
	switch entry.ResourceType {
	case string(ResourceArtifact):
		err = tx.QueryRowContext(ctx, `
			UPDATE platform.artifacts
			   SET deleted_at = now(), state = 'deleted',
			       revision = GREATEST(revision + 1, $2)
			 WHERE id = $1::uuid AND deleted_at IS NULL
			 RETURNING revision`, entry.ResourceID, entry.DeletionRevision).Scan(&revision)
		if err == nil {
			if err := tombstoneDerivedDocumentsTx(ctx, tx, entry.Record); err != nil {
				return tombstoneMissing, err
			}
		}
	case string(ResourceMemory):
		err = tx.QueryRowContext(ctx, `
			UPDATE platform.memories
			   SET deleted_at = now(), revision = GREATEST(revision + 1, $2)
			 WHERE id = $1::uuid AND deleted_at IS NULL
			 RETURNING revision, owner_id::text, application_id::text`,
			entry.ResourceID, entry.DeletionRevision).Scan(&revision, &owner, &app)
		if err == nil {
			scope := "memory:" + strings.ToLower(owner) + ":" + strings.ToLower(app)
			if _, err := tx.ExecContext(ctx, `
				INSERT INTO platform.policy_epochs (scope, revision) VALUES ($1, 1)
				ON CONFLICT (scope) DO UPDATE
				   SET revision = platform.policy_epochs.revision + 1`, scope); err != nil {
				return tombstoneMissing, fmt.Errorf("raising the replayed memory epoch: %w", err)
			}
		}
	case string(ResourceDocument):
		err = tx.QueryRowContext(ctx, `
			UPDATE platform.knowledge_documents
			   SET deleted_at = now(), state = 'deleted',
			       revision = GREATEST(revision + 1, $2)
			 WHERE id = $1::uuid AND deleted_at IS NULL
			 RETURNING revision`, entry.ResourceID, entry.DeletionRevision).Scan(&revision)
	case string(ResourceConversation):
		err = tx.QueryRowContext(ctx, `
			UPDATE platform.conversations
			   SET deleted_at = now(), revision = GREATEST(revision + 1, $2)
			 WHERE id = $1::uuid AND deleted_at IS NULL
			 RETURNING revision`, entry.ResourceID, entry.DeletionRevision).Scan(&revision)
		if err == nil {
			if _, err := tx.ExecContext(ctx, `
				UPDATE platform.requests
				   SET state = 'cancelled', revision = revision + 1, updated_at = now()
				 WHERE conversation_id = $1::uuid
				   AND state IN ('queued', 'routing', 'awaiting_input', 'awaiting_selection')`,
				entry.ResourceID); err != nil {
				return tombstoneMissing, fmt.Errorf("cancelling replayed conversation requests: %w", err)
			}
			if _, err := tx.ExecContext(ctx, `
				UPDATE platform.interactions AS i
				   SET state = 'invalidated'
				 WHERE i.state = 'pending'
				   AND i.request_id IN (
				        SELECT id FROM platform.requests WHERE conversation_id = $1::uuid)`,
				entry.ResourceID); err != nil {
				return tombstoneMissing, fmt.Errorf("invalidating replayed conversation interactions: %w", err)
			}
		}
	default:
		return tombstoneMissing, fmt.Errorf("unsupported ledger resource type %q", entry.ResourceType)
	}
	if errors.Is(err, sql.ErrNoRows) {
		// Distinguish "the row is already tombstoned" from "the restore does not
		// contain the row at all": the first is success, the second is a gap the
		// operator must see.
		probe, args := tombstoneProbe(entry)
		var deletedAt sql.NullTime
		if probeErr := tx.QueryRowContext(ctx, probe, args...).Scan(&deletedAt); probeErr != nil {
			if errors.Is(probeErr, sql.ErrNoRows) {
				return tombstoneMissing, nil
			}
			return tombstoneMissing, fmt.Errorf("reading the replayed resource row: %w", probeErr)
		}
		return tombstoneAlready, nil
	}
	if err != nil {
		return tombstoneMissing, fmt.Errorf("replaying the deletion decision: %w", err)
	}
	return tombstoneApplied, nil
}

func tombstoneDerivedDocumentsTx(ctx context.Context, tx *sql.Tx, record map[string]any) error {
	raw, ok := record["derived_document_ids"]
	if !ok {
		return nil
	}
	ids := make([]string, 0, 8)
	switch typed := raw.(type) {
	case []any:
		for _, item := range typed {
			if value, ok := item.(string); ok && uuid.Valid(value) {
				ids = append(ids, value)
			}
		}
	case []string:
		for _, value := range typed {
			if uuid.Valid(value) {
				ids = append(ids, value)
			}
		}
	}
	sort.Strings(ids)
	if len(ids) == 0 {
		return nil
	}
	if _, err := tx.ExecContext(ctx, `
		UPDATE platform.knowledge_documents
		   SET deleted_at = now(), state = 'deleted', revision = revision + 1
		 WHERE id = ANY($1::uuid[]) AND deleted_at IS NULL`, ids); err != nil {
		return fmt.Errorf("replaying the derived document tombstones: %w", err)
	}
	return nil
}

// TombstoneRecord is the closed evidence object of a deletion decision. It is
// written in the same transaction as the tombstone, so a restored database with
// this ledger can always re-derive the decision. derivedDocumentIDs are the rows
// the same transaction tombstoned as a consequence (a source artifact's
// documents); they are ids only.
func TombstoneRecord(
	resourceType ResourceType, resourceID string, revision int64, derivedDocumentIDs []string,
) map[string]any {
	record := map[string]any{
		"resource_type":     string(resourceType),
		"resource_id":       resourceID,
		"deletion_revision": float64(revision),
	}
	if len(derivedDocumentIDs) > 0 {
		sorted := append([]string(nil), derivedDocumentIDs...)
		sort.Strings(sorted)
		record["derived_document_ids"] = sorted
	}
	return record
}

// derivedDocumentIDsOf extracts the document ids a Target plan says the deletion
// tombstoned in the same transaction. It reads the search_index descriptor (the
// only place the resource adapters record them) and never parses free text.
func derivedDocumentIDsOf(plan Plan) []string {
	value, ok := plan.Details[ScopeSearchIndex]
	if !ok || value == nil {
		return nil
	}
	descriptor, ok := value.(map[string]any)
	if !ok {
		return nil
	}
	ids := make([]string, 0, 4)
	appendID := func(value any) {
		if id, ok := value.(string); ok && uuid.Valid(id) {
			ids = append(ids, id)
		}
	}
	if single, ok := descriptor["document_id"]; ok {
		appendID(single)
	}
	switch typed := descriptor["document_ids"].(type) {
	case []string:
		for _, id := range typed {
			appendID(id)
		}
	case []any:
		for _, item := range typed {
			appendID(item)
		}
	}
	sort.Strings(ids)
	return ids
}
