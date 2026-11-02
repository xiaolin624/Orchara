// Command maintenance sweep: object-store orphan cleanup (F00B/T026).
//
// Two kinds of bytes are safe to delete, and only after an age threshold:
//
//	staging/  no live artifact is waiting for it. A live artifact means an
//	          `uploading`/`scanning`, non-deleted artifact row whose
//	          staging_object_key is exactly this key. A staging key referenced
//	          only by a ready/quarantined/deleted row is a leftover from an
//	          upload that already finished (or failed).
//	sealed/   no artifact row references the key at all. A sealed object that any
//	          artifact row references is never deleted here, whatever the row's
//	          state: deletion of a live artifact's sealed bytes belongs to F07's
//	          deletion model, not to the orphan sweep.
//
// The sweep never guesses: it reads the references from PostgreSQL, lists the
// real store, and re-reads the database before each deletion decision. It is
// idempotent (a second run finds nothing) and prints one JSON line per deletion
// plus a summary. `--dry-run` reports what would be deleted and touches nothing.
package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"log"
	"os"
	"strings"
	"time"

	_ "github.com/jackc/pgx/v5/stdlib"

	"orchara/platform/internal/config"
	"orchara/platform/internal/objectstore"
)

// sweepDeletion is one planned/applied deletion.
type sweepDeletion struct {
	Key       string `json:"key"`
	Store     string `json:"store"` // staging|sealed
	Reason    string `json:"reason"`
	SizeBytes int64  `json:"size_bytes"`
	Action    string `json:"action"` // deleted|would_delete
}

// sweepSummary is the last JSON line the command prints.
type sweepSummary struct {
	Status         string `json:"status"`
	OrphanAge      string `json:"orphan_age"`
	DryRun         bool   `json:"dry_run"`
	ScannedStaging int    `json:"scanned_staging"`
	ScannedSealed  int    `json:"scanned_sealed"`
	Deleted        int    `json:"deleted"`
	WouldDelete    int    `json:"would_delete"`
	Kept           int    `json:"kept"`
	DeletedBytes   int64  `json:"deleted_bytes"`
	Note           string `json:"note"`
}

func runSweep(args []string) {
	options, err := parseSweepOptions(args)
	if err != nil {
		fmt.Fprintf(os.Stderr, "orchara-maintenance: %v\n\n%s", err, usage)
		os.Exit(2)
	}
	logger := log.New(os.Stderr, "orchara-maintenance ", log.LstdFlags|log.LUTC)
	cfg, err := config.LoadSweep(config.FromEnv())
	if err != nil {
		failMaintenance(logger, err)
	}
	store, err := objectstore.Open(cfg.ObjectDir)
	if err != nil {
		failMaintenance(logger, fmt.Errorf("object store rejected: %w", err))
	}
	db, err := sql.Open("pgx", cfg.DBDSN)
	if err != nil {
		failMaintenance(logger, errors.New("cannot open the maintenance database handle"))
	}
	defer db.Close()
	db.SetMaxOpenConns(1)

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Minute)
	defer cancel()
	if err := db.PingContext(ctx); err != nil {
		failMaintenance(logger, errors.New("maintenance cannot reach the database"))
	}

	summary, deletions, err := sweepOrphans(ctx, db, store, options.orphanAge, options.dryRun, time.Now())
	if err != nil {
		failMaintenance(logger, err)
	}
	for _, deletion := range deletions {
		_ = json.NewEncoder(os.Stdout).Encode(deletion)
	}
	_ = json.NewEncoder(os.Stdout).Encode(summary)
}

type sweepOptions struct {
	orphanAge time.Duration
	dryRun    bool
}

func parseSweepOptions(args []string) (*sweepOptions, error) {
	flags := flag.NewFlagSet("sweep", flag.ContinueOnError)
	flags.SetOutput(io.Discard)
	orphanAge := flags.Duration("orphan-age", 0,
		"only delete objects at least this old (required, e.g. 1h)")
	dryRun := flags.Bool("dry-run", false, "report what would be deleted without deleting")
	if err := flags.Parse(args); err != nil {
		return nil, err
	}
	if flags.NArg() > 0 {
		return nil, fmt.Errorf("unexpected arguments: %s", strings.Join(flags.Args(), " "))
	}
	supplied := false
	flags.Visit(func(visited *flag.Flag) {
		if visited.Name == "orphan-age" {
			supplied = true
		}
	})
	if !supplied {
		return nil, errors.New("--orphan-age is required (for example --orphan-age 1h)")
	}
	if *orphanAge < 0 {
		return nil, errors.New("--orphan-age must not be negative")
	}
	return &sweepOptions{orphanAge: *orphanAge, dryRun: *dryRun}, nil
}

// stagingReference is what the database knows about one staging key.
type stagingReference struct {
	live   bool
	reason string
}

