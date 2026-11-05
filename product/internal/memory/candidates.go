package memory

// F09/T054: personal memory candidates and the decision that confirms one.
//
//	propose  (internal mTLS, worker only) run + live attempt token -> one pending
//	         candidate per (run, logical_key), body sealed, scope derived from the
//	         run's frozen snapshot. A retried proposal returns the same candidate;
//	         different text under the same logical key is a conflict.
//	list     (public, owner only) newest first, keyset cursor, optional
//	         application scope. A pending row past its deadline is transitioned to
//	         expired by the read, so expiry is a server fact rather than something
//	         the page has to interpret.
//	decide   (public, owner only) exactly one memory for a confirm, idempotent by
//	         candidate state *and* by Idempotency-Key receipt; reject/expiry create
//	         nothing and a later confirm is refused with a located conflict.

import (
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"

	"orchara/platform/internal/jcs"
	"orchara/platform/internal/uuid"
)

// ProposeRequest is the contract's proposeMemoryCandidate body. The scope is not
// part of it: the server derives owner and application from the run.
type ProposeRequest struct {
	RunID        string
	AttemptToken string
	LogicalKey   string
	Text         string
}

// DecisionRequest is the contract's decideMemoryCandidate call.
type DecisionRequest struct {
	CandidateID    string
	CallerID       string
	Decision       string
	IdempotencyKey string
	// Path is the concrete request path the receipt is scoped by.
	Path string
}

// runScope is what a proposal is allowed to learn from a run.
type runScope struct {
	OwnerID         string
	ApplicationID   string
	State           string
	LiveAttempt     bool
	CancelRequested bool
	OwnerDisabled   bool
	OwnerIsHuman    bool
}

// loadRunScope reads the run and proves the presented token is its live attempt.
// It returns a typed refusal instead of a run the caller is not allowed to use.
func (s *Service) loadRunScope(ctx context.Context, runID, attemptToken string) (*runScope, error) {
	if !uuid.Valid(strings.TrimSpace(runID)) {
		return nil, failure(404, CodeResourceNotFound, "that run does not exist")
	}
	if !uuid.Valid(strings.TrimSpace(attemptToken)) {
		return nil, failure(403, CodeStaleAttempt,
			"the attempt token is not a live lease of this run")
	}
	scope := &runScope{}
	err := s.DB.QueryRowContext(ctx, `
		SELECT r.owner_id::text,
		       s.application_id::text,
		       r.state,
		       r.cancel_requested_at IS NOT NULL,
		       p.disabled_at IS NOT NULL,
		       EXISTS (SELECT 1 FROM platform.users AS u
		                WHERE u.principal_id = r.owner_id),
		       EXISTS (
		           SELECT 1
		             FROM platform.run_attempts AS at
		             JOIN platform.jobs AS j ON j.id = at.job_id AND j.run_id = at.run_id
		            WHERE at.token = $2::uuid
		              AND at.run_id = r.id
		              AND at.exited_at IS NULL
		              AND at.revoked_at IS NULL
		              AND at.lease_deadline > now()
		              AND j.current_attempt_token = at.token
		       )
		  FROM platform.runs AS r
		  JOIN platform.execution_snapshots AS s ON s.id = r.execution_snapshot_id
		  JOIN platform.principals AS p ON p.id = r.owner_id
		 WHERE r.id = $1::uuid`, strings.TrimSpace(runID), strings.TrimSpace(attemptToken),
	).Scan(
		&scope.OwnerID, &scope.ApplicationID, &scope.State,
		&scope.CancelRequested, &scope.OwnerDisabled, &scope.OwnerIsHuman, &scope.LiveAttempt,
	)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, failure(404, CodeResourceNotFound, "that run does not exist")
	}
	if err != nil {
		return nil, fmt.Errorf("reading the run: %w", err)
	}
	if !scope.LiveAttempt {
		return nil, failure(403, CodeStaleAttempt,
			"the attempt token is not a live lease of this run")
	}
	if !scope.OwnerIsHuman {
		// The structural personal-data boundary: a service-owned run cannot mint
		// personal memory for its responsible user.
		return nil, failure(403, CodePersonalScope,
			"personal memory belongs to human users; a service account cannot propose it")
	}
	if scope.OwnerDisabled {
		return nil, failure(403, CodePermissionRevoked, "the run owner was disabled")
	}
	if scope.CancelRequested {
		return nil, failure(409, CodeRunCancelled,
			"the run was cancelled; no memory candidate may be proposed")
	}
	if scope.State != "running" {
		return nil, failure(409, CodeRunNotRunning, "the run is not running")
	}
	return scope, nil
}

