package identity

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"strings"

	"orchara/platform/internal/audit"
	"orchara/platform/internal/uuid"
)

// handoverPrincipal is the snapshot of one principal that the handover rules
// need. It is a plain struct so the whole authorization rule can be unit-tested
// without a database.
type handoverPrincipal struct {
	ID          string
	Kind        string
	Disabled    bool
	HasUsersRow bool
	Roles       []string
}

// HandoverAccount is one service account moved by Handover.
type HandoverAccount struct {
	ID          string
	DisplayName string
}

// Errors the handover command reports on stderr. They are separate sentinels so
// the message can name the flag that has to be fixed.
var (
	// ErrHandoverSamePrincipal means --from and --to name the same principal.
	ErrHandoverSamePrincipal = errors.New("--from and --to must name different principals")
	// ErrHandoverTargetNotAdmin means --to is not an enabled human administrator.
	// A non-admin successor would become the governance owner of a service
	// account without being authorized to manage service accounts.
	ErrHandoverTargetNotAdmin = errors.New("--to must be an enabled human administrator")
	// ErrHandoverActorNotAdmin means --by is not an enabled human administrator.
	// The actor is the audit subject, so it must be someone who may manage
	// service accounts in the first place.
	ErrHandoverActorNotAdmin = errors.New("--by must be an enabled human administrator")
	// ErrHandoverFromNotHuman means --from is not a human principal. Only human
	// users can be a responsible_user_id (the schema enforces it), so a service
	// principal can never be the source of a handover.
	ErrHandoverFromNotHuman = errors.New("--from must be a human principal")
)

// validateHandover is the pure authorization rule for a responsibility
// handover. The departing principal may be disabled (that is the usual reason
// to hand work over), but must still be a human. The successor and the actor
// must both be enabled human administrators: only they are already authorized
// to manage service accounts, so the handover cannot be used to widen anyone's
// authority.
func validateHandover(from, to, by handoverPrincipal) error {
	if from.ID == to.ID {
		return ErrHandoverSamePrincipal
	}
	if from.Kind != "user" || !from.HasUsersRow {
		return ErrHandoverFromNotHuman
	}
	if to.Kind != "user" || !to.HasUsersRow || to.Disabled || !hasRole(to.Roles, "admin") {
		return ErrHandoverTargetNotAdmin
	}
	if by.Kind != "user" || !by.HasUsersRow || by.Disabled || !hasRole(by.Roles, "admin") {
		return ErrHandoverActorNotAdmin
	}
	return nil
}

// Handover reassigns every service account currently responsible to `from` to
// `to`, and records one `service_account.handover` audit event per account with
// `by` as the actor.
//
// Everything — loading the three principals, validating them, the update and the
// audit rows — happens in one transaction under rolesLockKey ("ROLESET1"), the
// same advisory lock used by role changes and disable/enable, so a handover
// cannot interleave with a demotion or a disable into an unauthorized state.
//
// Nothing else changes: no role bindings, no resource grants, no API keys. A
// revoked key stays revoked and a key that was not revoked keeps working; the
// handover therefore cannot expand anyone's authority. Responsibility is a
// governance link, not a credential.
func (s *Service) Handover(
	ctx context.Context,
	by, from, to string,
) ([]HandoverAccount, error) {
	by = strings.TrimSpace(by)
	from = strings.TrimSpace(from)
	to = strings.TrimSpace(to)
	for _, id := range []string{by, from, to} {
		if !uuid.Valid(id) {
			// The command line validates the shape first and exits 2; this is the
			// defensive path for a caller that reaches the service directly.
			return nil, ErrPrincipalNotFound
		}
	}

	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return nil, fmt.Errorf("starting the handover transaction: %w", err)
	}
	defer func() { _ = tx.Rollback() }()

	if _, err := tx.ExecContext(ctx, fmt.Sprintf("SELECT pg_advisory_xact_lock(%d)", rolesLockKey)); err != nil {
		return nil, fmt.Errorf("acquiring the handover lock: %w", err)
	}

	// Every check runs after the lock: a concurrently disabled successor or a
	// concurrently demoted actor is seen in its committed state.
	fromPrincipal, err := loadHandoverPrincipal(ctx, tx, from)
	if err != nil {
		return nil, fmt.Errorf("--from: %w", err)
	}
	toPrincipal, err := loadHandoverPrincipal(ctx, tx, to)
	if err != nil {
		return nil, fmt.Errorf("--to: %w", err)
	}
	byPrincipal, err := loadHandoverPrincipal(ctx, tx, by)
	if err != nil {
		return nil, fmt.Errorf("--by: %w", err)
	}
	if err := validateHandover(fromPrincipal, toPrincipal, byPrincipal); err != nil {
		return nil, err
	}

	rows, err := tx.QueryContext(ctx, `
		WITH moved AS (
		    UPDATE platform.principals
		       SET responsible_user_id = $2
		     WHERE responsible_user_id = $1
		       AND kind = 'service'
		    RETURNING id, display_name
		)
		SELECT id, display_name FROM moved ORDER BY display_name, id`,
		from, to,
	)
	if err != nil {
		return nil, fmt.Errorf("reassigning the service accounts: %w", err)
	}
	accounts := make([]HandoverAccount, 0, 4)
	for rows.Next() {
		var account HandoverAccount
		if err := rows.Scan(&account.ID, &account.DisplayName); err != nil {
			rows.Close()
			return nil, fmt.Errorf("reading the reassigned service accounts: %w", err)
		}
		accounts = append(accounts, account)
	}
	if err := rows.Err(); err != nil {
		rows.Close()
		return nil, fmt.Errorf("reading the reassigned service accounts: %w", err)
	}
	rows.Close()

	for index := range accounts {
		if err := audit.Record(ctx, tx, &by, "service_account.handover", "principal",
			&accounts[index].ID, map[string]any{
				"from": from,
				"to":   to,
			},
		); err != nil {
			return nil, err
		}
	}

	if err := tx.Commit(); err != nil {
		return nil, fmt.Errorf("committing the handover: %w", err)
	}
	return accounts, nil
}

// loadHandoverPrincipal reads the snapshot the handover rules need. A missing
// principal is an error naming the flag it came from, so the operator knows
// which id was wrong.
func loadHandoverPrincipal(
	ctx context.Context,
	tx *sql.Tx,
	principalID string,
) (handoverPrincipal, error) {
	principal := handoverPrincipal{ID: principalID}
	var disabledAt sql.NullTime
	err := tx.QueryRowContext(ctx, `
		SELECT p.kind, p.disabled_at,
		       EXISTS (SELECT 1 FROM platform.users AS u WHERE u.principal_id = p.id)
		  FROM platform.principals AS p
		 WHERE p.id = $1`,
		principalID,
	).Scan(&principal.Kind, &disabledAt, &principal.HasUsersRow)
	if errors.Is(err, sql.ErrNoRows) {
		return handoverPrincipal{}, ErrPrincipalNotFound
	}
	if err != nil {
		return handoverPrincipal{}, fmt.Errorf("looking up %s: %w", principalID, err)
	}
	principal.Disabled = disabledAt.Valid
	principal.Roles, err = rolesInTx(ctx, tx, principalID)
	if err != nil {
		return handoverPrincipal{}, err
	}
	return principal, nil
}
