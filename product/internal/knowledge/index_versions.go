package knowledge

// F10/T061: versioned knowledge indexes.
//
// The source document, the chunk set and the embedding index are three
// independent revisions:
//
//   - `knowledge_documents.revision` is the source revision (bumped when a new
//     sealed source artifact replaces the old one);
//   - `knowledge_index_versions.version` is the per-document index build number,
//     one row per (source revision, embedding model revision) attempt;
//   - chunks belong to exactly one index version (`knowledge_chunks.index_version_id`
//     plus `document_revision` for citation truth).
//
// `knowledge_documents.active_index_id` is the activation pointer. It is written
// only by the `document_ingest` completion guard, in the same transaction that
// re-reads the document FOR UPDATE and requires the job's source revision, a live
// (non-deleted) row and a still-building index version. A late job can therefore
// never activate over newer data or resurrect a deleted document; retrieval reads
// only the active index version and refuses to mix vectors from another embedding
// model revision.

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/jackc/pgx/v5/pgconn"

	"orchara/platform/internal/background"
	"orchara/platform/internal/embedding"
	"orchara/platform/internal/uuid"
)

// Index version states.
const (
	IndexStateBuilding   = "building"
	IndexStateReady      = "ready"
	IndexStateFailed     = "failed"
	IndexStateSuperseded = "superseded"
)

// ErrIndexBuildInProgress is a 409: a same-source rebuild was requested while
// one index version is already building. A *new source artifact* is allowed to
// supersede a running build (that is the update path the activation guard
// protects), but asking for the same build twice is refused.
var ErrIndexBuildInProgress = errors.New("an index build is already in progress for this document")

// ErrStaleRevision is an internal 409: the job's index version or source revision
// no longer matches the document. The handler maps it to the stable
// `stale_revision` job failure and activates nothing.
var ErrStaleRevision = errors.New("the index build no longer matches the document revision")

// IndexJob is the latest background job bound to one index version.
type IndexJob struct {
	ID           string
	State        string
	ErrorCode    *string
	AttemptCount int
}

// IndexVersion is one build row of a document, as the diagnostics read exposes it.
type IndexVersion struct {
	ID                     string
	DocumentID             string
	Version                int64
	DocumentRevision       int64
	EmbeddingModelRevision string
	EmbeddingDimension     int
	State                  string
	ChunkCount             int
	ErrorCode              *string
	ParserVersion          *string
	ChunkerVersion         *string
	ActivatedAt            *time.Time
	SupersededAt           *time.Time
	CreatedAt              time.Time
	UpdatedAt              time.Time
	// Active is true for the one index version the document pointer names.
	Active bool
	// BackgroundJob is the latest job bound to this version (nil if none).
	BackgroundJob *IndexJob
}

// Stage is an honest progress label derived from the persisted state and
// counters: queued (job ready, no chunk staged), building (handler running),
// complete, failed or superseded. It is not a synthetic percentage.
func (v IndexVersion) Stage() string {
	switch v.State {
	case IndexStateBuilding:
		if v.ChunkCount > 0 {
			return "staging"
		}
		if v.BackgroundJob != nil && v.BackgroundJob.State == string(background.StateReady) {
			return "queued"
		}
		return "building"
	case IndexStateReady:
		return "complete"
	case IndexStateFailed:
		return "failed"
	case IndexStateSuperseded:
		return "superseded"
	}
	return v.State
}

// ReindexResult is the non-contract answer of POST /knowledge-documents/{id}/reindex.
type ReindexResult struct {
	DocumentID             string
	State                  string
	Revision               int64
	IndexVersionID         string
	IndexVersion           int64
	EmbeddingModelRevision string
	JobID                  string
}

// RetrievalIndexInfo is additive retrieval metadata: which embedding revision the
// active indexes use and how many active indexes were excluded because they
// belong to another embedding model revision. Excluded indexes are never mixed
// into the answer.
type RetrievalIndexInfo struct {
	EmbeddingModelRevision string
	ActiveIndexes          int
	ExcludedIndexes        int
}

