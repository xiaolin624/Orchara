package knowledge

import "strconv"

// F10/T067/T061 review: one definition of the "current readable chunk" permission
// predicate.
//
// Retrieval (`rankRetrieval`), citation open (`GetChunk`) and the retrieval-cache
// hit re-check (`validateCachedItems`) must apply exactly the same permission
// condition before a chunk body is read or a cached body is served:
//
//   - the chunk belongs to a knowledge base owned by the caller, and neither the
//     knowledge base nor the document is tombstoned;
//   - the chunk belongs to the document's *active* index version, that version is
//     `ready`, and the chunk's `document_revision` equals the version's source
//     revision (so a superseded revision or another index's chunks are unreadable);
//   - the chunk's body artifact is ready, not deleted and owned by the knowledge
//     base owner (a foreign or revoked body is never readable).
//
// This predicate used to be three hand-copied SQL blocks in this package, and the
// T061 activation change had to edit all three. It is now one fragment: a later
// change to the permission rule changes one string, so the retrieval result, the
// opened citation and the cache hit cannot drift apart.
//
// The fragment is the `FROM ... WHERE ...` prefix. Each caller appends only its
// own candidate predicate (one knowledge base, one chunk id, or a chunk-id set)
// plus its ranking/model predicates, and keeps its own parameter numbering.
// `ownerParam` is the 1-based placeholder the caller binds the owner id to; it is
// a compile-time constant at every call site, never user input.
func currentReadableChunkSQL(ownerParam int) string {
	return `
		  FROM platform.knowledge_chunks AS kc
		  JOIN platform.knowledge_documents AS kd ON kd.id = kc.document_id
		  JOIN platform.knowledge_index_versions AS iv ON iv.id = kd.active_index_id
		  JOIN platform.knowledge_bases AS kb ON kb.id = kd.knowledge_base_id
		  JOIN platform.artifacts AS a ON a.id = kc.body_artifact_id
		 WHERE kb.owner_id = $` + strconv.Itoa(ownerParam) + `::uuid
		   AND kb.deleted_at IS NULL
		   AND kd.deleted_at IS NULL
		   AND iv.id = kc.index_version_id AND iv.state = 'ready'
		   AND iv.document_id = kd.id
		   AND kc.document_revision = iv.document_revision
		   AND a.owner_id = kb.owner_id
		   AND a.deleted_at IS NULL AND a.state = 'ready'`
}
