package budget

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"math/big"

	"orchara/platform/internal/uuid"
)

// Ledger is the authoritative decimal budget ledger. It owns no provider code:
// internal/capabilities reserves before a physical attempt and settles or
// releases after it.
type Ledger struct {
	DB *sql.DB
}

func New(db *sql.DB) *Ledger {
	return &Ledger{DB: db}
}

// ReserveRequest is one physical attempt's pre-dispatch reservation. AttemptKey
// is unique per physical attempt (operation id + attempt number) and is the
// idempotency anchor of the reserve/release/charge triple.
type ReserveRequest struct {
	RunID        string
	OperationID  string
	AttemptKey   string
	Amount       string
	Currency     string
	PriceVersion string
	TokensIn     *int64
	TokensOut    *int64
}

// Reservation is the committed hold.
type Reservation struct {
	EntryKey        string
	RunID           string
	BudgetRootRunID string
	Amount          string
	Currency        string
	PriceVersion    string
	RemainingBefore string
}

// Settlement is the actual-usage answer of one reservation. Amount must be the
// exact price-version charge of the reported tokens; a zero amount is legal
// (zero-priced stub profile) and still releases the reservation.
type Settlement struct {
	AttemptKey   string
	Amount       string
	Currency     string
	PriceVersion string
	TokensIn     *int64
	TokensOut    *int64
}

// SettlementResult is the idempotent outcome of Settle.
type SettlementResult struct {
	ReservedAmount string
	ChargedAmount  string
	ReleasedAmount string
	Currency       string
	AlreadySettled bool
}

// ReleaseResult is the outcome of Release.
type ReleaseResult struct {
	ReleasedAmount string
	Currency       string
	AlreadySettled bool
}

// Totals is the run-tree projection the owner read and the UI render.
type Totals struct {
	BudgetRootRunID string
	BudgetLimit     string
	Currency        string
	Reserved        string // outstanding holds: sum(reserve)-sum(release)
	Charged         string // sum(charge)
	Released        string // sum(release), for audit
	Remaining       string // budget_limit - charged - outstanding
	Overrun         bool
}

// Allowance is the shared run-tree allowance at one instant, read while holding
// the root Run's row lock. It is the same decimal arithmetic ReserveTx applies
// (limit - charged - outstanding, clamped at zero), so a caller can decide
// something *from* the allowance without inventing a second accounting path or
// contradicting a concurrent reservation.
type Allowance struct {
	RootRunID   string
	Limit       string
	Currency    string
	Charged     string
	Outstanding string
	Remaining   string
	Overrun     bool
}

// LockAllowanceTx resolves the tree's root Run and locks it exactly like
// ReserveTx, then returns the authoritative decimal allowance. The lock is the
// atomicity point: every reservation, settlement and release of the tree
// serialises on the same row, so the returned Remaining is a consistent
// snapshot. Nothing is written.
//
// F07/T080 uses it to freeze a child Run's budget from the parent's shared
// allowance and to refuse a delegation when nothing remains.
func (l *Ledger) LockAllowanceTx(
	ctx context.Context, tx *sql.Tx, runID string,
) (*Allowance, error) {
	root, limit, currency, err := lockBudgetRoot(ctx, tx, runID)
	if err != nil {
		return nil, err
	}
	charged, outstanding, err := ledgerTotalsTx(ctx, tx, root)
	if err != nil {
		return nil, err
	}
	consumed, err := addAmounts(charged, outstanding)
	if err != nil {
		return nil, err
	}
	remaining, overrun := remainingOf(limit, consumed)
	return &Allowance{
		RootRunID: root, Limit: limit, Currency: currency,
		Charged: charged, Outstanding: outstanding,
		Remaining: remaining, Overrun: overrun,
	}, nil
}

// ExhaustedError is the typed budget refusal: the conservative reservation
// does not fit the run tree's remaining budget. Remaining is the amount that was
// available before the refused reservation.
type ExhaustedError struct {
	Required  string
	Remaining string
	Currency  string
}

func (e *ExhaustedError) Error() string {
	return fmt.Sprintf("budget_exhausted: %s %s required, %s %s remaining",
		e.Required, e.Currency, e.Remaining, e.Currency)
}

// CurrencyMismatchError refuses a profile priced in a currency other than the
// run budget's: mixing currencies in one decimal ledger is never safe.
type CurrencyMismatchError struct {
	RunCurrency   string
	PriceCurrency string
}

func (e *CurrencyMismatchError) Error() string {
	return fmt.Sprintf("budget_currency_mismatch: run budget is %s, profile price is %s",
		e.RunCurrency, e.PriceCurrency)
}

