package identity

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"strings"

	"orchara/platform/internal/audit"
	"orchara/platform/internal/policy"
	"orchara/platform/internal/uuid"
)

// Disable / enable path for principals.
//
// SECURITY.md §1 and IMPLEMENTATION-DETAILS.md §1 fix the semantics: disabling a
// person immediately revokes their sessions and API keys, revokes the keys of
// every service account they are responsible for, raises the policy epoch and
// writes an audit event — all in one transaction, under the same advisory lock
// that serialises role changes, so a disable can never race a role demotion or a
// responsibility handover into a state with no administrator.
//
// A principal row is never deleted: audit_events references it, and the trail
// must keep resolving the original subject after the account is disabled.
// Enabling clears disabled_at only. Credentials stay revoked (sessions survive
// as revoked rows; keys keep revoked_at), because re-enabling an account must
// not silently resurrect credentials someone else may have obtained.

// PrincipalStateChange is the receipt of a successful SetDisabled call.
//
// Changed is false for an idempotent no-op (the requested state already held):
// no row, no audit event and no epoch bump happened in that case. Sessions and
// Keys count only the rows this call revoked.
type PrincipalStateChange struct {
	PrincipalID     string
	State           string
	Changed         bool
	SessionsRevoked int64
	KeysRevoked     int64
	Revision        int64
}

// disableChangesState is the idempotency rule: a request only changes anything
// when it asks for the opposite of the stored state.
func disableChangesState(requestedDisabled, currentlyDisabled bool) bool {
	return requestedDisabled != currentlyDisabled
}

// disablingLastAdmin is the pure last-administrator rule for disable. Only an
// enabled human who currently holds admin can be the last one; otherEnabledAdmins
// is the count of the other enabled human administrators, read inside the same
// locked transaction. This also covers self-disable: an administrator disabling
// themselves is refused unless someone else remains.
func disablingLastAdmin(
	kind string,
	currentlyDisabled bool,
	roles []string,
	otherEnabledAdmins int,
) bool {
	return kind == "user" &&
		!currentlyDisabled &&
		hasRole(roles, "admin") &&
		otherEnabledAdmins == 0
}

// validateDisableReason requires a non-empty (after trimming) reason, which is
// what makes the audit row explain itself. The reason is not a secret.
func validateDisableReason(reason string) (string, error) {
	trimmed := strings.TrimSpace(reason)
	if trimmed == "" {
		return "", &ValidationError{Field: "reason", Message: "reason is required"}
	}
	return trimmed, nil
}

