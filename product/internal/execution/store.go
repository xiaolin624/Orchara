package execution

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"strings"
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
	// Revision and the active-time facts let Claim refuse a queued Run whose
	// accumulated active time already reached its frozen limit, before any slot
	// or attempt exists (T034).
	Revision        int64
	MaxActiveMS     sql.NullInt64
	ActiveElapsedMS int64
	// ParentRunID is set for a delegated child Run (F07/T078/T081). It selects
	// the independent child pool and the per-parent cap instead of the Python
	// execution-slot pool.
	ParentRunID string
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
	RunOwnerID        string
	RunManifestSHA256 string
	ImageDigest       string
	ReportedResult    []byte
	CheckpointID      sql.NullString
	// T036: the Run's control facts the exit path needs to arbitrate cancel vs
	// deadline vs the stored proposal.
	RunCancelRequestedAt sql.NullTime
	RunErrorCode         sql.NullString
	// T034: the database-clock length of this attempt so far and whether its
	// interval was already settled into runs.active_elapsed_ms.
	LiveActiveMS   int64
	ActiveSettled  bool
	RunActiveMS    int64
	RunMaxActiveMS sql.NullInt64
}

// selectClaimCandidateTx picks one ready job whose run is queued and whose
// snapshot compiler version the caller supports.
//
// FOR UPDATE OF j, r (not OF s) locks exactly the two mutable rows the claim is
// about; SKIP LOCKED is what makes concurrent supervisors choose different jobs
// instead of queueing behind the same one. The snapshot is immutable and never
// locked for update.
//
// F07/T081 adds two properties to the same query, without a second queue:
//
//   - **pool routing.** A child Run's candidate joins the global/child_slots
//     counter (and, when it exists, its parent's counter) and is skipped while
//     that pool is full. A top-level candidate joins global/execution_slots. The
//     two joins are mutually exclusive through the CASE on parent_run_id, so a
//     saturated Python pool can never hide a runnable child and vice versa.
//   - **fair claiming.** Candidates are ordered by the *parent's*
//     last_child_claim_at (NULL => '-infinity') before the job age. A parent that
//     just had one child claimed sorts behind every parent that has not, so one
//     parent with many queued children cannot monopolise the child pool. A
//     top-level candidate has no parent and keeps the historical FIFO order.
func selectClaimCandidateTx(
	ctx context.Context,
	tx *sql.Tx,
	supportedCompilers []string,
) (*claimCandidate, error) {
	var (
		candidate claimCandidate
		resume    sql.NullString
		manifest  []byte
		parentID  sql.NullString
	)
	err := tx.QueryRowContext(ctx, `
		SELECT j.id::text, j.run_id::text, j.kind, j.resume_artifact_id::text,
		       r.execution_snapshot_id::text, s.manifest, s.manifest_sha256,
		       s.manifest_artifact_id::text, r.input_artifact_id::text,
		       r.manifest_sha256, r.budget_limit::text, r.currency,
		       r.revision, r.max_active_ms, r.active_elapsed_ms,
		       r.parent_run_id::text
		  FROM platform.jobs AS j
		  JOIN platform.runs AS r ON r.id = j.run_id
		  JOIN platform.principals AS p ON p.id = r.owner_id AND p.disabled_at IS NULL
		  JOIN platform.execution_snapshots AS s ON s.id = r.execution_snapshot_id
		  LEFT JOIN platform.runs AS parent ON parent.id = r.parent_run_id
		  LEFT JOIN platform.quota_counters AS qc
		         ON qc.scope = 'global'
		        AND qc.dimension = CASE
		                WHEN r.parent_run_id IS NULL THEN 'execution_slots'
		                ELSE 'child_slots'
		            END
		  LEFT JOIN platform.quota_counters AS pq
		         ON r.parent_run_id IS NOT NULL
		        AND pq.scope = 'parent:' || r.parent_run_id::text
		        AND pq.dimension = 'child_slots'
		 WHERE j.state = 'ready'
		   AND j.available_at <= now()
		   AND r.state = 'queued'
		   AND r.deleted_at IS NULL
		   AND s.compiler_version = ANY($1::text[])
		   AND (qc.used IS NULL OR qc.used < qc.capacity)
		   AND (pq.used IS NULL OR pq.used < pq.capacity)
		 ORDER BY COALESCE(parent.last_child_claim_at, '-infinity'::timestamptz),
		          j.available_at, j.id
		 LIMIT 1
		   FOR UPDATE OF j, r SKIP LOCKED`,
		supportedCompilers,
	).Scan(
		&candidate.JobID, &candidate.RunID, &candidate.JobKind, &resume,
		&candidate.SnapshotID, &manifest, &candidate.ManifestSHA256,
		&candidate.ManifestArtifactID, &candidate.InputArtifactID,
		&candidate.RunManifestSHA256, &candidate.BudgetLimit, &candidate.Currency,
		&candidate.Revision, &candidate.MaxActiveMS, &candidate.ActiveElapsedMS,
		&parentID,
	)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("selecting a claim candidate: %w", err)
	}
	candidate.ResumeArtifactID = resume
	candidate.Manifest = manifest
	candidate.ParentRunID = parentID.String
	return &candidate, nil
}

