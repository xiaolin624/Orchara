package background

// F05/T037: the tool_build completion guard.
//
// A tool build has two identities: the background job (delivery/retry) and the
// platform.tool_versions row (the build result). This file is the server half:
//
//   - Claim marks a submitted tool `building` in the same transaction that leases
//     the job (one attempt owns the work; a retry keeps it building).
//   - Complete, for a claimed success, re-reads the tool row under its lock,
//     checks the revision/source-hash CAS, requires the job-linked `sbom` and
//     `build_log` artifacts to be ready, re-reads their sealed bytes and computes
//     the authoritative content bundle digest. Only then does it set ready.
//   - A business failure (or a success whose facts cannot be proven) fails the
//     row with a safe code. A retryable failure leaves the row building for the
//     next attempt. A stale/revoked row is never overwritten.
//
// `image_digest` is the bundle digest: sha256:<hex> over the sorted source zip
// bytes, canonical dependency-lock bytes and SBOM bytes. It identifies a content
// bundle, **not** an OCI image; F15A builds images.

import (
	"bytes"
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"sort"
	"strings"

	"orchara/platform/internal/jcs"
	"orchara/platform/internal/objectstore"
)

// Build artifact bounds. The SBOM is metadata; the source is the same 20 MiB
// object ceiling every artifact uses.
const (
	MaxToolBuildSourceBytes = 20 << 20
	MaxToolBuildSBOMBytes   = 4 << 20
)

// safeToolBuildCodes is the closed vocabulary a failed build may store in
// tool_versions.build_error_code. An unknown submitted code is stored as
// build_failed, never verbatim.
var safeToolBuildCodes = map[string]bool{
	CodeBuildFailed:              true,
	CodeBuildSourceInvalid:       true,
	CodeBuildSourceChanged:       true,
	CodeBuildSandboxUnavailable:  true,
	CodeBuildTimeout:             true,
	CodeBuildCrashed:             true,
	CodeBuildOutputInvalid:       true,
	CodeBuildArtifactsInvalid:    true,
	CodeBuildArtifactInvalid:     true,
	CodeBuildDependenciesMissing: true,
	CodeTransientStorageIO:       true,
	CodeTransientInternal:        true,
	CodeLeaseExpired:             true,
}

func safeToolBuildCode(code string) string {
	if safeToolBuildCodes[code] {
		return code
	}
	return CodeBuildFailed
}

// markToolBuilding is called inside the claim transaction. The first attempt
// moves submitted -> building; a retry of the same job leaves it building. A
// stale row (revision moved, or no longer submitted/building) is left alone for
// the completion guard to reject.
func markToolBuilding(ctx context.Context, tx *sql.Tx, job *Job) error {
	if job.Kind != KindToolBuild {
		return nil
	}
	_, err := tx.ExecContext(ctx, `
		UPDATE platform.tool_versions
		   SET state = 'building',
		       build_started_at = COALESCE(build_started_at, now())
		 WHERE id = $1::uuid
		   AND state = 'submitted'
		   AND ($2::bigint IS NULL OR revision = $2::bigint)`,
		job.TargetID, job.ExpectedRevision)
	if err != nil {
		return fmt.Errorf("marking the tool build building: %w", err)
	}
	return nil
}

// failToolBuildForJobTx reconciles a terminal background-job failure with the
// tool row (the expired-lease sweep calls it; Complete's guard does the same for
// a submitted business failure). A retry never reaches it.
func failToolBuildForJobTx(
	ctx context.Context,
	tx *sql.Tx,
	kind Kind,
	targetID, errorCode string,
) error {
	if kind != KindToolBuild {
		return nil
	}
	return failToolBuildTx(ctx, tx, targetID, errorCode)
}

// failToolBuildTx marks a still-pending tool build failed. It never overwrites a
// row that already reached ready/revoked or whose revision moved.
func failToolBuildTx(ctx context.Context, tx *sql.Tx, toolID, errorCode string) error {
	if _, err := tx.ExecContext(ctx, `
		UPDATE platform.tool_versions
		   SET state = 'failed',
		       build_error_code = $2,
		       build_finished_at = now(),
		       revision = revision + 1
		 WHERE id = $1::uuid AND state IN ('submitted', 'building')`,
		toolID, safeToolBuildCode(errorCode)); err != nil {
		return fmt.Errorf("failing the tool build: %w", err)
	}
	return nil
}

