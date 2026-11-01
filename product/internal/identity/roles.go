package identity

import (
	"context"
	"database/sql"
	"errors"
	"fmt"

	"orchara/platform/internal/audit"
	"orchara/platform/internal/policy"
)

// RoleUpdate is the receipt of a successful SetRoles call.
type RoleUpdate struct {
	PrincipalID string
	OldRoles    []string
	NewRoles    []string
	Revision    int64
}

// ErrLastAdmin is returned when a role change would leave the installation with
// no enabled human administrator.
var ErrLastAdmin = errors.New("cannot remove the last enabled human administrator")

// ErrPrincipalNotFound is returned when the target principal does not exist.
var ErrPrincipalNotFound = errors.New("principal not found")

// rolesLockKey serialises role changes so two concurrent demotions cannot both
// observe the other administrator and both win. It is not a credential.
const rolesLockKey int64 = 0x524f_4c45_5345_5431 // "ROLESET1"

// SetRoles replaces a human user's role bindings.
//
// The whole change is one transaction, serialised by an advisory lock:
//
//  1. the target must exist and be a human user (a service account has no
//     roles; its authority is the scoped API key plus the ACL, T020);
//  2. removing `admin` from the last enabled human administrator is refused;
//  3. bindings are replaced, `roles.updated` is recorded with the old and new
//     sets, and the policy epoch is bumped in the same transaction.
//
// The epoch bump matters even though roles are not consulted for resource reads:
// any future decision that does depend on roles must not be able to outlive a
// role change.
func (s *Service) SetRoles(
	ctx context.Context,
	actorID, principalID string,
	roles []string,
) (*RoleUpdate, error) {
	newRoles, err := normalizeRoles(roles)
	if err != nil {
		return nil, err
	}

	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return nil, fmt.Errorf("starting the role update transaction: %w", err)
	}
	defer func() { _ = tx.Rollback() }()

	if _, err := tx.ExecContext(ctx, fmt.Sprintf("SELECT pg_advisory_xact_lock(%d)", rolesLockKey)); err != nil {
		return nil, fmt.Errorf("acquiring the role update lock: %w", err)
	}

	var kind string
	var disabledAt sql.NullTime
	err = tx.QueryRowContext(ctx,
		`SELECT kind, disabled_at FROM platform.principals WHERE id = $1`,
		principalID,
	).Scan(&kind, &disabledAt)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrPrincipalNotFound
	}
	if err != nil {
		return nil, fmt.Errorf("looking up the target principal: %w", err)
	}
	if kind != "user" {
		return nil, &ValidationError{
			Field:   "principal_id",
			Message: "role bindings apply only to human users",
		}
	}

	oldRoles, err := rolesInTx(ctx, tx, principalID)
	if err != nil {
		return nil, err
	}
	// Only a demotion can remove the last administrator; count the others only
	// when that is what this change does.
	if hasRole(oldRoles, "admin") && !hasRole(newRoles, "admin") {
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
		if removingLastAdmin(oldRoles, newRoles, otherEnabledAdmins) {
			return nil, ErrLastAdmin
		}
	}

	if _, err := tx.ExecContext(ctx,
		`DELETE FROM platform.role_bindings WHERE principal_id = $1`, principalID,
	); err != nil {
		return nil, fmt.Errorf("clearing the role bindings: %w", err)
	}
	for _, role := range newRoles {
		if _, err := tx.ExecContext(ctx,
			`INSERT INTO platform.role_bindings (principal_id, role) VALUES ($1, $2)`,
			principalID, role,
		); err != nil {
			return nil, fmt.Errorf("binding role %s: %w", role, err)
		}
	}

	if err := audit.Record(ctx, tx, &actorID, "roles.updated", "principal", &principalID, map[string]any{
		"principal_id": principalID,
		"old_roles":    oldRoles,
		"new_roles":    newRoles,
	}); err != nil {
		return nil, err
	}
	revision, err := policy.Bump(ctx, tx)
	if err != nil {
		return nil, err
	}
	if err := tx.Commit(); err != nil {
		return nil, fmt.Errorf("committing the role update: %w", err)
	}
	return &RoleUpdate{
		PrincipalID: principalID,
		OldRoles:    oldRoles,
		NewRoles:    newRoles,
		Revision:    revision,
	}, nil
}

// rolesInTx reads a principal's current bindings in a stable order.
func rolesInTx(ctx context.Context, tx *sql.Tx, principalID string) ([]string, error) {
	rows, err := tx.QueryContext(ctx,
		`SELECT role FROM platform.role_bindings WHERE principal_id = $1 ORDER BY role`,
		principalID,
	)
	if err != nil {
		return nil, fmt.Errorf("reading the current role bindings: %w", err)
	}
	defer rows.Close()

	roles := make([]string, 0, 3)
	for rows.Next() {
		var role string
		if err := rows.Scan(&role); err != nil {
			return nil, fmt.Errorf("reading the current role bindings: %w", err)
		}
		roles = append(roles, role)
	}
	return roles, rows.Err()
}

// removingLastAdmin is the pure last-administrator rule: a change is refused
// when the target currently holds admin, the new set does not, and no other
// enabled human administrator remains. otherEnabledAdmins is the count of such
// other administrators, read inside the same locked transaction.
func removingLastAdmin(currentRoles, newRoles []string, otherEnabledAdmins int) bool {
	return hasRole(currentRoles, "admin") && !hasRole(newRoles, "admin") && otherEnabledAdmins == 0
}

func hasRole(roles []string, role string) bool {
	for _, candidate := range roles {
		if candidate == role {
			return true
		}
	}
	return false
}
