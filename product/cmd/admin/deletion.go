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

const deletionUsage = `usage: orchara-admin deletion <export|replay> [flags]

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
`

func runDeletion(args []string) error {
	if len(args) == 0 {
		return usageErrorf("missing deletion subcommand; expected 'export' or 'replay'")
	}
	switch args[0] {
	case "export":
		return runDeletionExport(args[1:])
	case "replay":
		return runDeletionReplay(args[1:])
	default:
		return usageErrorf("unknown deletion subcommand %q; expected 'export' or 'replay'", args[0])
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

// openDeletionDatabase uses the same environment contract as every other admin
// command: ORCHARA_PROFILE + ORCHARA_DB_DSN, never a DSN on argv.
func openDeletionDatabase() (*sql.DB, error) {
	cfg, err := config.LoadDatabase(config.FromEnv())
	if err != nil {
		return nil, err
	}
	return openDatabase(cfg.DBDSN)
}
