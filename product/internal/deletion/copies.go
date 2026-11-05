package deletion

// F11/T072: the copy-class cleanup pass (checkpoints, summaries, sandbox temp,
// third-party history) and the backup-catalog registration.
//
// T071's `delete_content` handler owns the per-object storage purge. This pass is
// deliberately a *deployment inventory* pass, not a second handler: the
// checkpoint schema is out of band (the product must not create it, DATA-MODEL.md
// §22), sandbox staging directories live on the supervisor's host filesystem, and
// third-party history / backup media are outside the database. None of those are
// reachable through the object-store handler, so `cmd/maintenance copies` runs the
// inventory and records one ledger outcome per copy class. It mutates the *same*
// T069 job row through the documented scope-clearing rule (remove from
// `remaining_scopes`, append to `completed_scopes`, append to the ledger, one
// transaction) and never touches `delete_content`'s registry.
//
// Honesty rules implemented here:
//   - a missing `checkpoint` schema is `absent`/`checkpoint_schema_absent`, never
//     `cleared`;
//   - there is no summary store in this product, so the summary copy class is
//     `absent`/`no_summary_store` after counting the metadata-only surfaces;
//   - vendor copies, already-downloaded copies and backup media are recorded as
//     `scope_external` with a reason: the platform does not claim to erase them
//     (SECURITY.md §51);
//   - only a directory named for the deleted run's real attempt unit and not owned
//     by a live attempt is ever removed (`sandbox_temp`), and the removal is
//     verified on the real filesystem before it is recorded.

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"orchara/platform/internal/jcs"
	"orchara/platform/internal/uuid"
)

// CopyScopeOrder is the T072 scope order used when recording/clearing.
var CopyScopes = []Scope{ScopeCheckpoints, ScopeSandboxTemp, ScopeThirdPartyHistory, ScopeBackupCatalog}

// Copy-class names of the ledger vocabulary.
const (
	CopyCheckpoint       = "checkpoint"
	CopySummary          = "summary"
	CopySandboxTemp      = "sandbox_temp"
	CopyThirdParty       = "third_party_history"
	CopyBackupCatalog    = "backup_catalog"
	CopyBackupMedia      = "backup_media"
	CopyVendor           = "vendor_copy"
	CopyDownloaded       = "downloaded_copy"
	backupRetentionDays  = 30
	sandboxDirPrefix     = "orchara-sandbox-"
	checkpointLockBudget = 2 * time.Second
)

// CopyCleaner runs one bounded cleanup pass over the non-terminal deletion jobs
// that still owe one of the four F11/T072 scopes.
type CopyCleaner struct {
	DB *sql.DB
	// SandboxRoot is the supervisor's host staging directory (its OS temp dir by
	// default). The pass only reads/removes `orchara-sandbox-<attempt unit>-*`.
	SandboxRoot string
	// SandboxAge is the minimum age of a leftover directory before it is removed.
	// Zero means "no age gate": only tests set zero, against real directories.
	SandboxAge time.Duration
	// ThirdPartyComponents names the third-party memory/RAG components this
	// deployment actually runs. Empty is the documented default: T066's committed
	// decision is `do_not_adopt`, so no third-party history exists to clear.
	ThirdPartyComponents []string
	// Limit bounds one pass (0 means 100 jobs).
	Limit int
	// Now is the injected clock (tests set it; production leaves it nil).
	Now func() time.Time
	// Logf receives safe progress lines (ids/codes only) when non-nil.
	Logf func(format string, args ...any)
}

// CopyClassOutcome is one recorded per-copy-class result.
type CopyClassOutcome struct {
	JobID      string
	Scope      Scope
	CopyClass  string
	Outcome    string // cleared | absent | external | failed
	Reason     string
	Record     map[string]any
	ScopeKey   string
	FailureErr string // safe code only
}

// record appends the outcome to the caller's transaction.
func (outcome CopyClassOutcome) appendTx(ctx context.Context, tx *sql.Tx, job *copyJob) error {
	kind := EntryScopeCleared
	switch outcome.Outcome {
	case "absent":
		kind = EntryScopeAbsent
	case "external":
		kind = EntryScopeExternal
	case "failed":
		kind = EntryScopeFailed
	}
	scopeName := string(outcome.Scope)
	copyClass := outcome.CopyClass
	scopeKey := outcome.ScopeKey
	record := outcome.Record
	if record == nil {
		record = map[string]any{}
	}
	record["reason"] = outcome.Reason
	if outcome.FailureErr != "" {
		record["error_code"] = outcome.FailureErr
	}
	_, err := AppendLedgerTx(ctx, tx, LedgerAppend{
		DeletionJobID:    job.ID,
		ResourceType:     job.ResourceType,
		ResourceID:       job.ResourceID,
		DeletionRevision: job.DeletionRevision,
		EntryKind:        kind,
		Scope:            &scopeName,
		CopyClass:        &copyClass,
		ScopeKey:         &scopeKey,
		Record:           record,
	})
	return err
}

// CopyPassReport is the last JSON line `cmd/maintenance copies` prints.
type CopyPassReport struct {
	Status          string   `json:"status"`
	SandboxTempRoot string   `json:"sandbox_temp_root"`
	SandboxAge      string   `json:"sandbox_age"`
	ThirdParty      []string `json:"third_party_components"`
	Jobs            int      `json:"jobs"`
	JobsCompleted   int      `json:"jobs_completed"`
	ScopesCleared   int      `json:"scopes_cleared"`
	ScopesFailed    int      `json:"scopes_failed"`
	CopiesCleared   int      `json:"copies_cleared"`
	CopiesAbsent    int      `json:"copies_absent"`
	CopiesExternal  int      `json:"copies_external"`
	CheckpointLock  string   `json:"checkpoint_lock_timeout"`
	AttemptedJobIDs []string `json:"job_ids"`
	Note            string   `json:"note"`
}

