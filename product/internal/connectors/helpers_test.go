package connectors

import (
	"net/netip"
	"strconv"
	"testing"
)

// netipAddr is the address type the resolver and the guard share. The alias
// keeps the tests readable without importing net/netip in every assertion.
type netipAddr = netip.Addr

func parseAddr(t *testing.T, raw string) netip.Addr {
	t.Helper()
	addr, err := netip.ParseAddr(raw)
	if err != nil {
		t.Fatalf("parse address %q: %v", raw, err)
	}
	return addr
}

func itoa(value int) string { return strconv.Itoa(value) }
