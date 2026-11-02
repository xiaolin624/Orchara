// Package db applies the platform's numbered SQL migration chain.
//
// The contract, fixed by the F01 design, is:
//
//   - history lives in `platform.schema_migrations(version, name, sha256,
//     applied_at)`; a missing table is created before the first file runs;
//   - each SQL file runs in its own transaction, so a failing file rolls back
//     completely and is not recorded;
//   - an applied file whose checksum changed is a hard error (immutable
//     history), as is an applied version that vanished from the directory;
//   - concurrent runners are serialised with a session-level advisory lock;
//   - an up-to-date database logs `no pending migrations` and exits 0.
//
// The runner never relies on search_path: every statement is schema-qualified.
// The `checkpoint` schema is out of band and is not touched here.
package db

import (
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"fmt"
	"io"
	"io/fs"
	"log"
	"path"
	"regexp"
	"sort"
	"strings"
)

// AdvisoryLockKey serialises migration runners across processes. The value is
// arbitrary but must be stable forever; it is not a credential.
const AdvisoryLockKey int64 = 0x4f52_4348_4152_41 // "ORCHARA" (7 ASCII bytes)

const (
	ledgerSchema = "platform"
	ledgerTable  = "platform.schema_migrations"
)

// fileNamePattern accepts `<version>_<slug>.sql`, e.g. `0006_identity_core.sql`.
var fileNamePattern = regexp.MustCompile(`^([0-9]+)_([A-Za-z0-9][A-Za-z0-9_.-]*)\.sql$`)

// Migration is one immutable SQL file.
type Migration struct {
	Version string
	Name    string // full file name, as recorded in the ledger
	SQL     string
	SHA256  string
}

// Result summarises one Run call.
type Result struct {
	// Applied lists the migration file names applied by this call, in order.
	Applied []string
	// AlreadyApplied is how many files were recorded before this call.
	AlreadyApplied int
}

// Run applies every pending migration in chain order.
//
// It returns an error naming the offending file (and the SQL reason) when a
// file fails, is missing from history, or was modified after being applied.
func Run(ctx context.Context, db *sql.DB, chain []Migration, logger *log.Logger) (Result, error) {
	if logger == nil {
		logger = log.New(io.Discard, "", 0)
	}
	// One dedicated connection: the session advisory lock must be held by the
	// same backend that runs the migrations.
	conn, err := db.Conn(ctx)
	if err != nil {
		return Result{}, fmt.Errorf("acquiring a database connection for migrations: %w", err)
	}
	defer conn.Close()

	if _, err := conn.ExecContext(ctx, fmt.Sprintf("SELECT pg_advisory_lock(%d)", AdvisoryLockKey)); err != nil {
		return Result{}, fmt.Errorf("acquiring the migration advisory lock: %w", err)
	}
	defer func() {
		// The lock is released when the session ends anyway; unlocking keeps the
		// pooled connection from carrying it into unrelated work.
		_, _ = conn.ExecContext(context.Background(), fmt.Sprintf("SELECT pg_advisory_unlock(%d)", AdvisoryLockKey))
	}()

	if _, err := conn.ExecContext(ctx, "CREATE SCHEMA IF NOT EXISTS "+ledgerSchema); err != nil {
		return Result{}, fmt.Errorf("creating schema %s: %w", ledgerSchema, err)
	}
	if _, err := conn.ExecContext(ctx, `CREATE TABLE IF NOT EXISTS `+ledgerTable+` (
		version    text PRIMARY KEY,
		name       text NOT NULL,
		sha256     text NOT NULL,
		applied_at timestamptz NOT NULL DEFAULT now()
	)`); err != nil {
		return Result{}, fmt.Errorf("creating the migration ledger: %w", err)
	}

	applied, err := loadApplied(ctx, conn)
	if err != nil {
		return Result{}, err
	}

	known := make(map[string]Migration, len(chain))
	for _, migration := range chain {
		known[migration.Version] = migration
	}

	// Immutable history, both directions: a recorded file must still exist and
	// must still hash to the recorded value.
	for version, record := range applied {
		migration, ok := known[version]
		if !ok {
			return Result{}, fmt.Errorf(
				"applied migration %s (version %s) is missing from the migration directory; history cannot be rebuilt",
				record.Name, version)
		}
		if migration.SHA256 != record.SHA256 {
			return Result{}, fmt.Errorf(
				"migration %s was modified after it was applied: checksum mismatch (recorded %s, file %s); applied migrations are immutable",
				migration.Name, record.SHA256, migration.SHA256)
		}
	}

	result := Result{AlreadyApplied: len(applied)}
	for _, migration := range chain {
		if _, done := applied[migration.Version]; done {
			continue
		}
		if err := applyOne(ctx, conn, migration); err != nil {
			return result, err
		}
		logger.Printf("applied %s", migration.Name)
		result.Applied = append(result.Applied, migration.Name)
	}

	if len(result.Applied) == 0 {
		logger.Print("no pending migrations")
		return result, nil
	}
	logger.Printf("%d migration(s) applied", len(result.Applied))
	return result, nil
}