// lockQuotaCounterScopedTx reads one counter under a row lock. A missing row is
// a deployment defect, not a zero capacity: the claim fails closed with a 503
// instead of silently refusing every piece of work. The caller holds the row
// lock until commit, so the capacity it read cannot change under it.
func lockQuotaCounterScopedTx(
	ctx context.Context, tx *sql.Tx, scope, dimension string,
) (used, capacity int, err error) {
	err = tx.QueryRowContext(ctx, `
		SELECT used, capacity
		  FROM platform.quota_counters
		 WHERE scope = $1 AND dimension = $2
		 FOR UPDATE`,
		scope, dimension,
	).Scan(&used, &capacity)
	if errors.Is(err, sql.ErrNoRows) {
		return 0, 0, ErrQuotaUnavailable
	}
	if err != nil {
		return 0, 0, fmt.Errorf("reading the %s/%s quota counter: %w", scope, dimension, err)
	}
	return used, capacity, nil
}

// lockQuotaCounterTx is the top-level (Python execution-slot) shorthand.
func lockQuotaCounterTx(ctx context.Context, tx *sql.Tx) (used, capacity int, err error) {
	return lockQuotaCounterScopedTx(ctx, tx, quotaScope, quotaDimension)
}

// ensureQuotaCounterTx creates one lazily-owned counter (the per-parent child
// cap) at its documented capacity. ON CONFLICT DO NOTHING makes it exactly one
// row per scope: a second claim never rewrites the capacity.
func ensureQuotaCounterTx(
	ctx context.Context, tx *sql.Tx, scope, dimension string, capacity int,
) error {
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO platform.quota_counters (scope, dimension, used, capacity)
		VALUES ($1, $2, 0, $3)
		ON CONFLICT (scope, dimension) DO NOTHING`,
		scope, dimension, capacity,
	); err != nil {
		return fmt.Errorf("creating the %s/%s quota counter: %w", scope, dimension, err)
	}
	return nil
}

// takeQuotaSlotScopedTx increments `used` only while it is below `capacity`. The
// UPDATE's own predicate is the atomic check: two supervisors can race here,
// and exactly one of the "last slot" updates affects a row.
func takeQuotaSlotScopedTx(ctx context.Context, tx *sql.Tx, scope, dimension string) error {
	var used int
	err := tx.QueryRowContext(ctx, `
		UPDATE platform.quota_counters
		   SET used = used + 1
		 WHERE scope = $1 AND dimension = $2 AND used < capacity
		RETURNING used`,
		scope, dimension,
	).Scan(&used)
	if errors.Is(err, sql.ErrNoRows) {
		return errNotEnoughCapacity
	}
	if err != nil {
		return fmt.Errorf("taking a %s/%s quota slot: %w", scope, dimension, err)
	}
	return nil
}

// takeQuotaSlotTx is the top-level (Python execution-slot) shorthand.
func takeQuotaSlotTx(ctx context.Context, tx *sql.Tx) error {
	return takeQuotaSlotScopedTx(ctx, tx, quotaScope, quotaDimension)
}

// errNotEnoughCapacity is an internal outcome, not an error the HTTP layer
// reports: the claim answers 204 and nothing changed.
var errNotEnoughCapacity = errors.New("no execution capacity")

// takeAllocationScopedTx takes (or re-arms) the allocation for holderKey in one
// pool.
//
// The contract's UNIQUE(scope,dimension,holder_key) means a job that exits and
// is claimed again cannot insert a second row: the released row is re-armed in
// place. The WHERE clause refuses to re-arm a row that is still live, which
// would mean a ready job already holds a slot (an invariant violation).
func takeAllocationScopedTx(
	ctx context.Context, tx *sql.Tx, scope, dimension, holderKey string,
) error {
	token := uuid.NewString()
	var id string
	err := tx.QueryRowContext(ctx, `
		INSERT INTO platform.quota_allocations (id, scope, dimension, holder_key)
		VALUES ($1::uuid, $2, $3, $4)
		ON CONFLICT (scope, dimension, holder_key)
		DO UPDATE SET released_at = NULL
		 WHERE platform.quota_allocations.released_at IS NOT NULL
		RETURNING id::text`,
		token, scope, dimension, holderKey,
	).Scan(&id)
	if errors.Is(err, sql.ErrNoRows) {
		return ErrAllocationConflict
	}
	if err != nil {
		return fmt.Errorf("taking a %s/%s quota allocation: %w", scope, dimension, err)
	}
	return nil
}

// takeAllocationTx is the top-level (Python execution-slot) shorthand.
func takeAllocationTx(ctx context.Context, tx *sql.Tx, holderKey string) error {
	return takeAllocationScopedTx(ctx, tx, quotaScope, quotaDimension, holderKey)
}

// markParentChildClaimedTx records the fair-claiming rotation fact on the parent
// Run: this parent just consumed one child-pool claim, so its remaining queued
// children go behind every parent that has not. The child Run row is never the
// carrier of this fact.
func markParentChildClaimedTx(ctx context.Context, tx *sql.Tx, parentRunID string) error {
	if _, err := tx.ExecContext(ctx, `
		UPDATE platform.runs
		   SET last_child_claim_at = now()
		 WHERE id = $1::uuid AND deleted_at IS NULL`,
		parentRunID,
	); err != nil {
		return fmt.Errorf("recording the child-claim rotation: %w", err)
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

// lockExitRunTx takes the Run row lock before the exit query locks the
// attempt/job rows.
//
// F03/T035: every Run-scoped transition now locks the Run first (SubmitResult,
// maybeEnqueueResumeTx and the finalizer do). The exit path used to lock
// attempt->job->run in one statement, which could deadlock against a concurrent
// waiting resolution (run->waiting->job->attempt). The attempt's run_id is
// immutable, so reading it and locking the Run first does not change the exit's
// authority; a missing attempt is still attempt_not_found.
func lockExitRunTx(ctx context.Context, tx *sql.Tx, attemptID string) error {
	var runID string
	err := tx.QueryRowContext(ctx, `
		SELECT r.id::text
		  FROM platform.run_attempts AS a
		  JOIN platform.runs AS r ON r.id = a.run_id
		 WHERE a.token = $1::uuid
		 FOR UPDATE OF r`,
		attemptID,
	).Scan(&runID)
	if errors.Is(err, sql.ErrNoRows) {
		return ErrAttemptNotFound
	}
	if err != nil {
		return fmt.Errorf("locking the exit run: %w", err)
	}
	return nil
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
		       a.active_settled_at IS NOT NULL AS active_settled,
		       GREATEST(
		           0,
		           floor(extract(epoch FROM (now() - a.started_at)) * 1000)::bigint
		       ) AS live_active_ms,
		       r.cancel_requested_at, r.budget_limit::text,
		       (SELECT revision FROM platform.policy_epochs WHERE scope = 'acl'),
		       j.state, r.phase, r.active_elapsed_ms, r.max_active_ms
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
		&facts.LeaseLive, &facts.ActiveSettled, &facts.LiveActiveMS,
		&facts.CancelRequestedAt, &facts.BudgetLimit,
		&policy, &facts.JobState, &facts.RunPhase, &facts.RunActiveMS, &facts.RunMaxActiveMS,
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
// and reads the facts an exit decision needs: the claimed identity (manifest
// hash + image digest) the evidence must match, and whether a result proposal is
// already stored (which decides finalize vs recovery_pending). The evidence
// comparison (`exit_evidence = $2::jsonb`) is JSONB equality, so key order and
// whitespace never make a repeated exit look conflicting.
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
		       a.reported_result, a.checkpoint_id,
		       j.state, j.run_id::text, r.state, r.phase, r.revision, r.started_at,
		       r.owner_id::text, r.manifest_sha256, s.worker_image_digest,
		       r.cancel_requested_at, r.error_code
		  FROM platform.run_attempts AS a
		  JOIN platform.jobs AS j ON j.id = a.job_id
		  JOIN platform.runs AS r ON r.id = a.run_id
		  JOIN platform.execution_snapshots AS s ON s.id = r.execution_snapshot_id
		 WHERE a.token = $1::uuid
		 FOR UPDATE OF a, j, r`,
		attemptID, evidence,
	).Scan(
		&facts.Token, &facts.JobID, &facts.RunID, &facts.ExecutionUnitID,
		&facts.LeaseDeadline, &facts.ExitedAt, &facts.RevokedAt,
		&facts.LeaseLive, &facts.EvidenceSame,
		&facts.ReportedResult, &facts.CheckpointID,
		&facts.JobState, &facts.JobRunID, &facts.RunState, &facts.RunPhase,
		&facts.RunRevision, &facts.RunStartedAt,
		&facts.RunOwnerID, &facts.RunManifestSHA256, &facts.ImageDigest,
		&facts.RunCancelRequestedAt, &facts.RunErrorCode,
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

// holdJobTx stops dispatching the job without completing it. A leased job
// becomes held (the exit path); an already-held job is the idempotent no-op of a
// repeated finalize (waiting/blocked guard after an exit-first park). Nothing
// here invents a terminal state or a result; T035/T038 own the next transition.
func holdJobTx(ctx context.Context, tx *sql.Tx, jobID string) error {
	result, err := tx.ExecContext(ctx, `
		UPDATE platform.jobs
		   SET state = 'held',
		       current_attempt_token = NULL
		 WHERE id = $1::uuid AND state IN ('leased', 'held')`,
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

// failQueuedRunOnDeadlineTx is the "already over before any attempt" deadline
// transition: a queued Run whose settled active time already reached its frozen
// limit is committed failed(deadline_exceeded) instead of being leased. No worker
// slot/attempt ever exists, so there is no allocation to release; the start job is
// voided (never ran) and the Run keeps its accumulated total. The CAS on
// state+revision makes a race a no-op rather than an overwrite.
func failQueuedRunOnDeadlineTx(
	ctx context.Context, tx *sql.Tx, runID string, expectedRevision int64,
) error {
	result, err := tx.ExecContext(ctx, `
		UPDATE platform.runs
		   SET state = 'failed',
		       phase = 'idle',
		       revision = revision + 1,
		       finished_at = now(),
		       output_artifact_id = NULL,
		       error_code = $2,
		       cancel_requested_at = COALESCE(cancel_requested_at, now())
		 WHERE id = $1::uuid
		   AND state = 'queued'
		   AND revision = $3
		   AND deleted_at IS NULL`,
		runID, DeadlineErrorCode, expectedRevision,
	)
	if err != nil {
		return fmt.Errorf("failing the overdue queued run: %w", err)
	}
	affected, err := result.RowsAffected()
	if err != nil {
		return fmt.Errorf("failing the overdue queued run: %w", err)
	}
	if affected != 1 {
		return ErrStaleAttempt
	}
	return nil
}

// voidJobTx voids a job that never ran because its Run was terminalised before a
// claim. STATE-MACHINES §3 uses `void` for a terminal Run's job; `completed` is
// reserved for an execution that actually finished. A repeated call is a no-op.
func voidJobTx(ctx context.Context, tx *sql.Tx, jobID string) error {
	result, err := tx.ExecContext(ctx, `
		UPDATE platform.jobs
		   SET state = 'void',
		       current_attempt_token = NULL
		 WHERE id = $1::uuid AND state IN ('ready', 'void')`,
		jobID,
	)
	if err != nil {
		return fmt.Errorf("voiding the job: %w", err)
	}
	affected, err := result.RowsAffected()
	if err != nil {
		return fmt.Errorf("voiding the job: %w", err)
	}
	if affected != 1 {
		return ErrStaleAttempt
	}
	return nil
}

// OwnerDisabledErrorCode is the stable Run error_code for a queued Run whose
// owner was disabled before it was ever leased.
const OwnerDisabledErrorCode = "owner_disabled"

// failDisabledOwnerRunsTx fails every queued Run whose owner is currently
// disabled, voids its never-run job(s) and appends one run.status event. It is
// called inside the claim transaction, before candidate selection: claim is the
// execution plane's "current authorization" checkpoint, so a Run must not start
// merely because it was queued while the owner was still enabled.
//
// The UPDATE is atomic (no read-then-write race) and idempotent: after the first
// call the Run is no longer `queued`, so a later claim sees nothing to fail.
func failDisabledOwnerRunsTx(ctx context.Context, tx *sql.Tx) (int, error) {
	rows, err := tx.QueryContext(ctx, `
		UPDATE platform.runs AS r
		   SET state = 'failed',
		       phase = 'idle',
		       revision = revision + 1,
		       finished_at = now(),
		       output_artifact_id = NULL,
		       error_code = $1
		  FROM platform.principals AS p
		 WHERE p.id = r.owner_id
		   AND p.disabled_at IS NOT NULL
		   AND r.state = 'queued'
		   AND r.deleted_at IS NULL
		RETURNING r.id::text`, OwnerDisabledErrorCode)
	if err != nil {
		return 0, fmt.Errorf("failing queued runs of disabled owners: %w", err)
	}
	defer func() { _ = rows.Close() }()
	runIDs := make([]string, 0, 4)
	for rows.Next() {
		var runID string
		if err := rows.Scan(&runID); err != nil {
			return 0, fmt.Errorf("failing queued runs of disabled owners: %w", err)
		}
		runIDs = append(runIDs, runID)
	}
	if err := rows.Err(); err != nil {
		return 0, fmt.Errorf("failing queued runs of disabled owners: %w", err)
	}
	// Close before the follow-up statements: this transaction cannot run another
	// statement while the RETURNING cursor is open.
	if err := rows.Close(); err != nil {
		return 0, fmt.Errorf("failing queued runs of disabled owners: %w", err)
	}
	for _, runID := range runIDs {
		if _, err := tx.ExecContext(ctx, `
			UPDATE platform.jobs
			   SET state = 'void', current_attempt_token = NULL
			 WHERE run_id = $1::uuid AND state IN ('ready', 'held')`, runID); err != nil {
			return 0, fmt.Errorf("voiding the disabled owner's job: %w", err)
		}
		if _, err := AppendRunStatusTx(ctx, tx, runID); err != nil {
			return 0, err
		}
	}
	return len(runIDs), nil
}

// LiveLease is the current attempt's non-secret lease facts for one run, for the
// owner-facing run detail extension. It never returns the attempt token: the
// token is a credential and must not be exposed by a public read.
type LiveLease struct {
	LeaseDeadline   time.Time
	StartedAt       time.Time
	ExecutionUnitID string
}

func liveLease(ctx context.Context, db *sql.DB, runID string) (*LiveLease, error) {
	var lease LiveLease
	err := db.QueryRowContext(ctx, `
		SELECT a.lease_deadline, a.started_at, a.execution_unit_id
		  FROM platform.run_attempts AS a
		 WHERE a.run_id = $1::uuid
		   AND a.exited_at IS NULL
		   AND a.revoked_at IS NULL
		 ORDER BY a.started_at DESC
		 LIMIT 1`,
		runID,
	).Scan(&lease.LeaseDeadline, &lease.StartedAt, &lease.ExecutionUnitID)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("reading the live attempt lease: %w", err)
	}
	return &lease, nil
}

// runCancelRequestedAt reads the F03 cancel intent for one run. The endpoint
// that writes it is T036's; this read exists so the run detail can say
// "stopping" instead of inventing a terminal state (T033 UI).
func runCancelRequestedAt(ctx context.Context, db *sql.DB, runID string) (*time.Time, error) {
	var cancelAt sql.NullTime
	err := db.QueryRowContext(ctx, `
		SELECT cancel_requested_at FROM platform.runs WHERE id = $1::uuid`,
		runID,
	).Scan(&cancelAt)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("reading the run cancel intent: %w", err)
	}
	if !cancelAt.Valid {
		return nil, nil
	}
	value := cancelAt.Time
	return &value, nil
}

// nullableBytes passes a JSONB parameter as NULL when there is no value.
func nullableBytes(value []byte) any {
	if len(value) == 0 {
		return nil
	}
	return value
}

// nullableString passes an optional text parameter as SQL NULL.
func nullableString(value *string) any {
	if value == nil {
		return nil
	}
	return *value
}

// ---------------------------------------------------------------------------
// F03/T031: result proposals and the unified finalizer
// ---------------------------------------------------------------------------

// runFacts is one run row locked for the duration of a result-proposal
// transaction. The proposal checks state/phase/revision and needs the owner for
// the output-artifact admission (which is owned by the Run's owner).
type runFacts struct {
	ID       string
	OwnerID  string
	State    string
	Phase    string
	Revision int64
}

// lockRunTx locks one run and reads the facts the proposal needs. A missing run
// is ErrRunNotFound (404); the lock serialises the result and exit handlers, so
// exactly one of them observes the other's committed evidence.
func lockRunTx(ctx context.Context, tx *sql.Tx, runID string) (*runFacts, error) {
	var facts runFacts
	err := tx.QueryRowContext(ctx, `
		SELECT r.id::text, r.owner_id::text, r.state, r.phase, r.revision
		  FROM platform.runs AS r
		 WHERE r.id = $1::uuid
		   FOR UPDATE OF r`,
		runID,
	).Scan(&facts.ID, &facts.OwnerID, &facts.State, &facts.Phase, &facts.Revision)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrRunNotFound
	}
	if err != nil {
		return nil, fmt.Errorf("reading the run: %w", err)
	}
	return &facts, nil
}

