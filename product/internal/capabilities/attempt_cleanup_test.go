package capabilities

// C2.03 (#148): driver-level cancellation and cleanup decisions.
//
// The real ledger behaviour (real PostgreSQL, real HTTP/mTLS, real sealed bytes)
// is covered by tests/f06/test_c203_cancel_cleanup.py. These tests pin the two
// branches the HTTP path cannot schedule deterministically:
//
//   - a caller that is already cancelled before the first physical attempt
//     dispatches nothing and still closes the prepared operation;
//   - a cleanup write that the database refuses is returned as an
//     internal_error associated with the database failure, not swallowed and not
//     relabelled as a clean refusal/cancellation.
//
// The database seam is a narrow recording driver, exactly like the existing
// manifest ownership unit seam; it never pretends to be PostgreSQL.

import (
	"context"
	"database/sql"
	"database/sql/driver"
	"errors"
	"fmt"
	"io"
	"strings"
	"sync"
	"testing"

	"orchara/platform/internal/config"
	"orchara/platform/internal/modelproxy"
)

var errC203DatabaseRefused = errors.New("the database refused the cleanup write")

type c203RecordingDriver struct {
	mu     sync.Mutex
	execs  []string
	failOn string
	row    []driver.Value
}

var (
	c203DriverOnce  sync.Once
	c203DriverState = &c203RecordingDriver{}
)

func (d *c203RecordingDriver) reset() {
	d.mu.Lock()
	defer d.mu.Unlock()
	d.execs = nil
	d.failOn = ""
	d.row = nil
}

func (d *c203RecordingDriver) setRow(values ...driver.Value) {
	d.mu.Lock()
	defer d.mu.Unlock()
	d.row = append([]driver.Value(nil), values...)
}

func (d *c203RecordingDriver) failOnStatement(fragment string) {
	d.mu.Lock()
	defer d.mu.Unlock()
	d.failOn = fragment
}

func (d *c203RecordingDriver) recorded() []string {
	d.mu.Lock()
	defer d.mu.Unlock()
	return append([]string(nil), d.execs...)
}

func (d *c203RecordingDriver) exec(query string, args []driver.NamedValue) (driver.Result, error) {
	d.mu.Lock()
	record := fmt.Sprintf("%s | %v", query, args)
	d.execs = append(d.execs, record)
	failOn := d.failOn
	d.mu.Unlock()
	if failOn != "" && strings.Contains(query, failOn) {
		return nil, errC203DatabaseRefused
	}
	return driver.RowsAffected(1), nil
}

func (d *c203RecordingDriver) Open(string) (driver.Conn, error) {
	return &c203RecordingConn{driver: d}, nil
}

type c203RecordingConn struct {
	driver *c203RecordingDriver
}

func (c *c203RecordingConn) Prepare(string) (driver.Stmt, error) {
	return nil, errors.New("the recording driver does not prepare statements")
}

func (c *c203RecordingConn) Close() error { return nil }

func (c *c203RecordingConn) Begin() (driver.Tx, error) {
	return &c203RecordingTx{driver: c.driver}, nil
}

func (c *c203RecordingConn) ExecContext(
	_ context.Context, query string, args []driver.NamedValue,
) (driver.Result, error) {
	return c.driver.exec(query, args)
}

func (c *c203RecordingConn) QueryContext(
	_ context.Context, _ string, _ []driver.NamedValue,
) (driver.Rows, error) {
	c.driver.mu.Lock()
	row := append([]driver.Value(nil), c.driver.row...)
	c.driver.mu.Unlock()
	return &c203RecordingRows{row: row}, nil
}

type c203RecordingTx struct {
	driver *c203RecordingDriver
}

func (t *c203RecordingTx) Commit() error   { return nil }
func (t *c203RecordingTx) Rollback() error { return nil }

func (t *c203RecordingTx) ExecContext(
	_ context.Context, query string, args []driver.NamedValue,
) (driver.Result, error) {
	return t.driver.exec(query, args)
}

type c203RecordingRows struct {
	row  []driver.Value
	done bool
}

func (r *c203RecordingRows) Columns() []string {
	columns := make([]string, len(r.row))
	for index := range columns {
		columns[index] = fmt.Sprintf("c%d", index)
	}
	return columns
}

func (r *c203RecordingRows) Close() error { return nil }

func (r *c203RecordingRows) Next(dest []driver.Value) error {
	if r.done || len(r.row) == 0 {
		return io.EOF
	}
	if len(dest) != len(r.row) {
		return errors.New("the recording row column count changed")
	}
	copy(dest, r.row)
	r.done = true
	return nil
}

func newC203TestService(t *testing.T) *Service {
	t.Helper()
	c203DriverOnce.Do(func() {
		sql.Register("capabilities-c203-recording", c203DriverState)
	})
	c203DriverState.reset()
	db, err := sql.Open("capabilities-c203-recording", "")
	if err != nil {
		t.Fatalf("open the recording driver: %v", err)
	}
	t.Cleanup(func() {
		_ = db.Close()
		c203DriverState.reset()
	})
	return &Service{
		DB: db,
		Providers: modelproxy.NewProviders(
			config.ProfileDevStub, "", "", func(string) (string, bool) { return "", false },
		),
	}
}

