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
	"strings"

	"orchara/platform/internal/background"
	"orchara/platform/internal/config"
	"orchara/platform/internal/uuid"
)

// runBackgroundJobs is the administrative control interface for background
// jobs. Enqueue is the only operation: there is no "run this command" verb, and
// --kind is validated against the fixed whitelist, so a shell string can never
// become a handler.
func runBackgroundJobs(args []string) error {
	if len(args) == 0 {
		return usageErrorf("missing background-jobs command; expected 'enqueue'")
	}
	switch args[0] {
	case "enqueue":
		return runBackgroundEnqueue(args[1:])
	case "help", "-h", "--help":
		fmt.Fprint(os.Stdout, backgroundJobsUsage)
		return nil
	default:
		return usageErrorf("unknown background-jobs command %q; expected 'enqueue'", args[0])
	}
}

const backgroundJobsUsage = `usage: orchara-admin background-jobs enqueue --kind KIND --target-id UUID \
    --owner-id UUID --dedup-key KEY [--expected-revision N] [--expected-sha256 HEX] \
    [--input-artifact-id UUID]

The job is created in one transaction. (kind, dedup_key) is the idempotency key:
   same target/owner/expected facts -> the existing job is printed, exit 0
   different facts                  -> dedup conflict, exit 1, nothing written

--kind is one of the eight fixed handlers (artifact_finalize, route_request,
tool_build, document_ingest, application_evaluate, model_validate, delete_content,
catalog_index). T022 compiles a handler for artifact_finalize only; a job of
another whitelisted kind stays ready until its handler ticket lands.

--expected-revision is the source version the work must still match when it
finishes (artifacts.revision for artifact_finalize). Passing it is strongly
recommended: without it the source-moved guard has nothing to compare.
`