// Propose creates (or returns) the one candidate for this run and logical key.
//
// The body is sealed before the candidate row is committed and the object is
// removed if the transaction rolls back, so a committed candidate always has
// readable bytes.
func (s *Service) Propose(ctx context.Context, request ProposeRequest) (*Candidate, error) {
	text, err := validateText("text", request.Text)
	if err != nil {
		return nil, err
	}
	logicalKey, err := validateLogicalKey(request.LogicalKey)
	if err != nil {
		return nil, err
	}
	scope, err := s.loadRunScope(ctx, request.RunID, request.AttemptToken)
	if err != nil {
		return nil, err
	}

	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return nil, fmt.Errorf("starting the proposal transaction: %w", err)
	}
	defer func() { _ = tx.Rollback() }()

	existing, err := candidateByLogicalKeyTx(
		ctx, tx, strings.TrimSpace(request.RunID), logicalKey, textDigest(text),
	)
	if err != nil {
		return nil, err
	}
	if existing != nil {
		existing.Text = text // equal digests prove the bytes are identical
		if err := tx.Commit(); err != nil {
			return nil, fmt.Errorf("committing the replayed proposal: %w", err)
		}
		return existing, nil
	}

	artifactID, sealedKey, err := s.sealText(
		ctx, tx, scope.OwnerID, PurposeCandidate, text,
	)
	if err != nil {
		return nil, err
	}
	candidate := &Candidate{
		ID:             uuid.NewString(),
		OwnerID:        scope.OwnerID,
		ApplicationID:  scope.ApplicationID,
		BodyArtifactID: artifactID,
		SourceRunID:    strings.TrimSpace(request.RunID),
		LogicalKey:     logicalKey,
		State:          "pending",
		Text:           text,
	}
	err = tx.QueryRowContext(ctx, `
		INSERT INTO platform.memory_candidates
		       (id, owner_id, application_id, body_artifact_id, source_run_id,
		        logical_key, state, expires_at)
		VALUES ($1::uuid, $2::uuid, $3::uuid, $4::uuid, $5::uuid, $6, 'pending',
		        now() + $7::interval)
		RETURNING expires_at, created_at`,
		candidate.ID, candidate.OwnerID, candidate.ApplicationID, candidate.BodyArtifactID,
		candidate.SourceRunID, candidate.LogicalKey, intervalOf(s.candidateTTL()),
	).Scan(&candidate.ExpiresAt, &candidate.CreatedAt)
	if err != nil {
		s.discardObject(sealedKey)
		if isUniqueViolation(err) {
			// A concurrent proposal committed the same (run, logical_key) while this
			// transaction was reading. The insert blocks on the unique index until
			// the winner commits, so the winning row is visible now; return it when
			// the text matches and report the same conflict otherwise.
			_ = tx.Rollback()
			return s.candidateByLogicalKey(ctx, candidate.SourceRunID, logicalKey, textDigest(text))
		}
		return nil, fmt.Errorf("writing the memory candidate: %w", err)
	}
	if err := tx.Commit(); err != nil {
		s.discardObject(sealedKey)
		return nil, fmt.Errorf("committing the memory candidate: %w", err)
	}
	return candidate, nil
}

func (s *Service) candidateTTL() time.Duration {
	if s.CandidateTTL <= 0 {
		return DefaultCandidateTTL
	}
	return s.CandidateTTL
}

// intervalOf renders a duration as a PostgreSQL interval literal.
func intervalOf(value time.Duration) string {
	return fmt.Sprintf("%d milliseconds", value.Milliseconds())
}

// candidateByLogicalKeyTx reads the one candidate for (run, logical_key).
//
// A retried proposal with the same text is the same intention and returns the
// stored row; the same key with different text is a conflict, so a worker bug can
// never silently rewrite what the human is about to confirm.
func candidateByLogicalKeyTx(
	ctx context.Context, tx *sql.Tx, runID, logicalKey, digest string,
) (*Candidate, error) {
	return scanCandidate(tx.QueryRowContext(ctx, candidateByKeyQuery+" FOR UPDATE OF c",
		runID, logicalKey), digest)
}

