package catalog

// F02B: the one durable outbox writer.
//
// `platform.outbox` is the contract's durable signal table (created by T068's
// 0042). Publication (T068) and revocation (T070) both write exactly one row in
// the same transaction as the state change they describe, so a consumer (T077)
// can never observe the state change without its signal. There is no dispatcher
// or delivery in this build: `available_at` keeps its default, `delivered_at`
// stays NULL and `attempts` stays 0 until T077/T082 own consumption.

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"

	"orchara/platform/internal/uuid"
)

// insertOutboxTx writes one outbox row and returns its id. The caller owns the
// transaction (and therefore the commit that makes the signal durable).
func insertOutboxTx(
	ctx context.Context, tx *sql.Tx, kind, aggregateID string, payload any,
) (string, error) {
	encoded, err := json.Marshal(payload)
	if err != nil {
		return "", fmt.Errorf("encoding the outbox payload: %w", err)
	}
	outboxID := uuid.NewString()
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO platform.outbox (id, kind, aggregate_id, payload)
		VALUES ($1::uuid, $2, $3::uuid, $4::jsonb)`,
		outboxID, kind, aggregateID, string(encoded),
	); err != nil {
		return "", fmt.Errorf("recording the %s outbox row: %w", kind, err)
	}
	return outboxID, nil
}