// copyJob is one locked deletion job snapshot.
type copyJob struct {
	ID               string
	ResourceType     string
	ResourceID       string
	DeletionRevision int64
	State            string
	Remaining        []string
	Completed        []string
	ScopeDetails     map[string]any
}

// copyJobDetails is the per-scope descriptor set (internal `scope_details`).
type copyJobDetails struct {
	RunIDs       []string
	ThreadIDs    []string
	DocumentID   string
	KnowledgeIDs []string
}

func scopeRaw(details map[string]any, scope Scope) map[string]any {
	value, ok := details[string(scope)]
	if !ok || value == nil {
		return nil
	}
	raw, ok := value.(map[string]any)
	if !ok {
		return nil
	}
	return raw
}

func stringList(value any) []string {
	switch typed := value.(type) {
	case []string:
		return append([]string(nil), typed...)
	case []any:
		values := make([]string, 0, len(typed))
		for _, item := range typed {
			if text, ok := item.(string); ok && strings.TrimSpace(text) != "" {
				values = append(values, text)
			}
		}
		return values
	default:
		return nil
	}
}

// runIDsOf returns the affected run ids of one job (deduplicated, sorted).
func runIDsOf(details map[string]any) []string {
	seen := make(map[string]bool)
	ids := make([]string, 0, 8)
	for _, scope := range []Scope{ScopeCheckpoints, ScopeSandboxTemp, ScopeThirdPartyHistory} {
		for _, id := range stringList(scopeRaw(details, scope)["run_ids"]) {
			if uuid.Valid(id) && !seen[id] {
				seen[id] = true
				ids = append(ids, id)
			}
		}
	}
	sort.Strings(ids)
	return ids
}

// threadIDsOf returns the checkpoint thread ids the job names, plus the ones
// derived from the affected runs' authoritative `runs.checkpoint_thread_id`.
func threadIDsOf(ctx context.Context, db *sql.DB, details map[string]any, runIDs []string) ([]string, error) {
	seen := make(map[string]bool)
	ids := make([]string, 0, 8)
	for _, id := range stringList(scopeRaw(details, ScopeCheckpoints)["checkpoint_thread_ids"]) {
		if uuid.Valid(id) && !seen[id] {
			seen[id] = true
			ids = append(ids, id)
		}
	}
	if len(runIDs) > 0 {
		rows, err := db.QueryContext(ctx, `
			SELECT checkpoint_thread_id::text
			  FROM platform.runs
			 WHERE id = ANY($1::uuid[])`, runIDs)
		if err != nil {
			return nil, fmt.Errorf("reading the run checkpoint threads: %w", err)
		}
		defer rows.Close()
		for rows.Next() {
			var id string
			if err := rows.Scan(&id); err != nil {
				return nil, fmt.Errorf("scanning a run checkpoint thread: %w", err)
			}
			if uuid.Valid(id) && !seen[id] {
				seen[id] = true
				ids = append(ids, id)
			}
		}
		if err := rows.Err(); err != nil {
			return nil, fmt.Errorf("reading the run checkpoint threads: %w", err)
		}
	}
	sort.Strings(ids)
	return ids, nil
}

// scopeKeyOf derives the stable sha256 identity of one copy-class record.
func scopeKeyOf(scope Scope, copyClass string, reference map[string]any) (string, error) {
	canonical, err := jcs.Canonicalize(jsonNative(map[string]any{
		"scope":      string(scope),
		"copy_class": copyClass,
		"reference":  reference,
	}))
	if err != nil {
		return "", fmt.Errorf("canonicalising the copy scope key: %w", err)
	}
	return jcs.SHA256Hex([]byte(canonical)), nil
}

// Run executes one copy-cleanup pass. It processes each selected job
// independently; a job failure is recorded and does not abort the pass.
func (cleaner *CopyCleaner) Run(ctx context.Context) (CopyPassReport, []CopyClassOutcome, error) {
	now := time.Now
	if cleaner.Now != nil {
		now = cleaner.Now
	}
	limit := cleaner.Limit
	if limit <= 0 {
		limit = 100
	}
	thirdParty := make([]string, 0, len(cleaner.ThirdPartyComponents))
	for _, component := range cleaner.ThirdPartyComponents {
		if trimmed := strings.TrimSpace(component); trimmed != "" {
			thirdParty = append(thirdParty, trimmed)
		}
	}
	sort.Strings(thirdParty)
	report := CopyPassReport{
		Status:          "ok",
		SandboxTempRoot: cleaner.SandboxRoot,
		SandboxAge:      cleaner.SandboxAge.String(),
		ThirdParty:      thirdParty,
		CheckpointLock:  checkpointLockBudget.String(),
		Note: "T072 copy inventory: checkpoints/summaries, sandbox temp, third-party history, " +
			"and the backup deletion ledger. T071 owns the object/index/cache purge; " +
			"vendor copies, downloaded copies and backup media are recorded as not erasable.",
	}
	if thirdParty == nil {
		report.ThirdParty = []string{}
	}
	report.AttemptedJobIDs = []string{}

	ids, err := cleaner.candidateJobs(ctx, limit)
	if err != nil {
		return report, nil, err
	}
	outcomes := make([]CopyClassOutcome, 0, len(ids)*8)
	for _, jobID := range ids {
		jobOutcomes, jobErr := cleaner.processJob(ctx, jobID, thirdParty, now())
		for index := range jobOutcomes {
			jobOutcomes[index].JobID = jobID
		}
		outcomes = append(outcomes, jobOutcomes...)
		report.AttemptedJobIDs = append(report.AttemptedJobIDs, jobID)
		if jobErr != nil {
			// A failure already recorded per scope is reported through the ledger and
			// the job row; an infrastructure error aborts the pass so the operator sees it.
			var recorded *recordedCopyFailure
			if errors.As(jobErr, &recorded) {
				continue
			}
			return report, outcomes, jobErr
		}
	}
	// Re-read the attempted jobs for the summary counts so they describe the
	// committed rows, never an in-memory guess.
	for _, jobID := range report.AttemptedJobIDs {
		job, err := readCopyJob(ctx, cleaner.DB, jobID)
		if err != nil {
			return report, outcomes, err
		}
		if job == nil {
			continue
		}
		report.Jobs++
		if job.State == StateCompleted {
			report.JobsCompleted++
		}
	}
	clearedScopes := make(map[string]bool, len(outcomes))
	failedScopes := make(map[string]bool, len(outcomes))
	for _, outcome := range outcomes {
		key := outcome.JobID + "|" + string(outcome.Scope)
		switch outcome.Outcome {
		case "cleared":
			report.CopiesCleared++
			clearedScopes[key] = true
		case "absent":
			report.CopiesAbsent++
			clearedScopes[key] = true
		case "external":
			report.CopiesExternal++
			clearedScopes[key] = true
		case "failed":
			failedScopes[key] = true
			delete(clearedScopes, key)
		}
	}
	report.ScopesCleared = len(clearedScopes)
	report.ScopesFailed = len(failedScopes)
	if cleaner.Logf != nil {
		cleaner.Logf("copies: jobs=%d completed=%d cleared=%d failed=%d",
			report.Jobs, report.JobsCompleted, report.CopiesCleared, report.ScopesFailed)
	}
	return report, outcomes, nil
}