// candidateByLogicalKey is the same read outside a transaction. It exists for the
// unique-violation recovery: the insert that lost the race blocks until the
// winner commits, so a plain read can then return the winning row.
func (s *Service) candidateByLogicalKey(
	ctx context.Context, runID, logicalKey, digest string,
) (*Candidate, error) {
	candidate, err := scanCandidate(s.DB.QueryRowContext(ctx, candidateByKeyQuery, runID, logicalKey), digest)
	if err != nil {
		return nil, err
	}
	if candidate == nil {
		return nil, errors.New("the proposal insert conflicted but no candidate is visible")
	}
	return candidate, nil
}

const candidateByKeyQuery = `
		SELECT c.id::text, c.owner_id::text, c.application_id::text,
		       c.body_artifact_id::text, c.source_run_id::text, c.logical_key,
		       c.state, c.expires_at, c.created_at, c.decided_at, c.decided_by::text,
		       a.sha256
		  FROM platform.memory_candidates AS c
		  JOIN platform.artifacts AS a ON a.id = c.body_artifact_id
		 WHERE c.source_run_id = $1::uuid AND c.logical_key = $2
		   AND a.deleted_at IS NULL AND a.state = 'ready'`

type rowScanner interface {
	Scan(dest ...any) error
}

func scanCandidate(row rowScanner, digest string) (*Candidate, error) {
	var (
		candidate Candidate
		decidedBy sql.NullString
		sha       sql.NullString
	)
	err := row.Scan(
		&candidate.ID, &candidate.OwnerID, &candidate.ApplicationID,
		&candidate.BodyArtifactID, &candidate.SourceRunID, &candidate.LogicalKey,
		&candidate.State, &candidate.ExpiresAt, &candidate.CreatedAt,
		&candidate.DecidedAt, &decidedBy, &sha,
	)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("reading the existing candidate: %w", err)
	}
	if decidedBy.Valid {
		candidate.DecidedBy = decidedBy.String
	}
	if strings.ToLower(strings.TrimSpace(sha.String)) != digest {
		return nil, fieldFailure(409, CodeIdempotencyConflict,
			"this logical_key already names a candidate with different text", "logical_key")
	}
	return &candidate, nil
}

// isUniqueViolation reports the one database state the proposal path treats as a
// race it can resolve (23505), never as an internal error.
func isUniqueViolation(err error) bool {
	type sqlState interface{ SQLState() string }
	var state sqlState
	if errors.As(err, &state) {
		return state.SQLState() == "23505"
	}
	return false
}

