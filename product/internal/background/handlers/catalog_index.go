package handlers

// F08/T077 catalog_index handler.
//
// One job reconciles exactly one projection row from the authoritative
// `application_versions` row. The handler
//
//   - re-reads the version and its application inside the write transaction (the
//     authoritative re-check): a version revoked, archived or gone since enqueue
//     deletes its projection row instead of writing a stale one;
//   - refuses a version whose capability card is not a searchable object with a
//     non-empty purpose (`catalog_source_invalid`), so malformed metadata never
//     reaches the projection;
//   - verifies the job's expected manifest hash still matches the version, so a
//     job bound to different bytes cannot rewrite the projection.
//
// It never reads the knowledge tables and never claims a rebuild epoch; the full
// rebuild ledger belongs to `orchara-admin catalog rebuild`.

import (
	"context"
	"errors"

	"orchara/platform/internal/background"
	"orchara/platform/internal/catalog"
)

// CatalogIndex executes one catalog projection reconciliation.
func CatalogIndex(ctx context.Context, work background.HandlerWork) background.Outcome {
	if work.DB == nil || work.Job == nil {
		return background.Failed(background.CodeTransientInternal)
	}
	reader := catalog.New(work.DB, nil)
	outcome, err := reader.IndexVersionNow(ctx, work.Job.TargetID)
	switch {
	case errors.Is(err, catalog.ErrCatalogSourceInvalid):
		return background.Failed(catalog.CodeCatalogSourceInvalid)
	case errors.Is(err, catalog.ErrCatalogSourceMissing):
		return background.Failed(catalog.CodeCatalogSourceMissing)
	case err != nil:
		return background.Failed(background.CodeTransientInternal)
	}
	if work.Job.ExpectedSHA256 != nil && outcome.ManifestSHA256 != "" &&
		*work.Job.ExpectedSHA256 != outcome.ManifestSHA256 {
		return background.Failed(catalog.CodeCatalogSourceChanged)
	}
	return background.Succeeded()
}
