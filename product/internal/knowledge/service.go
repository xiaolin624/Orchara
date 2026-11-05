package knowledge

// F10/T055/T061 knowledge service: owner-scoped knowledge bases, ingested
// documents, versioned index builds, the controlled enqueue of `document_ingest`,
// and retrieval.
//
// Two boundaries are deliberate:
//
//   - The service never activates a document or an index version. It writes the
//     document as `scanning`, creates one `building` index version and enqueues
//     one job in the same transaction; the document_ingest completion guard in
//     internal/background is the only writer of `ready`/`failed`,
//     `active_index_id`, `active_revision` and `error_code`.
//   - Retrieval filters ACL, the active index version and the current embedding
//     model revision **in SQL**, then ranks only the surviving candidate set in
//     this process (ADR 0010/0017). It never ranks first and filters afterwards.
//     The contract has no query endpoint; the query/chunk-citation/reindex routes
//     are the documented non-contract surface this ticket's acceptance needs.

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"sort"
	"strings"
	"time"

	"orchara/platform/internal/background"
	"orchara/platform/internal/catalog"
	"orchara/platform/internal/embedding"
	"orchara/platform/internal/objectstore"
	"orchara/platform/internal/uuid"
)

// Document states (contract enum subset this slice writes).
const (
	StateUploaded = "uploaded"
	StateScanning = "scanning"
	StateIndexing = "indexing"
	StateReady    = "ready"
	StateFailed   = "failed"
)

// Query bounds. The candidate cap is a resource bound on the SQL-filtered set,
// not the caller's top_k: every surviving candidate is ranked in process.
const (
	DefaultTopK   = 8
	MaxTopK       = 50
	MaxCandidates = 2000
	// MaxChunksPerDocument bounds how many sealed chunk-body artifacts one
	// document revision may produce. A document that would exceed it fails with
	// ingest_too_many_chunks instead of creating an unbounded artifact count.
	MaxChunksPerDocument = 1000
	maxDocumentName      = 200
	queryExcerptRunes    = 240
)

// Errors mapped to HTTP answers by internal/api.
var (
	// ErrNotFound is "missing, not yours or deleted": every one of those answers
	// 404 and must stay indistinguishable (no enumeration, no 403 leak).
	ErrNotFound = errors.New("knowledge resource not found")
	// ErrArtifactNotVisible means the source artifact is missing, deleted or
	// owned by another principal. It is also ErrNotFound at the API boundary.
	ErrArtifactNotVisible = errors.New("source artifact is not visible")
	// ErrArtifactNotReady is a 409: the upload exists but the finalize guard has
	// not activated it yet.
	ErrArtifactNotReady = errors.New("source artifact is not ready")
	// ErrUnsupportedInput is a 422: no parser is registered for the source media
	// type in this build (PDF -> T060; CSV/XLSX are registered by T059).
	ErrUnsupportedInput = errors.New("this source format is not supported yet")
	// ErrEmbeddingUnavailable is a 503: the real embedding provider path was
	// configured but cannot produce vectors in this deployment.
	ErrEmbeddingUnavailable = errors.New("embedding provider is not configured")
	// ErrObjectStoreUnavailable means ORCHARA_OBJECT_DIR is missing.
	ErrObjectStoreUnavailable = errors.New("the object store is not configured")
	// ErrAnswerGeneratorUnavailable means no answer generator was wired. The
	// answer surface fails closed instead of returning an ungrounded answer.
	ErrAnswerGeneratorUnavailable = errors.New("the answer generator is not configured")
)

// ValidationError names the request field that failed semantic validation.
type ValidationError struct {
	Field   string
	Message string
}

func (e *ValidationError) Error() string { return e.Field + ": " + e.Message }

// Base is the contract's KnowledgeBase: exactly id, name, revision.
type Base struct {
	ID       string
	Name     string
	Revision int64
}

// Document is the contract's KnowledgeDocument.
type Document struct {
	ID               string
	KnowledgeBaseID  string
	State            string
	Revision         int64
	SourceArtifactID string
	ErrorCode        *string
}

// SpanView mirrors Span for the non-contract answer.
type SpanView = Span

// ChunkView is one retrieved chunk with its traceable citation.
type ChunkView struct {
	ChunkID          string
	DocumentID       string
	DocumentRevision int64
	DocumentState    string
	ChunkNo          int
	Span             SpanView
	Citation         json.RawMessage
	Text             string
	Score            float64
}

// Citation is the traceability document stored on every chunk.
//
// It names the exact source bytes (artifact id + sha256), the document revision
// and chunk number, the rune character span and 1-based line range inside the
// decoded source, the parser/chunker revisions and the embedding model revision.
// A citation therefore resolves to one unambiguous substring of one immutable
// artifact revision, even after a later document revision exists.
//
// The row fields are the tabular extension (T059). They are zero/empty for
// txt/md and byte-for-byte absent there (omitempty), so existing citations keep
// their shape:
//   - RowStart/RowEnd are the 1-based inclusive source row range. For CSV this
//     is the logical record number (header = row 1); for XLSX it is the
//     worksheet's own row number.
//   - HeaderRow is the header row's number, RowCount the number of data rows in
//     the chunk (a worksheet range may contain empty rows, so it can be smaller
//     than RowEnd-RowStart+1).
//   - SheetName is set for XLSX only; Columns repeats the header values when they
//     are small enough (ColumnsOmitted says they were not repeated).
//   - TextRender names what Span indexes (source_utf8 for CSV, the sheet
//     rendering for XLSX).
type Citation struct {
	DocumentID             string   `json:"document_id"`
	DocumentRevision       int64    `json:"document_revision"`
	ChunkNo                int      `json:"chunk_no"`
	Span                   Span     `json:"span"`
	OffsetUnit             string   `json:"offset_unit"`
	SourceArtifactID       string   `json:"source_artifact_id"`
	SourceSHA256           string   `json:"source_sha256"`
	SourceMediaType        string   `json:"source_media_type"`
	TextNormalization      string   `json:"text_normalization"`
	TextRender             string   `json:"text_render,omitempty"`
	ParserVersion          string   `json:"parser_version"`
	ChunkerVersion         string   `json:"chunker_version"`
	EmbeddingModelRevision string   `json:"embedding_model_revision"`
	RowStart               int      `json:"row_start,omitempty"`
	RowEnd                 int      `json:"row_end,omitempty"`
	RowCount               int      `json:"row_count,omitempty"`
	HeaderRow              int      `json:"header_row,omitempty"`
	SheetName              string   `json:"sheet_name,omitempty"`
	Columns                []string `json:"columns,omitempty"`
	ColumnsOmitted         bool     `json:"columns_omitted,omitempty"`
	// PDF is set only on a PDF chunk (T060). It is one additive object rather
	// than flat fields so that a page-local offset of zero is representable and
	// the citation of every other input family keeps its exact shape.
	PDF *CitationPDF `json:"pdf,omitempty"`
}