// ListCandidates returns the owner's candidates, newest first.
//
// The lazy expired transition runs first, so the stored state and the API agree;
// the projection still re-checks the deadline in SQL for the row that expires
// between the two statements.
func (s *Service) ListCandidates(
	ctx context.Context, ownerID string, limit int, cursor, applicationID string,
) ([]Candidate, *string, error) {
	if !uuid.Valid(strings.TrimSpace(ownerID)) {
		return nil, nil, failure(401, "unauthenticated", "an authenticated human is required")
	}
	limit = normaliseLimit(limit)
	scope, position, err := decodeListInput(applicationID, cursor)
	if err != nil {
		return nil, nil, err
	}
	if _, err := s.DB.ExecContext(ctx, `
		UPDATE platform.memory_candidates
		   SET state = 'expired', decided_at = now()
		 WHERE owner_id = $1::uuid AND state = 'pending' AND expires_at <= now()`,
		ownerID); err != nil {
		return nil, nil, fmt.Errorf("expiring the owner's candidates: %w", err)
	}

	rows, err := s.DB.QueryContext(ctx, `
		SELECT c.id::text, c.owner_id::text, c.application_id::text,
		       c.body_artifact_id::text, c.source_run_id::text, c.logical_key,
		       CASE WHEN c.state = 'pending' AND c.expires_at <= now()
		            THEN 'expired' ELSE c.state END,
		       c.expires_at, c.created_at, c.decided_at, c.decided_by::text,
		       a.object_key, a.sha256
		  FROM platform.memory_candidates AS c
		  JOIN platform.artifacts AS a ON a.id = c.body_artifact_id
		 WHERE c.owner_id = $1::uuid
		   AND a.deleted_at IS NULL AND a.state = 'ready'
		   AND ($2::uuid IS NULL OR c.application_id = $2::uuid)
		   AND (
		        $3::timestamptz IS NULL
		        OR (c.created_at, c.id) < ($3::timestamptz, $4::uuid)
		       )
		 ORDER BY c.created_at DESC, c.id DESC
		 LIMIT $5`, ownerID, nullableUUID(scope), nullableTime(position), cursorID(position), limit+1)
	if err != nil {
		return nil, nil, fmt.Errorf("listing memory candidates: %w", err)
	}
	defer func() { _ = rows.Close() }()

	items := make([]Candidate, 0, limit+1)
	keys := make([]string, 0, limit+1)
	digests := make([]string, 0, limit+1)
	for rows.Next() {
		var (
			candidate Candidate
			decidedBy sql.NullString
			objectKey string
			sha       sql.NullString
		)
		if err := rows.Scan(
			&candidate.ID, &candidate.OwnerID, &candidate.ApplicationID,
			&candidate.BodyArtifactID, &candidate.SourceRunID, &candidate.LogicalKey,
			&candidate.State, &candidate.ExpiresAt, &candidate.CreatedAt,
			&candidate.DecidedAt, &decidedBy, &objectKey, &sha,
		); err != nil {
			return nil, nil, fmt.Errorf("scanning the candidate list: %w", err)
		}
		if decidedBy.Valid {
			candidate.DecidedBy = decidedBy.String
		}
		items = append(items, candidate)
		keys = append(keys, objectKey)
		digests = append(digests, sha.String)
	}
	if err := rows.Err(); err != nil {
		return nil, nil, fmt.Errorf("listing memory candidates: %w", err)
	}
	hasMore := len(items) > limit
	if hasMore {
		items = items[:limit]
		keys = keys[:limit]
		digests = digests[:limit]
	}
	for index := range items {
		text, err := s.readBody(keys[index], digests[index])
		if err != nil {
			return nil, nil, err
		}
		items[index].Text = text
	}
	var next *string
	if hasMore && len(items) > 0 {
		last := items[len(items)-1]
		next = nextCursor(last.CreatedAt, last.ID, true)
	}
	return items, next, nil
}

