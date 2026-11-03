// Package modelproxy is the platform's controlled model-call boundary.
//
// Rules fixed by IMPLEMENTATION-DETAILS §2/§5 and BACKGROUND-JOBS §3:
//
//   - A worker/handler never calls a provider itself and never sees a
//     credential or a provider URL. It names the candidate model profile
//     version it believes it owns and a logical call key; the server decides
//     which fixture, endpoint and secret that means.
//   - Every physical attempt is one row in `platform.platform_model_calls`: the
//     row exists before the call is dispatched, carries the job owner, the
//     candidate, the scope (job id), a monotonically increasing attempt number,
//     real token usage and reserved/charged decimal amounts. A row can never be
//     a silent success: it is only `succeeded` after the provider answered and
//     its validated response was written as a sealed artifact.
//   - The same logical key with different parameters is a conflict, never a
//     second differently-billed call under the same identity.
//   - The stub provider is a real, explicitly registered provider profile: it
//     is only usable under the dev-stub/capacity-stub environment profile and
//     only for candidates registered with provider "stub"; its output is
//     labelled stub and derived deterministically from the fixture bytes. The
//     real-provider path reads its credential through the candidate's
//     secret_ref inside Go and fails closed (503 provider_not_configured) when
//     no endpoint is deployed. No vendor protocol is compiled in this batch
//     (F06 owns the adapters), so the real path can never pretend to have run.
package modelproxy

import (
	"bytes"
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"strings"

	"orchara/platform/internal/background"
	"orchara/platform/internal/jcs"
	"orchara/platform/internal/objectstore"
	"orchara/platform/internal/uuid"
)

// Errors callers map to HTTP answers. They are stable vocabulary.
var (
	// ErrNotConfigured means no real model endpoint is deployed (or the
	// candidate's credential cannot be resolved). The honest answer is 503
	// provider_not_configured and no ledger success.
	ErrNotConfigured = errors.New("model provider is not configured")
	// ErrAdapterUnavailable means an endpoint is configured but no vendor
	// adapter is compiled in this batch. F06 owns the protocol adapters.
	ErrAdapterUnavailable = errors.New("no vendor adapter is compiled for this provider")
	// ErrCandidateUnavailable means the candidate is missing, revoked, not in a
	// validation state, or carries invalid references.
	ErrCandidateUnavailable = errors.New("candidate model profile is not available for validation")
	// ErrCallConflict means the logical call key was already used with
	// different parameters.
	ErrCallConflict = errors.New("logical model call already used with different parameters")
	// ErrResultStoreUnavailable means the validated response cannot be sealed.
	ErrResultStoreUnavailable = errors.New("object store is not configured; the model result cannot be sealed")
	// ErrReplayUnavailable means a recorded successful call cannot be replayed
	// because its sealed result cannot be read back. The server never invents a
	// placeholder answer and never re-dispatches a recorded-success call.
	ErrReplayUnavailable = errors.New("the recorded model result cannot be replayed")
)

// Currency is the ledger currency for this batch. No budget policy is enabled
// yet (F05/F13A); the amounts are recorded so the later settlement has real
// numbers to settle.
const Currency = "USD"

// reservedUnitsPerInputToken is the conservative pre-dispatch reservation in
// 1e-8 currency units (1e-6 per input token).
const reservedUnitsPerInputToken int64 = 100

// Candidate is one registered model profile version.
type Candidate struct {
	ID                 string
	Provider           string
	ModelID            string
	EndpointRef        string
	SecretRef          string
	Config             json.RawMessage
	ValidationState    string
	Revoked            bool
	ManifestArtifactID string
}

// InvokeInput is one job-scoped model call. Fixture bytes are loaded by the
// server from the job's input artifact; the caller never supplies them.
type InvokeInput struct {
	JobID             string
	OwnerID           string
	Candidate         *Candidate
	LogicalKey        string
	Params            map[string]any
	Fixture           []byte
	FixtureSHA256     string
	FixtureArtifactID string
}

// InvokeResult is the contract's model-call answer plus a replay flag for
// diagnostics.
type InvokeResult struct {
	CallID           string
	State            string
	ResultArtifactID *string
	Data             map[string]any
	Replayed         bool
}

