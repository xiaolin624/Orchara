package background

// F10/T055/T061: the document_ingest completion guard.
//
// BACKGROUND-JOBS §2 says parsed/indexed content may be staged, and only an
// authoritative transaction that re-checks the revision may activate it. This
// guard is that transaction for knowledge indexes, and it is the only writer of a
// document's activation pointer, `state`, `active_revision`, `error_code`, and of
// an index version's ready/failed/superseded state:
//
//   - The document row is re-read FOR UPDATE. The document must exist, must not
//     be tombstoned, and its source revision must equal the revision the job and
//     the index version were created for. A late job from an older revision, or
//     any job whose document was deleted, is refused (`stale_revision` /
//     `source_deleted`) and activates nothing.
//   - The index version row is read FOR UPDATE too: it must still belong to this
//     document, still be `building`, and describe the same source revision and
//     embedding model revision. Otherwise the job is refused with
//     `stale_revision`.
//   - Success is accepted only while the document is `indexing` at that revision
//     and the staged chunk rows form a complete, citation-consistent set for the
//     index version: chunk numbers 0..n-1, one embedding model revision equal to
//     the index's, the index's fixed dimension, body artifacts ready and owned by
//     the job owner. Any deviation turns the claimed success into a failed index
//     version and a failed job, so a handler can never mark an empty or partial
//     set ready.
//   - Activation sets the index version ready and moves the document's
//     `active_index_id` in the same statement pair; the previous active index
//     becomes `superseded`. A concurrent rebuild of a newer source revision
//     cannot be overwritten by this job because the document revision check and
//     the row lock happen in this transaction.
//   - A non-retryable failure marks the index version failed and the document
//     failed *without* clearing the previous activation pointer: a failed rebuild
//     leaves the previous active index readable and can be re-submitted. A
//     retryable failure leaves both untouched for the next attempt.

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
)