// Decide applies one confirm/reject decision under a row lock.
//
// The receipt claim comes first so a concurrent duplicate blocks on the unique
// key and then replays the committed body; the row lock then makes the state
// machine single-writer. Any refusal rolls the receipt back, so a failed decision
// never suppresses a legitimate retry.
func (s *Service) Decide(ctx context.Context, request DecisionRequest) (*Decision, error) {
	candidateID := strings.TrimSpace(request.CandidateID)
	if !uuid.Valid(candidateID) {
		return nil, failure(404, CodeResourceNotFound, "that memory candidate does not exist")
	}
	if !uuid.Valid(strings.TrimSpace(request.CallerID)) {
		return nil, failure(401, "unauthenticated", "an authenticated human is required")
	}
	decision, err := normaliseDecision(request.Decision)
	if err != nil {
		return nil, err
	}
	key := strings.TrimSpace(request.IdempotencyKey)
	if key == "" || len(key) > 200 {
		return nil, fieldFailure(422, "validation_failed",
			"Idempotency-Key is required", "Idempotency-Key")
	}
	digest, err := decisionDigest(candidateID, decision)
	if err != nil {
		return nil, err
	}
	path := strings.TrimSpace(request.Path)
	if path == "" {
		return nil, errors.New("the decision receipt needs its request path")
	}

	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return nil, fmt.Errorf("starting the decision transaction: %w", err)
	}
	defer func() { _ = tx.Rollback() }()

	claimed, err := claimReceipt(ctx, tx, request.CallerID, path, key, digest)
	if err != nil {
		return nil, err
	}
	if !claimed {
		_ = tx.Rollback()
		replayed, err := s.replayReceipt(ctx, request.CallerID, path, key, digest)
		if err != nil {
			return nil, err
		}
		return replayed, nil
	}

	candidate, expired, err := lockOwnedCandidate(ctx, tx, candidateID, request.CallerID)
	if err != nil {
		return nil, err
	}
	if expired && candidate.State == "pending" {
		// Persist the lazy transition in its own committed statement: the receipt
		// of this failed call must not survive.
		_ = tx.Rollback()
		if _, err := s.DB.ExecContext(ctx, `
			UPDATE platform.memory_candidates
			   SET state = 'expired', decided_at = now()
			 WHERE id = $1::uuid AND owner_id = $2::uuid AND state = 'pending'`,
			candidateID, request.CallerID); err != nil {
			return nil, fmt.Errorf("expiring the candidate: %w", err)
		}
		return nil, failure(409, CodeCandidateExpired, "that memory candidate has expired")
	}

	switch candidate.State {
	case "expired":
		return nil, failure(409, CodeCandidateExpired, "that memory candidate has expired")
	case "confirmed":
		if decision == "reject" {
			return nil, failure(409, CodeCandidateConfirmed,
				"that memory candidate is already confirmed and has a memory")
		}
		memoryID, err := memoryIDForCandidateTx(ctx, tx, candidateID)
		if err != nil {
			return nil, err
		}
		result := &Decision{CandidateID: candidateID, State: "confirmed", MemoryID: memoryID}
		if err := completeDecisionTx(ctx, tx, request, digest, result); err != nil {
			return nil, err
		}
		if err := tx.Commit(); err != nil {
			return nil, fmt.Errorf("committing the replayed decision: %w", err)
		}
		return result, nil
	case "rejected":
		if decision == "confirm" {
			return nil, failure(409, CodeCandidateRejected,
				"that memory candidate was rejected and cannot be confirmed")
		}
		result := &Decision{CandidateID: candidateID, State: "rejected"}
		if err := completeDecisionTx(ctx, tx, request, digest, result); err != nil {
			return nil, err
		}
		if err := tx.Commit(); err != nil {
			return nil, fmt.Errorf("committing the replayed decision: %w", err)
		}
		return result, nil
	case "pending":
		// The two outcomes are written below; the switch above only replays.
	default:
		return nil, fmt.Errorf("unknown memory candidate state %q", candidate.State)
	}

	nowState := "rejected"
	var memoryID *string
	if decision == "confirm" {
		created, err := s.insertMemoryTx(ctx, tx, request.CallerID, candidate)
		if err != nil {
			return nil, err
		}
		memoryID = &created
		nowState = "confirmed"
		if err := bumpMemoryEpoch(ctx, tx, candidate.OwnerID, candidate.ApplicationID); err != nil {
			return nil, err
		}
	}
	if _, err := tx.ExecContext(ctx, `
		UPDATE platform.memory_candidates
		   SET state = $3, decided_at = now(), decided_by = $2::uuid, decision_key = $4
		 WHERE id = $1::uuid AND state = 'pending'`,
		candidateID, request.CallerID, nowState, key,
	); err != nil {
		return nil, fmt.Errorf("recording the decision: %w", err)
	}
	result := &Decision{CandidateID: candidateID, State: nowState, MemoryID: memoryID}
	if err := completeDecisionTx(ctx, tx, request, digest, result); err != nil {
		return nil, err
	}
	if err := tx.Commit(); err != nil {
		return nil, fmt.Errorf("committing the decision: %w", err)
	}
	return result, nil
}

