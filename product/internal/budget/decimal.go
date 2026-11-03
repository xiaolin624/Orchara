// Package budget is the F06/T046 shared decimal budget ledger.
//
// Rules fixed by IMPLEMENTATION-DETAILS §6/§7, EXECUTION §6 and the T046 brief:
//
//   - Money never touches a float. Every amount is a non-negative decimal
//     string parsed into math/big.Rat, rounded only at the numeric(20,8)
//     boundary: reservations round up (a reservation is conservative), actual
//     charges round half up. The ledger column is numeric(20,8), so exact
//     decimal strings are stored, summed and compared by PostgreSQL.
//   - `platform.usage_ledger` is the single budget authority. A reservation,
//     its release and its charge are three rows keyed by stable `entry_key`
//     values, so a replay, a retry or a later reconciliation is idempotent.
//   - Reservations are serialised on the budget root run row
//     (`SELECT ... FOR UPDATE`). Two concurrent calls can therefore never both
//     take the last reservation: the second one sees the first one's committed
//     reserve row and is refused with a typed ExhaustedError.
//   - A failed/cancelled attempt releases its full reservation; a successful
//     attempt releases the reservation and writes the actual charge (the
//     release cancels the hold, the charge is the money actually spent). A
//     provider answer with no usage keeps the conservative reservation
//     (`usage_missing`) until a later reconciliation settles it.
package budget

import (
	"errors"
	"fmt"
	"math/big"
	"regexp"
	"strings"
)

// Scale is the contract's numeric(20,8) fractional precision.
const Scale = 8

var (
	decimalPattern = regexp.MustCompile(`^\d{1,18}(\.\d{1,18})?$`)
	// storedAmountPattern is the numeric(20,8) shape: at most 12 integer digits
	// and at most 8 fractional digits, so no silent rounding can occur.
	storedAmountPattern = regexp.MustCompile(`^\d{1,12}(\.\d{1,8})?$`)
	scaleFactor         = new(big.Int).Exp(big.NewInt(10), big.NewInt(Scale), nil)
	scaleRat            = new(big.Rat).SetInt(scaleFactor)
	// maxUnits is one less than 10^20: numeric(20,8)'s largest representable
	// amount (12 integer digits and 8 fractional digits).
	maxUnits = new(big.Int).Sub(
		new(big.Int).Exp(big.NewInt(10), big.NewInt(20), nil), big.NewInt(1))
	million = big.NewRat(1000000, 1)
)

// Price is the profile's config price: input/output per million tokens in the
// run currency. It is the price version a charge is settled against.
type Price struct {
	Currency         string
	InputPerMillion  string
	OutputPerMillion string
}

// ZeroPrice is a stub/zero-cost profile: it reserves and charges 0, so a pure
// test model never exhausts a budget.
func ZeroPrice(currency string) Price {
	return Price{Currency: currency, InputPerMillion: "0", OutputPerMillion: "0"}
}

// ValidAmount reports whether value is a non-negative decimal string that can
// become a numeric(20,8) amount without silent rounding.
func ValidAmount(value string) bool {
	if !storedAmountPattern.MatchString(value) {
		return false
	}
	units, err := unitsFromString(value)
	if err != nil {
		return false
	}
	return units.Cmp(maxUnits) <= 0
}

// ParsePrice validates a configured price. Absent values are the caller's
// job; an explicit empty or non-decimal price is an error.
func ParsePrice(price Price) (Price, error) {
	if len(price.Currency) != 3 {
		return Price{}, fmt.Errorf("price currency must be exactly 3 characters")
	}
	for field, value := range map[string]string{
		"input_per_million":  price.InputPerMillion,
		"output_per_million": price.OutputPerMillion,
	} {
		if !decimalPattern.MatchString(value) {
			return Price{}, fmt.Errorf("%s must be a non-negative decimal string", field)
		}
	}
	return price, nil
}

// PriceVersion is the stable identity of the price a physical attempt was
// settled against. It names the profile, both per-million prices and the
// currency, so a later price edit is a new version, never a reinterpretation
// of an old charge.
func PriceVersion(profileVersionID string, price Price) string {
	return fmt.Sprintf("price-v1:%s:in=%s:out=%s:%s",
		profileVersionID, price.InputPerMillion, price.OutputPerMillion, price.Currency)
}

// EstimateReservation is the conservative pre-dispatch amount: the estimated
// input tokens at the input price plus max_output_tokens at the output price,
// rounded up to 8 decimals.
func EstimateReservation(price Price, inputTokens, maxOutputTokens int64) (string, error) {
	if inputTokens < 0 || maxOutputTokens < 0 {
		return "", errors.New("token counts must not be negative")
	}
	total := new(big.Rat)
	for _, part := range []struct {
		perMillion string
		tokens     int64
	}{
		{price.InputPerMillion, inputTokens},
		{price.OutputPerMillion, maxOutputTokens},
	} {
		rate, ok := new(big.Rat).SetString(part.perMillion)
		if !ok {
			return "", fmt.Errorf("invalid price %q", part.perMillion)
		}
		amount := new(big.Rat).Mul(rate, new(big.Rat).SetInt64(part.tokens))
		total.Add(total, amount.Quo(amount, million))
	}
	return formatUnitsFromRat(total, true)
}

