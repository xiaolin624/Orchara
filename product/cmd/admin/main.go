// Command admin runs the platform's administrative commands.
//
// It deliberately reads its connection from the environment (ORCHARA_PROFILE +
// ORCHARA_DB_DSN) the same way the API does: a DSN on the command line would
// end up in shell history, process listings and support transcripts.
//
// Commands:
//
//	migrate    apply the numbered SQL migrations
//	bootstrap  create the first local administrator (password on stdin only)
//	handover   move unfinished service-account responsibility to another admin
package main

import (
	"bufio"
	"context"
	"database/sql"
	"errors"
	"flag"
	"fmt"
	"io"
	"log"
	"os"
	"strings"
	"time"

	_ "github.com/jackc/pgx/v5/stdlib"
	"golang.org/x/term"

	migrations "orchara/platform/db"
	"orchara/platform/internal/config"
	dbmigrate "orchara/platform/internal/db"
	"orchara/platform/internal/identity"
	"orchara/platform/internal/uuid"
)

const (
	commandTimeout = 5 * time.Minute
	usage          = `usage: orchara-admin <command> [flags]

commands:
  migrate    apply pending SQL migrations (ORCHARA_PROFILE + ORCHARA_DB_DSN)
  bootstrap  create the first local administrator (password read from stdin)
  handover   move unfinished service-account responsibility to another administrator

handover:
  orchara-admin handover --from <principal> --to <principal> --by <principal>

  --from  the departing responsible person (may be disabled)
  --to    the successor: an enabled human administrator
  --by    the acting administrator written to the audit events
`
)

// usageError marks a command-line usage problem (missing/unknown flag,
// malformed id). It exits 2, like the standard flag package, while a refusal
// decided against the database exits 1.
type usageError struct {
	message string
}

func (e *usageError) Error() string { return e.message }

func usageErrorf(format string, args ...any) error {
	return &usageError{message: fmt.Sprintf(format, args...) + "\n\n" + usage}
}

func main() {
	if err := run(os.Args[1:]); err != nil {
		var usageProblem *usageError
		if errors.As(err, &usageProblem) {
			fmt.Fprintf(os.Stderr, "orchara-admin: %v\n", usageProblem)
			os.Exit(2)
		}
		fmt.Fprintf(os.Stderr, "orchara-admin: %v\n", err)
		os.Exit(1)
	}
}

func run(args []string) error {
	if len(args) == 0 {
		return errors.New("missing command; expected 'migrate', 'bootstrap' or 'handover'")
	}
	switch args[0] {
	case "migrate":
		return runMigrate(args[1:])
	case "bootstrap":
		return runBootstrap(args[1:])
	case "handover":
		return runHandover(args[1:])
	case "help", "-h", "--help":
		fmt.Fprint(os.Stdout, usage)
		return nil
	default:
		return fmt.Errorf("unknown command %q; expected 'migrate', 'bootstrap' or 'handover'", args[0])
	}
}

func runMigrate(args []string) error {
	flags := flag.NewFlagSet("migrate", flag.ContinueOnError)
	migrationsDir := flags.String(
		"migrations-dir", "",
		"apply SQL files from this directory instead of the embedded chain (tests only)",
	)
	if err := flags.Parse(args); err != nil {
		return err
	}

	cfg, err := config.LoadDatabase(config.FromEnv())
	if err != nil {
		return err
	}
	db, err := openDatabase(cfg.DBDSN)
	if err != nil {
		return err
	}
	defer db.Close()

	ctx, cancel := context.WithTimeout(context.Background(), commandTimeout)
	defer cancel()
	if err := db.PingContext(ctx); err != nil {
		// The DSN is not echoed: it carries the password.
		return errors.New("cannot reach the database")
	}

	chain, err := loadChain(*migrationsDir)
	if err != nil {
		return err
	}
	logger := log.New(os.Stdout, "orchara-admin ", log.LstdFlags|log.LUTC)
	if _, err := dbmigrate.Run(ctx, db, chain, logger); err != nil {
		return err
	}
	return nil
}

