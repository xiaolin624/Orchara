package execution

import (
	"context"
	"database/sql"
	"strings"
	"testing"

	_ "github.com/jackc/pgx/v5/stdlib"
)

// HARD-1 regression (two-axis review of integration/f13a-f15a-f09-f10): the
// task-input and worker-provenance reads used to discard their query error, so
// a real DB failure produced exactly the same facts as "no fixture"/"no
// attempt". T062's admin attribution read could then report an empty
// attribution instead of failing. A closed handle is a real read failure: both
// reads must return it, never an empty fact.
func TestEvaluationRunAttributionReadsDoNotSwallowDatabaseErrors(t *testing.T) {
	db, err := sql.Open("pgx", "postgres://orchara@127.0.0.1:1/orchara?sslmode=disable")
	if err != nil {
		t.Fatalf("opening the disposed handle: %v", err)
	}
	if err := db.Close(); err != nil {
		t.Fatalf("closing the disposed handle: %v", err)
	}
	runs := &EvaluationRuns{DB: db}
	const runID = "11111111-1111-4111-8111-111111111111"

	artifactID, sha256, err := runs.taskInputOf(context.Background(), runID)
	if err == nil {
		t.Fatalf("a failed task-input read returned no error (id=%q sha256=%q); "+
			"'no fixture' must not hide a read failure", artifactID, sha256)
	}
	if !strings.Contains(err.Error(), "task input") {
		t.Fatalf("the task-input error does not locate the read: %v", err)
	}
	if artifactID != "" || sha256 != "" {
		t.Fatalf("a failed task-input read returned facts: id=%q sha256=%q", artifactID, sha256)
	}

	workerID, unitID, err := runs.workerProvenanceOf(context.Background(), runID)
	if err == nil {
		t.Fatalf("a failed provenance read returned no error (worker=%q unit=%q); "+
			"'no attempt' must not hide a read failure", workerID, unitID)
	}
	if !strings.Contains(err.Error(), "provenance") {
		t.Fatalf("the provenance error does not locate the read: %v", err)
	}
	if workerID != "" || unitID != "" {
		t.Fatalf("a failed provenance read returned facts: worker=%q unit=%q", workerID, unitID)
	}
}
