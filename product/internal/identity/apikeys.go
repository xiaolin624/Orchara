package identity

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"strings"
	"time"

	"orchara/platform/internal/audit"
	"orchara/platform/internal/uuid"
)

// ScopeApplicationRead permits a read of an application only when the service
// principal also holds an unexpired resource grant (or owns the application):
// scope is necessary, never sufficient.
const ScopeApplicationRead = "application:read"

// ScopeApplicationWrite permits creating an application and saving its draft.
// It was added by F02A/T023 together with the endpoints that use it: T020's
// closed vocabulary only knew application:read, so F02A's write path would have
// been unreachable for scoped keys. A write key still owns nothing it did not
// create; ownership and the ACL remain the content decision.
const ScopeApplicationWrite = "application:write"

// APIKeyPrefix makes a leaked key recognisable in a log or a support ticket. It
// is not a secret and carries no meaning beyond "this is an Orchara key".
const APIKeyPrefix = "orchara_"

// knownScopes is the closed vocabulary. A later ticket extends this map
// together with whatever endpoint uses the scope; an unknown value is a
// validation error rather than a stored string no authorizer understands.
var knownScopes = map[string]bool{
	ScopeApplicationRead:  true,
	ScopeApplicationWrite: true,
}

// Errors the API layer maps to contract codes. They are sentinels so handlers
// never inspect driver messages.
var (
	// ErrPersonalScope is returned when a key requests a personal or memory
	// scope: those data domains belong to individual humans, and a service
	// account must never borrow its responsible user's personal data.
	ErrPersonalScope = errors.New("personal scopes are not available to service accounts")
	// ErrInvalidKey covers unknown, revoked and expired keys. They are one
	// error on purpose: distinguishing them would let a caller enumerate keys.
	ErrInvalidKey = errors.New("api key is not recognised")
	// ErrAPIKeyNotFound is a typed not-found for the admin revoke operation.
	ErrAPIKeyNotFound = errors.New("api key not found")
	// ErrResponsibleDisabled means the service account's named responsible
	// human is gone or disabled. That rule takes effect on the very next call,
	// without waiting for an explicit key revocation (SECURITY.md §1).
	ErrResponsibleDisabled = errors.New("the responsible user is disabled")
)

// PersonalScopeError names the refused personal/memory scope. The scope name is
// not a secret; it lets the API return a precise field error.
type PersonalScopeError struct {
	Scope string
}

func (e *PersonalScopeError) Error() string {
	return fmt.Sprintf("scope %q is personal data; service accounts cannot hold it", e.Scope)
}

// Unwrap lets callers use errors.Is(err, ErrPersonalScope).
func (e *PersonalScopeError) Unwrap() error { return ErrPersonalScope }

// APIKeySecret is the one-time result of CreateAPIKey. Secret is returned once
// and never stored; Scopes and ExpiresAt are the persisted properties.
type APIKeySecret struct {
	ID          string
	PrincipalID string
	Scopes      []string
	ExpiresAt   time.Time
	Secret      string
}

// apiKeyState is the decision input for one stored key row. It is a struct (not
// a set of booleans passed around) so the ordering rule can be unit-tested
// without a database.
type apiKeyState struct {
	Kind                string
	Revoked             bool
	Expired             bool
	PrincipalDisabled   bool
	ResponsibleExists   bool
	ResponsibleDisabled bool
}

// apiKeyFailure applies the credential decision in the order the contract
// fixes:
//
//  1. revoked or expired → ErrInvalidKey, indistinguishable from unknown;
//  2. not a service principal, or the principal itself is disabled →
//     ErrPrincipalDisabled;
//  3. the service principal's responsible human is gone or disabled →
//     ErrResponsibleDisabled.
//
// The order matters for tests and for information release: a revoked key never
// reveals the state of its owner.
func apiKeyFailure(state apiKeyState) error {
	if state.Revoked || state.Expired {
		return ErrInvalidKey
	}
	if state.Kind != "service" || state.PrincipalDisabled {
		return ErrPrincipalDisabled
	}
	if !state.ResponsibleExists || state.ResponsibleDisabled {
		return ErrResponsibleDisabled
	}
	return nil
}

// HasScope reports whether the identity's credential carries scope. A session
// cookie has no scopes (F01 scopes only describe API keys); handlers apply
// requireScope to API-key callers only.
func (i *Identity) HasScope(scope string) bool {
	if i == nil {
		return false
	}
	for _, candidate := range i.Scopes {
		if candidate == scope {
			return true
		}
	}
	return false
}

