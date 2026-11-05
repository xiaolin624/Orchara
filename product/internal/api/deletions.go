package api

// F11/T069: the deletion contract surface.
//
//	DELETE /api/v1/conversations/{conversation_id}     deleteConversation
//	DELETE /api/v1/artifacts/{artifact_id}             deleteArtifact
//	DELETE /api/v1/memories/{memory_id}                deleteMemory
//	DELETE /api/v1/knowledge-documents/{document_id}   deleteDocument
//	GET    /api/v1/deletions/{deletion_id}             getDeletion
//
// The DELETE answers are the contract's DeletionAccepted (202) where `id` is the
// deletion_job id and `resource_id` is the deleted resource; a repeated DELETE
// returns the same job. The tombstone, the job row and the audit event commit
// before the 202 is written, so the next read/retrieval/cache/recovery request
// already refuses the content. Physical cleanup is T071/T072; the job honestly
// stays `pending` with its remaining scopes.
//
// Access follows the neighbouring endpoints: cookie sessions need X-CSRF-Token
// (authorize); an API key needs the existing application:write for a delete and
// application:read for the job read. Every resource is owner-only — a foreign,
// unknown or malformed id is the same located 404. `conversation` has no
// authority until F08/T074 creates it, so every conversation id is that 404; the
// resource type is nevertheless wired through the same generic foundation.

import (
	"errors"
	"net/http"

	"orchara/platform/internal/deletion"
	"orchara/platform/internal/identity"
)

type deletionAcceptedView struct {
	ID         string `json:"id"`
	State      string `json:"state"`
	ResourceID string `json:"resource_id"`
}

type deletionJobView struct {
	ID              string   `json:"id"`
	State           string   `json:"state"`
	RemainingScopes []string `json:"remaining_scopes"`
}

// writeDeletionFailure maps the foundation's located outcomes onto the contract
// error envelope. It always writes, so callers return immediately afterwards.
func (s *Server) writeDeletionFailure(w http.ResponseWriter, r *http.Request, err error) {
	var inUse *deletion.InUseError
	switch {
	case errors.Is(err, deletion.ErrNotFound):
		writeError(w, r, http.StatusNotFound, "resource_not_found", "that resource does not exist")
	case errors.As(err, &inUse):
		writeErrorWithDetails(w, r, http.StatusConflict, "resource_in_use",
			"the artifact is still referenced by a run or application configuration",
			map[string]any{"references": inUse.References})
	default:
		if typed, ok := deletion.AsError(err); ok {
			writeError(w, r, typed.Status, typed.Code, typed.Message)
			return
		}
		s.internalError(w, r, err)
	}
}

// deleteResource is the one handler body shared by every DELETE route: the
// resource type is compiled into the route, never parsed from the request.
func (s *Server) deleteResource(
	w http.ResponseWriter, r *http.Request, resourceType deletion.ResourceType, id string,
) {
	caller, ok := s.authorize(w, r, "")
	if !ok {
		return
	}
	if caller.Method == identity.AuthMethodAPIKey {
		if failure := requireScope(caller, identity.ScopeApplicationWrite); failure != nil {
			writeError(w, r, failure.Status, failure.Code, failure.Message)
			return
		}
	}
	job, err := s.deletions.Delete(r.Context(), caller.PrincipalID, resourceType, id)
	if err != nil {
		s.writeDeletionFailure(w, r, err)
		return
	}
	writeJSON(w, http.StatusAccepted, deletionAcceptedView{
		ID:         job.ID,
		State:      job.State,
		ResourceID: job.ResourceID,
	})
}

func (s *Server) handleDeleteArtifact(w http.ResponseWriter, r *http.Request) {
	s.deleteResource(w, r, deletion.ResourceArtifact, r.PathValue("artifact_id"))
}

func (s *Server) handleDeleteDocument(w http.ResponseWriter, r *http.Request) {
	s.deleteResource(w, r, deletion.ResourceDocument, r.PathValue("document_id"))
}

func (s *Server) handleDeleteConversation(w http.ResponseWriter, r *http.Request) {
	// F08/T074 owns the conversation authority and its read-refusal proof.
	s.deleteResource(w, r, deletion.ResourceConversation, r.PathValue("conversation_id"))
}

func (s *Server) handleGetDeletion(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "")
	if !ok {
		return
	}
	if caller.Method == identity.AuthMethodAPIKey {
		if failure := requireScope(caller, identity.ScopeApplicationRead); failure != nil {
			writeError(w, r, failure.Status, failure.Code, failure.Message)
			return
		}
	}
	job, err := s.deletions.Get(r.Context(), caller.PrincipalID, r.PathValue("deletion_id"))
	if err != nil {
		s.writeDeletionFailure(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, deletionJobView{
		ID:              job.ID,
		State:           job.State,
		RemainingScopes: job.RemainingScopes,
	})
}
