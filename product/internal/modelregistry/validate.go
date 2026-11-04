package modelregistry

// The validate flow: one synthetic compatibility case, one background job, one
// evaluation row. The server chooses the fixture bytes; the worker never
// supplies a case, a path or a provider URL.
//
// A compatibility *verdict* is a report outcome: a model that fails the check
// is a succeeded job with a failed report. The completion guard in
// internal/background owns that decision; this file only enqueues the work and
// records the user-visible 202.

import (
	"bytes"
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"unicode"

	"orchara/platform/internal/background"
	"orchara/platform/internal/jcs"
	"orchara/platform/internal/objectstore"
	"orchara/platform/internal/uuid"
)

// FixtureSchema is the synthetic compatibility fixture schema. It carries no
// user data and no model output.
const FixtureSchema = "orchara.model-compat/1"

// FixtureCheckStructuredOutput is the only check the T039 fixture asks for.
const FixtureCheckStructuredOutput = "structured_output"

// FixtureCaseInvalid is the documented stub simulation: a model profile whose
// provider is `stub` and whose model_id ends in `-incompatible` is handed this
// case, and the stub provider reports a failed structured-output check. It is
// how the failed-verdict path can be exercised deterministically without a real
// incompatible model.
const (
	FixtureCaseOK          = "ok"
	FixtureCaseInvalidCase = "structured_output_invalid"
	incompatibleSuffix     = "-incompatible"
)