// guardToolBuildCompletion is the one completion decision for KindToolBuild.
func guardToolBuildCompletion(
	ctx context.Context,
	c *Controller,
	tx *sql.Tx,
	job *Job,
	completion *Completion,
) error {
	switch completion.State {
	case StateSucceeded:
		failureCode, guardErr := c.acceptToolBuild(ctx, tx, job, completion)
		switch {
		case errors.Is(guardErr, errGuardTransient):
			completion.State = StateFailed
			completion.ErrorCode = CodeTransientStorageIO
		case guardErr != nil:
			return guardErr
		case failureCode != "":
			completion.State = StateFailed
			completion.ErrorCode = failureCode
			completion.ResultArtifactID = ""
		}
		if completion.State == StateFailed && !retryAllowed(job, completion.ErrorCode) {
			if err := recordBuildLogArtifactTx(ctx, tx, job); err != nil {
				return err
			}
			if err := failToolBuildTx(ctx, tx, job.TargetID, completion.ErrorCode); err != nil {
				return err
			}
		}
	case StateFailed:
		if !retryAllowed(job, completion.ErrorCode) {
			if err := recordBuildLogArtifactTx(ctx, tx, job); err != nil {
				return err
			}
			if err := failToolBuildTx(ctx, tx, job.TargetID, completion.ErrorCode); err != nil {
				return err
			}
		}
	case StateCancelled:
		if err := recordBuildLogArtifactTx(ctx, tx, job); err != nil {
			return err
		}
		if err := failToolBuildTx(ctx, tx, job.TargetID, CodeBuildFailed); err != nil {
			return err
		}
	}
	return nil
}

// recordBuildLogArtifactTx links the latest ready build_log artifact of this job
// to a failed tool row, so the owner (and an administrator) can diagnose the
// failure from the build-facts read. It never overwrites a recorded log.
func recordBuildLogArtifactTx(ctx context.Context, tx *sql.Tx, job *Job) error {
	result, err := tx.ExecContext(ctx, `
		UPDATE platform.tool_versions
		   SET build_log_artifact_id = (
		       SELECT a.id
		         FROM platform.artifacts AS a
		         JOIN platform.background_job_artifacts AS l
		           ON l.artifact_id = a.id AND l.background_job_id = $2::uuid
		        WHERE l.purpose = 'build_log'
		          AND a.owner_id = $3::uuid
		          AND a.state = 'ready'
		          AND a.deleted_at IS NULL
		        ORDER BY a.created_at DESC, a.id DESC
		        LIMIT 1
		   )
		 WHERE id = $1::uuid AND build_log_artifact_id IS NULL
		   AND state IN ('submitted', 'building')`,
		job.TargetID, job.ID, job.OwnerID)
	if err != nil {
		return fmt.Errorf("recording the failed tool build log: %w", err)
	}
	_ = result
	return nil
}

