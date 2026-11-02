package api

import (
	"testing"
	"time"
)

type fakeClock struct{ at time.Time }

func (c *fakeClock) now() time.Time          { return c.at }
func (c *fakeClock) advance(d time.Duration) { c.at = c.at.Add(d) }

func newTestClock() *fakeClock {
	return &fakeClock{at: time.Date(2026, 11, 1, 12, 0, 0, 0, time.UTC)}
}

func TestLoginLimiterTripsOnTheFifthFailure(t *testing.T) {
	clock := newTestClock()
	limiter := newLoginLimiter(loginFailureLimit, loginFailureWindow, clock.now)
	key := loginKeyFor("alice", "127.0.0.1:54321")

	for failure := 1; failure < loginFailureLimit; failure++ {
		limiter.fail(key)
		if blocked, _ := limiter.blocked(key); blocked {
			t.Fatalf("the key was blocked after only %d failures", failure)
		}
	}
	limiter.fail(key)

	blocked, retry := limiter.blocked(key)
	if !blocked {
		t.Fatal("the key was not blocked after the fifth failure")
	}
	if retry != loginFailureWindow {
		t.Fatalf("retry after = %s, want %s", retry, loginFailureWindow)
	}
}

func TestLoginLimiterIsPerLoginAndPerPeerAddress(t *testing.T) {
	clock := newTestClock()
	limiter := newLoginLimiter(loginFailureLimit, loginFailureWindow, clock.now)
	blockedKey := loginKeyFor("alice", "127.0.0.1:1000")
	for range loginFailureLimit {
		limiter.fail(blockedKey)
	}

	if blocked, _ := limiter.blocked(blockedKey); !blocked {
		t.Fatal("the tripped key is not blocked")
	}
	if blocked, _ := limiter.blocked(loginKeyFor("bob", "127.0.0.1:1000")); blocked {
		t.Fatal("a different login shares another login's budget")
	}
	if blocked, _ := limiter.blocked(loginKeyFor("alice", "10.0.0.7:1000")); blocked {
		t.Fatal("a different peer address shares another address's budget")
	}
}

func TestLoginLimiterKeyLowercasesTheLogin(t *testing.T) {
	key := loginKeyFor("  Alice  ", "127.0.0.1:54321")
	if key.login != "alice" {
		t.Fatalf("key login = %q, want alice", key.login)
	}
	if key.addr != "127.0.0.1" {
		t.Fatalf("key addr = %q, want 127.0.0.1", key.addr)
	}
	// A remote address without a port must not produce an empty key part.
	if got := loginKeyFor("bob", "127.0.0.1").addr; got != "127.0.0.1" {
		t.Fatalf("addr without port = %q", got)
	}
}

func TestLoginLimiterWindowExpiry(t *testing.T) {
	clock := newTestClock()
	limiter := newLoginLimiter(loginFailureLimit, loginFailureWindow, clock.now)
	key := loginKeyFor("alice", "127.0.0.1:1000")
	for range loginFailureLimit {
		limiter.fail(key)
	}

	clock.advance(loginFailureWindow - time.Second)
	if blocked, _ := limiter.blocked(key); !blocked {
		t.Fatal("the key stopped being blocked before its window ended")
	}
	clock.advance(time.Second)
	if blocked, _ := limiter.blocked(key); blocked {
		t.Fatal("the key is still blocked after its window ended")
	}
	// The next failure starts a fresh window instead of inheriting the old one.
	limiter.fail(key)
	if blocked, _ := limiter.blocked(key); blocked {
		t.Fatal("one failure after an expired window must not block")
	}
}

func TestLoginLimiterSuccessClearsTheCounter(t *testing.T) {
	clock := newTestClock()
	limiter := newLoginLimiter(loginFailureLimit, loginFailureWindow, clock.now)
	key := loginKeyFor("alice", "127.0.0.1:1000")
	for range loginFailureLimit - 1 {
		limiter.fail(key)
	}

	limiter.succeed(key)
	if blocked, _ := limiter.blocked(key); blocked {
		t.Fatal("a successful sign-in left the key throttled")
	}
	for failure := 1; failure < loginFailureLimit; failure++ {
		limiter.fail(key)
		if blocked, _ := limiter.blocked(key); blocked {
			t.Fatalf("the cleared counter was not reset: blocked after %d new failures", failure)
		}
	}
}

func TestLoginLimiterPrunesFinishedWindows(t *testing.T) {
	clock := newTestClock()
	limiter := newLoginLimiter(loginFailureLimit, loginFailureWindow, clock.now)
	// A small bound keeps the test quick; production uses the package constants.
	limiter.pruneAt = 4
	limiter.maxKeys = 8

	for index := 0; index < 20; index++ {
		limiter.fail(loginKeyFor("flood-"+string(rune('a'+index)), "127.0.0.1"))
	}
	if len(limiter.fails) > limiter.maxKeys {
		t.Fatalf("the limiter tracks %d keys, above the %d cap", len(limiter.fails), limiter.maxKeys)
	}

	clock.advance(loginFailureWindow)
	limiter.fail(loginKeyFor("newcomer", "127.0.0.1"))
	if len(limiter.fails) > limiter.pruneAt {
		t.Fatalf("expired windows were not pruned: %d keys remain", len(limiter.fails))
	}
}
