package memory

import (
	"errors"
	"strings"
	"testing"
	"time"

	"orchara/platform/internal/catalog"
)

func TestValidateTextAcceptsTheContractCeilingAndRefusesBlankOrLonger(t *testing.T) {
	legacy := strings.Repeat("a", MaxTextRunes)
	if _, err := validateText("text", legacy); err != nil {
		t.Fatalf("4000 ASCII characters must be accepted: %v", err)
	}
	// The contract counts characters, not bytes: 4000 three-byte runes are legal.
	if _, err := validateText("text", strings.Repeat("记", MaxTextRunes)); err != nil {
		t.Fatalf("4000 multi-byte characters must be accepted: %v", err)
	}
	for name, value := range map[string]string{
		"empty":      "",
		"whitespace": "   \n\t",
		"too long":   strings.Repeat("a", MaxTextRunes+1),
	} {
		text, err := validateText("text", value)
		if text != "" {
			t.Fatalf("%s: returned partial text", name)
		}
		typed, ok := AsError(err)
		if !ok || typed.Status != 422 || typed.Code != "validation_failed" || typed.Field != "text" {
			t.Fatalf("%s: expected a located 422, got %v", name, err)
		}
	}
}

func TestValidateLogicalKeyIsBoundedAndNonEmpty(t *testing.T) {
	if key, err := validateLogicalKey("  agent:node:memory:1  "); err != nil || key != "agent:node:memory:1" {
		t.Fatalf("key = %q err = %v", key, err)
	}
	for _, value := range []string{"", "   ", strings.Repeat("k", 201)} {
		if _, err := validateLogicalKey(value); err == nil {
			t.Fatalf("value %q must be refused", value)
		} else if typed, ok := AsError(err); !ok || typed.Field != "logical_key" {
			t.Fatalf("value %q: expected a located logical_key error, got %v", value, err)
		}
	}
}

func TestNormaliseDecisionIsTheClosedContractVocabulary(t *testing.T) {
	for raw, want := range map[string]string{"confirm": "confirm", " CONFIRM ": "confirm", "reject": "reject"} {
		got, err := normaliseDecision(raw)
		if err != nil || got != want {
			t.Fatalf("%q -> %q, %v", raw, got, err)
		}
	}
	for _, raw := range []string{"", "maybe", "approve"} {
		if _, err := normaliseDecision(raw); err == nil {
			t.Fatalf("%q must be refused", raw)
		} else if typed, ok := AsError(err); !ok || typed.Field != "decision" || typed.Status != 422 {
			t.Fatalf("%q: expected a located 422, got %v", raw, err)
		}
	}
}

func TestDecodeListInputValidatesTheScopeAndTheCursor(t *testing.T) {
	scope, position, err := decodeListInput("", "")
	if err != nil || scope != "" || position.Set {
		t.Fatalf("empty input: scope=%q set=%v err=%v", scope, position.Set, err)
	}

	applicationID := "33333333-3333-4333-8333-333333333333"
	scope, _, err = decodeListInput(applicationID, "")
	if err != nil || scope != applicationID {
		t.Fatalf("scope = %q, %v", scope, err)
	}

	if _, _, err := decodeListInput("not-a-uuid", ""); err == nil {
		t.Fatal("a malformed application_id must be refused")
	} else if typed, ok := AsError(err); !ok || typed.Field != "application_id" || typed.Status != 422 {
		t.Fatalf("expected a located 422, got %v", err)
	}

	if _, _, err := decodeListInput("", "not-a-cursor"); err == nil {
		t.Fatal("a malformed cursor must be refused")
	} else if typed, ok := AsError(err); !ok || typed.Code != "invalid_cursor" || typed.Status != 400 {
		t.Fatalf("expected invalid_cursor, got %v", err)
	}

	at := time.Date(2026, 11, 4, 8, 0, 0, 123456000, time.UTC)
	cursor := catalog.EncodeCursor(at, "44444444-4444-4444-8444-444444444444")
	_, position, err = decodeListInput("", cursor)
	if err != nil {
		t.Fatalf("a cursor produced by catalog.EncodeCursor must decode: %v", err)
	}
	if !position.Set || !position.At.Equal(at) || position.ID != "44444444-4444-4444-8444-444444444444" {
		t.Fatalf("position = %+v", position)
	}
}

func TestListLimitsAreNormalisedNotClampedSilently(t *testing.T) {
	for value, want := range map[int]int{-1: DefaultListLimit, 0: DefaultListLimit, 5: 5, MaxListLimit: MaxListLimit, 1000: MaxListLimit} {
		if got := normaliseLimit(value); got != want {
			t.Fatalf("normaliseLimit(%d) = %d, want %d", value, got, want)
		}
	}
}

func TestTextDigestAndIntervalAreTheOnesTheStoreUses(t *testing.T) {
	if got := textDigest("abc"); got != "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad" {
		t.Fatalf("textDigest must be the hex SHA-256 of the bytes: %s", got)
	}
	if got := intervalOf(90 * time.Minute); got != "5400000 milliseconds" {
		t.Fatalf("intervalOf = %q", got)
	}
}

func TestTheServiceAlwaysHasAUsableCandidateTTL(t *testing.T) {
	if got := (&Service{}).candidateTTL(); got != DefaultCandidateTTL {
		t.Fatalf("zero ttl = %s, want %s", got, DefaultCandidateTTL)
	}
	if got := (&Service{CandidateTTL: time.Hour}).candidateTTL(); got != time.Hour {
		t.Fatalf("explicit ttl = %s", got)
	}
}

func TestAsErrorIgnoresNonTypedErrors(t *testing.T) {
	if _, ok := AsError(errors.New("plain")); ok {
		t.Fatal("a plain error must not become a typed refusal")
	}
	if _, ok := AsError(nil); ok {
		t.Fatal("nil must not become a typed refusal")
	}
}