// BeginValidation starts (or joins) a compatibility validation for one model.
//
// The row lock serialises concurrent starts: the first caller enqueues and
// moves the model to running, the second observes running and returns the same
// evaluation instead of a second job. A passed or revoked model is a 409.
func (s *Service) BeginValidation(
	ctx context.Context,
	modelID, ownerID, idempotencyKey, path string,
) (*EvaluationJob, error) {
	key := strings.TrimSpace(idempotencyKey)
	if key == "" || len(key) > 200 {
		return nil, &ValidationError{
			Field:   "Idempotency-Key",
			Message: "the Idempotency-Key header is required and must be 1-200 characters",
		}
	}
	for _, char := range key {
		if char < 0x20 || char == 0x7f || unicode.IsSpace(char) {
			return nil, &ValidationError{
				Field:   "Idempotency-Key",
				Message: "the Idempotency-Key header must be printable and contain no spaces",
			}
		}
	}
	if !uuid.Valid(strings.TrimSpace(modelID)) {
		return nil, ErrNotFound
	}
	if !uuid.Valid(ownerID) {
		return nil, errors.New("model validation requires a principal id")
	}

	requestSHA, err := validateRequestDigest(modelID)
	if err != nil {
		return nil, err
	}

	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return nil, fmt.Errorf("starting the model validation transaction: %w", err)
	}
	defer func() { _ = tx.Rollback() }()

	var (
		provider    string
		modelName   string
		state       string
		revoked     sql.NullTime
		manifestSHA string
	)
	err = tx.QueryRowContext(ctx, `
		SELECT provider, model_id, validation_state, revoked_at, manifest_sha256
		  FROM platform.model_profile_versions
		 WHERE id = $1::uuid
		 FOR UPDATE`, modelID,
	).Scan(&provider, &modelName, &state, &revoked, &manifestSHA)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrNotFound
	}
	if err != nil {
		return nil, fmt.Errorf("reading the model profile: %w", err)
	}
	if revoked.Valid {
		return nil, &ConflictError{Code: "model_revoked", Message: "a revoked model cannot be validated"}
	}
	switch state {
	case "running":
		existing, err := readRunningEvaluation(ctx, tx, modelID)
		if err != nil {
			return nil, err
		}
		if existing != nil {
			return existing, nil
		}
		// A running row with no evaluation cannot be explained; refuse rather
		// than enqueue a second job for the same model.
		return nil, &ConflictError{
			Code:    "validation_in_progress",
			Message: "this model already has a validation in progress",
		}
	case "passed":
		return nil, &ConflictError{
			Code:    "already_validated",
			Message: "this model already passed compatibility validation",
		}
	case "pending", "failed":
		// Proceed: a failed model may be validated again (same immutable
		// profile, new evaluation).
	default:
		return nil, &ConflictError{Code: "model_state_invalid", Message: "the model cannot be validated in its current state"}
	}

	// Idempotent replay: the same key and the same request returns the response
	// recorded for that key. A different request under the same key is a
	// conflict, never a silent second job.
	if replay, err := replayIdempotent(ctx, tx, ownerID, path, key, requestSHA); err != nil {
		return nil, err
	} else if replay != nil {
		if err := tx.Commit(); err != nil {
			return nil, fmt.Errorf("committing the idempotent replay: %w", err)
		}
		return replay, nil
	}

	if s.Store == nil {
		return nil, objectstore.ErrNotConfigured
	}
	caseName := FixtureCaseOK
	if provider == "stub" && strings.HasSuffix(modelName, incompatibleSuffix) {
		caseName = FixtureCaseInvalidCase
	}
	fixture, err := fixtureBytes(caseName, modelID)
	if err != nil {
		return nil, err
	}
	fixtureSHA := sha256Hex(fixture)

	sealedKey, err := objectstore.SealedKey()
	if err != nil {
		return nil, err
	}
	object, err := s.Store.PutSealed(sealedKey, bytes.NewReader(fixture), int64(len(fixture)))
	if err != nil {
		return nil, fmt.Errorf("sealing the compatibility fixture: %w", err)
	}
	if object.SHA256 != fixtureSHA || object.Size != int64(len(fixture)) {
		_ = s.Store.Delete(sealedKey)
		return nil, errors.New("the sealed compatibility fixture does not match the fixture bytes")
	}
	discardObject := func() { _ = s.Store.Delete(sealedKey) }

	fixtureID := uuid.NewString()
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO platform.artifacts
		       (id, owner_id, object_key, purpose, sha256, media_type, size_bytes, state, revision)
		VALUES ($1::uuid, $2::uuid, $3, 'model_fixture', $4, 'application/json', $5, 'ready', 1)`,
		fixtureID, ownerID, sealedKey, fixtureSHA, int64(len(fixture)),
	); err != nil {
		discardObject()
		return nil, fmt.Errorf("registering the compatibility fixture artifact: %w", err)
	}

	evaluationID := uuid.NewString()
	job, _, err := background.EnqueueTx(ctx, tx, background.EnqueueParams{
		Kind:            background.KindModelValidate,
		TargetID:        modelID,
		OwnerID:         ownerID,
		DedupKey:        evaluationID,
		InputArtifactID: fixtureID,
	})
	if err != nil {
		discardObject()
		return nil, fmt.Errorf("enqueueing the model validation job: %w", err)
	}

	if _, err := tx.ExecContext(ctx, `
		INSERT INTO platform.evaluation_jobs
		       (id, kind, owner_id, background_job_id, model_profile_version_id,
		        dataset_artifact_id, state)
		VALUES ($1::uuid, 'model', $2::uuid, $3::uuid, $4::uuid, $5::uuid, 'queued')`,
		evaluationID, ownerID, job.ID, modelID, fixtureID,
	); err != nil {
		discardObject()
		return nil, fmt.Errorf("recording the model evaluation: %w", err)
	}
	if _, err := tx.ExecContext(ctx, `
		UPDATE platform.model_profile_versions
		   SET validation_state = 'running'
		 WHERE id = $1::uuid AND validation_state IN ('pending', 'failed')`, modelID,
	); err != nil {
		discardObject()
		return nil, fmt.Errorf("marking the model validation running: %w", err)
	}

	evaluation := &EvaluationJob{
		ID:              evaluationID,
		Kind:            "model",
		State:           "queued",
		BackgroundJobID: job.ID,
	}
	responseJSON, err := json.Marshal(evaluation)
	if err != nil {
		discardObject()
		return nil, fmt.Errorf("encoding the validation response: %w", err)
	}
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO platform.idempotency_keys
		       (principal_id, method, path, key, request_sha256, response_code,
		        response_json, expires_at)
		VALUES ($1::uuid, 'POST', $2, $3, $4, 202, $5::jsonb, now() + interval '24 hours')`,
		ownerID, path, key, requestSHA, string(responseJSON),
	); err != nil {
		discardObject()
		return nil, fmt.Errorf("recording the validation idempotency key: %w", err)
	}

	if err := tx.Commit(); err != nil {
		discardObject()
		return nil, fmt.Errorf("committing the model validation: %w", err)
	}
	return evaluation, nil
}

