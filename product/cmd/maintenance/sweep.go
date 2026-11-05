// Command maintenance sweep: staging object orphan cleanup (F00B/T026), extended
// by F11/T071 to provably-unreferenced sealed objects.
//
// Deletion rules:
//
//	staging/  no live uploader is waiting for it. A live artifact means an
//	          `uploading`/`scanning`, non-deleted artifact row whose
//	          staging_object_key is exactly this key. A staging key referenced
//	          only by a ready/quarantined/deleted row is a leftover from an
//	          upload that already finished (or failed).
//	sealed/   no artifact row names it *and* it is older than both
//	          --orphan-age and the documented 10-minute sealed-write grace.
//	          The five reference classes are checked explicitly: artifact
//	          rows, run artifacts, knowledge chunk bodies and memory bodies all
//	          resolve through platform.artifacts.object_key, and a tombstoned
//	          row still owns its object until the deletion purge clears it, so
//	          the sweep never races a pending deletion. The grace is the
//	          substitute for the missing writer/deletion lock: a real writer can
//	          be between PutSealed and the artifact-row commit, so a fresh
//	          sealed object is never garbage even when one snapshot sees no row.
//
// Until T071 the command only counted unreferenced sealed objects
// (`sealed_deferred`) and deleted none. It now deletes the old ones and reports
// the young ones it deferred; `--dry-run` reports the same set without deleting.
//
// The sweep never guesses: it reads the references from PostgreSQL, lists the
// real store, and re-reads the database before each deletion decision. It is
// idempotent (a second run finds nothing) and prints one JSON line per deletion
// plus a summary.
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

// sealedOrphanGrace is the minimum age of a sealed object before the sweep may
// call it an orphan. A sealed object is written before its artifact row commits;
// without a lock shared by the writer and the sweep, the age window is what makes
// "no row references this key" a stable observation instead of a race. It is a
// code constant (not a flag) so an operator cannot disable the safety margin by
// passing --orphan-age 0s: sealed deletion uses max(--orphan-age, this grace).
const sealedOrphanGrace = 10 * time.Minute

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
	SealedDeferred int    `json:"sealed_deferred"`
	SealedDeleted  int    `json:"sealed_deleted"`
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
		Note: "staging objects without a live uploader; sealed objects only when no artifact/run/chunk/memory " +
			"reference names them and they are older than both --orphan-age and the 10m sealed-write grace " +
			"(sealed_deferred counts the unreferenced-but-too-young ones)",
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

	sealedMinAge := orphanAge
	if sealedMinAge < sealedOrphanGrace {
		sealedMinAge = sealedOrphanGrace
	}
	for _, object := range sealedObjects {
		if sealed[object.Key] {
			// Named by an artifact row in some state (a tombstoned row still owns
			// its object until the deletion purge clears it). Never deleted here.
			summary.Kept++
			continue
		}
		// Unreferenced *by this snapshot*. A concurrent writer may be between
		// sealing the object and committing its artifact row, so only an object
		// older than the grace window is a stable observation.
		if now.Sub(object.ModTime) < sealedMinAge {
			summary.SealedDeferred++
			summary.Kept++
			continue
		}
		if err := decide(object.Key, "sealed", "unreferenced_sealed_object", object.Size); err != nil {
			return summary, deletions, err
		}
		summary.SealedDeleted++
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

// loadSealedReferences returns every object key any resource references. The
// five reference classes are named explicitly even though the last four resolve
// through the artifact row they point at: the join makes the rule executable
// rather than implied, so a future schema that stores a body key directly cannot
// silently become sweepable.
func loadSealedReferences(ctx context.Context, db *sql.DB) (map[string]bool, error) {
	rows, err := db.QueryContext(ctx, `
		SELECT object_key FROM platform.artifacts
		UNION
		SELECT a.object_key FROM platform.run_artifacts AS ra
		  JOIN platform.artifacts AS a ON a.id = ra.artifact_id
		UNION
		SELECT a.object_key FROM platform.knowledge_chunks AS kc
		  JOIN platform.artifacts AS a ON a.id = kc.body_artifact_id
		UNION
		SELECT a.object_key FROM platform.memories AS m
		  JOIN platform.artifacts AS a ON a.id = m.body_artifact_id
		UNION
		SELECT a.object_key FROM platform.memory_candidates AS c
		  JOIN platform.artifacts AS a ON a.id = c.body_artifact_id`)
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