// resultAttemptFacts is the attempt+job binding the result endpoint checks.
type resultAttemptFacts struct {
	Token               string
	JobID               string
	RunID               string
	JobState            string
	CurrentAttemptToken sql.NullString
	ReportedResult      []byte
	ExitedAt            sql.NullTime
	Superseded          bool
}

// lockAttemptForResultTx locks the attempt named by the token *for this run* and
// its job. A token that belongs to another run, or an attempt superseded by a
// later attempt of the same run, is not a valid proposer: the query's bindings
// are the cross-run guard.
func lockAttemptForResultTx(
	ctx context.Context, tx *sql.Tx, runID, token string,
) (*resultAttemptFacts, error) {
	var facts resultAttemptFacts
	err := tx.QueryRowContext(ctx, `
		SELECT a.token::text, a.job_id::text, a.run_id::text,
		       a.reported_result, a.exited_at,
		       j.state, j.current_attempt_token::text,
		       EXISTS (
		           SELECT 1 FROM platform.run_attempts AS newer
		            WHERE newer.run_id = a.run_id
		              AND (newer.started_at, newer.token) > (a.started_at, a.token)
		       ) AS superseded
		  FROM platform.run_attempts AS a
		  JOIN platform.jobs AS j ON j.id = a.job_id
		 WHERE a.token = $1::uuid
		   AND a.run_id = $2::uuid
		   FOR UPDATE OF a, j`,
		token, runID,
	).Scan(
		&facts.Token, &facts.JobID, &facts.RunID,
		&facts.ReportedResult, &facts.ExitedAt,
		&facts.JobState, &facts.CurrentAttemptToken, &facts.Superseded,
	)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrStaleAttempt
	}
	if err != nil {
		return nil, fmt.Errorf("reading the result attempt: %w", err)
	}
	return &facts, nil
}

