package children

// F07/T078: the frozen-output-schema gate for a delegated child Run.
//
// The finalizer calls this seam only inside its transaction, after the output
// artifact's ownership/state checks. It answers three questions:
//
//  1. is this Run a delegated child? (a `run_children` receipt exists) — an
//     ordinary Run keeps the previous finalizer behaviour;
//  2. what output schema was frozen for that child? (the child version's
//     `manifest.definition.output_schema`) — never the live draft, never the
//     call_app node's refinement;
//  3. do the sealed output bytes satisfy it? The platform's shared restricted
//     JSON-Schema validator is the same one publication used.
//
// A violation returns *execution.InvalidRunResultError, which the finalizer
// converts into a failed child Run with `child_output_schema_invalid`. The parent
// Run is not touched: T080/T081 own what a required-child failure does to the
// parent.

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"strings"

	"orchara/platform/internal/catalog"
	"orchara/platform/internal/execution"
	"orchara/platform/internal/jcs"
	"orchara/platform/internal/objectstore"
)

// ResultValidator implements execution.RunResultValidator over the real catalog
// rows, the sealed object store and the `run_children` receipt.
type ResultValidator struct {
	DB      *sql.DB
	Objects *objectstore.Store
}

// NewResultValidator builds the finalizer seam. A nil object store refuses a
// delegated result instead of accepting bytes it cannot verify.
func NewResultValidator(db *sql.DB, objects *objectstore.Store) *ResultValidator {
	return &ResultValidator{DB: db, Objects: objects}
}

// ValidateRunResult implements execution.RunResultValidator.
func (v *ResultValidator) ValidateRunResult(
	ctx context.Context, tx *sql.Tx, runID, outputArtifactID string,
) error {
	var versionID string
	err := tx.QueryRowContext(ctx, `
		SELECT child_version_id::text
		  FROM platform.run_children
		 WHERE child_run_id = $1::uuid`, runID).Scan(&versionID)
	if errors.Is(err, sql.ErrNoRows) {
		return nil
	}
	if err != nil {
		return fmt.Errorf("reading the child delegation receipt: %w", err)
	}
	if v.Objects == nil {
		return &execution.InvalidRunResultError{
			Code:    execution.ChildOutputSchemaInvalidCode,
			Message: "the child result cannot be validated: no object store is configured",
		}
	}
	definition, err := v.childDefinitionTx(ctx, tx, versionID)
	if err != nil {
		return err
	}
	output, err := v.outputArtifactTx(ctx, tx, outputArtifactID)
	if err != nil {
		return err
	}
	located := catalog.ValidateChildResult(definition, output)
	if len(located) == 0 {
		return nil
	}
	return &execution.InvalidRunResultError{
		Code: execution.ChildOutputSchemaInvalidCode,
		Message: fmt.Sprintf(
			"the child result does not satisfy the frozen output schema at %s: %s",
			located[0].Pointer, located[0].Message),
	}
}

func (v *ResultValidator) childDefinitionTx(
	ctx context.Context, tx *sql.Tx, versionID string,
) (map[string]any, error) {
	var manifestText string
	err := tx.QueryRowContext(ctx, `
		SELECT manifest::text FROM platform.application_versions WHERE id = $1::uuid`,
		versionID).Scan(&manifestText)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, &execution.InvalidRunResultError{
			Code:    execution.ChildOutputSchemaInvalidCode,
			Message: "the child application version no longer exists",
		}
	}
	if err != nil {
		return nil, fmt.Errorf("reading the child application version: %w", err)
	}
	definition, ok := catalog.DefinitionOfManifest([]byte(manifestText))
	if !ok {
		return nil, &execution.InvalidRunResultError{
			Code:    execution.ChildOutputSchemaInvalidCode,
			Message: "the child application version has no frozen execution definition",
		}
	}
	return definition, nil
}

// outputArtifactTx reads the sealed bytes and proves them against the artifact
// row. The finalizer already checked ownership/state; the bytes are still
// re-hashed because the schema decision must be made on the exact bytes.
func (v *ResultValidator) outputArtifactTx(
	ctx context.Context, tx *sql.Tx, artifactID string,
) (map[string]any, error) {
	var (
		objectKey string
		sha256    string
		size      int64
	)
	err := tx.QueryRowContext(ctx, `
		SELECT object_key, sha256, size_bytes
		  FROM platform.artifacts WHERE id = $1::uuid`, artifactID,
	).Scan(&objectKey, &sha256, &size)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, &execution.InvalidRunResultError{
			Code:    execution.ChildOutputSchemaInvalidCode,
			Message: "the child output artifact no longer exists",
		}
	}
	if err != nil {
		return nil, fmt.Errorf("reading the child output artifact: %w", err)
	}
	reader, _, err := v.Objects.Open(objectKey)
	if err != nil {
		return nil, fmt.Errorf("opening the child output artifact: %w", err)
	}
	defer reader.Close()
	payload, err := io.ReadAll(io.LimitReader(reader, MaxInputBytes+1))
	if err != nil {
		return nil, fmt.Errorf("reading the child output artifact: %w", err)
	}
	if int64(len(payload)) != size || !strings.EqualFold(jcs.SHA256Hex(payload), sha256) {
		return nil, &execution.InvalidRunResultError{
			Code:    execution.ChildOutputSchemaInvalidCode,
			Message: "the child output artifact bytes do not match the recorded hash",
		}
	}
	var output map[string]any
	if err := json.Unmarshal(payload, &output); err != nil || output == nil {
		return nil, &execution.InvalidRunResultError{
			Code:    execution.ChildOutputSchemaInvalidCode,
			Message: "the child output artifact is not a JSON object",
		}
	}
	return output, nil
}
