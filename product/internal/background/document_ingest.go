package background

// F10/T055: the document_ingest completion guard.
//
// BACKGROUND-JOBS §2 says parsed/indexed content may be staged, and only an
// authoritative transaction that re-checks the revision may activate it. This
// guard is that transaction, and it is the only writer of a document's
// state/active_revision/error_code:
//
//   - success is accepted only while the document is still scanning/indexing at
//     the job's expected revision, and only when the staged chunk rows form a
//     complete, citation-consistent set whose body artifacts are all ready and
//     owned by the job owner. Any deviation turns the claimed success into a
//     failed job, so a handler can never mark an empty or partial parse ready.
//   - readiness is set together with active_revision = revision; retrieval
//     requires both, so a superseded revision becomes unreadable in the same
//     statement.
//   - a terminal failure marks the document failed with the specific code; a
//     retryable failure leaves it scanning/indexing for the next attempt.
//   - a job whose expected revision no longer matches fails without touching the
//     document: a newer revision owns it.

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
	var (
		state    string
		revision int64
		deleted  sql.NullTime
	)
	err := tx.QueryRowContext(ctx, `
		SELECT state, revision, deleted_at
		  FROM platform.knowledge_documents
		 WHERE id = $1::uuid
		 FOR UPDATE`, job.TargetID).Scan(&state, &revision, &deleted)
	if errors.Is(err, sql.ErrNoRows) {
		completion.State = StateFailed
		completion.ErrorCode = CodeSourceDeleted
		return nil
	}
	if err != nil {
		return fmt.Errorf("reading the document: %w", err)
	}
	if deleted.Valid || state == "deleted" || state == "deleting" {
		completion.State = StateFailed
		completion.ErrorCode = CodeSourceDeleted
		return nil
	}
	if job.ExpectedRevision != nil && *job.ExpectedRevision != revision {
		// A newer revision owns the document. This job fails, and the document is
		// left exactly as the newer job will find it.
		completion.State = StateFailed
		completion.ErrorCode = CodeIngestSourceChanged
		return nil
	}

	switch completion.State {
	case StateSucceeded:
		if state != "scanning" && state != "indexing" {
			completion.State = StateFailed
			completion.ErrorCode = CodeIngestIncomplete
			return nil
		}
		failureCode, err := verifyStagedDocumentChunks(ctx, tx, job, revision)
		if err != nil {
			return err
		}
		if failureCode != "" {
			completion.State = StateFailed
			completion.ErrorCode = failureCode
			return failDocumentTx(ctx, tx, job.TargetID, revision, failureCode)
		}
		result, err := tx.ExecContext(ctx, `
			UPDATE platform.knowledge_documents
			   SET state = 'ready',
			       active_revision = revision,
			       error_code = NULL,
			       chunker_version = (
			           SELECT min(kc.citation->>'chunker_version')
			             FROM platform.knowledge_chunks AS kc
			            WHERE kc.document_id = knowledge_documents.id
			              AND kc.document_revision = knowledge_documents.revision
			       ),
			       embedding_model_revision = (
			           SELECT min(kc.embedding_model_revision)
			             FROM platform.knowledge_chunks AS kc
			            WHERE kc.document_id = knowledge_documents.id
			              AND kc.document_revision = knowledge_documents.revision
			       ),
			       updated_at = now()
			 WHERE id = $1::uuid AND revision = $2
			   AND state IN ('scanning', 'indexing')`,
			job.TargetID, revision,
		)
		if err != nil {
			return fmt.Errorf("activating the document revision: %w", err)
		}
		affected, err := result.RowsAffected()
		if err != nil {
			return fmt.Errorf("activating the document revision: %w", err)
		}
		if affected != 1 {
			// The row lock should make this impossible; never claim an activation
			// that did not happen.
			completion.State = StateFailed
			completion.ErrorCode = CodeIngestSourceChanged
		}
		return nil

	case StateFailed:
		if retryAllowed(job, completion.ErrorCode) {
			return nil
		}
		return failDocumentTx(ctx, tx, job.TargetID, revision, completion.ErrorCode)

	case StateCancelled:
		completion.State = StateFailed
		completion.ErrorCode = CodeIngestCancelled
		return failDocumentTx(ctx, tx, job.TargetID, revision, CodeIngestCancelled)
	}
	return nil
}

// verifyStagedDocumentChunks re-reads the staged set and returns a stable
// failure code ("" on success). It is deliberately one statement: the set is
// either consistent as a whole or the revision must not activate.
func verifyStagedDocumentChunks(
	ctx context.Context, tx *sql.Tx, job *Job, revision int64,
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
		modelRevisions    int
		badCitations      int
		badArtifacts      int
	)
	err := tx.QueryRowContext(ctx, `
		SELECT count(*),
		       count(DISTINCT kc.chunk_no),
		       coalesce(min(kc.chunk_no), -1),
		       coalesce(max(kc.chunk_no), -1),
		       count(*) FILTER (WHERE kc.embedding IS NULL),
		       count(*) FILTER (WHERE kc.embedding_dimension IS DISTINCT FROM 384),
		       count(DISTINCT kc.embedding_model_revision),
		       count(*) FILTER (
		           WHERE kc.citation->>'document_id' <> $2
		              OR kc.citation->>'document_revision' <> ($3::bigint)::text
		              OR kc.citation->>'source_artifact_id' <> $4
		       ),
		       count(*) FILTER (
		           WHERE a.state <> 'ready' OR a.deleted_at IS NOT NULL OR a.owner_id <> $5::uuid
		       )
		  FROM platform.knowledge_chunks AS kc
		  JOIN platform.artifacts AS a ON a.id = kc.body_artifact_id
		 WHERE kc.document_id = $1::uuid AND kc.document_revision = $6`,
		job.TargetID, job.TargetID, revision, inputArtifactID, job.OwnerID, revision,
	).Scan(
		&total, &distinctNumbers, &minimumNumber, &maximumNumber,
		&missingEmbeddings, &badDimensions, &modelRevisions, &badCitations, &badArtifacts,
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
	case badDimensions > 0 || modelRevisions != 1 || badCitations > 0:
		return CodeIngestCitationInvalid, nil
	}
	return "", nil
}

// failDocumentTx records a terminal ingestion failure. It only moves a document
// that is still pre-activation at the given revision, so a concurrently
// activated revision is never clobbered.
func failDocumentTx(ctx context.Context, tx *sql.Tx, documentID string, revision int64, code string) error {
	if _, err := tx.ExecContext(ctx, `
		UPDATE platform.knowledge_documents
		   SET state = 'failed', active_revision = NULL, error_code = $3, updated_at = now()
		 WHERE id = $1::uuid AND revision = $2
		   AND state IN ('uploaded', 'scanning', 'indexing')`,
		documentID, revision, code,
	); err != nil {
		return fmt.Errorf("failing the document: %w", err)
	}
	return nil
}
