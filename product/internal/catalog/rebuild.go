package catalog

// F08/T077: the catalog projection writer and the rebuild process.
//
// A projection row is derived from exactly one authoritative version row:
// `application_versions.capability_card` becomes `search_text`, the version's
// `manifest_sha256` becomes `source_manifest_sha256`, and the owning application
// is copied for the SQL ACL predicate. Nothing else is trusted. The same
// transaction re-reads the version under a share lock, so a row is only written
// for a version that was published, unrevoked and unarchived at write time.
//
// A full rebuild is the restart path. It
//   1. bumps the projection epoch and records `rebuilding` (committed before any
//      version is read, so an interrupted/failed attempt is observable),
//   2. deletes projection rows that no longer match the authority,
//   3. indexes every eligible version, one transaction per version,
//   4. marks the ledger `ready` and delivers the pending `catalog_index` outbox
//      rows in the final transaction.
// An error aborts the rebuild, records `last_error_code`/`last_error_at` against
// that epoch and leaves the tree in a state where search uses the authorized
// full-text fallback; search never serves a half-built projection.
//
// `IndexVersionTx` is the incremental seam T068/T070 (and the `catalog_index`
// handler) call. A version that is revoked, archived or gone is reconciled by
// deleting its entry rather than writing a stale one.

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"time"

	"orchara/platform/internal/background"
	"orchara/platform/internal/uuid"
)

// IndexOutcome is the result of one incremental projection write.
type IndexOutcome struct {
	VersionID      string
	ApplicationID  string
	ManifestSHA256 string
	// Result is "indexed" when the row was written, "removed" when an existing
	// row was deleted because the authoritative version is revoked, archived or
	// gone, and "skipped" when there was nothing to write or remove.
	Result string
}

// RebuildOptions selects the rebuild shape.
type RebuildOptions struct {
	// VersionID, when set, indexes exactly that version without touching the
	// ledger (the incremental administrative path).
	VersionID string
}

// RebuildReport is the machine-readable result the admin CLI prints.
type RebuildReport struct {
	Status          string     `json:"status"`
	State           string     `json:"state"`
	ProjectionEpoch int64      `json:"projection_epoch"`
	Expected        int        `json:"expected"`
	Indexed         int        `json:"indexed"`
	OutboxConsumed  int        `json:"outbox_consumed"`
	RebuiltAt       *time.Time `json:"rebuilt_at,omitempty"`
	ErrorCode       string     `json:"error_code,omitempty"`
}