// checkOutputArtifactTx enforces the four admission rules of a succeeded
// proposal: the artifact exists, is owned by the run's owner, is ready and not
// tombstoned, and is registered to this run in run_artifacts. Registration is
// T033; until then the link is the authority that the artifact belongs to this
// run.
func checkOutputArtifactTx(
	ctx context.Context, tx *sql.Tx, runID, runOwnerID, artifactID string,
) error {
	var (
		ownerID   string
		state     string
		deletedAt sql.NullTime
		sha256    sql.NullString
		sizeBytes sql.NullInt64
		relation  sql.NullString
	)
	err := tx.QueryRowContext(ctx, `
		SELECT a.owner_id::text, a.state, a.deleted_at, a.sha256, a.size_bytes,
		       (
		           SELECT ra.relation FROM platform.run_artifacts AS ra
		            WHERE ra.run_id = $1::uuid AND ra.artifact_id = a.id
		       )
		  FROM platform.artifacts AS a
		 WHERE a.id = $2::uuid`,
		runID, artifactID,
	).Scan(&ownerID, &state, &deletedAt, &sha256, &sizeBytes, &relation)
	if errors.Is(err, sql.ErrNoRows) {
		return &OutputArtifactError{
			Code:    CodeArtifactNotFound,
			Message: "the output artifact does not exist",
		}
	}
	if err != nil {
		return fmt.Errorf("reading the output artifact: %w", err)
	}
	switch {
	case !strings.EqualFold(ownerID, runOwnerID):
		return &OutputArtifactError{
			Code:    CodeArtifactNotOwned,
			Message: "the output artifact belongs to another principal; a run may only publish its owner's artifact",
		}
	case state != "ready" || deletedAt.Valid || !sha256.Valid || !sizeBytes.Valid:
		return &OutputArtifactError{
			Code:    CodeArtifactNotReady,
			Message: "the output artifact is not ready (state=" + state + ")",
		}
	case !relation.Valid:
		return &OutputArtifactError{
			Code:    CodeArtifactNotLinked,
			Message: "the output artifact is not registered to this run",
		}
	case relation.String != "output":
		// A link is not enough: the input artifact (relation=input), a node
		// artifact or a log must never be published as the run's final output.
		// T033 closes the vocabulary to output/artifact/log.
		return &OutputArtifactError{
			Code:    CodeArtifactNotOutput,
			Message: "the artifact is linked to this run as " + relation.String + ", not as the final output",
		}
	}
	return nil
}

