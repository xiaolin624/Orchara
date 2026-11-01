package policy

import (
	"context"
	"errors"
	"sync"
	"testing"
	"time"
)

// fakeStore is the in-memory decision store used by the authorizer unit tests.
//
// It exists because the cache semantics (serve once, invalidate on an epoch
// bump, never serve an expired allow, fail closed on a missing epoch) are pure
// decision behaviour and must not need a PostgreSQL connection to prove. The SQL
// store is exercised end to end against a real database by
// product/tests/f01/test_t019_*.py; this fake never stands in for that.
type fakeStore struct {
	mu          sync.Mutex
	epoch       int64
	epochErr    error
	grantErr    error
	grants      map[string]decision
	epochReads  int
	grantCalls  []time.Time
	grantLookup []string
}

func newFakeStore() *fakeStore {
	return &fakeStore{grants: make(map[string]decision)}
}

func fakeKey(subject PrincipalRef, resource ResourceRef, action string) string {
	return subject.ID + "|" + resource.Type + "|" + resource.ID + "|" + action
}

func (f *fakeStore) currentEpoch(context.Context) (int64, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.epochReads++
	if f.epochErr != nil {
		return 0, f.epochErr
	}
	return f.epoch, nil
}

func (f *fakeStore) grantState(
	_ context.Context,
	subject PrincipalRef,
	resource ResourceRef,
	action string,
	at time.Time,
) (decision, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	key := fakeKey(subject, resource, action)
	f.grantCalls = append(f.grantCalls, at)
	f.grantLookup = append(f.grantLookup, key)
	if f.grantErr != nil {
		return decision{}, f.grantErr
	}
	result, ok := f.grants[key]
	if !ok {
		return decision{}, nil
	}
	if !result.expiresAt.IsZero() && !at.Before(result.expiresAt) {
		return decision{}, nil
	}
	return result, nil
}

func (f *fakeStore) grantCallCount() int {
	f.mu.Lock()
	defer f.mu.Unlock()
	return len(f.grantCalls)
}

func (f *fakeStore) epochReadCount() int {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.epochReads
}

func (f *fakeStore) setGrant(subject PrincipalRef, resource ResourceRef, action string, value decision) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.grants[fakeKey(subject, resource, action)] = value
}

func (f *fakeStore) bump() {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.epoch++
}

func testAuthorizer(store decisionStore, clock func() time.Time) *Authorizer {
	return newWithStore(store, clock)
}

func readRequest() (PrincipalRef, ResourceRef) {
	return PrincipalRef{ID: "11111111-1111-4111-8111-111111111111"},
		ResourceRef{
			Type:    "application",
			ID:      "22222222-2222-4222-8222-222222222222",
			OwnerID: "33333333-3333-4333-8333-333333333333",
		}
}

func TestOwnerIsAlwaysAllowedWithoutAPolicyLookup(t *testing.T) {
	store := newFakeStore()
	authorizer := testAuthorizer(store, time.Now)
	_, resource := readRequest()

	allow, err := authorizer.Allow(context.Background(), PrincipalRef{ID: resource.OwnerID}, resource, "read")
	if err != nil {
		t.Fatalf("owner allowance returned an error: %v", err)
	}
	if !allow {
		t.Fatal("the owner of the resource was denied")
	}
	if store.epochReadCount() != 0 || store.grantCallCount() != 0 {
		t.Fatalf("an ownership check reached the policy store: %+v", authorizer.Stats())
	}
}

func TestAnEmptyOwnerIsNotAnOwnershipPath(t *testing.T) {
	store := newFakeStore()
	subject, resource := readRequest()
	resource.OwnerID = ""
	store.setGrant(subject, resource, "read", decision{allow: true})

	allow, err := testAuthorizer(store, time.Now).Allow(context.Background(), subject, resource, "read")
	if err != nil || !allow {
		t.Fatalf("an ownerless resource should fall through to the grant: allow=%v err=%v", allow, err)
	}
	if store.grantCallCount() != 1 {
		t.Fatalf("the grant table was not consulted: %d lookups", store.grantCallCount())
	}
}

func TestACachedAllowIsServedWithinOneEpoch(t *testing.T) {
	store := newFakeStore()
	subject, resource := readRequest()
	store.setGrant(subject, resource, "read", decision{allow: true, expiresAt: time.Now().Add(time.Hour)})
	authorizer := testAuthorizer(store, time.Now)

	first, err := authorizer.Allow(context.Background(), subject, resource, "read")
	if err != nil || !first {
		t.Fatalf("first decision: allow=%v err=%v", first, err)
	}
	second, err := authorizer.Allow(context.Background(), subject, resource, "read")
	if err != nil || !second {
		t.Fatalf("second decision: allow=%v err=%v", second, err)
	}
	if store.epochReadCount() != 2 {
		t.Fatalf("the epoch must be read on every call, got %d reads", store.epochReadCount())
	}
	if store.grantCallCount() != 1 {
		t.Fatalf("the grant lookup was not cached: %d lookups", store.grantCallCount())
	}
	stats := authorizer.Stats()
	if stats.CacheHits != 1 || stats.CacheMisses != 1 {
		t.Fatalf("unexpected cache counters: %+v", stats)
	}
}