// NoReservationError is a settlement without a reserve row.
type NoReservationError struct{ AttemptKey string }

func (e *NoReservationError) Error() string {
	return "attempt_not_reserved: " + e.AttemptKey
}

// ErrEntryConflict: an existing entry_key was reused with a different amount.
var ErrEntryConflict = errors.New("budget_entry_conflict")

// ErrSettled can be compared with errors.Is when a release/charge would change
// an already settled attempt.
var ErrSettled = errors.New("budget_attempt_already_settled")

// AttemptKey is the stable key of one physical attempt.
func AttemptKey(operationID string, attemptNo int) string {
	return fmt.Sprintf("%s#%d", operationID, attemptNo)
}

func reserveKey(attemptKey string) string { return attemptKey + ":reserve" }
func releaseKey(attemptKey string) string { return attemptKey + ":release" }
func chargeKey(attemptKey string) string  { return attemptKey + ":charge" }

// Reserve runs one reservation transaction.
func (l *Ledger) Reserve(ctx context.Context, request ReserveRequest) (*Reservation, error) {
	tx, err := l.DB.BeginTx(ctx, nil)
	if err != nil {
		return nil, err
	}
	defer func() { _ = tx.Rollback() }()
	reservation, err := l.ReserveTx(ctx, tx, request)
	if err != nil {
		return nil, err
	}
	if err := tx.Commit(); err != nil {
		return nil, err
	}
	return reservation, nil
}

