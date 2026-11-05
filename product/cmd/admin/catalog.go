package main

// F08/T077: the catalog projection is rebuilt and inspected here.
//
//	catalog rebuild [--all | --version UUID]   full rebuild or one version
//	catalog index --version UUID               enqueue a catalog_index job
//	catalog status                             print the rebuild ledger
//
// The rebuild is a real process: it records its epoch and `rebuilding` state
// before it reads any version, commits one transaction per version, and only
// marks the ledger `ready` (and delivers the pending outbox rows) when the whole
// authority has been indexed. An abort records `last_error_code`/`last_error_at`;
// an interrupted process leaves the ledger `rebuilding`. Search never serves a
// projection the ledger does not call ready.

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"strings"

	"orchara/platform/internal/catalog"
	"orchara/platform/internal/config"
	"orchara/platform/internal/uuid"
)

const catalogUsage = `usage: orchara-admin catalog <command> [flags]

commands:
  rebuild   rebuild the authorized purpose catalog projection from the authority
  index     enqueue one catalog_index background job for one version
  status    print the rebuild ledger (epoch, state, last error)

catalog rebuild [--all | --version UUID]
  --all            rebuild every published, unrevoked version (the default)
  --version UUID   index exactly one version without starting a new epoch

catalog index --version UUID
  Enqueue one catalog_index job (dedup key <version>:<manifest_sha256>) that the
  maintenance process reconciles against the authoritative row before writing.

catalog status
  Prints {"id","state","projection_epoch","rebuilt_at","last_error_code",...}; a
  database without a ledger prints state="absent".
`

func runCatalog(args []string) error {
	if len(args) == 0 {
		return usageErrorf("missing catalog command; expected 'rebuild', 'index' or 'status'")
	}
	switch args[0] {
	case "rebuild":
		return runCatalogRebuild(args[1:])
	case "index":
		return runCatalogIndex(args[1:])
	case "status":
		return runCatalogStatus(args[1:])
	case "help", "-h", "--help":
		fmt.Fprint(os.Stdout, catalogUsage)
		return nil
	default:
		return usageErrorf("unknown catalog command %q; expected 'rebuild', 'index' or 'status'", args[0])
	}
}

func runCatalogRebuild(args []string) error {
	flags := flag.NewFlagSet("catalog rebuild", flag.ContinueOnError)
	flags.SetOutput(io.Discard)
	all := flags.Bool("all", false, "rebuild every eligible version (the default)")
	versionID := flags.String("version", "", "index exactly one application version")
	if err := flags.Parse(args); err != nil {
		return usageErrorf("%v", err)
	}
	if flags.NArg() > 0 {
		return usageErrorf("unexpected arguments: %s", strings.Join(flags.Args(), " "))
	}
	version := strings.TrimSpace(*versionID)
	if version != "" {
		if *all {
			return usageErrorf("--all and --version are mutually exclusive")
		}
		if !uuid.Valid(version) {
			return usageErrorf("--version must be a UUID")
		}
	}

	db, err := openCatalogDatabase()
	if err != nil {
		return err
	}
	defer db.Close()

	ctx, cancel := context.WithTimeout(context.Background(), commandTimeout)
	defer cancel()
	report, rebuildErr := catalog.RebuildCatalog(ctx, db, catalog.RebuildOptions{VersionID: version})
	if report != nil {
		encoded, marshalErr := json.Marshal(report)
		if marshalErr != nil {
			return marshalErr
		}
		fmt.Fprintln(os.Stdout, string(encoded))
	}
	if rebuildErr != nil {
		return rebuildErr
	}
	return nil
}