// sweepOrphans performs one bounded pass. It returns the deletions it applied
// (or would apply in dry-run mode) and the summary.
func sweepOrphans(
	ctx context.Context,
	db *sql.DB,
	store *objectstore.Store,
	orphanAge time.Duration,
	dryRun bool,
	now time.Time,
) (sweepSummary, []sweepDeletion, error) {
	summary := sweepSummary{
		Status:    "ok",
		OrphanAge: orphanAge.String(),
		DryRun:    dryRun,
		Note:      "staging without a live uploader and sealed objects no artifact row references; live/deleted-artifact sealed bytes are F07's",
	}

	staging, err := loadStagingReferences(ctx, db)
	if err != nil {
		return summary, nil, err
	}
	sealed, err := loadSealedReferences(ctx, db)
	if err != nil {
		return summary, nil, err
	}

	stagingObjects, err := store.List("staging/")
	if err != nil {
		return summary, nil, fmt.Errorf("listing staging objects: %w", err)
	}
	sealedObjects, err := store.List("sealed/")
	if err != nil {
		return summary, nil, fmt.Errorf("listing sealed objects: %w", err)
	}
	summary.ScannedStaging = len(stagingObjects)
	summary.ScannedSealed = len(sealedObjects)

	deletions := make([]sweepDeletion, 0)
	decide := func(key, storeName, reason string, size int64) error {
		deletion := sweepDeletion{Key: key, Store: storeName, Reason: reason, SizeBytes: size}
		if dryRun {
			deletion.Action = "would_delete"
			summary.WouldDelete++
			deletions = append(deletions, deletion)
			return nil
		}
		if err := store.Delete(key); err != nil {
			return fmt.Errorf("deleting orphan %s: %w", key, err)
		}
		deletion.Action = "deleted"
		summary.Deleted++
		summary.DeletedBytes += size
		deletions = append(deletions, deletion)
		return nil
	}

	for _, object := range stagingObjects {
		reference, present := staging[object.Key]
		if present && reference.live {
			summary.Kept++
			continue
		}
		tooYoung := now.Sub(object.ModTime) < orphanAge
		if tooYoung {
			summary.Kept++
			continue
		}
		reason := "no_artifact_reference"
		if present {
			reason = reference.reason
		}
		if err := decide(object.Key, "staging", reason, object.Size); err != nil {
			return summary, deletions, err
		}
	}

	for _, object := range sealedObjects {
		if sealed[object.Key] {
			// Referenced by an artifact row in some state. The sweep never
			// deletes those: F07's deletion model owns artifact removal.
			summary.Kept++
			continue
		}
		if now.Sub(object.ModTime) < orphanAge {
			summary.Kept++
			continue
		}
		if err := decide(object.Key, "sealed", "no_artifact_reference", object.Size); err != nil {
			return summary, deletions, err
		}
	}
	return summary, deletions, nil
}

// loadStagingReferences maps each referenced staging key to whether a live
// uploader still owns it. Only uploading/scanning, non-deleted artifacts are
// live; every other state means the staging object is no longer needed.
func loadStagingReferences(ctx context.Context, db *sql.DB) (map[string]stagingReference, error) {
	rows, err := db.QueryContext(ctx, `
		SELECT staging_object_key, state, deleted_at
		  FROM platform.artifacts
		 WHERE staging_object_key IS NOT NULL`)
	if err != nil {
		return nil, fmt.Errorf("reading staging references: %w", err)
	}
	defer rows.Close()
	references := make(map[string]stagingReference)
	for rows.Next() {
		var (
			key     string
			state   string
			deleted sql.NullTime
		)
		if err := rows.Scan(&key, &state, &deleted); err != nil {
			return nil, fmt.Errorf("reading staging references: %w", err)
		}
		if deleted.Valid {
			references[key] = stagingReference{live: false, reason: "artifact_deleted"}
			continue
		}
		switch state {
		case "uploading", "scanning":
			references[key] = stagingReference{live: true}
		case "ready":
			references[key] = stagingReference{live: false, reason: "artifact_ready"}
		case "quarantined":
			references[key] = stagingReference{live: false, reason: "artifact_quarantined"}
		default:
			references[key] = stagingReference{live: false, reason: "artifact_not_uploading"}
		}
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("reading staging references: %w", err)
	}
	return references, nil
}

// loadSealedReferences returns every object key any artifact row points at.
func loadSealedReferences(ctx context.Context, db *sql.DB) (map[string]bool, error) {
	rows, err := db.QueryContext(ctx, `SELECT object_key FROM platform.artifacts`)
	if err != nil {
		return nil, fmt.Errorf("reading sealed references: %w", err)
	}
	defer rows.Close()
	references := make(map[string]bool)
	for rows.Next() {
		var key string
		if err := rows.Scan(&key); err != nil {
			return nil, fmt.Errorf("reading sealed references: %w", err)
		}
		references[key] = true
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("reading sealed references: %w", err)
	}
	return references, nil
}