// insertIndexVersionTx creates one building index version for a document. The
// caller must hold the document row lock; the partial unique index
// `knowledge_index_versions_one_building` is the race-proof half of the same
// rule (one build per document per source revision: a newer revision may build
// while an older revision's job is still in flight, and the guard fences it).
func insertIndexVersionTx(
	ctx context.Context,
	tx *sql.Tx,
	documentID string,
	documentRevision int64,
	modelRevision string,
	dimension int,
	parserVersion string,
) (string, int64, error) {
	if strings.TrimSpace(modelRevision) == "" {
		return "", 0, ErrEmbeddingUnavailable
	}
	if dimension <= 0 {
		return "", 0, ErrEmbeddingUnavailable
	}
	var version int64
	if err := tx.QueryRowContext(ctx, `
		SELECT coalesce(max(version), 0) + 1
		  FROM platform.knowledge_index_versions
		 WHERE document_id = $1::uuid`, documentID,
	).Scan(&version); err != nil {
		return "", 0, fmt.Errorf("allocating the index version: %w", err)
	}
	indexID := uuid.NewString()
	_, err := tx.ExecContext(ctx, `
		INSERT INTO platform.knowledge_index_versions
			(id, document_id, version, document_revision, embedding_model_revision,
			 embedding_dimension, state, parser_version)
		VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, 'building', $7)`,
		indexID, documentID, version, documentRevision, modelRevision, dimension, parserVersion,
	)
	if err != nil {
		var pgErr *pgconn.PgError
		if errors.As(err, &pgErr) && pgErr.Code == "23505" &&
			pgErr.ConstraintName == "knowledge_index_versions_one_building" {
			return "", 0, ErrIndexBuildInProgress
		}
		return "", 0, fmt.Errorf("creating the index version: %w", err)
	}
	return indexID, version, nil
}

// enqueueIndexBuildTx binds one document_ingest job to one index version.
func enqueueIndexBuildTx(
	ctx context.Context,
	tx *sql.Tx,
	ownerID, documentID, sourceArtifactID, indexID string,
	documentRevision int64,
) (*background.Job, error) {
	job, _, err := background.EnqueueTx(ctx, tx, background.EnqueueParams{
		Kind:                   background.KindDocumentIngest,
		TargetID:               documentID,
		OwnerID:                ownerID,
		DedupKey:               fmt.Sprintf("knowledge-index:%s", indexID),
		ExpectedRevision:       &documentRevision,
		ExpectedIndexVersionID: indexID,
		InputArtifactID:        sourceArtifactID,
	})
	if err != nil {
		return nil, err
	}
	return job, nil
}

