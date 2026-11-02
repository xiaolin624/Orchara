package execution

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"time"

	"orchara/platform/internal/uuid"
)

// This file is the SQL half of the package: every query that reads or writes
// claim/heartbeat/exit state. The service half validates, signs links and maps
// typed errors; it never builds SQL. Keeping the two apart makes the "one
// database clock" rule visible: every time comparison below is `now()` inside
// the statement that writes the row.

// claimCandidate is one lease-able job with the immutable facts the lease
// carries. It is read under the job+run row locks in the claim transaction.
type claimCandidate struct {
	JobID              string
	RunID              string
	JobKind            string
	ResumeArtifactID   sql.NullString
	SnapshotID         string
	Manifest           []byte
	ManifestSHA256     string
	ManifestArtifactID string
	InputArtifactID    string
	RunManifestSHA256  string
	BudgetLimit        string
	Currency           string
}

// artifactFacts is the sealed-object identity the lease links are minted from.
type artifactFacts struct {
	ID        string
	ObjectKey string
	SHA256    sql.NullString
	SizeBytes sql.NullInt64
	State     string
	Deleted   sql.NullTime
}

// attemptFacts is one attempt plus the job/run control facts a heartbeat or
// exit needs. LeaseLiveness is decided by the database, not by Go.
type attemptFacts struct {
	Token             string
	JobID             string
	RunID             string
	ExecutionUnitID   string
	LeaseDeadline     string
	HeartbeatAt       string
	ExitedAt          sql.NullTime
	RevokedAt         sql.NullTime
	LeaseLive         bool
	EvidenceSame      bool
	CancelRequestedAt sql.NullTime
	BudgetLimit       string
	PolicyRevision    sql.NullInt64
	JobState          string
	JobRunID          string
	RunState          string
	RunPhase          string
	RunRevision       int64
	RunStartedAt      sql.NullTime
}

// selectClaimCandidateTx picks one ready job whose run is queued and whose
// snapshot compiler version the caller supports.
//
// FOR UPDATE OF j, r (not OF s) locks exactly the two mutable rows the claim is
// about; SKIP LOCKED is what makes concurrent supervisors choose different jobs
// instead of queueing behind the same one. The snapshot is immutable and never
// locked for update.
func selectClaimCandidateTx(
	ctx context.Context,
	tx *sql.Tx,
	supportedCompilers []string,
) (*claimCandidate, error) {
	var (
		candidate claimCandidate
		resume    sql.NullString
		manifest  []byte
	)
	err := tx.QueryRowContext(ctx, `
		SELECT j.id::text, j.run_id::text, j.kind, j.resume_artifact_id::text,
		       r.execution_snapshot_id::text, s.manifest, s.manifest_sha256,
		       s.manifest_artifact_id::text, r.input_artifact_id::text,
		       r.manifest_sha256, r.budget_limit::text, r.currency
		  FROM platform.jobs AS j
		  JOIN platform.runs AS r ON r.id = j.run_id
		  JOIN platform.execution_snapshots AS s ON s.id = r.execution_snapshot_id
		 WHERE j.state = 'ready'
		   AND j.available_at <= now()
		   AND r.state = 'queued'
		   AND r.deleted_at IS NULL
		   AND s.compiler_version = ANY($1::text[])
		 ORDER BY j.available_at, j.id
		 LIMIT 1
		   FOR UPDATE OF j, r SKIP LOCKED`,
		supportedCompilers,
	).Scan(
		&candidate.JobID, &candidate.RunID, &candidate.JobKind, &resume,
		&candidate.SnapshotID, &manifest, &candidate.ManifestSHA256,
		&candidate.ManifestArtifactID, &candidate.InputArtifactID,
		&candidate.RunManifestSHA256, &candidate.BudgetLimit, &candidate.Currency,
	)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("selecting a claim candidate: %w", err)
	}
	candidate.ResumeArtifactID = resume
	candidate.Manifest = manifest
	return &candidate, nil
}