func runBootstrap(args []string) error {
	flags := flag.NewFlagSet("bootstrap", flag.ContinueOnError)
	login := flags.String("login", "", "login of the first administrator")
	displayName := flags.String("display-name", "", "display name of the first administrator")
	if err := flags.Parse(args); err != nil {
		return err
	}
	if strings.TrimSpace(*login) == "" || strings.TrimSpace(*displayName) == "" {
		return errors.New("--login and --display-name are required")
	}

	password, err := readPassword(os.Stdin)
	if err != nil {
		return err
	}

	cfg, err := config.LoadDatabase(config.FromEnv())
	if err != nil {
		return err
	}
	db, err := openDatabase(cfg.DBDSN)
	if err != nil {
		return err
	}
	defer db.Close()

	ctx, cancel := context.WithTimeout(context.Background(), commandTimeout)
	defer cancel()
	if err := db.PingContext(ctx); err != nil {
		return errors.New("cannot reach the database")
	}

	principalID, err := identity.Bootstrap(ctx, db, *login, *displayName, password)
	if err != nil {
		return err
	}
	// Prints the principal id and login only. The password is never echoed.
	fmt.Printf("bootstrapped principal %s login=%s\n", principalID, strings.TrimSpace(*login))
	return nil
}

// runHandover moves unfinished service-account responsibility from one person
// to another.
//
// The three ids are validated for shape on the command line (a malformed UUID
// can never name a principal, so it is a usage error, exit 2). The semantic
// checks — every principal exists, the successor and the actor are enabled human
// administrators, --from and --to differ — happen in one transaction inside
// identity.Handover, under the same advisory lock as role changes and
// disable/enable. A refusal prints the reason and exits 1 without changing the
// database.
func runHandover(args []string) error {
	flags := flag.NewFlagSet("handover", flag.ContinueOnError)
	// The flag package's own output is discarded; every message goes through
	// usageErrorf so there is exactly one usage block on stderr.
	flags.SetOutput(io.Discard)
	from := flags.String("from", "", "principal id of the departing responsible user")
	to := flags.String("to", "", "principal id of the successor (an enabled human administrator)")
	by := flags.String("by", "", "principal id of the acting administrator (audit actor)")
	if err := flags.Parse(args); err != nil {
		return usageErrorf("%v", err)
	}

	fromID := strings.TrimSpace(*from)
	toID := strings.TrimSpace(*to)
	byID := strings.TrimSpace(*by)
	// Deterministic order so a command line with several problems reports the
	// first one (--from, then --to, then --by).
	for _, required := range []struct {
		name  string
		value string
	}{
		{"--from", fromID},
		{"--to", toID},
		{"--by", byID},
	} {
		if required.value == "" {
			return usageErrorf("%s is required", required.name)
		}
		if !uuid.Valid(required.value) {
			return usageErrorf("%s must be a UUID", required.name)
		}
	}

	cfg, err := config.LoadDatabase(config.FromEnv())
	if err != nil {
		return err
	}
	db, err := openDatabase(cfg.DBDSN)
	if err != nil {
		return err
	}
	defer db.Close()

	ctx, cancel := context.WithTimeout(context.Background(), commandTimeout)
	defer cancel()
	if err := db.PingContext(ctx); err != nil {
		// The DSN is not echoed: it carries the password.
		return errors.New("cannot reach the database")
	}

	accounts, err := identity.New(db).Handover(ctx, byID, fromID, toID)
	if err != nil {
		return err
	}
	// The count and one line per moved account (id and display name). No secret
	// is printed: a handover never touches a credential.
	fmt.Printf("handover: %d service account(s) reassigned from %s to %s\n",
		len(accounts), fromID, toID)
	for _, account := range accounts {
		fmt.Printf("%s\t%s\n", account.ID, account.DisplayName)
	}
	return nil
}

func openDatabase(dsn string) (*sql.DB, error) {
	db, err := sql.Open("pgx", dsn)
	if err != nil {
		return nil, errors.New("cannot open the database handle")
	}
	db.SetMaxOpenConns(2)
	db.SetConnMaxLifetime(5 * time.Minute)
	return db, nil
}

func loadChain(dir string) ([]dbmigrate.Migration, error) {
	if dir != "" {
		return dbmigrate.LoadFS(os.DirFS(dir), ".")
	}
	return dbmigrate.LoadFS(migrations.Files, "migrations")
}

// readPassword reads the password without echoing it.
//
// A terminal gets the hidden x/term reader. A pipe (tests, CI, a secrets file
// on stdin) reads one plain line: that is not an echo, the bytes never reach a
// terminal or a log.
func readPassword(stdin *os.File) (string, error) {
	fd := int(stdin.Fd())
	if term.IsTerminal(fd) {
		fmt.Fprint(os.Stderr, "password: ")
		raw, err := term.ReadPassword(fd)
		fmt.Fprintln(os.Stderr)
		if err != nil {
			return "", fmt.Errorf("reading the password from the terminal: %w", err)
		}
		return string(raw), nil
	}
	line, err := bufio.NewReader(stdin).ReadString('\n')
	if err != nil && !errors.Is(err, io.EOF) {
		return "", fmt.Errorf("reading the password from stdin: %w", err)
	}
	return strings.TrimRight(line, "\r\n"), nil
}
