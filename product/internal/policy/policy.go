// Package policy implements the platform's default-deny resource authorization.
//
// A resource is visible to a principal when the principal owns it, or when an
// unexpired `platform.resource_grants` row names (principal, resource_type,
// resource_id, action). There is no role bypass: an administrator who neither
// owns the resource nor holds a grant is refused exactly like anyone else
// (SECURITY.md §1: managing the ACL is not the same as reading the body).
//
// Decisions are cached in process, keyed by the policy epoch:
//
//   - every call reads `platform.policy_epochs.acl` first, so a write that bumps
//     the revision (grant, revoke, role change) makes the previously cached key
//     unreachable — a cached allow cannot survive a revoke;
//   - a cached allow carries its grant's `expires_at` and is re-validated against
//     the injected clock, so an expiring grant cannot stay allowed inside one
//     epoch;
//   - any error, including a missing epoch row, fails closed.
//
// The cache is an optimization only: clearing it can change latency, never a
// decision.
package policy

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"sync"
	"time"
)

// ScopeACL is the policy_epochs row that governs resource grants.
const ScopeACL = "acl"

// defaultMaxEntries bounds the in-process decision cache. Pruning drops entries
// from older epochs first; a full table is cleared rather than grown without
// bound (the table is a cache, so clearing is always safe).
const defaultMaxEntries = 10000

// ErrEpochMissing means policy_epochs has no `acl` row. It fails closed; the
// migration seeds the row, so this is a broken-installation state.
var ErrEpochMissing = errors.New("the acl policy epoch is missing")

// PrincipalRef names the acting principal. An empty ID is refused (deny).
type PrincipalRef struct {
	ID string
}

// ResourceRef names the resource. OwnerID is what the resource row records as
// its owner; an empty OwnerID means the resource has no ownership path and only
// a grant can allow access.
type ResourceRef struct {
	Type    string
	ID      string
	OwnerID string
}

// Stats is an observational snapshot of one Authorizer. CacheHits/Misses count
// decision-cache outcomes; EpochReads counts policy-epoch reads (one per Allow
// call that reaches the policy), GrantQueries counts storage lookups.
type Stats struct {
	EpochReads   int64
	GrantQueries int64
	CacheHits    int64
	CacheMisses  int64
	Entries      int
}

type decision struct {
	allow     bool
	expiresAt time.Time
}

type decisionKey struct {
	principalID  string
	resourceType string
	resourceID   string
	action       string
	epoch        int64
}

// decisionStore is the persistence seam behind the authorizer. The SQL
// implementation is the only production one; the unit tests use an in-memory
// implementation to prove the cache and clock behaviour without a database.
type decisionStore interface {
	currentEpoch(ctx context.Context) (int64, error)
	grantState(
		ctx context.Context,
		subject PrincipalRef,
		resource ResourceRef,
		action string,
		at time.Time,
	) (decision, error)
}

// Authorizer answers Allow and owns the grant write path.
type Authorizer struct {
	DB  *sql.DB
	Now func() time.Time

	store      decisionStore
	mu         sync.Mutex
	cache      map[decisionKey]decision
	stats      Stats
	maxEntries int
}

// New builds an authorizer over db.
func New(db *sql.DB) *Authorizer {
	return &Authorizer{
		DB:         db,
		Now:        time.Now,
		store:      &sqlStore{db: db},
		cache:      make(map[decisionKey]decision),
		maxEntries: defaultMaxEntries,
	}
}

// newWithStore is the unit-test constructor: it swaps the persistence seam and
// the clock. It is not exported because production code must use New.
func newWithStore(store decisionStore, now func() time.Time) *Authorizer {
	return &Authorizer{
		Now:        now,
		store:      store,
		cache:      make(map[decisionKey]decision),
		maxEntries: defaultMaxEntries,
	}
}

func (a *Authorizer) clock() time.Time {
	if a.Now == nil {
		return time.Now().UTC()
	}
	return a.Now().UTC()
}

// Clock returns the authorizer's current time. Handlers use it for the same
// "must be in the future" check the write path applies, so both read one clock.
func (a *Authorizer) Clock() time.Time { return a.clock() }