// acceptToolBuild verifies one claimed build success and, only when everything
// matches, commits state=ready in the caller's transaction. A non-empty failure
// code means "the claimed success is not proven"; it is turned into a failed
// attempt (and then a failed row) by the caller.
func (c *Controller) acceptToolBuild(
	ctx context.Context,
	tx *sql.Tx,
	job *Job,
	completion *Completion,
) (string, error) {
	var (
		state          string
		revision       int64
		sourceID       string
		sourceSHA      sql.NullString
		lockSHA        sql.NullString
		ownerID        string
		dependencyLock []byte
	)
	err := tx.QueryRowContext(ctx, `
		SELECT state, revision, source_artifact_id::text, source_sha256,
		       dependency_lock_sha256, owner_id::text, dependency_lock::text
		  FROM platform.tool_versions
		 WHERE id = $1::uuid
		 FOR UPDATE`, job.TargetID,
	).Scan(&state, &revision, &sourceID, &sourceSHA, &lockSHA, &ownerID, &dependencyLock)
	if errors.Is(err, sql.ErrNoRows) {
		return CodeBuildSourceChanged, nil
	}
	if err != nil {
		return "", fmt.Errorf("reading the tool version: %w", err)
	}
	expectedRevision := int64(1)
	if job.ExpectedRevision != nil {
		expectedRevision = *job.ExpectedRevision
	}
	expectedSourceSHA := ""
	if job.ExpectedSHA256 != nil {
		expectedSourceSHA = strings.ToLower(*job.ExpectedSHA256)
	}
	recordedSourceSHA := strings.ToLower(strings.TrimSpace(sourceSHA.String))
	if state != "building" || revision != expectedRevision ||
		sourceID != pointerText(job.InputArtifactID) ||
		recordedSourceSHA != expectedSourceSHA ||
		!strings.EqualFold(ownerID, job.OwnerID) ||
		!sourceSHA.Valid || !isSHA256Hex(recordedSourceSHA) {
		return CodeBuildSourceChanged, nil
	}
	if !lockSHA.Valid || !isSHA256Hex(strings.ToLower(lockSHA.String)) {
		return CodeBuildSourceChanged, nil
	}

	// The source artifact must still be the admitted ready upload this job was
	// created for. A tombstone or a content change can never produce a ready tool.
	var (
		sourceObjectKey string
		sourceState     string
		sourceDeleted   sql.NullTime
		sourceStoredSHA sql.NullString
	)
	err = tx.QueryRowContext(ctx, `
		SELECT object_key, state, deleted_at, sha256
		  FROM platform.artifacts
		 WHERE id = $1::uuid`, sourceID,
	).Scan(&sourceObjectKey, &sourceState, &sourceDeleted, &sourceStoredSHA)
	if errors.Is(err, sql.ErrNoRows) {
		return CodeBuildSourceChanged, nil
	}
	if err != nil {
		return "", fmt.Errorf("reading the tool source artifact: %w", err)
	}
	if sourceDeleted.Valid || sourceState != "ready" || !sourceStoredSHA.Valid ||
		strings.ToLower(strings.TrimSpace(sourceStoredSHA.String)) != recordedSourceSHA {
		return CodeBuildSourceChanged, nil
	}

	// The SBOM is the completion's result artifact; the build log is the latest
	// ready artifact with purpose build_log linked to this job (an earlier retry
	// may have produced one too, but only a ready, current one is accepted).
	if completion.ResultArtifactID == "" {
		return CodeBuildArtifactsInvalid, nil
	}
	sbom, failureCode, guardErr := c.readJobBuildArtifact(
		ctx, tx, job, completion.ResultArtifactID, "sbom", MaxToolBuildSBOMBytes,
	)
	if guardErr != nil || failureCode != "" {
		return failureCode, guardErr
	}
	var logID sql.NullString
	err = tx.QueryRowContext(ctx, `
		SELECT a.id::text
		  FROM platform.artifacts AS a
		  JOIN platform.background_job_artifacts AS l
		    ON l.artifact_id = a.id AND l.background_job_id = $1::uuid
		 WHERE l.purpose = 'build_log'
		   AND a.owner_id = $2::uuid
		   AND a.state = 'ready'
		   AND a.deleted_at IS NULL
		 ORDER BY a.created_at DESC, a.id DESC
		 LIMIT 1`, job.ID, job.OwnerID,
	).Scan(&logID)
	if errors.Is(err, sql.ErrNoRows) {
		return CodeBuildArtifactsInvalid, nil
	}
	if err != nil {
		return "", fmt.Errorf("reading the tool build log artifact: %w", err)
	}

	// Re-read the sealed bytes and recompute the content bundle digest. The
	// sandbox's own build.json is only a claim; this is the authority.
	sourceBytes, err := readSealedCapped(c.Store, sourceObjectKey, MaxToolBuildSourceBytes)
	switch {
	case errors.Is(err, objectstore.ErrNotExist), errors.Is(err, objectstore.ErrInvalidKey),
		errors.Is(err, objectstore.ErrTooLarge):
		return CodeBuildSourceChanged, nil
	case errors.Is(err, ErrObjectStoreUnavailable):
		return "", err
	case err != nil:
		return "", fmt.Errorf("%w: %v", errGuardTransient, err)
	}
	if sha256Hex(sourceBytes) != recordedSourceSHA {
		return CodeBuildSourceChanged, nil
	}

	lockCanonical, err := jcs.CanonicalizeBytes(bytes.TrimSpace(dependencyLock))
	if err != nil {
		return CodeBuildSourceChanged, nil
	}
	if sha256Hex(lockCanonical) != strings.ToLower(strings.TrimSpace(lockSHA.String)) {
		return CodeBuildSourceChanged, nil
	}
	bundleDigest := contentBundleDigest(sourceBytes, lockCanonical, sbom)

	result, err := tx.ExecContext(ctx, `
		UPDATE platform.tool_versions
		   SET state = 'ready',
		       image_digest = $2,
		       build_bundle_digest = $2,
		       build_log_artifact_id = $3::uuid,
		       sbom_artifact_id = $4::uuid,
		       build_error_code = NULL,
		       build_finished_at = now(),
		       revision = revision + 1
		 WHERE id = $1::uuid
		   AND state = 'building'
		   AND revision = $5`,
		job.TargetID, bundleDigest, logID.String, completion.ResultArtifactID, revision,
	)
	if err != nil {
		return "", fmt.Errorf("activating the tool build: %w", err)
	}
	affected, err := result.RowsAffected()
	if err != nil {
		return "", fmt.Errorf("activating the tool build: %w", err)
	}
	if affected != 1 {
		return CodeBuildSourceChanged, nil
	}
	return "", nil
}

