// Package handlers is the compile-time kind -> handler registry.
//
// It is a package of ordinary Go functions, not a dispatcher over strings: the
// only way to run one is to reference it here, and the maintenance client's
// --kinds flag can narrow the set but can never introduce a new one. No handler
// shells out, and no user-supplied text becomes a command or a handler name.
package handlers

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"io"
	"strings"

	"orchara/platform/internal/artifacts"
	"orchara/platform/internal/background"
	"orchara/platform/internal/objectstore"
	"orchara/platform/internal/uuid"
)

// Registry is the set of kinds this build can execute. Each registered handler
// also has an entry in the fixed retry table (internal/background/job.go); a
// kind that is absent here is never claimed, even if it is a whitelisted kind.
// T022 implemented artifact_finalize; T024 adds model_validate; T026 adds
// application_evaluate; T030 adds tool_run; T037 adds tool_build. The remaining
// whitelisted kinds stay
// ready until their ticket lands (T027/F03/F05/F07/F13A).
var Registry = background.Registry{
	background.KindArtifactFinalize:    ArtifactFinalize,
	background.KindModelValidate:       ModelValidate,
	background.KindApplicationEvaluate: ApplicationEvaluate,
	background.KindToolRun:             ToolRun,
	background.KindToolBuild:           ToolBuild,
	// T055: txt/md ingestion (parse -> chunk -> embed -> sealed chunk bodies).
	background.KindDocumentIngest: DocumentIngest,
}

// artifactFinalizeRow is what the finalize handler needs from
// platform.artifacts. The handler only reads; the server-side guard in
// background.Complete is the only writer of state/sha256, and the only code that
// ever moves bytes to the artifact's own object_key.
type artifactFinalizeRow struct {
	StagingKey     sql.NullString
	ExpectedSHA256 sql.NullString
	SizeBytes      sql.NullInt64
	State          string
	DeletedAt      sql.NullTime
	Revision       int64
	MediaType      string
	Purpose        string
}