// normalizeScopes validates and normalizes the requested scope set: non-empty,
// no blanks, no duplicates, and a member of the closed F01 vocabulary.
//
// personal:* and memory:* are refused with their own error before the unknown
// check so the caller gets `personal_scope_forbidden`, not a generic validation
// failure: which of the two happened is exactly what the security boundary
// report is about.
func normalizeScopes(scopes []string) ([]string, error) {
	if len(scopes) == 0 {
		return nil, &ValidationError{Field: "scopes", Message: "at least one scope is required"}
	}
	seen := make(map[string]bool, len(scopes))
	normalized := make([]string, 0, len(scopes))
	for _, scope := range scopes {
		trimmed := strings.TrimSpace(scope)
		if trimmed == "" {
			return nil, &ValidationError{Field: "scopes", Message: "a blank scope is not valid"}
		}
		if isPersonalScope(trimmed) {
			return nil, &PersonalScopeError{Scope: trimmed}
		}
		if !knownScopes[trimmed] {
			return nil, &ValidationError{
				Field:   "scopes",
				Message: fmt.Sprintf("unknown scope %q (F01 allows application:read)", scope),
			}
		}
		if seen[trimmed] {
			return nil, &ValidationError{Field: "scopes", Message: fmt.Sprintf("duplicate scope %q", scope)}
		}
		seen[trimmed] = true
		normalized = append(normalized, trimmed)
	}
	return normalized, nil
}

// isPersonalScope matches the personal data domains a service account may never
// request, even if a later ticket adds more scopes to the registry.
func isPersonalScope(scope string) bool {
	return strings.HasPrefix(scope, "personal:") || strings.HasPrefix(scope, "memory:")
}

// validateAPIKeyExpiry refuses an expiry that is not strictly in the future.
// The clock is the Service's injected clock, so the rule is testable and the
// API and the write path read the same time source.
func validateAPIKeyExpiry(expiresAt, now time.Time) error {
	if !expiresAt.After(now) {
		return &ValidationError{Field: "expires_at", Message: "expires_at must be in the future"}
	}
	return nil
}

// newAPIKeySecret mints a secret and its storage hash. The secret is
// `orchara_` + base64url(32 random bytes); only the SHA-256 hex is persisted.
func newAPIKeySecret() (secret, hash string) {
	secret = APIKeyPrefix + NewToken()
	return secret, HashToken(secret)
}

// CreateServiceAccount creates a named service principal owned by a responsible
// human user.
//
// One transaction creates the principal and writes `service_account.created`.
// The responsible person must already exist, be a human user (a service account
// cannot be responsible for another) and be enabled; a service account has no
// users row and therefore cannot sign in.
func (s *Service) CreateServiceAccount(
	ctx context.Context,
	actorID, displayName, responsibleUserID string,
) (string, error) {
	displayName, err := validateDisplayName(displayName)
	if err != nil {
		return "", err
	}
	responsibleUserID = strings.TrimSpace(responsibleUserID)
	if !uuid.Valid(responsibleUserID) {
		return "", &ValidationError{
			Field:   "responsible_user_id",
			Message: "responsible_user_id must be a UUID",
		}
	}

	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return "", fmt.Errorf("starting the service account transaction: %w", err)
	}
	defer func() { _ = tx.Rollback() }()

	// Service-account creation is serialised with every other write that changes
	// who manages the installation or who owns a service account: role changes,
	// disable/enable and handover all take the same "ROLESET1" advisory lock. The
	// responsible-user check below and the INSERT therefore cannot interleave
	// with a disable or a handover: creation either commits before them (and the
	// handover then moves the new row) or sees the disabled person and is refused.
	if _, err := tx.ExecContext(ctx, fmt.Sprintf("SELECT pg_advisory_xact_lock(%d)", rolesLockKey)); err != nil {
		return "", fmt.Errorf("acquiring the service account lock: %w", err)
	}

	var (
		kind        string
		disabledAt  sql.NullTime
		hasUsersRow bool
	)
	err = tx.QueryRowContext(ctx, `
		SELECT p.kind, p.disabled_at,
		       EXISTS (SELECT 1 FROM platform.users AS u WHERE u.principal_id = p.id)
		  FROM platform.principals AS p
		 WHERE p.id = $1`,
		responsibleUserID,
	).Scan(&kind, &disabledAt, &hasUsersRow)
	if errors.Is(err, sql.ErrNoRows) {
		return "", ErrPrincipalNotFound
	}
	if err != nil {
		return "", fmt.Errorf("looking up the responsible principal: %w", err)
	}
	if kind != "user" || !hasUsersRow {
		return "", &ValidationError{
			Field:   "responsible_user_id",
			Message: "the responsible principal must be a human user",
		}
	}
	if disabledAt.Valid {
		return "", ErrPrincipalDisabled
	}

	principalID := NewUUID()
	if _, err := tx.ExecContext(ctx,
		`INSERT INTO platform.principals (id, kind, display_name, responsible_user_id)
		 VALUES ($1, 'service', $2, $3)`,
		principalID, displayName, responsibleUserID,
	); err != nil {
		return "", fmt.Errorf("creating the service principal: %w", err)
	}
	if err := audit.Record(ctx, tx, &actorID, "service_account.created", "principal", &principalID,
		map[string]any{
			"display_name":        displayName,
			"responsible_user_id": responsibleUserID,
		},
	); err != nil {
		return "", err
	}
	if err := tx.Commit(); err != nil {
		return "", fmt.Errorf("committing the service account: %w", err)
	}
	return principalID, nil
}