// Allow reports whether subject may perform action on resource.
//
// The current policy epoch is read first, on every call. Ownership is checked
// next, but it is a decision, not a shortcut around the policy: a broken
// installation whose epoch row is missing fails closed even for the owner.
// Everything else consults the epoch-keyed decision cache and then the grant
// table.
func (a *Authorizer) Allow(
	ctx context.Context,
	subject PrincipalRef,
	resource ResourceRef,
	action string,
) (bool, error) {
	epoch, err := a.epoch(ctx)
	if err != nil {
		return false, err
	}
	if resource.OwnerID != "" && subject.ID != "" && subject.ID == resource.OwnerID {
		return true, nil
	}
	if subject.ID == "" || resource.Type == "" || resource.ID == "" || action == "" {
		// An incomplete reference cannot be authorized; deny instead of guessing
		// (and do not spend a grant lookup on it).
		return false, nil
	}

	key := decisionKey{
		principalID:  subject.ID,
		resourceType: resource.Type,
		resourceID:   resource.ID,
		action:       action,
		epoch:        epoch,
	}
	now := a.clock()
	if cached, ok := a.cached(key, now); ok {
		return cached, nil
	}

	found, err := a.store.grantState(ctx, subject, resource, action, now)
	if err != nil {
		return false, err
	}
	a.remember(key, found, epoch)
	return found.allow, nil
}

// epoch reads the current acl revision. It fails closed on a missing row.
func (a *Authorizer) epoch(ctx context.Context) (int64, error) {
	revision, err := a.store.currentEpoch(ctx)
	if err != nil {
		return 0, err
	}
	a.mu.Lock()
	a.stats.EpochReads++
	a.mu.Unlock()
	return revision, nil
}

// cached serves a decision only when it is still valid: a cached allow with an
// expiry that has passed is dropped and treated as a miss.
func (a *Authorizer) cached(key decisionKey, now time.Time) (bool, bool) {
	a.mu.Lock()
	defer a.mu.Unlock()
	found, ok := a.cache[key]
	if !ok {
		a.stats.CacheMisses++
		return false, false
	}
	if found.allow && !found.expiresAt.IsZero() && !now.Before(found.expiresAt) {
		delete(a.cache, key)
		a.stats.CacheMisses++
		return false, false
	}
	a.stats.CacheHits++
	return found.allow, true
}

// remember stores one decision for its epoch, pruning stale entries when the
// table reaches its cap.
func (a *Authorizer) remember(key decisionKey, value decision, epoch int64) {
	a.mu.Lock()
	defer a.mu.Unlock()
	a.stats.GrantQueries++
	if len(a.cache) >= a.maxEntries {
		for cachedKey := range a.cache {
			if cachedKey.epoch != epoch {
				delete(a.cache, cachedKey)
			}
		}
		if len(a.cache) >= a.maxEntries {
			// Still full within one epoch: the table is only an optimization.
			clear(a.cache)
		}
	}
	a.cache[key] = value
}

// Stats returns a consistent snapshot of the counters.
func (a *Authorizer) Stats() Stats {
	a.mu.Lock()
	defer a.mu.Unlock()
	snapshot := a.stats
	snapshot.Entries = len(a.cache)
	return snapshot
}

// sqlStore is the production decision store: two small queries, both
// schema-qualified and both reading only what the decision needs.
type sqlStore struct {
	db *sql.DB
}

func (s *sqlStore) currentEpoch(ctx context.Context) (int64, error) {
	var revision int64
	err := s.db.QueryRowContext(
		ctx, `SELECT revision FROM platform.policy_epochs WHERE scope = $1`, ScopeACL,
	).Scan(&revision)
	if errors.Is(err, sql.ErrNoRows) {
		return 0, ErrEpochMissing
	}
	if err != nil {
		return 0, fmt.Errorf("reading the acl policy epoch: %w", err)
	}
	return revision, nil
}

func (s *sqlStore) grantState(
	ctx context.Context,
	subject PrincipalRef,
	resource ResourceRef,
	action string,
	at time.Time,
) (decision, error) {
	var expiresAt sql.NullTime
	err := s.db.QueryRowContext(ctx, `
		SELECT expires_at
		  FROM platform.resource_grants
		 WHERE principal_id = $1
		   AND resource_type = $2
		   AND resource_id = $3
		   AND action = $4
		   AND (expires_at IS NULL OR expires_at > $5)`,
		subject.ID, resource.Type, resource.ID, action, at,
	).Scan(&expiresAt)
	if errors.Is(err, sql.ErrNoRows) {
		return decision{}, nil
	}
	if err != nil {
		return decision{}, fmt.Errorf("reading a resource grant: %w", err)
	}
	result := decision{allow: true}
	if expiresAt.Valid {
		// The cached allow must carry the grant's own expiry so the clock can
		// retire it without waiting for an epoch bump.
		result.expiresAt = expiresAt.Time
	}
	return result, nil
}
