package budget

import (
	"errors"
	"testing"
)

// Expected values are worked examples of the contract, not a reimplementation:
// 1,000,000 tokens at 3 per million is exactly 3; a price with 8 decimals and a
// large token count must not drift through float64.
func TestReservationAndChargeAreExactDecimals(t *testing.T) {
	price := Price{Currency: "USD", InputPerMillion: "3", OutputPerMillion: "7"}
	reserved, err := EstimateReservation(price, 1_000_000, 2_000_000)
	if err != nil {
		t.Fatalf("EstimateReservation: %v", err)
	}
	if reserved != "17.00000000" {
		t.Fatalf("want 17.00000000, got %s", reserved)
	}
	charged, err := ActualCharge(price, 1_000_000, 2_000_000)
	if err != nil {
		t.Fatalf("ActualCharge: %v", err)
	}
	if charged != "17.00000000" {
		t.Fatalf("want 17.00000000, got %s", charged)
	}
}

func TestReservationRoundsUpAndChargeRoundsHalfUp(t *testing.T) {
	price := Price{Currency: "USD", InputPerMillion: "0.00000001", OutputPerMillion: "0"}
	// 1 token at 1e-8 per million is 1e-14: below one unit, so a reservation
	// (ceil) is one unit while an actual charge (half up) is zero.
	reserved, err := EstimateReservation(price, 1, 0)
	if err != nil {
		t.Fatalf("EstimateReservation: %v", err)
	}
	if reserved != "0.00000001" {
		t.Fatalf("want ceil 0.00000001, got %s", reserved)
	}
	charged, err := ActualCharge(price, 1, 0)
	if err != nil {
		t.Fatalf("ActualCharge: %v", err)
	}
	if charged != "0.00000000" {
		t.Fatalf("want half-up 0.00000000, got %s", charged)
	}
	// Exactly half a unit rounds up for an actual charge: 0.005 per million
	// tokens for one token is 5e-9, which is half of 1e-8.
	half := Price{Currency: "USD", InputPerMillion: "0.005", OutputPerMillion: "0"}
	charged, err = ActualCharge(half, 1, 0)
	if err != nil {
		t.Fatalf("ActualCharge half: %v", err)
	}
	if charged != "0.00000001" {
		t.Fatalf("want half-up 0.00000001, got %s", charged)
	}
}

func TestNoFloatDriftAtNumericLimit(t *testing.T) {
	// The largest numeric(20,8) amount is exactly representable, and one more
	// unit is refused instead of silently wrapping.
	price := Price{Currency: "USD", InputPerMillion: "999999999999.99999999", OutputPerMillion: "0"}
	charged, err := ActualCharge(price, 1_000_000, 0)
	if err != nil {
		t.Fatalf("ActualCharge: %v", err)
	}
	if charged != "999999999999.99999999" {
		t.Fatalf("want 999999999999.99999999, got %s", charged)
	}
	if !ValidAmount(charged) {
		t.Fatalf("a numeric(20,8) amount must be valid: %s", charged)
	}
	if _, err := ActualCharge(price, 1_000_001, 0); err == nil {
		t.Fatal("numeric(20,8) overflow must be refused")
	}
	if ValidAmount("1000000000000.00000000") {
		t.Fatal("thirteen integer digits must be refused")
	}
}

func TestAmountsRejectNegativesExponentsAndNaN(t *testing.T) {
	for _, value := range []string{
		"-1", "1e-3", "NaN", "Inf", "1.2.3", "", "01.000000001",
		"0.000000001", // nine fractional digits
	} {
		if ValidAmount(value) {
			t.Fatalf("%q must be refused", value)
		}
	}
}

func TestPriceVersionNamesThePriceNotTheWireFormat(t *testing.T) {
	price := Price{Currency: "USD", InputPerMillion: "1", OutputPerMillion: "2"}
	version := PriceVersion("22222222-2222-4222-8222-222222222222", price)
	want := "price-v1:22222222-2222-4222-8222-222222222222:in=1:out=2:USD"
	if version != want {
		t.Fatalf("want %s, got %s", want, version)
	}
	if !ValidAmount("0") || !ValidAmount("0.00000001") {
		t.Fatal("zero and one unit are valid ledger amounts")
	}
}

func TestCompareAndAddAmountsAreExact(t *testing.T) {
	comparison, err := CompareAmounts("0.1", "0.10000000")
	if err != nil || comparison != 0 {
		t.Fatalf("0.1 must equal 0.10000000: %v %v", comparison, err)
	}
	sum, err := AddAmounts("999999999999.99999999", "0.00000001")
	if err != nil || sum != "1000000000000.00000000" {
		t.Fatalf("exact sum: %q %v", sum, err)
	}
	if _, err := SubtractAmounts("0.00000001", "0.00000002"); err == nil {
		t.Fatal("a negative ledger amount must be refused")
	}
}

func TestAttemptKeyIsStablePerPhysicalAttempt(t *testing.T) {
	operation := "33333333-3333-4333-8333-333333333333"
	if AttemptKey(operation, 1) != operation+"#1" {
		t.Fatalf("attempt key: %s", AttemptKey(operation, 1))
	}
	if reserveKey(AttemptKey(operation, 1)) == reserveKey(AttemptKey(operation, 2)) {
		t.Fatal("different physical attempts must not share a reservation key")
	}
}

func TestTypedBudgetErrorsAreIdentifiable(t *testing.T) {
	var exhausted *ExhaustedError
	err := error(&ExhaustedError{Required: "1.00000000", Remaining: "0.50000000", Currency: "USD"})
	if !errors.As(err, &exhausted) || exhausted.Remaining != "0.50000000" {
		t.Fatalf("typed refusal lost: %v", err)
	}
	var noReservation *NoReservationError
	if !errors.As(error(&NoReservationError{AttemptKey: "x#1"}), &noReservation) {
		t.Fatal("typed missing reservation lost")
	}
}
