package requests

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"strings"

	"orchara/platform/internal/audit"
	"orchara/platform/internal/background"
	"orchara/platform/internal/catalog"
	"orchara/platform/internal/compiler"
	"orchara/platform/internal/execution"
	"orchara/platform/internal/jcs"
	"orchara/platform/internal/objectstore"
	"orchara/platform/internal/uuid"
)

// Artifact refusal codes, shared with the T027 debug vocabulary so a client
// renders both submissions the same way.
const (
	codeArtifactNotFound   = "artifact_not_found"
	codeArtifactNotVisible = "artifact_not_visible"
	codeArtifactNotReady   = "artifact_not_ready"
)

// idempotencyTTL is how long a committed receipt replays. It is informational
// bookkeeping (0014): expiry never permits a second execution because the
// Request/Run rows are the authority.
const idempotencyTTL = 24 * 60 * 60

// Submit validates and executes one POST /api/v1/requests.
//
// Order is deliberate:
//  1. idempotency scope and canonical body (422/400) — cheap, no writes;
//  2. the conversation is live and owned by the caller (404) — nothing leaks;
//  3. the explicit version is published/runnable (404/409) — no Run is ever
//     created from a revoked version;
//  4. referenced attachments and handover sources are the caller's (422);
//  5. the message bytes and the confirmed handover summary are sealed (503 if the
//     object store is absent);
//  6. one transaction: lock the conversation, claim the receipt, write the
//     Request (+ handover record), then promote/dispatch it if it is the earliest
//     unfinished request and the slot is free.
//
// A replay of a committed key returns the stored 202 body without creating a
// second Request or Run; the same key with a different canonical body is 409.
func (s *Service) Submit(
	ctx context.Context,
	callerID string,
	scope IdempotencyScope,
	input *SubmitInput,
) (*SubmitOutcome, error) {
	caller, ok := requireUUID(callerID)
	if !ok {
		return nil, failure(401, "unauthenticated", "an authenticated human is required")
	}
	if input == nil || input.RequestSHA256 == "" {
		return nil, failure(422, "validation_failed", "a parsed request body is required")
	}
	if !uuid.Valid(scope.PrincipalID) || !strings.EqualFold(scope.PrincipalID, caller) ||
		scope.Method != "POST" || scope.Path != SubmitPath || strings.TrimSpace(scope.Key) == "" {
		return nil, failureWithDetails(400, "idempotency_key_required",
			"the Idempotency-Key header is required and must be one non-empty value",
			map[string]any{"header": "Idempotency-Key"})
	}
	if !uuid.Valid(input.ConversationID) {
		return nil, conversationNotFound()
	}

	alive, err := s.conversationAlive(ctx, caller, input.ConversationID)
	if err != nil {
		return nil, err
	}
	if !alive {
		return nil, conversationNotFound()
	}

	// The explicit version is checked before any bytes are sealed. The transaction
	// re-reads it under a share lock, so a concurrent revocation either wins (409)
	// or waits for this dispatch.
	if input.ApplicationVersionID != "" {
		if _, err := s.Catalog.ResolveRunnableVersion(ctx, caller, input.ApplicationVersionID); err != nil {
			return nil, versionRefusal(err)
		}
	}

	artifactIDs := append([]string(nil), input.ArtifactIDs...)
	if input.Handoff != nil {
		artifactIDs = append(artifactIDs, input.Handoff.ArtifactIDs...)
	}
	if err := s.checkArtifacts(ctx, caller, artifactIDs); err != nil {
		return nil, err
	}
	fromVersionID := ""
	if input.Handoff != nil {
		// The handover summary must be a non-empty user confirmation and every
		// source Run must be the caller's own Run in this conversation.
		if strings.TrimSpace(input.Handoff.Summary) == "" {
			return nil, located("handoff.summary",
				"the handover summary must say what crosses the application boundary")
		}
		fromVersionID, err = s.checkHandoffSources(ctx, caller, input.ConversationID, input.Handoff.SourceRunIDs)
		if err != nil {
			return nil, err
		}
	}

	messageKey, messageSHA, messageSize, err := s.sealBytes(ctx, []byte(input.Message))
	if err != nil {
		return nil, err
	}
	var (
		summaryKey  string
		summarySHA  string
		summarySize int64
	)
	if input.Handoff != nil {
		summaryKey, summarySHA, summarySize, err = s.sealBytes(ctx, []byte(input.Handoff.Summary))
		if err != nil {
			s.discardSealed(messageKey)
			return nil, err
		}
	}
	discard := func() {
		s.discardSealed(messageKey)
		if summaryKey != "" {
			s.discardSealed(summaryKey)
		}
	}

	outcome, err := s.createRequestTx(ctx, createParams{
		CallerID:    caller,
		Input:       input,
		Scope:       scope,
		MessageKey:  messageKey,
		MessageSHA:  messageSHA,
		MessageSize: messageSize,
		SummaryKey:  summaryKey,
		SummarySHA:  summarySHA,
		SummarySize: summarySize,
		FromVersion: fromVersionID,
	})
	if err != nil {
		discard()
		return nil, err
	}
	if outcome.Replayed {
		// A concurrent winner's request references its own sealed input; the bytes
		// this attempt sealed are unreferenced (mirrors the T027 replay rule).
		discard()
	}
	return outcome, nil
}

