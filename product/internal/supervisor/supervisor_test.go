package supervisor

import (
	"context"
	"errors"
	"testing"
	"time"
)

// fakeStore records what the sweep asked it to do.
type fakeStore struct {
	leaseAcquired bool
	leaseError    error
	stale         []string
	live          int
	parked        int
	staleError    error

	leaseCalls   int
	releaseCalls int
	parkArgs     []string
}

func (f *fakeStore) TryAcquireLease(context.Context) (bool, error) {
	f.leaseCalls++
	return f.leaseAcquired, f.leaseError
}

func (f *fakeStore) ReleaseLease(context.Context) error {
	f.releaseCalls++
	return nil
}

func (f *fakeStore) StaleWorkers(context.Context, time.Time) ([]string, error) {
	return f.stale, f.staleError
}

func (f *fakeStore) LiveWorkerCount(context.Context, time.Time) (int, error) {
	return f.live, nil
}

func (f *fakeStore) MarkRunsRecoveryPending(_ context.Context, ids []string) (int, error) {
	f.parkArgs = append(f.parkArgs, ids...)
	return f.parked, nil
}

func newSupervisor(t *testing.T, store Store) *Supervisor {
	t.Helper()
	svc, err := New(store, Config{StaleAfter: time.Minute})
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	return svc
}

func TestSweepSkipsWhenAnotherSupervisorHoldsTheLease(t *testing.T) {
	store := &fakeStore{leaseAcquired: false, stale: []string{"w1"}, parked: 3}
	result, err := newSupervisor(t, store).Sweep(context.Background())

	if err != nil {
		t.Fatalf("Sweep: %v", err)
	}
	if !result.Skipped {
		t.Fatal("expected the pass to be skipped")
	}
	if len(store.parkArgs) != 0 {
		t.Fatalf("a skipped pass must not touch runs, parked %v", store.parkArgs)
	}
	if store.releaseCalls != 0 {
		t.Fatal("a lease that was not acquired must not be released")
	}
}

func TestSweepParksRunsOfStaleWorkers(t *testing.T) {
	store := &fakeStore{leaseAcquired: true, stale: []string{"w1", "w2"}, parked: 2}
	result, err := newSupervisor(t, store).Sweep(context.Background())

	if err != nil {
		t.Fatalf("Sweep: %v", err)
	}
	if result.Skipped {
		t.Fatal("pass should not be skipped when the lease is held")
	}
	if result.RunsParked != 2 {
		t.Fatalf("RunsParked = %d, want 2", result.RunsParked)
	}
	if len(store.parkArgs) != 2 {
		t.Fatalf("expected both stale workers to be handed over, got %v", store.parkArgs)
	}
	if store.releaseCalls != 1 {
		t.Fatalf("lease must be released exactly once, got %d", store.releaseCalls)
	}
}

func TestSweepDoesNothingWhenEveryWorkerIsLive(t *testing.T) {
	store := &fakeStore{leaseAcquired: true, stale: nil, live: 2}
	result, err := newSupervisor(t, store).Sweep(context.Background())

	if err != nil {
		t.Fatalf("Sweep: %v", err)
	}
	if len(store.parkArgs) != 0 {
		t.Fatalf("live workers must not have their runs touched, parked %v", store.parkArgs)
	}
	if result.LiveWorkers != 2 {
		t.Fatalf("LiveWorkers = %d, want 2", result.LiveWorkers)
	}
	if result.RunsParked != 0 {
		t.Fatalf("RunsParked = %d, want 0", result.RunsParked)
	}
}

func TestSweepNeverRestarts(t *testing.T) {
	store := &fakeStore{leaseAcquired: true, stale: []string{"w1"}, parked: 1}
	result, err := newSupervisor(t, store).Sweep(context.Background())
	if err != nil {
		t.Fatalf("Sweep: %v", err)
	}
	// The only verb available to the supervisor is "park".
	if result.NeverRestartedNote == "" {
		t.Fatal("result must state that runs are parked, not restarted")
	}
	if result.RunsParked != 1 {
		t.Fatalf("RunsParked = %d, want 1", result.RunsParked)
	}
}

func TestNewRequiresAStore(t *testing.T) {
	if _, err := New(nil, Config{}); !errors.Is(err, ErrNoDatabase) {
		t.Fatalf("expected ErrNoDatabase, got %v", err)
	}
}

func TestStaleAfterDefaultsWhenUnset(t *testing.T) {
	store := &fakeStore{leaseAcquired: true}
	svc, err := New(store, Config{})
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	result, err := svc.Sweep(context.Background())
	if err != nil {
		t.Fatalf("Sweep: %v", err)
	}
	if result.CutoffSeconds <= 0 {
		t.Fatalf("expected a positive default staleness window, got %v", result.CutoffSeconds)
	}
}