// insertMemoryTx creates the one memory a confirm is allowed to create. The
// UNIQUE(candidate_id) constraint makes a concurrent/duplicate insert return the
// existing row instead of a second one. The memory reuses the candidate's sealed
// body: the bytes are already immutably addressed by SHA-256, and copying them
// would duplicate personal data for no new guarantee.
func (s *Service) insertMemoryTx(
	ctx context.Context, tx *sql.Tx, confirmerID string, candidate *lockedCandidate,
) (string, error) {
	// The confirm re-reads the sealed body before it commits the memory: a memory
	// row must never be created pointing at missing or mismatched bytes. The read
	// is small (the text is <= 4000 characters) and it fails closed with 503,
	// leaving the candidate pending so the human can retry once storage is healthy.
	var (
		objectKey string
		sha       sql.NullString
	)
	err := tx.QueryRowContext(ctx, `
		SELECT object_key, sha256 FROM platform.artifacts
		 WHERE id = $1::uuid AND deleted_at IS NULL AND state = 'ready'`,
		candidate.BodyArtifactID).Scan(&objectKey, &sha)
	if errors.Is(err, sql.ErrNoRows) {
		// The body was tombstoned between the proposal and the confirmation: the
		// content must not be resurrected into a new memory.
		return "", failure(409, "memory_candidate_unavailable",
			"the candidate body is no longer available")
	}
	if err != nil {
		return "", fmt.Errorf("reading the candidate body artifact: %w", err)
	}
	if _, err := s.readBody(objectKey, sha.String); err != nil {
		return "", err
	}

	memoryID := uuid.NewString()
	var inserted string
	err = tx.QueryRowContext(ctx, `
		INSERT INTO platform.memories
		       (id, owner_id, application_id, body_artifact_id, candidate_id,
		        revision, confirmed_at, confirmed_by)
		VALUES ($1::uuid, $2::uuid, $3::uuid, $4::uuid, $5::uuid, 1, now(), $6::uuid)
		ON CONFLICT (candidate_id) DO NOTHING
		RETURNING id::text`,
		memoryID, candidate.OwnerID, candidate.ApplicationID, candidate.BodyArtifactID,
		candidate.ID, confirmerID,
	).Scan(&inserted)
	if err == nil {
		return inserted, nil
	}
	if !errors.Is(err, sql.ErrNoRows) {
		return "", fmt.Errorf("creating the memory: %w", err)
	}
	existing, lookupErr := memoryIDForCandidateTx(ctx, tx, candidate.ID)
	if lookupErr != nil {
		return "", lookupErr
	}
	if existing == nil {
		return "", errors.New("the candidate is unique but its memory is not visible")
	}
	return *existing, nil
}

func memoryIDForCandidateTx(ctx context.Context, tx *sql.Tx, candidateID string) (*string, error) {
	var id string
	err := tx.QueryRowContext(ctx,
		`SELECT id::text FROM platform.memories WHERE candidate_id = $1::uuid`,
		candidateID).Scan(&id)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, errors.New("the confirmed candidate has no memory row")
	}
	if err != nil {
		return nil, fmt.Errorf("reading the confirmed memory: %w", err)
	}
	return &id, nil
}

// lockedCandidate is the row a decision is allowed to read.
type lockedCandidate struct {
	ID             string
	OwnerID        string
	ApplicationID  string
	BodyArtifactID string
	State          string
}

func lockOwnedCandidate(
	ctx context.Context, tx *sql.Tx, candidateID, ownerID string,
) (*lockedCandidate, bool, error) {
	var (
		candidate lockedCandidate
		expired   bool
	)
	err := tx.QueryRowContext(ctx, `
		SELECT id::text, owner_id::text, application_id::text, body_artifact_id::text,
		       state, expires_at <= now()
		  FROM platform.memory_candidates
		 WHERE id = $1::uuid AND owner_id = $2::uuid
		 FOR UPDATE`, candidateID, ownerID,
	).Scan(
		&candidate.ID, &candidate.OwnerID, &candidate.ApplicationID,
		&candidate.BodyArtifactID, &candidate.State, &expired,
	)
	if errors.Is(err, sql.ErrNoRows) {
		// Unknown, another user's and an administrator's view of someone else's
		// candidate are one answer, so the endpoint cannot enumerate ids.
		return nil, false, failure(404, CodeResourceNotFound,
			"that memory candidate does not exist")
	}
	if err != nil {
		return nil, false, fmt.Errorf("reading the memory candidate: %w", err)
	}
	return &candidate, expired, nil
}

func normaliseDecision(raw string) (string, error) {
	switch strings.ToLower(strings.TrimSpace(raw)) {
	case "confirm":
		return "confirm", nil
	case "reject":
		return "reject", nil
	default:
		return "", fieldFailure(422, "validation_failed",
			"decision must be confirm or reject", "decision")
	}
}