// CitationPDF is the page attribution of one PDF chunk. PageStart/PageEnd are
// 1-based inclusive page numbers (equal for the page-bounded chunker);
// PageCharStart/PageCharEnd are rune offsets inside that page's text;
// PageCount is the document's page count and PagesWithoutText lists the pages
// that had no text layer, so a partial text layer is recorded honestly instead of
// being presented as full coverage.
type CitationPDF struct {
	PageStart        int   `json:"page_start"`
	PageEnd          int   `json:"page_end"`
	PageCharStart    int   `json:"page_char_start"`
	PageCharEnd      int   `json:"page_char_end"`
	PageCount        int   `json:"page_count"`
	PagesWithoutText []int `json:"pages_without_text,omitempty"`
}

// ChunkRow is one staged chunk the handler writes before activation.
type ChunkRow struct {
	ID             string
	DocumentID     string
	Revision       int64
	IndexVersionID string
	No             int
	BodyArtifactID string
	Citation       Citation
	ModelRevision  string
	Embedding      []float64
}

// Service is the knowledge read/write surface. Objects may be nil; every
// operation that needs bytes then fails closed.
type Service struct {
	DB       *sql.DB
	Objects  *objectstore.Store
	Embedder *embedding.Service
	// Answerer is the labelled answer generator (F10/T064). New wires the
	// extractive stub; a deployment may replace it, but Answer always validates
	// the draft against its own retrieval before returning it.
	Answerer AnswerGenerator
	// Cache is the F10/T067 retrieval cache configuration. New wires the
	// documented default; cmd/api overrides it from ORCHARA_KNOWLEDGE_RETRIEVAL_CACHE.
	Cache CacheOptions
}

// New builds the service. A nil embedder means the deployment has not configured
// one; the service then refuses queries instead of ranking with a guess.
func New(db *sql.DB, objects *objectstore.Store, embedder *embedding.Service) *Service {
	return &Service{
		DB: db, Objects: objects, Embedder: embedder,
		Answerer: &ExtractiveStub{},
		Cache:    DefaultCacheOptions(),
	}
}

// ---------------------------------------------------------------------------
// knowledge bases
// ---------------------------------------------------------------------------

// CreateBase inserts one owner-scoped knowledge base.
func (s *Service) CreateBase(ctx context.Context, ownerID, name string) (Base, error) {
	trimmed := strings.TrimSpace(name)
	if trimmed == "" {
		return Base{}, &ValidationError{Field: "name", Message: "name is required"}
	}
	if len([]rune(trimmed)) > maxDocumentName {
		return Base{}, &ValidationError{
			Field: "name", Message: fmt.Sprintf("name must be at most %d characters", maxDocumentName),
		}
	}
	base := Base{ID: uuid.NewString(), Name: trimmed, Revision: 1}
	if _, err := s.DB.ExecContext(ctx, `
		INSERT INTO platform.knowledge_bases (id, name, owner_id, revision)
		VALUES ($1::uuid, $2, $3::uuid, 1)`,
		base.ID, base.Name, ownerID,
	); err != nil {
		return Base{}, fmt.Errorf("creating the knowledge base: %w", err)
	}
	return base, nil
}

const selectBaseColumns = `id::text, name, revision`

// ListBases returns one owner's knowledge bases in DESC (created_at, id) order.
func (s *Service) ListBases(
	ctx context.Context, ownerID, rawCursor string, limit int,
) ([]Base, *string, error) {
	cursorAt, cursorID, err := decodeKeysetCursor(rawCursor)
	if err != nil {
		return nil, nil, err
	}
	rows, err := s.DB.QueryContext(ctx, `
		SELECT `+selectBaseColumns+`, created_at
		  FROM platform.knowledge_bases
		 WHERE owner_id = $1::uuid AND deleted_at IS NULL
		   AND ($3::timestamptz IS NULL OR (created_at, id) < ($3::timestamptz, $4::uuid))
		 ORDER BY created_at DESC, id DESC
		 LIMIT $2`,
		ownerID, limit, cursorAt, cursorID,
	)
	if err != nil {
		return nil, nil, fmt.Errorf("listing knowledge bases: %w", err)
	}
	defer func() { _ = rows.Close() }()

	items := make([]Base, 0, limit)
	var (
		lastCreated time.Time
		lastID      string
	)
	for rows.Next() {
		var (
			base      Base
			createdAt time.Time
		)
		if err := rows.Scan(&base.ID, &base.Name, &base.Revision, &createdAt); err != nil {
			return nil, nil, fmt.Errorf("reading a knowledge base: %w", err)
		}
		items = append(items, base)
		lastCreated, lastID = createdAt, base.ID
	}
	if err := rows.Err(); err != nil {
		return nil, nil, fmt.Errorf("listing knowledge bases: %w", err)
	}
	var next *string
	if len(items) == limit {
		token := catalog.EncodeCursor(lastCreated, lastID)
		next = &token
	}
	return items, next, nil
}

// GetBase returns one owner-visible knowledge base.
func (s *Service) GetBase(ctx context.Context, ownerID, baseID string) (Base, error) {
	if !uuid.Valid(strings.TrimSpace(baseID)) {
		return Base{}, ErrNotFound
	}
	var base Base
	err := s.DB.QueryRowContext(ctx, `
		SELECT `+selectBaseColumns+`
		  FROM platform.knowledge_bases
		 WHERE id = $1::uuid AND owner_id = $2::uuid AND deleted_at IS NULL`,
		baseID, ownerID,
	).Scan(&base.ID, &base.Name, &base.Revision)
	if errors.Is(err, sql.ErrNoRows) {
		return Base{}, ErrNotFound
	}
	if err != nil {
		return Base{}, fmt.Errorf("reading the knowledge base: %w", err)
	}
	return base, nil
}

// ---------------------------------------------------------------------------
// documents
// ---------------------------------------------------------------------------

const selectDocumentColumns = `d.id::text, d.knowledge_base_id::text, d.state, d.revision,
	d.source_artifact_id::text, d.error_code`

type documentScanner interface {
	Scan(dest ...any) error
}

func scanDocument(row documentScanner) (Document, error) {
	var (
		document  Document
		errorCode sql.NullString
	)
	if err := row.Scan(
		&document.ID, &document.KnowledgeBaseID, &document.State, &document.Revision,
		&document.SourceArtifactID, &errorCode,
	); err != nil {
		return Document{}, err
	}
	if errorCode.Valid {
		value := errorCode.String
		document.ErrorCode = &value
	}
	return document, nil
}