// lockQuotaCounterTx reads the one seeded counter under a row lock. A missing
// row is a deployment defect, not a zero capacity: the claim fails closed with
// a 503 instead of silently refusing every piece of work.
func lockQuotaCounterTx(ctx context.Context, tx *sql.Tx) (used, capacity int, err error) {
	err = tx.QueryRowContext(ctx, `
		SELECT used, capacity
		  FROM platform.quota_counters
		 WHERE scope = $1 AND dimension = $2
		 FOR UPDATE`,
		quotaScope, quotaDimension,
	).Scan(&used, &capacity)
	if errors.Is(err, sql.ErrNoRows) {
		return 0, 0, ErrQuotaUnavailable
	}
	if err != nil {
		return 0, 0, fmt.Errorf("reading the execution quota counter: %w", err)
	}
	return used, capacity, nil
}

// takeQuotaSlotTx increments `used` only while it is below `capacity`. The
// UPDATE's own predicate is the atomic check: two supervisors can race here,
// and exactly one of the "last slot" updates affects a row.
func takeQuotaSlotTx(ctx context.Context, tx *sql.Tx) error {
	var used int
	err := tx.QueryRowContext(ctx, `
		UPDATE platform.quota_counters
		   SET used = used + 1
		 WHERE scope = $1 AND dimension = $2 AND used < capacity
		RETURNING used`,
		quotaScope, quotaDimension,
	).Scan(&used)
	if errors.Is(err, sql.ErrNoRows) {
		return errNotEnoughCapacity
	}
	if err != nil {
		return fmt.Errorf("taking an execution quota slot: %w", err)
	}
	return nil
}

// errNotEnoughCapacity is an internal outcome, not an error the HTTP layer
// reports: the claim answers 204 and nothing changed.
var errNotEnoughCapacity = errors.New("no execution capacity")

// takeAllocationTx takes (or re-arms) the allocation for holderKey.
//
// The contract's UNIQUE(scope,dimension,holder_key) means a job that exits and
// is claimed again cannot insert a second row: the released row is re-armed in
// place. The WHERE clause refuses to re-arm a row that is still live, which
// would mean a ready job already holds a slot (an invariant violation).
func takeAllocationTx(ctx context.Context, tx *sql.Tx, holderKey string) error {
	token := uuid.NewString()
	var id string
	err := tx.QueryRowContext(ctx, `
		INSERT INTO platform.quota_allocations (id, scope, dimension, holder_key)
		VALUES ($1::uuid, $2, $3, $4)
		ON CONFLICT (scope, dimension, holder_key)
		DO UPDATE SET released_at = NULL
		 WHERE platform.quota_allocations.released_at IS NOT NULL
		RETURNING id::text`,
		token, quotaScope, quotaDimension, holderKey,
	).Scan(&id)
	if errors.Is(err, sql.ErrNoRows) {
		return ErrAllocationConflict
	}
	if err != nil {
		return fmt.Errorf("taking an execution quota allocation: %w", err)
	}
	return nil
}

// insertAttemptTx writes the one live attempt row for this claim and returns the
// lease facts read back from the database.
//
// The deadline is `now() + ttl` in the INSERT itself, and the RETURNING values
// are the authority the lease response carries: the API never formats a Go
// wall-clock deadline that could differ from the stored one.
func insertAttemptTx(
	ctx context.Context,
	tx *sql.Tx,
	candidate *claimCandidate,
	workerID, executionUnitID, token string,
	leaseTTLSeconds float64,
) (leaseDeadline string, heartbeatAt string, startedAt string, err error) {
	err = tx.QueryRowContext(ctx, `
		INSERT INTO platform.run_attempts
		    (token, job_id, run_id, worker_id, execution_unit_id,
		     lease_deadline, heartbeat_at)
		VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $5,
		        now() + make_interval(secs => $6::double precision), now())
		RETURNING lease_deadline::text, heartbeat_at::text, started_at::text`,
		token, candidate.JobID, candidate.RunID, workerID, executionUnitID, leaseTTLSeconds,
	).Scan(&leaseDeadline, &heartbeatAt, &startedAt)
	if err != nil {
		return "", "", "", fmt.Errorf("creating the run attempt: %w", err)
	}
	return leaseDeadline, heartbeatAt, startedAt, nil
}

// leaseJobTx moves the candidate job from ready to leased and names the attempt.
// The state predicate is a second CAS: a job that changed under us is a stale
// claim and the whole transaction rolls back.
func leaseJobTx(ctx context.Context, tx *sql.Tx, jobID, token string) error {
	result, err := tx.ExecContext(ctx, `
		UPDATE platform.jobs
		   SET state = 'leased',
		       current_attempt_token = $2::uuid
		 WHERE id = $1::uuid AND state = 'ready'`,
		jobID, token,
	)
	if err != nil {
		return fmt.Errorf("leasing the job: %w", err)
	}
	affected, err := result.RowsAffected()
	if err != nil {
		return fmt.Errorf("leasing the job: %w", err)
	}
	if affected != 1 {
		return ErrStaleAttempt
	}
	return nil
}