// ReserveTx is Reserve inside the caller's transaction. internal/capabilities
// uses it so the attempt row and its reservation commit atomically: there is no
// window in which a dispatching attempt has no budget hold.
func (l *Ledger) ReserveTx(ctx context.Context, tx *sql.Tx, request ReserveRequest) (*Reservation, error) {
	if request.Amount == "" {
		request.Amount = "0"
	}
	if !ValidAmount(request.Amount) {
		return nil, fmt.Errorf("reservation amount %q is not a valid decimal", request.Amount)
	}
	if request.AttemptKey == "" || request.OperationID == "" || request.RunID == "" {
		return nil, errors.New("reservation needs run, operation and attempt key")
	}
	root, limit, currency, err := lockBudgetRoot(ctx, tx, request.RunID)
	if err != nil {
		return nil, err
	}
	if request.Currency != "" && request.Currency != currency {
		return nil, &CurrencyMismatchError{RunCurrency: currency, PriceCurrency: request.Currency}
	}
	entryKey := reserveKey(request.AttemptKey)
	existing, err := entryAmount(ctx, tx, entryKey)
	if err != nil {
		return nil, err
	}
	if existing != "" {
		if cmp, err := CompareAmounts(existing, request.Amount); err != nil || cmp != 0 {
			return nil, ErrEntryConflict
		}
		return &Reservation{
			EntryKey: entryKey, RunID: request.RunID, BudgetRootRunID: root,
			Amount: existing, Currency: currency, PriceVersion: request.PriceVersion,
			RemainingBefore: "0.00000000",
		}, nil
	}
	charged, outstanding, err := ledgerTotalsTx(ctx, tx, root)
	if err != nil {
		return nil, err
	}
	remaining, err := subtractAmounts(limit, charged, outstanding)
	if err != nil {
		return nil, err
	}
	if cmp, err := CompareAmounts(request.Amount, remaining); err != nil {
		return nil, err
	} else if cmp > 0 {
		return nil, &ExhaustedError{Required: request.Amount, Remaining: remaining, Currency: currency}
	}
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO platform.usage_ledger
			(id, run_id, budget_root_run_id, operation_id, entry_key, kind,
			 amount, currency, tokens_in, tokens_out, price_version)
		VALUES ($1::uuid, $2::uuid, $3::uuid, $4::uuid, $5, 'reserve',
		        $6::numeric, $7, $8, $9, $10)`,
		uuid.NewString(), request.RunID, root, request.OperationID, entryKey, request.Amount,
		currency, request.TokensIn, request.TokensOut, request.PriceVersion,
	); err != nil {
		if isUniqueViolation(err) {
			return nil, ErrEntryConflict
		}
		return nil, err
	}
	return &Reservation{
		EntryKey: entryKey, RunID: request.RunID, BudgetRootRunID: root,
		Amount: request.Amount, Currency: currency, PriceVersion: request.PriceVersion,
		RemainingBefore: remaining,
	}, nil
}

// Settle charges the actual usage and releases the paired reservation in one
// transaction. It is idempotent by entry_key: a replay of the same settlement
// returns the recorded amounts and changes nothing.
func (l *Ledger) Settle(ctx context.Context, settlement Settlement) (*SettlementResult, error) {
	tx, err := l.DB.BeginTx(ctx, nil)
	if err != nil {
		return nil, err
	}
	defer func() { _ = tx.Rollback() }()
	result, err := l.SettleTx(ctx, tx, settlement)
	if err != nil {
		return nil, err
	}
	if err := tx.Commit(); err != nil {
		return nil, err
	}
	return result, nil
}

// SettleTx is Settle inside the caller's transaction so a receipt, its
// operation state and its budget settlement commit together.
func (l *Ledger) SettleTx(ctx context.Context, tx *sql.Tx, settlement Settlement) (*SettlementResult, error) {
	if settlement.AttemptKey == "" {
		return nil, errors.New("settlement needs an attempt key")
	}
	if settlement.Amount == "" {
		settlement.Amount = "0"
	}
	if !ValidAmount(settlement.Amount) {
		return nil, fmt.Errorf("charge amount %q is not a valid decimal", settlement.Amount)
	}
	reserve, err := reservationRow(ctx, tx, reserveKey(settlement.AttemptKey))
	if err != nil {
		return nil, err
	}
	if reserve == nil {
		return nil, &NoReservationError{AttemptKey: settlement.AttemptKey}
	}
	if _, _, _, err := lockBudgetRoot(ctx, tx, reserve.RunID); err != nil {
		return nil, err
	}
	existingCharge, err := entryAmount(ctx, tx, chargeKey(settlement.AttemptKey))
	if err != nil {
		return nil, err
	}
	existingRelease, err := entryAmount(ctx, tx, releaseKey(settlement.AttemptKey))
	if err != nil {
		return nil, err
	}
	if existingCharge != "" || existingRelease != "" {
		charged := existingCharge
		if charged == "" {
			charged = "0.00000000"
		}
		released := existingRelease
		if released == "" {
			released = "0.00000000"
		}
		return &SettlementResult{
			ReservedAmount: reserve.Amount, ChargedAmount: charged,
			ReleasedAmount: released, Currency: reserve.Currency, AlreadySettled: true,
		}, nil
	}
	priceVersion := settlement.PriceVersion
	if priceVersion == "" {
		priceVersion = reserve.PriceVersion
	}
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO platform.usage_ledger
			(id, run_id, budget_root_run_id, operation_id, entry_key, kind,
			 amount, currency, tokens_in, tokens_out, price_version)
		VALUES ($1::uuid, $2::uuid, $3::uuid, $4::uuid, $5, 'release',
		        $6::numeric, $7, NULL, NULL, $8)`,
		uuid.NewString(), reserve.RunID, reserve.BudgetRootRunID, reserve.OperationID,
		releaseKey(settlement.AttemptKey), reserve.Amount, reserve.Currency, priceVersion,
	); err != nil {
		return nil, err
	}
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO platform.usage_ledger
			(id, run_id, budget_root_run_id, operation_id, entry_key, kind,
			 amount, currency, tokens_in, tokens_out, price_version)
		VALUES ($1::uuid, $2::uuid, $3::uuid, $4::uuid, $5, 'charge',
		        $6::numeric, $7, $8, $9, $10)`,
		uuid.NewString(), reserve.RunID, reserve.BudgetRootRunID, reserve.OperationID,
		chargeKey(settlement.AttemptKey), settlement.Amount,
		reserve.Currency, settlement.TokensIn, settlement.TokensOut, priceVersion,
	); err != nil {
		return nil, err
	}
	return &SettlementResult{
		ReservedAmount: reserve.Amount, ChargedAmount: settlement.Amount,
		ReleasedAmount: reserve.Amount, Currency: reserve.Currency,
	}, nil
}

// Release returns a failed/cancelled attempt's full reservation. It is
// idempotent and never charges anything.
func (l *Ledger) Release(ctx context.Context, attemptKey string) (*ReleaseResult, error) {
	tx, err := l.DB.BeginTx(ctx, nil)
	if err != nil {
		return nil, err
	}
	defer func() { _ = tx.Rollback() }()
	result, err := l.ReleaseTx(ctx, tx, attemptKey)
	if err != nil {
		return nil, err
	}
	if err := tx.Commit(); err != nil {
		return nil, err
	}
	return result, nil
}

// ReleaseTx is Release inside the caller's transaction.
func (l *Ledger) ReleaseTx(ctx context.Context, tx *sql.Tx, attemptKey string) (*ReleaseResult, error) {
	reserve, err := reservationRow(ctx, tx, reserveKey(attemptKey))
	if err != nil {
		return nil, err
	}
	if reserve == nil {
		return nil, nil
	}
	if _, _, _, err := lockBudgetRoot(ctx, tx, reserve.RunID); err != nil {
		return nil, err
	}
	existingRelease, err := entryAmount(ctx, tx, releaseKey(attemptKey))
	if err != nil {
		return nil, err
	}
	if existingRelease != "" {
		return &ReleaseResult{ReleasedAmount: existingRelease, Currency: reserve.Currency, AlreadySettled: true}, nil
	}
	charge, err := entryAmount(ctx, tx, chargeKey(attemptKey))
	if err != nil {
		return nil, err
	}
	if charge != "" {
		return nil, ErrSettled
	}
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO platform.usage_ledger
			(id, run_id, budget_root_run_id, operation_id, entry_key, kind,
			 amount, currency, tokens_in, tokens_out, price_version)
		VALUES ($1::uuid, $2::uuid, $3::uuid, $4::uuid, $5, 'release',
		        $6::numeric, $7, NULL, NULL, $8)`,
		uuid.NewString(), reserve.RunID, reserve.BudgetRootRunID, reserve.OperationID,
		releaseKey(attemptKey), reserve.Amount, reserve.Currency, reserve.PriceVersion,
	); err != nil {
		return nil, err
	}
	return &ReleaseResult{ReleasedAmount: reserve.Amount, Currency: reserve.Currency}, nil
}