// guardDocumentIngestCompletion is the server-side activation decision for one
// ingestion attempt. See the file comment for the exact rules.
func guardDocumentIngestCompletion(
	ctx context.Context,
	_ *Controller,
	tx *sql.Tx,
	job *Job,
	completion *Completion,
) error {
	if completion.ResultArtifactID != "" {
		return &ValidationError{
			Field:   "result_artifact_id",
			Message: "document_ingest has no result artifact; the staged chunk set is the result",
		}
	}
	indexID := ""
	if job.ExpectedIndexVersionID != nil {
		indexID = *job.ExpectedIndexVersionID
	}

	var (
		state       string
		revision    int64
		deleted     sql.NullTime
		activeIndex sql.NullString
	)
	err := tx.QueryRowContext(ctx, `
		SELECT state, revision, deleted_at, active_index_id::text
		  FROM platform.knowledge_documents
		 WHERE id = $1::uuid
		 FOR UPDATE`, job.TargetID).Scan(&state, &revision, &deleted, &activeIndex)
	if errors.Is(err, sql.ErrNoRows) {
		if err := failIndexVersionTx(ctx, tx, indexID, CodeSourceDeleted); err != nil {
			return err
		}
		completion.State = StateFailed
		completion.ErrorCode = CodeSourceDeleted
		return nil
	}
	if err != nil {
		return fmt.Errorf("reading the document: %w", err)
	}
	if deleted.Valid || state == "deleted" || state == "deleting" {
		if err := failIndexVersionTx(ctx, tx, indexID, CodeSourceDeleted); err != nil {
			return err
		}
		completion.State = StateFailed
		completion.ErrorCode = CodeSourceDeleted
		return nil
	}
	if indexID == "" {
		completion.State = StateFailed
		completion.ErrorCode = CodeStaleRevision
		return nil
	}

	var (
		indexDocument  string
		indexRevision  int64
		indexModel     string
		indexDimension int
		indexState     string
	)
	err = tx.QueryRowContext(ctx, `
		SELECT document_id::text, document_revision, embedding_model_revision,
		       embedding_dimension, state
		  FROM platform.knowledge_index_versions
		 WHERE id = $1::uuid
		 FOR UPDATE`, indexID,
	).Scan(&indexDocument, &indexRevision, &indexModel, &indexDimension, &indexState)
	if errors.Is(err, sql.ErrNoRows) {
		completion.State = StateFailed
		completion.ErrorCode = CodeStaleRevision
		return nil
	}
	if err != nil {
		return fmt.Errorf("reading the index version: %w", err)
	}
	if indexDocument != job.TargetID || indexState != "building" {
		completion.State = StateFailed
		completion.ErrorCode = CodeStaleRevision
		return nil
	}
	if job.ExpectedRevision == nil || *job.ExpectedRevision != revision ||
		indexRevision != revision {
		// A newer source revision owns the document. This job is refused without
		// touching the document or the newer revision's build.
		if err := failIndexVersionTx(ctx, tx, indexID, CodeStaleRevision); err != nil {
			return err
		}
		completion.State = StateFailed
		completion.ErrorCode = CodeStaleRevision
		return nil
	}

	switch completion.State {
	case StateSucceeded:
		if state != "indexing" {
			// The ticket requires the document to still be indexing. A claimed
			// success that arrives after the document moved to another state is a
			// protocol violation, never an activation.
			if err := failIndexVersionTx(ctx, tx, indexID, CodeStaleRevision); err != nil {
				return err
			}
			completion.State = StateFailed
			completion.ErrorCode = CodeStaleRevision
			return nil
		}
		failureCode, err := verifyStagedDocumentChunks(
			ctx, tx, job, indexID, indexRevision, indexModel, indexDimension,
		)
		if err != nil {
			return err
		}
		if failureCode != "" {
			completion.State = StateFailed
			completion.ErrorCode = failureCode
			if err := failIndexVersionTx(ctx, tx, indexID, failureCode); err != nil {
				return err
			}
			return failDocumentAfterAttemptTx(ctx, tx, job.TargetID, revision, failureCode)
		}
		result, err := tx.ExecContext(ctx, `
			UPDATE platform.knowledge_index_versions
			   SET state = 'ready',
			       chunk_count = (
			           SELECT count(*) FROM platform.knowledge_chunks AS kc
			            WHERE kc.index_version_id = knowledge_index_versions.id
			       ),
			       chunker_version = (
			           SELECT min(kc.citation->>'chunker_version')
			             FROM platform.knowledge_chunks AS kc
			            WHERE kc.index_version_id = knowledge_index_versions.id
			       ),
			       error_code = NULL,
			       activated_at = now(),
			       updated_at = now()
			 WHERE id = $1::uuid AND state = 'building'`, indexID)
		if err != nil {
			return fmt.Errorf("activating the index version: %w", err)
		}
		affected, err := result.RowsAffected()
		if err != nil {
			return fmt.Errorf("activating the index version: %w", err)
		}
		if affected != 1 {
			// The row lock should make this impossible; never claim an activation
			// that did not happen.
			completion.State = StateFailed
			completion.ErrorCode = CodeStaleRevision
			return nil
		}
		if _, err := tx.ExecContext(ctx, `
			UPDATE platform.knowledge_documents
			   SET state = 'ready',
			       active_index_id = $2::uuid,
			       active_revision = $3,
			       active_embedding_model_revision = $4,
			       embedding_model_revision = $4,
			       chunker_version = (
			           SELECT chunker_version FROM platform.knowledge_index_versions
			            WHERE id = $2::uuid
			       ),
			       error_code = NULL,
			       updated_at = now()
			 WHERE id = $1::uuid`, job.TargetID, indexID, revision, indexModel,
		); err != nil {
			return fmt.Errorf("activating the document index: %w", err)
		}
		if activeIndex.Valid && activeIndex.String != indexID {
			if _, err := tx.ExecContext(ctx, `
				UPDATE platform.knowledge_index_versions
				   SET state = 'superseded', superseded_at = now(), updated_at = now()
				 WHERE id = $1::uuid AND state = 'ready'`, activeIndex.String,
			); err != nil {
				return fmt.Errorf("superseding the previous index: %w", err)
			}
		}
		return nil

	case StateFailed:
		if retryAllowed(job, completion.ErrorCode) {
			// Infrastructure failure: the index version stays building and the
			// document stays indexing for the next attempt.
			return nil
		}
		if err := failIndexVersionTx(ctx, tx, indexID, completion.ErrorCode); err != nil {
			return err
		}
		return failDocumentAfterAttemptTx(ctx, tx, job.TargetID, revision, completion.ErrorCode)

	case StateCancelled:
		completion.State = StateFailed
		completion.ErrorCode = CodeIngestCancelled
		if err := failIndexVersionTx(ctx, tx, indexID, CodeIngestCancelled); err != nil {
			return err
		}
		return failDocumentAfterAttemptTx(ctx, tx, job.TargetID, revision, CodeIngestCancelled)
	}
	return nil
}