// storeResultTx writes the proposal once. The `reported_result IS NULL`
// predicate is the once-only rule; the transaction already holds the attempt
// lock, so a second writer serially observes the stored value.
func storeResultTx(
	ctx context.Context, tx *sql.Tx, token string, proposal []byte, checkpointID any,
) error {
	result, err := tx.ExecContext(ctx, `
		UPDATE platform.run_attempts
		   SET reported_result = $2::jsonb,
		       reported_at = now(),
		       checkpoint_id = COALESCE($3, checkpoint_id)
		 WHERE token = $1::uuid
		   AND reported_result IS NULL`,
		token, proposal, checkpointID,
	)
	if err != nil {
		return fmt.Errorf("storing the result proposal: %w", err)
	}
	affected, err := result.RowsAffected()
	if err != nil {
		return fmt.Errorf("storing the result proposal: %w", err)
	}
	if affected != 1 {
		return ErrEventConflict
	}
	return nil
}

// setRunFinalizingTx is the proposal's only run write: phase=finalizing while
// the state stays running (and the revision does not move, because no state
// transition happened). A run parked in recovery_pending keeps its idle phase;
// a later result completes the finalizer rather than faking execution again.
func setRunFinalizingTx(ctx context.Context, tx *sql.Tx, runID string) error {
	_, err := tx.ExecContext(ctx, `
		UPDATE platform.runs
		   SET phase = 'finalizing'
		 WHERE id = $1::uuid AND state = 'running'`,
		runID,
	)
	if err != nil {
		return fmt.Errorf("setting the run phase to finalizing: %w", err)
	}
	return nil
}