// ReferenceCatalog is the deployer-registered reference set the proxy accepts.
// A registration may name either a legacy deployment reference (`env:`/`file:`)
// or a catalog id; anything else is refused before a provider can see it.
type ReferenceCatalog interface {
	HasEndpoint(id string) bool
	HasCredential(id string) bool
}

// Service owns candidate resolution and the model-call ledger. It writes sealed
// result artifacts but never activates anything else.
type Service struct {
	DB        *sql.DB
	Store     *objectstore.Store
	Providers *Providers
	Catalog   ReferenceCatalog
}

// NewService builds the model proxy.
func NewService(db *sql.DB, store *objectstore.Store, providers *Providers, catalog ReferenceCatalog) *Service {
	return &Service{DB: db, Store: store, Providers: providers, Catalog: catalog}
}

// Candidate loads and validates one candidate row.
//
// Only pending/running validations are callable: a passed candidate is not a
// validation subject any more, and a revoked/failed one is not available at all.
// The reference shapes are checked here so a row that names an arbitrary URL or
// path can never reach the provider layer.
func (s *Service) Candidate(ctx context.Context, candidateID string) (*Candidate, error) {
	if !uuid.Valid(strings.TrimSpace(candidateID)) {
		return nil, ErrCandidateUnavailable
	}
	var (
		candidate Candidate
		configRaw []byte
		revoked   sql.NullTime
	)
	err := s.DB.QueryRowContext(ctx, `
		SELECT id::text, provider, model_id, endpoint_ref, secret_ref, config,
		       validation_state, revoked_at, manifest_artifact_id::text
		  FROM platform.model_profile_versions
		 WHERE id = $1::uuid`, candidateID,
	).Scan(
		&candidate.ID, &candidate.Provider, &candidate.ModelID, &candidate.EndpointRef,
		&candidate.SecretRef, &configRaw, &candidate.ValidationState, &revoked,
		&candidate.ManifestArtifactID,
	)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrCandidateUnavailable
	}
	if err != nil {
		return nil, fmt.Errorf("reading the candidate model profile: %w", err)
	}
	if revoked.Valid {
		return nil, ErrCandidateUnavailable
	}
	switch candidate.ValidationState {
	case "pending", "running":
	default:
		return nil, ErrCandidateUnavailable
	}
	candidate.Config = json.RawMessage(configRaw)
	if err := validateReferences(&candidate, s.Catalog); err != nil {
		return nil, err
	}
	return &candidate, nil
}

// validateReferences enforces IMPLEMENTATION-DETAILS §2: endpoint_ref/secret_ref
// are deployer-registered references, never a URL or a file path.
//
// Two reference forms are accepted:
//   - the T024 deployment form (`env:NAME` for the endpoint, `env:`/`file:` for
//     the credential), used by the maintenance tests and any deployment that
//     registers candidates directly;
//   - an F06 catalog id, which must be present in the deployer-registered
//     catalog. A slug that is not registered is refused exactly like an
//     unregistered `env:` name would be.
func validateReferences(candidate *Candidate, catalog ReferenceCatalog) error {
	endpoint := strings.TrimSpace(candidate.EndpointRef)
	legacyEndpoint := strings.HasPrefix(endpoint, "env:") && len(endpoint) > len("env:")
	catalogEndpoint := catalog != nil && catalog.HasEndpoint(endpoint)
	if !legacyEndpoint && !catalogEndpoint {
		return fmt.Errorf("%w: endpoint_ref must be a registered catalog endpoint or an env: reference", ErrCandidateUnavailable)
	}
	secret := strings.TrimSpace(candidate.SecretRef)
	legacySecret := false
	if _, err := parseReference(secret); err == nil {
		legacySecret = true
	}
	catalogSecret := catalog != nil && catalog.HasCredential(secret)
	if !legacySecret && !catalogSecret {
		return fmt.Errorf("%w: secret_ref is not a registered credential reference", ErrCandidateUnavailable)
	}
	return nil
}