// ArtifactFinalize copies the artifact's staging bytes to this attempt's own
// sealed key.
//
// It performs the handler half of IMPLEMENTATION-DETAILS §9: read staging,
// enforce the 20 MiB ceiling, compute the SHA-256 while streaming, write the
// sealed object, and report the outcome. It never activates the artifact and
// never writes a business row: activation is the server's guarded decision, and
// a handler that lied about a success still cannot make the artifact ready.
//
// The write target is fenced per attempt: it is derived from the job id and the
// live attempt token (objectstore.AttemptSealedKey), never the artifact's own
// object_key. A paused attempt that resumes after a newer attempt activated the
// artifact can therefore overwrite or delete only its own object; the active
// sealed bytes are moved into artifact.object_key exclusively by the completion
// guard. That fence is what keeps BACKGROUND-JOBS §3's "后续覆盖不影响 sealed
// 对象" true even when attempts overlap.
//
// The handler reads the artifact row with the maintenance database identity
// because T022 does not yet have the job-scoped artifact-status endpoint (T024
// owns it). The row is the real table and the read is the real state; only the
// transport is interim. See the report/ADR for this boundary.
func ArtifactFinalize(ctx context.Context, work background.HandlerWork) background.Outcome {
	if work.DB == nil || work.Store == nil {
		// The runtime refused to start without these; if a caller constructs the
		// work by hand, treat it as an infrastructure failure rather than panic.
		return background.Failed(background.CodeTransientInternal)
	}
	if work.Job == nil || !uuid.Valid(work.Job.ID) || !uuid.Valid(work.AttemptToken) {
		// Without a real (job, attempt) pair there is no name for this attempt's
		// object; refusing is safer than writing to a guessable key.
		return background.Failed(background.CodeTransientInternal)
	}
	var artifact artifactFinalizeRow
	err := work.DB.QueryRowContext(ctx, `
		SELECT staging_object_key, expected_sha256, size_bytes,
		       state, deleted_at, revision, media_type, purpose
		  FROM platform.artifacts
		 WHERE id = $1::uuid`, work.Job.TargetID,
	).Scan(
		&artifact.StagingKey, &artifact.ExpectedSHA256,
		&artifact.SizeBytes, &artifact.State, &artifact.DeletedAt, &artifact.Revision,
		&artifact.MediaType, &artifact.Purpose,
	)
	switch {
	case errors.Is(err, sql.ErrNoRows):
		return background.Failed(background.CodeSourceDeleted)
	case err != nil:
		return background.Failed(background.CodeTransientInternal)
	}
	if artifact.DeletedAt.Valid {
		return background.Failed(background.CodeSourceDeleted)
	}
	if artifact.State != "scanning" {
		return background.Failed(background.CodeArtifactStateNotScanning)
	}
	if work.Job.ExpectedRevision != nil && *work.Job.ExpectedRevision != artifact.Revision {
		return background.Failed(background.CodeSourceChanged)
	}
	if !artifact.StagingKey.Valid || strings.TrimSpace(artifact.StagingKey.String) == "" {
		return background.Failed(background.CodeArtifactStagingMissing)
	}
	expectedSHA := strings.ToLower(strings.TrimSpace(nullString(artifact.ExpectedSHA256)))
	if !isSHA256(expectedSHA) {
		return background.Failed(background.CodeArtifactExpectedHashMiss)
	}

	var declaredSize *int64
	if artifact.SizeBytes.Valid {
		size := artifact.SizeBytes.Int64
		declaredSize = &size
	}
	attemptKey := objectstore.AttemptSealedKey(work.Job.ID, work.AttemptToken)
	code, err := sealAttemptObject(
		work.Store, strings.TrimSpace(artifact.StagingKey.String), attemptKey, expectedSHA, declaredSize,
	)
	switch {
	case err != nil:
		return background.Failed(background.CodeTransientStorageIO)
	case code != "":
		return finalizeFailure(ctx, work, artifact, code)
	}
	if !artifacts.IsUserUploadPurpose(artifact.Purpose) {
		// Reports/logs/receipts are produced by trusted server handlers and keep
		// their own shape rules; T028 admission applies to user uploads.
		return background.Succeeded()
	}

	// T028: after hashing/sealing the attempt object, inspect the real bytes and
	// persist the admission fact. The declared media type is never trusted: the
	// sniffed format is compared with the shape the declaration promises.
	content, err := readAttemptObject(work.Store, attemptKey)
	if err != nil {
		return background.Failed(background.CodeTransientStorageIO)
	}
	admission := artifacts.InspectUploadForPurpose(artifact.MediaType, artifact.Purpose, content)
	if err := recordAdmission(ctx, work.DB, work.Job.TargetID, admission); err != nil {
		return background.Failed(background.CodeTransientInternal)
	}
	if admission.Verdict != artifacts.VerdictAccepted {
		// Reject: remove only this attempt's fenced object and let the completion
		// guard see a business failure. The guard quarantines; this handler never
		// writes a business row.
		_ = work.Store.Delete(attemptKey)
		return background.Failed(background.CodeArtifactFormatRejected)
	}
	return background.Succeeded()
}

// finalizeFailure records the admission outcome of a business failure that was
// discovered before content inspection (missing staging, oversized object,
// declared size/hash mismatch) and reports the stable code.
//
// A user upload whose staging object is missing gets the T028-specific
// upload-incomplete code (quarantine-implying) instead of T022's
// artifact_staging_missing, whose documented meaning is "job fails, artifact
// stays scanning". The two meanings must not be merged.
func finalizeFailure(
	ctx context.Context,
	work background.HandlerWork,
	artifact artifactFinalizeRow,
	code string,
) background.Outcome {
	if !artifacts.IsUserUploadPurpose(artifact.Purpose) {
		return background.Failed(code)
	}
	failure := code
	if code == background.CodeArtifactStagingMissing {
		failure = background.CodeArtifactUploadIncomplete
	}
	declared := strings.TrimSpace(artifact.MediaType)
	if declared == "" {
		declared = "application/octet-stream"
	}
	admission := artifacts.Admission{
		DeclaredMediaType: declared,
		Verdict:           artifacts.VerdictRejected,
		Reason:            failure,
	}
	if err := recordAdmission(ctx, work.DB, work.Job.TargetID, admission); err != nil {
		return background.Failed(background.CodeTransientInternal)
	}
	return background.Failed(failure)
}