// completeJobTx is the finalizer's job transition: the execution is over, the
// job is completed and it no longer names the revoked token. The `state IN
// (leased, held, completed)` predicate makes a repeated finalize a no-op instead
// of an error (the run CAS below still guarantees one committed outcome).
func completeJobTx(ctx context.Context, tx *sql.Tx, jobID string) error {
	result, err := tx.ExecContext(ctx, `
		UPDATE platform.jobs
		   SET state = 'completed',
		       current_attempt_token = NULL
		 WHERE id = $1::uuid
		   AND state IN ('leased', 'held', 'completed')`,
		jobID,
	)
	if err != nil {
		return fmt.Errorf("completing the job: %w", err)
	}
	affected, err := result.RowsAffected()
	if err != nil {
		return fmt.Errorf("completing the job: %w", err)
	}
	if affected != 1 {
		return ErrStaleAttempt
	}
	return nil
}

// revokeAttemptTx clears the execution credential. The exit path already
// revoked it; the finalizer repeats the statement idempotently so a terminal
// Run never has a live token even if it was finalized through a different path.
func revokeAttemptTx(ctx context.Context, tx *sql.Tx, token string) error {
	_, err := tx.ExecContext(ctx, `
		UPDATE platform.run_attempts
		   SET revoked_at = COALESCE(revoked_at, now())
		 WHERE token = $1::uuid`,
		token,
	)
	if err != nil {
		return fmt.Errorf("revoking the execution attempt: %w", err)
	}
	return nil
}

