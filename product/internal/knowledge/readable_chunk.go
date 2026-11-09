package knowledge

import "strconv"

// F10/T067/T061 review: one definition of the "current readable chunk" permission
// predicate.
//
// Retrieval (`rankRetrieval`), citation open (`GetChunk`), answer re-check
// (`Answer` through `GetChunk`) and the retrieval-cache hit re-check
// (`reader.CheckCached`) must apply exactly the same permission
// condition before a chunk body is read or a cached body is served:
//
//   - the chunk belongs to a knowledge base owned by the caller, and neither the
//     knowledge base nor the document is tombstoned;
//   - the chunk belongs to the document's *active* index version, that version is
//     `ready`, and the chunk's `document_revision` equals the version's source
//     revision (so a superseded revision or another index's chunks are unreadable);
//   - the chunk's body artifact is ready, not deleted and owned by the knowledge
//     base owner (a foreign or revoked body is never readable);
//   - the source artifact the chunk's citation names is itself still live, not
//     deleted and owned by the knowledge base owner.
//
// The last condition is the C3.03 immediate-deletion closure, not a revision
// condition: an old active index stays readable while it is the active one (a
// failed rebuild keeps serving it, ADR 0018), but the chunks it serves were
// derived from its source. Replacing the source (B) keeps the old active index
// readable until B activates; deleting the *old* source A must still stop A's
// chunks immediately even though the document row now names B and is therefore
// not tombstoned by the artifact deletion's derived-document rule. It is checked
// here, in the one predicate retrieval, citation open and the cache hit share, so
// the three paths cannot drift. Nothing here asks for the document's latest
// revision or `state='ready'`: the index activation pointer remains the only
// revision authority.
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
		  JOIN platform.artifacts AS src
		    ON src.id = CASE
		         WHEN kc.citation->>'source_artifact_id'
		              ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
		         THEN (kc.citation->>'source_artifact_id')::uuid
		       END
		 WHERE kb.owner_id = $` + strconv.Itoa(ownerParam) + `::uuid
		   AND kb.deleted_at IS NULL
		   AND kd.deleted_at IS NULL
		   AND iv.id = kc.index_version_id AND iv.state = 'ready'
		   AND iv.document_id = kd.id
		   AND kc.document_revision = iv.document_revision
		   AND a.owner_id = kb.owner_id
		   AND a.deleted_at IS NULL AND a.state = 'ready'
		   AND src.owner_id = kb.owner_id
		   AND src.deleted_at IS NULL AND src.state <> 'deleted'`
}
