// Command admin deletion: export and replay the backup deletion ledger.
//
// F11/T072. The ledger is the restore-time control: OPERATIONS.md §46 says a
// restore is (1) restore into an isolated environment, (2) verify DB/object
// consistency and decryption, (3) replay the deletion ledger / disable revoked
// keys, ... before reads open. These two subcommands are that step:
//
//	orchara-admin deletion export --file ledger.jsonl
//	    Verify the live hash chain and write a self-describing JSONL export
//	    (header with count+head_sha256, then one entry per line). The file is
//	    the independently retainable artifact.
//
//	orchara-admin deletion replay --file ledger.jsonl [--check-only]
//	    Verify the file's hash chain and re-apply every tombstone decision that
//	    is still alive in this database. --check-only verifies and counts
//	    without writing. A broken chain is refused before anything changes;
//	    a second replay appends nothing.
//
//	orchara-admin deletion alerts [--deadline 24h] [--limit 100]
//	    F11/T076: the read-only cleanup alert surface. It prints one JSON report
//	    and exits 0 when no deletion/cleanup job is past its deadline or failed,
//	    and 3 when at least one is (1 = the pass could not run, 2 = usage). It
//	    never writes a row: the tombstone remains the read authority.
//
// One JSON line goes to stdout on success; a refusal goes to stderr and exits 1;
// a usage problem exits 2. Credentials are never printed (the DSN comes from the
// environment, like every other admin command).
package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"

	"orchara/platform/internal/config"
	"orchara/platform/internal/deletion"
)

const deletionUsage = `usage: orchara-admin deletion <export|replay|alerts> [flags]

export:
  orchara-admin deletion export --file PATH
    Verify the live hash chain and write one JSONL ledger export to PATH.
    The file header records the entry count and head hash; every entry carries
    its hash and the previous entry's hash. This file is the independently
    retainable deletion ledger.

replay:
  orchara-admin deletion replay --file PATH [--check-only]
    Verify PATH (a broken hash chain is refused) and re-apply every tombstone
    decision that is still alive in this database, before reads open after a
    restore. --check-only verifies and reports what would be replayed without
    writing anything. A second replay appends nothing.

alerts:
  orchara-admin deletion alerts [--deadline 24h] [--limit 100]
    Read-only alert surface for deletion/cleanup jobs past their deadline.
    One JSON report on stdout. Exit 0 = no alert, 3 = at least one alert,
    1 = the pass could not run, 2 = usage. It never writes and never deletes.
`

func runDeletion(args []string) error {
	if len(args) == 0 {
		return usageErrorf("missing deletion subcommand; expected 'export', 'replay' or 'alerts'")
	}
	switch args[0] {
	case "export":
		return runDeletionExport(args[1:])
	case "replay":
		return runDeletionReplay(args[1:])
	case "alerts":
		return runDeletionAlerts(args[1:])
	default:
		return usageErrorf("unknown deletion subcommand %q; expected 'export', 'replay' or 'alerts'", args[0])
	}
}

type deletionExportReport struct {
	Status     string `json:"status"`
	File       string `json:"file"`
	Entries    int    `json:"entries"`
	HeadSHA256 string `json:"head_sha256"`
	Database   string `json:"database"`
}

func runDeletionExport(args []string) error {
	flags := flag.NewFlagSet("deletion export", flag.ContinueOnError)
	flags.SetOutput(io.Discard)
	file := flags.String("file", "", "path of the JSONL ledger export to write")
	if err := flags.Parse(args); err != nil {
		return usageErrorf("%v", err)
	}
	if strings.TrimSpace(*file) == "" {
		return usageErrorf("--file is required")
	}
	target := strings.TrimSpace(*file)
	db, err := openDeletionDatabase()
	if err != nil {
		return err
	}
	defer db.Close()

	ctx, cancel := context.WithTimeout(context.Background(), commandTimeout)
	defer cancel()
	if err := db.PingContext(ctx); err != nil {
		return errors.New("cannot reach the database")
	}
	var database string
	if err := db.QueryRowContext(ctx, "SELECT current_database()").Scan(&database); err != nil {
		return errors.New("cannot read the database name")
	}

	directory := filepath.Dir(target)
	temporary, err := os.CreateTemp(directory, ".orchara-ledger-*.jsonl")
	if err != nil {
		return fmt.Errorf("creating the ledger export file: %w", err)
	}
	temporaryPath := temporary.Name()
	removeTemporary := true
	defer func() {
		_ = temporary.Close()
		if removeTemporary {
			_ = os.Remove(temporaryPath)
		}
	}()
	header, _, err := deletion.ExportLedger(ctx, db, temporary, database)
	if err != nil {
		return err
	}
	if err := temporary.Sync(); err != nil {
		return fmt.Errorf("flushing the ledger export: %w", err)
	}
	if err := temporary.Close(); err != nil {
		return fmt.Errorf("closing the ledger export: %w", err)
	}
	if err := os.Rename(temporaryPath, target); err != nil {
		return fmt.Errorf("publishing the ledger export: %w", err)
	}
	removeTemporary = false
	return json.NewEncoder(os.Stdout).Encode(deletionExportReport{
		Status:     "ok",
		File:       target,
		Entries:    header.Count,
		HeadSHA256: header.HeadSHA256,
		Database:   database,
	})
}