// allocationRow is one locked quota allocation row of a holder.
type allocationRow struct {
	Scope      string
	Dimension  string
	ReleasedAt sql.NullTime
}

// releaseAllocationOnceTx releases **every** allocation row of one holder exactly
// once, whichever pool(s) it occupies. F07/T081 made this generic because a
// delegated child attempt holds two rows (global/child_slots and
// parent:<parent_run_id>/child_slots) while a top-level attempt holds one
// (global/execution_slots). Each row's own scope/dimension selects the counter to
// decrement, so no second release system exists.
//
// A row whose released_at is already set is the successful no-op of a repeated
// finalize; a missing row is an invariant violation. The counter decrement only
// runs with the release, so concurrent/duplicate finalizers cannot decrement
// twice. Rows are locked in (scope, dimension) order so two releases that share
// two counters can never deadlock.
func releaseAllocationOnceTx(ctx context.Context, tx *sql.Tx, holderKey string) error {
	rows, err := tx.QueryContext(ctx, `
		SELECT scope, dimension, released_at
		  FROM platform.quota_allocations
		 WHERE holder_key = $1
		 ORDER BY scope, dimension
		 FOR UPDATE`,
		holderKey,
	)
	if err != nil {
		return fmt.Errorf("reading the quota allocations of %s: %w", holderKey, err)
	}
	var held []allocationRow
	for rows.Next() {
		var row allocationRow
		if err := rows.Scan(&row.Scope, &row.Dimension, &row.ReleasedAt); err != nil {
			rows.Close()
			return fmt.Errorf("reading the quota allocations of %s: %w", holderKey, err)
		}
		held = append(held, row)
	}
	if err := rows.Err(); err != nil {
		rows.Close()
		return fmt.Errorf("reading the quota allocations of %s: %w", holderKey, err)
	}
	if err := rows.Close(); err != nil {
		return fmt.Errorf("reading the quota allocations of %s: %w", holderKey, err)
	}
	if len(held) == 0 {
		return ErrAllocationConflict
	}
	for _, row := range held {
		if row.ReleasedAt.Valid {
			continue
		}
		result, err := tx.ExecContext(ctx, `
			UPDATE platform.quota_allocations
			   SET released_at = now()
			 WHERE scope = $1 AND dimension = $2 AND holder_key = $3
			   AND released_at IS NULL`,
			row.Scope, row.Dimension, holderKey,
		)
		if err != nil {
			return fmt.Errorf("releasing the %s/%s quota allocation: %w", row.Scope, row.Dimension, err)
		}
		affected, err := result.RowsAffected()
		if err != nil {
			return fmt.Errorf("releasing the %s/%s quota allocation: %w", row.Scope, row.Dimension, err)
		}
		if affected != 1 {
			return ErrAllocationConflict
		}
		result, err = tx.ExecContext(ctx, `
			UPDATE platform.quota_counters
			   SET used = used - 1
			 WHERE scope = $1 AND dimension = $2 AND used > 0`,
			row.Scope, row.Dimension,
		)
		if err != nil {
			return fmt.Errorf("decrementing the %s/%s quota counter: %w", row.Scope, row.Dimension, err)
		}
		affected, err = result.RowsAffected()
		if err != nil {
			return fmt.Errorf("decrementing the %s/%s quota counter: %w", row.Scope, row.Dimension, err)
		}
		if affected != 1 {
			return ErrAllocationConflict
		}
	}
	return nil
}

