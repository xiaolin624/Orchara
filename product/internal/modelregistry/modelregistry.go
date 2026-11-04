// Package modelregistry implements F06/T039: administrator model registration
// and the server-side compatibility-validation flow.
//
// A registration is immutable. The server builds the manifest itself from the
// declared fields (never from client bytes), hashes the RFC 8785 canonical
// bytes, seals them through the object store and records one
// platform.model_profile_versions row in `pending`. The manifest deliberately
// carries no timestamp and no run content: the same registration always
// produces the same bytes and the same digest, and the UNIQUE manifest_sha256
// makes a duplicate registration a conflict rather than a second row.
//
// Nothing here stores or returns a secret. `secret_ref` is a catalog reference
// (the deployer resolves it), and the public projection never includes it.
package modelregistry

import (
	"bytes"
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"regexp"
	"strconv"
	"strings"
	"time"

	"orchara/platform/internal/jcs"
	"orchara/platform/internal/objectstore"
	"orchara/platform/internal/uuid"
)

// ManifestFormatVersion is the server-built manifest format this package
// writes and later versions must keep understanding.
const ManifestFormatVersion = 1

// Registration limits. They are validation, not secrets policy: the catalog
// owns which references exist.
const (
	maxNameLen       = 200
	maxModelIDLen    = 200
	maxProviderLen   = 64
	maxDestination   = 100
	DefaultListLimit = 20
	MaxListLimit     = 100
)

// ValidationError is a request-shape refusal (HTTP 422).
type ValidationError struct {
	Field   string
	Message string
}

func (e *ValidationError) Error() string { return e.Field + ": " + e.Message }

// ConflictError is a state refusal (HTTP 409). Code is a stable machine value.
type ConflictError struct {
	Code    string
	Message string
}

func (e *ConflictError) Error() string { return e.Message }

// ErrNotFound means the model is absent or not visible to the caller.
var ErrNotFound = errors.New("model profile not found")

// ErrInvalidCursor means the list cursor is not one this server minted.
var ErrInvalidCursor = errors.New("invalid model list cursor")

// ErrIdempotencyKeyReuse means the same Idempotency-Key was used for a
// different validate request. The API maps it to the contract's
// `idempotency_conflict` code, exactly like the debug path.
var ErrIdempotencyKeyReuse = errors.New("idempotency key reused with a different request")

// decimalAmount is a non-negative decimal money string. Binary float parsing is
// deliberately not used for money: `NaN`, `Inf`, hexadecimal floats and
// exponent forms are refused by the grammar before they can become a price.
var decimalAmount = regexp.MustCompile(`^\d{1,18}(\.\d{1,18})?$`)

// ModelPrice is the contract's price object.
type ModelPrice struct {
	Currency         string `json:"currency"`
	InputPerMillion  string `json:"input_per_million"`
	OutputPerMillion string `json:"output_per_million"`
}

// ModelConfig is the contract's ModelConfig. Pointers distinguish "absent"
// from a zero value so the required-field rules can be enforced exactly.
type ModelConfig struct {
	Kind                      string     `json:"kind"`
	Protocol                  string     `json:"protocol"`
	ContextWindow             *int64     `json:"context_window,omitempty"`
	MaxOutputTokens           *int64     `json:"max_output_tokens,omitempty"`
	TimeoutMS                 int64      `json:"timeout_ms"`
	EmbeddingDimension        *int64     `json:"embedding_dimension,omitempty"`
	Temperature               *float64   `json:"temperature,omitempty"`
	FallbackProfileVersionIDs []string   `json:"fallback_profile_version_ids,omitempty"`
	Price                     ModelPrice `json:"price"`
}

// ModelInput is the contract's ModelInput (the registration request body).
type ModelInput struct {
	Name            string      `json:"name"`
	Provider        string      `json:"provider"`
	ModelID         string      `json:"model_id"`
	EndpointRef     string      `json:"endpoint_ref"`
	SecretRef       string      `json:"secret_ref"`
	Config          ModelConfig `json:"config"`
	DataDestination string      `json:"data_destination"`
}

// ModelProfile is the contract's ModelProfile projection. It is exactly the
// six contract fields; no secret reference, endpoint reference or config leaks.
type ModelProfile struct {
	ID                 string  `json:"id"`
	Name               string  `json:"name"`
	ModelID            string  `json:"model_id"`
	ValidationState    string  `json:"validation_state"`
	DataDestination    string  `json:"data_destination"`
	EvaluationReportID *string `json:"evaluation_report_id"`
}

