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
	"strings"

	"orchara/platform/internal/background"
	"orchara/platform/internal/objectstore"
	"orchara/platform/internal/uuid"
)

// Registry is the set of kinds this build can execute. Each registered handler
// also has an entry in the fixed retry table (internal/background/job.go); a
// kind that is absent here is never claimed, even if it is a whitelisted kind.
// T022 implemented artifact_finalize; T024 adds model_validate; T026 adds
// application_evaluate. The remaining five whitelisted kinds stay ready until
// their ticket lands (T027/F03/F05/F07/F13A).
var Registry = background.Registry{
	background.KindArtifactFinalize:    ArtifactFinalize,
	background.KindModelValidate:       ModelValidate,
	background.KindApplicationEvaluate: ApplicationEvaluate,
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
		       state, deleted_at, revision
		  FROM platform.artifacts
		 WHERE id = $1::uuid`, work.Job.TargetID,
	).Scan(
		&artifact.StagingKey, &artifact.ExpectedSHA256,
		&artifact.SizeBytes, &artifact.State, &artifact.DeletedAt, &artifact.Revision,
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
		return background.Failed(code)
	}
	return background.Succeeded()
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