// Totals reads the run tree's budget facts. It is a read, not a lock; the
// authoritative check remains the row lock inside Reserve.
func (l *Ledger) Totals(ctx context.Context, runID string) (*Totals, error) {
	root, limit, currency, err := l.budgetRoot(ctx, runID)
	if err != nil {
		return nil, err
	}
	charged, outstanding, err := l.totals(ctx, root)
	if err != nil {
		return nil, err
	}
	consumed, err := addAmounts(charged, outstanding)
	if err != nil {
		return nil, err
	}
	remaining, overrun := remainingOf(limit, consumed)
	released, err := l.released(ctx, root)
	if err != nil {
		return nil, err
	}
	return &Totals{
		BudgetRootRunID: root, BudgetLimit: limit, Currency: currency,
		Reserved: outstanding, Charged: charged, Released: released,
		Remaining: remaining, Overrun: overrun,
	}, nil
}

type reservation struct {
	RunID           string
	BudgetRootRunID string
	OperationID     string
	Amount          string
	Currency        string
	PriceVersion    string
}

func reservationRow(ctx context.Context, tx *sql.Tx, entryKey string) (*reservation, error) {
	var row reservation
	err := tx.QueryRowContext(ctx, `
		SELECT run_id::text, budget_root_run_id::text, operation_id::text,
		       amount::text, currency, price_version
		  FROM platform.usage_ledger
		 WHERE entry_key = $1 AND kind = 'reserve'`, entryKey,
	).Scan(&row.RunID, &row.BudgetRootRunID, &row.OperationID,
		&row.Amount, &row.Currency, &row.PriceVersion)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &row, nil
}

func entryAmount(ctx context.Context, tx *sql.Tx, entryKey string) (string, error) {
	var amount string
	err := tx.QueryRowContext(ctx, `
		SELECT amount::text FROM platform.usage_ledger WHERE entry_key = $1`, entryKey,
	).Scan(&amount)
	if errors.Is(err, sql.ErrNoRows) {
		return "", nil
	}
	if err != nil {
		return "", err
	}
	return amount, nil
}

// lockBudgetRoot resolves the topmost ancestor and locks its run row. Every
// reservation of the tree serialises here; the lock is held until the caller's
// transaction commits or rolls back.
func lockBudgetRoot(ctx context.Context, tx *sql.Tx, runID string) (root, limit, currency string, err error) {
	if err := tx.QueryRowContext(ctx, `
		WITH RECURSIVE chain AS (
		    SELECT id, parent_run_id FROM platform.runs WHERE id = $1::uuid
		    UNION ALL
		    SELECT r.id, r.parent_run_id
		      FROM platform.runs AS r JOIN chain AS c ON r.id = c.parent_run_id
		)
		SELECT id::text FROM chain WHERE parent_run_id IS NULL LIMIT 1`, runID,
	).Scan(&root); err != nil {
		return "", "", "", err
	}
	if err := tx.QueryRowContext(ctx, `
		SELECT budget_limit::text, currency FROM platform.runs
		 WHERE id = $1::uuid FOR UPDATE`, root,
	).Scan(&limit, &currency); err != nil {
		return "", "", "", err
	}
	return root, limit, currency, nil
}