func versionRefusal(err error) error {
	switch {
	case errors.Is(err, catalog.ErrNotVisible):
		return failure(404, "resource_not_found", "that application version does not exist")
	case errors.Is(err, catalog.ErrVersionRevoked):
		return failure(409, "version_revoked",
			"that application version was revoked; a new Run may never bind it")
	case errors.Is(err, catalog.ErrVersionArchived):
		return failure(409, "version_archived",
			"the application of that version was archived; it cannot start a new Run")
	default:
		return err
	}
}

// checkArtifacts verifies every referenced artifact: it must exist, be ready, not
// tombstoned and belong to the caller. Problems are located at the member that
// names the artifact so the client can point at it.
func (s *Service) checkArtifacts(ctx context.Context, callerID string, ids []string) error {
	unique := make(map[string]bool, len(ids))
	ordered := make([]string, 0, len(ids))
	for _, id := range ids {
		if !unique[id] {
			unique[id] = true
			ordered = append(ordered, id)
		}
	}
	if len(ordered) == 0 {
		return nil
	}
	rows, err := s.DB.QueryContext(ctx, `
		SELECT id::text, owner_id::text, state, deleted_at IS NOT NULL,
		       (sha256 IS NOT NULL AND size_bytes IS NOT NULL) AS sealed
		  FROM platform.artifacts
		 WHERE id = ANY($1::uuid[])`, ordered)
	if err != nil {
		return fmt.Errorf("reading the referenced artifacts: %w", err)
	}
	defer rows.Close()
	type fact struct {
		ownerID string
		state   string
		deleted bool
		sealed  bool
	}
	found := make(map[string]fact, len(ordered))
	for rows.Next() {
		var (
			id string
			f  fact
		)
		if err := rows.Scan(&id, &f.ownerID, &f.state, &f.deleted, &f.sealed); err != nil {
			return fmt.Errorf("reading the referenced artifacts: %w", err)
		}
		found[id] = f
	}
	if err := rows.Err(); err != nil {
		return fmt.Errorf("reading the referenced artifacts: %w", err)
	}
	locatedErrors := make([]map[string]any, 0)
	for index, id := range ordered {
		f, present := found[id]
		pointer := fmt.Sprintf("/artifact_ids/%d", index)
		switch {
		case !present:
			locatedErrors = append(locatedErrors, map[string]any{
				"code": codeArtifactNotFound, "pointer": pointer,
				"message": "the referenced artifact does not exist",
			})
		case !strings.EqualFold(f.ownerID, callerID):
			locatedErrors = append(locatedErrors, map[string]any{
				"code": codeArtifactNotVisible, "pointer": pointer,
				"message": "the referenced artifact belongs to another principal",
			})
		case f.state != "ready" || f.deleted || !f.sealed:
			locatedErrors = append(locatedErrors, map[string]any{
				"code": codeArtifactNotReady, "pointer": pointer,
				"message": "the referenced artifact is not a ready, sealed artifact",
			})
		}
	}
	if len(locatedErrors) > 0 {
		return failureWithDetails(422, "validation_failed",
			"the request references artifacts that cannot be run inputs",
			map[string]any{"errors": locatedErrors})
	}
	return nil
}