// startRunTx is the one aggregate transition a claim performs:
// queued -> running, phase executing, first started_at, revision+1. Every later
// run transition belongs to the F03 finalizer/cancel tickets.
func startRunTx(ctx context.Context, tx *sql.Tx, runID string) (int64, error) {
	var revision int64
	err := tx.QueryRowContext(ctx, `
		UPDATE platform.runs
		   SET state = 'running',
		       phase = 'executing',
		       started_at = COALESCE(started_at, now()),
		       revision = revision + 1
		 WHERE id = $1::uuid AND state = 'queued' AND deleted_at IS NULL
		RETURNING revision`,
		runID,
	).Scan(&revision)
	if errors.Is(err, sql.ErrNoRows) {
		return 0, ErrStaleAttempt
	}
	if err != nil {
		return 0, fmt.Errorf("starting the run: %w", err)
	}
	return revision, nil
}

// loadArtifactTx reads the sealed object facts of one artifact. It does not
// decide readiness; the service does, so the refusal message can distinguish an
// unusable object from a missing one only by the same 503 (never by existence).
func loadArtifactTx(ctx context.Context, tx *sql.Tx, artifactID string) (artifactFacts, error) {
	var (
		facts artifactFacts
	)
	err := tx.QueryRowContext(ctx, `
		SELECT id::text, object_key, sha256, size_bytes, state, deleted_at
		  FROM platform.artifacts
		 WHERE id = $1::uuid`,
		artifactID,
	).Scan(&facts.ID, &facts.ObjectKey, &facts.SHA256, &facts.SizeBytes, &facts.State, &facts.Deleted)
	if errors.Is(err, sql.ErrNoRows) {
		return artifactFacts{}, ErrObjectStoreUnavailable
	}
	if err != nil {
		return artifactFacts{}, fmt.Errorf("reading the execution artifact: %w", err)
	}
	return facts, nil
}

// renewLeaseTx extends a live attempt's lease. The liveness predicate is here,
// against `now()`, so a lease that expired between the SELECT and this UPDATE is
// refused instead of resurrected.
func renewLeaseTx(
	ctx context.Context,
	tx *sql.Tx,
	token string,
	leaseTTLSeconds float64,
	checkpointConnections []byte,
) (string, error) {
	var deadline string
	err := tx.QueryRowContext(ctx, `
		UPDATE platform.run_attempts
		   SET lease_deadline = now() + make_interval(secs => $2::double precision),
		       heartbeat_at = now(),
		       checkpoint_connections = COALESCE($3::jsonb, checkpoint_connections)
		 WHERE token = $1::uuid
		   AND exited_at IS NULL
		   AND revoked_at IS NULL
		   AND lease_deadline > now()
		RETURNING lease_deadline::text`,
		token, leaseTTLSeconds, nullableBytes(checkpointConnections),
	).Scan(&deadline)
	if errors.Is(err, sql.ErrNoRows) {
		return "", ErrStaleAttempt
	}
	if err != nil {
		return "", fmt.Errorf("renewing the run attempt lease: %w", err)
	}
	return deadline, nil
}