// IndexVersionTx writes or removes one projection row inside tx. The caller owns
// the commit, so a publish/revoke transaction can index in the same transaction
// as its state change (the T068/T070 hook), and an interrupted caller never
// leaves a half-written entry.
func (r *Reader) IndexVersionTx(
	ctx context.Context, tx *sql.Tx, versionID string,
) (*IndexOutcome, error) {
	if tx == nil {
		return nil, errors.New("an indexing transaction is required")
	}
	if !uuid.Valid(versionID) {
		return nil, ErrCatalogSourceMissing
	}
	var (
		applicationID string
		manifestSHA   string
		cardText      string
		revoked       sql.NullTime
		archived      sql.NullTime
	)
	err := tx.QueryRowContext(ctx, `
		SELECT v.application_id::text, v.manifest_sha256, v.capability_card::text,
		       v.revoked_at, a.archived_at
		  FROM platform.application_versions AS v
		  JOIN platform.applications AS a ON a.id = v.application_id
		 WHERE v.id = $1::uuid
		   FOR SHARE OF v, a`, versionID,
	).Scan(&applicationID, &manifestSHA, &cardText, &revoked, &archived)
	if errors.Is(err, sql.ErrNoRows) {
		return r.removeIndexEntryTx(ctx, tx, versionID, "skipped")
	}
	if err != nil {
		return nil, fmt.Errorf("reading the version to index: %w", err)
	}
	outcome := &IndexOutcome{
		VersionID: versionID, ApplicationID: applicationID, ManifestSHA256: manifestSHA,
	}
	if revoked.Valid || archived.Valid {
		return r.removeIndexEntryTx(ctx, tx, versionID, "removed")
	}
	_, searchText, err := parseCatalogCard(cardText)
	if err != nil {
		return nil, err
	}
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO platform.application_catalog_entries
		       (application_version_id, application_id, search_text,
		        source_manifest_sha256, updated_at)
		VALUES ($1::uuid, $2::uuid, $3, $4, now())
		ON CONFLICT (application_version_id) DO UPDATE
		   SET application_id = EXCLUDED.application_id,
		       search_text = EXCLUDED.search_text,
		       source_manifest_sha256 = EXCLUDED.source_manifest_sha256,
		       updated_at = now()`,
		versionID, applicationID, searchText, manifestSHA,
	); err != nil {
		return nil, fmt.Errorf("writing the catalog projection entry: %w", err)
	}
	outcome.Result = "indexed"
	return outcome, nil
}

func (r *Reader) removeIndexEntryTx(
	ctx context.Context, tx *sql.Tx, versionID, result string,
) (*IndexOutcome, error) {
	if _, err := tx.ExecContext(ctx, `
		DELETE FROM platform.application_catalog_entries
		 WHERE application_version_id = $1::uuid`, versionID,
	); err != nil {
		return nil, fmt.Errorf("removing a stale catalog projection entry: %w", err)
	}
	return &IndexOutcome{VersionID: versionID, Result: result}, nil
}

// IndexVersionNow is IndexVersionTx in its own transaction. It is the
// `catalog_index` handler's write path.
func (r *Reader) IndexVersionNow(
	ctx context.Context, versionID string,
) (*IndexOutcome, error) {
	tx, err := r.DB.BeginTx(ctx, nil)
	if err != nil {
		return nil, fmt.Errorf("starting the catalog indexing transaction: %w", err)
	}
	defer func() { _ = tx.Rollback() }()
	outcome, err := r.IndexVersionTx(ctx, tx, versionID)
	if err != nil {
		return nil, err
	}
	if err := tx.Commit(); err != nil {
		return nil, fmt.Errorf("committing the catalog indexing transaction: %w", err)
	}
	return outcome, nil
}

// EnqueueIndexVersionTx writes one `catalog_index` job in the caller's
// transaction. The caller is the authority (publish/revoke path or the admin CLI);
// the handler re-reads the authority before writing, so a version revoked between
// enqueue and execution is reconciled, never mistaken for a valid projection.
//
// The dedup key is `<version_id>:<manifest_sha256>`: the same version content is
// one intention, and a new manifest hash is new work. This is the seam T068/T070
// call; T077 does not depend on that hook existing (a missing hook degrades to
// fallback search, never to a leak).
func (r *Reader) EnqueueIndexVersionTx(
	ctx context.Context, tx *sql.Tx, versionID string,
) (*background.Job, bool, error) {
	if tx == nil {
		return nil, false, errors.New("an enqueue transaction is required")
	}
	if !uuid.Valid(versionID) {
		return nil, false, ErrCatalogSourceMissing
	}
	var (
		ownerID     string
		manifestSHA string
	)
	err := tx.QueryRowContext(ctx, `
		SELECT a.owner_id::text, v.manifest_sha256
		  FROM platform.application_versions AS v
		  JOIN platform.applications AS a ON a.id = v.application_id
		 WHERE v.id = $1::uuid`, versionID,
	).Scan(&ownerID, &manifestSHA)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, false, ErrCatalogSourceMissing
	}
	if err != nil {
		return nil, false, fmt.Errorf("reading the version to enqueue: %w", err)
	}
	expected := manifestSHA
	return background.EnqueueTx(ctx, tx, background.EnqueueParams{
		Kind:           background.KindCatalogIndex,
		TargetID:       versionID,
		OwnerID:        ownerID,
		DedupKey:       versionID + ":" + manifestSHA,
		ExpectedSHA256: expected,
	})
}

// RebuildCatalog runs the rebuild (or one incremental version) against db.
// A failure is returned as *ErrCatalogRebuild with the recorded stable code; the
// returned report is still populated so the CLI can print it.
func RebuildCatalog(
	ctx context.Context, db *sql.DB, options RebuildOptions,
) (*RebuildReport, error) {
	reader := New(db, nil)
	if options.VersionID != "" {
		outcome, err := reader.IndexVersionNow(ctx, options.VersionID)
		if err != nil {
			code := rebuildErrorCode(err)
			return &RebuildReport{Status: "failed", State: CatalogIndexFailed, ErrorCode: code}, &ErrCatalogRebuild{Code: code, Err: err}
		}
		report := &RebuildReport{Status: "ok", State: outcome.Result}
		if outcome.Result == "indexed" {
			report.Indexed = 1
		}
		if state, err := readIndexLedger(ctx, db); err == nil && state != nil {
			report.ProjectionEpoch = state.projectionEpoch
		}
		return report, nil
	}
	return rebuildAll(ctx, db, reader)
}

// indexLedger is the one-row rebuild ledger as read by the writer.
type indexLedger struct {
	state           string
	projectionEpoch int64
}

func readIndexLedger(ctx context.Context, db *sql.DB) (*indexLedger, error) {
	var ledger indexLedger
	err := db.QueryRowContext(ctx,
		`SELECT state, projection_epoch FROM platform.catalog_index_state WHERE id = 'catalog'`,
	).Scan(&ledger.state, &ledger.projectionEpoch)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &ledger, nil
}

func rebuildAll(
	ctx context.Context, db *sql.DB, reader *Reader,
) (*RebuildReport, error) {
	epoch, err := startRebuild(ctx, db)
	if err != nil {
		return &RebuildReport{Status: "failed", ErrorCode: CodeCatalogRebuildFailed},
			&ErrCatalogRebuild{Code: CodeCatalogRebuildFailed, Err: err}
	}
	report := &RebuildReport{Status: "ok", ProjectionEpoch: epoch}
	fail := func(err error) (*RebuildReport, error) {
		code := rebuildErrorCode(err)
		if superseded := recordRebuildFailure(ctx, db, epoch, code); superseded {
			code = CodeCatalogRebuildSuperseded
		}
		report.Status = "failed"
		report.State = CatalogIndexFailed
		report.ErrorCode = code
		return report, &ErrCatalogRebuild{Code: code, Err: err}
	}

	rows, err := loadRebuildVersions(ctx, db)
	if err != nil {
		return fail(err)
	}
	report.Expected = len(rows)
	if _, err := db.ExecContext(ctx, `
		DELETE FROM platform.application_catalog_entries AS e
		 USING platform.application_versions AS v, platform.applications AS a
		 WHERE v.id = e.application_version_id
		   AND a.id = v.application_id
		   AND (a.archived_at IS NOT NULL
		        OR v.revoked_at IS NOT NULL
		        OR v.capability_card IS NULL
		        OR v.capability_card = '{}'::jsonb
		        OR e.application_id <> v.application_id
		        OR e.source_manifest_sha256 <> v.manifest_sha256)`); err != nil {
		return fail(fmt.Errorf("deleting stale catalog entries: %w", err))
	}

	for index, versionID := range rows {
		tx, err := db.BeginTx(ctx, nil)
		if err != nil {
			return fail(fmt.Errorf("starting the catalog rebuild batch: %w", err))
		}
		outcome, indexErr := reader.IndexVersionTx(ctx, tx, versionID)
		if indexErr != nil {
			_ = tx.Rollback()
			return fail(indexErr)
		}
		// The per-version progress is committed with the entry, so an
		// interrupted rebuild leaves both the written rows and their count.
		if _, err := tx.ExecContext(ctx, `
			UPDATE platform.catalog_index_state
			   SET indexed_count = $2, expected_count = $3, updated_at = now()
			 WHERE id = 'catalog' AND projection_epoch = $1`,
			epoch, index+1, len(rows)); err != nil {
			_ = tx.Rollback()
			return fail(fmt.Errorf("recording the catalog rebuild progress: %w", err))
		}
		if err := tx.Commit(); err != nil {
			return fail(fmt.Errorf("committing the catalog rebuild batch: %w", err))
		}
		if outcome.Result == "indexed" {
			report.Indexed++
		}
	}

	final, consumed, err := finishRebuild(ctx, db, epoch, len(rows), report.Indexed)
	if err != nil {
		return fail(err)
	}
	report.State = CatalogIndexReady
	report.RebuiltAt = final
	report.OutboxConsumed = consumed
	return report, nil
}

func startRebuild(ctx context.Context, db *sql.DB) (int64, error) {
	var epoch int64
	err := db.QueryRowContext(ctx, `
		INSERT INTO platform.catalog_index_state AS ledger
		       (id, state, projection_epoch, rebuild_started_at, rebuilt_at,
		        last_error_code, last_error_at, expected_count, indexed_count, updated_at)
		VALUES ('catalog', 'rebuilding', 1, now(), NULL, NULL, NULL, 0, 0, now())
		ON CONFLICT (id) DO UPDATE
		   SET state = 'rebuilding',
		       projection_epoch = ledger.projection_epoch + 1,
		       rebuild_started_at = now(),
		       rebuilt_at = NULL,
		       last_error_code = NULL,
		       last_error_at = NULL,
		       expected_count = 0,
		       indexed_count = 0,
		       updated_at = now()
		RETURNING projection_epoch`).Scan(&epoch)
	if err != nil {
		return 0, fmt.Errorf("recording the catalog rebuild start: %w", err)
	}
	return epoch, nil
}

func loadRebuildVersions(ctx context.Context, db *sql.DB) ([]string, error) {
	rows, err := db.QueryContext(ctx, `
		SELECT v.id::text
		  FROM platform.application_versions AS v
		  JOIN platform.applications AS a ON a.id = v.application_id
		 WHERE a.archived_at IS NULL
		   AND v.revoked_at IS NULL
		   AND v.capability_card IS NOT NULL
		   AND v.capability_card <> '{}'::jsonb
		 ORDER BY v.id`)
	if err != nil {
		return nil, fmt.Errorf("loading the catalog rebuild set: %w", err)
	}
	defer rows.Close()
	versions := make([]string, 0, 16)
	for rows.Next() {
		var versionID string
		if err := rows.Scan(&versionID); err != nil {
			return nil, fmt.Errorf("loading the catalog rebuild set: %w", err)
		}
		versions = append(versions, versionID)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("loading the catalog rebuild set: %w", err)
	}
	return versions, nil
}

func finishRebuild(
	ctx context.Context, db *sql.DB, epoch int64, expected, indexed int,
) (*time.Time, int, error) {
	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return nil, 0, fmt.Errorf("starting the catalog rebuild commit: %w", err)
	}
	defer func() { _ = tx.Rollback() }()
	var rebuiltAt time.Time
	err = tx.QueryRowContext(ctx, `
		UPDATE platform.catalog_index_state
		   SET state = 'ready', expected_count = $2, indexed_count = $3,
		       rebuilt_at = now(), updated_at = now()
		 WHERE id = 'catalog' AND projection_epoch = $1
		RETURNING rebuilt_at`, epoch, expected, indexed).Scan(&rebuiltAt)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, 0, ErrCatalogRebuildSuperseded
	}
	if err != nil {
		return nil, 0, fmt.Errorf("finishing the catalog rebuild: %w", err)
	}
	// The outbox is a durable signal, not the execution record. A full rebuild has
	// just re-derived every eligible version from the authority, so every pending
	// catalog signal is satisfied by that read: marking it delivered is the honest
	// consumption. The row is kept (delivered_at, attempts) for audit.
	delivered, err := tx.ExecContext(ctx, `
		UPDATE platform.outbox
		   SET delivered_at = now(), attempts = attempts + 1
		 WHERE kind = 'catalog_index'
		   AND delivered_at IS NULL
		   AND available_at <= now()`)
	if err != nil {
		return nil, 0, fmt.Errorf("delivering the catalog outbox rows: %w", err)
	}
	consumed, err := delivered.RowsAffected()
	if err != nil {
		return nil, 0, fmt.Errorf("counting the delivered catalog outbox rows: %w", err)
	}
	if err := tx.Commit(); err != nil {
		return nil, 0, fmt.Errorf("committing the catalog rebuild: %w", err)
	}
	return &rebuiltAt, int(consumed), nil
}

// recordRebuildFailure records the stable code against the rebuild's own epoch.
// It reports true when the epoch was superseded (no row updated), so the caller
// can stop claiming this rebuild owns the ledger.
func recordRebuildFailure(ctx context.Context, db *sql.DB, epoch int64, code string) bool {
	result, err := db.ExecContext(ctx, `
		UPDATE platform.catalog_index_state
		   SET state = 'failed', last_error_code = $2, last_error_at = now(),
		       updated_at = now()
		 WHERE id = 'catalog' AND projection_epoch = $1`, epoch, code)
	if err != nil {
		return false
	}
	affected, err := result.RowsAffected()
	if err != nil {
		return false
	}
	return affected == 0
}

func rebuildErrorCode(err error) string {
	switch {
	case errors.Is(err, ErrCatalogSourceInvalid):
		return CodeCatalogSourceInvalid
	case errors.Is(err, ErrCatalogSourceMissing):
		return CodeCatalogSourceMissing
	case errors.Is(err, ErrCatalogRebuildSuperseded):
		return CodeCatalogRebuildSuperseded
	default:
		return CodeCatalogRebuildFailed
	}
}