// EvaluationJob is the contract's EvaluationJob projection returned by validate.
type EvaluationJob struct {
	ID              string  `json:"id"`
	Kind            string  `json:"kind"`
	State           string  `json:"state"`
	BackgroundJobID string  `json:"background_job_id"`
	ReportID        *string `json:"report_id"`
	ErrorCode       *string `json:"error_code"`
}

// manifestDocument is the exact server-built manifest. Field order is
// irrelevant: it is marshalled, then RFC 8785 canonicalised.
type manifestDocument struct {
	FormatVersion   int         `json:"format_version"`
	Name            string      `json:"name"`
	Provider        string      `json:"provider"`
	ModelID         string      `json:"model_id"`
	EndpointRef     string      `json:"endpoint_ref"`
	SecretRef       string      `json:"secret_ref"`
	Config          ModelConfig `json:"config"`
	DataDestination string      `json:"data_destination"`
}

// Service owns registration and the read projections.
type Service struct {
	DB      *sql.DB
	Store   *objectstore.Store
	Catalog *Catalog
}

// New builds the registry service. A nil catalog is the dev-stub default, so a
// caller that forgets to parse the environment still gets the documented stub
// catalog rather than unrestricted references.
func New(db *sql.DB, store *objectstore.Store, catalog *Catalog) (*Service, error) {
	if catalog == nil {
		var err error
		catalog, err = FromEnv(nil)
		if err != nil {
			return nil, err
		}
	}
	return &Service{DB: db, Store: store, Catalog: catalog}, nil
}

// Manifest returns the canonical bytes and digest of a validated input. It is
// exported for the unit tests and for admin tooling that needs to reproduce the
// server's digest without writing a row.
func Manifest(input ModelInput) ([]byte, string, error) {
	document := manifestDocument{
		FormatVersion:   ManifestFormatVersion,
		Name:            input.Name,
		Provider:        input.Provider,
		ModelID:         input.ModelID,
		EndpointRef:     input.EndpointRef,
		SecretRef:       input.SecretRef,
		Config:          input.Config,
		DataDestination: input.DataDestination,
	}
	raw, err := json.Marshal(document)
	if err != nil {
		return nil, "", fmt.Errorf("encoding the model manifest: %w", err)
	}
	canonical, err := jcs.CanonicalizeBytes(raw)
	if err != nil {
		return nil, "", fmt.Errorf("canonicalising the model manifest: %w", err)
	}
	sum := sha256.Sum256(canonical)
	return canonical, hex.EncodeToString(sum[:]), nil
}

// ValidateInput checks every registration rule against the catalog. It mutates
// nothing and is exported so the API can answer 422 before opening a
// transaction.
func (s *Service) ValidateInput(input *ModelInput) error {
	trim := func(value *string) { *value = strings.TrimSpace(*value) }
	trim(&input.Name)
	trim(&input.Provider)
	trim(&input.ModelID)
	trim(&input.EndpointRef)
	trim(&input.SecretRef)
	trim(&input.DataDestination)
	trim(&input.Config.Kind)
	trim(&input.Config.Protocol)
	trim(&input.Config.Price.Currency)
	trim(&input.Config.Price.InputPerMillion)
	trim(&input.Config.Price.OutputPerMillion)

	if input.Name == "" || len(input.Name) > maxNameLen {
		return &ValidationError{Field: "name", Message: "name must be between 1 and 200 characters"}
	}
	if input.Provider == "" || len(input.Provider) > maxProviderLen {
		return &ValidationError{Field: "provider", Message: "provider must be between 1 and 64 characters"}
	}
	if input.ModelID == "" || len(input.ModelID) > maxModelIDLen {
		return &ValidationError{Field: "model_id", Message: "model_id must be between 1 and 200 characters"}
	}
	if input.DataDestination == "" || len(input.DataDestination) > maxDestination {
		return &ValidationError{Field: "data_destination", Message: "data_destination must be between 1 and 100 characters"}
	}
	if err := validateConfig(&input.Config); err != nil {
		return err
	}
	if s.Catalog == nil {
		return &ValidationError{Field: "endpoint_ref", Message: "no model catalog is configured"}
	}
	endpoint, ok := s.Catalog.Endpoint(input.EndpointRef)
	if !ok {
		return &ValidationError{
			Field:   "endpoint_ref",
			Message: "endpoint_ref must be a pre-registered catalog endpoint",
		}
	}
	if _, ok := s.Catalog.Credential(input.SecretRef); !ok {
		return &ValidationError{
			Field:   "secret_ref",
			Message: "secret_ref must be a pre-registered catalog credential reference",
		}
	}
	if input.SecretRef != endpoint.CredentialRef {
		return &ValidationError{
			Field:   "secret_ref",
			Message: "secret_ref must be the credential reference registered for that endpoint",
		}
	}
	if input.DataDestination != endpoint.DataDestination {
		return &ValidationError{
			Field:   "data_destination",
			Message: "data_destination must match the destination registered for that endpoint",
		}
	}
	if input.Provider == "stub" && !isStubDestination(input.DataDestination) {
		return &ValidationError{
			Field:   "provider",
			Message: "provider \"stub\" is only allowed for the dev-stub or capacity-stub destination",
		}
	}
	return nil
}

