package capabilities

// T101 acceptance finding: the sealed-manifest ownership rule.
//
// F06/T041 required the manifest artifact's owner to equal the Run owner. That is
// true for a debug snapshot (the author seals it), but an F08/T074 published Run
// pins the *version's* manifest artifact, which the publisher sealed
// (SubmitPublication is owner-only), while the Run owner is the granted requester.
// The rule then refused every capability call of a published Run whose owner was
// not the publisher (503 manifest_unavailable).
//
// The fix relaxes only the ownership equality for `run.Kind == "published"`: the
// artifact must still be ready, its digest must still equal the snapshot digest
// and its bytes must still hash to `runs.manifest_sha256`. These tests pin all
// three halves of that decision.
//
// The row-returning driver below is a narrow unit seam: the production SQL path
// (the real database, object bytes and mTLS capability call) is covered end to end
// by tests/f12/journeys/j101_service_key_write.py (J6).

import (
	"bytes"
	"context"
	"crypto/sha256"
	"database/sql"
	"database/sql/driver"
	"encoding/hex"
	"errors"
	"io"
	"strings"
	"sync"
	"testing"

	"orchara/platform/internal/objectstore"
)

const (
	manifestTestRunID     = "11111111-1111-4111-8111-111111111111"
	manifestTestToken     = "22222222-2222-4222-8222-222222222222"
	manifestTestOwner     = "33333333-3333-4333-8333-333333333333"
	manifestTestPublisher = "44444444-4444-4444-8444-444444444444"
	manifestTestArtifact  = "55555555-5555-4555-8555-555555555555"
	manifestTestSnapshot  = "66666666-6666-4666-8666-666666666666"
)

// manifestRowDriver serves exactly one canned `loadAuthorizedRun` row.
type manifestRowDriver struct {
	mu  sync.Mutex
	row []driver.Value
}

var (
	manifestRowOnce  sync.Once
	manifestRowState = &manifestRowDriver{}
)

func (d *manifestRowDriver) set(row []driver.Value) {
	d.mu.Lock()
	defer d.mu.Unlock()
	d.row = append([]driver.Value(nil), row...)
}

func (d *manifestRowDriver) Open(string) (driver.Conn, error) {
	return &manifestRowConn{driver: d}, nil
}

type manifestRowConn struct {
	driver *manifestRowDriver
}

func (c *manifestRowConn) Prepare(string) (driver.Stmt, error) {
	return nil, errors.New("manifest row driver does not prepare statements")
}

func (c *manifestRowConn) Close() error { return nil }

func (c *manifestRowConn) Begin() (driver.Tx, error) {
	return nil, errors.New("manifest row driver has no transactions")
}

func (c *manifestRowConn) QueryContext(
	_ context.Context, _ string, _ []driver.NamedValue,
) (driver.Rows, error) {
	c.driver.mu.Lock()
	defer c.driver.mu.Unlock()
	return &manifestRowRows{row: append([]driver.Value(nil), c.driver.row...)}, nil
}

type manifestRowRows struct {
	row  []driver.Value
	done bool
}

func (r *manifestRowRows) Columns() []string {
	columns := make([]string, len(r.row))
	for index := range columns {
		columns[index] = "c" + string(rune('a'+index))
	}
	return columns
}

func (r *manifestRowRows) Close() error { return nil }

func (r *manifestRowRows) Next(dest []driver.Value) error {
	if r.done {
		return io.EOF
	}
	if len(dest) != len(r.row) {
		return errors.New("manifest row column count changed")
	}
	copy(dest, r.row)
	r.done = true
	return nil
}

// loadAuthorizedRow is the exact 16-column projection `loadAuthorizedRun` scans,
// in its scan order.
func loadAuthorizedRow(
	kind, snapshotSHA, runSHA, artifactOwner, artifactState, artifactKey string,
	artifactSHA any,
	live bool,
) []driver.Value {
	return []driver.Value{
		manifestTestOwner,    // r.owner_id
		kind,                 // r.kind
		"running",            // r.state
		nil,                  // r.cancel_requested_at
		"1.00",               // r.budget_limit::text
		"USD",                // r.currency
		runSHA,               // r.manifest_sha256
		manifestTestSnapshot, // s.id
		snapshotSHA,          // s.manifest_sha256
		manifestTestArtifact, // s.manifest_artifact_id
		artifactOwner,        // a.owner_id
		artifactState,        // a.state
		artifactKey,          // a.object_key
		artifactSHA,          // a.sha256
		nil,                  // p.disabled_at
		live,                 // the live-attempt EXISTS probe
	}
}

func manifestSHA256Hex(payload []byte) string {
	sum := sha256.Sum256(payload)
	return hex.EncodeToString(sum[:])
}