// CreateAPIKey issues one scoped key for an existing service principal.
//
// The target must be a service principal (keys are not a second credential for
// humans), and its responsible human must still exist and be enabled. Scopes
// are validated against the closed F01 vocabulary, personal scopes are refused,
// and the expiry must be in the future. The secret is returned once; only its
// SHA-256 is stored. The insert and the `api_key.created` audit event share one
// transaction, and the audit metadata never contains the secret or its hash.
func (s *Service) CreateAPIKey(
	ctx context.Context,
	actorID, principalID string,
	scopes []string,
	expiresAt time.Time,
) (*APIKeySecret, error) {
	principalID = strings.TrimSpace(principalID)
	if !uuid.Valid(principalID) {
		return nil, &ValidationError{Field: "principal_id", Message: "principal_id must be a UUID"}
	}
	normalizedScopes, err := normalizeScopes(scopes)
	if err != nil {
		return nil, err
	}
	now := s.now()
	if err := validateAPIKeyExpiry(expiresAt, now); err != nil {
		return nil, err
	}

	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return nil, fmt.Errorf("starting the api key transaction: %w", err)
	}
	defer func() { _ = tx.Rollback() }()

	// A credential must not be born after the disable that should have revoked
	// it. The state read below and the key INSERT share the "ROLESET1" advisory
	// lock used by SetDisabled/SetRoles/Handover, so a disable either commits
	// before this transaction (the check sees it and no key is created) or after
	// it (the disable's revoke UPDATE sees this committed key and kills it).
	if _, err := tx.ExecContext(ctx, fmt.Sprintf("SELECT pg_advisory_xact_lock(%d)", rolesLockKey)); err != nil {
		return nil, fmt.Errorf("acquiring the api key lock: %w", err)
	}

	var (
		kind                string
		disabledAt          sql.NullTime
		responsibleID       sql.NullString
		responsibleDisabled sql.NullTime
		responsibleExists   bool
	)
	err = tx.QueryRowContext(ctx, `
		SELECT p.kind, p.disabled_at, p.responsible_user_id,
		       (rp.id IS NOT NULL) AS responsible_exists,
		       rp.disabled_at
		  FROM platform.principals AS p
		  LEFT JOIN platform.principals AS rp ON rp.id = p.responsible_user_id
		 WHERE p.id = $1`,
		principalID,
	).Scan(&kind, &disabledAt, &responsibleID, &responsibleExists, &responsibleDisabled)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrPrincipalNotFound
	}
	if err != nil {
		return nil, fmt.Errorf("looking up the service principal: %w", err)
	}
	if kind != "service" {
		return nil, &ValidationError{
			Field:   "principal_id",
			Message: "API keys are issued only to service principals",
		}
	}
	if !responsibleExists || responsibleDisabled.Valid {
		return nil, &ValidationError{
			Field:   "principal_id",
			Message: "the service account has no enabled responsible user",
		}
	}
	if disabledAt.Valid {
		return nil, &ValidationError{
			Field:   "principal_id",
			Message: "the service account is disabled",
		}
	}

	secret, hash := newAPIKeySecret()
	keyID := NewUUID()
	expiresAt = expiresAt.UTC()
	if _, err := tx.ExecContext(ctx,
		`INSERT INTO platform.api_keys (id, principal_id, key_hash, scopes, expires_at)
		 VALUES ($1, $2, $3, $4, $5)`,
		keyID, principalID, hash, normalizedScopes, expiresAt,
	); err != nil {
		return nil, fmt.Errorf("creating the api key: %w", err)
	}
	if err := audit.Record(ctx, tx, &actorID, "api_key.created", "api_key", &keyID,
		map[string]any{
			"principal_id": principalID,
			"scopes":       normalizedScopes,
			"expires_at":   expiresAt.Format(time.RFC3339),
		},
	); err != nil {
		return nil, err
	}
	if err := tx.Commit(); err != nil {
		return nil, fmt.Errorf("committing the api key: %w", err)
	}
	return &APIKeySecret{
		ID:          keyID,
		PrincipalID: principalID,
		Scopes:      normalizedScopes,
		ExpiresAt:   expiresAt,
		Secret:      secret,
	}, nil
}

