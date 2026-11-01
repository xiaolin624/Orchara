package identity

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/jackc/pgx/v5/pgconn"
)

// Lifetimes and names fixed by DESIGN.md §4/§5.
const (
	SessionTTL        = 24 * time.Hour
	InvitationTTL     = 24 * time.Hour
	SessionCookieName = "platform_session"
)

// Known roles. F01 fixes this vocabulary; an unknown role is a validation error
// rather than a row that no authorizer understands.
var knownRoles = map[string]bool{"admin": true, "creator": true, "user": true}

// Errors callers map to HTTP codes. They are sentinels so the API layer never
// has to inspect driver errors or string messages.
var (
	ErrInvalidCredentials = errors.New("invalid credentials")
	ErrPrincipalDisabled  = errors.New("principal is disabled")
	ErrUnauthenticated    = errors.New("unauthenticated")
	ErrSessionExpired     = errors.New("session expired")
	ErrSessionRevoked     = errors.New("session revoked")
	ErrAdminExists        = errors.New("an enabled human administrator already exists")
	ErrLoginTaken         = errors.New("login is already taken")
	ErrActivationInvalid  = errors.New("activation token is not recognised")
	ErrActivationConsumed = errors.New("activation token was already consumed")
	ErrActivationExpired  = errors.New("activation token has expired")
	ErrValidation         = errors.New("validation failed")
)

// ValidationError names the request field that failed semantic validation.
// Its message never contains a secret.
type ValidationError struct {
	Field   string
	Message string
}

func (e *ValidationError) Error() string {
	return fmt.Sprintf("%s: %s", e.Field, e.Message)
}

// Unwrap lets callers use errors.Is(err, ErrValidation).
func (e *ValidationError) Unwrap() error { return ErrValidation }

// Invitation is the one-time response of POST /api/v1/admin/users. The raw
// Token is shown once and never stored.
type Invitation struct {
	PrincipalID string
	Token       string
	ExpiresAt   time.Time
}

// Session is the response of POST /api/v1/auth/login.
type Session struct {
	PrincipalID string
	Token       string
	CSRFToken   string
	ExpiresAt   time.Time
}

// Identity is an authenticated principal as resolved from a session cookie.
type Identity struct {
	PrincipalID string
	Kind        string
	Roles       []string
	ExpiresAt   time.Time
}

// HasRole reports whether the identity carries role.
func (i *Identity) HasRole(role string) bool {
	for _, candidate := range i.Roles {
		if candidate == role {
			return true
		}
	}
	return false
}

// Service owns identity persistence. It takes a *sql.DB rather than a pool
// abstraction because transactions are part of the contract (single-use
// activation, one-admin bootstrap).
type Service struct {
	DB  *sql.DB
	Now func() time.Time
}

// New builds a Service over db.
func New(db *sql.DB) *Service {
	return &Service{DB: db, Now: time.Now}
}

func (s *Service) now() time.Time {
	if s.Now == nil {
		return time.Now().UTC()
	}
	return s.Now().UTC()
}

// bootstrapLockKey serialises first-admin creation. It is not a credential.
const bootstrapLockKey int64 = 0x5442_4f4f_5453_5452 // "TBOOTSTR" (8 ASCII bytes)

// Bootstrap creates the first local administrator.
//
// It is one transaction: take the advisory lock, refuse when an enabled human
// admin already exists, then create principal + user + admin binding + audit.
// There is deliberately no public HTTP equivalent.
func Bootstrap(ctx context.Context, db *sql.DB, login, displayName, password string) (string, error) {
	login, err := validateLogin(login)
	if err != nil {
		return "", err
	}
	displayName, err = validateDisplayName(displayName)
	if err != nil {
		return "", err
	}
	if err := validateNewPassword(password, ""); err != nil {
		return "", err
	}
	passwordHash, err := HashPassword(password)
	if err != nil {
		return "", err
	}

	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return "", fmt.Errorf("starting the bootstrap transaction: %w", err)
	}
	defer func() { _ = tx.Rollback() }()

	if _, err := tx.ExecContext(ctx, fmt.Sprintf("SELECT pg_advisory_xact_lock(%d)", bootstrapLockKey)); err != nil {
		return "", fmt.Errorf("acquiring the bootstrap lock: %w", err)
	}

	var adminExists bool
	if err := tx.QueryRowContext(ctx, `SELECT EXISTS (
		SELECT 1
		  FROM platform.principals AS p
		  JOIN platform.users AS u ON u.principal_id = p.id
		  JOIN platform.role_bindings AS r ON r.principal_id = p.id AND r.role = 'admin'
		 WHERE p.kind = 'user' AND p.disabled_at IS NULL
	)`).Scan(&adminExists); err != nil {
		return "", fmt.Errorf("checking for an existing administrator: %w", err)
	}
	if adminExists {
		return "", ErrAdminExists
	}

	principalID := NewUUID()
	if _, err := tx.ExecContext(ctx,
		`INSERT INTO platform.principals (id, kind, display_name) VALUES ($1, 'user', $2)`,
		principalID, displayName,
	); err != nil {
		return "", fmt.Errorf("creating the administrator principal: %w", err)
	}
	if _, err := tx.ExecContext(ctx,
		`INSERT INTO platform.users (principal_id, login, password_hash, must_change_password)
		 VALUES ($1, $2, $3, false)`,
		principalID, login, passwordHash,
	); err != nil {
		return "", fmt.Errorf("creating the administrator user: %w", err)
	}
	if _, err := tx.ExecContext(ctx,
		`INSERT INTO platform.role_bindings (principal_id, role) VALUES ($1, 'admin')`,
		principalID,
	); err != nil {
		return "", fmt.Errorf("granting the administrator role: %w", err)
	}
	if err := RecordAudit(ctx, tx, nil, "admin.bootstrap", "principal", &principalID, map[string]any{
		"login": login,
	}); err != nil {
		return "", err
	}
	if err := tx.Commit(); err != nil {
		return "", fmt.Errorf("committing the bootstrap transaction: %w", err)
	}
	return principalID, nil
}