// runOutcome is one committed Run state transition the finalizer asks for.
type runOutcome struct {
	State            string
	OutputArtifactID *string
	ErrorCode        *string
	Finished         bool
}

// updateRunOutcomeTx is the single Run aggregate transition of the finalizer:
// state, revision+1, phase idle, the finished timestamp exactly for a real
// terminal state (needs_reconciliation is non-terminal and stays unfinished),
// the output artifact and the error code. The expected state+revision make it a
// CAS: a run that moved under the transaction is a conflict, not an overwrite.
func updateRunOutcomeTx(
	ctx context.Context,
	tx *sql.Tx,
	runID, expectedState string,
	expectedRevision int64,
	outcome runOutcome,
) error {
	result, err := tx.ExecContext(ctx, `
		UPDATE platform.runs
		   SET state = $2,
		       phase = 'idle',
		       revision = revision + 1,
		       finished_at = CASE WHEN $3 THEN now() ELSE NULL END,
		       output_artifact_id = $4::uuid,
		       error_code = $5,
		       recovery_reason = NULL,
		       recovery_since = NULL
		 WHERE id = $1::uuid
		   AND state = $6
		   AND revision = $7
		   AND deleted_at IS NULL`,
		runID, outcome.State, outcome.Finished,
		nullableString(outcome.OutputArtifactID), nullableString(outcome.ErrorCode),
		expectedState, expectedRevision,
	)
	if err != nil {
		return fmt.Errorf("committing the run outcome: %w", err)
	}
	affected, err := result.RowsAffected()
	if err != nil {
		return fmt.Errorf("committing the run outcome: %w", err)
	}
	if affected != 1 {
		return ErrStaleAttempt
	}
	return nil
}

// parkRunForRecoveryTx is the exit-without-result transition: the supervisor has
// proven the execution unit exited, but there is no result to commit. The run is
// parked in recovery_pending (non-terminal), the job is held and the slot is
// already released. T038 owns proving the old unit drained before any new
// attempt; T031 never requeues on exit alone.
//
// `reason` is the closed parked-reason code (0016 baseline had no such column;
// 0024 adds it): T031's exit-without-result path records `exit_without_result`,
// the lease-expiry sweep records `lease_expired`. recovery_since is the database
// clock of the park; the pair is what the owner-facing X-Run-Recovery-* headers
// read.
func parkRunForRecoveryTx(
	ctx context.Context, tx *sql.Tx, runID, reason string,
) error {
	result, err := tx.ExecContext(ctx, `
		UPDATE platform.runs
		   SET state = 'recovery_pending',
		       phase = 'idle',
		       revision = revision + 1,
		       recovery_reason = $2,
		       recovery_since = now()
		 WHERE id = $1::uuid AND state = 'running' AND deleted_at IS NULL`,
		runID, reason,
	)
	if err != nil {
		return fmt.Errorf("parking the run for recovery: %w", err)
	}
	affected, err := result.RowsAffected()
	if err != nil {
		return fmt.Errorf("parking the run for recovery: %w", err)
	}
	if affected != 1 {
		return ErrStaleAttempt
	}
	return nil
}

// hasLiveChildrenTx is the "all child runs finished" half of the finalizer
// guard. Children are ordinary `platform.runs` rows with parent_run_id set
// (F03 §7 / T035 creates them); a child that is not succeeded/failed/cancelled
// blocks the parent's terminal transition.
func hasLiveChildrenTx(ctx context.Context, tx *sql.Tx, runID string) (bool, error) {
	var live bool
	err := tx.QueryRowContext(ctx, `
		SELECT EXISTS (
		    SELECT 1 FROM platform.runs AS child
		     WHERE child.parent_run_id = $1::uuid
		       AND child.deleted_at IS NULL
		       AND child.state NOT IN ('succeeded', 'failed', 'cancelled')
		)`,
		runID,
	).Scan(&live)
	if err != nil {
		return false, fmt.Errorf("checking live child runs: %w", err)
	}
	return live, nil
}

// hasUnknownOperationsTx is the "no unknown side effects" half of the finalizer
// guard. EXECUTION §5/§7 requires a Run with unresolved external writes to be
// parked in needs_reconciliation instead of succeeding/failing/cancelling.
// T036 replaces T031's always-false placeholder with the real ledger query; the
// finalizer keeps calling this one predicate.
func hasUnknownOperationsTx(ctx context.Context, tx *sql.Tx, runID string) (bool, error) {
	var unresolved bool
	err := tx.QueryRowContext(ctx, `
		SELECT EXISTS (
		    SELECT 1 FROM platform.run_unknown_operations
		     WHERE run_id = $1::uuid
		       AND resolved_at IS NULL
		)`,
		runID,
	).Scan(&unresolved)
	if err != nil {
		return false, fmt.Errorf("checking unresolved unknown operations: %w", err)
	}
	return unresolved, nil
}