// recordAdmission upserts the one admission row for an artifact. It is
// insert-once evidence: a retried attempt updates the same row instead of
// accumulating per-attempt facts. Reasonable empty sniff values are stored as
// NULL, which the table's CHECKs allow only for a rejection.
func recordAdmission(
	ctx context.Context,
	db *sql.DB,
	artifactID string,
	admission artifacts.Admission,
) error {
	if db == nil {
		return errors.New("recording an admission requires a database")
	}
	declared := strings.TrimSpace(admission.DeclaredMediaType)
	if declared == "" {
		declared = "application/octet-stream"
	}
	var (
		sniffed any
		format  any
		reason  any
	)
	if value := strings.TrimSpace(admission.SniffedMediaType); value != "" {
		sniffed = value
	}
	if value := strings.TrimSpace(string(admission.DetectedFormat)); value != "" {
		format = value
	}
	if value := strings.TrimSpace(admission.Reason); value != "" {
		reason = value
	}
	_, err := db.ExecContext(ctx, `
		INSERT INTO platform.artifact_admissions
			(artifact_id, declared_media_type, sniffed_media_type, detected_format, verdict, reason)
		VALUES ($1::uuid, $2, $3, $4, $5, $6)
		ON CONFLICT (artifact_id) DO UPDATE
		   SET declared_media_type = EXCLUDED.declared_media_type,
		       sniffed_media_type = EXCLUDED.sniffed_media_type,
		       detected_format = EXCLUDED.detected_format,
		       verdict = EXCLUDED.verdict,
		       reason = EXCLUDED.reason,
		       checked_at = now()`,
		artifactID, declared, sniffed, format, admission.Verdict, reason,
	)
	return err
}

// readAttemptObject reads this attempt's own sealed object for inspection. The
// read is capped at the platform ceiling: the handler must never inspect bytes
// larger than an artifact can hold.
func readAttemptObject(store *objectstore.Store, key string) ([]byte, error) {
	if store == nil {
		return nil, objectstore.ErrNotConfigured
	}
	reader, _, err := store.Open(key)
	if err != nil {
		return nil, err
	}
	defer func() { _ = reader.Close() }()
	content, err := io.ReadAll(io.LimitReader(reader, objectstore.MaxObjectBytes+1))
	if err != nil {
		return nil, err
	}
	if int64(len(content)) > objectstore.MaxObjectBytes {
		return nil, objectstore.ErrTooLarge
	}
	return content, nil
}

// sealAttemptObject writes the staging bytes to attemptKey and verifies the
// declared size and hash in the same pass. It returns a stable business failure
// code ("" on success) or an error for a storage failure the attempt may retry.
//
// A failed content check deletes the attempt object it just wrote: the retry
// gets a new attempt token and a new key, so keeping this one would only be an
// orphan. It never reads, writes or deletes another key, in particular not the
// artifact's active object_key.
func sealAttemptObject(
	store *objectstore.Store,
	stagingKey, attemptKey, expectedSHA string,
	declaredSize *int64,
) (string, error) {
	reader, _, err := store.Open(stagingKey)
	switch {
	case errors.Is(err, objectstore.ErrNotExist), errors.Is(err, objectstore.ErrInvalidKey):
		return background.CodeArtifactStagingMissing, nil
	case err != nil:
		return "", err
	}
	defer func() { _ = reader.Close() }()

	sealed, err := store.PutSealed(attemptKey, reader, objectstore.MaxObjectBytes)
	switch {
	case errors.Is(err, objectstore.ErrTooLarge):
		return background.CodeArtifactTooLarge, nil
	case errors.Is(err, objectstore.ErrInvalidKey):
		// attemptKey is derived, never caller-supplied; an invalid key is an
		// internal defect. Reporting it as transient keeps the attempt from
		// silently succeeding while a fix is deployed.
		return "", err
	case err != nil:
		return "", err
	}
	if declaredSize != nil && sealed.Size != *declaredSize {
		// Never leave an unusable attempt object behind: the retry may still see
		// a repaired staging object, and the active object must stay untouched.
		_ = store.Delete(sealed.Key)
		return background.CodeArtifactSizeMismatch, nil
	}
	if sealed.SHA256 != expectedSHA {
		_ = store.Delete(sealed.Key)
		return background.CodeArtifactHashMismatch, nil
	}
	return "", nil
}

func nullString(value sql.NullString) string {
	if !value.Valid {
		return ""
	}
	return value.String
}

func isSHA256(value string) bool {
	if len(value) != 64 {
		return false
	}
	for index := 0; index < len(value); index++ {
		char := value[index]
		if (char < '0' || char > '9') && (char < 'a' || char > 'f') {
			return false
		}
	}
	return true
}

// Describe is a small diagnostic used by the maintenance CLI's startup line.
func Describe() string {
	kinds := Registry.SupportedKinds()
	names := make([]string, 0, len(kinds))
	for _, kind := range kinds {
		names = append(names, string(kind))
	}
	return fmt.Sprintf("handlers=%s", strings.Join(names, ","))
}