func runCatalogIndex(args []string) error {
	flags := flag.NewFlagSet("catalog index", flag.ContinueOnError)
	flags.SetOutput(io.Discard)
	versionID := flags.String("version", "", "application version to index")
	if err := flags.Parse(args); err != nil {
		return usageErrorf("%v", err)
	}
	version := strings.TrimSpace(*versionID)
	if version == "" {
		return usageErrorf("--version is required")
	}
	if !uuid.Valid(version) {
		return usageErrorf("--version must be a UUID")
	}

	db, err := openCatalogDatabase()
	if err != nil {
		return err
	}
	defer db.Close()

	ctx, cancel := context.WithTimeout(context.Background(), commandTimeout)
	defer cancel()
	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return errors.New("cannot start the catalog index transaction")
	}
	defer func() { _ = tx.Rollback() }()
	job, created, err := catalog.New(db, nil).EnqueueIndexVersionTx(ctx, tx, version)
	if err != nil {
		return err
	}
	if err := tx.Commit(); err != nil {
		return errors.New("cannot commit the catalog index transaction")
	}
	payload := struct {
		Status    string `json:"status"`
		VersionID string `json:"version_id"`
		JobID     string `json:"job_id"`
		Created   bool   `json:"created"`
	}{Status: "ok", VersionID: version, JobID: job.ID, Created: created}
	encoded, err := json.Marshal(payload)
	if err != nil {
		return err
	}
	fmt.Fprintln(os.Stdout, string(encoded))
	return nil
}

func runCatalogStatus(args []string) error {
	flags := flag.NewFlagSet("catalog status", flag.ContinueOnError)
	flags.SetOutput(io.Discard)
	if err := flags.Parse(args); err != nil {
		return usageErrorf("%v", err)
	}
	if flags.NArg() > 0 {
		return usageErrorf("unexpected arguments: %s", strings.Join(flags.Args(), " "))
	}
	db, err := openCatalogDatabase()
	if err != nil {
		return err
	}
	defer db.Close()
	ctx, cancel := context.WithTimeout(context.Background(), commandTimeout)
	defer cancel()

	payload := struct {
		ID               string  `json:"id"`
		State            string  `json:"state"`
		ProjectionEpoch  int64   `json:"projection_epoch"`
		RebuildStartedAt *string `json:"rebuild_started_at"`
		RebuiltAt        *string `json:"rebuilt_at"`
		LastErrorCode    *string `json:"last_error_code"`
		LastErrorAt      *string `json:"last_error_at"`
		ExpectedCount    int     `json:"expected_count"`
		IndexedCount     int     `json:"indexed_count"`
	}{ID: "catalog", State: catalog.CatalogIndexAbsent}

	var (
		started, rebuilt, lastErrorAt sql.NullString
		lastError                     sql.NullString
	)
	err = db.QueryRowContext(ctx, `
		SELECT state, projection_epoch, rebuild_started_at::text, rebuilt_at::text,
		       last_error_code, last_error_at::text, expected_count, indexed_count
		  FROM platform.catalog_index_state
		 WHERE id = 'catalog'`,
	).Scan(&payload.State, &payload.ProjectionEpoch, &started, &rebuilt,
		&lastError, &lastErrorAt, &payload.ExpectedCount, &payload.IndexedCount)
	if err != nil && !errors.Is(err, sql.ErrNoRows) {
		return errors.New("cannot read the catalog index ledger")
	}
	if started.Valid {
		value := started.String
		payload.RebuildStartedAt = &value
	}
	if rebuilt.Valid {
		value := rebuilt.String
		payload.RebuiltAt = &value
	}
	if lastError.Valid {
		value := lastError.String
		payload.LastErrorCode = &value
	}
	if lastErrorAt.Valid {
		value := lastErrorAt.String
		payload.LastErrorAt = &value
	}
	encoded, err := json.Marshal(payload)
	if err != nil {
		return err
	}
	fmt.Fprintln(os.Stdout, string(encoded))
	return nil
}

func openCatalogDatabase() (*sql.DB, error) {
	cfg, err := config.LoadDatabase(config.FromEnv())
	if err != nil {
		return nil, err
	}
	db, err := openDatabase(cfg.DBDSN)
	if err != nil {
		return nil, err
	}
	return db, nil
}