// recordedCopyFailure marks a failure that was already written to the ledger/job;
// the pass continues with the next job.
type recordedCopyFailure struct {
	jobID string
	code  string
}

func (e *recordedCopyFailure) Error() string {
	return "copy cleanup recorded a failure for job " + e.jobID + ": " + e.code
}

func (cleaner *CopyCleaner) candidateJobs(ctx context.Context, limit int) ([]string, error) {
	scopes := make([]string, 0, len(CopyScopes))
	for _, scope := range CopyScopes {
		scopes = append(scopes, string(scope))
	}
	rows, err := cleaner.DB.QueryContext(ctx, `
		SELECT id::text
		  FROM platform.deletion_jobs
		 WHERE state IN ('pending', 'failed')
		   AND remaining_scopes ?| $1::text[]
		 ORDER BY created_at, id
		 LIMIT $2`, scopes, limit)
	if err != nil {
		return nil, fmt.Errorf("reading the deletion jobs owed by T072: %w", err)
	}
	defer rows.Close()
	ids := make([]string, 0, 16)
	for rows.Next() {
		var id string
		if err := rows.Scan(&id); err != nil {
			return nil, fmt.Errorf("scanning a deletion job id: %w", err)
		}
		ids = append(ids, id)
	}
	return ids, rows.Err()
}

func readCopyJob(ctx context.Context, db *sql.DB, jobID string) (*copyJob, error) {
	job := &copyJob{}
	var (
		remaining, completed, details []byte
	)
	err := db.QueryRowContext(ctx, `
		SELECT id::text, resource_type, resource_id::text, deletion_revision, state,
		       remaining_scopes, completed_scopes, scope_details
		  FROM platform.deletion_jobs
		 WHERE id = $1::uuid`, jobID,
	).Scan(
		&job.ID, &job.ResourceType, &job.ResourceID, &job.DeletionRevision,
		&job.State, &remaining, &completed, &details,
	)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("reading deletion job %s: %w", jobID, err)
	}
	if err := json.Unmarshal(remaining, &job.Remaining); err != nil {
		return nil, fmt.Errorf("decoding the remaining scopes: %w", err)
	}
	if err := json.Unmarshal(completed, &job.Completed); err != nil {
		return nil, fmt.Errorf("decoding the completed scopes: %w", err)
	}
	if err := json.Unmarshal(details, &job.ScopeDetails); err != nil {
		return nil, fmt.Errorf("decoding the deletion scope details: %w", err)
	}
	if job.ScopeDetails == nil {
		job.ScopeDetails = map[string]any{}
	}
	return job, nil
}