func runBackgroundEnqueue(args []string) error {
	flags := flag.NewFlagSet("background-jobs enqueue", flag.ContinueOnError)
	flags.SetOutput(io.Discard)
	kindFlag := flags.String("kind", "", "fixed handler kind")
	targetID := flags.String("target-id", "", "id of the resource the job works on")
	ownerID := flags.String("owner-id", "", "principal id that owns the job")
	dedupKey := flags.String("dedup-key", "", "idempotency key, unique per kind")
	expectedRevision := flags.Int64("expected-revision", 0, "source revision the job expects")
	expectedSHA := flags.String("expected-sha256", "", "expected content hash")
	inputArtifact := flags.String("input-artifact-id", "", "optional input artifact id")
	if err := flags.Parse(args); err != nil {
		return usageErrorf("%v", err)
	}
	if flags.NArg() > 0 {
		return usageErrorf("unexpected arguments: %s", strings.Join(flags.Args(), " "))
	}

	kind := strings.TrimSpace(*kindFlag)
	if kind == "" {
		return usageErrorf("--kind is required")
	}
	if !background.KnownKind(kind) {
		return usageErrorf("unknown kind %q; expected one of %s", kind, strings.Join(kindNames(), ", "))
	}
	target := strings.TrimSpace(*targetID)
	if target == "" {
		return usageErrorf("--target-id is required")
	}
	if !uuid.Valid(target) {
		return usageErrorf("--target-id must be a UUID")
	}
	owner := strings.TrimSpace(*ownerID)
	if owner == "" {
		return usageErrorf("--owner-id is required")
	}
	if !uuid.Valid(owner) {
		return usageErrorf("--owner-id must be a UUID")
	}
	dedup := strings.TrimSpace(*dedupKey)
	if dedup == "" {
		return usageErrorf("--dedup-key is required")
	}

	params := background.EnqueueParams{
		Kind:     background.Kind(kind),
		TargetID: target,
		OwnerID:  owner,
		DedupKey: dedup,
	}
	revisionSupplied := false
	flags.Visit(func(visited *flag.Flag) {
		if visited.Name == "expected-revision" {
			revisionSupplied = true
		}
	})
	if revisionSupplied {
		// An explicitly supplied 0 is a usage error, never "no expected
		// revision": 0 cannot name an artifacts.revision, and silently ignoring
		// it would drop the source-moved guard.
		if *expectedRevision < 1 {
			return usageErrorf("--expected-revision must be greater than zero")
		}
		value := *expectedRevision
		params.ExpectedRevision = &value
	}
	sha := strings.ToLower(strings.TrimSpace(*expectedSHA))
	if sha != "" {
		if len(sha) != 64 || !isLowerHex(sha) {
			return usageErrorf("--expected-sha256 must be 64 lowercase hexadecimal characters")
		}
		params.ExpectedSHA256 = sha
	}
	input := strings.TrimSpace(*inputArtifact)
	if input != "" {
		if !uuid.Valid(input) {
			return usageErrorf("--input-artifact-id must be a UUID")
		}
		params.InputArtifactID = input
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

	job, _, err := enqueueInTransaction(ctx, db, params)
	if err != nil {
		return err
	}
	payload := struct {
		ID    string `json:"id"`
		State string `json:"state"`
		Kind  string `json:"kind"`
	}{ID: job.ID, State: string(job.State), Kind: string(job.Kind)}
	encoded, err := json.Marshal(payload)
	if err != nil {
		return err
	}
	fmt.Fprintln(os.Stdout, string(encoded))
	return nil
}

// enqueueInTransaction is EnqueueTx with the administrative command's own
// transaction. It exists instead of calling Controller.Enqueue so the CLI has no
// object store or model configuration to load.
//
// A model_validate job names a synthetic fixture; the CLI refuses a business
// artifact, a foreign artifact or a not-ready artifact before the job row is
// written (the same checks run again in the model-calls endpoint, which re-reads
// the row after a claim). This is the administrative registration boundary
// documented in the T024 report: F06 owns the real fixture UI, and no user
// upload purpose can produce model_fixture.
func enqueueInTransaction(
	ctx context.Context,
	db *sql.DB,
	params background.EnqueueParams,
) (*background.Job, bool, error) {
	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return nil, false, errors.New("cannot start the enqueue transaction")
	}
	defer func() { _ = tx.Rollback() }()
	if params.Kind == background.KindModelValidate && params.InputArtifactID != "" {
		if err := background.RequireSyntheticFixtureTx(
			ctx, tx, params.InputArtifactID, params.OwnerID,
		); err != nil {
			return nil, false, fixtureRefusal(err)
		}
	}
	job, created, err := background.EnqueueTx(ctx, tx, params)
	if err != nil {
		return nil, false, err
	}
	if err := tx.Commit(); err != nil {
		return nil, false, errors.New("cannot commit the enqueue transaction")
	}
	return job, created, nil
}

// fixtureRefusal turns the typed fixture errors into the operator-facing reason.
// These are data refusals (exit 1), not command-line shape errors (exit 2): the
// flags are well formed, the artifact the operator named is not usable.
func fixtureRefusal(err error) error {
	switch {
	case errors.Is(err, background.ErrArtifactNotFound):
		return errors.New("--input-artifact-id does not name an artifact")
	case errors.Is(err, background.ErrFixtureNotSynthetic):
		return fmt.Errorf(
			"--input-artifact-id must be an administrative synthetic model fixture (purpose %q); "+
				"a user-uploaded artifact may never be given to a model",
			background.PurposeModelFixture,
		)
	case errors.Is(err, background.ErrFixtureNotOwned):
		return errors.New("--input-artifact-id belongs to a different principal than --owner-id")
	case errors.Is(err, background.ErrArtifactNotReady):
		return errors.New("--input-artifact-id must be ready and not deleted")
	default:
		return err
	}
}

func kindNames() []string {
	kinds := background.AllKinds()
	names := make([]string, 0, len(kinds))
	for _, kind := range kinds {
		names = append(names, string(kind))
	}
	return names
}

func isLowerHex(value string) bool {
	for index := 0; index < len(value); index++ {
		char := value[index]
		if (char < '0' || char > '9') && (char < 'a' || char > 'f') {
			return false
		}
	}
	return true
}
