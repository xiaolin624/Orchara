package execution

// F03/T038: the SQL half of conservative recovery.
//
// Lock order is the package-wide Run -> attempt/job order. The recover
// transaction re-reads every fact it decided on while holding the row locks, so
// a heartbeat, cancel, claim or artifact change that raced the (slower) object
// verification is a refusal, never a stale write. Every timestamp is the
// database clock; Go only formats values read back.

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"strings"
	"time"

	"orchara/platform/internal/uuid"
)

// recoveryQueryer is the read surface shared by the service's *sql.DB (checks
// before the write transaction) and *sql.Tx (the same checks under locks).
type recoveryQueryer interface {
	QueryRowContext(ctx context.Context, query string, args ...any) *sql.Row
	QueryContext(ctx context.Context, query string, args ...any) (*sql.Rows, error)
}

// recoveryRunFacts is one Run plus the frozen snapshot facts recovery needs.
type recoveryRunFacts struct {
	ID                         string
	OwnerID                    string
	State                      string
	Phase                      string
	Revision                   int64
	RecoveryReason             sql.NullString
	RecoverySince              sql.NullTime
	CancelRequestedAt          sql.NullTime
	ExecutionSnapshotID        string
	ManifestSHA256             string
	InputArtifactID            string
	SnapshotManifestArtifactID string
	SnapshotManifestSHA256     string
	SnapshotWorkerImageDigest  string
	LiveAttempts               int64
	Attempts                   int64
	HasEvidence                bool
}

// loadRecoveryRunFacts reads (and optionally locks) one non-deleted Run and its
// snapshot identity. A missing/deleted Run is (nil, nil): the caller keeps the
// 404 vocabulary.
func loadRecoveryRunFacts(
	ctx context.Context, q recoveryQueryer, runID string, lock bool,
) (*recoveryRunFacts, error) {
	query := `
		SELECT r.id::text, r.owner_id::text, r.state, r.phase, r.revision,
		       r.recovery_reason, r.recovery_since, r.cancel_requested_at,
		       r.execution_snapshot_id::text,
		       COALESCE(r.manifest_sha256, ''),
		       COALESCE(r.input_artifact_id::text, ''),
		       s.manifest_artifact_id::text,
		       COALESCE(s.manifest_sha256, ''),
		       s.worker_image_digest,
		       (SELECT count(*) FROM platform.run_attempts AS a
		         WHERE a.run_id = r.id
		           AND a.exited_at IS NULL
		           AND a.revoked_at IS NULL) AS live_attempts,
		       (SELECT count(*) FROM platform.run_attempts AS a
		         WHERE a.run_id = r.id) AS attempts,
		       EXISTS (SELECT 1 FROM platform.run_recovery_evidence AS e
		                WHERE e.run_id = r.id) AS has_evidence
		  FROM platform.runs AS r
		  JOIN platform.execution_snapshots AS s ON s.id = r.execution_snapshot_id
		 WHERE r.id = $1::uuid
		   AND r.deleted_at IS NULL`
	if lock {
		query += ` FOR UPDATE OF r`
	}
	var facts recoveryRunFacts
	err := q.QueryRowContext(ctx, query, runID).Scan(
		&facts.ID, &facts.OwnerID, &facts.State, &facts.Phase, &facts.Revision,
		&facts.RecoveryReason, &facts.RecoverySince, &facts.CancelRequestedAt,
		&facts.ExecutionSnapshotID, &facts.ManifestSHA256, &facts.InputArtifactID,
		&facts.SnapshotManifestArtifactID, &facts.SnapshotManifestSHA256,
		&facts.SnapshotWorkerImageDigest,
		&facts.LiveAttempts, &facts.Attempts, &facts.HasEvidence,
	)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("reading the run recovery facts: %w", err)
	}
	return &facts, nil
}

// recoveryAttemptFacts is the latest attempt of one Run and the job facts the
// recover decision needs. The token is a credential and is never reported.
type recoveryAttemptFacts struct {
	Token                 string
	JobID                 string
	RunID                 string
	ExecutionUnitID       string
	StartedAt             string
	HeartbeatAt           string
	LeaseDeadline         string
	ExitedAt              sql.NullTime
	RevokedAt             sql.NullTime
	ActiveSettledAt       sql.NullTime
	ExitEvidence          []byte
	CheckpointConnections []byte
	HeartbeatFresh        bool
	Live                  bool
	JobState              string
	JobKind               string
	ResumeArtifactID      sql.NullString
	LiveJobs              int64
}

