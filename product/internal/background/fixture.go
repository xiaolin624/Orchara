package background

// Synthetic model fixtures (F00B/T024 review fix).
//
// model_validate must never read a business user's content: T024's acceptance
// says "model_validate 只用对应候选和合成 fixture" and BACKGROUND-JOBS.md §8
// says the platform must not try a model on a user's正文. An artifact the
// uploader produced (`report`, `log`, `receipt`, `parsed_data`) is business
// content; only a server-side synthetic fixture may be handed to a model.
//
// The marker is the artifact purpose `model_fixture`. It deliberately is NOT one
// of the contract's upload purposes (`report`/`log`/`receipt`/`parsed_data`), so
// a normal uploader cannot create one: the only registration paths in this batch
// are the administrative/seed path documented in the T024 report (and used by
// the tests), and F06 owns the real fixture-registration UI. Enqueue and the
// model-calls endpoint both re-check the marker, so a business artifact cannot
// be substituted by a stale or hand-written job row either.
//
// F06 replaces the registration path, not this predicate: an internal fixture
// is still synthetic by definition, and a user upload must never become one.

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"strings"
)

const (
	// PurposeModelFixture marks an artifact as a server-registered synthetic
	// model fixture. It is not an upload purpose.
	PurposeModelFixture = "model_fixture"
)

var (
	// ErrFixtureNotSynthetic means the job's input artifact exists but is not a
	// synthetic model fixture (for example the uploader's parsed_data).
	ErrFixtureNotSynthetic = errors.New("the artifact is not a synthetic model fixture")
	// ErrFixtureNotOwned means the fixture artifact belongs to another principal
	// than the job owner. It is never read in that case.
	ErrFixtureNotOwned = errors.New("the fixture artifact belongs to another principal")
)

// RequireSyntheticFixtureTx validates the input artifact of a model_validate
// enqueue inside the caller's transaction.
//
// It performs the same three checks the job would rely on later, but early: the
// purpose marker, ownership by the job owner, and readiness. A missing artifact
// is ErrArtifactNotFound. An empty input artifact means "no fixture" and is left
// for the handler/model-call endpoint to refuse with fixture_missing; enqueue
// does not invent one.
func RequireSyntheticFixtureTx(
	ctx context.Context,
	tx *sql.Tx,
	inputArtifactID, ownerID string,
) error {
	artifactID := strings.TrimSpace(inputArtifactID)
	if artifactID == "" {
		return nil
	}
	if tx == nil {
		return errors.New("RequireSyntheticFixtureTx requires a transaction")
	}
	var (
		purpose string
		state   string
		owner   string
		deleted sql.NullTime
	)
	err := tx.QueryRowContext(ctx, `
		SELECT purpose, state, owner_id::text, deleted_at
		  FROM platform.artifacts
		 WHERE id = $1::uuid
		 FOR SHARE`, artifactID,
	).Scan(&purpose, &state, &owner, &deleted)
	if errors.Is(err, sql.ErrNoRows) {
		return ErrArtifactNotFound
	}
	if err != nil {
		return fmt.Errorf("reading the fixture artifact: %w", err)
	}
	if purpose != PurposeModelFixture {
		return fmt.Errorf("%w: purpose is %q", ErrFixtureNotSynthetic, purpose)
	}
	if !strings.EqualFold(owner, ownerID) {
		return ErrFixtureNotOwned
	}
	if deleted.Valid || state != "ready" {
		return ErrArtifactNotReady
	}
	return nil
}
