package deletion

// F11/T076: the deletion/cleanup alert surface.
//
// OPERATIONS.md §36 sets the physical-cleanup target for a logical delete (24h)
// and §55 requires an alert on deletion backlog and failure count, while the
// data/retrieval paths keep refusing through the tombstone. This deployment has
// no metrics/OTel stack (ADR 0029), so the observable surface is one read-only
// operator command over the authoritative rows:
//
//	orchara-admin deletion alerts [--deadline 24h] [--limit 100]
//
// It answers three questions, from PostgreSQL, with no cache and no guess:
//
//  1. is a deletion job still owed work past the documented deadline
//     (`deletion_job_overdue`)?
//  2. did a deletion job fail (`deletion_job_failed`)?
//  3. did the `delete_content` purge job fail, time out (`cleanup_timeout`, the
//     T071 `delete_timeout` code) or stay unclaimed past the deadline
//     (`cleanup_stalled`)?
//
// The command writes nothing: it never advances a job, never deletes bytes and
// never touches a tombstone. A non-empty alert list is the process exit signal
// (cmd/admin maps it to exit 3); the JSON line is the machine-readable record.
//
// The report also carries the honest copy boundary: a platform cleanup job cannot
// erase a vendor's own store, backup media or a copy the user already downloaded.
// The T072 copy pass records those as external/not erasable in the deletion
// ledger; this surface repeats the boundary so an operator cannot read "no
// alerts" as "every copy on earth is gone".

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"sort"
	"strings"
	"time"
)

// Alert codes are a closed vocabulary. Operators alert on the code, never on the
// message text.
const (
	// AlertDeletionOverdue is a deletion job that has not completed within the
	// documented physical-cleanup deadline.
	AlertDeletionOverdue = "deletion_job_overdue"
	// AlertDeletionFailed is a deletion job whose evidence transaction recorded a
	// scope failure (a shared reference, a remaining index/cache row, ...).
	AlertDeletionFailed = "deletion_job_failed"
	// AlertCleanupStalled is a `delete_content` purge job that no maintenance
	// process claimed within the deadline.
	AlertCleanupStalled = "cleanup_stalled"
	// AlertCleanupFailed is a `delete_content` purge job whose handler/guard
	// refused for a reason retrying cannot fix.
	AlertCleanupFailed = "cleanup_failed"
	// AlertCleanupTimeout is a `delete_content` attempt that reported the retryable
	// T071 attempt timeout. The job is retried, but the deadline is at risk.
	AlertCleanupTimeout = "cleanup_timeout"
)

// DefaultCleanupDeadline is OPERATIONS.md §36's physical-cleanup target: 24h
// after the logical delete. A deployment may tighten it with --deadline.
const DefaultCleanupDeadline = 24 * time.Hour

// MaxAlertLimit bounds one pass so an operator command cannot scan an unbounded
// backlog.
const MaxAlertLimit = 500

// AlertBoundary is the honest copy boundary printed with every report. It is
// deliberately a constant: the statement must not drift per deployment.
const AlertBoundary = "a platform cleanup job cannot erase a third-party component's own store, " +
	"backup media, or a copy the user already downloaded; the T072 copy pass records those " +
	"as external/not_erasable in the deletion ledger, and this surface never claims otherwise"

// Alert is one operator-facing finding. It carries ids, codes and counts only --
// never a body, an object key, a hash or a sealed URL.
type Alert struct {
	Code            string   `json:"code"`
	DeletionID      string   `json:"deletion_id,omitempty"`
	BackgroundJobID string   `json:"background_job_id,omitempty"`
	ResourceType    string   `json:"resource_type,omitempty"`
	ResourceID      string   `json:"resource_id,omitempty"`
	State           string   `json:"state,omitempty"`
	ErrorCode       string   `json:"error_code,omitempty"`
	AgeSeconds      int64    `json:"age_seconds"`
	DeadlineSeconds int64    `json:"deadline_seconds"`
	AttemptCount    int      `json:"attempt_count"`
	RemainingScopes []string `json:"remaining_scopes,omitempty"`
}

// AlertsReport is the one JSON line the operator command prints.
type AlertsReport struct {
	Status    string `json:"status"` // ok | alert
	Deadline  string `json:"deadline"`
	CheckedAt string `json:"checked_at"`
	// JobsExamined counts the deletion jobs and purge jobs one pass looked at, so
	// "ok" is distinguishable from "there was nothing to look at".
	JobsExamined int     `json:"jobs_examined"`
	DeletionJobs int     `json:"deletion_jobs"`
	CleanupJobs  int     `json:"cleanup_jobs"`
	Alerts       []Alert `json:"alerts"`
	Boundary     string  `json:"boundary"`
}

