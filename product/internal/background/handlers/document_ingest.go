package handlers

// F10/T055 document_ingest handler.
//
// It performs the handler half of the ingestion pipeline and nothing more:
//
//	job-scoped binding -> signed download of the bound source artifact -> parse
//	(txt/md) -> deterministic chunking -> local embedding -> one sealed body
//	artifact per chunk (the exact existing upload/complete/finalize flow) ->
//	wait for those artifacts to be activated -> stage the chunk rows.
//
// It never marks the document ready and never writes state/active_revision: the
// completion guard in internal/background re-verifies the staged set in the same
// transaction that activates the revision. Empty parse, an unsupported/corrupt
// source or any handler failure therefore ends in a failed job and, through the
// guard, a failed document with a specific code — never ready.
//
// Only the declared source media type selects the parser; the handler never
// guesses a format from a file name and never runs a shell.

import (
	"context"
	"errors"
	"time"

	"orchara/platform/internal/background"
	"orchara/platform/internal/embedding"
	"orchara/platform/internal/knowledge"
	"orchara/platform/internal/objectstore"
	"orchara/platform/internal/uuid"
)

// Document-ingest wait defaults. The chunk body artifacts are finalized by the
// system_io pool, which is why document_ingest belongs to the work pool
// (BACKGROUND-JOBS §3 / ADR 0005 §1): waiting must not consume the pool that
// makes progress.
const (
	defaultChunkArtifactWaitTimeout = 60 * time.Second
	defaultChunkArtifactPoll        = 100 * time.Millisecond
)

// DocumentIngest executes one ingestion attempt.
func DocumentIngest(ctx context.Context, work background.HandlerWork) background.Outcome {
	if work.Scope == nil || work.DB == nil || work.Embedder == nil || work.Job == nil {
		return background.Failed(background.CodeTransientInternal)
	}
	binding, err := work.Scope.DocumentIngest(ctx, work.Job.ID, work.AttemptToken)
	if err != nil {
		return documentIngestBindingOutcome(err)
	}
	link, err := work.Scope.Download(ctx, work.Job.ID, work.AttemptToken, binding.SourceArtifactID)
	if err != nil {
		return documentIngestScopeOutcome(err, background.CodeIngestSourceUnavailable)
	}
	content, err := work.Scope.FetchURL(ctx, link, objectstore.MaxObjectBytes)
	if err != nil {
		return background.Failed(background.CodeTransientStorageIO)
	}
	if sha256Hex(content) != binding.SourceSHA256 {
		// The job is bound to one immutable artifact revision. Different bytes
		// are a deterministic refusal, not something a retry can fix.
		return background.Failed(background.CodeIngestSourceChanged)
	}

	parser, ok := knowledge.ParserFor(binding.SourceMediaType)
	if !ok {
		return background.Failed(background.CodeIngestUnsupportedMediaType)
	}
	parsed, err := parser.Parse(content)
	switch {
	case errors.Is(err, knowledge.ErrEmptyContent):
		return background.Failed(background.CodeIngestEmptyContent)
	case errors.Is(err, knowledge.ErrInvalidEncoding):
		return background.Failed(background.CodeIngestInvalidEncoding)
	case errors.Is(err, knowledge.ErrUnsupportedMediaType):
		return background.Failed(background.CodeIngestUnsupportedMediaType)
	case err != nil:
		return background.Failed(background.CodeTransientInternal)
	}

	chunks := knowledge.ChunkText(parsed.Text)
	if len(chunks) == 0 {
		return background.Failed(background.CodeIngestEmptyContent)
	}
	if len(chunks) > knowledge.MaxChunksPerDocument {
		return background.Failed(background.CodeIngestTooManyChunks)
	}
	runes := []rune(parsed.Text)
	for _, chunk := range chunks {
		// The chunker's invariant is the citation's foundation: a span that does
		// not slice back to the chunk text is a platform defect, never data the
		// user must fix.
		if chunk.Span.StartChar < 0 || chunk.Span.EndChar > len(runes) ||
			chunk.Span.StartChar >= chunk.Span.EndChar ||
			string(runes[chunk.Span.StartChar:chunk.Span.EndChar]) != chunk.Text {
			return background.Failed(background.CodeIngestCitationInvalid)
		}
	}

	texts := make([]string, len(chunks))
	for index := range chunks {
		texts[index] = chunks[index].Text
	}
	vectors, err := work.Embedder.Embed(ctx, texts)
	if err != nil {
		if errors.Is(err, embedding.ErrNotConfigured) {
			return background.Failed(background.CodeEmbeddingProviderNotConfigured)
		}
		return background.Failed(background.CodeTransientInternal)
	}
	if len(vectors) != len(chunks) {
		return background.Failed(background.CodeTransientInternal)
	}

	rows := make([]knowledge.ChunkRow, 0, len(chunks))
	artifactIDs := make([]string, 0, len(chunks))
	for index, chunk := range chunks {
		if work.Heartbeat != nil {
			_ = work.Heartbeat(ctx)
		}
		body := []byte(chunk.Text)
		digest := sha256Hex(body)
		ticket, err := work.Scope.Upload(ctx, work.Job.ID, work.AttemptToken, background.UploadRequest{
			MediaType: "text/plain",
			SizeBytes: int64(len(body)),
			Purpose:   "knowledge_chunk",
		})
		if err != nil {
			return documentIngestScopeOutcome(err, background.CodeIngestIncomplete)
		}
		if err := work.Scope.PutURL(ctx, ticket.URL, body); err != nil {
			return background.Failed(background.CodeTransientStorageIO)
		}
		if _, err := work.Scope.Complete(
			ctx, work.Job.ID, work.AttemptToken, ticket.ArtifactID, digest,
		); err != nil {
			return documentIngestScopeOutcome(err, background.CodeTransientStorageIO)
		}
		artifactIDs = append(artifactIDs, ticket.ArtifactID)
		rows = append(rows, knowledge.ChunkRow{
			ID:             uuid.NewString(),
			DocumentID:     binding.DocumentID,
			Revision:       binding.DocumentRevision,
			No:             chunk.No,
			BodyArtifactID: ticket.ArtifactID,
			Citation: knowledge.Citation{
				DocumentID:             binding.DocumentID,
				DocumentRevision:       binding.DocumentRevision,
				ChunkNo:                chunk.No,
				Span:                   chunk.Span,
				OffsetUnit:             "rune",
				SourceArtifactID:       binding.SourceArtifactID,
				SourceSHA256:           binding.SourceSHA256,
				SourceMediaType:        binding.SourceMediaType,
				TextNormalization:      parsed.Normalization,
				ParserVersion:          binding.ParserVersion,
				ChunkerVersion:         knowledge.ChunkerVersion,
				EmbeddingModelRevision: work.Embedder.ModelRevision(),
			},
			ModelRevision: work.Embedder.ModelRevision(),
			Embedding:     vectors[index],
		})
	}

	if outcome, ok := waitForChunkArtifacts(ctx, work, artifactIDs); !ok {
		return outcome
	}
	if err := knowledge.StageChunks(ctx, work.DB, rows); err != nil {
		return background.Failed(background.CodeTransientInternal)
	}
	return background.Succeeded()
}