func TestAnEpochBumpInvalidatesTheCachedAllowImmediately(t *testing.T) {
	store := newFakeStore()
	subject, resource := readRequest()
	store.setGrant(subject, resource, "read", decision{allow: true})
	authorizer := testAuthorizer(store, time.Now)

	if allow, err := authorizer.Allow(context.Background(), subject, resource, "read"); err != nil || !allow {
		t.Fatalf("initial decision: allow=%v err=%v", allow, err)
	}

	// A revoke deletes the row and bumps the epoch in one transaction.
	store.mu.Lock()
	delete(store.grants, fakeKey(subject, resource, "read"))
	store.mu.Unlock()
	store.bump()

	allow, err := authorizer.Allow(context.Background(), subject, resource, "read")
	if err != nil {
		t.Fatalf("allow after revoke returned an error: %v", err)
	}
	if allow {
		t.Fatal("a cached allow survived the policy epoch bump")
	}
	if store.grantCallCount() != 2 {
		t.Fatalf("the revoked grant was not re-read: %d lookups", store.grantCallCount())
	}
}

func TestAnExpiredCachedAllowIsNotServed(t *testing.T) {
	store := newFakeStore()
	subject, resource := readRequest()
	start := time.Date(2026, 1, 2, 3, 4, 5, 0, time.UTC)
	expires := start.Add(time.Minute)
	current := start
	store.setGrant(subject, resource, "read", decision{allow: true, expiresAt: expires})
	authorizer := testAuthorizer(store, func() time.Time { return current })

	if allow, err := authorizer.Allow(context.Background(), subject, resource, "read"); err != nil || !allow {
		t.Fatalf("decision before expiry: allow=%v err=%v", allow, err)
	}
	// The clock is now past the cached grant's expiry. The store (like the real
	// SQL predicate) no longer returns the grant.
	current = expires.Add(time.Second)
	allow, err := authorizer.Allow(context.Background(), subject, resource, "read")
	if err != nil {
		t.Fatalf("decision after expiry returned an error: %v", err)
	}
	if allow {
		t.Fatal("an allow was served from cache after its grant expired")
	}
	if store.grantCallCount() != 2 {
		t.Fatalf("the expired allow was not re-validated against the store: %d lookups", store.grantCallCount())
	}
	lastLookup := store.grantCalls[len(store.grantCalls)-1]
	if !lastLookup.Equal(expires.Add(time.Second)) {
		t.Fatalf("the store was not queried with the injected clock: %s", lastLookup)
	}
}

func TestACachedDenyIsReportedWithoutAQuery(t *testing.T) {
	store := newFakeStore()
	subject, resource := readRequest()
	authorizer := testAuthorizer(store, time.Now)

	for attempt := 0; attempt < 2; attempt++ {
		allow, err := authorizer.Allow(context.Background(), subject, resource, "read")
		if err != nil || allow {
			t.Fatalf("attempt %d: allow=%v err=%v", attempt, allow, err)
		}
	}
	if store.grantCallCount() != 1 {
		t.Fatalf("the denial was not cached: %d lookups", store.grantCallCount())
	}
}

func TestAMissingPolicyEpochFailsClosed(t *testing.T) {
	store := newFakeStore()
	store.epochErr = ErrEpochMissing
	subject, resource := readRequest()

	allow, err := testAuthorizer(store, time.Now).Allow(context.Background(), subject, resource, "read")
	if allow {
		t.Fatal("a missing policy epoch must never allow")
	}
	if !errors.Is(err, ErrEpochMissing) {
		t.Fatalf("expected ErrEpochMissing, got %v", err)
	}
}

func TestAStoreErrorFailsClosed(t *testing.T) {
	store := newFakeStore()
	store.grantErr = errors.New("database is on fire")
	subject, resource := readRequest()

	allow, err := testAuthorizer(store, time.Now).Allow(context.Background(), subject, resource, "read")
	if allow {
		t.Fatal("a storage error must never allow")
	}
	if err == nil {
		t.Fatal("a storage error was swallowed")
	}
}

func TestAnIncompleteReferenceIsDeniedWithoutALookup(t *testing.T) {
	store := newFakeStore()
	authorizer := testAuthorizer(store, time.Now)
	resource := ResourceRef{Type: "application", ID: "22222222-2222-4222-8222-222222222222"}

	allow, err := authorizer.Allow(context.Background(), PrincipalRef{}, resource, "read")
	if err != nil || allow {
		t.Fatalf("an empty principal should be denied: allow=%v err=%v", allow, err)
	}
	if store.epochReadCount() != 0 {
		t.Fatal("an incomplete reference reached the policy store")
	}
}

func TestTheCacheDoesNotGrowWithoutBoundAcrossEpochs(t *testing.T) {
	store := newFakeStore()
	subject, resource := readRequest()
	store.setGrant(subject, resource, "read", decision{allow: true})
	authorizer := testAuthorizer(store, time.Now)
	authorizer.maxEntries = 4

	for revision := 0; revision < 20; revision++ {
		if allow, err := authorizer.Allow(context.Background(), subject, resource, "read"); err != nil || !allow {
			t.Fatalf("revision %d: allow=%v err=%v", revision, allow, err)
		}
		store.bump()
	}
	if size := authorizer.Stats().Entries; size > authorizer.maxEntries {
		t.Fatalf("decision cache grew to %d entries (cap %d)", size, authorizer.maxEntries)
	}
}