func runDeletionReplay(args []string) error {
	flags := flag.NewFlagSet("deletion replay", flag.ContinueOnError)
	flags.SetOutput(io.Discard)
	file := flags.String("file", "", "path of the JSONL ledger export to replay")
	checkOnly := flags.Bool("check-only", false, "verify and count, write nothing")
	if err := flags.Parse(args); err != nil {
		return usageErrorf("%v", err)
	}
	if strings.TrimSpace(*file) == "" {
		return usageErrorf("--file is required")
	}
	source, err := os.Open(strings.TrimSpace(*file))
	if err != nil {
		return fmt.Errorf("opening the ledger file: %w", err)
	}
	defer source.Close()
	_, entries, err := deletion.ParseLedgerFile(source)
	if err != nil {
		return err
	}

	db, err := openDeletionDatabase()
	if err != nil {
		return err
	}
	defer db.Close()
	ctx, cancel := context.WithTimeout(context.Background(), commandTimeout)
	defer cancel()
	if err := db.PingContext(ctx); err != nil {
		return errors.New("cannot reach the database")
	}
	result, err := deletion.ReplayLedger(ctx, db, entries, *checkOnly)
	if err != nil {
		return err
	}
	return json.NewEncoder(os.Stdout).Encode(result)
}

// runDeletionAlerts is the F11/T076 cleanup alert surface. It prints the JSON
// report even when it exits non-zero, so a scheduler gets the finding and the
// signal from the same invocation.
func runDeletionAlerts(args []string) error {
	flags := flag.NewFlagSet("deletion alerts", flag.ContinueOnError)
	flags.SetOutput(io.Discard)
	deadline := flags.Duration(
		"deadline", deletion.DefaultCleanupDeadline,
		"how long after the logical delete a cleanup job may still be owed (e.g. 24h)",
	)
	limit := flags.Int("limit", 100, "maximum rows examined per query")
	if err := flags.Parse(args); err != nil {
		return usageErrorf("%v", err)
	}
	if flags.NArg() > 0 {
		return usageErrorf("unexpected arguments: %s", strings.Join(flags.Args(), " "))
	}
	if *deadline <= 0 {
		return usageErrorf("--deadline must be greater than zero")
	}
	if *limit < 1 || *limit > deletion.MaxAlertLimit {
		return usageErrorf("--limit must be between 1 and %d", deletion.MaxAlertLimit)
	}

	db, err := openDeletionDatabase()
	if err != nil {
		return err
	}
	defer db.Close()
	ctx, cancel := context.WithTimeout(context.Background(), commandTimeout)
	defer cancel()
	if err := db.PingContext(ctx); err != nil {
		return errors.New("cannot reach the database")
	}

	report, err := deletion.LoadAlerts(ctx, db, deletion.AlertsOptions{
		Deadline: *deadline,
		Limit:    *limit,
	})
	if err != nil {
		return err
	}
	if err := json.NewEncoder(os.Stdout).Encode(report); err != nil {
		return err
	}
	if len(report.Alerts) == 0 {
		return nil
	}
	// A decided, non-zero signal: the pass ran and found an overdue or failed
	// cleanup job. This is not a command failure, so it carries its own exit code.
	return &exitCodeError{
		code: deletion.AlertsExitCode,
		message: fmt.Sprintf(
			"%d deletion cleanup alert(s) within %s; run the purge/copies passes or inspect the named jobs",
			len(report.Alerts), report.Deadline,
		),
	}
}

// openDeletionDatabase uses the same environment contract as every other admin
// command: ORCHARA_PROFILE + ORCHARA_DB_DSN, never a DSN on argv.
func openDeletionDatabase() (*sql.DB, error) {
	cfg, err := config.LoadDatabase(config.FromEnv())
	if err != nil {
		return nil, err
	}
	return openDatabase(cfg.DBDSN)
}