// waitForChunkArtifacts waits until every chunk body artifact is activated by
// the finalize guard. A quarantined/deleted body is a deterministic failure; a
// bounded timeout is retryable so a slow system_io pool is not reported as a
// content rejection.
func waitForChunkArtifacts(
	ctx context.Context, work background.HandlerWork, artifactIDs []string,
) (background.Outcome, bool) {
	timeout := work.Settings.ArtifactReadyTimeout
	if timeout <= 0 {
		timeout = defaultChunkArtifactWaitTimeout
	}
	poll := work.Settings.ArtifactReadyPoll
	if poll <= 0 {
		poll = defaultChunkArtifactPoll
	}
	deadline := time.Now().Add(timeout)
	for {
		if ctx.Err() != nil {
			return background.Failed(background.CodeTransientInternal), false
		}
		ready := 0
		for _, artifactID := range artifactIDs {
			view, err := work.Scope.Status(ctx, work.Job.ID, work.AttemptToken, artifactID)
			if err != nil {
				return documentIngestScopeOutcome(err, background.CodeTransientStorageIO), false
			}
			switch view.State {
			case "ready":
				ready++
			case "quarantined", "deleted":
				return background.Failed(background.CodeIngestIncomplete), false
			}
		}
		if ready == len(artifactIDs) {
			return background.Succeeded(), true
		}
		if time.Now().After(deadline) {
			return background.Failed(background.CodeIngestChunkArtifactTimeout), false
		}
		if work.Heartbeat != nil {
			_ = work.Heartbeat(ctx)
		}
		select {
		case <-ctx.Done():
			return background.Failed(background.CodeTransientInternal), false
		case <-time.After(poll):
		}
	}
}

// documentIngestBindingOutcome maps the internal API's stable codes for the
// job-scoped binding onto document failure codes.
func documentIngestBindingOutcome(err error) background.Outcome {
	var coded interface{ ErrorCode() string }
	if errors.As(err, &coded) {
		switch coded.ErrorCode() {
		case background.CodeStaleAttempt:
			return background.Failed(background.CodeStaleAttempt)
		case "resource_not_found":
			return background.Failed(background.CodeSourceDeleted)
		case "precondition_failed":
			return background.Failed(background.CodeIngestSourceUnavailable)
		case "validation_failed":
			return background.Failed(background.CodeIngestUnsupportedMediaType)
		}
	}
	return background.Failed(background.CodeTransientInternal)
}

// documentIngestScopeOutcome maps a job-scoped artifact call failure. The
// fallback is the code used when the failure is not a recognizable contract
// refusal.
func documentIngestScopeOutcome(err error, fallback string) background.Outcome {
	var coded interface{ ErrorCode() string }
	if errors.As(err, &coded) {
		switch coded.ErrorCode() {
		case background.CodeStaleAttempt:
			return background.Failed(background.CodeStaleAttempt)
		case "artifact_not_found":
			return background.Failed(background.CodeSourceDeleted)
		case "artifact_not_ready":
			return background.Failed(background.CodeIngestSourceUnavailable)
		case "artifact_conflict", "validation_failed":
			return background.Failed(background.CodeIngestIncomplete)
		case background.CodeObjectStoreUnavailable, "object_signing_unavailable":
			return background.Failed(background.CodeObjectStoreUnavailable)
		}
	}
	return background.Failed(fallback)
}
