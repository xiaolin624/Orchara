package api

import (
	"strings"
	"sync"
	"time"
)

// The ticket's numbers: five failed attempts per 60 seconds per (login, peer
// address). Retry-After reports the whole window, because that is the earliest
// retry that is certainly accepted.
const (
	loginFailureLimit  = 5
	loginFailureWindow = 60 * time.Second

	// Memory hygiene for the in-process map. An attacker who invents login names
	// must not be able to grow it without bound.
	loginLimiterPruneAt = 64
	loginLimiterMaxKeys = 4096
)

// loginKey is the throttle identity: the login (lowercased, so "Admin" and
// "admin" share a budget) plus the peer address. Neither part is a secret.
type loginKey struct {
	login string
	addr  string
}

// loginKeyFor builds the throttle key from the request body's login and the
// accepted TCP peer address.
func loginKeyFor(login, remoteAddr string) loginKey {
	return loginKey{login: strings.ToLower(strings.TrimSpace(login)), addr: remoteHost(remoteAddr)}
}

// loginAttempt is one key's fixed window of failures.
type loginAttempt struct {
	failures int
	until    time.Time
}

// loginLimiter is the per-process brute-force guard for POST /api/v1/auth/login.
//
// It is deliberately small and in-memory: a single-instance guard, not a
// distributed quota (F15B owns shared limits). The clock is injected so a unit
// test can prove window expiry without sleeping.
type loginLimiter struct {
	mu      sync.Mutex
	limit   int
	window  time.Duration
	now     func() time.Time
	fails   map[loginKey]*loginAttempt
	pruneAt int
	maxKeys int
}

func newLoginLimiter(limit int, window time.Duration, now func() time.Time) *loginLimiter {
	if now == nil {
		now = time.Now
	}
	return &loginLimiter{
		limit:   limit,
		window:  window,
		now:     now,
		fails:   make(map[loginKey]*loginAttempt),
		pruneAt: loginLimiterPruneAt,
		maxKeys: loginLimiterMaxKeys,
	}
}

// blocked reports whether key has exhausted its window, and how long a
// throttled client should wait. A key whose window has ended is not blocked and
// is not reported as such even if its row has not been pruned yet.
func (l *loginLimiter) blocked(key loginKey) (bool, time.Duration) {
	l.mu.Lock()
	defer l.mu.Unlock()

	attempt, ok := l.fails[key]
	if !ok {
		return false, 0
	}
	if !l.now().Before(attempt.until) || attempt.failures < l.limit {
		return false, 0
	}
	return true, l.window
}

// fail records one failed attempt. The first failure opens a window; later
// failures inside it accumulate, so the fifth failure trips the key until the
// window ends.
func (l *loginLimiter) fail(key loginKey) {
	l.mu.Lock()
	defer l.mu.Unlock()

	now := l.now()
	attempt, ok := l.fails[key]
	if !ok || !now.Before(attempt.until) {
		attempt = &loginAttempt{until: now.Add(l.window)}
		l.fails[key] = attempt
	}
	attempt.failures++
	l.pruneLocked(now)
}

// succeed clears key's counter: a successful sign-in proves the password after
// all, so it must not leave the caller closer to being throttled.
func (l *loginLimiter) succeed(key loginKey) {
	l.mu.Lock()
	defer l.mu.Unlock()
	delete(l.fails, key)
}

// pruneLocked drops finished windows once the map is worth walking, then
// evicts arbitrary rows if a flood of distinct logins still exceeds the cap.
// Losing a failure count can only make the guard more permissive, never block a
// legitimate caller.
func (l *loginLimiter) pruneLocked(now time.Time) {
	if len(l.fails) < l.pruneAt {
		return
	}
	for key, attempt := range l.fails {
		if !now.Before(attempt.until) {
			delete(l.fails, key)
		}
	}
	for key := range l.fails {
		if len(l.fails) < l.maxKeys {
			break
		}
		delete(l.fails, key)
	}
}
