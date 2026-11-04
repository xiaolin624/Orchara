package capabilities

// F06/T046: the budget seam between the capability service and internal/budget.
//
// A model physical attempt reserves the conservative price-version amount
// before the provider is touched, then either settles the actual usage, releases
// the whole reservation on failure, or keeps it when the provider reported no
// usage. The price version is the model profile's registered config price, so a
// later price edit cannot reinterpret a recorded charge.

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"strings"

	"orchara/platform/internal/budget"
	"orchara/platform/internal/uuid"
)

// modelPrice is the resolved price/version of one profile under one run's
// currency. A profile without a configured price is the zero-price stub: it
// reserves 0, so pure/development calls never starve a real budget.
type modelPrice struct {
	price     budget.Price
	version   string
	maxOutput int64
}

// priceForProfile resolves the profile's config price. The run currency is the
// ledger currency; a profile priced in another currency is refused later by the
// ledger's CurrencyMismatchError rather than mixed into one decimal sum.
func priceForProfile(profile *ModelProfile, runCurrency string) (*modelPrice, *Error) {
	if profile == nil {
		return nil, capFailure(500, CodeInternal, "the model profile is missing")
	}
	price := budget.ZeroPrice(runCurrency)
	if raw, present := profile.Config["price"]; present && raw != nil {
		object, ok := raw.(map[string]any)
		if !ok {
			return nil, capFailure(422, CodeBudgetPriceInvalid,
				"the model profile price must be an object")
		}
		price = budget.Price{
			Currency:         configString(object, "currency"),
			InputPerMillion:  configString(object, "input_per_million"),
			OutputPerMillion: configString(object, "output_per_million"),
		}
	}
	if _, err := budget.ParsePrice(price); err != nil {
		return nil, capFailure(422, CodeBudgetPriceInvalid,
			"the model profile price is not a decimal price")
	}
	return &modelPrice{
		price:     price,
		version:   budget.PriceVersion(profile.ID, price),
		maxOutput: maxOutputTokensFor(profile),
	}, nil
}

func configString(object map[string]any, key string) string {
	value, _ := object[key].(string)
	return strings.TrimSpace(value)
}

// reservationFor computes the conservative reservation request of one physical
// attempt of this profile. Estimated input tokens are derived from the canonical
// params shape exactly as the compatibility check estimates them (one token per
// four bytes); output is the profile's max_output_tokens.
func reservationFor(profile *ModelProfile, run *runContext, params map[string]any) (*budget.ReserveRequest, *Error) {
	pricing, capErr := priceForProfile(profile, run.Currency)
	if capErr != nil {
		return nil, capErr
	}
	inputTokens := estimatedInputTokens(params)
	amount, err := budget.EstimateReservation(pricing.price, inputTokens, pricing.maxOutput)
	if err != nil {
		return nil, capFailure(422, CodeBudgetPriceInvalid,
			"the model profile price cannot be applied to this call")
	}
	tokensIn := inputTokens
	tokensOut := pricing.maxOutput
	return &budget.ReserveRequest{
		Amount: amount, Currency: pricing.price.Currency, PriceVersion: pricing.version,
		TokensIn: &tokensIn, TokensOut: &tokensOut,
	}, nil
}

func estimatedInputTokens(params map[string]any) int64 {
	raw, err := json.Marshal(params)
	if err != nil {
		return 0
	}
	return int64((len(raw) + 3) / 4)
}

// mapBudgetError turns the ledger's typed errors into the capability error
// vocabulary. A refusal is a platform failure (429 budget_exhausted) carrying
// the remaining amount; it never becomes a provider attempt.
func mapBudgetError(err error) *Error {
	var exhausted *budget.ExhaustedError
	if errors.As(err, &exhausted) {
		return &Error{
			Status:  429,
			Code:    CodeBudgetExhausted,
			Message: "the run budget does not fit the next model call reservation",
			Details: map[string]any{
				"remaining_amount": exhausted.Remaining,
				"required_amount":  exhausted.Required,
				"currency":         exhausted.Currency,
			},
		}
	}
	var mismatch *budget.CurrencyMismatchError
	if errors.As(err, &mismatch) {
		return &Error{
			Status:  422,
			Code:    CodeBudgetCurrencyMismatch,
			Message: "the model profile price currency does not match the run budget",
			Details: map[string]any{
				"run_currency":   mismatch.RunCurrency,
				"price_currency": mismatch.PriceCurrency,
			},
		}
	}
	var noReservation *budget.NoReservationError
	if errors.As(err, &noReservation) {
		return capFailure(409, CodeUsageNotReserved, "the attempt has no budget reservation")
	}
	return capFailure(500, CodeInternal, "the budget ledger could not record the call")
}

// settleFailureUsage reports whether a provider answer whose projection failed
// still proves real token consumption. A nil projection or a missing/unusable
// usage object means the platform cannot price the call: the conservative
// reservation must be kept for maintenance reconciliation instead of released.
func settleFailureUsage(projection *providerProjection) (tokensIn, tokensOut int64, ok bool) {
	if projection == nil || projection.Usage == nil {
		return 0, 0, false
	}
	return projection.TokensIn, projection.TokensOut, true
}