// checkHandoffSources verifies the user's confirmed source Runs: every id must be
// a real Run of this conversation owned by the caller. It returns the version of
// the newest source Run (when it has one) for the handover record.
func (s *Service) checkHandoffSources(
	ctx context.Context, callerID, conversationID string, runIDs []string,
) (string, error) {
	type runFact struct {
		ownerID      string
		conversation string
		versionID    sql.NullString
		createdAt    string
	}
	newestVersion := ""
	var newest string
	for index, runID := range runIDs {
		var fact runFact
		err := s.DB.QueryRowContext(ctx, `
			SELECT owner_id::text, conversation_id::text, application_version_id::text,
			       created_at::text
			  FROM platform.runs
			 WHERE id = $1::uuid AND deleted_at IS NULL`,
			runID,
		).Scan(&fact.ownerID, &fact.conversation, &fact.versionID, &fact.createdAt)
		if errors.Is(err, sql.ErrNoRows) {
			return "", handoffSourceUnknown(index, "the source Run does not exist")
		}
		if err != nil {
			return "", fmt.Errorf("reading a handover source Run: %w", err)
		}
		if !strings.EqualFold(fact.ownerID, callerID) {
			return "", handoffSourceUnknown(index, "the source Run belongs to another principal")
		}
		if !strings.EqualFold(fact.conversation, conversationID) {
			return "", handoffSourceUnknown(index, "the source Run is not part of this conversation")
		}
		if fact.versionID.Valid && (newest == "" || fact.createdAt > newest) {
			newest = fact.createdAt
			newestVersion = fact.versionID.String
		}
	}
	return newestVersion, nil
}

func handoffSourceUnknown(index int, message string) *Error {
	return failureWithDetails(422, "handoff_source_unknown", message, map[string]any{
		"field": fmt.Sprintf("handoff.source_run_ids/%d", index),
	})
}

// sealBytes writes one sealed object and returns its key/hash/size after
// verifying the store's own report. A failed verification deletes the object
// instead of leaving an unverifiable input behind.
func (s *Service) sealBytes(ctx context.Context, data []byte) (string, string, int64, error) {
	if s.Objects == nil {
		return "", "", 0, failure(503, "object_store_unavailable",
			"the object store is not configured; the request input cannot be sealed")
	}
	key, err := objectstore.SealedKey()
	if err != nil {
		return "", "", 0, err
	}
	digest := jcs.SHA256Hex(data)
	object, err := s.Objects.PutSealed(key, bytes.NewReader(data), int64(len(data)))
	if err != nil {
		return "", "", 0, fmt.Errorf("sealing the request input: %w", err)
	}
	if object.SHA256 != digest || object.Size != int64(len(data)) {
		_ = s.Objects.Delete(key)
		return "", "", 0, errors.New("the sealed request input does not match its bytes")
	}
	return key, digest, int64(len(data)), nil
}

func (s *Service) discardSealed(key string) {
	if s.Objects != nil && key != "" {
		_ = s.Objects.Delete(key)
	}
}

type createParams struct {
	CallerID    string
	Input       *SubmitInput
	Scope       IdempotencyScope
	MessageKey  string
	MessageSHA  string
	MessageSize int64
	SummaryKey  string
	SummarySHA  string
	SummarySize int64
	FromVersion string
}

