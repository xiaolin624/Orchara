package handlers

// F11/T071: the delete_content handler.
//
// It performs the physical half of one purge attempt and nothing else: read the
// deletion job's scope descriptors (the documented T022-style interim read with
// the maintenance database identity), then delete every sealed object the job
// names and no live resource still references. It writes no business row; the
// completion guard re-reads the same descriptors, verifies the objects are gone,
// deletes the derived index/cache rows and records the scope progress in one
// evidence transaction.
//
// Why the handler does not delete database rows: a handler result is a proposal.
// If it wrote chunk/cache rows and then crashed before submitting, the deletion
// job would claim work that has no evidence. Keeping "move the bytes" and "record
// the evidence" separate is what makes a replayed or partially failed attempt
// safe: object deletion is idempotent (objectstore.Delete of a missing key
// succeeds), and the guard's verification is the only path that advances a scope.

import (
	"context"
	"errors"

	"orchara/platform/internal/background"
	"orchara/platform/internal/deletion"
	"orchara/platform/internal/uuid"
)

// DeleteContent implements the delete_content kind.
func DeleteContent(ctx context.Context, work background.HandlerWork) background.Outcome {
	if work.DB == nil || work.Store == nil || work.Job == nil ||
		!uuid.Valid(work.Job.TargetID) {
		// The runtime refuses to start without the store/database; a hand-built
		// work value is an infrastructure failure, never a silent success.
		return background.Failed(background.CodeTransientInternal)
	}
	timeout := work.Settings.DeleteContentTimeout
	if timeout <= 0 {
		timeout = background.DefaultDeleteContentTimeout
	}
	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()

	targets, err := deletion.LoadPurgeTargets(ctx, work.DB, work.Job.TargetID)
	if err != nil {
		if errors.Is(err, deletion.ErrNotFound) {
			// The deletion job is the authority and is never deleted; if it is
			// gone, acting on a guessed target would be worse than failing.
			return background.Failed(background.CodeSourceDeleted)
		}
		if ctx.Err() != nil {
			// F11/T076: the bounded attempt expired before it could even read its
			// scope set. That is the retryable timeout code (the deletion alert
			// surface reports it), not an internal error: a slow object store must
			// not look like a bug, and a retry with the normal deadline finishes.
			return background.Failed(background.CodeDeleteTimeout)
		}
		return background.Failed(background.CodeTransientInternal)
	}
	if len(targets.Job.Remaining) == 0 {
		// Nothing is owed; the guard's replay check will record the no-op.
		return background.Succeeded()
	}
	for _, object := range targets.Objects {
		if object.Shared {
			// A live document/memory still reads these bytes. The guard records
			// the non-retryable shared-reference refusal.
			continue
		}
		if err := work.Store.Delete(object.Key); err != nil {
			if ctx.Err() != nil {
				return background.Failed(background.CodeDeleteTimeout)
			}
			return background.Failed(background.CodeTransientStorageIO)
		}
	}
	return background.Succeeded()
}
