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
)

// Registry is the set of kinds this build can execute. T022 implements the one
// real handler the ticket owns (artifact_finalize); the other seven whitelisted
// kinds stay ready until the ticket that owns their handler lands (T024/T026).
var Registry = background.Registry{
	background.KindArtifactFinalize: ArtifactFinalize,
}

// artifactFinalizeRow is what the finalize handler needs from
// platform.artifacts. The handler only reads; the server-side guard in
// background.Complete is the only writer of state/sha256.
type artifactFinalizeRow struct {
	ObjectKey      string
	StagingKey     sql.NullString
	ExpectedSHA256 sql.NullString
	SizeBytes      sql.NullInt64
	State          string
	DeletedAt      sql.NullTime
	Revision       int64
}

// ArtifactFinalize copies the artifact's staging bytes to its preallocated
// sealed key.
//
// It performs the handler half of IMPLEMENTATION-DETAILS §9: read staging,
// enforce the 20 MiB ceiling, compute the SHA-256 while streaming, write the
// sealed object, and report the outcome. It never activates the artifact and
// never writes a business row: activation is the server's guarded decision, and
// a handler that lied about a success still cannot make the artifact ready.
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
	var artifact artifactFinalizeRow
	err := work.DB.QueryRowContext(ctx, `
		SELECT object_key, staging_object_key, expected_sha256, size_bytes,
		       state, deleted_at, revision
		  FROM platform.artifacts
		 WHERE id = $1::uuid`, work.Job.TargetID,
	).Scan(
		&artifact.ObjectKey, &artifact.StagingKey, &artifact.ExpectedSHA256,
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

	reader, _, err := work.Store.Open(strings.TrimSpace(artifact.StagingKey.String))
	switch {
	case errors.Is(err, objectstore.ErrNotExist):
		return background.Failed(background.CodeArtifactStagingMissing)
	case errors.Is(err, objectstore.ErrInvalidKey):
		return background.Failed(background.CodeArtifactStagingMissing)
	case err != nil:
		return background.Failed(background.CodeTransientStorageIO)
	}
	defer func() { _ = reader.Close() }()

	sealed, err := work.Store.PutSealed(
		strings.TrimSpace(artifact.ObjectKey), reader, objectstore.MaxObjectBytes,
	)
	switch {
	case errors.Is(err, objectstore.ErrTooLarge):
		return background.Failed(background.CodeArtifactTooLarge)
	case errors.Is(err, objectstore.ErrInvalidKey):
		// A stored object_key that is not a sealed key is a data defect; the
		// artifact can never be sealed by this handler.
		return background.Failed(background.CodeArtifactObjectMissing)
	case err != nil:
		return background.Failed(background.CodeTransientStorageIO)
	}

	if artifact.SizeBytes.Valid && sealed.Size != artifact.SizeBytes.Int64 {
		// Never leave an unusable sealed object behind: the retry may still see a
		// repaired staging object, and the orphan sweep would otherwise find it.
		_ = work.Store.Delete(sealed.Key)
		return background.Failed(background.CodeArtifactSizeMismatch)
	}
	if sealed.SHA256 != expectedSHA {
		_ = work.Store.Delete(sealed.Key)
		return background.Failed(background.CodeArtifactHashMismatch)
	}
	return background.Succeeded()
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