// createRequestTx writes the receipt, the sealed-input rows, the Request (and its
// handover record) and the first queue decision in one transaction.
func (s *Service) createRequestTx(ctx context.Context, params createParams) (*SubmitOutcome, error) {
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return nil, fmt.Errorf("starting the request transaction: %w", err)
	}
	defer func() { _ = tx.Rollback() }()

	// 1. The conversation row lock serialises every submit/cancel/delete for this
	// conversation: the slot decision below cannot race a tombstone or a second
	// submit.
	if _, err := s.conversationAliveTx(ctx, tx, params.CallerID, params.Input.ConversationID); err != nil {
		return nil, err
	}

	// 2. Claim the receipt. A concurrent duplicate blocks on the unique key, then
	// loses this insert and replays the committed body.
	result, err := tx.ExecContext(ctx, `
		INSERT INTO platform.idempotency_keys
		       (principal_id, method, path, key, request_sha256, expires_at)
		VALUES ($1::uuid, $2, $3, $4, $5, now() + make_interval(secs => $6))
		ON CONFLICT (principal_id, method, path, key) DO NOTHING`,
		params.Scope.PrincipalID, params.Scope.Method, params.Scope.Path, params.Scope.Key,
		params.Input.RequestSHA256, idempotencyTTL,
	)
	if err != nil {
		return nil, fmt.Errorf("claiming the idempotency receipt: %w", err)
	}
	claimed, err := result.RowsAffected()
	if err != nil {
		return nil, fmt.Errorf("reading the idempotency claim: %w", err)
	}
	if claimed == 0 {
		_ = tx.Rollback()
		return s.replay(ctx, params.Scope, params.Input.RequestSHA256)
	}

	requestID := uuid.NewString()
	inputArtifactID := uuid.NewString()
	if err := s.insertArtifactTx(ctx, tx, inputArtifactID, params.CallerID,
		params.MessageKey, PurposeRequestInput, "text/plain; charset=utf-8",
		params.MessageSHA, params.MessageSize); err != nil {
		return nil, err
	}
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO platform.requests
		       (id, conversation_id, owner_id, input_artifact_id, artifact_ids, state,
		        revision, requested_version_id, created_at, updated_at)
		VALUES ($1::uuid, $2::uuid, $3::uuid, $4::uuid, $5::uuid[], 'queued', 1,
		        $6, now(), now())`,
		requestID, params.Input.ConversationID, params.CallerID, inputArtifactID,
		uuidArrayLiteral(params.Input.ArtifactIDs),
		nullableUUID(params.Input.ApplicationVersionID),
	); err != nil {
		return nil, fmt.Errorf("creating the request: %w", err)
	}

	if params.Input.Handoff != nil {
		summaryArtifactID := uuid.NewString()
		if err := s.insertArtifactTx(ctx, tx, summaryArtifactID, params.CallerID,
			params.SummaryKey, PurposeHandoffSummary, "text/plain; charset=utf-8",
			params.SummarySHA, params.SummarySize); err != nil {
			return nil, err
		}
		if _, err := tx.ExecContext(ctx, `
			INSERT INTO platform.request_handoffs
			       (request_id, conversation_id, summary_artifact_id, source_run_ids,
			        artifact_ids, confirmed_by, confirmed_at, from_version_id)
			VALUES ($1::uuid, $2::uuid, $3::uuid, $4::uuid[], $5::uuid[], $6::uuid, now(), $7)`,
			requestID, params.Input.ConversationID, summaryArtifactID,
			uuidArrayLiteral(params.Input.Handoff.SourceRunIDs),
			uuidArrayLiteral(params.Input.Handoff.ArtifactIDs),
			params.CallerID, nullableUUID(params.FromVersion),
		); err != nil {
			return nil, fmt.Errorf("recording the confirmed handover: %w", err)
		}
	}

	metadata := map[string]any{"conversation_id": params.Input.ConversationID}
	if params.Input.ApplicationVersionID != "" {
		metadata["application_version_id"] = params.Input.ApplicationVersionID
	}
	metadata["handoff"] = params.Input.Handoff != nil
	if err := audit.Record(ctx, tx, &params.CallerID, "request.submitted", "request",
		&requestID, metadata); err != nil {
		return nil, err
	}

	// 3. The first queue decision: only the earliest unfinished request may leave
	// `queued`, and only while the slot is free.
	occupied, err := slotOccupiedTx(ctx, tx, params.Input.ConversationID)
	if err != nil {
		return nil, err
	}
	earliest, err := earliestUnfinishedTx(ctx, tx, params.Input.ConversationID)
	if err != nil {
		return nil, err
	}
	if !occupied && earliest != nil && earliest.ID == requestID {
		if params.Input.ApplicationVersionID != "" {
			if _, err := s.dispatchVersionTx(ctx, tx, params.CallerID, requestID,
				params.Input.ConversationID, params.Input.ApplicationVersionID); err != nil {
				return nil, err
			}
		} else if err := promoteQueuedTx(ctx, tx, requestID, params.CallerID); err != nil {
			return nil, err
		}
	}

	created, err := readRequestTx(ctx, tx, requestID)
	if err != nil {
		return nil, err
	}
	payload, err := json.Marshal(created)
	if err != nil {
		return nil, fmt.Errorf("encoding the request response: %w", err)
	}
	if _, err := tx.ExecContext(ctx, `
		UPDATE platform.idempotency_keys
		   SET response_code = 202, response_json = $5::jsonb
		 WHERE principal_id = $1::uuid AND method = $2 AND path = $3 AND key = $4`,
		params.Scope.PrincipalID, params.Scope.Method, params.Scope.Path, params.Scope.Key,
		string(payload),
	); err != nil {
		return nil, fmt.Errorf("storing the idempotent request response: %w", err)
	}
	if err := tx.Commit(); err != nil {
		return nil, fmt.Errorf("committing the request: %w", err)
	}
	return &SubmitOutcome{Request: created, Replayed: false}, nil
}

// replay returns the stored 202 body when the key and canonical body match. A
// deleted conversation refuses the replay too: the T069 tombstone makes every
// read refuse immediately, and a replay is a read of that request's facts.
func (s *Service) replay(
	ctx context.Context, scope IdempotencyScope, digest string,
) (*SubmitOutcome, error) {
	var (
		storedDigest string
		responseCode sql.NullInt64
		responseJSON sql.NullString
	)
	err := s.DB.QueryRowContext(ctx, `
		SELECT request_sha256, response_code, response_json::text
		  FROM platform.idempotency_keys
		 WHERE principal_id = $1::uuid AND method = $2 AND path = $3 AND key = $4`,
		scope.PrincipalID, scope.Method, scope.Path, scope.Key,
	).Scan(&storedDigest, &responseCode, &responseJSON)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, errors.New("the idempotency claim conflicted but no receipt is visible")
	}
	if err != nil {
		return nil, fmt.Errorf("reading the idempotency receipt: %w", err)
	}
	if storedDigest != digest {
		return nil, &IdempotencyConflictError{}
	}
	if !responseCode.Valid || responseCode.Int64 != 202 || !responseJSON.Valid {
		return nil, errors.New("the idempotency receipt has no stored 202 response")
	}
	var request Request
	if err := json.Unmarshal([]byte(responseJSON.String), &request); err != nil {
		return nil, fmt.Errorf("decoding the stored idempotent response: %w", err)
	}
	// The response body is exactly the contract Request (no conversation field);
	// read the authoritative conversation back and refuse the replay if it was
	// tombstoned since the receipt was written.
	var conversationID string
	err = s.DB.QueryRowContext(ctx, `
		SELECT conversation_id::text
		  FROM platform.requests
		 WHERE id = $1::uuid AND owner_id = $2::uuid`,
		request.ID, scope.PrincipalID,
	).Scan(&conversationID)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, requestNotFound()
	}
	if err != nil {
		return nil, fmt.Errorf("reading the replayed request: %w", err)
	}
	request.ConversationID = conversationID
	alive, err := s.conversationAlive(ctx, scope.PrincipalID, conversationID)
	if err != nil {
		return nil, err
	}
	if !alive {
		return nil, conversationNotFound()
	}
	return &SubmitOutcome{Request: &request, Replayed: true}, nil
}

func (s *Service) insertArtifactTx(
	ctx context.Context, tx *sql.Tx, artifactID, ownerID, objectKey, purpose,
	mediaType, sha256 string, size int64,
) error {
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO platform.artifacts
		       (id, owner_id, object_key, purpose, sha256, media_type, size_bytes, state, revision)
		VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7, 'ready', 1)`,
		artifactID, ownerID, objectKey, purpose, sha256, mediaType, size,
	); err != nil {
		return fmt.Errorf("registering the sealed request artifact: %w", err)
	}
	return nil
}