// RevokeAPIKey revokes one key and returns its owning principal id.
//
// Revoking an already-revoked key is success without a second audit row: the
// endpoint is idempotent, and the trail records the moment the key actually
// stopped working, not every repeated request. An unknown key is a typed
// not-found.
func (s *Service) RevokeAPIKey(ctx context.Context, actorID, keyID string) (string, error) {
	if !uuid.Valid(keyID) {
		return "", ErrAPIKeyNotFound
	}
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return "", fmt.Errorf("starting the api key revoke transaction: %w", err)
	}
	defer func() { _ = tx.Rollback() }()

	var principalID string
	err = tx.QueryRowContext(ctx, `
		UPDATE platform.api_keys
		   SET revoked_at = now()
		 WHERE id = $1
		   AND revoked_at IS NULL
		RETURNING principal_id`,
		keyID,
	).Scan(&principalID)
	if errors.Is(err, sql.ErrNoRows) {
		// Either the key does not exist or it was already revoked. Both are
		// decided inside the same transaction, so a concurrent revoke cannot
		// produce two audit rows.
		var existing string
		err = tx.QueryRowContext(ctx,
			`SELECT principal_id FROM platform.api_keys WHERE id = $1`, keyID,
		).Scan(&existing)
		if errors.Is(err, sql.ErrNoRows) {
			return "", ErrAPIKeyNotFound
		}
		if err != nil {
			return "", fmt.Errorf("looking up the api key: %w", err)
		}
		return existing, nil
	}
	if err != nil {
		return "", fmt.Errorf("revoking the api key: %w", err)
	}
	if err := audit.Record(ctx, tx, &actorID, "api_key.revoked", "api_key", &keyID,
		map[string]any{"principal_id": principalID},
	); err != nil {
		return "", err
	}
	if err := tx.Commit(); err != nil {
		return "", fmt.Errorf("committing the api key revoke: %w", err)
	}
	return principalID, nil
}

// AuthenticateAPIKey resolves a presented bearer secret to an identity.
//
// Only the hash is compared, and the decision is made from the stored row every
// time: revocation and expiry therefore take effect on the very next call. The
// identity carries the key's scopes and id for in-process authorization; the
// row itself is never re-read by a handler. Roles are nil: a service principal
// has no role bindings, so the IdentityView becomes `[]` and every role-gated
// admin endpoint refuses it.
func (s *Service) AuthenticateAPIKey(ctx context.Context, presented string) (*Identity, error) {
	presented = strings.TrimSpace(presented)
	if presented == "" {
		return nil, ErrInvalidKey
	}
	var (
		keyID               string
		principalID         string
		kind                string
		scopes              []string
		expiresAt           time.Time
		revokedAt           sql.NullTime
		disabledAt          sql.NullTime
		expired             bool
		responsibleID       sql.NullString
		responsibleDisabled sql.NullTime
	)
	err := s.DB.QueryRowContext(ctx, `
		SELECT k.id, k.principal_id, p.kind, k.scopes, k.expires_at,
		       k.revoked_at, p.disabled_at, (k.expires_at <= now()) AS expired,
		       rp.id, rp.disabled_at
		  FROM platform.api_keys AS k
		  JOIN platform.principals AS p ON p.id = k.principal_id
		  LEFT JOIN platform.principals AS rp ON rp.id = p.responsible_user_id
		 WHERE k.key_hash = $1`,
		HashToken(presented),
	).Scan(
		&keyID, &principalID, &kind, &scopes, &expiresAt,
		&revokedAt, &disabledAt, &expired,
		&responsibleID, &responsibleDisabled,
	)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrInvalidKey
	}
	if err != nil {
		return nil, fmt.Errorf("looking up the api key: %w", err)
	}
	if err := apiKeyFailure(apiKeyState{
		Kind:                kind,
		Revoked:             revokedAt.Valid,
		Expired:             expired,
		PrincipalDisabled:   disabledAt.Valid,
		ResponsibleExists:   responsibleID.Valid,
		ResponsibleDisabled: responsibleDisabled.Valid,
	}); err != nil {
		return nil, err
	}
	return &Identity{
		PrincipalID: principalID,
		Kind:        "service",
		// A service principal has no roles; nil is projected to `[]` by the
		// API's IdentityView, and every role check fails closed.
		Roles:     nil,
		ExpiresAt: expiresAt,
		Method:    AuthMethodAPIKey,
		Scopes:    scopes,
		KeyID:     keyID,
	}, nil
}
