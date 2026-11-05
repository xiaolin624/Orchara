package execution

// F07/T078: the seam that validates the terminal result of a delegated child Run
// against the child version's frozen output schema.
//
// The rule the ticket implements (EXECUTION.md §7, cited in the T078 report):
// a child Run's result must conform to the output schema frozen in the child
// version it was spawned from. A non-conforming result fails the *child* Run with
// `child_output_schema_invalid`; the parent is untouched (the parent decides what
// to do with a failed required child, which is F07/T080/T081).
//
// Why a seam and not a direct dependency: the schema and the artifact bytes live
// in the catalog/object-store world, and this package must not import the
// catalog (catalog already imports execution for AppendRunStatusTx). api.New
// wires the implementation (`internal/children.ResultValidator`), which reads the
// `run_children` receipt added by migration 0045 and validates the output
// artifact bytes with the shared restricted-schema validator.
//
// Only a run that actually has a `run_children` row is validated: ordinary runs
// (debug, top-level published) keep their exact previous finalizer behaviour.

import (
	"context"
	"database/sql"
	"errors"
)

// InvalidRunResultError is a *successful* result proposal whose content violates
// the run's frozen result schema. The finalizer converts it into a failed Run with
// this stable code; it is never stored as a transient server error.
type InvalidRunResultError struct {
	Code    string
	Message string
}

func (e *InvalidRunResultError) Error() string {
	if e.Message == "" {
		return e.Code
	}
	return e.Message
}

// ChildOutputSchemaInvalidCode is the stable run error_code for a child whose
// terminal result violates its frozen output schema.
const ChildOutputSchemaInvalidCode = "child_output_schema_invalid"

// RunResultValidator is the optional finalizer check. ValidateRunResult is called
// inside the finalizer transaction after the output artifact's ownership/state
// checks, so it may read rows with the transaction. Returning nil accepts the
// proposal; returning *InvalidRunResultError fails the Run with that code; any
// other error aborts the transaction (the worker retries, nothing is committed).
type RunResultValidator interface {
	ValidateRunResult(
		ctx context.Context, tx *sql.Tx, runID, outputArtifactID string,
	) error
}

// ValidateRunResult is a no-op on a Service without a wired validator.
func (s *Service) validateRunResult(
	ctx context.Context, tx *sql.Tx, runID, outputArtifactID string,
) error {
	if s.ResultValidator == nil {
		return nil
	}
	err := s.ResultValidator.ValidateRunResult(ctx, tx, runID, outputArtifactID)
	if err == nil {
		return nil
	}
	var invalid *InvalidRunResultError
	if errors.As(err, &invalid) {
		if invalid.Code == "" {
			invalid.Code = ChildOutputSchemaInvalidCode
		}
		return invalid
	}
	return err
}
