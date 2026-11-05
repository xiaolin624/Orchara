package approvals

import (
	"context"
	"database/sql"
	"encoding/base64"
	"errors"
	"fmt"
	"strconv"
	"strings"
	"time"

	"orchara/platform/internal/uuid"
)

// DefaultListLimit and MaxListLimit mirror the contract's listApprovals query.
const (
	DefaultListLimit = 20
	MaxListLimit     = 100
)

// Get returns one approval to its designated approver or an administrator.
// Everyone else gets the same 404 as a missing row: an approval names a real
// target, so its existence is not a public fact.
func (s *Service) Get(ctx context.Context, callerID, approvalID string, callerIsAdmin bool) (*Approval, error) {
	approvalID = strings.TrimSpace(approvalID)
	if !uuid.Valid(approvalID) {
		return nil, failure(404, CodeNotFound, "that approval does not exist")
	}
	approval, err := loadApproval(ctx, s.DB, approvalID)
	if err != nil {
		return nil, fmt.Errorf("reading the approval: %w", err)
	}
	if approval == nil {
		return nil, failure(404, CodeNotFound, "that approval does not exist")
	}
	if !callerIsAdmin && !strings.EqualFold(strings.TrimSpace(callerID), approval.ApproverID) {
		return nil, failure(404, CodeNotFound, "that approval does not exist")
	}
	return approval, nil
}

// List returns the caller's approvals, pending first (then earliest expiry, then
// id) with a keyset cursor. An administrator is not the designated approver and
// therefore does not see someone else's queue through this endpoint.
func (s *Service) List(ctx context.Context, callerID string, limit int, cursor string) ([]Approval, string, error) {
	callerID = strings.TrimSpace(callerID)
	if !uuid.Valid(callerID) {
		return nil, "", failure(401, "unauthenticated", "an authenticated human is required")
	}
	if limit <= 0 {
		limit = DefaultListLimit
	}
	if limit > MaxListLimit {
		limit = MaxListLimit
	}
	rank, expires, after, err := decodeCursor(cursor)
	if err != nil {
		return nil, "", err
	}
	rows, err := s.DB.QueryContext(ctx, `
		SELECT `+approvalColumns+`,
		       CASE WHEN state = 'pending' THEN 0 ELSE 1 END
		  FROM platform.approvals
		 WHERE approver_id = $1::uuid
		   AND (
		       CASE WHEN state = 'pending' THEN 0 ELSE 1 END > $2
		       OR (CASE WHEN state = 'pending' THEN 0 ELSE 1 END = $2
		           AND (expires_at > $3::timestamptz
		                OR (expires_at = $3::timestamptz AND id > $4::uuid)))
		   )
		 ORDER BY CASE WHEN state = 'pending' THEN 0 ELSE 1 END, expires_at, id
		 LIMIT $5`, callerID, rank, expires, after, limit+1)
	if err != nil {
		return nil, "", fmt.Errorf("listing approvals: %w", err)
	}
	defer func() { _ = rows.Close() }()

	items := []Approval{}
	for rows.Next() {
		var (
			approval    Approval
			operation   sql.NullString
			decidedAt   sql.NullTime
			pendingRank int
		)
		if err := rows.Scan(
			&approval.ID, &approval.RunID, &approval.State, &approval.ProposalArtifactID,
			&approval.ParamsSHA256, &approval.ExpiresAt, &approval.ApproverID,
			&operation, &approval.NodeKey, &decidedAt, &pendingRank,
		); err != nil {
			return nil, "", fmt.Errorf("scanning the approval list: %w", err)
		}
		approval.OperationID = operation.String
		if decidedAt.Valid {
			value := decidedAt.Time
			approval.DecidedAt = &value
		}
		approval.ExpiresAt = approval.ExpiresAt.UTC()
		items = append(items, approval)
	}
	if err := rows.Err(); err != nil {
		return nil, "", fmt.Errorf("listing approvals: %w", err)
	}
	hasMore := len(items) > limit
	if hasMore {
		items = items[:limit]
	}
	next := ""
	if hasMore && len(items) > 0 {
		next = encodeCursor(items[len(items)-1])
	}
	return items, next, nil
}

// Cursor format: rank|expires_at(RFC3339Nano)|id, base64url. It is opaque to the
// client and only orders (pending-first, expires_at, id); no internal row id or
// proposal content is exposed beyond the contract's own id.
func encodeCursor(approval Approval) string {
	rank := "1"
	if approval.State == "pending" {
		rank = "0"
	}
	raw := rank + "|" + approval.ExpiresAt.UTC().Format(time.RFC3339Nano) + "|" + approval.ID
	return base64.RawURLEncoding.EncodeToString([]byte(raw))
}

func decodeCursor(cursor string) (int, time.Time, string, error) {
	cursor = strings.TrimSpace(cursor)
	if cursor == "" {
		return 0, time.Unix(0, 0).UTC(), "00000000-0000-0000-0000-000000000000", nil
	}
	raw, err := base64.RawURLEncoding.DecodeString(cursor)
	if err != nil {
		return 0, time.Time{}, "", failure(422, "validation_failed", "cursor is not valid")
	}
	parts := strings.Split(string(raw), "|")
	if len(parts) != 3 {
		return 0, time.Time{}, "", failure(422, "validation_failed", "cursor is not valid")
	}
	rank, err := strconv.Atoi(parts[0])
	if err != nil || (rank != 0 && rank != 1) {
		return 0, time.Time{}, "", failure(422, "validation_failed", "cursor is not valid")
	}
	expires, err := time.Parse(time.RFC3339Nano, parts[1])
	if err != nil {
		return 0, time.Time{}, "", failure(422, "validation_failed", "cursor is not valid")
	}
	if !uuid.Valid(parts[2]) {
		return 0, time.Time{}, "", failure(422, "validation_failed", "cursor is not valid")
	}
	return rank, expires, parts[2], nil
}

// loadApproval reads one row outside a transaction (Get). The decision path
// locks the same columns through loadApprovalTx.
func loadApproval(ctx context.Context, db *sql.DB, approvalID string) (*Approval, error) {
	var (
		approval  Approval
		operation sql.NullString
		decidedAt sql.NullTime
	)
	err := db.QueryRowContext(ctx, `
		SELECT `+approvalColumns+` FROM platform.approvals WHERE id = $1::uuid`, approvalID,
	).Scan(
		&approval.ID, &approval.RunID, &approval.State, &approval.ProposalArtifactID,
		&approval.ParamsSHA256, &approval.ExpiresAt, &approval.ApproverID,
		&operation, &approval.NodeKey, &decidedAt,
	)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	approval.OperationID = operation.String
	if decidedAt.Valid {
		value := decidedAt.Time
		approval.DecidedAt = &value
	}
	return &approval, nil
}