// Invoke runs one job-scoped model call and meters it.
//
// The ledger row is written before dispatch, the provider answer is sealed as a
// ready server-side artifact, and only then does the row become `succeeded`.
// A dispatch error marks the row `failed` with no charge and returns the typed
// error; nothing pretends a call succeeded.
func (s *Service) Invoke(ctx context.Context, input InvokeInput) (InvokeResult, error) {
	if input.Candidate == nil {
		return InvokeResult{}, ErrCandidateUnavailable
	}
	logicalKey := strings.TrimSpace(input.LogicalKey)
	if logicalKey == "" || len(logicalKey) > 200 {
		return InvokeResult{}, &background.ValidationError{
			Field:   "logical_key",
			Message: "logical_key must be between 1 and 200 characters",
		}
	}
	for _, char := range logicalKey {
		if char < 0x20 || char == 0x7f {
			return InvokeResult{}, &background.ValidationError{
				Field:   "logical_key",
				Message: "logical_key must be printable",
			}
		}
	}
	if !uuid.Valid(strings.TrimSpace(input.JobID)) || !uuid.Valid(strings.TrimSpace(input.OwnerID)) {
		return InvokeResult{}, fmt.Errorf("%w: job or owner is not a UUID", ErrCallConflict)
	}
	if s.Store == nil {
		// A call whose validated answer cannot be sealed would still be billable
		// at the provider; refuse before reserving an attempt.
		return InvokeResult{}, ErrResultStoreUnavailable
	}
	if s.Providers == nil {
		return InvokeResult{}, ErrNotConfigured
	}
	provider, err := s.Providers.For(input.Candidate)
	if err != nil {
		return InvokeResult{}, err
	}
	// Refuse before reserving an attempt: a provider that cannot dispatch must
	// not leave a ledger row that a later reader could mistake for a billed call.
	if err := provider.Available(input.Candidate); err != nil {
		return InvokeResult{}, err
	}
	paramsDigest, err := digestParams(input.Params)
	if err != nil {
		return InvokeResult{}, &background.ValidationError{
			Field:   "params",
			Message: "params must be canonical JSON",
		}
	}

	callID, attempt, replay, err := s.reserveAttempt(ctx, input, paramsDigest)
	if err != nil {
		return InvokeResult{}, err
	}
	if replay != nil {
		return *replay, nil
	}

	if _, err := s.DB.ExecContext(ctx, `
		UPDATE platform.platform_model_calls SET state = 'dispatching' WHERE id = $1::uuid`,
		callID,
	); err != nil {
		return InvokeResult{}, fmt.Errorf("marking the model call as dispatching: %w", err)
	}

	response, callErr := provider.Call(ctx, CallRequest{
		Candidate:     input.Candidate,
		Params:        input.Params,
		Fixture:       input.Fixture,
		FixtureSHA256: input.FixtureSHA256,
		Attempt:       attempt,
	})
	if callErr != nil {
		// The attempt happened but produced no usable answer. Record the
		// failure honestly: no tokens, no charge, no result artifact.
		if _, err := s.DB.ExecContext(ctx, `
			UPDATE platform.platform_model_calls
			   SET state = 'failed', charged_amount = 0
			 WHERE id = $1::uuid`, callID,
		); err != nil {
			return InvokeResult{}, fmt.Errorf("recording the failed model call: %w", err)
		}
		return InvokeResult{}, callErr
	}

	if s.Store == nil {
		_, _ = s.DB.ExecContext(ctx, `
			UPDATE platform.platform_model_calls SET state = 'failed', charged_amount = 0
			 WHERE id = $1::uuid`, callID)
		return InvokeResult{}, ErrResultStoreUnavailable
	}
	object, err := s.Store.CreateSealed(bytes.NewReader(response.Output), objectstore.MaxObjectBytes)
	if err != nil {
		_, _ = s.DB.ExecContext(ctx, `
			UPDATE platform.platform_model_calls SET state = 'failed', charged_amount = 0
			 WHERE id = $1::uuid`, callID)
		return InvokeResult{}, fmt.Errorf("sealing the model result: %w", err)
	}
	resultArtifactID := uuid.NewString()
	if err := s.finishSucceeded(ctx, input, callID, resultArtifactID, object, response); err != nil {
		// The sealed object is left for T026's orphan sweep; the ledger row is
		// still `dispatching` on a broken transaction, which is impossible to
		// report as success.
		return InvokeResult{}, err
	}
	return InvokeResult{
		CallID:           callID,
		State:            "succeeded",
		ResultArtifactID: &resultArtifactID,
		Data:             response.Data,
	}, nil
}

