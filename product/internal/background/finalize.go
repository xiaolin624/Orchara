package background

// Per-attempt object fencing for artifact_finalize (second review fix).
//
// T022 originally had the handler write straight to platform.artifacts.object_key
// and had the completion guard re-hash that same key. That was correct only while
// one attempt at a time could exist: an attempt that was paused after it read the
// artifact row could resume after a retry had already activated the artifact and
// then overwrite (or, on a hash/size mismatch, delete) the now-active sealed
// object. BACKGROUND-JOBS §3 requires "后续覆盖不影响 sealed 对象".
//
// The fence is two halves that cannot diverge because both derive the key through
// objectstore.AttemptSealedKey(jobID, attemptToken):
//
//   - the handler writes/verifies/deletes only its own attempt key;
//   - the guard verifies that key and, only after every fact still agrees, moves
//     the verified bytes to artifact.object_key in one atomic rename, then
//     activates the row. Any rejection removes only the attempt key.
//
// A retry mints a new lease token and therefore a new attempt key, so an older
// attempt can never name the bytes of a newer one.

import (
	"errors"
	"fmt"

	"orchara/platform/internal/objectstore"
)

// promoteAttemptObject is the guard's object-store decision: verify the attempt's
// own sealed object and move it to the artifact's preallocated active key.
//
// It returns the verified object (its hash is what the row records), a stable
// business failure code ("" on success), or an error for an infrastructure
// failure the attempt may retry (wrapped in errGuardTransient). On every business
// rejection it deletes only the attempt key; the active key is never read,
// written or deleted by this function except by the successful atomic move.
func promoteAttemptObject(
	store *objectstore.Store,
	attemptKey, activeKey, expectedSHA string,
	sizeBytes *int64,
) (objectstore.Object, string, error) {
	object, err := store.Hash(attemptKey)
	switch {
	case errors.Is(err, objectstore.ErrNotExist), errors.Is(err, objectstore.ErrInvalidKey):
		// No attempt object: the handler never wrote one. That is a content
		// failure (quarantine), not a transient outage.
		return objectstore.Object{}, CodeArtifactObjectMissing, nil
	case errors.Is(err, objectstore.ErrTooLarge):
		discardAttemptObject(store, attemptKey)
		return objectstore.Object{}, CodeArtifactTooLarge, nil
	case err != nil:
		return objectstore.Object{}, "", fmt.Errorf("%w: %v", errGuardTransient, err)
	}
	if sizeBytes != nil && object.Size != *sizeBytes {
		discardAttemptObject(store, attemptKey)
		return object, CodeArtifactSizeMismatch, nil
	}
	if object.SHA256 != expectedSHA {
		discardAttemptObject(store, attemptKey)
		return object, CodeArtifactHashMismatch, nil
	}
	if err := store.Move(attemptKey, activeKey); err != nil {
		switch {
		case errors.Is(err, objectstore.ErrInvalidKey):
			// The artifact's recorded object_key is not a sealed key: a data
			// defect. Remove the attempt bytes (they can never be published) and
			// let the caller quarantine rather than retry forever.
			discardAttemptObject(store, attemptKey)
			return object, CodeArtifactObjectMissing, nil
		case errors.Is(err, objectstore.ErrNotExist):
			return object, CodeArtifactObjectMissing, nil
		default:
			return object, "", fmt.Errorf("%w: %v", errGuardTransient, err)
		}
	}
	return object, "", nil
}

// discardAttemptObject removes this attempt's own object. It is best effort: a
// failed delete leaves an unreferenced sealed object for the F07/F13A sealed
// orphan sweep, never a changed active object.
func discardAttemptObject(store *objectstore.Store, attemptKey string) {
	_ = store.Delete(attemptKey)
}
