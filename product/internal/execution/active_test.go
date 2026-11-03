package execution

import (
	"database/sql"
	"testing"
)

// The deadline decision is the one rule every check (claim, heartbeat, result,
// exit) shares. It must treat a NULL limit as "unknown / no deadline", not as
// zero: a run whose snapshot carries no policy must never be failed by an
// invented deadline.
func TestDeadlineExceededTreatsNullAsUnknown(t *testing.T) {
	maxOf := func(value int64) sql.NullInt64 { return sql.NullInt64{Int64: value, Valid: true} }
	cases := []struct {
		name      string
		max       sql.NullInt64
		projected int64
		want      bool
	}{
		{"NULL is unknown, never a zero deadline", sql.NullInt64{}, 9_999_999, false},
		{"NULL with no accrued time", sql.NullInt64{}, 0, false},
		{"a known zero limit is already reached", maxOf(0), 0, true},
		{"under the limit", maxOf(1000), 999, false},
		{"exactly at the limit", maxOf(1000), 1000, true},
		{"over the limit", maxOf(1000), 1001, true},
		{"negative accrued time cannot exceed a positive limit", maxOf(1000), -1, false},
	}
	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			if got := deadlineExceeded(testCase.max, testCase.projected); got != testCase.want {
				t.Fatalf("deadlineExceeded(%+v, %d) = %v, want %v",
					testCase.max, testCase.projected, got, testCase.want)
			}
		})
	}
}

// The projected active time is the accounting rule the UI and the deadline
// share: a settled attempt contributes nothing more, an executing live attempt
// contributes its whole interval, and any other phase accrues nothing.
func TestProjectedActiveMSPinsThePhaseRule(t *testing.T) {
	cases := []struct {
		name      string
		settled   int64
		phase     string
		liveMS    int64
		hasLive   bool
		projected int64
	}{
		{"executing with a live attempt adds its interval", 5000, "executing", 2000, true, 7000},
		{"finalizing never accrues", 5000, "finalizing", 9000, true, 5000},
		{"waiting never accrues", 5000, "waiting", 9000, true, 5000},
		{"idle never accrues", 5000, "idle", 9000, false, 5000},
		{"executing without a live attempt adds nothing", 5000, "executing", 0, false, 5000},
		{"a settled attempt is not added twice", 5000, "executing", 0, false, 5000},
	}
	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			got := projectedActiveMS(
				testCase.settled, testCase.phase, testCase.liveMS, testCase.hasLive,
			)
			if got != testCase.projected {
				t.Fatalf("projectedActiveMS(%d, %q, %d, %v) = %d, want %d",
					testCase.settled, testCase.phase, testCase.liveMS, testCase.hasLive,
					got, testCase.projected)
			}
		})
	}
}