// loadHeartbeatAttemptTx locks one attempt that belongs to runID and reads the
// job/run control facts. `lease_deadline > now()` is computed by the database.
// The job must still name this token; a cleared/replaced token is stale.
func loadHeartbeatAttemptTx(ctx context.Context, tx *sql.Tx, runID, token string) (*attemptFacts, error) {
	var (
		facts  attemptFacts
		policy sql.NullInt64
	)
	err := tx.QueryRowContext(ctx, `
		SELECT a.token::text, a.job_id::text, a.run_id::text, a.execution_unit_id,
		       a.heartbeat_at::text, a.exited_at, a.revoked_at,
		       a.lease_deadline > now() AS lease_live,
		       r.cancel_requested_at, r.budget_limit::text,
		       (SELECT revision FROM platform.policy_epochs WHERE scope = 'acl'),
		       j.state
		  FROM platform.run_attempts AS a
		  JOIN platform.jobs AS j ON j.id = a.job_id AND j.run_id = a.run_id
		  JOIN platform.runs AS r ON r.id = a.run_id
		 WHERE a.token = $1::uuid
		   AND a.run_id = $2::uuid
		   AND j.current_attempt_token = a.token
		 FOR UPDATE OF a`,
		token, runID,
	).Scan(
		&facts.Token, &facts.JobID, &facts.RunID, &facts.ExecutionUnitID,
		&facts.HeartbeatAt, &facts.ExitedAt, &facts.RevokedAt,
		&facts.LeaseLive, &facts.CancelRequestedAt, &facts.BudgetLimit,
		&policy, &facts.JobState,
	)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrStaleAttempt
	}
	if err != nil {
		return nil, fmt.Errorf("reading the heartbeat attempt: %w", err)
	}
	if !policy.Valid {
		return nil, fmt.Errorf("the acl policy epoch is missing")
	}
	facts.PolicyRevision = policy
	return &facts, nil
}

// loadAttemptForExitTx locks the attempt, its job and its run in one statement
// and reads the facts an exit decision needs. The evidence comparison
// (`exit_evidence = $2::jsonb`) is JSONB equality, so key order and whitespace
// never make a repeated exit look conflicting.
func loadAttemptForExitTx(
	ctx context.Context,
	tx *sql.Tx,
	attemptID string,
	evidence []byte,
) (*attemptFacts, error) {
	var facts attemptFacts
	err := tx.QueryRowContext(ctx, `
		SELECT a.token::text, a.job_id::text, a.run_id::text, a.execution_unit_id,
		       a.lease_deadline::text, a.exited_at, a.revoked_at,
		       a.lease_deadline > now() AS lease_live,
		       (a.exit_evidence IS NOT NULL AND a.exit_evidence = $2::jsonb) AS evidence_same,
		       j.state, j.run_id::text, r.state, r.phase, r.revision, r.started_at
		  FROM platform.run_attempts AS a
		  JOIN platform.jobs AS j ON j.id = a.job_id
		  JOIN platform.runs AS r ON r.id = a.run_id
		 WHERE a.token = $1::uuid
		 FOR UPDATE OF a, j, r`,
		attemptID, evidence,
	).Scan(
		&facts.Token, &facts.JobID, &facts.RunID, &facts.ExecutionUnitID,
		&facts.LeaseDeadline, &facts.ExitedAt, &facts.RevokedAt,
		&facts.LeaseLive, &facts.EvidenceSame,
		&facts.JobState, &facts.JobRunID, &facts.RunState, &facts.RunPhase,
		&facts.RunRevision, &facts.RunStartedAt,
	)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrAttemptNotFound
	}
	if err != nil {
		return nil, fmt.Errorf("reading the exiting attempt: %w", err)
	}
	return &facts, nil
}

// recordExitTx marks the attempt exited and revokes its token in one statement.
// exited_at is the database clock; the evidence is recorded verbatim.
func recordExitTx(ctx context.Context, tx *sql.Tx, token string, evidence []byte) error {
	result, err := tx.ExecContext(ctx, `
		UPDATE platform.run_attempts
		   SET exited_at = now(),
		       exit_evidence = $2::jsonb,
		       revoked_at = now()
		 WHERE token = $1::uuid
		   AND exited_at IS NULL
		   AND revoked_at IS NULL`,
		token, evidence,
	)
	if err != nil {
		return fmt.Errorf("recording the attempt exit: %w", err)
	}
	affected, err := result.RowsAffected()
	if err != nil {
		return fmt.Errorf("recording the attempt exit: %w", err)
	}
	if affected != 1 {
		return ErrStaleAttempt
	}
	return nil
}