// processJob runs the four reducers for one job and records their evidence in one
// transaction (or records a failure when the transaction itself cannot run).
func (cleaner *CopyCleaner) processJob(
	ctx context.Context, jobID string, thirdParty []string, now time.Time,
) ([]CopyClassOutcome, error) {
	outcomes := make([]CopyClassOutcome, 0, 8)
	// Phase A: read-only planning.
	job, err := readCopyJob(ctx, cleaner.DB, jobID)
	if err != nil {
		return outcomes, err
	}
	if job == nil || job.State == StateCompleted {
		return outcomes, nil
	}
	owed := make([]Scope, 0, len(CopyScopes))
	for _, scope := range CopyScopes {
		if containsScopeString(job.Remaining, scope) {
			owed = append(owed, scope)
		}
	}
	if len(owed) == 0 {
		return outcomes, nil
	}
	runIDs := runIDsOf(job.ScopeDetails)
	threadIDs, err := threadIDsOf(ctx, cleaner.DB, job.ScopeDetails, runIDs)
	if err != nil {
		return outcomes, err
	}

	// Phase B: the filesystem part of the sandbox scope (for every owed scope set
	// that contains it), computed before the evidence transaction. The result is
	// recorded by the evidence transaction; a removal failure is an outcome of the
	// sandbox scope, not an aborted pass.
	var sandboxOutcome *CopyClassOutcome
	if containsScopeNamed(owed, ScopeSandboxTemp) {
		outcome, _ := cleaner.cleanSandboxTemp(ctx, runIDs, now)
		sandboxOutcome = &outcome
	}

	// Phase C: the evidence transaction: DB deletions, ledger entries, job row.
	tx, err := cleaner.DB.BeginTx(ctx, nil)
	if err != nil {
		return outcomes, fmt.Errorf("starting the copy evidence transaction: %w", err)
	}
	defer func() { _ = tx.Rollback() }()

	locked, err := readCopyJobForUpdate(ctx, tx, jobID)
	if err != nil {
		return outcomes, err
	}
	if locked == nil || locked.State == StateCompleted {
		return outcomes, nil
	}
	owedNow := make([]Scope, 0, len(CopyScopes))
	for _, scope := range CopyScopes {
		if containsScopeString(locked.Remaining, scope) {
			owedNow = append(owedNow, scope)
		}
	}
	if len(owedNow) == 0 {
		return outcomes, nil
	}

	transactionOutcomes := make([]CopyClassOutcome, 0, 8)
	failedScope := Scope("")
	failureCode := ""
	failureReason := ""

	for _, scope := range owedNow {
		switch scope {
		case ScopeCheckpoints:
			checkpointOutcomes, code, reason, err := cleaner.cleanCheckpointsTx(ctx, tx, locked, threadIDs, runIDs)
			if err != nil {
				_ = tx.Rollback()
				return cleaner.recordFailure(ctx, jobID, ScopeCheckpoints, code, reason, outcomes)
			}
			transactionOutcomes = append(transactionOutcomes, checkpointOutcomes...)
		case ScopeSandboxTemp:
			outcome, failure := cleaner.resultSandboxTemp(ctx, locked, runIDs, sandboxOutcome)
			transactionOutcomes = append(transactionOutcomes, outcome)
			if failure != "" {
				failedScope, failureCode, failureReason = scope, failure, "sandbox_temp_remove_failed"
			}
		case ScopeThirdPartyHistory:
			thirdPartyOutcomes, failure := cleaner.thirdPartyOutcomes(ctx, locked, runIDs, thirdParty)
			transactionOutcomes = append(transactionOutcomes, thirdPartyOutcomes...)
			if failure != "" {
				failedScope, failureCode, failureReason = scope, failure, "third_party_copy_check_failed"
			}
		case ScopeBackupCatalog:
			transactionOutcomes = append(transactionOutcomes,
				cleaner.backupCatalogOutcomes(locked, runIDs)...)
		}
	}

	// Append every class record and compute the job's new scope sets.
	clearedScopes := make([]Scope, 0, len(owedNow))
	for _, outcome := range transactionOutcomes {
		if err := outcome.appendTx(ctx, tx, locked); err != nil {
			return outcomes, err
		}
	}
	for _, scope := range owedNow {
		scopeCleared := true
		for _, outcome := range transactionOutcomes {
			if outcome.Scope == scope && outcome.Outcome == "failed" {
				scopeCleared = false
			}
		}
		if scopeCleared && scope != failedScope {
			clearedScopes = append(clearedScopes, scope)
		}
	}
	jobState := StatePending
	errorCode := ""
	if failedScope != "" {
		jobState = StateFailed
		errorCode = failureCode
	}
	if err := applyScopeProgressTx(ctx, tx, locked, clearedScopes, jobState, errorCode); err != nil {
		return outcomes, err
	}
	if err := tx.Commit(); err != nil {
		return outcomes, fmt.Errorf("committing the copy evidence: %w", err)
	}
	outcomes = append(outcomes, transactionOutcomes...)
	if failedScope != "" {
		if cleaner.Logf != nil {
			cleaner.Logf("copies: job %s scope %s failed (%s)", jobID, failedScope, failureCode)
		}
		return outcomes, &recordedCopyFailure{jobID: jobID, code: failureCode + ":" + failureReason}
	}
	return outcomes, nil
}

// recordFailure records one scope failure in a fresh transaction (the evidence
// transaction rolled back, e.g. because a locked checkpoint table refused the
// DELETE). The scope stays in remaining_scopes and the job carries the code.
func (cleaner *CopyCleaner) recordFailure(
	ctx context.Context, jobID string, scope Scope, code, reason string, outcomes []CopyClassOutcome,
) ([]CopyClassOutcome, error) {
	if code == "" {
		code = "copy_scope_failed"
		if scope == ScopeCheckpoints {
			code = "copy_checkpoint_failed"
		}
	}
	if reason == "" {
		reason = "copy_scope_failed"
	}
	scopeName := string(scope)
	failure := CopyClassOutcome{
		Scope:      scope,
		CopyClass:  string(scope),
		Outcome:    "failed",
		Reason:     reason,
		FailureErr: code,
		Record:     map[string]any{"scope": scopeName},
	}
	if scope == ScopeCheckpoints {
		failure.CopyClass = CopyCheckpoint
	}
	key, err := scopeKeyOf(scope, failure.CopyClass, map[string]any{"job": jobID, "scope": scopeName})
	if err == nil {
		failure.ScopeKey = key
	}
	tx, err := cleaner.DB.BeginTx(ctx, nil)
	if err != nil {
		return outcomes, err
	}
	defer func() { _ = tx.Rollback() }()
	locked, err := readCopyJobForUpdate(ctx, tx, jobID)
	if err != nil {
		return outcomes, err
	}
	if locked == nil {
		return outcomes, nil
	}
	if err := failure.appendTx(ctx, tx, locked); err != nil {
		return outcomes, err
	}
	if err := applyScopeProgressTx(ctx, tx, locked, nil, StateFailed, code); err != nil {
		return outcomes, err
	}
	if err := tx.Commit(); err != nil {
		return outcomes, err
	}
	outcomes = append(outcomes, failure)
	return outcomes, &recordedCopyFailure{jobID: jobID, code: code}
}