// ReindexDocument is the non-contract rebuild entry (ADR 0018).
//
// It creates a new index version and one background job. With a new
// `source_artifact_id` the source revision is bumped and the previous active
// index stays readable until the new one passes the guard; without one it
// rebuilds the current source revision under the deployment's embedding model.
// A same-source request while a build is already running is refused; a new source
// may supersede a running build because that is exactly the update the guard must
// fence.
func (s *Service) ReindexDocument(
	ctx context.Context, ownerID, documentID string, sourceArtifactID *string,
) (ReindexResult, error) {
	if !uuid.Valid(strings.TrimSpace(documentID)) {
		return ReindexResult{}, ErrNotFound
	}
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return ReindexResult{}, fmt.Errorf("starting the reindex transaction: %w", err)
	}
	defer func() { _ = tx.Rollback() }()

	var (
		currentSource string
		revision      int64
		state         string
		deleted       sql.NullTime
		parserVersion sql.NullString
	)
	err = tx.QueryRowContext(ctx, `
		SELECT d.source_artifact_id::text, d.revision, d.state, d.deleted_at, d.parser_version
		  FROM platform.knowledge_documents AS d
		  JOIN platform.knowledge_bases AS b ON b.id = d.knowledge_base_id
		 WHERE d.id = $1::uuid AND b.owner_id = $2::uuid AND b.deleted_at IS NULL
		 FOR UPDATE OF d`,
		documentID, ownerID,
	).Scan(&currentSource, &revision, &state, &deleted, &parserVersion)
	if errors.Is(err, sql.ErrNoRows) {
		return ReindexResult{}, ErrNotFound
	}
	if err != nil {
		return ReindexResult{}, fmt.Errorf("reading the document for reindex: %w", err)
	}
	if deleted.Valid || state == "deleted" || state == "deleting" {
		return ReindexResult{}, ErrNotFound
	}

	targetSource := currentSource
	targetRevision := revision
	replacement := ""
	if sourceArtifactID != nil {
		replacement = strings.TrimSpace(*sourceArtifactID)
	}
	if replacement != "" && replacement != currentSource {
		if !uuid.Valid(replacement) {
			return ReindexResult{}, ErrArtifactNotVisible
		}
		parser, revisionAfter, err := validateReplacementSource(
			ctx, tx, ownerID, replacement, revision,
		)
		if err != nil {
			return ReindexResult{}, err
		}
		targetSource = replacement
		targetRevision = revisionAfter
		parserVersion = sql.NullString{String: parser.Version(), Valid: true}
		if _, err := tx.ExecContext(ctx, `
			UPDATE platform.knowledge_documents
			   SET source_artifact_id = $2::uuid, revision = $3, state = 'scanning',
			       parser_version = $4, error_code = NULL, chunker_version = NULL,
			       updated_at = now()
			 WHERE id = $1::uuid`,
			documentID, targetSource, targetRevision, parser.Version(),
		); err != nil {
			return ReindexResult{}, fmt.Errorf("moving the document to the new source revision: %w", err)
		}
	} else {
		if state == StateScanning || state == StateIndexing {
			return ReindexResult{}, ErrIndexBuildInProgress
		}
		if _, err := tx.ExecContext(ctx, `
			UPDATE platform.knowledge_documents
			   SET state = 'scanning', error_code = NULL, updated_at = now()
			 WHERE id = $1::uuid`, documentID,
		); err != nil {
			return ReindexResult{}, fmt.Errorf("starting the index rebuild: %w", err)
		}
	}

	modelRevision := ""
	if s.Embedder != nil {
		modelRevision = s.Embedder.ModelRevision()
	}
	if modelRevision == "" {
		return ReindexResult{}, ErrEmbeddingUnavailable
	}
	dimension := embedding.Dimensions
	if s.Embedder != nil && s.Embedder.Dimensions() > 0 {
		dimension = s.Embedder.Dimensions()
	}
	indexID, indexVersion, err := insertIndexVersionTx(
		ctx, tx, documentID, targetRevision, modelRevision, dimension, parserVersion.String,
	)
	if err != nil {
		return ReindexResult{}, err
	}
	job, err := enqueueIndexBuildTx(
		ctx, tx, ownerID, documentID, targetSource, indexID, targetRevision,
	)
	if err != nil {
		return ReindexResult{}, err
	}
	if err := tx.Commit(); err != nil {
		return ReindexResult{}, fmt.Errorf("committing the reindex: %w", err)
	}
	return ReindexResult{
		DocumentID: documentID, State: StateScanning, Revision: targetRevision,
		IndexVersionID: indexID, IndexVersion: indexVersion,
		EmbeddingModelRevision: modelRevision, JobID: job.ID,
	}, nil
}

// validateReplacementSource applies the same source-artifact rules as
// CreateDocument to a replacement: owner, live, ready and a registered parser.
func validateReplacementSource(
	ctx context.Context, tx *sql.Tx, ownerID, artifactID string, currentRevision int64,
) (Parser, int64, error) {
	var (
		artifactOwner string
		artifactState string
		mediaType     string
		revision      int64
		deleted       sql.NullTime
	)
	err := tx.QueryRowContext(ctx, `
		SELECT owner_id::text, state, media_type, revision, deleted_at
		  FROM platform.artifacts
		 WHERE id = $1::uuid`, artifactID,
	).Scan(&artifactOwner, &artifactState, &mediaType, &revision, &deleted)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, 0, ErrArtifactNotVisible
	}
	if err != nil {
		return nil, 0, fmt.Errorf("reading the replacement source artifact: %w", err)
	}
	if artifactOwner != ownerID || deleted.Valid || artifactState == "deleted" ||
		artifactState == "quarantined" {
		return nil, 0, ErrArtifactNotVisible
	}
	if artifactState != "ready" {
		return nil, 0, ErrArtifactNotReady
	}
	parser, ok := ParserFor(mediaType)
	if !ok {
		return nil, 0, ErrUnsupportedInput
	}
	return parser, currentRevision + 1, nil
}

// retrievalIndexInfo counts the active index versions of one knowledge base and
// how many of them belong to another embedding model revision. The latter are
// not candidates for this query; counting them makes the "model switched but not
// rebuilt" state visible instead of silently empty.
func (s *Service) retrievalIndexInfo(
	ctx context.Context, ownerID, baseID, modelRevision string,
) (RetrievalIndexInfo, error) {
	var active, excluded int
	err := s.DB.QueryRowContext(ctx, `
		SELECT count(*) FILTER (WHERE iv.embedding_model_revision = $3),
		       count(*) FILTER (WHERE iv.embedding_model_revision <> $3)
		  FROM platform.knowledge_documents AS d
		  JOIN platform.knowledge_bases AS b ON b.id = d.knowledge_base_id
		  JOIN platform.knowledge_index_versions AS iv ON iv.id = d.active_index_id
		 WHERE b.id = $1::uuid AND b.owner_id = $2::uuid AND b.deleted_at IS NULL
		   AND d.deleted_at IS NULL AND iv.state = 'ready'`,
		baseID, ownerID, modelRevision,
	).Scan(&active, &excluded)
	if err != nil {
		return RetrievalIndexInfo{}, fmt.Errorf("reading the active index reality: %w", err)
	}
	return RetrievalIndexInfo{
		EmbeddingModelRevision: modelRevision,
		ActiveIndexes:          active,
		ExcludedIndexes:        excluded,
	}, nil
}