// releaseAllocationTx sets released_at once and decrements `used` only when the
// release actually happened. The `released_at IS NULL` predicate is the
// exactly-once rule; the counter's `used > 0` guard makes a second call a no-op
// instead of a negative count.
func releaseAllocationTx(ctx context.Context, tx *sql.Tx, holderKey string) (bool, error) {
	var id string
	err := tx.QueryRowContext(ctx, `
		UPDATE platform.quota_allocations
		   SET released_at = now()
		 WHERE scope = $1 AND dimension = $2 AND holder_key = $3
		   AND released_at IS NULL
		RETURNING id::text`,
		quotaScope, quotaDimension, holderKey,
	).Scan(&id)
	if errors.Is(err, sql.ErrNoRows) {
		// Nothing to release: the exact-once rule already held. A missing row for
		// a just-exited attempt is an invariant violation, not a double release,
		// so only the caller decides; the return value distinguishes them.
		return false, nil
	}
	if err != nil {
		return false, fmt.Errorf("releasing the execution quota allocation: %w", err)
	}
	result, err := tx.ExecContext(ctx, `
		UPDATE platform.quota_counters
		   SET used = used - 1
		 WHERE scope = $1 AND dimension = $2 AND used > 0`,
		quotaScope, quotaDimension,
	)
	if err != nil {
		return false, fmt.Errorf("decrementing the execution quota counter: %w", err)
	}
	affected, err := result.RowsAffected()
	if err != nil {
		return false, fmt.Errorf("decrementing the execution quota counter: %w", err)
	}
	if affected != 1 {
		return false, ErrAllocationConflict
	}
	return true, nil
}

// returnJobToQueueTx is the started=false release: the job becomes ready again
// and the run returns to queued with phase idle. `started_at` is deliberately
// untouched (the claim's first-claim timestamp is the only value it ever had).
func returnJobToQueueTx(ctx context.Context, tx *sql.Tx, jobID, runID string) error {
	result, err := tx.ExecContext(ctx, `
		UPDATE platform.jobs
		   SET state = 'ready',
		       current_attempt_token = NULL,
		       available_at = now()
		 WHERE id = $1::uuid AND state = 'leased'`,
		jobID,
	)
	if err != nil {
		return fmt.Errorf("returning the job to the queue: %w", err)
	}
	if affected, err := result.RowsAffected(); err != nil || affected != 1 {
		if err != nil {
			return fmt.Errorf("returning the job to the queue: %w", err)
		}
		return ErrStaleAttempt
	}
	result, err = tx.ExecContext(ctx, `
		UPDATE platform.runs
		   SET state = 'queued',
		       phase = 'idle',
		       revision = revision + 1
		 WHERE id = $1::uuid AND state = 'running'`,
		runID,
	)
	if err != nil {
		return fmt.Errorf("returning the run to the queue: %w", err)
	}
	if affected, err := result.RowsAffected(); err != nil || affected != 1 {
		if err != nil {
			return fmt.Errorf("returning the run to the queue: %w", err)
		}
		return ErrStaleAttempt
	}
	return nil
}

// holdJobTx is the started!=false release: the job stops being leased but the
// run stays non-terminal. T031/T038 own the next transition; nothing here
// invents a terminal state or a result.
func holdJobTx(ctx context.Context, tx *sql.Tx, jobID string) error {
	result, err := tx.ExecContext(ctx, `
		UPDATE platform.jobs
		   SET state = 'held',
		       current_attempt_token = NULL
		 WHERE id = $1::uuid AND state = 'leased'`,
		jobID,
	)
	if err != nil {
		return fmt.Errorf("holding the job: %w", err)
	}
	if affected, err := result.RowsAffected(); err != nil || affected != 1 {
		if err != nil {
			return fmt.Errorf("holding the job: %w", err)
		}
		return ErrStaleAttempt
	}
	return nil
}

// liveLease returns the live attempt's non-secret lease facts for one run, for
// the owner-facing run detail extension. It never returns the attempt token: the
// token is a credential and must not be exposed by a public read.
type LiveLease struct {
	LeaseDeadline time.Time
	StartedAt     time.Time
}

func liveLease(ctx context.Context, db *sql.DB, runID string) (*LiveLease, error) {
	var lease LiveLease
	err := db.QueryRowContext(ctx, `
		SELECT a.lease_deadline, a.started_at
		  FROM platform.run_attempts AS a
		 WHERE a.run_id = $1::uuid
		   AND a.exited_at IS NULL
		   AND a.revoked_at IS NULL
		 ORDER BY a.started_at DESC
		 LIMIT 1`,
		runID,
	).Scan(&lease.LeaseDeadline, &lease.StartedAt)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("reading the live attempt lease: %w", err)
	}
	return &lease, nil
}

// nullableBytes passes a JSONB parameter as NULL when there is no value.
func nullableBytes(value []byte) any {
	if len(value) == 0 {
		return nil
	}
	return value
}