func isStubDestination(destination string) bool {
	switch destination {
	case "local-stub", "capacity-stub":
		return true
	}
	return false
}

func validateConfig(config *ModelConfig) error {
	switch config.Kind {
	case "generation", "routing", "embedding", "judge":
	default:
		return &ValidationError{Field: "config.kind", Message: "config.kind must be generation, routing, embedding or judge"}
	}
	switch config.Protocol {
	case "chat_compatible", "embedding_compatible":
	default:
		return &ValidationError{
			Field:   "config.protocol",
			Message: "config.protocol must be chat_compatible or embedding_compatible",
		}
	}
	if config.TimeoutMS < 1 {
		return &ValidationError{Field: "config.timeout_ms", Message: "config.timeout_ms must be positive"}
	}
	if config.Temperature != nil && (*config.Temperature < 0 || *config.Temperature > 2) {
		return &ValidationError{Field: "config.temperature", Message: "config.temperature must be between 0 and 2"}
	}
	if len(config.Price.Currency) != 3 {
		return &ValidationError{Field: "config.price.currency", Message: "config.price.currency must be exactly 3 characters"}
	}
	for field, value := range map[string]string{
		"config.price.input_per_million":  config.Price.InputPerMillion,
		"config.price.output_per_million": config.Price.OutputPerMillion,
	} {
		if !decimalAmount.MatchString(value) {
			return &ValidationError{Field: field, Message: field + " must be a non-negative decimal string"}
		}
	}
	seen := make(map[string]bool, len(config.FallbackProfileVersionIDs))
	for _, id := range config.FallbackProfileVersionIDs {
		if !uuid.Valid(id) {
			return &ValidationError{
				Field:   "config.fallback_profile_version_ids",
				Message: "each fallback profile id must be a UUID",
			}
		}
		if seen[id] {
			return &ValidationError{
				Field:   "config.fallback_profile_version_ids",
				Message: "fallback profile ids must be unique",
			}
		}
		seen[id] = true
	}
	if config.Kind == "embedding" {
		if config.EmbeddingDimension == nil || *config.EmbeddingDimension < 1 {
			return &ValidationError{
				Field:   "config.embedding_dimension",
				Message: "an embedding config requires a positive embedding_dimension",
			}
		}
		return nil
	}
	if config.ContextWindow == nil || *config.ContextWindow < 1 {
		return &ValidationError{
			Field:   "config.context_window",
			Message: "config.context_window must be positive for a non-embedding model",
		}
	}
	if config.MaxOutputTokens == nil || *config.MaxOutputTokens < 1 {
		return &ValidationError{
			Field:   "config.max_output_tokens",
			Message: "config.max_output_tokens must be positive for a non-embedding model",
		}
	}
	return nil
}