func (l *Ledger) budgetRoot(ctx context.Context, runID string) (root, limit, currency string, err error) {
	err = l.DB.QueryRowContext(ctx, `
		WITH RECURSIVE chain AS (
		    SELECT id, parent_run_id FROM platform.runs WHERE id = $1::uuid
		    UNION ALL
		    SELECT r.id, r.parent_run_id
		      FROM platform.runs AS r JOIN chain AS c ON r.id = c.parent_run_id
		)
		SELECT id::text FROM chain WHERE parent_run_id IS NULL LIMIT 1`, runID,
	).Scan(&root)
	if err != nil {
		return "", "", "", err
	}
	err = l.DB.QueryRowContext(ctx, `
		SELECT budget_limit::text, currency FROM platform.runs WHERE id = $1::uuid`, root,
	).Scan(&limit, &currency)
	if err != nil {
		return "", "", "", err
	}
	return root, limit, currency, nil
}

func ledgerTotalsTx(ctx context.Context, tx *sql.Tx, root string) (charged, outstanding string, err error) {
	err = tx.QueryRowContext(ctx, `
		SELECT
		    COALESCE(sum(CASE kind WHEN 'charge' THEN amount ELSE 0 END), 0)::text,
		    COALESCE(sum(CASE kind WHEN 'reserve' THEN amount
		                     WHEN 'release' THEN -amount ELSE 0 END), 0)::text
		  FROM platform.usage_ledger WHERE budget_root_run_id = $1::uuid`, root,
	).Scan(&charged, &outstanding)
	return charged, outstanding, err
}

func (l *Ledger) totals(ctx context.Context, root string) (charged, outstanding string, err error) {
	err = l.DB.QueryRowContext(ctx, `
		SELECT
		    COALESCE(sum(CASE kind WHEN 'charge' THEN amount ELSE 0 END), 0)::text,
		    COALESCE(sum(CASE kind WHEN 'reserve' THEN amount
		                     WHEN 'release' THEN -amount ELSE 0 END), 0)::text
		  FROM platform.usage_ledger WHERE budget_root_run_id = $1::uuid`, root,
	).Scan(&charged, &outstanding)
	return charged, outstanding, err
}

func (l *Ledger) released(ctx context.Context, root string) (string, error) {
	var released string
	err := l.DB.QueryRowContext(ctx, `
		SELECT COALESCE(sum(amount), 0)::text FROM platform.usage_ledger
		 WHERE budget_root_run_id = $1::uuid AND kind = 'release'`, root,
	).Scan(&released)
	return released, err
}

// subtractAmounts is limit - charged - outstanding and refuses to go negative.
func subtractAmounts(limit string, charged string, outstanding string) (string, error) {
	limitUnits, err := unitsFromString(limit)
	if err != nil {
		return "", err
	}
	chargedUnits, err := unitsFromString(charged)
	if err != nil {
		return "", err
	}
	outstandingUnits, err := unitsFromString(outstanding)
	if err != nil {
		return "", err
	}
	remaining := new(big.Int).Sub(limitUnits, chargedUnits)
	remaining.Sub(remaining, outstandingUnits)
	if remaining.Sign() < 0 {
		return "", &ExhaustedError{Required: "0.00000000", Remaining: "0.00000000"}
	}
	return formatUnits(remaining)
}

// remainingOf is limit-consumed clamped at zero; overrun reports that the
// consumed sum exceeds the limit (actual usage can exceed a conservative
// reservation when a provider reports more tokens than estimated).
func remainingOf(limit, consumed string) (string, bool) {
	limitUnits, err := unitsFromString(limit)
	if err != nil {
		return "0.00000000", false
	}
	consumedUnits, err := unitsFromString(consumed)
	if err != nil {
		return "0.00000000", false
	}
	remaining := new(big.Int).Sub(limitUnits, consumedUnits)
	if remaining.Sign() < 0 {
		return "0.00000000", true
	}
	formatted, err := formatUnits(remaining)
	if err != nil {
		return "0.00000000", false
	}
	return formatted, false
}

func addAmounts(left, right string) (string, error) {
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

func isUniqueViolation(err error) bool {
	type sqlState interface{ SQLState() string }
	var state sqlState
	return errors.As(err, &state) && state.SQLState() == "23505"
}