// readJobBuildArtifact reads one named-purpose artifact that is linked to this
// job, ready, non-deleted, owned by the job owner and not oversized. It returns
// the sealed bytes. A storage read failure is transient.
func (c *Controller) readJobBuildArtifact(
	ctx context.Context,
	tx *sql.Tx,
	job *Job,
	artifactID, purpose string,
	maxBytes int64,
) ([]byte, string, error) {
	var (
		ownerID   string
		state     string
		deleted   sql.NullTime
		storedSHA sql.NullString
		objectKey string
	)
	err := tx.QueryRowContext(ctx, `
		SELECT a.owner_id::text, a.state, a.deleted_at, a.sha256, a.object_key
		  FROM platform.artifacts AS a
		  JOIN platform.background_job_artifacts AS l
		    ON l.artifact_id = a.id AND l.background_job_id = $2::uuid
		 WHERE a.id = $1::uuid AND l.purpose = $3`,
		artifactID, job.ID, purpose,
	).Scan(&ownerID, &state, &deleted, &storedSHA, &objectKey)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, CodeBuildArtifactsInvalid, nil
	}
	if err != nil {
		return nil, "", fmt.Errorf("reading the tool build %s artifact: %w", purpose, err)
	}
	if deleted.Valid || state != "ready" || !strings.EqualFold(ownerID, job.OwnerID) ||
		!storedSHA.Valid || !isSHA256Hex(strings.ToLower(storedSHA.String)) {
		return nil, CodeBuildArtifactsInvalid, nil
	}
	content, err := readSealedCapped(c.Store, objectKey, maxBytes)
	switch {
	case errors.Is(err, objectstore.ErrNotExist), errors.Is(err, objectstore.ErrInvalidKey),
		errors.Is(err, objectstore.ErrTooLarge):
		return nil, CodeBuildArtifactsInvalid, nil
	case errors.Is(err, ErrObjectStoreUnavailable):
		return nil, "", err
	case err != nil:
		return nil, "", fmt.Errorf("%w: %v", errGuardTransient, err)
	}
	if sha256Hex(content) != strings.ToLower(strings.TrimSpace(storedSHA.String)) {
		return nil, CodeBuildArtifactsInvalid, nil
	}
	return content, "", nil
}

// readSealedCapped reads one sealed object with a strict byte cap. It never
// returns more than max+1 bytes and never parses the content.
func readSealedCapped(store *objectstore.Store, key string, max int64) ([]byte, error) {
	if store == nil {
		return nil, ErrObjectStoreUnavailable
	}
	reader, _, err := store.Open(key)
	if err != nil {
		return nil, err
	}
	defer func() { _ = reader.Close() }()
	content, err := io.ReadAll(io.LimitReader(reader, max+1))
	if err != nil {
		return nil, err
	}
	if int64(len(content)) > max {
		return nil, objectstore.ErrTooLarge
	}
	return content, nil
}

// contentBundleDigest is the documented bundle identity: SHA-256 over the
// lexicographically sorted source, canonical lock and SBOM bytes. It is a
// content bundle digest, not an OCI image digest.
func contentBundleDigest(source, canonicalLock, sbom []byte) string {
	parts := [][]byte{
		append([]byte(nil), source...),
		append([]byte(nil), canonicalLock...),
		append([]byte(nil), sbom...),
	}
	sort.Slice(parts, func(i, j int) bool { return bytes.Compare(parts[i], parts[j]) < 0 })
	hash := sha256.New()
	for _, part := range parts {
		_, _ = hash.Write(part)
	}
	return "sha256:" + hex.EncodeToString(hash.Sum(nil))
}

func pointerText(value *string) string {
	if value == nil {
		return ""
	}
	return *value
}