// SetDisabled disables or enables one principal.
//
// The whole change is one transaction serialised by rolesLockKey (the shared
// "ROLESET1" advisory lock):
//
//  1. the principal must exist (unknown and malformed ids are one not-found);
//  2. asking for the state it already has is an idempotent no-op with no audit
//     row and no epoch bump;
//  3. disabling the last enabled human administrator is refused, including
//     self-disable with no other administrator left;
//  4. disabling revokes the principal's sessions, its own API keys and the API
//     keys of every service principal it is responsible for, sets disabled_at,
//     bumps the policy epoch and records `principal.disabled`;
//  5. enabling clears disabled_at, bumps the epoch and records
//     `principal.enabled`; revoked credentials stay revoked.
func (s *Service) SetDisabled(
	ctx context.Context,
	actorID, principalID string,
	disabled bool,
	reason string,
) (*PrincipalStateChange, error) {
	reason, err := validateDisableReason(reason)
	if err != nil {
		return nil, err
	}
	principalID = strings.TrimSpace(principalID)
	if !uuid.Valid(principalID) {
		// A malformed id cannot name a principal; it is the same not-found as an
		// id that does not exist (no enumeration).
		return nil, ErrPrincipalNotFound
	}

	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return nil, fmt.Errorf("starting the principal state transaction: %w", err)
	}
	defer func() { _ = tx.Rollback() }()

	// The same lock as SetRoles and Handover: disable/enable, role demotion and
	// responsibility handover are serialised, so the last-admin count and the
	// responsibility reassignment cannot interleave into an inconsistent state.
	if _, err := tx.ExecContext(ctx, fmt.Sprintf("SELECT pg_advisory_xact_lock(%d)", rolesLockKey)); err != nil {
		return nil, fmt.Errorf("acquiring the principal state lock: %w", err)
	}

	var (
		kind       string
		disabledAt sql.NullTime
	)
	err = tx.QueryRowContext(ctx,
		`SELECT kind, disabled_at FROM platform.principals WHERE id = $1`,
		principalID,
	).Scan(&kind, &disabledAt)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrPrincipalNotFound
	}
	if err != nil {
		return nil, fmt.Errorf("looking up the principal: %w", err)
	}

	state := "enabled"
	if disabled {
		state = "disabled"
	}
	if !disableChangesState(disabled, disabledAt.Valid) {
		// Already in the requested state: report it without writing anything.
		return &PrincipalStateChange{PrincipalID: principalID, State: state}, nil
	}

	roles, err := rolesInTx(ctx, tx, principalID)
	if err != nil {
		return nil, err
	}

	change := &PrincipalStateChange{PrincipalID: principalID, State: state, Changed: true}
	if disabled {
		if kind == "user" && hasRole(roles, "admin") {
			var otherEnabledAdmins int
			err = tx.QueryRowContext(ctx, `
				SELECT count(*)
				  FROM platform.principals AS p
				  JOIN platform.users AS u ON u.principal_id = p.id
				  JOIN platform.role_bindings AS r
				    ON r.principal_id = p.id AND r.role = 'admin'
				 WHERE p.kind = 'user'
				   AND p.disabled_at IS NULL
				   AND p.id <> $1`,
				principalID,
			).Scan(&otherEnabledAdmins)
			if err != nil {
				return nil, fmt.Errorf("counting the remaining administrators: %w", err)
			}
			if disablingLastAdmin(kind, disabledAt.Valid, roles, otherEnabledAdmins) {
				return nil, ErrLastAdmin
			}
		}

		// Revoke every live session of the principal. A disabled person cannot
		// keep using a browser session that was opened before the change.
		sessionsResult, err := tx.ExecContext(ctx, `
			UPDATE platform.sessions
			   SET revoked_at = now()
			 WHERE principal_id = $1
			   AND revoked_at IS NULL`,
			principalID,
		)
		if err != nil {
			return nil, fmt.Errorf("revoking the principal's sessions: %w", err)
		}
		change.SessionsRevoked, err = sessionsResult.RowsAffected()
		if err != nil {
			return nil, fmt.Errorf("counting the revoked sessions: %w", err)
		}

		// Revoke the principal's own keys and those of the service accounts it
		// is responsible for. This is the concrete SECURITY.md §1 rule: a
		// departing or disabled responsible person's service keys stop working,
		// not merely stop authenticating through the responsible-user check.
		keysResult, err := tx.ExecContext(ctx, `
			UPDATE platform.api_keys
			   SET revoked_at = now()
			 WHERE revoked_at IS NULL
			   AND (principal_id = $1
			        OR principal_id IN (
			            SELECT s.id
			              FROM platform.principals AS s
			             WHERE s.responsible_user_id = $1))`,
			principalID,
		)
		if err != nil {
			return nil, fmt.Errorf("revoking the API keys: %w", err)
		}
		change.KeysRevoked, err = keysResult.RowsAffected()
		if err != nil {
			return nil, fmt.Errorf("counting the revoked API keys: %w", err)
		}

		// CAS on disabled_at: a concurrent disable of the same principal waits on
		// the lock above and then sees the already-disabled row, so exactly one
		// audit event describes the moment it was disabled.
		if _, err := tx.ExecContext(ctx,
			`UPDATE platform.principals
			    SET disabled_at = now()
			  WHERE id = $1
			    AND disabled_at IS NULL`,
			principalID,
		); err != nil {
			return nil, fmt.Errorf("disabling the principal: %w", err)
		}

		if err := audit.Record(ctx, tx, &actorID, "principal.disabled", "principal", &principalID,
			map[string]any{
				"reason":           reason,
				"sessions_revoked": change.SessionsRevoked,
				"keys_revoked":     change.KeysRevoked,
			},
		); err != nil {
			return nil, err
		}
	} else {
		if _, err := tx.ExecContext(ctx,
			`UPDATE platform.principals
			    SET disabled_at = NULL
			  WHERE id = $1
			    AND disabled_at IS NOT NULL`,
			principalID,
		); err != nil {
			return nil, fmt.Errorf("enabling the principal: %w", err)
		}
		if err := audit.Record(ctx, tx, &actorID, "principal.enabled", "principal", &principalID,
			map[string]any{"reason": reason},
		); err != nil {
			return nil, err
		}
	}

	revision, err := policy.Bump(ctx, tx)
	if err != nil {
		return nil, err
	}
	change.Revision = revision
	if err := tx.Commit(); err != nil {
		return nil, fmt.Errorf("committing the principal state change: %w", err)
	}
	return change, nil
}