// loadRecoveryAttemptFacts reads (and optionally locks) the Run's latest attempt
// and its job. `heartbeat_fresh` is decided by the database clock against the
// grace window: Go never compares a wall clock.
func loadRecoveryAttemptFacts(
	ctx context.Context, q recoveryQueryer, runID string, grace time.Duration, lock bool,
) (*recoveryAttemptFacts, error) {
	query := `
		SELECT a.token::text, a.job_id::text, a.run_id::text, a.execution_unit_id,
		       a.started_at::text, a.heartbeat_at::text, a.lease_deadline::text,
		       a.exited_at, a.revoked_at, a.active_settled_at,
		       a.exit_evidence, a.checkpoint_connections,
		       (a.heartbeat_at > now() - make_interval(secs => $2::double precision)) AS heartbeat_fresh,
		       (a.exited_at IS NULL AND a.revoked_at IS NULL) AS live,
		       j.state, j.kind, j.resume_artifact_id::text,
		       (SELECT count(*) FROM platform.jobs AS j2
		         WHERE j2.run_id = a.run_id
		           AND j2.state IN ('ready', 'leased', 'held')) AS live_jobs
		  FROM platform.run_attempts AS a
		  JOIN platform.jobs AS j ON j.id = a.job_id AND j.run_id = a.run_id
		 WHERE a.run_id = $1::uuid
		 ORDER BY a.started_at DESC, a.token DESC
		 LIMIT 1`
	if lock {
		query += ` FOR UPDATE OF a, j`
	}
	facts := recoveryAttemptFacts{}
	err := q.QueryRowContext(ctx, query, runID, grace.Seconds()).Scan(
		&facts.Token, &facts.JobID, &facts.RunID, &facts.ExecutionUnitID,
		&facts.StartedAt, &facts.HeartbeatAt, &facts.LeaseDeadline,
		&facts.ExitedAt, &facts.RevokedAt, &facts.ActiveSettledAt, &facts.ExitEvidence,
		&facts.CheckpointConnections, &facts.HeartbeatFresh, &facts.Live,
		&facts.JobState, &facts.JobKind, &facts.ResumeArtifactID, &facts.LiveJobs,
	)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("reading the run recovery attempt: %w", err)
	}
	return &facts, nil
}

// loadRecoveryArtifact reads one artifact row for the artifact checks. found=false
// is a legitimate "no such row" (the caller maps it to its own refusal).
func loadRecoveryArtifact(
	ctx context.Context, q recoveryQueryer, artifactID string,
) (artifactFacts, bool, error) {
	if strings.TrimSpace(artifactID) == "" {
		return artifactFacts{}, false, nil
	}
	var facts artifactFacts
	err := q.QueryRowContext(ctx, `
		SELECT id::text, object_key, sha256, size_bytes, state, deleted_at
		  FROM platform.artifacts
		 WHERE id = $1::uuid`,
		artifactID,
	).Scan(&facts.ID, &facts.ObjectKey, &facts.SHA256, &facts.SizeBytes, &facts.State, &facts.Deleted)
	if errors.Is(err, sql.ErrNoRows) {
		return artifactFacts{}, false, nil
	}
	if err != nil {
		return artifactFacts{}, false, fmt.Errorf("reading the recovery artifact: %w", err)
	}
	return facts, true, nil
}

