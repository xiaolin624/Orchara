package identity

import (
	"context"
	"database/sql"
	"errors"
	"fmt"

	"orchara/platform/internal/audit"
)

// Activate consumes a one-time activation token and sets the user's password.
//
// Order matters:
//  1. read the token row and validate the password first, so a rejected
//     password (too weak / equal to the login) leaves the token usable;
//  2. hash the new password outside the transaction;
//  3. inside one transaction, consume the token with a single conditional
//     UPDATE and set the password. The UPDATE is the compare-and-swap: of two
//     concurrent activations exactly one matches `consumed_at IS NULL`.
func (s *Service) Activate(ctx context.Context, rawToken, password string) (string, error) {
	if rawToken == "" {
		return "", ErrActivationInvalid
	}
	tokenHash := HashToken(rawToken)

	var (
		principalID string
		login       string
		consumedAt  sql.NullTime
		expired     bool
	)
	err := s.DB.QueryRowContext(ctx, `
		SELECT u.principal_id, u.login, t.consumed_at, (t.expires_at <= now()) AS expired
		  FROM platform.activation_tokens AS t
		  JOIN platform.users AS u ON u.principal_id = t.principal_id
		 WHERE t.token_hash = $1`,
		tokenHash,
	).Scan(&principalID, &login, &consumedAt, &expired)
	if errors.Is(err, sql.ErrNoRows) {
		return "", ErrActivationInvalid
	}
	if err != nil {
		return "", fmt.Errorf("looking up the activation token: %w", err)
	}
	if consumedAt.Valid {
		return "", ErrActivationConsumed
	}
	if expired {
		return "", ErrActivationExpired
	}
	if err := validateNewPassword(password, login); err != nil {
		return "", err
	}
	passwordHash, err := HashPassword(password)
	if err != nil {
		return "", err
	}

	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return "", fmt.Errorf("starting the activation transaction: %w", err)
	}
	defer func() { _ = tx.Rollback() }()

	var activatedPrincipal string
	err = tx.QueryRowContext(ctx, `
		UPDATE platform.activation_tokens
		   SET consumed_at = now()
		 WHERE token_hash = $1
		   AND consumed_at IS NULL
		   AND expires_at > now()
		RETURNING principal_id`,
		tokenHash,
	).Scan(&activatedPrincipal)
	if errors.Is(err, sql.ErrNoRows) {
		// Another activation won the compare-and-swap, or the row expired in
		// between. Re-read to report the real reason instead of guessing.
		return "", s.classifyActivationLoss(ctx, tokenHash)
	}
	if err != nil {
		return "", fmt.Errorf("consuming the activation token: %w", err)
	}

	if _, err := tx.ExecContext(ctx, `
		UPDATE platform.users
		   SET password_hash = $1, must_change_password = false
		 WHERE principal_id = $2`,
		passwordHash, activatedPrincipal,
	); err != nil {
		return "", fmt.Errorf("setting the password: %w", err)
	}
	if err := audit.Record(ctx, tx, &activatedPrincipal, "user.activated", "principal", &activatedPrincipal, map[string]any{
		"login": login,
	}); err != nil {
		return "", err
	}
	if err := tx.Commit(); err != nil {
		return "", fmt.Errorf("committing the activation: %w", err)
	}
	return activatedPrincipal, nil
}

func (s *Service) classifyActivationLoss(ctx context.Context, tokenHash string) error {
	var (
		consumedAt sql.NullTime
		expired    bool
	)
	err := s.DB.QueryRowContext(ctx, `
		SELECT consumed_at, (expires_at <= now()) AS expired
		  FROM platform.activation_tokens
		 WHERE token_hash = $1`,
		tokenHash,
	).Scan(&consumedAt, &expired)
	if errors.Is(err, sql.ErrNoRows) {
		return ErrActivationInvalid
	}
	if err != nil {
		return fmt.Errorf("re-reading the activation token: %w", err)
	}
	if consumedAt.Valid {
		return ErrActivationConsumed
	}
	if expired {
		return ErrActivationExpired
	}
	// Unconsumed and unexpired, yet the CAS matched nothing: do not pretend the
	// token is usable. Reporting "invalid" is conservative and leaks nothing.
	return ErrActivationInvalid
}