// readIndexVersions reads a document's index builds (newest first, bounded) with
// their latest bound job. It is the diagnostics/rebuild progress read, not a
// state authority: every fact comes from the rows the guard wrote.
func (s *Service) readIndexVersions(
	ctx context.Context, documentID, activeIndexID string, limit int,
) ([]IndexVersion, error) {
	rows, err := s.DB.QueryContext(ctx, `
		SELECT id::text, document_id::text, version, document_revision,
		       embedding_model_revision, embedding_dimension, state, chunk_count,
		       error_code, parser_version, chunker_version, activated_at, superseded_at,
		       created_at, updated_at
		  FROM platform.knowledge_index_versions
		 WHERE document_id = $1::uuid
		 ORDER BY version DESC
		 LIMIT $2`, documentID, limit,
	)
	if err != nil {
		return nil, fmt.Errorf("reading the index versions: %w", err)
	}
	defer func() { _ = rows.Close() }()
	versions := make([]IndexVersion, 0, limit)
	for rows.Next() {
		var (
			version        IndexVersion
			errorCode      sql.NullString
			parserVersion  sql.NullString
			chunkerVersion sql.NullString
			activatedAt    sql.NullTime
			supersededAt   sql.NullTime
		)
		if err := rows.Scan(
			&version.ID, &version.DocumentID, &version.Version, &version.DocumentRevision,
			&version.EmbeddingModelRevision, &version.EmbeddingDimension, &version.State,
			&version.ChunkCount, &errorCode, &parserVersion, &chunkerVersion,
			&activatedAt, &supersededAt, &version.CreatedAt, &version.UpdatedAt,
		); err != nil {
			return nil, fmt.Errorf("reading an index version: %w", err)
		}
		version.ErrorCode = nullStringPointer(errorCode)
		version.ParserVersion = nullStringPointer(parserVersion)
		version.ChunkerVersion = nullStringPointer(chunkerVersion)
		if activatedAt.Valid {
			value := activatedAt.Time
			version.ActivatedAt = &value
		}
		if supersededAt.Valid {
			value := supersededAt.Time
			version.SupersededAt = &value
		}
		version.Active = activeIndexID != "" && version.ID == activeIndexID
		versions = append(versions, version)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("reading the index versions: %w", err)
	}
	if len(versions) == 0 {
		return versions, nil
	}
	ids := make([]string, 0, len(versions))
	for _, version := range versions {
		ids = append(ids, version.ID)
	}
	jobRows, err := s.DB.QueryContext(ctx, `
		SELECT DISTINCT ON (expected_index_version_id)
		       expected_index_version_id::text, id::text, state, error_code, attempt_count
		  FROM platform.background_jobs
		 WHERE kind = 'document_ingest'
		   AND expected_index_version_id = ANY($1::uuid[])
		 ORDER BY expected_index_version_id, created_at DESC, id DESC`,
		ids,
	)
	if err != nil {
		return nil, fmt.Errorf("reading the index jobs: %w", err)
	}
	defer func() { _ = jobRows.Close() }()
	jobs := make(map[string]*IndexJob, len(versions))
	for jobRows.Next() {
		var (
			indexID string
			job     IndexJob
			code    sql.NullString
		)
		if err := jobRows.Scan(&indexID, &job.ID, &job.State, &code, &job.AttemptCount); err != nil {
			return nil, fmt.Errorf("reading an index job: %w", err)
		}
		job.ErrorCode = nullStringPointer(code)
		copied := job
		jobs[indexID] = &copied
	}
	if err := jobRows.Err(); err != nil {
		return nil, fmt.Errorf("reading the index jobs: %w", err)
	}
	for index := range versions {
		versions[index].BackgroundJob = jobs[versions[index].ID]
	}
	return versions, nil
}