// cleanCheckpointsTx deletes the named checkpoint threads and returns the
// checkpoint + summary copy-class records. A missing schema is `absent`.
func (cleaner *CopyCleaner) cleanCheckpointsTx(
	ctx context.Context, tx *sql.Tx, job *copyJob, threadIDs, runIDs []string,
) ([]CopyClassOutcome, string, string, error) {
	checkpointKey, err := scopeKeyOf(ScopeCheckpoints, CopyCheckpoint,
		map[string]any{"job": job.ID, "threads": threadIDs})
	if err != nil {
		return nil, "", "", err
	}
	outcomes := make([]CopyClassOutcome, 0, 2)
	present, err := checkpointTables(ctx, tx)
	if err != nil {
		return nil, "copy_checkpoint_probe_failed", "checkpoint_table_probe_failed", err
	}
	if len(threadIDs) == 0 {
		outcomes = append(outcomes, CopyClassOutcome{
			Scope: ScopeCheckpoints, CopyClass: CopyCheckpoint, Outcome: "absent",
			Reason: "no_checkpoint_thread_reference", ScopeKey: checkpointKey,
			Record: map[string]any{"thread_ids": []string{}},
		})
	} else if !present["checkpoints"] {
		outcomes = append(outcomes, CopyClassOutcome{
			Scope: ScopeCheckpoints, CopyClass: CopyCheckpoint, Outcome: "absent",
			Reason: "checkpoint_schema_absent", ScopeKey: checkpointKey,
			Record: map[string]any{
				"thread_ids": threadIDs,
				"tables":     map[string]any{},
				"note":       "the checkpoint schema is out of band; the product must not create it",
			},
		})
	} else {
		if _, err := tx.ExecContext(ctx, "SET LOCAL lock_timeout = '"+checkpointLockBudget.String()+"'"); err != nil {
			return nil, "copy_checkpoint_locked", "checkpoint_lock_timeout_not_set", err
		}
		tables := map[string]any{}
		deletedTotal := 0
		for _, table := range []string{"checkpoints", "checkpoint_blobs", "checkpoint_writes"} {
			if !present[table] {
				tables[table] = map[string]any{"present": false, "deleted": 0}
				continue
			}
			result, err := tx.ExecContext(ctx,
				"DELETE FROM checkpoint."+table+" WHERE thread_id = ANY($1::text[])", threadIDs)
			if err != nil {
				return nil, "copy_checkpoint_delete_failed", "checkpoint_row_delete_failed", err
			}
			deleted, err := result.RowsAffected()
			if err != nil {
				return nil, "copy_checkpoint_delete_failed", "checkpoint_row_count_failed", err
			}
			deletedTotal += int(deleted)
			tables[table] = map[string]any{"present": true, "deleted": float64(deleted)}
		}
		outcomes = append(outcomes, CopyClassOutcome{
			Scope: ScopeCheckpoints, CopyClass: CopyCheckpoint, Outcome: "cleared",
			Reason: "checkpoint_thread_rows_deleted", ScopeKey: checkpointKey,
			Record: map[string]any{
				"thread_ids": threadIDs,
				"tables":     tables,
				"rows":       float64(deletedTotal),
			},
		})
	}

	// Summary copy class: this product stores no context-summary body. The
	// surfaces that could carry one are counted so the absent verdict is bounded
	// by real rows, never asserted from prose.
	summaryKey, err := scopeKeyOf(ScopeCheckpoints, CopySummary,
		map[string]any{"job": job.ID, "runs": runIDs})
	if err != nil {
		return nil, "", "", err
	}
	summary := CopyClassOutcome{
		Scope: ScopeCheckpoints, CopyClass: CopySummary, Outcome: "absent",
		Reason: "no_summary_store", ScopeKey: summaryKey,
		Record: map[string]any{
			"surfaces": map[string]any{},
			"note":     "no context-summary store exists; run events/attempt results are metadata-only",
		},
	}
	if len(runIDs) > 0 {
		surfaces, err := summarySurfaceCounts(ctx, tx, runIDs)
		if err != nil {
			return nil, "copy_summary_probe_failed", "summary_surface_probe_failed", err
		}
		summary.Record["surfaces"] = surfaces
	} else {
		summary.Reason = "no_run_reference"
	}
	outcomes = append(outcomes, summary)
	return outcomes, "", "", nil
}

// checkpointTables reports which framework tables are present in the schema.
func checkpointTables(ctx context.Context, tx *sql.Tx) (map[string]bool, error) {
	present := make(map[string]bool, 3)
	for _, table := range []string{"checkpoints", "checkpoint_blobs", "checkpoint_writes"} {
		var found sql.NullString
		if err := tx.QueryRowContext(ctx,
			"SELECT to_regclass($1)::text", "checkpoint."+table).Scan(&found); err != nil {
			return nil, fmt.Errorf("probing the checkpoint schema: %w", err)
		}
		present[table] = found.Valid
	}
	return present, nil
}

func summarySurfaceCounts(ctx context.Context, tx *sql.Tx, runIDs []string) (map[string]any, error) {
	counts := map[string]any{}
	var events, reported, outputs int
	if err := tx.QueryRowContext(ctx,
		"SELECT count(*) FROM platform.run_events WHERE run_id = ANY($1::uuid[])",
		runIDs).Scan(&events); err != nil {
		return nil, err
	}
	if err := tx.QueryRowContext(ctx,
		"SELECT count(*) FROM platform.run_attempts WHERE run_id = ANY($1::uuid[]) AND reported_result IS NOT NULL",
		runIDs).Scan(&reported); err != nil {
		return nil, err
	}
	if err := tx.QueryRowContext(ctx,
		"SELECT count(*) FROM platform.run_node_outputs WHERE run_id = ANY($1::uuid[])",
		runIDs).Scan(&outputs); err != nil {
		return nil, err
	}
	counts["run_events"] = float64(events)
	counts["run_attempts.reported_result"] = float64(reported)
	counts["run_node_outputs"] = float64(outputs)
	return counts, nil
}