// fixtureBytes is the deterministic synthetic fixture. It names the model
// profile so a report from one model can never be replayed for another.
func fixtureBytes(caseName, modelID string) ([]byte, error) {
	raw, err := json.Marshal(map[string]any{
		"schema":                   FixtureSchema,
		"case":                     caseName,
		"check":                    FixtureCheckStructuredOutput,
		"model_profile_version_id": modelID,
	})
	if err != nil {
		return nil, fmt.Errorf("encoding the compatibility fixture: %w", err)
	}
	canonical, err := jcs.CanonicalizeBytes(raw)
	if err != nil {
		return nil, fmt.Errorf("canonicalising the compatibility fixture: %w", err)
	}
	return canonical, nil
}

// validateRequestDigest is the fixed request hash the idempotency receipt
// records: the canonical bytes of the one input the request carries.
func validateRequestDigest(modelID string) (string, error) {
	raw, err := json.Marshal(map[string]any{"model_profile_version_id": modelID})
	if err != nil {
		return "", fmt.Errorf("encoding the validation request: %w", err)
	}
	canonical, err := jcs.CanonicalizeBytes(raw)
	if err != nil {
		return "", fmt.Errorf("canonicalising the validation request: %w", err)
	}
	sum := sha256.Sum256(canonical)
	return hex.EncodeToString(sum[:]), nil
}

func sha256Hex(content []byte) string {
	sum := sha256.Sum256(content)
	return hex.EncodeToString(sum[:])
}

func readRunningEvaluation(ctx context.Context, tx *sql.Tx, modelID string) (*EvaluationJob, error) {
	var (
		evaluation EvaluationJob
		reportID   sql.NullString
		errorCode  sql.NullString
	)
	err := tx.QueryRowContext(ctx, `
		SELECT id::text, kind, state, background_job_id::text, report_id::text, error_code
		  FROM platform.evaluation_jobs
		 WHERE kind = 'model' AND model_profile_version_id = $1::uuid
		   AND state IN ('queued', 'running')
		 ORDER BY created_at DESC
		 LIMIT 1`, modelID,
	).Scan(&evaluation.ID, &evaluation.Kind, &evaluation.State,
		&evaluation.BackgroundJobID, &reportID, &errorCode)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("reading the running model evaluation: %w", err)
	}
	if reportID.Valid {
		value := reportID.String
		evaluation.ReportID = &value
	}
	if errorCode.Valid {
		value := errorCode.String
		evaluation.ErrorCode = &value
	}
	return &evaluation, nil
}

func replayIdempotent(
	ctx context.Context,
	tx *sql.Tx,
	ownerID, path, key, requestSHA string,
) (*EvaluationJob, error) {
	var (
		recordedSHA string
		responseRaw []byte
	)
	err := tx.QueryRowContext(ctx, `
		SELECT request_sha256, response_json
		  FROM platform.idempotency_keys
		 WHERE principal_id = $1::uuid AND method = 'POST' AND path = $2 AND key = $3
		 FOR UPDATE`, ownerID, path, key,
	).Scan(&recordedSHA, &responseRaw)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("reading the validation idempotency key: %w", err)
	}
	if recordedSHA != requestSHA {
		return nil, ErrIdempotencyKeyReuse
	}
	var evaluation EvaluationJob
	decoder := json.NewDecoder(bytes.NewReader(responseRaw))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&evaluation); err != nil {
		return nil, fmt.Errorf("decoding the recorded validation response: %w", err)
	}
	return &evaluation, nil
}