// recheckRecoveryArtifactRows re-reads the two artifact rows inside the write
// transaction. The sealed bytes were verified before the transaction; this proves
// neither row was deleted/rewritten in between.
func recheckRecoveryArtifactRows(
	ctx context.Context, tx *sql.Tx, run *recoveryRunFacts,
) *RecoveryRefusal {
	manifest, found, err := loadRecoveryArtifact(ctx, tx, run.SnapshotManifestArtifactID)
	if err != nil {
		return recoveryRefusal(RecoveryCodeArtifactUnavailable, "%v", err)
	}
	if !found || manifest.State != "ready" || manifest.Deleted.Valid ||
		!manifest.SHA256.Valid ||
		!strings.EqualFold(manifest.SHA256.String, run.SnapshotManifestSHA256) {
		return recoveryRefusal(
			RecoveryCodeArtifactUnavailable,
			"the frozen manifest artifact changed while the evidence was being checked",
		)
	}
	input, found, err := loadRecoveryArtifact(ctx, tx, run.InputArtifactID)
	if err != nil {
		return recoveryRefusal(RecoveryCodeInputDeleted, "%v", err)
	}
	if !found || input.State != "ready" || input.Deleted.Valid ||
		!input.SHA256.Valid || !input.SizeBytes.Valid {
		return recoveryRefusal(
			RecoveryCodeInputDeleted,
			"the run input artifact changed while the evidence was being checked",
		)
	}
	return nil
}

// finalizeRecoveredAttemptTx records the operator's exit proof on the old attempt
// and revokes its token so every late write is stale. A supervisor exit already
// recorded by T031 is preserved (COALESCE): the platform never overwrites trusted
// evidence with an operator document.
func finalizeRecoveredAttemptTx(
	ctx context.Context, tx *sql.Tx, token string, evidence []byte,
) error {
	result, err := tx.ExecContext(ctx, `
		UPDATE platform.run_attempts
		   SET exited_at = COALESCE(exited_at, now()),
		       exit_evidence = COALESCE(exit_evidence, $2::jsonb),
		       revoked_at = COALESCE(revoked_at, now())
		 WHERE token = $1::uuid`,
		token, evidence,
	)
	if err != nil {
		return fmt.Errorf("finalizing the recovered attempt: %w", err)
	}
	affected, err := result.RowsAffected()
	if err != nil {
		return fmt.Errorf("finalizing the recovered attempt: %w", err)
	}
	if affected != 1 {
		return ErrStaleAttempt
	}
	return nil
}

// requeueRecoveredJobTx re-arms exactly the one held/leased job of the Run. The
// kind and every other fact (target, owner, dedup key, expected revision/hash,
// resume artifact) stay exactly as they were; only the dispatch state changes.
func requeueRecoveredJobTx(ctx context.Context, tx *sql.Tx, jobID, runID string) error {
	result, err := tx.ExecContext(ctx, `
		UPDATE platform.jobs
		   SET state = 'ready',
		       current_attempt_token = NULL,
		       available_at = now()
		 WHERE id = $1::uuid
		   AND run_id = $2::uuid
		   AND state IN ('held', 'leased')`,
		jobID, runID,
	)
	if err != nil {
		return fmt.Errorf("re-arming the recovered job: %w", err)
	}
	affected, err := result.RowsAffected()
	if err != nil {
		return fmt.Errorf("re-arming the recovered job: %w", err)
	}
	if affected != 1 {
		return ErrStaleAttempt
	}
	return nil
}

// returnRecoveredRunToQueueTx is the one aggregate transition of a successful
// recovery: queued / phase idle / revision+1 and the recovery columns cleared, CAS
// on the inspected state+revision. A run that moved under the transaction is a
// stale-attempt refusal, not an overwrite.
func returnRecoveredRunToQueueTx(
	ctx context.Context, tx *sql.Tx, runID string, expectedRevision int64,
) error {
	result, err := tx.ExecContext(ctx, `
		UPDATE platform.runs
		   SET state = 'queued',
		       phase = 'idle',
		       revision = revision + 1,
		       recovery_reason = NULL,
		       recovery_since = NULL
		 WHERE id = $1::uuid
		   AND state = 'recovery_pending'
		   AND revision = $2
		   AND deleted_at IS NULL`,
		runID, expectedRevision,
	)
	if err != nil {
		return fmt.Errorf("returning the recovered run to the queue: %w", err)
	}
	affected, err := result.RowsAffected()
	if err != nil {
		return fmt.Errorf("returning the recovered run to the queue: %w", err)
	}
	if affected != 1 {
		return ErrStaleAttempt
	}
	return nil
}