func c203TestSession(t *testing.T, service *Service) *modelAttemptSession {
	t.Helper()
	profile := &ModelProfile{
		ID:              "11111111-1111-4111-8111-111111111111",
		Provider:        "stub",
		ModelID:         "stub-model",
		Config:          map[string]any{"max_output_tokens": float64(16)},
		ValidationState: "passed",
	}
	run := &runContext{
		ID: "22222222-2222-4222-8222-222222222222", Currency: "USD",
	}
	spec := &capabilitySpec{
		Kind:    "model",
		Profile: profile,
		Model:   &ModelParams{Messages: []map[string]any{{"role": "user", "content": "hi"}}},
	}
	input := Input{
		Params: map[string]any{"messages": []any{map[string]any{"role": "user", "content": "hi"}}},
	}
	return newModelAttemptSession(
		service, run, input, spec,
		"33333333-3333-4333-8333-333333333333", false,
	)
}

func c203Contains(records []string, fragment string) bool {
	for _, record := range records {
		if strings.Contains(record, fragment) {
			return true
		}
	}
	return false
}

// A caller that is already cancelled before the first physical attempt must not
// dispatch anything, and the prepared operation must not stay `authorized`: a
// replay would otherwise answer operation_in_progress for a call that never
// happened.
func TestCancelledBeforeTheFirstAttemptDispatchesNothingAndClosesTheOperation(t *testing.T) {
	service := newC203TestService(t)
	session := c203TestSession(t, service)
	cancelled, cancel := context.WithCancel(context.Background())
	cancel()

	calls := 0
	_, err := session.dispatch(cancelled, modelContentHandler{
		Call: func(context.Context, modelAttemptInfo) (modelproxy.ProviderResponse, error) {
			calls++
			return modelproxy.ProviderResponse{}, nil
		},
	})
	if calls != 0 {
		t.Fatalf("a cancelled caller still dispatched %d physical call(s)", calls)
	}
	capErr, ok := AsError(err)
	if !ok {
		t.Fatalf("the cancellation was not a typed capability error: %v", err)
	}
	if capErr.Code != CodeCallerCancelled {
		t.Fatalf("the cancellation code is %q, want %q", capErr.Code, CodeCallerCancelled)
	}
	records := c203DriverState.recorded()
	if c203Contains(records, "INSERT INTO platform.operation_attempts") {
		t.Fatalf("a cancelled pre-call dispatch fabricated an attempt row: %v", records)
	}
	if !c203Contains(records, "caller_cancelled") {
		t.Fatalf("the operation was not closed with the cancellation code: %v", records)
	}
}

// A cleanup write the database refuses must be reported as an unclosed ledger,
// associated with the database error, instead of the clean cancellation or the
// provider refusal that would imply the operation had been closed. The same
// guard covers the post-provider close used while sealing a complete answer.
func TestACleanupWriteFailureIsReportedAsAnUnclosedLedger(t *testing.T) {
	cases := []struct {
		name string
		call func(*Service) *Error
	}{
		{
			name: "pre-dispatch refusal close",
			call: func(service *Service) *Error {
				return service.closeOperation(context.Background(), "op-1", CodeBudgetPriceInvalid)
			},
		},
		{
			name: "cancelled attempt close",
			call: func(service *Service) *Error {
				return service.finishCancelled(context.Background(), "op-1", 1, CodeCallerCancelled)
			},
		},
		{
			name: "terminal provider failure close",
			call: func(service *Service) *Error {
				return service.closeAttemptAndOperation(context.Background(), "op-1", 1, "provider_error")
			},
		},
		{
			name: "complete answer close after a refused seal",
			call: func(service *Service) *Error {
				c203DriverState.setRow("dispatching")
				return service.closeAfterProviderFailure(
					context.Background(), "op-1", 1, CodeInternal, nil, nil,
				)
			},
		},
	}
	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			service := newC203TestService(t)
			c203DriverState.failOnStatement("UPDATE platform.operations")
			capErr := testCase.call(service)
			if capErr == nil {
				t.Fatal("a cleanup write that the database refused was reported as a clean close")
			}
			if capErr.Status != 500 || capErr.Code != CodeInternal {
				t.Fatalf("the cleanup failure is %d %s, want 500 %s",
					capErr.Status, capErr.Code, CodeInternal)
			}
			if !errors.Is(capErr, errC203DatabaseRefused) {
				t.Fatalf("the database failure was not associated with the cleanup error: %v", capErr)
			}
			if c203Contains(c203DriverState.recorded(), "INSERT INTO platform.operation_attempts") {
				t.Fatalf("the cleanup fabricated an attempt row: %v", c203DriverState.recorded())
			}
		})
	}
}

// The driver re-checks the live request context after a retry/fallback close and
// before any notice, backoff or next attempt. That guard is exercised end to end
// by the streaming backoff case in the C2.03 real-stack module; the retry close
// itself durability is covered by the detached-context real-stack case.