// AlertsOptions configures one pass.
type AlertsOptions struct {
	// Deadline is how long after creation a job may still be owed. Zero means the
	// documented default.
	Deadline time.Duration
	// Limit bounds each query.
	Limit int
}

// LoadAlerts reads the deletion and purge-job state and returns the report. It is
// read-only; every decision comes from a committed row.
func LoadAlerts(ctx context.Context, db *sql.DB, options AlertsOptions) (AlertsReport, error) {
	deadline := options.Deadline
	if deadline <= 0 {
		deadline = DefaultCleanupDeadline
	}
	limit := options.Limit
	if limit <= 0 || limit > MaxAlertLimit {
		limit = MaxAlertLimit
	}
	report := AlertsReport{
		Status:   "ok",
		Deadline: deadline.String(),
		Alerts:   []Alert{},
		Boundary: AlertBoundary,
	}
	var checkedAt time.Time
	if err := db.QueryRowContext(ctx, `SELECT now()`).Scan(&checkedAt); err != nil {
		return report, fmt.Errorf("reading the database clock for the alert pass: %w", err)
	}
	report.CheckedAt = checkedAt.UTC().Format(time.RFC3339Nano)
	deadlineSeconds := int64(deadline.Seconds())

	deletionJobs, err := alertDeletionJobs(ctx, db, deadlineSeconds, limit)
	if err != nil {
		return report, err
	}
	report.DeletionJobs = len(deletionJobs)
	cleanupJobs, err := alertCleanupJobs(ctx, db, deadlineSeconds, limit)
	if err != nil {
		return report, err
	}
	report.CleanupJobs = len(cleanupJobs)
	report.JobsExamined = report.DeletionJobs + report.CleanupJobs

	alerts := make([]Alert, 0, 8)
	for _, job := range deletionJobs {
		base := Alert{
			DeletionID:      job.ID,
			ResourceType:    job.ResourceType,
			ResourceID:      job.ResourceID,
			State:           job.State,
			ErrorCode:       job.ErrorCode,
			AgeSeconds:      job.AgeSeconds,
			DeadlineSeconds: deadlineSeconds,
			AttemptCount:    job.AttemptCount,
			RemainingScopes: job.RemainingScopes,
		}
		if job.State == StateFailed {
			failed := base
			failed.Code = AlertDeletionFailed
			alerts = append(alerts, failed)
		}
		if job.AgeSeconds > deadlineSeconds {
			overdue := base
			overdue.Code = AlertDeletionOverdue
			alerts = append(alerts, overdue)
		}
	}
	for _, job := range cleanupJobs {
		alert := Alert{
			Code:            alertCleanupCode(job),
			DeletionID:      job.TargetID,
			BackgroundJobID: job.ID,
			State:           job.State,
			ErrorCode:       job.ErrorCode,
			AgeSeconds:      job.AgeSeconds,
			DeadlineSeconds: deadlineSeconds,
			AttemptCount:    job.AttemptCount,
		}
		if alert.Code == "" {
			continue
		}
		alerts = append(alerts, alert)
	}
	sort.Slice(alerts, func(i, j int) bool {
		if alerts[i].Code != alerts[j].Code {
			return alerts[i].Code < alerts[j].Code
		}
		if alerts[i].DeletionID != alerts[j].DeletionID {
			return alerts[i].DeletionID < alerts[j].DeletionID
		}
		return alerts[i].BackgroundJobID < alerts[j].BackgroundJobID
	})
	report.Alerts = alerts
	if len(alerts) > 0 {
		report.Status = "alert"
	}
	return report, nil
}

// alertDeletionJob is one row of the deletion-job alert query.
type alertDeletionJob struct {
	ID              string
	ResourceType    string
	ResourceID      string
	State           string
	ErrorCode       string
	AgeSeconds      int64
	AttemptCount    int
	RemainingScopes []string
}

// alertCleanupJob is one row of the `delete_content` alert query.
type alertCleanupJob struct {
	ID           string
	TargetID     string
	State        string
	ErrorCode    string
	AgeSeconds   int64
	AttemptCount int
}