// insertRecoveryEvidenceTx records the audited proof and always names the job it
// re-armed. Requeue reuses the same logical job on a later cycle, so the column is
// deliberately non-unique; the once-per-attempt rule stays UNIQUE(run_id,
// attempt_token), and every cycle keeps a readable link to its requeue.
func insertRecoveryEvidenceTx(
	ctx context.Context,
	tx *sql.Tx,
	runID, token string,
	evidence []byte,
	evidenceSHA, actorID, jobID string,
) (string, time.Time, error) {
	var recordedAt time.Time
	err := tx.QueryRowContext(ctx, `
		INSERT INTO platform.run_recovery_evidence
		    (id, run_id, attempt_token, evidence, evidence_sha256, recorded_by, recorded_at, resumed_job_id)
		VALUES ($1::uuid, $2::uuid, $3::uuid, $4::jsonb, $5, $6::uuid, now(), $7::uuid)
		RETURNING recorded_at`,
		uuid.NewString(), runID, token, evidence, evidenceSHA, actorID, jobID,
	).Scan(&recordedAt)
	if err != nil {
		return "", time.Time{}, fmt.Errorf("recording the recovery evidence: %w", err)
	}
	return jobID, recordedAt, nil
}

// loadRecoveryFacts projects the parked/evidenced state of one Run for the
// owner-facing detail. A missing/deleted Run is (nil, nil).
func loadRecoveryFacts(
	ctx context.Context, q recoveryQueryer, runID string,
) (*RecoveryFacts, error) {
	var (
		reason     sql.NullString
		since      sql.NullTime
		evidenceAt sql.NullTime
		sha        sql.NullString
		resumed    sql.NullString
	)
	err := q.QueryRowContext(ctx, `
		SELECT r.recovery_reason, r.recovery_since,
		       e.recorded_at, e.evidence_sha256, e.resumed_job_id::text
		  FROM platform.runs AS r
		  LEFT JOIN LATERAL (
		      SELECT recorded_at, evidence_sha256, resumed_job_id
		        FROM platform.run_recovery_evidence
		       WHERE run_id = r.id
		       ORDER BY recorded_at DESC, id DESC
		       LIMIT 1
		  ) AS e ON true
		 WHERE r.id = $1::uuid
		   AND r.deleted_at IS NULL`,
		runID,
	).Scan(&reason, &since, &evidenceAt, &sha, &resumed)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("reading the run recovery facts: %w", err)
	}
	facts := &RecoveryFacts{}
	if reason.Valid {
		facts.Reason = reason.String
	}
	if since.Valid {
		value := since.Time
		facts.Since = &value
	}
	if evidenceAt.Valid {
		value := evidenceAt.Time
		facts.EvidenceAt = &value
	}
	if sha.Valid {
		facts.EvidenceSHA256 = sha.String
	}
	if resumed.Valid {
		value := resumed.String
		facts.ResumedJobID = &value
	}
	return facts, nil
}

// ---------------------------------------------------------------------------
// sweep
// ---------------------------------------------------------------------------

// leaseSweepCandidate is one expired live attempt from the scan.
type leaseSweepCandidate struct {
	Token           string
	RunID           string
	JobID           string
	ExecutionUnitID string
	LeaseDeadline   string
	// LeaseDeadlineTime is the typed lease end. The park settles active time at
	// this last provable activity end, never at park time.
	LeaseDeadlineTime time.Time
	HeartbeatAt       string
}