// ReconcileRequest is the maintenance-owned settlement of a retained
// missing-usage reservation.
type ReconcileRequest struct {
	OperationID  string
	AttemptNo    int
	Amount       string
	PriceVersion string
	TokensIn     *int64
	TokensOut    *int64
}

// reconcileResult is the safe answer of a reconciliation.
type reconcileResult struct {
	OperationID    string
	AttemptNo      int
	ChargedAmount  string
	Currency       string
	AlreadySettled bool
}

// ReconcileUsage settles a usage_missing attempt. It is maintenance-only: the
// worker that made the call cannot invent its own cost. The attempt must already
// carry a retained reservation; the reconciliation then charges the actual
// amount and releases the hold with the ledger's idempotent entry keys.
func (s *Service) ReconcileUsage(ctx context.Context, role, runID string, request ReconcileRequest) (*reconcileResult, *Error) {
	if role != "maintenance" {
		return nil, capFailure(403, "forbidden", "only the maintenance capacity may reconcile usage")
	}
	if s.Budget == nil {
		return nil, capFailure(503, "budget_unavailable", "the budget ledger is not configured")
	}
	if !uuid.Valid(runID) {
		return nil, capFailure(404, CodeRunNotFound, "that run does not exist")
	}
	if request.AttemptNo <= 0 {
		return nil, validationFailure("attempt_no", "attempt_no must be positive")
	}
	if !budget.ValidAmount(request.Amount) {
		return nil, validationFailure("amount", "amount must be a non-negative decimal with at most 8 fractional digits")
	}
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return nil, capFailure(500, CodeInternal, "the reconciliation transaction could not start")
	}
	defer func() { _ = tx.Rollback() }()
	var operationID, settlementState, currency string
	var recordedPrice, recordedCharge string
	err = tx.QueryRowContext(ctx, `
		SELECT o.id::text, a.settlement_state, COALESCE(a.currency, r.currency),
		       COALESCE(a.price_version, ''), COALESCE(a.charged_amount::text, '')
		  FROM platform.operations AS o
		  JOIN platform.operation_attempts AS a ON a.operation_id = o.id
		  JOIN platform.runs AS r ON r.id = o.run_id
		 WHERE o.run_id = $1::uuid AND o.id = $2::uuid AND a.attempt_no = $3`,
		runID, request.OperationID, request.AttemptNo,
	).Scan(&operationID, &settlementState, &currency, &recordedPrice, &recordedCharge)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, capFailure(404, CodeOperationNotFound, "that operation attempt does not exist for this run")
	}
	if err != nil {
		return nil, capFailure(500, CodeInternal, "the operation attempt could not be read")
	}
	if settlementState != "usage_missing" && settlementState != "settled" {
		return nil, capFailure(409, CodeUsageNotReserved,
			"only a retained missing-usage reservation can be reconciled")
	}
	if settlementState == "settled" {
		return &reconcileResult{
			OperationID: operationID, AttemptNo: request.AttemptNo,
			ChargedAmount: recordedCharge, Currency: currency, AlreadySettled: true,
		}, nil
	}
	priceVersion := request.PriceVersion
	if priceVersion == "" {
		priceVersion = recordedPrice
	}
	if priceVersion == "" {
		return nil, capFailure(409, CodeUsageNotReserved, "the attempt has no price version to settle against")
	}
	settled, err := s.Budget.SettleTx(ctx, tx, budget.Settlement{
		AttemptKey:   budget.AttemptKey(operationID, request.AttemptNo),
		Amount:       request.Amount,
		Currency:     currency,
		PriceVersion: priceVersion,
		TokensIn:     request.TokensIn,
		TokensOut:    request.TokensOut,
	})
	if err != nil {
		return nil, mapBudgetError(err)
	}
	if _, err := tx.ExecContext(ctx, `
		UPDATE platform.operation_attempts
		   SET charged_amount = $3::numeric, tokens_in = $4, tokens_out = $5,
		       price_version = $6, settlement_state = 'settled', finished_at = COALESCE(finished_at, now())
		 WHERE operation_id = $1::uuid AND attempt_no = $2
		   AND settlement_state = 'usage_missing'`,
		operationID, request.AttemptNo, settled.ChargedAmount,
		request.TokensIn, request.TokensOut, priceVersion,
	); err != nil {
		return nil, capFailure(500, CodeInternal, "the reconciled usage could not be recorded")
	}
	if err := tx.Commit(); err != nil {
		return nil, capFailure(500, CodeInternal, "the reconciled usage could not be committed")
	}
	return &reconcileResult{
		OperationID: operationID, AttemptNo: request.AttemptNo,
		ChargedAmount: settled.ChargedAmount, Currency: settled.Currency,
	}, nil
}