func alertDeletionJobs(
	ctx context.Context, db *sql.DB, deadlineSeconds int64, limit int,
) ([]alertDeletionJob, error) {
	rows, err := db.QueryContext(ctx, `
		SELECT id::text, resource_type, resource_id::text, state,
		       COALESCE(error_code, ''), remaining_scopes::text, attempt_count,
		       GREATEST(0, floor(extract(epoch FROM now() - created_at)))::bigint
		  FROM platform.deletion_jobs
		 WHERE state <> 'completed'
		   AND (state = 'failed' OR created_at < now() - make_interval(secs => $1::double precision))
		 ORDER BY created_at, id
		 LIMIT $2`, float64(deadlineSeconds), limit)
	if err != nil {
		return nil, fmt.Errorf("reading the deletion jobs for the alert pass: %w", err)
	}
	defer func() { _ = rows.Close() }()
	jobs := make([]alertDeletionJob, 0, 4)
	for rows.Next() {
		var (
			job alertDeletionJob
			raw []byte
		)
		if err := rows.Scan(
			&job.ID, &job.ResourceType, &job.ResourceID, &job.State,
			&job.ErrorCode, &raw, &job.AttemptCount, &job.AgeSeconds,
		); err != nil {
			return nil, fmt.Errorf("scanning a deletion job for the alert pass: %w", err)
		}
		if job.RemainingScopes, err = decodeAlertScopes(raw); err != nil {
			return nil, err
		}
		jobs = append(jobs, job)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("reading the deletion jobs for the alert pass: %w", err)
	}
	return jobs, nil
}

func alertCleanupJobs(
	ctx context.Context, db *sql.DB, deadlineSeconds int64, limit int,
) ([]alertCleanupJob, error) {
	rows, err := db.QueryContext(ctx, `
		SELECT id::text, target_id::text, state, COALESCE(error_code, ''), attempt_count,
		       GREATEST(0, floor(extract(epoch FROM now() - created_at)))::bigint
		  FROM platform.background_jobs
		 WHERE kind = $3
		   AND (
		        state = 'failed'
		     OR (state IN ('ready', 'leased')
		         AND created_at < now() - make_interval(secs => $1::double precision))
		   )
		 ORDER BY created_at, id
		 LIMIT $2`, float64(deadlineSeconds), limit, string(KindDeleteContentBackground))
	if err != nil {
		return nil, fmt.Errorf("reading the purge jobs for the alert pass: %w", err)
	}
	defer func() { _ = rows.Close() }()
	jobs := make([]alertCleanupJob, 0, 4)
	for rows.Next() {
		var job alertCleanupJob
		if err := rows.Scan(
			&job.ID, &job.TargetID, &job.State, &job.ErrorCode,
			&job.AttemptCount, &job.AgeSeconds,
		); err != nil {
			return nil, fmt.Errorf("scanning a purge job for the alert pass: %w", err)
		}
		jobs = append(jobs, job)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("reading the purge jobs for the alert pass: %w", err)
	}
	return jobs, nil
}

// KindDeleteContentBackground is the background-job kind the purge handler is
// registered under (internal/background.KindDeleteContent). It is repeated here as
// a constant string so this package keeps no dependency on internal/background
// (the background completion guard imports this package).
const KindDeleteContentBackground = "delete_content"

// alertCleanupCode maps one purge job to its alert code ("" when it is healthy).
func alertCleanupCode(job alertCleanupJob) string {
	switch {
	case job.ErrorCode == "delete_timeout":
		return AlertCleanupTimeout
	case job.State == "failed":
		return AlertCleanupFailed
	case job.State == "ready" || job.State == "leased":
		return AlertCleanupStalled
	default:
		return ""
	}
}

func decodeAlertScopes(raw []byte) ([]string, error) {
	if len(raw) == 0 {
		return nil, nil
	}
	var names []string
	if err := json.Unmarshal(raw, &names); err != nil {
		return nil, fmt.Errorf("decoding a deletion job's remaining scopes for the alert pass: %w", err)
	}
	for index := range names {
		names[index] = strings.TrimSpace(names[index])
	}
	return names, nil
}

// AlertsExitCode is the process exit code that means "at least one alert". It is
// distinct from 1 (a command failure) and 2 (a usage problem) so a scheduler can
// tell "the pass ran and found an overdue deletion" from "the pass could not run".
const AlertsExitCode = 3