// promoteQueuedTx moves one queued request into `routing` and records its single
// durable route_request job. Used for requests without an explicit version.
func promoteQueuedTx(ctx context.Context, tx *sql.Tx, requestID, callerID string) error {
	var revision int64
	err := tx.QueryRowContext(ctx, `
		UPDATE platform.requests
		   SET state = 'routing', revision = revision + 1, updated_at = now()
		 WHERE id = $1::uuid AND state = 'queued'
		 RETURNING revision`, requestID).Scan(&revision)
	if errors.Is(err, sql.ErrNoRows) {
		return errors.New("the queued request disappeared before it could be routed")
	}
	if err != nil {
		return fmt.Errorf("promoting the queued request: %w", err)
	}
	if _, _, err := background.EnqueueTx(ctx, tx, background.EnqueueParams{
		Kind:             background.KindRouteRequest,
		TargetID:         requestID,
		OwnerID:          callerID,
		DedupKey:         requestID,
		ExpectedRevision: &revision,
	}); err != nil {
		return fmt.Errorf("enqueueing the route_request job: %w", err)
	}
	return nil
}

// dispatchVersionTx creates the frozen published execution and its one Run for an
// explicit version, then moves the Request to `dispatched`. The version is
// re-read inside the transaction under a share lock: a revocation that committed
// first is refused here, and one that starts later waits for this Run insert.
func (s *Service) dispatchVersionTx(
	ctx context.Context, tx *sql.Tx, callerID, requestID, conversationID, versionID string,
) (string, error) {
	version, err := s.Catalog.ResolveRunnableVersionTx(ctx, tx, callerID, versionID)
	if err != nil {
		return "", versionRefusal(err)
	}
	var manifest compiler.Manifest
	if err := json.Unmarshal(version.Manifest, &manifest); err != nil {
		return "", fmt.Errorf("decoding the published manifest: %w", err)
	}
	budget, currency, maxActiveMS, policyProblem := catalog.RunPolicy(manifest.Definition)
	if policyProblem != nil {
		return "", fmt.Errorf("the published version has no runnable policy: %s", policyProblem.Message)
	}

	// The sealed manifest artifact is the execution identity. A version whose
	// artifact is gone or whose hash no longer matches cannot start a Run.
	var (
		artifactState   string
		artifactSHA     sql.NullString
		artifactDeleted bool
	)
	err = tx.QueryRowContext(ctx, `
		SELECT state, sha256, deleted_at IS NOT NULL
		  FROM platform.artifacts
		 WHERE id = $1::uuid`, version.ManifestArtifactID,
	).Scan(&artifactState, &artifactSHA, &artifactDeleted)
	if errors.Is(err, sql.ErrNoRows) {
		return "", failure(409, "version_unavailable",
			"the published manifest artifact of that version no longer exists")
	}
	if err != nil {
		return "", fmt.Errorf("reading the published manifest artifact: %w", err)
	}
	if artifactState != "ready" || artifactDeleted || !artifactSHA.Valid ||
		artifactSHA.String != version.ManifestSHA256 {
		return "", failure(409, "version_unavailable",
			"the published manifest artifact is not ready or does not match the version hash")
	}

	snapshotID := uuid.NewString()
	runID := uuid.NewString()
	jobID := uuid.NewString()
	checkpointThreadID := uuid.NewString()
	var inputArtifactID string
	if err := tx.QueryRowContext(ctx,
		`SELECT input_artifact_id::text FROM platform.requests WHERE id = $1::uuid`,
		requestID).Scan(&inputArtifactID); err != nil {
		return "", fmt.Errorf("reading the request input artifact: %w", err)
	}
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO platform.execution_snapshots
		       (id, kind, application_id, application_version_id, draft_revision, owner_id,
		        manifest, manifest_sha256, manifest_artifact_id, compiler_version,
		        worker_image_digest, state_schema_version)
		VALUES ($1::uuid, 'published', $2::uuid, $3::uuid, NULL, $4::uuid,
		        $5::jsonb, $6, $7::uuid, $8, $9, $10)`,
		snapshotID, version.ApplicationID, version.ID, callerID, string(version.Manifest),
		version.ManifestSHA256, version.ManifestArtifactID, version.CompilerVersion,
		version.WorkerImageDigest, version.StateSchemaVersion,
	); err != nil {
		return "", fmt.Errorf("creating the published execution snapshot: %w", err)
	}

	// The request's sealed message is the Run's task input. Its attachments (and
	// the confirmed handover summary) are linked below, never the conversation's
	// history or the previous application's memory.
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO platform.runs
		       (id, request_id, conversation_id, parent_run_id, parent_invocation_key,
		        owner_id, kind, execution_snapshot_id, application_version_id,
		        manifest_sha256, input_artifact_id, output_artifact_id,
		        checkpoint_thread_id, state, revision, next_event_seq,
		        budget_limit, currency, phase, active_elapsed_ms, max_active_ms)
		VALUES ($1::uuid, $2::uuid, $3::uuid, NULL, NULL,
		        $4::uuid, 'published', $5::uuid, $6::uuid,
		        $7, $8::uuid, NULL,
		        $9::uuid, 'queued', 1, 1, $10::numeric, $11, 'idle', 0, $12)`,
		runID, requestID, conversationID, callerID, snapshotID, version.ID,
		version.ManifestSHA256, inputArtifactID, checkpointThreadID, budget, currency,
		maxActiveMS,
	); err != nil {
		return "", fmt.Errorf("creating the published run: %w", err)
	}
	if _, err := execution.AppendRunStatusTx(ctx, tx, runID); err != nil {
		return "", fmt.Errorf("recording the queued run event: %w", err)
	}
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO platform.jobs (id, run_id, kind, state, available_at)
		VALUES ($1::uuid, $2::uuid, 'start', 'ready', now())`,
		jobID, runID,
	); err != nil {
		return "", fmt.Errorf("creating the published start job: %w", err)
	}

	linked, err := runInputArtifactIDsTx(ctx, tx, requestID, inputArtifactID)
	if err != nil {
		return "", err
	}
	for _, artifactID := range linked {
		if _, err := tx.ExecContext(ctx, `
			INSERT INTO platform.run_artifacts (run_id, artifact_id, attempt_token, relation)
			VALUES ($1::uuid, $2::uuid, NULL, 'input')`,
			runID, artifactID,
		); err != nil {
			return "", fmt.Errorf("linking the published run input: %w", err)
		}
	}

	if err := dispatchRequestTx(ctx, tx, requestID); err != nil {
		return "", err
	}
	actor := callerID
	metadata := map[string]any{
		"run_id":                 runID,
		"application_version_id": version.ID,
		"version_no":             version.VersionNo,
	}
	if err := audit.Record(ctx, tx, &actor, "request.dispatched", "request",
		&requestID, metadata); err != nil {
		return "", err
	}
	return runID, nil
}

// runInputArtifactIDsTx is the exact input closure of one published Run: the
// sealed message, the caller's submitted attachments and the confirmed handover
// summary. It never reads another request's/Run's artifacts.
func runInputArtifactIDsTx(
	ctx context.Context, tx *sql.Tx, requestID, messageArtifactID string,
) ([]string, error) {
	rows, err := tx.QueryContext(ctx, `
		SELECT artifact_id::text
		  FROM (
		    SELECT $2::uuid AS artifact_id
		    UNION
		    SELECT unnest(artifact_ids) FROM platform.requests WHERE id = $1::uuid
		    UNION
		    SELECT summary_artifact_id FROM platform.request_handoffs WHERE request_id = $1::uuid
		  ) AS inputs
		 ORDER BY artifact_id::text`,
		requestID, messageArtifactID,
	)
	if err != nil {
		return nil, fmt.Errorf("reading the run input closure: %w", err)
	}
	defer rows.Close()
	ids := make([]string, 0, 8)
	for rows.Next() {
		var id string
		if err := rows.Scan(&id); err != nil {
			return nil, fmt.Errorf("reading the run input closure: %w", err)
		}
		ids = append(ids, id)
	}
	return ids, rows.Err()
}

// dispatchRequestTx is the revision-CAS transition queued/routing -> dispatched.
func dispatchRequestTx(ctx context.Context, tx *sql.Tx, requestID string) error {
	result, err := tx.ExecContext(ctx, `
		UPDATE platform.requests
		   SET state = 'dispatched', revision = revision + 1, updated_at = now()
		 WHERE id = $1::uuid AND state IN ('queued', 'routing')`, requestID)
	if err != nil {
		return fmt.Errorf("dispatching the request: %w", err)
	}
	affected, err := result.RowsAffected()
	if err != nil {
		return fmt.Errorf("reading the dispatch transition: %w", err)
	}
	if affected != 1 {
		return failure(409, "state_conflict",
			"the request is no longer in a state that can be dispatched")
	}
	return nil
}

func nullableUUID(value string) any {
	if strings.TrimSpace(value) == "" {
		return nil
	}
	return value
}

// uuidArrayLiteral renders a Go string slice as a PostgreSQL uuid[] literal. The
// values were validated as UUIDs before they reach this point, so no untrusted
// text is interpolated; an empty slice is an empty array, never NULL.
func uuidArrayLiteral(values []string) string {
	quoted := make([]string, 0, len(values))
	for _, value := range values {
		quoted = append(quoted, `"`+strings.ToLower(value)+`"`)
	}
	return "{" + strings.Join(quoted, ",") + "}"
}