// loadLeaseSweepCandidates scans running Runs whose live attempt lease expired
// beyond the grace window. It does not lock: each park re-checks under
// `FOR UPDATE`, so a heartbeat/claim/cancel between scan and park is a skip.
//
// Only `running` Runs are parked. `cancelling` and `waiting_*` are deliberately
// left alone: recovery must not resurrect a cancel intent, and STATE-MACHINES §2
// has no waiting_* -> recovery_pending edge. A cancel that has no live supervisor
// left to confirm it is a separate (documented) successor problem.
func loadLeaseSweepCandidates(
	ctx context.Context, db *sql.DB, grace time.Duration, limit int,
) ([]leaseSweepCandidate, error) {
	rows, err := db.QueryContext(ctx, `
		SELECT a.token::text, a.run_id::text, a.job_id::text, a.execution_unit_id,
		       a.lease_deadline, a.lease_deadline::text, a.heartbeat_at::text
		  FROM platform.run_attempts AS a
		  JOIN platform.runs AS r ON r.id = a.run_id
		  JOIN platform.jobs AS j ON j.id = a.job_id AND j.run_id = a.run_id
		 WHERE a.exited_at IS NULL
		   AND a.revoked_at IS NULL
		   AND a.lease_deadline < now() - make_interval(secs => $1::double precision)
		   AND r.state = 'running'
		   AND r.deleted_at IS NULL
		   AND r.cancel_requested_at IS NULL
		 ORDER BY a.lease_deadline, a.token
		 LIMIT $2`,
		grace.Seconds(), limit,
	)
	if err != nil {
		return nil, fmt.Errorf("scanning expired execution leases: %w", err)
	}
	defer rows.Close()
	candidates := make([]leaseSweepCandidate, 0, limit)
	for rows.Next() {
		var candidate leaseSweepCandidate
		if err := rows.Scan(
			&candidate.Token, &candidate.RunID, &candidate.JobID,
			&candidate.ExecutionUnitID, &candidate.LeaseDeadlineTime,
			&candidate.LeaseDeadline, &candidate.HeartbeatAt,
		); err != nil {
			return nil, fmt.Errorf("scanning expired execution leases: %w", err)
		}
		candidates = append(candidates, candidate)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("scanning expired execution leases: %w", err)
	}
	return candidates, nil
}

// lockLeaseSweepCandidateTx re-reads one candidate under row locks. It returns
// nil when the attempt/run/job no longer satisfies every sweep predicate.
func lockLeaseSweepCandidateTx(
	ctx context.Context, tx *sql.Tx, token string, grace time.Duration,
) (*leaseSweepCandidate, error) {
	var candidate leaseSweepCandidate
	err := tx.QueryRowContext(ctx, `
		SELECT a.token::text, a.run_id::text, a.job_id::text, a.execution_unit_id,
		       a.lease_deadline, a.lease_deadline::text, a.heartbeat_at::text
		  FROM platform.run_attempts AS a
		  JOIN platform.runs AS r ON r.id = a.run_id
		  JOIN platform.jobs AS j ON j.id = a.job_id AND j.run_id = a.run_id
		 WHERE a.token = $1::uuid
		   AND a.exited_at IS NULL
		   AND a.revoked_at IS NULL
		   AND a.lease_deadline < now() - make_interval(secs => $2::double precision)
		   AND r.state = 'running'
		   AND r.deleted_at IS NULL
		   AND r.cancel_requested_at IS NULL
		   AND j.state IN ('leased', 'held')
		 FOR UPDATE OF a, j, r`,
		token, grace.Seconds(),
	).Scan(
		&candidate.Token, &candidate.RunID, &candidate.JobID,
		&candidate.ExecutionUnitID, &candidate.LeaseDeadlineTime,
		&candidate.LeaseDeadline, &candidate.HeartbeatAt,
	)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("re-reading the expired lease candidate: %w", err)
	}
	return &candidate, nil
}

// ---------------------------------------------------------------------------
// actor
// ---------------------------------------------------------------------------

// resolveRecoveryActor resolves the real administrator recorded as the evidence
// author. An empty login resolves the bootstrap administrator (the earliest
// enabled human admin); a named login must be an enabled human administrator. An
// empty id with no error means "no such actor" (the caller refuses).
func resolveRecoveryActor(
	ctx context.Context, q recoveryQueryer, login string,
) (string, string, error) {
	login = strings.TrimSpace(login)
	var id, foundLogin string
	err := q.QueryRowContext(ctx, `
		SELECT p.id::text, u.login
		  FROM platform.principals AS p
		  JOIN platform.users AS u ON u.principal_id = p.id
		  JOIN platform.role_bindings AS rb
		    ON rb.principal_id = p.id AND rb.role = 'admin'
		 WHERE p.disabled_at IS NULL
		   AND ($1 = '' OR u.login = $1)
		 ORDER BY p.created_at, p.id
		 LIMIT 1`,
		login,
	).Scan(&id, &foundLogin)
	if errors.Is(err, sql.ErrNoRows) {
		return "", "", nil
	}
	if err != nil {
		return "", "", fmt.Errorf("resolving the recovery actor: %w", err)
	}
	return id, foundLogin, nil
}
