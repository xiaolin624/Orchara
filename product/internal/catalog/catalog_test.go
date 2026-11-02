package catalog

import (
	"context"
	"database/sql"
	"database/sql/driver"
	"errors"
	"io"
	"strings"
	"sync"
	"testing"
	"time"

	"orchara/platform/internal/policy"
)

// captureDriver is a database/sql driver that records the query the reader
// sends and returns an empty result set. It exists for one narrow unit test:
// the list query must carry the caller's clock (not the database's now()), and
// the vocabulary constants must be the query parameters. The real SQL path is
// covered end to end by product/tests/f01/test_t019_*.py; this driver never
// stands in for that.
type captureDriver struct {
	mu    sync.Mutex
	query string
	args  []driver.NamedValue
}

var (
	captureOnce  sync.Once
	captureState = &captureDriver{}
)

func (d *captureDriver) Open(string) (driver.Conn, error) { return &captureConn{d: d}, nil }

func (d *captureDriver) record(query string, args []driver.NamedValue) {
	d.mu.Lock()
	defer d.mu.Unlock()
	d.query = query
	d.args = append([]driver.NamedValue(nil), args...)
}

func (d *captureDriver) snapshot() (string, []driver.NamedValue) {
	d.mu.Lock()
	defer d.mu.Unlock()
	return d.query, append([]driver.NamedValue(nil), d.args...)
}

type captureConn struct {
	d *captureDriver
}

func (c *captureConn) Prepare(string) (driver.Stmt, error) {
	return nil, errors.New("catalog capture driver does not prepare statements")
}

func (c *captureConn) Close() error { return nil }

func (c *captureConn) Begin() (driver.Tx, error) {
	return nil, errors.New("catalog capture driver has no transactions")
}

func (c *captureConn) QueryContext(
	_ context.Context,
	query string,
	args []driver.NamedValue,
) (driver.Rows, error) {
	c.d.record(query, args)
	return &captureRows{}, nil
}

type captureRows struct{}

func (r *captureRows) Columns() []string {
	return []string{"id", "name", "active_version_id", "created_at"}
}

func (r *captureRows) Close() error { return nil }

func (r *captureRows) Next([]driver.Value) error { return io.EOF }

func TestListVisibleQueriesWithTheProvidedClockAndVocabulary(t *testing.T) {
	captureOnce.Do(func() { sql.Register("catalog-clock-capture", captureState) })
	db, err := sql.Open("catalog-clock-capture", "")
	if err != nil {
		t.Fatalf("open capture database: %v", err)
	}
	defer db.Close()

	clock := time.Date(2026, 11, 2, 3, 4, 5, 0, time.UTC)
	reader := New(db, nil)
	items, next, err := reader.ListVisible(
		context.Background(),
		"11111111-1111-4111-8111-111111111111",
		"",
		5,
		clock,
	)
	if err != nil {
		t.Fatalf("list visible through the capture driver: %v", err)
	}
	if len(items) != 0 || next != nil {
		t.Fatalf("an empty result set should be an empty page: %v %v", items, next)
	}

	query, args := captureState.snapshot()
	if !strings.Contains(query, "g.expires_at > $5") {
		t.Fatalf("the list query does not compare the grant expiry with $5:\n%s", query)
	}
	if strings.Contains(query, "now()") {
		t.Fatalf("the list query still reads the database clock:\n%s", query)
	}
	if len(args) != 7 {
		t.Fatalf("expected 7 query arguments, got %d: %+v", len(args), args)
	}
	if got, ok := args[4].Value.(time.Time); !ok || !got.Equal(clock) {
		t.Fatalf("argument 5 is not the provided clock: %#v", args[4].Value)
	}
	if args[5].Value != policy.ResourceApplication {
		t.Fatalf("argument 6 is not the application resource type: %#v", args[5].Value)
	}
	if args[6].Value != policy.ActionRead {
		t.Fatalf("argument 7 is not the read action: %#v", args[6].Value)
	}
}