// cleanSandboxTemp removes the stale host workspaces of this job's attempts. It
// runs outside the evidence transaction because the filesystem is not
// transactional; a removal failure is reported, never swallowed.
func (cleaner *CopyCleaner) cleanSandboxTemp(
	ctx context.Context, runIDs []string, now time.Time,
) (CopyClassOutcome, error) {
	outcome := CopyClassOutcome{Scope: ScopeSandboxTemp, CopyClass: CopySandboxTemp}
	key, err := scopeKeyOf(ScopeSandboxTemp, CopySandboxTemp, map[string]any{"runs": runIDs})
	if err != nil {
		return outcome, err
	}
	outcome.ScopeKey = key
	outcome.Record = map[string]any{"runs": runIDs}
	if len(runIDs) == 0 {
		outcome.Outcome = "absent"
		outcome.Reason = "no_run_reference"
		return outcome, nil
	}
	units, live, err := cleaner.attemptUnits(ctx, runIDs)
	if err != nil {
		outcome.Outcome = "failed"
		outcome.Reason = "sandbox_attempt_probe_failed"
		outcome.FailureErr = "copy_sandbox_probe_failed"
		return outcome, err
	}
	if len(units) == 0 {
		outcome.Outcome = "absent"
		outcome.Reason = "no_attempt_reference"
		return outcome, nil
	}
	// A live attempt owns its workspace; never delete it (a cancelled run's
	// supervisor may still be shutting down) and never claim the copy is gone.
	// The scope stays outstanding and the next pass retries it.
	liveUnits := make([]string, 0, len(units))
	for _, unit := range units {
		if live[unit] {
			liveUnits = append(liveUnits, unit)
		}
	}
	if len(liveUnits) > 0 {
		outcome.Outcome = "failed"
		outcome.Reason = "sandbox_owned_by_live_attempt"
		outcome.FailureErr = "copy_sandbox_live_attempt"
		outcome.Record["units"] = liveUnits
		return outcome, errors.New("a live attempt still owns a sandbox workspace")
	}
	entries, err := cleaner.removeStaleWorkspaces(units, now)
	if err != nil {
		outcome.Outcome = "failed"
		outcome.Reason = "sandbox_workspace_remove_failed"
		outcome.FailureErr = "copy_sandbox_remove_failed"
		outcome.Record["entries"] = entries
		return outcome, err
	}
	outcome.Record["entries"] = entries
	outcome.Record["units"] = units
	if len(entries) == 0 {
		outcome.Outcome = "absent"
		outcome.Reason = "no_leftover_workspace"
		return outcome, nil
	}
	bytes := 0.0
	for _, entry := range entries {
		bytes += entry["bytes"].(float64)
	}
	outcome.Record["bytes"] = bytes
	outcome.Outcome = "cleared"
	outcome.Reason = "run_workspace_leftovers_removed"
	return outcome, nil
}

// resultSandboxTemp converts the pre-transaction sandbox result into an outcome
// whose scope key is bound to the locked job.
func (cleaner *CopyCleaner) resultSandboxTemp(
	ctx context.Context, job *copyJob, runIDs []string, prepared *CopyClassOutcome,
) (CopyClassOutcome, string) {
	outcome := CopyClassOutcome{
		Scope: ScopeSandboxTemp, CopyClass: CopySandboxTemp, Outcome: "absent",
		Reason: "no_run_reference", Record: map[string]any{"runs": runIDs},
	}
	if prepared != nil {
		outcome = *prepared
	}
	key, err := scopeKeyOf(ScopeSandboxTemp, CopySandboxTemp,
		map[string]any{"job": job.ID, "runs": runIDs})
	if err == nil {
		outcome.ScopeKey = key
	}
	if outcome.Outcome == "failed" {
		if outcome.FailureErr == "" {
			outcome.FailureErr = "copy_sandbox_remove_failed"
		}
		return outcome, outcome.FailureErr
	}
	return outcome, ""
}

func (cleaner *CopyCleaner) attemptUnits(
	ctx context.Context, runIDs []string,
) ([]string, map[string]bool, error) {
	rows, err := cleaner.DB.QueryContext(ctx, `
		SELECT execution_unit_id,
		       bool_or(exited_at IS NULL AND revoked_at IS NULL) AS live
		  FROM platform.run_attempts
		 WHERE run_id = ANY($1::uuid[]) AND execution_unit_id IS NOT NULL
		 GROUP BY execution_unit_id
		 ORDER BY execution_unit_id`, runIDs)
	if err != nil {
		return nil, nil, fmt.Errorf("reading the deleted runs' execution units: %w", err)
	}
	defer rows.Close()
	units := make([]string, 0, 4)
	live := make(map[string]bool)
	for rows.Next() {
		var (
			unit   string
			isLive bool
		)
		if err := rows.Scan(&unit, &isLive); err != nil {
			return nil, nil, fmt.Errorf("scanning an execution unit: %w", err)
		}
		if strings.TrimSpace(unit) == "" {
			continue
		}
		units = append(units, unit)
		live[unit] = isLive
	}
	return units, live, rows.Err()
}

// removeStaleWorkspaces removes `orchara-sandbox-<unit>-*` directories whose
// name is one of the job's units and whose modification time is older than the
// age gate. It verifies the removal on the real filesystem.
func (cleaner *CopyCleaner) removeStaleWorkspaces(
	units []string, now time.Time,
) ([]map[string]any, error) {
	entries := make([]map[string]any, 0, 4)
	if strings.TrimSpace(cleaner.SandboxRoot) == "" {
		return entries, nil
	}
	rootEntries, err := os.ReadDir(cleaner.SandboxRoot)
	if errors.Is(err, os.ErrNotExist) {
		return entries, nil
	}
	if err != nil {
		return entries, fmt.Errorf("reading the sandbox staging root: %w", err)
	}
	prefixes := make([]string, 0, len(units))
	for _, unit := range units {
		prefixes = append(prefixes, sandboxDirPrefix+unit+"-")
	}
	for _, entry := range rootEntries {
		if !entry.IsDir() {
			continue
		}
		name := entry.Name()
		matched := false
		for _, prefix := range prefixes {
			if strings.HasPrefix(name, prefix) {
				matched = true
				break
			}
		}
		if !matched {
			continue
		}
		path := filepath.Join(cleaner.SandboxRoot, name)
		info, err := entry.Info()
		if err != nil {
			return entries, fmt.Errorf("reading a sandbox workspace: %w", err)
		}
		if cleaner.SandboxAge > 0 && now.Sub(info.ModTime()) < cleaner.SandboxAge {
			continue
		}
		bytes := workspaceBytes(path)
		item := map[string]any{"name": name, "bytes": float64(bytes), "removed": false}
		if err := os.RemoveAll(path); err != nil {
			entries = append(entries, item)
			return entries, fmt.Errorf("removing a sandbox workspace: %w", err)
		}
		if _, err := os.Stat(path); err == nil {
			entries = append(entries, item)
			return entries, errors.New("the sandbox workspace is still present after removal")
		}
		item["removed"] = true
		entries = append(entries, item)
	}
	return entries, nil
}

