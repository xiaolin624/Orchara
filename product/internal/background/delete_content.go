package background

// F11/T071: the delete_content completion guard.
//
// The handler only deletes the sealed bytes named by the deletion job's scopes.
// This guard is the server-side authority that turns one attempt into evidence:
// in the same transaction that will complete the background job it
//
//   - locks the platform.deletion_jobs row (one writer per deletion),
//   - re-reads the scope descriptors and derives the same object/index/cache set,
//   - verifies every named sealed object is really gone (objectstore.Stat),
//   - deletes the chunk/vector/index-version and retrieval-cache rows the job
//     names, and marks the owning artifact rows purged/deleted,
//   - moves the cleared scopes from remaining_scopes to completed_scopes, and
//   - records the honest state: `pending` while T072's four scopes are still
//     owed, `failed` with a safe code when one of this ticket's scopes could not
//     be cleared, `completed` only when no scope remains.
//
// The proposal override rules matter: a guard must never return an error for
// "the bytes are still there" (F11 notes §3.6 — a guard error aborts the whole
// maintenance run and leaves the job leased until the sweep). A still-present
// object therefore becomes a retryable Failed(transient_storage_io), exactly the
// retry table entry delete_content registers. A shared object becomes the
// non-retryable object_shared_reference: the over-deletion refusal is a fact the
// operator must see, not a transient glitch.

import (
	"context"
	"database/sql"
	"time"

	"orchara/platform/internal/deletion"
)

// DefaultDeleteContentTimeout bounds one purge attempt when the deployment did
// not configure one. A purge is a bounded number of file deletions plus one
// evidence transaction; two minutes is generous for a document with thousands of
// chunk bodies and short enough that a wedged object store cannot hold the work
// slot for the whole 30s lease and beyond.
const DefaultDeleteContentTimeout = 2 * time.Minute

// guardDeleteContentCompletion is registered in complete.go's completionGuards.
func guardDeleteContentCompletion(
	ctx context.Context,
	c *Controller,
	tx *sql.Tx,
	job *Job,
	completion *Completion,
) error {
	result, err := deletion.ApplyPurgeOutcome(ctx, tx, c.Store, job.TargetID)
	if err != nil {
		// A real database/consistency failure: the transaction cannot be trusted,
		// so the completion is refused. This is never "content still present".
		return err
	}
	switch {
	case result.Done:
		completion.State = StateSucceeded
		completion.ErrorCode = ""
		completion.ResultArtifactID = ""
	case result.Failure == deletion.PurgeOK:
		// Every scope this ticket owns is cleared; T072's four are still owed, so
		// the deletion job honestly stays `pending`. The purge attempt itself
		// succeeded.
		completion.State = StateSucceeded
		completion.ErrorCode = ""
		completion.ResultArtifactID = ""
	default:
		completion.State = StateFailed
		// F11/T076: a bounded attempt that expired keeps its timeout
		// classification. The bytes are still there *because* the attempt ran out
		// of time, and the operator alert surface must be able to tell a slow store
		// (retryable `delete_timeout`) from a store error. The deletion job's own
		// error code still records the evidence-derived failure, so the timeout is
		// reported on the attempt, never used to hide the outstanding scope.
		if completion.ErrorCode == CodeDeleteTimeout &&
			result.Failure == deletion.PurgeObjectsPresent {
			completion.ErrorCode = CodeDeleteTimeout
		} else {
			completion.ErrorCode = purgeFailureCode(result.Failure)
		}
		completion.ResultArtifactID = ""
	}
	return nil
}

// purgeFailureCode maps the engine's outcome onto the controller's closed code
// vocabulary. transient_* codes are in the fixed delete_content retry table;
// object_shared_reference and purge_unsupported_target are not (retrying cannot
// make a live reference vanish or teach this build a new resource type).
func purgeFailureCode(failure deletion.PurgeFailure) string {
	switch failure {
	case deletion.PurgeObjectsPresent:
		return CodeTransientStorageIO
	case deletion.PurgeIndexRemain, deletion.PurgeCacheRemain:
		return CodeTransientInternal
	case deletion.PurgeSharedReference:
		return CodeObjectSharedReference
	case deletion.PurgeUnsupportedTarget:
		return CodePurgeUnsupportedTarget
	default:
		return CodeTransientInternal
	}
}