// InviteUser creates a non-loginable user plus a one-time activation token.
// Only the SHA-256 of the token is stored.
func (s *Service) InviteUser(
	ctx context.Context,
	adminID string,
	login, displayName string,
	roles []string,
) (*Invitation, error) {
	login, err := validateLogin(login)
	if err != nil {
		return nil, err
	}
	displayName, err = validateDisplayName(displayName)
	if err != nil {
		return nil, err
	}
	roles, err = normalizeRoles(roles)
	if err != nil {
		return nil, err
	}

	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return nil, fmt.Errorf("starting the invitation transaction: %w", err)
	}
	defer func() { _ = tx.Rollback() }()

	var taken bool
	if err := tx.QueryRowContext(ctx,
		`SELECT EXISTS (SELECT 1 FROM platform.users WHERE login = $1)`, login,
	).Scan(&taken); err != nil {
		return nil, fmt.Errorf("checking the login: %w", err)
	}
	if taken {
		return nil, ErrLoginTaken
	}

	principalID := NewUUID()
	if _, err := tx.ExecContext(ctx,
		`INSERT INTO platform.principals (id, kind, display_name) VALUES ($1, 'user', $2)`,
		principalID, displayName,
	); err != nil {
		return nil, fmt.Errorf("creating the invited principal: %w", err)
	}
	if _, err := tx.ExecContext(ctx,
		`INSERT INTO platform.users (principal_id, login, password_hash, must_change_password)
		 VALUES ($1, $2, $3, true)`,
		principalID, login, PendingActivation,
	); err != nil {
		if isLoginUniqueViolation(err) {
			return nil, ErrLoginTaken
		}
		return nil, fmt.Errorf("creating the invited user: %w", err)
	}
	for _, role := range roles {
		if _, err := tx.ExecContext(ctx,
			`INSERT INTO platform.role_bindings (principal_id, role) VALUES ($1, $2)`,
			principalID, role,
		); err != nil {
			return nil, fmt.Errorf("binding role %s: %w", role, err)
		}
	}

	rawToken := NewToken()
	expiresAt := s.now().Add(InvitationTTL)
	if _, err := tx.ExecContext(ctx,
		`INSERT INTO platform.activation_tokens (id, principal_id, token_hash, expires_at)
		 VALUES ($1, $2, $3, $4)`,
		NewUUID(), principalID, HashToken(rawToken), expiresAt,
	); err != nil {
		return nil, fmt.Errorf("creating the activation token: %w", err)
	}
	if err := RecordAudit(ctx, tx, &adminID, "user.invited", "principal", &principalID, map[string]any{
		"login": login,
		"roles": roles,
	}); err != nil {
		return nil, err
	}
	if err := tx.Commit(); err != nil {
		return nil, fmt.Errorf("committing the invitation: %w", err)
	}
	return &Invitation{PrincipalID: principalID, Token: rawToken, ExpiresAt: expiresAt}, nil
}

func isLoginUniqueViolation(err error) bool {
	var pgErr *pgconn.PgError
	if !errors.As(err, &pgErr) || pgErr.Code != "23505" {
		return false
	}
	return pgErr.ConstraintName == "users_login_key"
}

func validateLogin(login string) (string, error) {
	trimmed := strings.TrimSpace(login)
	if trimmed == "" {
		return "", &ValidationError{Field: "login", Message: "login is required"}
	}
	if len(trimmed) > 255 {
		return "", &ValidationError{Field: "login", Message: "login must be at most 255 characters"}
	}
	return trimmed, nil
}

func validateDisplayName(displayName string) (string, error) {
	trimmed := strings.TrimSpace(displayName)
	if trimmed == "" {
		return "", &ValidationError{Field: "display_name", Message: "display_name is required"}
	}
	if len(trimmed) > 255 {
		return "", &ValidationError{Field: "display_name", Message: "display_name must be at most 255 characters"}
	}
	return trimmed, nil
}

func normalizeRoles(roles []string) ([]string, error) {
	if len(roles) == 0 {
		return nil, &ValidationError{Field: "roles", Message: "at least one role is required"}
	}
	seen := make(map[string]bool, len(roles))
	normalized := make([]string, 0, len(roles))
	for _, role := range roles {
		trimmed := strings.TrimSpace(role)
		if !knownRoles[trimmed] {
			return nil, &ValidationError{Field: "roles", Message: fmt.Sprintf("unknown role %q", role)}
		}
		if seen[trimmed] {
			return nil, &ValidationError{Field: "roles", Message: fmt.Sprintf("duplicate role %q", role)}
		}
		seen[trimmed] = true
		normalized = append(normalized, trimmed)
	}
	return normalized, nil
}

// validateNewPassword applies the F01 minimum and refuses a password that is
// identical to the login (a trivial credential-reuse case called out by the
// contract). login may be empty for bootstrap, which has no previous login.
func validateNewPassword(password, login string) error {
	if len(password) < MinPasswordLength {
		return &ValidationError{
			Field:   "password",
			Message: fmt.Sprintf("password must be at least %d characters", MinPasswordLength),
		}
	}
	if login != "" && password == login {
		return &ValidationError{Field: "password", Message: "password must not equal the login"}
	}
	return nil
}