func workspaceBytes(root string) int64 {
	var total int64
	_ = filepath.Walk(root, func(_ string, info os.FileInfo, err error) error {
		if err != nil || info == nil || info.IsDir() {
			return nil
		}
		total += info.Size()
		return nil
	})
	return total
}

// thirdPartyOutcomes records the third-party history class and the vendor copy
// declaration. The default deployment has no third-party memory component
// (T066's committed decision is do_not_adopt), so the class is absent with a
// reason; a configured component is external and not erased by the platform.
//
// A failed model-call check is a failed check, not evidence that there was
// nothing to look for: it is returned as a failure code so the vendor class is
// recorded `failed` and the job keeps the scope (the same visibility rule the
// other copy classes follow).
func (cleaner *CopyCleaner) thirdPartyOutcomes(
	ctx context.Context, job *copyJob, runIDs []string, components []string,
) ([]CopyClassOutcome, string) {
	key, _ := scopeKeyOf(ScopeThirdPartyHistory, CopyThirdParty,
		map[string]any{"job": job.ID, "components": components})
	outcome := CopyClassOutcome{
		Scope: ScopeThirdPartyHistory, CopyClass: CopyThirdParty, ScopeKey: key,
		Outcome: "absent", Reason: "no_third_party_component_deployed",
		Record: map[string]any{"components": components},
	}
	if len(components) > 0 {
		outcome.Outcome = "external"
		outcome.Reason = "component_history_outside_platform_control"
		outcome.Record["note"] = "vendor copy cannot be erased immediately; recorded instead of claimed"
	}
	outcomes := []CopyClassOutcome{outcome}

	vendorKey, _ := scopeKeyOf(ScopeThirdPartyHistory, CopyVendor,
		map[string]any{"job": job.ID, "runs": runIDs})
	vendor := CopyClassOutcome{
		Scope: ScopeThirdPartyHistory, CopyClass: CopyVendor, ScopeKey: vendorKey,
		Record: map[string]any{"runs": runIDs},
	}
	calls, countErr := cleaner.modelCallCount(ctx, runIDs)
	vendor.Outcome, vendor.Reason = modelCallCopyOutcome(calls, countErr)
	if countErr != nil {
		vendor.FailureErr = "copy_vendor_count_failed"
		return append(outcomes, vendor), "copy_vendor_count_failed"
	}
	if calls > 0 {
		vendor.Record["model_calls"] = float64(calls)
	}
	outcomes = append(outcomes, vendor)
	return outcomes, ""
}

// modelCallCopyOutcome classifies the vendor-copy check from the model-call count.
// The error branch is what keeps "the query failed" distinguishable from "the
// path does not exist": both used to collapse into `absent`.
func modelCallCopyOutcome(calls int, countErr error) (outcome, reason string) {
	switch {
	case countErr != nil:
		return "failed", "model_call_count_failed"
	case calls > 0:
		return "external", "model_provider_may_retain_prompts"
	default:
		return "absent", "no_external_model_path"
	}
}

func (cleaner *CopyCleaner) modelCallCount(ctx context.Context, runIDs []string) (int, error) {
	if len(runIDs) == 0 {
		return 0, nil
	}
	var calls int
	if err := cleaner.DB.QueryRowContext(ctx, `
		SELECT count(*) FROM platform.operations
		 WHERE run_id = ANY($1::uuid[]) AND capability_kind = 'model'`,
		runIDs).Scan(&calls); err != nil {
		return 0, err
	}
	return calls, nil
}

// backupCatalogOutcomes registers the deletion decision in the independent
// ledger and declares the genuinely not-erasable copies (downloaded copy, backup
// media). `backup_catalog` itself is cleared because the registration succeeded.
func (cleaner *CopyCleaner) backupCatalogOutcomes(job *copyJob, runIDs []string) []CopyClassOutcome {
	catalogKey, _ := scopeKeyOf(ScopeBackupCatalog, CopyBackupCatalog, map[string]any{"job": job.ID})
	outcomes := []CopyClassOutcome{{
		Scope: ScopeBackupCatalog, CopyClass: CopyBackupCatalog, ScopeKey: catalogKey,
		Outcome: "cleared", Reason: "deletion_registered_in_ledger",
		Record: map[string]any{
			"retention_days": float64(backupRetentionDays),
			"ledger":         "platform.deletion_ledger",
		},
	}}
	downloadedKey, _ := scopeKeyOf(ScopeBackupCatalog, CopyDownloaded, map[string]any{"job": job.ID})
	outcomes = append(outcomes, CopyClassOutcome{
		Scope: ScopeBackupCatalog, CopyClass: CopyDownloaded, ScopeKey: downloadedKey,
		Outcome: "external", Reason: "signed_download_outside_platform_control",
		Record: map[string]any{
			"note": "an already-downloaded copy cannot be recalled (SECURITY.md §51)",
		},
	})
	mediaKey, _ := scopeKeyOf(ScopeBackupCatalog, CopyBackupMedia, map[string]any{"job": job.ID})
	outcomes = append(outcomes, CopyClassOutcome{
		Scope: ScopeBackupCatalog, CopyClass: CopyBackupMedia, ScopeKey: mediaKey,
		Outcome: "external", Reason: "backup_media_retention",
		Record: map[string]any{
			"retention_days": float64(backupRetentionDays),
			"note":           "backup media keeps pre-deletion copies until retention expiry",
			"runs":           runIDs,
		},
	})
	return outcomes
}