// verifyStagedDocumentChunks re-reads the staged set of one index version and
// returns a stable failure code ("" on success). It is deliberately one
// statement: the set is either complete and consistent as a whole or the index
// version must not activate.
func verifyStagedDocumentChunks(
	ctx context.Context, tx *sql.Tx, job *Job, indexID string, revision int64,
	modelRevision string, dimension int,
) (string, error) {
	inputArtifactID := ""
	if job.InputArtifactID != nil {
		inputArtifactID = *job.InputArtifactID
	}
	var (
		total             int
		distinctNumbers   int
		minimumNumber     int
		maximumNumber     int
		missingEmbeddings int
		badDimensions     int
		badModels         int
		badCitations      int
		badArtifacts      int
	)
	err := tx.QueryRowContext(ctx, `
		SELECT count(*),
		       count(DISTINCT kc.chunk_no),
		       coalesce(min(kc.chunk_no), -1),
		       coalesce(max(kc.chunk_no), -1),
		       count(*) FILTER (WHERE kc.embedding IS NULL),
		       count(*) FILTER (WHERE kc.embedding_dimension IS DISTINCT FROM $5),
		       count(*) FILTER (WHERE kc.embedding_model_revision IS DISTINCT FROM $6),
		       count(*) FILTER (
		           WHERE kc.citation->>'document_id' <> $2
		              OR kc.citation->>'document_revision' <> ($3::bigint)::text
		              OR kc.citation->>'source_artifact_id' <> $4
		       ),
		       count(*) FILTER (
		           WHERE a.state <> 'ready' OR a.deleted_at IS NOT NULL OR a.owner_id <> $7::uuid
		       )
		  FROM platform.knowledge_chunks AS kc
		  JOIN platform.artifacts AS a ON a.id = kc.body_artifact_id
		 WHERE kc.document_id = $1::uuid AND kc.document_revision = $3
		   AND kc.index_version_id = $8::uuid`,
		job.TargetID, job.TargetID, revision, inputArtifactID, dimension, modelRevision,
		job.OwnerID, indexID,
	).Scan(
		&total, &distinctNumbers, &minimumNumber, &maximumNumber,
		&missingEmbeddings, &badDimensions, &badModels, &badCitations, &badArtifacts,
	)
	if err != nil {
		return "", fmt.Errorf("verifying the staged chunks: %w", err)
	}
	switch {
	case total == 0:
		// A parser that produced no content, or a handler that claimed success
		// without staging anything, must never become ready.
		return CodeIngestEmptyContent, nil
	case distinctNumbers != total || minimumNumber != 0 || maximumNumber != total-1:
		return CodeIngestCitationInvalid, nil
	case missingEmbeddings > 0 || badArtifacts > 0:
		return CodeIngestIncomplete, nil
	case badDimensions > 0 || badModels > 0 || badCitations > 0:
		return CodeIngestCitationInvalid, nil
	}
	return "", nil
}

// failIndexVersionTx records a terminal build failure on one still-building index
// version. It never touches a version that already activated or failed.
func failIndexVersionTx(ctx context.Context, tx *sql.Tx, indexID, code string) error {
	if indexID == "" {
		return nil
	}
	if _, err := tx.ExecContext(ctx, `
		UPDATE platform.knowledge_index_versions
		   SET state = 'failed', error_code = $2, updated_at = now()
		 WHERE id = $1::uuid AND state = 'building'`, indexID, code,
	); err != nil {
		return fmt.Errorf("failing the index version: %w", err)
	}
	return nil
}

// failDocumentAfterAttemptTx records a terminal ingestion failure on a document
// that is still pre-activation at the given revision. It deliberately does not
// clear `active_index_id` / `active_revision` / `active_embedding_model_revision`:
// a failed rebuild leaves the previous active index exactly as it was.
func failDocumentAfterAttemptTx(
	ctx context.Context, tx *sql.Tx, documentID string, revision int64, code string,
) error {
	if _, err := tx.ExecContext(ctx, `
		UPDATE platform.knowledge_documents
		   SET state = 'failed', error_code = $3, updated_at = now()
		 WHERE id = $1::uuid AND revision = $2
		   AND state IN ('uploaded', 'scanning', 'indexing')`,
		documentID, revision, code,
	); err != nil {
		return fmt.Errorf("failing the document: %w", err)
	}
	return nil
}