// reserveAttempt locks the job's ledger namespace, applies the replay/conflict
// rules and inserts the prepared row.
//
// A non-nil replay result means the logical key already succeeded with the same
// params: the recorded answer is returned and nothing new is billed. A nil
// replay with a nil error means the caller may dispatch attempt N.
func (s *Service) reserveAttempt(
	ctx context.Context,
	input InvokeInput,
	paramsDigest string,
) (callID string, attempt int, replay *InvokeResult, err error) {
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return "", 0, nil, fmt.Errorf("starting the model call transaction: %w", err)
	}
	defer func() { _ = tx.Rollback() }()

	// Serialise every model call for this job: the attempt number is computed
	// from the previous max, and two concurrent calls must not pick the same
	// physical_attempt (the UNIQUE constraint is the backstop, the lock is the
	// deterministic answer).
	var locked int
	err = tx.QueryRowContext(ctx, `
		SELECT 1 FROM platform.background_jobs WHERE id = $1::uuid FOR UPDATE`,
		input.JobID,
	).Scan(&locked)
	if errors.Is(err, sql.ErrNoRows) {
		return "", 0, nil, background.ErrNotFound
	}
	if err != nil {
		return "", 0, nil, fmt.Errorf("locking the model call job: %w", err)
	}

	var (
		lastID      string
		lastDigest  sql.NullString
		lastState   string
		lastAttempt int
	)
	err = tx.QueryRowContext(ctx, `
		SELECT id::text, params_sha256, state, physical_attempt
		  FROM platform.platform_model_calls
		 WHERE scope_key = $1 AND logical_key = $2
		 ORDER BY physical_attempt DESC
		 LIMIT 1`, input.JobID, strings.TrimSpace(input.LogicalKey),
	).Scan(&lastID, &lastDigest, &lastState, &lastAttempt)
	switch {
	case errors.Is(err, sql.ErrNoRows):
		attempt = 1
	case err != nil:
		return "", 0, nil, fmt.Errorf("reading the previous model call: %w", err)
	default:
		if !lastDigest.Valid || lastDigest.String != paramsDigest {
			return "", 0, nil, ErrCallConflict
		}
		if lastState == "succeeded" {
			// A recorded success is replayed verbatim: re-read the sealed result
			// artifact that holds the provider's canonical answer and return it.
			// The handler derives its report (compatibility, provider/stub
			// markers) from this data, so a placeholder would silently turn a
			// passed validation into a failed one. The read happens in the same
			// transaction as the ledger lookup, so a concurrent completion cannot
			// half-apply underneath the replay.
			replay, err := s.recordedResult(ctx, tx, lastID, input.OwnerID)
			if err != nil {
				return "", 0, nil, err
			}
			if err := tx.Commit(); err != nil {
				return "", 0, nil, fmt.Errorf("committing the model call replay: %w", err)
			}
			return "", 0, &replay, nil
		}
		attempt = lastAttempt + 1
	}

	callID = uuid.NewString()
	reserved := formatUnits(estimateReservedUnits(len(input.Fixture)))
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO platform.platform_model_calls
			(id, owner_id, background_job_id, model_profile_version_id, scope_key, logical_key,
			 physical_attempt, state, currency, reserved_amount, params_sha256)
		VALUES ($1::uuid, $2::uuid, $3::uuid, $4::uuid, $5, $6, $7, 'prepared', $8, $9::numeric, $10)`,
		callID, input.OwnerID, input.JobID, input.Candidate.ID,
		input.JobID, strings.TrimSpace(input.LogicalKey), attempt, Currency, reserved, paramsDigest,
	); err != nil {
		if isUniqueViolation(err) {
			return "", 0, nil, ErrCallConflict
		}
		return "", 0, nil, fmt.Errorf("recording the model call attempt: %w", err)
	}
	if err := tx.Commit(); err != nil {
		return "", 0, nil, fmt.Errorf("committing the model call attempt: %w", err)
	}
	return callID, attempt, nil, nil
}

// recordedResult re-reads the sealed answer of a recorded successful call and
// returns it verbatim (call id, result artifact, provider data).
//
// Replay must not invent a placeholder: the handler derives its compatibility
// report from this data, so `{"replayed": true}` would make a passed validation
// look failed. The ledger row, the artifact row and the sealed bytes are all
// re-checked here; a recorded success whose bytes cannot be verified is an error,
// never a re-dispatch and never a made-up answer.
func (s *Service) recordedResult(
	ctx context.Context,
	tx *sql.Tx,
	callID, ownerID string,
) (InvokeResult, error) {
	var (
		resultID      sql.NullString
		objectKey     sql.NullString
		state         sql.NullString
		deleted       sql.NullTime
		storedSHA     sql.NullString
		artifactOwner sql.NullString
	)
	err := tx.QueryRowContext(ctx, `
		SELECT c.result_artifact_id::text, a.object_key, a.state, a.deleted_at, a.sha256,
		       a.owner_id::text
		  FROM platform.platform_model_calls AS c
		  LEFT JOIN platform.artifacts AS a ON a.id = c.result_artifact_id
		 WHERE c.id = $1::uuid AND c.owner_id = $2::uuid`, callID, ownerID,
	).Scan(&resultID, &objectKey, &state, &deleted, &storedSHA, &artifactOwner)
	if errors.Is(err, sql.ErrNoRows) {
		return InvokeResult{}, fmt.Errorf("%w: the recorded call is gone", ErrReplayUnavailable)
	}
	if err != nil {
		return InvokeResult{}, fmt.Errorf("reading the recorded model call: %w", err)
	}
	if !resultID.Valid || !objectKey.Valid || !state.Valid || !storedSHA.Valid ||
		deleted.Valid || state.String != "ready" ||
		!strings.EqualFold(artifactOwner.String, ownerID) {
		return InvokeResult{}, fmt.Errorf(
			"%w: the recorded result artifact is not a ready object of this call", ErrReplayUnavailable,
		)
	}
	if s.Store == nil {
		return InvokeResult{}, ErrResultStoreUnavailable
	}
	content, digest, err := readReplayObject(s.Store, objectKey.String)
	switch {
	case errors.Is(err, objectstore.ErrNotExist),
		errors.Is(err, objectstore.ErrInvalidKey),
		errors.Is(err, objectstore.ErrTooLarge):
		return InvokeResult{}, fmt.Errorf("%w: %v", ErrReplayUnavailable, err)
	case err != nil:
		return InvokeResult{}, fmt.Errorf("reading the recorded model result: %w", err)
	}
	if !strings.EqualFold(digest, storedSHA.String) {
		return InvokeResult{}, fmt.Errorf(
			"%w: the sealed bytes do not match the recorded hash", ErrReplayUnavailable,
		)
	}
	var data map[string]any
	decoder := json.NewDecoder(bytes.NewReader(content))
	// Numbers keep their literal form: replay must return the recorded answer,
	// not a float64 reinterpretation of it.
	decoder.UseNumber()
	if err := decoder.Decode(&data); err != nil || data == nil {
		return InvokeResult{}, fmt.Errorf("%w: the recorded result is not a JSON object", ErrReplayUnavailable)
	}
	if err := decoder.Decode(&struct{}{}); !errors.Is(err, io.EOF) {
		return InvokeResult{}, fmt.Errorf("%w: the recorded result has trailing data", ErrReplayUnavailable)
	}
	artifact := resultID.String
	return InvokeResult{
		CallID:           callID,
		State:            "succeeded",
		ResultArtifactID: &artifact,
		Data:             data,
		Replayed:         true,
	}, nil
}

// readReplayObject reads at most MaxObjectBytes of one sealed object and returns
// its bytes and SHA-256. Bytes over the platform ceiling are refused rather than
// hashed forever.
func readReplayObject(store *objectstore.Store, key string) ([]byte, string, error) {
	reader, _, err := store.Open(key)
	if err != nil {
		return nil, "", err
	}
	defer func() { _ = reader.Close() }()
	content, err := io.ReadAll(io.LimitReader(reader, objectstore.MaxObjectBytes+1))
	if err != nil {
		return nil, "", err
	}
	if int64(len(content)) > objectstore.MaxObjectBytes {
		return nil, "", objectstore.ErrTooLarge
	}
	sum := sha256.Sum256(content)
	return content, hex.EncodeToString(sum[:]), nil
}

// finishSucceeded writes the validated response as a ready sealed artifact and
// closes the ledger row in one transaction.
func (s *Service) finishSucceeded(
	ctx context.Context,
	input InvokeInput,
	callID, artifactID string,
	object objectstore.Object,
	response ProviderResponse,
) error {
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return fmt.Errorf("starting the model result transaction: %w", err)
	}
	defer func() { _ = tx.Rollback() }()
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO platform.artifacts
			(id, owner_id, object_key, purpose, sha256, media_type, size_bytes, state, revision)
		VALUES ($1::uuid, $2::uuid, $3, 'model_result', $4, 'application/json', $5, 'ready', 1)`,
		artifactID, input.OwnerID, object.Key, object.SHA256, object.Size,
	); err != nil {
		return fmt.Errorf("recording the model result artifact: %w", err)
	}
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO platform.background_job_artifacts
			(background_job_id, artifact_id, relation, purpose)
		VALUES ($1::uuid, $2::uuid, 'output', 'model_result')`,
		input.JobID, artifactID,
	); err != nil {
		return fmt.Errorf("linking the model result artifact: %w", err)
	}
	result, err := tx.ExecContext(ctx, `
		UPDATE platform.platform_model_calls
		   SET state = 'succeeded', result_artifact_id = $2::uuid,
		       tokens_in = $3, tokens_out = $4, charged_amount = $5::numeric
		 WHERE id = $1::uuid AND state = 'dispatching'`,
		callID, artifactID, response.TokensIn, response.TokensOut, formatUnits(response.ChargedUnits),
	)
	if err != nil {
		return fmt.Errorf("closing the model call ledger row: %w", err)
	}
	affected, err := result.RowsAffected()
	if err != nil {
		return fmt.Errorf("closing the model call ledger row: %w", err)
	}
	if affected != 1 {
		return fmt.Errorf("model call %s was not in dispatching state", callID)
	}
	if err := tx.Commit(); err != nil {
		return fmt.Errorf("committing the model result: %w", err)
	}
	return nil
}

// digestParams is the conflict-detection identity of a logical call: JCS bytes
// of the params (so key order cannot fake a difference), then SHA-256.
func digestParams(params map[string]any) (string, error) {
	raw, err := json.Marshal(params)
	if err != nil {
		return "", err
	}
	canonical, err := jcs.CanonicalizeBytes(raw)
	if err != nil {
		return "", err
	}
	sum := sha256.Sum256(canonical)
	return hex.EncodeToString(sum[:]), nil
}

// fixtureSHA256Hex is the server's hash of the fixture bytes it read from the
// sealed object. The stub reports it so a test can prove the real fixture was
// used rather than a substitute.
func fixtureSHA256Hex(content []byte) string {
	sum := sha256.Sum256(content)
	return hex.EncodeToString(sum[:])
}

// formatUnits renders an amount in 1e-8 currency units as a decimal string that
// PostgreSQL numeric(20,8) accepts exactly (no float ever touches money).
func formatUnits(units int64) string {
	if units < 0 {
		units = 0
	}
	return fmt.Sprintf("%d.%08d", units/100000000, units%100000000)
}

// estimateReservedUnits is the pre-dispatch reservation: the input token
// estimate (one token per four fixture bytes) at 1e-6 per token. It is recorded
// before the call, never charged; the real charge comes from the provider's
// usage.
func estimateReservedUnits(fixtureBytes int) int64 {
	tokens := (fixtureBytes + 3) / 4
	return int64(tokens) * reservedUnitsPerInputToken
}

func isUniqueViolation(err error) bool {
	type sqlState interface{ SQLState() string }
	var state sqlState
	if errors.As(err, &state) {
		return state.SQLState() == "23505"
	}
	return false
}