// readCopyJobForUpdate locks one job for the evidence transaction.
func readCopyJobForUpdate(ctx context.Context, tx *sql.Tx, jobID string) (*copyJob, error) {
	job := &copyJob{}
	var remaining, completed, details []byte
	err := tx.QueryRowContext(ctx, `
		SELECT id::text, resource_type, resource_id::text, deletion_revision, state,
		       remaining_scopes, completed_scopes, scope_details
		  FROM platform.deletion_jobs
		 WHERE id = $1::uuid
		 FOR UPDATE`, jobID,
	).Scan(
		&job.ID, &job.ResourceType, &job.ResourceID, &job.DeletionRevision,
		&job.State, &remaining, &completed, &details,
	)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("locking deletion job %s: %w", jobID, err)
	}
	if err := json.Unmarshal(remaining, &job.Remaining); err != nil {
		return nil, fmt.Errorf("decoding the remaining scopes: %w", err)
	}
	if err := json.Unmarshal(completed, &job.Completed); err != nil {
		return nil, fmt.Errorf("decoding the completed scopes: %w", err)
	}
	if err := json.Unmarshal(details, &job.ScopeDetails); err != nil {
		return nil, fmt.Errorf("decoding the deletion scope details: %w", err)
	}
	return job, nil
}

// ClearScopesTx is the shared scope-clearing rule of the F11 deletion model.
//
// T071's `delete_content` handler and T072's copy pass both owe the same job-row
// transition: the cleared scopes leave `remaining_scopes`, enter
// `completed_scopes`, the attempt is counted, and the job becomes `completed`
// (with the `job_completed` ledger entry) when nothing remains. It is exported so
// a cleanup ticket in another package can call it instead of re-implementing the
// update; the caller supplies the scopes it just recorded evidence for.
//
// State is `pending` when scopes remain and `failed` when the caller is recording
// a failure; `completed` is always derived from an empty remaining set, never
// requested by the caller.
func ClearScopesTx(
	ctx context.Context, tx *sql.Tx, jobID string, cleared []Scope, state, errorCode string,
) error {
	job, err := readCopyJobForUpdate(ctx, tx, jobID)
	if err != nil {
		return err
	}
	if job == nil {
		return nil
	}
	return applyScopeProgressTx(ctx, tx, job, cleared, state, errorCode)
}

// applyScopeProgressTx is the one scope-clearing rule T071 and T072 share: remove
// the cleared scopes from `remaining_scopes`, append them to `completed_scopes`,
// keep a stable sort, count the attempt and complete the job when nothing remains.
func applyScopeProgressTx(
	ctx context.Context, tx *sql.Tx, job *copyJob,
	cleared []Scope, state, errorCode string,
) error {
	remaining := make([]string, 0, len(job.Remaining))
	for _, scope := range job.Remaining {
		keep := true
		for _, done := range cleared {
			if scope == string(done) {
				keep = false
			}
		}
		if keep {
			remaining = append(remaining, scope)
		}
	}
	completedSet := make(map[string]bool, len(job.Completed)+len(cleared))
	for _, scope := range job.Completed {
		completedSet[scope] = true
	}
	for _, scope := range cleared {
		completedSet[string(scope)] = true
	}
	completed := make([]string, 0, len(completedSet))
	for scope := range completedSet {
		completed = append(completed, scope)
	}
	sort.Strings(remaining)
	sort.Strings(completed)

	finalState := state
	var completedAt any
	if len(remaining) == 0 {
		finalState = StateCompleted
		completedAt = time.Now().UTC()
	}
	if finalState == StateCompleted && len(remaining) == 0 {
		// The job_completed record declares the non-erasable classes once per job.
		scopeName := string(ScopeBackupCatalog)
		record := map[string]any{
			"reason": "all_scopes_terminal",
			"scopes": completed,
			"non_erasable": []map[string]any{
				{"copy_class": CopyVendor, "reason": "provider_retention_outside_platform_control"},
				{"copy_class": CopyDownloaded, "reason": "already_downloaded"},
				{"copy_class": CopyBackupMedia, "reason": "backup_retention"},
			},
		}
		if _, err := AppendLedgerTx(ctx, tx, LedgerAppend{
			DeletionJobID:    job.ID,
			ResourceType:     job.ResourceType,
			ResourceID:       job.ResourceID,
			DeletionRevision: job.DeletionRevision,
			EntryKind:        EntryJobCompleted,
			Scope:            &scopeName,
			Record:           record,
		}); err != nil {
			return err
		}
	}
	if _, err := tx.ExecContext(ctx, `
		UPDATE platform.deletion_jobs
		   SET remaining_scopes = $2::jsonb,
		       completed_scopes = $3::jsonb,
		       state = $4,
		       error_code = NULLIF($5, ''),
		       completed_at = CASE WHEN $4 = 'completed' THEN COALESCE($6, now()) ELSE completed_at END,
		       started_at = COALESCE(started_at, now()),
		       attempt_count = attempt_count + 1,
		       updated_at = now()
		 WHERE id = $1::uuid`,
		job.ID, mustJSON(remaining), mustJSON(completed), finalState, errorCode, completedAt,
	); err != nil {
		return fmt.Errorf("recording the copy scope progress: %w", err)
	}
	return nil
}

func mustJSON(value any) string {
	encoded, err := json.Marshal(value)
	if err != nil {
		return "[]"
	}
	return string(encoded)
}

func containsScopeString(scopes []string, wanted Scope) bool {
	for _, scope := range scopes {
		if scope == string(wanted) {
			return true
		}
	}
	return false
}

func containsScopeNamed(scopes []Scope, wanted Scope) bool {
	for _, scope := range scopes {
		if scope == wanted {
			return true
		}
	}
	return false
}
