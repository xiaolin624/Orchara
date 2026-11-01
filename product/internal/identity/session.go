package identity

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"strings"
)

// Login verifies credentials and issues a session.
//
// An unknown login and a wrong password return the same sentinel and both spend
// one argon2id verification, so the response does not reveal whether an account
// exists. A disabled principal is only reported after the password verified.
func (s *Service) Login(ctx context.Context, login, password string) (*Session, error) {
	var (
		principalID string
		storedHash  string
		disabledAt  sql.NullTime
	)
	err := s.DB.QueryRowContext(ctx, `
		SELECT p.id, u.password_hash, p.disabled_at
		  FROM platform.users AS u
		  JOIN platform.principals AS p ON p.id = u.principal_id
		 WHERE u.login = $1`,
		strings.TrimSpace(login),
	).Scan(&principalID, &storedHash, &disabledAt)
	if errors.Is(err, sql.ErrNoRows) {
		verifyDummy(password)
		return nil, ErrInvalidCredentials
	}
	if err != nil {
		return nil, fmt.Errorf("looking up the login: %w", err)
	}

	ok, needsRehash := VerifyPassword(storedHash, password)
	if !ok {
		return nil, ErrInvalidCredentials
	}
	if disabledAt.Valid {
		return nil, ErrPrincipalDisabled
	}

	// Rehashing is best-effort and computed outside the transaction so a slow
	// hash does not hold a row lock on the account.
	var rehashed string
	if needsRehash {
		if candidate, hashErr := HashPassword(password); hashErr == nil {
			rehashed = candidate
		}
	}

	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return nil, fmt.Errorf("starting the login transaction: %w", err)
	}
	defer func() { _ = tx.Rollback() }()

	if rehashed != "" {
		if _, err := tx.ExecContext(ctx,
			`UPDATE platform.users SET password_hash = $1 WHERE principal_id = $2`,
			rehashed, principalID,
		); err != nil {
			return nil, fmt.Errorf("rehashing the password: %w", err)
		}
	}

	rawToken := NewToken()
	// The CSRF token is bound to the session token (HMAC-SHA256) instead of
	// being a second random value, so GET /auth/me can recompute it after a page
	// refresh while the database still stores only a hash.
	csrfToken := DeriveCSRFToken(rawToken)
	expiresAt := s.now().Add(SessionTTL)
	if _, err := tx.ExecContext(ctx,
		`INSERT INTO platform.sessions (token_hash, principal_id, csrf_hash, expires_at)
		 VALUES ($1, $2, $3, $4)`,
		HashToken(rawToken), principalID, HashToken(csrfToken), expiresAt,
	); err != nil {
		return nil, fmt.Errorf("creating the session: %w", err)
	}
	if err := RecordAudit(ctx, tx, &principalID, "auth.login", "principal", &principalID, map[string]any{
		"login": strings.TrimSpace(login),
	}); err != nil {
		return nil, err
	}
	if err := tx.Commit(); err != nil {
		return nil, fmt.Errorf("committing the login: %w", err)
	}
	return &Session{
		PrincipalID: principalID,
		Token:       rawToken,
		CSRFToken:   csrfToken,
		ExpiresAt:   expiresAt,
	}, nil
}

// Authenticate resolves a raw session cookie value to an identity.
//
// Expiry is decided by the database (`now()`), not the client or the process
// clock, so a skewed host cannot extend a session.
func (s *Service) Authenticate(ctx context.Context, rawToken string) (*Identity, error) {
	if strings.TrimSpace(rawToken) == "" {
		return nil, ErrUnauthenticated
	}
	var (
		principalID string
		kind        string
		csrfHash    string
		expiresAt   sql.NullTime
		revokedAt   sql.NullTime
		disabledAt  sql.NullTime
		expired     bool
	)
	err := s.DB.QueryRowContext(ctx, `
		SELECT s.principal_id, p.kind, s.csrf_hash, s.expires_at, s.revoked_at, p.disabled_at,
		       (s.expires_at <= now()) AS expired
		  FROM platform.sessions AS s
		  JOIN platform.principals AS p ON p.id = s.principal_id
		 WHERE s.token_hash = $1`,
		HashToken(rawToken),
	).Scan(&principalID, &kind, &csrfHash, &expiresAt, &revokedAt, &disabledAt, &expired)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrUnauthenticated
	}
	if err != nil {
		return nil, fmt.Errorf("looking up the session: %w", err)
	}
	if revokedAt.Valid {
		return nil, ErrSessionRevoked
	}
	if expired || !expiresAt.Valid {
		return nil, ErrSessionExpired
	}
	if disabledAt.Valid {
		return nil, ErrPrincipalDisabled
	}

	roles, err := LoadRoles(ctx, s.DB, principalID)
	if err != nil {
		return nil, err
	}
	return &Identity{
		PrincipalID: principalID,
		Kind:        kind,
		Roles:       roles,
		ExpiresAt:   expiresAt.Time,
		Method:      AuthMethodSessionCookie,
		// Recompute the CSRF token from the presented cookie: the plaintext is
		// never stored, so this is the only way a refreshed page can get it back.
		CSRFToken:   DeriveCSRFToken(rawToken),
		CSRFHash:    csrfHash,
		SessionHash: HashToken(rawToken),
	}, nil
}

// Logout revokes exactly the session identified by its stored token hash.
//
// Only one row is touched: signing out in one browser must not sign the user out
// everywhere. It reports false when the session was already revoked (or gone),
// which keeps a repeated logout a no-op instead of an error. The audit event is
// written in the same transaction, so a revoke is never invisible to the trail.
func (s *Service) Logout(ctx context.Context, sessionHash, principalID string) (bool, error) {
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return false, fmt.Errorf("starting the logout transaction: %w", err)
	}
	defer func() { _ = tx.Rollback() }()

	var revoked string
	err = tx.QueryRowContext(ctx, `
		UPDATE platform.sessions
		   SET revoked_at = now()
		 WHERE token_hash = $1
		   AND revoked_at IS NULL
		RETURNING token_hash`,
		sessionHash,
	).Scan(&revoked)
	if errors.Is(err, sql.ErrNoRows) {
		return false, nil
	}
	if err != nil {
		return false, fmt.Errorf("revoking the session: %w", err)
	}
	if err := RecordAudit(ctx, tx, &principalID, "auth.logout", "principal", &principalID, nil); err != nil {
		return false, err
	}
	if err := tx.Commit(); err != nil {
		return false, fmt.Errorf("committing the logout: %w", err)
	}
	return true, nil
}

// LoadRoles reads a principal's role bindings in a stable order.
func LoadRoles(ctx context.Context, db *sql.DB, principalID string) ([]string, error) {
	rows, err := db.QueryContext(ctx,
		`SELECT role FROM platform.role_bindings WHERE principal_id = $1 ORDER BY role`,
		principalID,
	)
	if err != nil {
		return nil, fmt.Errorf("loading roles: %w", err)
	}
	defer rows.Close()

	roles := make([]string, 0, 3)
	for rows.Next() {
		var role string
		if err := rows.Scan(&role); err != nil {
			return nil, fmt.Errorf("loading roles: %w", err)
		}
		roles = append(roles, role)
	}
	return roles, rows.Err()
}