func newManifestTestService(t *testing.T) *Service {
	t.Helper()
	manifestRowOnce.Do(func() {
		sql.Register("capabilities-manifest-row", manifestRowState)
	})
	db, err := sql.Open("capabilities-manifest-row", "")
	if err != nil {
		t.Fatalf("open the manifest row driver: %v", err)
	}
	t.Cleanup(func() { _ = db.Close() })
	store, err := objectstore.Open(t.TempDir())
	if err != nil {
		t.Fatalf("open the test object store: %v", err)
	}
	return &Service{DB: db, Store: store}
}

func writeManifestObject(t *testing.T, store *objectstore.Store, payload []byte) string {
	t.Helper()
	object, err := store.PutSealed("sealed/"+strings.Repeat("a", 64), bytes.NewReader(payload), 0)
	if err != nil {
		t.Fatalf("seal the test manifest: %v", err)
	}
	return object.Key
}

func assertManifestUnavailable(t *testing.T, capErr *Error) {
	t.Helper()
	if capErr == nil {
		t.Fatal("a broken sealed manifest was accepted")
	}
	if capErr.Status != 503 || capErr.Code != CodeManifestUnavailable {
		t.Fatalf("expected 503 %s, got %d %s (%s)",
			CodeManifestUnavailable, capErr.Status, capErr.Code, capErr.Message)
	}
}

// (a) A published Run may use the version manifest the publisher sealed even when
// the Run owner is the granted requester.
func TestLoadAuthorizedRunAllowsPublishedManifestSealedByItsPublisher(t *testing.T) {
	service := newManifestTestService(t)
	payload := []byte(`{"manifest_version":"1.0","application_id":"t101-manifest-app"}`)
	key := writeManifestObject(t, service.Store, payload)
	digest := manifestSHA256Hex(payload)
	if manifestTestPublisher == manifestTestOwner {
		t.Fatal("the test must use distinct publisher and run-owner principals")
	}
	manifestRowState.set(loadAuthorizedRow(
		"published", digest, digest, manifestTestPublisher, "ready", key, digest, true,
	))

	run, capErr := service.loadAuthorizedRun(context.Background(), manifestTestRunID, manifestTestToken)
	if capErr != nil {
		t.Fatalf("a published Run's publisher-sealed manifest was refused: %d %s (%s)",
			capErr.Status, capErr.Code, capErr.Message)
	}
	if run.Manifest == nil || run.Manifest.ApplicationID != "t101-manifest-app" {
		t.Fatalf("the sealed manifest was not decoded: %+v", run.Manifest)
	}
	if run.Kind != "published" || run.OwnerID != manifestTestOwner {
		t.Fatalf("the loaded facts are wrong: kind=%q owner=%q", run.Kind, run.OwnerID)
	}
	if !strings.EqualFold(run.ManifestSHA256, digest) {
		t.Fatalf("the run manifest digest changed: %q", run.ManifestSHA256)
	}
}

// (b) The relaxed ownership rule must not relax the digest proofs.
func TestLoadAuthorizedRunStillRefusesPublishedManifestDigestMismatches(t *testing.T) {
	service := newManifestTestService(t)
	payload := []byte(`{"manifest_version":"1.0","application_id":"t101-manifest-app"}`)
	key := writeManifestObject(t, service.Store, payload)
	digest := manifestSHA256Hex(payload)
	other := strings.Repeat("f", 64)

	tests := []struct {
		name        string
		snapshotSHA string
		runSHA      string
	}{
		{name: "run and snapshot digests disagree", snapshotSHA: other, runSHA: digest},
		{name: "sealed bytes do not hash to the run digest", snapshotSHA: other, runSHA: other},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			manifestRowState.set(loadAuthorizedRow(
				"published", test.snapshotSHA, test.runSHA, manifestTestPublisher,
				"ready", key, digest, true,
			))
			_, capErr := service.loadAuthorizedRun(
				context.Background(), manifestTestRunID, manifestTestToken,
			)
			assertManifestUnavailable(t, capErr)
		})
	}

	// An artifact that is not ready stays unavailable even for a published Run.
	manifestRowState.set(loadAuthorizedRow(
		"published", digest, digest, manifestTestPublisher, "staging", key, digest, true,
	))
	_, capErr := service.loadAuthorizedRun(
		context.Background(), manifestTestRunID, manifestTestToken,
	)
	assertManifestUnavailable(t, capErr)
}

// (c) The debug rule is unchanged: another principal's artifact is still refused.
func TestLoadAuthorizedRunKeepsTheOwnerEqualityForNonPublishedRuns(t *testing.T) {
	service := newManifestTestService(t)
	payload := []byte(`{"manifest_version":"1.0","application_id":"t101-manifest-app"}`)
	key := writeManifestObject(t, service.Store, payload)
	digest := manifestSHA256Hex(payload)

	for _, kind := range []string{"debug"} {
		manifestRowState.set(loadAuthorizedRow(
			kind, digest, digest, manifestTestPublisher, "ready", key, digest, true,
		))
		_, capErr := service.loadAuthorizedRun(
			context.Background(), manifestTestRunID, manifestTestToken,
		)
		assertManifestUnavailable(t, capErr)
	}
}
