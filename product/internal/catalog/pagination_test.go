package catalog

import (
	"encoding/base64"
	"errors"
	"strings"
	"testing"
	"time"
)

func TestParseLimitDefaultsToTwenty(t *testing.T) {
	limit, err := ParseLimit("")
	if err != nil || limit != DefaultLimit {
		t.Fatalf("empty limit: limit=%d err=%v", limit, err)
	}
	if DefaultLimit != 20 {
		t.Fatalf("the contract default is 20, got %d", DefaultLimit)
	}
}

func TestParseLimitAcceptsTheContractRangeOnly(t *testing.T) {
	cases := map[string]int{"1": 1, "20": 20, "100": 100, " 7 ": 7}
	for raw, want := range cases {
		limit, err := ParseLimit(raw)
		if err != nil {
			t.Fatalf("limit %q was rejected: %v", raw, err)
		}
		if limit != want {
			t.Fatalf("limit %q parsed as %d, want %d", raw, limit, want)
		}
	}
	for _, raw := range []string{"0", "101", "-1", "abc", "1.5", "10x", " ", "999999999999999999999"} {
		if _, err := ParseLimit(raw); !errors.Is(err, ErrInvalidLimit) {
			t.Fatalf("limit %q was accepted or returned the wrong error: %v", raw, err)
		}
	}
}

func TestEncodeCursorIsOpaqueAndRoundTrips(t *testing.T) {
	createdAt := time.Date(2026, 7, 8, 9, 10, 11, 123456000, time.UTC)
	id := "11111111-1111-4111-8111-111111111111"

	cursor := EncodeCursor(createdAt, id)
	if strings.Contains(cursor, id) || strings.Contains(cursor, "2026") || strings.Contains(cursor, "|") {
		t.Fatalf("cursor leaks its payload: %q", cursor)
	}
	if strings.ContainsAny(cursor, "+/=") {
		t.Fatalf("cursor is not raw base64url: %q", cursor)
	}

	decodedAt, decodedID, err := DecodeCursor(cursor)
	if err != nil {
		t.Fatalf("round trip failed: %v", err)
	}
	if !decodedAt.Equal(createdAt) {
		t.Fatalf("timestamp changed in the round trip: %s != %s", decodedAt, createdAt)
	}
	if decodedID != id {
		t.Fatalf("id changed in the round trip: %s", decodedID)
	}
}

func TestDecodeCursorRejectsMalformedValues(t *testing.T) {
	cases := map[string]string{
		"not base64":         "***",
		"no separator":       base64URL("2026-07-08T09:10:11Z"),
		"bad timestamp":      base64URL("yesterday|11111111-1111-4111-8111-111111111111"),
		"bad uuid":           base64URL("2026-07-08T09:10:11Z|not-a-uuid"),
		"too many parts":     base64URL("2026-07-08T09:10:11Z|11111111-1111-4111-8111-111111111111|extra"),
		"empty payload":      base64URL(""),
		"empty id":           base64URL("2026-07-08T09:10:11Z|"),
		"padded and invalid": "####",
	}
	for name, raw := range cases {
		if _, _, err := DecodeCursor(raw); !errors.Is(err, ErrInvalidCursor) {
			t.Fatalf("%s was accepted or returned the wrong error: %v", name, err)
		}
	}
}

func TestDecodeCursorRequiresTheCanonicalUUIDForm(t *testing.T) {
	for _, id := range []string{
		"11111111111141118111111111111111",
		"{11111111-1111-4111-8111-111111111111}",
		"11111111-1111-4111-8111-11111111111g",
	} {
		cursor := base64URL("2026-07-08T09:10:11Z|" + id)
		if _, _, err := DecodeCursor(cursor); !errors.Is(err, ErrInvalidCursor) {
			t.Fatalf("non-canonical uuid %q was accepted: %v", id, err)
		}
	}
}

func base64URL(value string) string {
	return base64.RawURLEncoding.EncodeToString([]byte(value))
}