// decisionDigest is the canonical request digest stored in the receipt.
func decisionDigest(candidateID, decision string) (string, error) {
	raw, err := json.Marshal(map[string]any{
		"candidate_id": candidateID,
		"decision":     decision,
	})
	if err != nil {
		return "", fmt.Errorf("encoding the decision digest: %w", err)
	}
	canonical, err := jcs.CanonicalizeBytes(raw)
	if err != nil {
		return "", fmt.Errorf("canonicalising the decision digest: %w", err)
	}
	sum := sha256.Sum256(canonical)
	return hex.EncodeToString(sum[:]), nil
}

func claimReceipt(
	ctx context.Context, tx *sql.Tx, principalID, path, key, digest string,
) (bool, error) {
	result, err := tx.ExecContext(ctx, `
		INSERT INTO platform.idempotency_keys
		       (principal_id, method, path, key, request_sha256, expires_at)
		VALUES ($1::uuid, $2, $3, $4, $5, now() + $6::interval)
		ON CONFLICT (principal_id, method, path, key) DO NOTHING`,
		principalID, decisionMethod, path, key, digest, intervalOf(IdempotencyTTL),
	)
	if err != nil {
		return false, fmt.Errorf("claiming the decision receipt: %w", err)
	}
	affected, err := result.RowsAffected()
	if err != nil {
		return false, fmt.Errorf("reading the decision claim: %w", err)
	}
	return affected == 1, nil
}

// replayReceipt returns the committed decision body for the same key and digest.
func (s *Service) replayReceipt(
	ctx context.Context, principalID, path, key, digest string,
) (*Decision, error) {
	var (
		recordDigest string
		response     sql.NullString
	)
	err := s.DB.QueryRowContext(ctx, `
		SELECT request_sha256, response_json::text
		  FROM platform.idempotency_keys
		 WHERE principal_id = $1::uuid AND method = $2 AND path = $3 AND key = $4`,
		principalID, decisionMethod, path, key,
	).Scan(&recordDigest, &response)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, errors.New("the decision receipt disappeared after its claim")
	}
	if err != nil {
		return nil, fmt.Errorf("reading the decision receipt: %w", err)
	}
	if recordDigest != digest {
		return nil, failure(409, CodeIdempotencyConflict,
			"this Idempotency-Key was already used for a different decision")
	}
	if !response.Valid {
		return nil, errors.New("the decision receipt has no stored response")
	}
	var stored struct {
		CandidateID string  `json:"candidate_id"`
		State       string  `json:"state"`
		MemoryID    *string `json:"memory_id"`
	}
	if err := json.Unmarshal([]byte(response.String), &stored); err != nil {
		return nil, fmt.Errorf("decoding the stored decision: %w", err)
	}
	return &Decision{CandidateID: stored.CandidateID, State: stored.State, MemoryID: stored.MemoryID}, nil
}

// completeDecisionTx stores the response body in the claimed receipt.
func completeDecisionTx(
	ctx context.Context, tx *sql.Tx, request DecisionRequest, digest string, result *Decision,
) error {
	body, err := json.Marshal(map[string]any{
		"candidate_id": result.CandidateID,
		"state":        result.State,
		"memory_id":    result.MemoryID,
	})
	if err != nil {
		return fmt.Errorf("encoding the decision response: %w", err)
	}
	if _, err := tx.ExecContext(ctx, `
		UPDATE platform.idempotency_keys
		   SET response_code = 200, response_json = $5::jsonb
		 WHERE principal_id = $1::uuid AND method = $2 AND path = $3 AND key = $4`,
		request.CallerID, decisionMethod, strings.TrimSpace(request.Path),
		strings.TrimSpace(request.IdempotencyKey), string(body),
	); err != nil {
		return fmt.Errorf("storing the decision response: %w", err)
	}
	return nil
}

// helpers shared with memories.go -------------------------------------------

func normaliseLimit(limit int) int {
	if limit <= 0 {
		return DefaultListLimit
	}
	if limit > MaxListLimit {
		return MaxListLimit
	}
	return limit
}

func nullableUUID(value string) any {
	if strings.TrimSpace(value) == "" {
		return nil
	}
	return value
}

func nullableTime(position *cursorPosition) any {
	if position == nil || !position.Set {
		return nil
	}
	return position.At.UTC()
}

func cursorID(position *cursorPosition) any {
	if position == nil || !position.Set {
		return nil
	}
	return position.ID
}