type recorded struct {
	Name   string
	SHA256 string
}

func loadApplied(ctx context.Context, conn *sql.Conn) (map[string]recorded, error) {
	rows, err := conn.QueryContext(ctx, "SELECT version, name, sha256 FROM "+ledgerTable)
	if err != nil {
		return nil, fmt.Errorf("reading the migration ledger: %w", err)
	}
	defer rows.Close()

	applied := make(map[string]recorded)
	for rows.Next() {
		var version string
		var record recorded
		if err := rows.Scan(&version, &record.Name, &record.SHA256); err != nil {
			return nil, fmt.Errorf("reading the migration ledger: %w", err)
		}
		applied[version] = record
	}
	return applied, rows.Err()
}

func applyOne(ctx context.Context, conn *sql.Conn, migration Migration) error {
	tx, err := conn.BeginTx(ctx, nil)
	if err != nil {
		return fmt.Errorf("starting migration %s: %w", migration.Name, err)
	}
	defer func() { _ = tx.Rollback() }()

	if _, err := tx.ExecContext(ctx, migration.SQL); err != nil {
		// The transaction is rolled back by the deferred Rollback: the file's
		// partial DDL never survives a failure.
		return fmt.Errorf("migration %s failed: %w", migration.Name, err)
	}
	if _, err := tx.ExecContext(ctx,
		"INSERT INTO "+ledgerTable+" (version, name, sha256) VALUES ($1, $2, $3)",
		migration.Version, migration.Name, migration.SHA256,
	); err != nil {
		return fmt.Errorf("recording migration %s: %w", migration.Name, err)
	}
	if err := tx.Commit(); err != nil {
		return fmt.Errorf("committing migration %s: %w", migration.Name, err)
	}
	return nil
}

// LoadFS reads `*.sql` files from dir inside fsys and returns them in version
// order. Non-SQL files are ignored; a malformed or duplicate version is fatal.
func LoadFS(fsys fs.FS, dir string) ([]Migration, error) {
	entries, err := fs.ReadDir(fsys, dir)
	if err != nil {
		return nil, fmt.Errorf("reading migrations from %s: %w", dir, err)
	}

	chain := make([]Migration, 0, len(entries))
	seen := make(map[string]string, len(entries))
	for _, entry := range entries {
		if entry.IsDir() || !strings.HasSuffix(entry.Name(), ".sql") {
			continue
		}
		version, err := versionOf(entry.Name())
		if err != nil {
			return nil, err
		}
		if previous, duplicate := seen[version]; duplicate {
			return nil, fmt.Errorf("duplicate migration version %s: %s and %s", version, previous, entry.Name())
		}
		data, err := fs.ReadFile(fsys, path.Join(dir, entry.Name()))
		if err != nil {
			return nil, fmt.Errorf("reading migration %s: %w", entry.Name(), err)
		}
		sum := sha256.Sum256(data)
		chain = append(chain, Migration{
			Version: version,
			Name:    entry.Name(),
			SQL:     string(data),
			SHA256:  hex.EncodeToString(sum[:]),
		})
		seen[version] = entry.Name()
	}

	if len(chain) == 0 {
		return nil, fmt.Errorf("no migrations found in %s", dir)
	}
	sort.Slice(chain, func(i, j int) bool { return chain[i].Version < chain[j].Version })
	return chain, nil
}

func versionOf(name string) (string, error) {
	match := fileNamePattern.FindStringSubmatch(name)
	if match == nil {
		return "", fmt.Errorf("migration file %q does not match <version>_<slug>.sql", name)
	}
	return match[1], nil
}