// ActualCharge is the provider's actual usage at the recorded price: actual
// input tokens at the input price plus actual output tokens at the output
// price, rounded half up to 8 decimals.
func ActualCharge(price Price, tokensIn, tokensOut int64) (string, error) {
	if tokensIn < 0 || tokensOut < 0 {
		return "", errors.New("token counts must not be negative")
	}
	total := new(big.Rat)
	for _, part := range []struct {
		perMillion string
		tokens     int64
	}{
		{price.InputPerMillion, tokensIn},
		{price.OutputPerMillion, tokensOut},
	} {
		rate, ok := new(big.Rat).SetString(part.perMillion)
		if !ok {
			return "", fmt.Errorf("invalid price %q", part.perMillion)
		}
		amount := new(big.Rat).Mul(rate, new(big.Rat).SetInt64(part.tokens))
		total.Add(total, amount.Quo(amount, million))
	}
	return formatUnitsFromRat(total, false)
}

// CompareAmounts compares two valid decimal amounts without float conversion.
func CompareAmounts(left, right string) (int, error) {
	leftUnits, err := unitsFromString(left)
	if err != nil {
		return 0, err
	}
	rightUnits, err := unitsFromString(right)
	if err != nil {
		return 0, err
	}
	return leftUnits.Cmp(rightUnits), nil
}

// AddAmounts adds two valid decimal amounts exactly.
func AddAmounts(left, right string) (string, error) {
	leftUnits, err := unitsFromString(left)
	if err != nil {
		return "", err
	}
	rightUnits, err := unitsFromString(right)
	if err != nil {
		return "", err
	}
	return formatUnits(new(big.Int).Add(leftUnits, rightUnits))
}

// SubtractAmounts computes left-right exactly; a negative result is refused
// because the ledger vocabulary has no negative amount.
func SubtractAmounts(left, right string) (string, error) {
	leftUnits, err := unitsFromString(left)
	if err != nil {
		return "", err
	}
	rightUnits, err := unitsFromString(right)
	if err != nil {
		return "", err
	}
	difference := new(big.Int).Sub(leftUnits, rightUnits)
	if difference.Sign() < 0 {
		return "", errors.New("amount would be negative")
	}
	return formatUnits(difference)
}

func unitsFromString(value string) (*big.Int, error) {
	if !decimalPattern.MatchString(value) {
		return nil, fmt.Errorf("%q is not a non-negative decimal string", value)
	}
	rational, ok := new(big.Rat).SetString(value)
	if !ok {
		return nil, fmt.Errorf("%q is not a decimal amount", value)
	}
	return unitsFromRat(rational, false)
}

// unitsFromRat quantises a rational amount to 1e-8 units. roundUp is the
// reservation rule (ceil); otherwise half-up is used for actual charges.
func unitsFromRat(value *big.Rat, roundUp bool) (*big.Int, error) {
	if value.Sign() < 0 {
		return nil, errors.New("negative amount")
	}
	scaled := new(big.Rat).Mul(value, scaleRat)
	numerator := scaled.Num()
	denominator := scaled.Denom()
	units, remainder := new(big.Int).QuoRem(numerator, denominator, new(big.Int))
	if remainder.Sign() > 0 {
		if roundUp {
			units.Add(units, big.NewInt(1))
		} else if new(big.Int).Lsh(remainder, 1).Cmp(denominator) >= 0 {
			units.Add(units, big.NewInt(1))
		}
	}
	if units.Sign() < 0 || units.Cmp(maxUnits) > 0 {
		return nil, fmt.Errorf("amount does not fit numeric(20,8)")
	}
	return units, nil
}

func formatUnits(units *big.Int) (string, error) {
	if units == nil || units.Sign() < 0 {
		return "", errors.New("invalid amount units")
	}
	digits := units.String()
	if len(digits) <= Scale {
		digits = strings.Repeat("0", Scale-len(digits)+1) + digits
	}
	return digits[:len(digits)-Scale] + "." + digits[len(digits)-Scale:], nil
}

// FormatUnitsString is the canonical numeric(20,8) rendering of a stored
// amount; internal tests use it to compare exact strings.
func FormatUnitsString(units *big.Int) string {
	formatted, err := formatUnits(units)
	if err != nil {
		return ""
	}
	return formatted
}

// formatUnitsFromRat quantises and renders in one step.
func formatUnitsFromRat(value *big.Rat, roundUp bool) (string, error) {
	units, err := unitsFromRat(value, roundUp)
	if err != nil {
		return "", err
	}
	return formatUnits(units)
}