// CreateDocument binds one ready, owner-visible source artifact to a knowledge
// base and enqueues the single controlled ingestion job.
//
// The transaction writes the document (`scanning`) and the job together, so a
// job cannot exist for a rolled-back document. The artifact must already be
// `ready` (the existing upload->finalize path is the only way bytes enter) and
// its declared media type must have a registered parser.
func (s *Service) CreateDocument(
	ctx context.Context, ownerID, baseID, sourceArtifactID string,
) (Document, error) {
	if !uuid.Valid(strings.TrimSpace(baseID)) || !uuid.Valid(strings.TrimSpace(sourceArtifactID)) {
		return Document{}, ErrNotFound
	}
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return Document{}, fmt.Errorf("starting the document transaction: %w", err)
	}
	defer func() { _ = tx.Rollback() }()

	var baseRevision int64
	err = tx.QueryRowContext(ctx, `
		SELECT revision FROM platform.knowledge_bases
		 WHERE id = $1::uuid AND owner_id = $2::uuid AND deleted_at IS NULL
		 FOR SHARE`,
		baseID, ownerID,
	).Scan(&baseRevision)
	if errors.Is(err, sql.ErrNoRows) {
		return Document{}, ErrNotFound
	}
	if err != nil {
		return Document{}, fmt.Errorf("reading the knowledge base: %w", err)
	}

	var (
		artifactOwner    string
		artifactState    string
		artifactRevision int64
		mediaType        string
		deleted          sql.NullTime
	)
	err = tx.QueryRowContext(ctx, `
		SELECT owner_id::text, state, media_type, revision, deleted_at
		  FROM platform.artifacts
		 WHERE id = $1::uuid`,
		sourceArtifactID,
	).Scan(&artifactOwner, &artifactState, &mediaType, &artifactRevision, &deleted)
	if errors.Is(err, sql.ErrNoRows) {
		return Document{}, ErrArtifactNotVisible
	}
	if err != nil {
		return Document{}, fmt.Errorf("reading the source artifact: %w", err)
	}
	if artifactOwner != ownerID || deleted.Valid || artifactState == "deleted" ||
		artifactState == "quarantined" {
		return Document{}, ErrArtifactNotVisible
	}
	if artifactState != "ready" {
		return Document{}, ErrArtifactNotReady
	}
	parser, ok := ParserFor(mediaType)
	if !ok {
		return Document{}, ErrUnsupportedInput
	}

	// The contract has no Idempotency-Key on ingestDocument. Repetition is
	// resolved against the real resource state: the same source artifact already
	// bound to this knowledge base is the same intention. A ready document under
	// the same embedding model is returned as-is; a ready document whose active
	// index was built with another embedding model revision starts a rebuild
	// (new index version, same source revision); a failed document gets a new
	// index version and, when it never had an active index, one new source
	// revision (a failed parse is retryable). This is the T061 version
	// replacement seam.
	modelRevision := ""
	if s.Embedder != nil {
		modelRevision = s.Embedder.ModelRevision()
	}
	dimension := embedding.Dimensions
	if s.Embedder != nil && s.Embedder.Dimensions() > 0 {
		dimension = s.Embedder.Dimensions()
	}

	existing, existingErr := scanDocument(tx.QueryRowContext(ctx, `
		SELECT `+selectDocumentColumns+`
		  FROM platform.knowledge_documents AS d
		 WHERE d.knowledge_base_id = $1::uuid AND d.source_artifact_id = $2::uuid
		   AND d.deleted_at IS NULL
		 ORDER BY d.revision DESC, d.created_at DESC
		 LIMIT 1
		 FOR UPDATE`,
		baseID, sourceArtifactID,
	))
	switch {
	case existingErr == nil:
		var (
			activeIndexID sql.NullString
			activeModel   sql.NullString
		)
		if err := tx.QueryRowContext(ctx, `
			SELECT d.active_index_id::text, coalesce(iv.embedding_model_revision, '')
			  FROM platform.knowledge_documents AS d
			  LEFT JOIN platform.knowledge_index_versions AS iv ON iv.id = d.active_index_id
			 WHERE d.id = $1::uuid`, existing.ID,
		).Scan(&activeIndexID, &activeModel); err != nil {
			return Document{}, fmt.Errorf("reading the active index: %w", err)
		}
		modelChanged := existing.State == StateReady && activeIndexID.Valid &&
			activeModel.String != "" && activeModel.String != modelRevision
		switch existing.State {
		case StateScanning, StateIndexing:
			// A live build owns the document: the repeat ingest is the same intent
			// (T055 idempotency), so nothing is created and nothing is healed.
			live, liveErr := background.LiveDocumentBuildTx(ctx, tx, existing.ID, existing.Revision)
			if liveErr != nil {
				return Document{}, liveErr
			}
			if live {
				if err := tx.Commit(); err != nil {
					return Document{}, fmt.Errorf("committing the idempotent document: %w", err)
				}
				return existing, nil
			}
			// T061 (#62) upgrade recovery: no job can ever finish this attempt (a
			// pre-0039 job that lost its binding, a dead lease, a build that failed
			// outside the guard). End it with a specific code and fall through to
			// the retry path below, so the document gets a new revision/index
			// version instead of being returned stuck forever.
			if err := reclaimAbandonedBuildTx(
				ctx, tx, existing.ID, existing.Revision, background.CodeIngestBuildAbandoned,
			); err != nil {
				return Document{}, err
			}
			code := background.CodeIngestBuildAbandoned
			existing.State = StateFailed
			existing.ErrorCode = &code
		}
		if existing.State != StateFailed && !modelChanged {
			if err := tx.Commit(); err != nil {
				return Document{}, fmt.Errorf("committing the idempotent document: %w", err)
			}
			return existing, nil
		}
		nextRevision := existing.Revision
		if !activeIndexID.Valid {
			// A document that never activated an index has no readable revision to
			// preserve; a retry is a new source revision (T055 semantics).
			nextRevision = existing.Revision + 1
		}
		if _, err := tx.ExecContext(ctx, `
			UPDATE platform.knowledge_documents
			   SET revision = $2, state = 'scanning', error_code = NULL,
			       parser_version = $3, chunker_version = NULL,
			       active_revision = CASE WHEN active_index_id IS NULL
			                              THEN NULL ELSE active_revision END,
			       embedding_model_revision = CASE WHEN active_index_id IS NULL
			                                       THEN NULL ELSE embedding_model_revision END,
			       updated_at = now()
			 WHERE id = $1::uuid`,
			existing.ID, nextRevision, parser.Version(),
		); err != nil {
			return Document{}, fmt.Errorf("retrying the document: %w", err)
		}
		indexID, _, err := insertIndexVersionTx(
			ctx, tx, existing.ID, nextRevision, modelRevision, dimension, parser.Version(),
		)
		if err != nil {
			return Document{}, err
		}
		if _, err := enqueueIndexBuildTx(
			ctx, tx, ownerID, existing.ID, sourceArtifactID, indexID, nextRevision,
		); err != nil {
			return Document{}, err
		}
		if err := tx.Commit(); err != nil {
			return Document{}, fmt.Errorf("committing the retried document: %w", err)
		}
		existing.Revision = nextRevision
		existing.State = StateScanning
		existing.ErrorCode = nil
		return existing, nil
	case !errors.Is(existingErr, sql.ErrNoRows):
		return Document{}, fmt.Errorf("reading an existing document: %w", existingErr)
	}

	document := Document{
		ID:               uuid.NewString(),
		KnowledgeBaseID:  baseID,
		State:            StateScanning,
		Revision:         1,
		SourceArtifactID: sourceArtifactID,
	}
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO platform.knowledge_documents
			(id, knowledge_base_id, source_artifact_id, revision, state, parser_version)
		VALUES ($1::uuid, $2::uuid, $3::uuid, 1, 'scanning', $4)`,
		document.ID, baseID, sourceArtifactID, parser.Version(),
	); err != nil {
		return Document{}, fmt.Errorf("creating the document: %w", err)
	}
	revision := document.Revision
	indexID, _, err := insertIndexVersionTx(
		ctx, tx, document.ID, revision, modelRevision, dimension, parser.Version(),
	)
	if err != nil {
		return Document{}, err
	}
	if _, err := enqueueIndexBuildTx(
		ctx, tx, ownerID, document.ID, sourceArtifactID, indexID, revision,
	); err != nil {
		return Document{}, err
	}
	if err := tx.Commit(); err != nil {
		return Document{}, fmt.Errorf("committing the document: %w", err)
	}
	return document, nil
}

// ListDocuments lists one owner's documents in one knowledge base.
func (s *Service) ListDocuments(
	ctx context.Context, ownerID, baseID, rawCursor string, limit int,
) ([]Document, *string, error) {
	if !uuid.Valid(strings.TrimSpace(baseID)) {
		return nil, nil, ErrNotFound
	}
	if _, err := s.GetBase(ctx, ownerID, baseID); err != nil {
		return nil, nil, err
	}
	cursorAt, cursorID, err := decodeKeysetCursor(rawCursor)
	if err != nil {
		return nil, nil, err
	}
	rows, err := s.DB.QueryContext(ctx, `
		SELECT `+selectDocumentColumns+`, d.created_at
		  FROM platform.knowledge_documents AS d
		 WHERE d.knowledge_base_id = $1::uuid AND d.deleted_at IS NULL
		   AND ($3::timestamptz IS NULL OR (d.created_at, d.id) < ($3::timestamptz, $4::uuid))
		 ORDER BY d.created_at DESC, d.id DESC
		 LIMIT $2`,
		baseID, limit, cursorAt, cursorID,
	)
	if err != nil {
		return nil, nil, fmt.Errorf("listing documents: %w", err)
	}
	defer func() { _ = rows.Close() }()

	items := make([]Document, 0, limit)
	var (
		lastCreated time.Time
		lastID      string
	)
	for rows.Next() {
		var (
			document  Document
			createdAt time.Time
			errorCode sql.NullString
		)
		if err := rows.Scan(
			&document.ID, &document.KnowledgeBaseID, &document.State, &document.Revision,
			&document.SourceArtifactID, &errorCode, &createdAt,
		); err != nil {
			return nil, nil, fmt.Errorf("reading a document: %w", err)
		}
		if errorCode.Valid {
			value := errorCode.String
			document.ErrorCode = &value
		}
		items = append(items, document)
		lastCreated, lastID = createdAt, document.ID
	}
	if err := rows.Err(); err != nil {
		return nil, nil, fmt.Errorf("listing documents: %w", err)
	}
	var next *string
	if len(items) == limit {
		token := catalog.EncodeCursor(lastCreated, lastID)
		next = &token
	}
	return items, next, nil
}

// GetDocument returns one owner-visible document.
func (s *Service) GetDocument(ctx context.Context, ownerID, documentID string) (Document, error) {
	if !uuid.Valid(strings.TrimSpace(documentID)) {
		return Document{}, ErrNotFound
	}
	document, err := scanDocument(s.DB.QueryRowContext(ctx, `
		SELECT `+selectDocumentColumns+`
		  FROM platform.knowledge_documents AS d
		  JOIN platform.knowledge_bases AS b ON b.id = d.knowledge_base_id
		 WHERE d.id = $1::uuid AND d.deleted_at IS NULL
		   AND b.owner_id = $2::uuid AND b.deleted_at IS NULL`,
		documentID, ownerID,
	))
	if errors.Is(err, sql.ErrNoRows) {
		return Document{}, ErrNotFound
	}
	if err != nil {
		return Document{}, fmt.Errorf("reading the document: %w", err)
	}
	return document, nil
}

// Diagnostics is the documented non-contract read that makes a failed or
// in-progress ingestion diagnosable on the page. The contract's
// KnowledgeDocument has no room for these fields (additionalProperties:false),
// so they live on GET /api/v1/knowledge-documents/{id}/diagnostics.
//
// Detail is derived on read, not stored: when a document failed with a content
// refusal whose location this build can re-derive (the recorded parser revision
// is still the registered one and the sealed source is still readable), the
// same bytes are parsed again and the location is returned. The durable fact is
// the recorded error code; the location is a projection of it. That keeps a
// single knowledge-state authority (the document row) without a second failure
// write path (ADR 0012).
type Diagnostics struct {
	DocumentID                   string
	State                        string
	Revision                     int64
	ActiveRevision               *int64
	ActiveIndexID                *string
	ActiveIndex                  *IndexVersion
	Indexes                      []IndexVersion
	ActiveEmbeddingModelRevision *string
	ParserVersion                *string
	ChunkerVersion               *string
	EmbeddingModelRevision       *string
	ErrorCode                    *string
	Detail                       *ParseDetail
	ChunkCount                   int
	BackgroundJobID              *string
	BackgroundJobState           *string
	BackgroundJobCode            *string
	BackgroundJobAttempts        int
}

// DocumentDiagnostics reads the owner-visible ingestion facts of one document.
// It never exposes object keys, artifact bytes or another principal's data.
func (s *Service) DocumentDiagnostics(
	ctx context.Context, ownerID, documentID string,
) (Diagnostics, error) {
	document, err := s.GetDocument(ctx, ownerID, documentID)
	if err != nil {
		return Diagnostics{}, err
	}
	diagnostics := Diagnostics{
		DocumentID: document.ID,
		State:      document.State,
		Revision:   document.Revision,
		ErrorCode:  document.ErrorCode,
	}
	var (
		activeRevision sql.NullInt64
		activeIndexID  sql.NullString
		activeModel    sql.NullString
		parserVersion  sql.NullString
		chunkerVersion sql.NullString
		modelRevision  sql.NullString
	)
	if err := s.DB.QueryRowContext(ctx, `
		SELECT active_revision, active_index_id::text, active_embedding_model_revision,
		       parser_version, chunker_version, embedding_model_revision
		  FROM platform.knowledge_documents
		 WHERE id = $1::uuid`, documentID,
	).Scan(&activeRevision, &activeIndexID, &activeModel, &parserVersion,
		&chunkerVersion, &modelRevision); err != nil {
		return Diagnostics{}, fmt.Errorf("reading the document diagnostics: %w", err)
	}
	if activeRevision.Valid {
		value := activeRevision.Int64
		diagnostics.ActiveRevision = &value
	}
	diagnostics.ActiveIndexID = nullStringPointer(activeIndexID)
	diagnostics.ActiveEmbeddingModelRevision = nullStringPointer(activeModel)
	diagnostics.ParserVersion = nullStringPointer(parserVersion)
	diagnostics.ChunkerVersion = nullStringPointer(chunkerVersion)
	diagnostics.EmbeddingModelRevision = nullStringPointer(modelRevision)
	indexes, err := s.readIndexVersions(ctx, documentID, activeIndexID.String, 20)
	if err != nil {
		return Diagnostics{}, err
	}
	diagnostics.Indexes = indexes
	for index := range indexes {
		if indexes[index].Active {
			active := indexes[index]
			diagnostics.ActiveIndex = &active
			break
		}
	}
	if diagnostics.ActiveIndex != nil {
		diagnostics.ChunkCount = diagnostics.ActiveIndex.ChunkCount
	} else if err := s.DB.QueryRowContext(ctx, `
		SELECT count(*) FROM platform.knowledge_chunks
		 WHERE document_id = $1::uuid AND document_revision = $2`,
		documentID, document.Revision,
	).Scan(&diagnostics.ChunkCount); err != nil {
		return Diagnostics{}, fmt.Errorf("counting the document chunks: %w", err)
	}
	var (
		jobID    string
		jobState string
		jobCode  sql.NullString
		attempts int
	)
	err = s.DB.QueryRowContext(ctx, `
		SELECT id::text, state, error_code, attempt_count
		  FROM platform.background_jobs
		 WHERE kind = 'document_ingest' AND target_id = $1::uuid
		 ORDER BY created_at DESC, id DESC
		 LIMIT 1`, documentID,
	).Scan(&jobID, &jobState, &jobCode, &attempts)
	switch {
	case errors.Is(err, sql.ErrNoRows):
	case err != nil:
		return Diagnostics{}, fmt.Errorf("reading the document job: %w", err)
	default:
		diagnostics.BackgroundJobID = &jobID
		diagnostics.BackgroundJobState = &jobState
		diagnostics.BackgroundJobCode = nullStringPointer(jobCode)
		diagnostics.BackgroundJobAttempts = attempts
	}
	diagnostics.Detail = s.deriveParseFailure(ctx, document)
	return diagnostics, nil
}

// deriveParseFailure re-derives the safe location of a content refusal.
//
// It never writes anything and never becomes an authority: the document row's
// error_code stays the durable fact, and a document that failed for an
// infrastructure reason (or whose source bytes are gone) simply has no detail.
// The recorded parser revision must still name the registered parser, so a later
// build cannot attribute an old failure to new parsing rules.
func (s *Service) deriveParseFailure(ctx context.Context, document Document) *ParseDetail {
	if document.State != StateFailed || document.ErrorCode == nil ||
		!IsParseFailureCode(*document.ErrorCode) {
		return nil
	}
	var (
		objectKey     string
		mediaType     string
		artifactState string
		deleted       sql.NullTime
		recorded      sql.NullString
	)
	err := s.DB.QueryRowContext(ctx, `
		SELECT a.object_key, a.media_type, a.state, a.deleted_at, d.parser_version
		  FROM platform.knowledge_documents AS d
		  JOIN platform.artifacts AS a ON a.id = d.source_artifact_id
		 WHERE d.id = $1::uuid`, document.ID,
	).Scan(&objectKey, &mediaType, &artifactState, &deleted, &recorded)
	if err != nil || deleted.Valid || artifactState != "ready" || !recorded.Valid {
		return nil
	}
	parser, ok := ParserFor(mediaType)
	if !ok || parser.Version() != recorded.String || s.Objects == nil {
		return nil
	}
	reader, _, err := s.Objects.Open(objectKey)
	if err != nil {
		return nil
	}
	defer func() { _ = reader.Close() }()
	content, err := io.ReadAll(io.LimitReader(reader, objectstore.MaxObjectBytes+1))
	if err != nil || int64(len(content)) > objectstore.MaxObjectBytes {
		return nil
	}
	_, parseErr := parser.Parse(content)
	if parseErr == nil {
		// The bytes parse cleanly now; do not invent a location that disagrees
		// with the recorded failure.
		return nil
	}
	typed, ok := asParseError(parseErr)
	if !ok || typed.FailureCode != *document.ErrorCode {
		return nil
	}
	detail := typed.Location
	return &detail
}

func nullStringPointer(value sql.NullString) *string {
	if !value.Valid {
		return nil
	}
	copied := value.String
	return &copied
}

// ---------------------------------------------------------------------------
// the job-scoped ingestion binding (internal mTLS endpoint)
// ---------------------------------------------------------------------------

// IngestBinding is what the document_ingest handler is allowed to learn: the
// document it owns, the exact source artifact revision/hash it must parse, the
// parser revision that matches the declared media type, and the one index version
// this job may stage chunks for. It contains no object key, no DSN and no other
// principal's data.
type IngestBinding struct {
	DocumentID             string `json:"document_id"`
	KnowledgeBaseID        string `json:"knowledge_base_id"`
	DocumentRevision       int64  `json:"document_revision"`
	IndexVersionID         string `json:"index_version_id"`
	IndexVersion           int64  `json:"index_version"`
	EmbeddingModelRevision string `json:"embedding_model_revision"`
	EmbeddingDimension     int    `json:"embedding_dimension"`
	SourceArtifactID       string `json:"source_artifact_id"`
	SourceSHA256           string `json:"source_sha256"`
	SourceMediaType        string `json:"source_media_type"`
	ParserVersion          string `json:"parser_version"`
}

// DocumentIngestBinding validates one document_ingest job against the real
// document and source-artifact rows and advances `scanning -> indexing` the first
// time the handler asks. That transition is the API process's write, not the
// handler's, and it is a CAS on (revision, state), so a stale job cannot move a
// newer revision.
func (s *Service) DocumentIngestBinding(ctx context.Context, job *background.Job) (IngestBinding, error) {
	if job == nil || job.Kind != background.KindDocumentIngest {
		return IngestBinding{}, ErrNotFound
	}
	if !uuid.Valid(job.TargetID) || job.InputArtifactID == nil || !uuid.Valid(*job.InputArtifactID) ||
		job.ExpectedIndexVersionID == nil || !uuid.Valid(*job.ExpectedIndexVersionID) {
		return IngestBinding{}, ErrNotFound
	}
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return IngestBinding{}, fmt.Errorf("starting the binding transaction: %w", err)
	}
	defer func() { _ = tx.Rollback() }()

	var (
		binding      IngestBinding
		state        string
		baseID       string
		sourceID     string
		deleted      sql.NullTime
		artifactSHA  sql.NullString
		artifactType string
		artifactOwn  string
		artifactStat string
		artifactDel  sql.NullTime
	)
	err = tx.QueryRowContext(ctx, `
		SELECT d.id::text, d.knowledge_base_id::text, d.revision, d.state, d.source_artifact_id::text,
		       d.deleted_at
		  FROM platform.knowledge_documents AS d
		  JOIN platform.knowledge_bases AS b ON b.id = d.knowledge_base_id
		 WHERE d.id = $1::uuid AND b.owner_id = $2::uuid
		 FOR UPDATE OF d`,
		job.TargetID, job.OwnerID,
	).Scan(&binding.DocumentID, &baseID, &binding.DocumentRevision, &state, &sourceID, &deleted)
	if errors.Is(err, sql.ErrNoRows) {
		return IngestBinding{}, ErrNotFound
	}
	if err != nil {
		return IngestBinding{}, fmt.Errorf("reading the document binding: %w", err)
	}
	if deleted.Valid || (state != StateScanning && state != StateIndexing) {
		return IngestBinding{}, ErrNotFound
	}
	if job.ExpectedRevision != nil && *job.ExpectedRevision != binding.DocumentRevision {
		return IngestBinding{}, ErrNotFound
	}
	if sourceID != *job.InputArtifactID {
		return IngestBinding{}, ErrNotFound
	}
	err = tx.QueryRowContext(ctx, `
		SELECT owner_id::text, state, media_type, sha256, deleted_at
		  FROM platform.artifacts
		 WHERE id = $1::uuid`,
		sourceID,
	).Scan(&artifactOwn, &artifactStat, &artifactType, &artifactSHA, &artifactDel)
	if errors.Is(err, sql.ErrNoRows) {
		return IngestBinding{}, ErrNotFound
	}
	if err != nil {
		return IngestBinding{}, fmt.Errorf("reading the source artifact: %w", err)
	}
	if artifactOwn != job.OwnerID || artifactDel.Valid || artifactStat != "ready" ||
		!artifactSHA.Valid {
		// A tombstoned or not-yet-ready source is a business refusal: the handler
		// must fail the document instead of waiting on unreadable bytes.
		return IngestBinding{}, ErrArtifactNotReady
	}
	parser, ok := ParserFor(artifactType)
	if !ok {
		return IngestBinding{}, ErrUnsupportedInput
	}
	// The job is bound to one building index version. Re-read it under the
	// document lock: a version that already failed, belongs to another document
	// or was built from another source revision must never be fed by this job.
	var (
		indexDocument  string
		indexRevision  int64
		indexModel     string
		indexDimension int
		indexState     string
	)
	err = tx.QueryRowContext(ctx, `
		SELECT document_id::text, document_revision, embedding_model_revision,
		       embedding_dimension, state, version
		  FROM platform.knowledge_index_versions
		 WHERE id = $1::uuid
		 FOR SHARE`, *job.ExpectedIndexVersionID,
	).Scan(&indexDocument, &indexRevision, &indexModel, &indexDimension, &indexState,
		&binding.IndexVersion)
	if errors.Is(err, sql.ErrNoRows) {
		return IngestBinding{}, ErrStaleRevision
	}
	if err != nil {
		return IngestBinding{}, fmt.Errorf("reading the index version: %w", err)
	}
	if indexDocument != binding.DocumentID || indexRevision != binding.DocumentRevision ||
		indexState != IndexStateBuilding {
		return IngestBinding{}, ErrStaleRevision
	}
	binding.IndexVersionID = *job.ExpectedIndexVersionID
	binding.EmbeddingModelRevision = indexModel
	binding.EmbeddingDimension = indexDimension
	if state == StateScanning {
		if _, err := tx.ExecContext(ctx, `
			UPDATE platform.knowledge_documents
			   SET state = 'indexing', updated_at = now()
			 WHERE id = $1::uuid AND state = 'scanning' AND revision = $2`,
			binding.DocumentID, binding.DocumentRevision,
		); err != nil {
			return IngestBinding{}, fmt.Errorf("advancing the document to indexing: %w", err)
		}
	}
	if err := tx.Commit(); err != nil {
		return IngestBinding{}, fmt.Errorf("committing the binding: %w", err)
	}
	binding.KnowledgeBaseID = baseID
	binding.SourceArtifactID = sourceID
	binding.SourceSHA256 = artifactSHA.String
	binding.SourceMediaType = artifactType
	binding.ParserVersion = parser.Version()
	return binding, nil
}

// StageChunks replaces the chunk rows of one index version with the handler's
// staged set. It is one transaction: a retried attempt either replaces the whole
// version's set or changes nothing. The chunks of a superseded/failed index
// version are never touched by another build, so two builds of the same source
// revision cannot overwrite each other's vectors. It never touches the document
// state; the completion guard activates the version only after re-reading these
// rows.
func StageChunks(ctx context.Context, db *sql.DB, rows []ChunkRow) error {
	if len(rows) == 0 {
		return &ValidationError{Field: "chunks", Message: "at least one chunk is required"}
	}
	documentID := rows[0].DocumentID
	revision := rows[0].Revision
	indexVersionID := rows[0].IndexVersionID
	if !uuid.Valid(indexVersionID) {
		return &ValidationError{Field: "chunks", Message: "an index version is required"}
	}
	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return fmt.Errorf("starting the chunk transaction: %w", err)
	}
	defer func() { _ = tx.Rollback() }()
	if _, err := tx.ExecContext(ctx, `
		DELETE FROM platform.knowledge_chunks
		 WHERE index_version_id = $1::uuid`,
		indexVersionID,
	); err != nil {
		return fmt.Errorf("replacing staged chunks: %w", err)
	}
	for _, row := range rows {
		if row.DocumentID != documentID || row.Revision != revision ||
			row.IndexVersionID != indexVersionID {
			return &ValidationError{
				Field:   "chunks",
				Message: "all chunks must share document, revision and index version",
			}
		}
		citation, err := json.Marshal(row.Citation)
		if err != nil {
			return fmt.Errorf("encoding the chunk citation: %w", err)
		}
		var dimension any
		var vector any
		if len(row.Embedding) > 0 {
			dimension = len(row.Embedding)
			vector = float64Array(row.Embedding)
		}
		if _, err := tx.ExecContext(ctx, `
			INSERT INTO platform.knowledge_chunks
				(id, document_id, document_revision, index_version_id, chunk_no,
				 body_artifact_id, citation, embedding_model_revision, embedding,
				 embedding_dimension)
			VALUES ($1::uuid, $2::uuid, $3, $4::uuid, $5, $6::uuid, $7::jsonb, $8,
			        $9::double precision[], $10)`,
			row.ID, row.DocumentID, row.Revision, row.IndexVersionID, row.No,
			row.BodyArtifactID, string(citation), row.ModelRevision, vector, dimension,
		); err != nil {
			return fmt.Errorf("staging a chunk: %w", err)
		}
	}
	if err := tx.Commit(); err != nil {
		return fmt.Errorf("committing the staged chunks: %w", err)
	}
	return nil
}

// float64Array formats a Go slice as a PostgreSQL array literal. It avoids a
// driver-specific array type and keeps the embedding representation explicit.
func float64Array(vector []float64) string {
	var builder strings.Builder
	builder.WriteByte('{')
	for index, value := range vector {
		if index > 0 {
			builder.WriteByte(',')
		}
		builder.WriteString(formatFloat(value))
	}
	builder.WriteByte('}')
	return builder.String()
}

func formatFloat(value float64) string {
	// strconv.FormatFloat with -1 precision is round-trip exact; using the
	// 'g' format keeps the literal compact.
	return trimFloat(fmt.Sprintf("%v", value))
}

func trimFloat(value string) string {
	if value == "-0" {
		return "0"
	}
	return value
}

// ---------------------------------------------------------------------------
// retrieval (non-contract surface documented in ADR 0010)
// ---------------------------------------------------------------------------

// QueryResult is the non-contract retrieval answer. ACLEpoch is the knowledge
// base revision the answer was filtered against; T067 keys its cache on
// (principal, acl_epoch, embedding revision, query, top_k) and re-checks the
// document/body-artifact state on every hit. Cache is the additive per-request
// cache state (ADR 0014).
type QueryResult struct {
	Items                  []ChunkView
	EmbeddingModelRevision string
	TopK                   int
	ACLEpoch               int64
	// Index is the additive T061 metadata: the embedding revision of the active
	// indexes this answer could read, and how many active indexes were excluded
	// because they were built with another model revision.
	Index RetrievalIndexInfo
	Cache RetrievalCacheInfo
}

// Query ranks the SQL-filtered candidate set for one owner's knowledge base.
//
// The SQL statement applies, in this order and before any ranking:
// owner ACL, knowledge-base/document tombstones, the document's active index
// version (ready), chunk.index_version_id = active index, chunk.document_revision
// = index document_revision, body artifact ready/not-deleted/same owner, and the
// embedding model revision on both the index and the chunk. Only the rows that
// survive are scored. An empty result is a normal empty list.
//
// With the T067 cache enabled a repeated query may be answered from one stored
// entry, but only after the key is recomputed from current state and every
// returned chunk's document/body artifact is re-checked; a stale entry is
// discarded and the request proceeds as a miss.
func (s *Service) Query(
	ctx context.Context, ownerID, baseID, query string, topK int,
) (QueryResult, error) {
	if !uuid.Valid(strings.TrimSpace(baseID)) {
		return QueryResult{}, ErrNotFound
	}
	normalized := strings.TrimSpace(query)
	if normalized == "" {
		return QueryResult{}, &ValidationError{Field: "query", Message: "query is required"}
	}
	if len([]rune(normalized)) > 2000 {
		return QueryResult{}, &ValidationError{
			Field: "query", Message: "query must be at most 2000 characters",
		}
	}
	if topK == 0 {
		topK = DefaultTopK
	}
	if topK < 1 || topK > MaxTopK {
		return QueryResult{}, &ValidationError{
			Field: "top_k", Message: fmt.Sprintf("top_k must be between 1 and %d", MaxTopK),
		}
	}
	base, err := s.GetBase(ctx, ownerID, baseID)
	if err != nil {
		return QueryResult{}, err
	}
	modelRevision := ""
	if s.Embedder != nil {
		modelRevision = s.Embedder.ModelRevision()
	}
	if modelRevision == "" {
		return QueryResult{}, ErrEmbeddingUnavailable
	}
	// The query is embedded before a cache lookup on purpose: the cache must not
	// hide a failing/unconfigured embedding provider, and the key is defined over
	// exactly the string that was embedded.
	queryVector, err := s.Embedder.EmbedOne(ctx, normalized)
	if err != nil {
		if errors.Is(err, embedding.ErrNotConfigured) {
			return QueryResult{}, ErrEmbeddingUnavailable
		}
		return QueryResult{}, fmt.Errorf("embedding the query: %w", err)
	}

	result := QueryResult{
		EmbeddingModelRevision: modelRevision,
		TopK:                   topK,
		ACLEpoch:               base.Revision,
		Cache: RetrievalCacheInfo{
			Enabled:    s.Cache.Enabled,
			TTLSeconds: int64(s.Cache.ttl().Seconds()),
		},
	}
	indexInfo, err := s.retrievalIndexInfo(ctx, ownerID, base.ID, modelRevision)
	if err != nil {
		return QueryResult{}, err
	}
	result.Index = indexInfo
	if s.Cache.Enabled {
		key, keyErr := s.cacheKeyFor(
			ctx, ownerID, base, modelRevision, canonicalCacheQuery(normalized), topK,
		)
		if keyErr != nil {
			// The cache key cannot be derived; the authoritative path still answers.
			result.Cache.Discarded = true
			result.Cache.DiscardReason = CacheDiscardUnavailable
		} else {
			cachedItems, cacheInfo, cacheErr := s.cachedRetrieval(ctx, key)
			if cacheErr == nil && cacheInfo.Hit {
				result.Items = cachedItems
				result.Cache = cacheInfo
				return result, nil
			}
			result.Cache = cacheInfo
			if cacheErr != nil {
				result.Cache.Discarded = true
				result.Cache.DiscardReason = CacheDiscardUnavailable
			}
			ranked, rankErr := s.rankRetrieval(
				ctx, baseID, ownerID, queryVector, modelRevision, topK,
			)
			if rankErr != nil {
				return QueryResult{}, rankErr
			}
			result.Items = ranked.items
			if ok, _ := shouldStoreRetrievalCache(ranked.items, ranked.skipped); ok {
				stored, storeErr := s.storeCachedRetrieval(ctx, key, ranked.cacheItems)
				result.Cache.Stored = storeErr == nil && stored
			}
			return result, nil
		}
	}
	ranked, err := s.rankRetrieval(ctx, baseID, ownerID, queryVector, modelRevision, topK)
	if err != nil {
		return QueryResult{}, err
	}
	result.Items = ranked.items
	return result, nil
}

// rankedRetrieval is the authoritative miss result: the ranked chunks plus the
// stored-cache projection of the same rows (built only for the returned top_k).
type rankedRetrieval struct {
	items      []ChunkView
	cacheItems []retrievalCacheItem
	// skipped counts candidates whose sealed body could not be read; a partial
	// result is never memoized (it would mask a transient unreadable body).
	skipped int
}

// rankRetrieval runs the T055 SQL-filter-first retrieval and ranks the surviving
// candidates in this process. It is the only path that reads chunk bodies for a
// query; a cache hit returns the same shape without touching the candidate set.
func (s *Service) rankRetrieval(
	ctx context.Context, baseID, ownerID string, queryVector []float64,
	modelRevision string, topK int,
) (rankedRetrieval, error) {
	rows, err := s.DB.QueryContext(ctx, `
		SELECT kc.id::text, kc.document_id::text, kc.document_revision, kc.chunk_no,
		       kc.citation, kc.embedding, a.object_key,
		       kc.body_artifact_id::text, coalesce(a.sha256, '')
		  FROM platform.knowledge_chunks AS kc
		  JOIN platform.knowledge_documents AS kd ON kd.id = kc.document_id
		  JOIN platform.knowledge_index_versions AS iv ON iv.id = kd.active_index_id
		  JOIN platform.knowledge_bases AS kb ON kb.id = kd.knowledge_base_id
		  JOIN platform.artifacts AS a ON a.id = kc.body_artifact_id
		 WHERE kb.id = $1::uuid AND kb.owner_id = $2::uuid AND kb.deleted_at IS NULL
		   AND kd.deleted_at IS NULL
		   AND iv.id = kc.index_version_id AND iv.state = 'ready'
		   AND iv.document_id = kd.id
		   AND iv.embedding_model_revision = $3
		   AND kc.document_revision = iv.document_revision
		   AND kc.embedding IS NOT NULL
		   AND kc.embedding_model_revision = $3
		   AND kc.embedding_dimension = iv.embedding_dimension
		   AND a.owner_id = kb.owner_id
		   AND a.deleted_at IS NULL AND a.state = 'ready'
		 ORDER BY kc.document_id, kc.chunk_no
		 LIMIT $4`,
		baseID, ownerID, modelRevision, MaxCandidates,
	)
	if err != nil {
		return rankedRetrieval{}, fmt.Errorf("reading the retrieval candidates: %w", err)
	}
	defer func() { _ = rows.Close() }()

	type candidate struct {
		view           ChunkView
		embedding      []float64
		objectKey      string
		bodyArtifactID string
		bodySHA256     string
	}
	var candidates []candidate
	for rows.Next() {
		var (
			view           ChunkView
			citation       []byte
			vectorText     sql.NullString
			objectKey      string
			bodyArtifactID string
			bodySHA256     string
		)
		if err := rows.Scan(
			&view.ChunkID, &view.DocumentID, &view.DocumentRevision, &view.ChunkNo,
			&citation, &vectorText, &objectKey, &bodyArtifactID, &bodySHA256,
		); err != nil {
			return rankedRetrieval{}, fmt.Errorf("reading a retrieval candidate: %w", err)
		}
		view.Citation = json.RawMessage(citation)
		view.Span = spanFromCitation(citation)
		candidates = append(candidates, candidate{
			view: view, embedding: parseFloatArray(vectorText.String), objectKey: objectKey,
			bodyArtifactID: bodyArtifactID, bodySHA256: bodySHA256,
		})
	}
	if err := rows.Err(); err != nil {
		return rankedRetrieval{}, fmt.Errorf("reading the retrieval candidates: %w", err)
	}

	type scored struct {
		candidate candidate
		score     float64
	}
	ranked := make([]scored, 0, len(candidates))
	skipped := 0
	for _, item := range candidates {
		text, err := s.readChunkText(item.objectKey)
		if err != nil {
			// A chunk whose sealed body is unreadable is not retrievable: skipping
			// it is honest, and it never leaks bytes or a path. It also marks the
			// result partial, so it will not be memoized.
			skipped++
			continue
		}
		item.view.Text = text
		item.view.Score = embedding.Cosine(queryVector, item.embedding)
		ranked = append(ranked, scored{candidate: item, score: item.view.Score})
	}
	sort.SliceStable(ranked, func(left, right int) bool {
		return chunkLess(ranked[left].candidate.view, ranked[right].candidate.view)
	})
	if len(ranked) > topK {
		ranked = ranked[:topK]
	}
	result := rankedRetrieval{
		items:      make([]ChunkView, 0, len(ranked)),
		cacheItems: make([]retrievalCacheItem, 0, len(ranked)),
		skipped:    skipped,
	}
	for _, item := range ranked {
		item.candidate.view.DocumentState = StateReady
		result.items = append(result.items, item.candidate.view)
		result.cacheItems = append(result.cacheItems, cacheItemOf(
			item.candidate.view, item.candidate.bodyArtifactID, item.candidate.bodySHA256,
		))
	}
	return result, nil
}

// GetChunk opens one citation the owner is currently allowed to read.
//
// The ACL, document tombstone, ready/active revision and body-artifact
// readiness are re-checked in SQL before a byte is opened. A revoked, deleted,
// failed, superseded-revision or foreign chunk is ErrNotFound (404) with no body
// and no reason that would reveal which of those it is.
func (s *Service) GetChunk(ctx context.Context, ownerID, chunkID string) (ChunkView, error) {
	if !uuid.Valid(strings.TrimSpace(chunkID)) {
		return ChunkView{}, ErrNotFound
	}
	var (
		view      ChunkView
		citation  []byte
		objectKey string
	)
	err := s.DB.QueryRowContext(ctx, `
		SELECT kc.id::text, kc.document_id::text, kc.document_revision, kc.chunk_no,
		       kc.citation, a.object_key
		  FROM platform.knowledge_chunks AS kc
		  JOIN platform.knowledge_documents AS kd ON kd.id = kc.document_id
		  JOIN platform.knowledge_index_versions AS iv ON iv.id = kd.active_index_id
		  JOIN platform.knowledge_bases AS kb ON kb.id = kd.knowledge_base_id
		  JOIN platform.artifacts AS a ON a.id = kc.body_artifact_id
		 WHERE kc.id = $1::uuid AND kb.owner_id = $2::uuid AND kb.deleted_at IS NULL
		   AND kd.deleted_at IS NULL
		   AND iv.id = kc.index_version_id AND iv.state = 'ready'
		   AND iv.document_id = kd.id
		   AND kc.document_revision = iv.document_revision
		   AND a.owner_id = kb.owner_id
		   AND a.deleted_at IS NULL AND a.state = 'ready'`,
		chunkID, ownerID,
	).Scan(&view.ChunkID, &view.DocumentID, &view.DocumentRevision, &view.ChunkNo,
		&citation, &objectKey)
	if errors.Is(err, sql.ErrNoRows) {
		return ChunkView{}, ErrNotFound
	}
	if err != nil {
		return ChunkView{}, fmt.Errorf("reading the chunk: %w", err)
	}
	view.Citation = json.RawMessage(citation)
	view.Span = spanFromCitation(citation)
	text, err := s.readChunkText(objectKey)
	if err != nil {
		return ChunkView{}, ErrNotFound
	}
	view.Text = text
	view.DocumentState = StateReady
	return view, nil
}

// spanFromCitation re-projects the stored citation's span onto the answer. The
// citation stays the single stored traceability document; this never invents a
// range, it only exposes the recorded one.
func spanFromCitation(citation []byte) Span {
	var stored struct {
		Span Span `json:"span"`
	}
	if err := json.Unmarshal(citation, &stored); err != nil {
		return Span{}
	}
	return stored.Span
}

// readChunkText reads one prepared sealed chunk body. It never returns more than
// the single-object ceiling and never an object key.
func (s *Service) readChunkText(objectKey string) (string, error) {
	if s.Objects == nil {
		return "", ErrObjectStoreUnavailable
	}
	reader, _, err := s.Objects.Open(objectKey)
	if err != nil {
		return "", err
	}
	defer func() { _ = reader.Close() }()
	content, err := io.ReadAll(io.LimitReader(reader, objectstore.MaxObjectBytes+1))
	if err != nil {
		return "", err
	}
	if int64(len(content)) > objectstore.MaxObjectBytes {
		return "", objectstore.ErrTooLarge
	}
	return string(content), nil
}

// ---------------------------------------------------------------------------
// shared helpers
// ---------------------------------------------------------------------------

func decodeKeysetCursor(raw string) (*time.Time, *string, error) {
	if strings.TrimSpace(raw) == "" {
		return nil, nil, nil
	}
	createdAt, id, err := catalog.DecodeCursor(raw)
	if err != nil {
		return nil, nil, err
	}
	return &createdAt, &id, nil
}

func parseFloatArray(raw string) []float64 {
	trimmed := strings.TrimSpace(raw)
	if len(trimmed) < 2 || trimmed[0] != '{' || trimmed[len(trimmed)-1] != '}' {
		return nil
	}
	body := trimmed[1 : len(trimmed)-1]
	if strings.TrimSpace(body) == "" {
		return nil
	}
	parts := strings.Split(body, ",")
	values := make([]float64, 0, len(parts))
	for _, part := range parts {
		var value float64
		if _, err := fmt.Sscanf(strings.TrimSpace(part), "%g", &value); err != nil {
			return nil
		}
		values = append(values, value)
	}
	return values
}

// chunkLess is the ranking order: score DESC, then a deterministic tiebreak that
// does not depend on map/database order.
func chunkLess(left, right ChunkView) bool {
	if left.Score != right.Score {
		return left.Score > right.Score
	}
	if left.DocumentID != right.DocumentID {
		return left.DocumentID < right.DocumentID
	}
	return left.ChunkNo < right.ChunkNo
}
