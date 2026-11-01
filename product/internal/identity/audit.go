package identity

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
)

// RecordAudit appends one audit event inside the caller's transaction.
//
// metadata is typed, non-secret context (logins are acceptable; passwords,
// tokens and cookie values are not). Passing a nil actor records a system
// action, which is what bootstrap is.
func RecordAudit(
	ctx context.Context,
	tx *sql.Tx,
	actorID *string,
	action string,
	resourceType string,
	resourceID *string,
	metadata map[string]any,
) error {
	payload := []byte("{}")
	if len(metadata) > 0 {
		encoded, err := json.Marshal(metadata)
		if err != nil {
			return fmt.Errorf("encoding audit metadata for %s: %w", action, err)
		}
		payload = encoded
	}
	if _, err := tx.ExecContext(
		ctx,
		`INSERT INTO platform.audit_events (id, actor_id, action, resource_type, resource_id, metadata)
		 VALUES ($1, $2, $3, $4, $5, $6)`,
		NewUUID(), actorID, action, resourceType, resourceID, string(payload),
	); err != nil {
		return fmt.Errorf("recording audit event %s: %w", action, err)
	}
	return nil
}