// Register validates and registers one candidate. The manifest object and both
// rows commit together (or neither does).
func (s *Service) Register(ctx context.Context, ownerID string, input ModelInput) (*ModelProfile, error) {
	if !uuid.Valid(ownerID) {
		return nil, errors.New("model registration requires a principal id")
	}
	if err := s.ValidateInput(&input); err != nil {
		return nil, err
	}
	if s.Store == nil {
		return nil, objectstore.ErrNotConfigured
	}
	manifestBytes, manifestSHA, err := Manifest(input)
	if err != nil {
		return nil, err
	}

	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return nil, fmt.Errorf("starting the model registration transaction: %w", err)
	}
	defer func() { _ = tx.Rollback() }()

	sealedKey, err := objectstore.SealedKey()
	if err != nil {
		return nil, err
	}
	object, err := s.Store.PutSealed(sealedKey, bytes.NewReader(manifestBytes), int64(len(manifestBytes)))
	if err != nil {
		return nil, fmt.Errorf("sealing the model manifest: %w", err)
	}
	if object.SHA256 != manifestSHA || object.Size != int64(len(manifestBytes)) {
		_ = s.Store.Delete(sealedKey)
		return nil, errors.New("the sealed model manifest does not match the canonical bytes")
	}
	discardObject := func() { _ = s.Store.Delete(sealedKey) }

	artifactID := uuid.NewString()
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO platform.artifacts
		       (id, owner_id, object_key, purpose, sha256, media_type, size_bytes, state, revision)
		VALUES ($1::uuid, $2::uuid, $3, 'model_manifest', $4, 'application/json', $5, 'ready', 1)`,
		artifactID, ownerID, sealedKey, manifestSHA, int64(len(manifestBytes)),
	); err != nil {
		discardObject()
		return nil, fmt.Errorf("registering the model manifest artifact: %w", err)
	}

	modelID := uuid.NewString()
	configJSON, err := json.Marshal(input.Config)
	if err != nil {
		discardObject()
		return nil, fmt.Errorf("encoding the model config: %w", err)
	}
	var insertedID string
	err = tx.QueryRowContext(ctx, `
		INSERT INTO platform.model_profile_versions
		       (id, name, provider, model_id, endpoint_ref, secret_ref, config,
		        data_destination, manifest_sha256, manifest_artifact_id, validation_state)
		VALUES ($1::uuid, $2, $3, $4, $5, $6, $7::jsonb, $8, $9, $10::uuid, 'pending')
		ON CONFLICT (manifest_sha256) DO NOTHING
		RETURNING id::text`,
		modelID, input.Name, input.Provider, input.ModelID, input.EndpointRef,
		input.SecretRef, string(configJSON), input.DataDestination, manifestSHA, artifactID,
	).Scan(&insertedID)
	if errors.Is(err, sql.ErrNoRows) {
		discardObject()
		return nil, &ConflictError{
			Code:    "model_already_registered",
			Message: "an identical model registration already exists",
		}
	}
	if err != nil {
		discardObject()
		return nil, fmt.Errorf("registering the model profile: %w", err)
	}

	profile, err := readProfile(tx.QueryRowContext(ctx, `
		SELECT `+profileColumns+`
		  FROM platform.model_profile_versions
		 WHERE id = $1::uuid`, insertedID))
	if err != nil {
		discardObject()
		return nil, err
	}
	if err := tx.Commit(); err != nil {
		discardObject()
		return nil, fmt.Errorf("committing the model registration: %w", err)
	}
	return profile, nil
}

const profileColumns = `
	id::text, name, model_id, validation_state, data_destination,
	evaluation_report_id::text`

type rowScanner interface {
	Scan(dest ...any) error
}

func readProfile(row rowScanner) (*ModelProfile, error) {
	var (
		profile  ModelProfile
		reportID sql.NullString
	)
	if err := row.Scan(
		&profile.ID, &profile.Name, &profile.ModelID, &profile.ValidationState,
		&profile.DataDestination, &reportID,
	); err != nil {
		return nil, err
	}
	if reportID.Valid {
		value := reportID.String
		profile.EvaluationReportID = &value
	}
	return &profile, nil
}

// List returns one page of model profiles. Admin sees every row; any other
// principal sees only passed, non-revoked rows (the set a shared application
// may select). When applicationID is set the page is the selectable set for
// that application: passed and non-revoked only, even for an administrator.
func (s *Service) List(
	ctx context.Context,
	callerID string,
	admin bool,
	applicationID string,
	cursor string,
	limit int,
) ([]ModelProfile, *string, error) {
	// callerID is part of the seam for a future owner-scoped catalog; the
	// contract's visibility rule is admin-or-passed, so it is not a predicate.
	_ = callerID
	if limit < 1 || limit > MaxListLimit {
		return nil, nil, &ValidationError{Field: "limit", Message: "limit must be between 1 and 100"}
	}
	args := []any{admin}
	visArg := 1
	visibility := fmt.Sprintf(`($%d::boolean OR (validation_state = 'passed' AND revoked_at IS NULL))`, visArg)
	if applicationID != "" {
		if !uuid.Valid(applicationID) {
			return nil, nil, &ValidationError{Field: "application_id", Message: "application_id must be a UUID"}
		}
		visibility = `(validation_state = 'passed' AND revoked_at IS NULL)`
	}
	if strings.TrimSpace(cursor) != "" {
		createdAt, id, err := decodeCursor(cursor)
		if err != nil {
			return nil, nil, err
		}
		args = append(args, createdAt, id)
		visibility += fmt.Sprintf(` AND (created_at, id) < ($%d::timestamptz, $%d::uuid)`,
			len(args)-1, len(args))
	}
	args = append(args, limit+1)
	rows, err := s.DB.QueryContext(ctx, `
		SELECT `+profileColumns+`
		  FROM platform.model_profile_versions
		 WHERE `+visibility+`
		 ORDER BY created_at DESC, id DESC
		 LIMIT $`+strconv.Itoa(len(args)), args...)
	if err != nil {
		return nil, nil, fmt.Errorf("listing model profiles: %w", err)
	}
	defer func() { _ = rows.Close() }()

	items := make([]ModelProfile, 0, limit)
	for rows.Next() {
		profile, err := readProfile(rows)
		if err != nil {
			return nil, nil, fmt.Errorf("reading a model profile: %w", err)
		}
		items = append(items, *profile)
	}
	if err := rows.Err(); err != nil {
		return nil, nil, fmt.Errorf("listing model profiles: %w", err)
	}
	if len(items) <= limit {
		return items, nil, nil
	}
	last := items[limit-1]
	var createdAt time.Time
	if err := s.DB.QueryRowContext(ctx,
		`SELECT created_at FROM platform.model_profile_versions WHERE id = $1::uuid`,
		last.ID,
	).Scan(&createdAt); err != nil {
		return nil, nil, fmt.Errorf("reading the model cursor row: %w", err)
	}
	next := encodeCursor(createdAt, last.ID)
	return items[:limit], &next, nil
}

// Get returns one visible model profile, or ErrNotFound. Non-admins see only
// passed, non-revoked rows, so a pending id is not even discoverable.
func (s *Service) Get(ctx context.Context, modelID, callerID string, admin bool) (*ModelProfile, error) {
	if !uuid.Valid(strings.TrimSpace(modelID)) {
		return nil, ErrNotFound
	}
	profile, err := readProfile(s.DB.QueryRowContext(ctx, `
		SELECT `+profileColumns+`
		  FROM platform.model_profile_versions
		 WHERE id = $1::uuid
		   AND ($2::boolean OR (validation_state = 'passed' AND revoked_at IS NULL))`,
		modelID, admin))
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrNotFound
	}
	if err != nil {
		return nil, fmt.Errorf("reading the model profile: %w", err)
	}
	return profile, nil
}

// encodeCursor/decodeCursor are an opaque (created_at, id) keyset cursor. The
// encoding is base64url of "RFC3339Nano|uuid"; it is deliberately not a secret,
// only a stable position that an unparseable value cannot forge.
func encodeCursor(createdAt time.Time, id string) string {
	return base64.RawURLEncoding.EncodeToString(
		[]byte(createdAt.UTC().Format(time.RFC3339Nano) + "|" + id),
	)
}

func decodeCursor(cursor string) (time.Time, string, error) {
	raw, err := base64.RawURLEncoding.DecodeString(strings.TrimSpace(cursor))
	if err != nil {
		return time.Time{}, "", ErrInvalidCursor
	}
	parts := strings.SplitN(string(raw), "|", 2)
	if len(parts) != 2 || !uuid.Valid(parts[1]) {
		return time.Time{}, "", ErrInvalidCursor
	}
	createdAt, err := time.Parse(time.RFC3339Nano, parts[0])
	if err != nil {
		return time.Time{}, "", ErrInvalidCursor
	}
	return createdAt, parts[1], nil
}
