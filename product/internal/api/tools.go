package api

// F05/T037: tool submission, reads, review and revocation.
//
//	POST /api/v1/tools                          -> 202 Accepted (submitTool)
//	GET  /api/v1/tools                          -> 200 {items,next_cursor} (listTools)
//	GET  /api/v1/tools/{tool_version_id}        -> 200 ToolVersion (getTool)
//	POST /api/v1/tools/{tool_version_id}/review -> 200 Accepted (reviewTool)
//	POST /api/v1/tools/{tool_version_id}/revoke -> 202 Accepted (non-contract)
//	GET  /api/v1/tools/{tool_version_id}/build  -> 200 build facts (non-contract)
//
// Authorization:
//   - submit mirrors requireApplicationWrite exactly: a session must hold the
//     creator role, an API key must carry application:write;
//   - reads use the same interim API-key scope mapping as T028
//     (application:read); a session is not scope-limited;
//   - review/revoke are administrator-only. An API key is never an
//     administrator (a service account has no roles), so a bearer key is
//     refused 403 even if it carries application:write;
//   - visibility is owner-or-admin-or-approved (invisible == 404), so an
//     unapproved tool is not even discoverable by another principal.
//
// The contract ToolVersion projection is exactly seven fields. Build/review
// facts the contract has no room for travel through the documented non-contract
// build-facts endpoint, never as extra members of ToolVersion.

import (
	"encoding/json"
	"errors"
	"net/http"
	"strconv"
	"strings"
	"time"

	"orchara/platform/internal/catalog"
	"orchara/platform/internal/identity"
	"orchara/platform/internal/toolversions"
	"orchara/platform/internal/uuid"
)

type toolVersionView struct {
	ID             string          `json:"id"`
	State          string          `json:"state"`
	Approved       bool            `json:"approved"`
	ImageDigest    *string         `json:"image_digest"`
	ManifestSHA256 string          `json:"manifest_sha256"`
	InputSchema    json.RawMessage `json:"input_schema"`
	OutputSchema   json.RawMessage `json:"output_schema"`
}

type toolListResponse struct {
	Items      []toolVersionView `json:"items"`
	NextCursor *string           `json:"next_cursor"`
}

func toolViewOf(version *toolversions.Version) toolVersionView {
	return toolVersionView{
		ID:             version.ID,
		State:          version.State,
		Approved:       version.Approved,
		ImageDigest:    version.ImageDigest,
		ManifestSHA256: version.ManifestSHA256,
		InputSchema:    version.InputSchema,
		OutputSchema:   version.OutputSchema,
	}
}

// requireToolRead applies the interim API-key scope mapping for tool reads.
func (s *Server) requireToolRead(w http.ResponseWriter, r *http.Request, caller *identity.Identity) bool {
	if failure := requireScope(caller, identity.ScopeApplicationRead); failure != nil {
		writeError(w, r, failure.Status, failure.Code, failure.Message)
		return false
	}
	return true
}

// writeToolFailure maps the service's typed errors onto the contract envelope.
func (s *Server) writeToolFailure(w http.ResponseWriter, r *http.Request, err error) {
	var (
		validation *toolversions.ValidationError
		conflict   *toolversions.ConflictError
	)
	switch {
	case err == nil:
		return
	case errors.Is(err, toolversions.ErrNotVisible):
		writeError(w, r, http.StatusNotFound, "resource_not_found", "that tool version does not exist")
	case errors.As(err, &validation):
		writeErrorWithDetails(w, r, http.StatusUnprocessableEntity, "validation_failed",
			validation.Error(), map[string]any{"field": validation.Field})
	case errors.As(err, &conflict):
		status := http.StatusConflict
		writeError(w, r, status, conflict.Code, conflict.Message)
	case errors.Is(err, catalog.ErrInvalidCursor):
		writeError(w, r, http.StatusBadRequest, "invalid_cursor", "the cursor is not recognised")
	default:
		s.internalError(w, r, err)
	}
}

func (s *Server) handleSubmitTool(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "")
	if !ok {
		return
	}
	if failure := requireApplicationWrite(caller); failure != nil {
		writeError(w, r, failure.Status, failure.Code, failure.Message)
		return
	}
	var request toolversions.SubmitInput
	if err := decodeJSON(w, r, &request); err != nil {
		writeError(w, r, http.StatusBadRequest, "invalid_json", "request body is not valid JSON")
		return
	}
	version, err := s.toolVersions.Submit(r.Context(), caller.PrincipalID, request)
	if err != nil {
		s.writeToolFailure(w, r, err)
		return
	}
	writeJSON(w, http.StatusAccepted, acceptedResponse{ID: version.ID, State: version.State})
}

func (s *Server) handleListTools(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "")
	if !ok {
		return
	}
	if !s.requireToolRead(w, r, caller) {
		return
	}
	limit := 20
	if raw := strings.TrimSpace(r.URL.Query().Get("limit")); raw != "" {
		parsed, err := strconv.Atoi(raw)
		if err != nil || parsed < 1 || parsed > 100 {
			writeErrorWithDetails(w, r, http.StatusUnprocessableEntity, "validation_failed",
				"limit must be between 1 and 100", map[string]any{"field": "limit"})
			return
		}
		limit = parsed
	}
	if applicationID := strings.TrimSpace(r.URL.Query().Get("application_id")); applicationID != "" {
		// The contract has the parameter, but no publication flow (F02B) has yet
		// linked a version to tools, so filtering by application would require
		// inventing a join. Accept a well-formed id and document that the filter
		// is not applied until F02B owns that relation.
		if !uuid.Valid(applicationID) {
			writeErrorWithDetails(w, r, http.StatusUnprocessableEntity, "validation_failed",
				"application_id must be a UUID", map[string]any{"field": "application_id"})
			return
		}
	}
	versions, next, err := s.toolVersions.List(
		r.Context(), caller.PrincipalID, caller.HasRole(identity.RoleAdmin),
		r.URL.Query().Get("cursor"), limit,
	)
	if err != nil {
		s.writeToolFailure(w, r, err)
		return
	}
	items := make([]toolVersionView, 0, len(versions))
	for index := range versions {
		items = append(items, toolViewOf(&versions[index]))
	}
	writeJSON(w, http.StatusOK, toolListResponse{Items: items, NextCursor: next})
}

func (s *Server) handleGetTool(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "")
	if !ok {
		return
	}
	if !s.requireToolRead(w, r, caller) {
		return
	}
	version, err := s.toolVersions.Get(
		r.Context(), caller.PrincipalID, r.PathValue("tool_version_id"),
		caller.HasRole(identity.RoleAdmin),
	)
	if err != nil {
		s.writeToolFailure(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, toolViewOf(version))
}

func (s *Server) handleReviewTool(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, identity.RoleAdmin)
	if !ok {
		return
	}
	// The contract requires Idempotency-Key on reviewTool. The decision row is
	// the durable idempotency fact (same decision+reason replays with no second
	// audit row); the header is still required so a caller cannot omit the
	// operation identity the contract declares.
	values := r.Header.Values("Idempotency-Key")
	key := ""
	if len(values) == 1 {
		key = strings.TrimSpace(values[0])
	}
	if len(values) != 1 || key == "" {
		writeErrorWithDetails(w, r, http.StatusBadRequest, "idempotency_key_required",
			"the Idempotency-Key header is required and must be one non-empty value",
			map[string]any{"header": "Idempotency-Key"})
		return
	}
	var request toolversions.ReviewRequest
	if err := decodeJSON(w, r, &request); err != nil {
		writeError(w, r, http.StatusBadRequest, "invalid_json", "request body is not valid JSON")
		return
	}
	version, err := s.toolVersions.Review(
		r.Context(), caller.PrincipalID, r.PathValue("tool_version_id"), request,
	)
	if err != nil {
		s.writeToolFailure(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, acceptedResponse{ID: version.ID, State: version.State})
}

type revokeToolRequest struct {
	Reason string `json:"reason"`
}

func (s *Server) handleRevokeTool(w http.ResponseWriter, r *http.Request) {
	// Documented non-contract revocation, admin-only like review. An API key is
	// refused by the role requirement (service accounts have no roles).
	caller, ok := s.authorize(w, r, identity.RoleAdmin)
	if !ok {
		return
	}
	var request revokeToolRequest
	if err := decodeJSON(w, r, &request); err != nil {
		writeError(w, r, http.StatusBadRequest, "invalid_json", "request body is not valid JSON")
		return
	}
	version, err := s.toolVersions.Revoke(
		r.Context(), caller.PrincipalID, r.PathValue("tool_version_id"), request.Reason,
	)
	if err != nil {
		s.writeToolFailure(w, r, err)
		return
	}
	writeJSON(w, http.StatusAccepted, acceptedResponse{ID: version.ID, State: version.State})
}

// buildFactsResponse is the non-contract build/review diagnostics projection.
// It has no object key, no artifact bytes and no sandbox log text.
type buildFactsResponse struct {
	ToolVersionID      string  `json:"tool_version_id"`
	State              string  `json:"state"`
	Revision           int64   `json:"revision"`
	SourceSHA256       *string `json:"source_sha256"`
	DependencyLockSHA  *string `json:"dependency_lock_sha256"`
	ManifestSHA256     string  `json:"manifest_sha256"`
	BuildLogArtifactID *string `json:"build_log_artifact_id"`
	SBOMArtifactID     *string `json:"sbom_artifact_id"`
	BuildBundleDigest  *string `json:"build_bundle_digest"`
	BuildErrorCode     *string `json:"build_error_code"`
	BuildStartedAt     *string `json:"build_started_at"`
	BuildFinishedAt    *string `json:"build_finished_at"`
	ReviewDecision     *string `json:"review_decision"`
	ReviewReason       *string `json:"review_reason"`
	ReviewedAt         *string `json:"reviewed_at"`
	RevokedAt          *string `json:"revoked_at"`
	RevokeReason       *string `json:"revoke_reason"`
}

func timeText(value *time.Time) *string {
	if value == nil {
		return nil
	}
	text := value.UTC().Format(time.RFC3339)
	return &text
}

func (s *Server) handleGetToolBuild(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "")
	if !ok {
		return
	}
	if !s.requireToolRead(w, r, caller) {
		return
	}
	facts, err := s.toolVersions.BuildFacts(
		r.Context(), caller.PrincipalID, r.PathValue("tool_version_id"),
		caller.HasRole(identity.RoleAdmin),
	)
	if err != nil {
		s.writeToolFailure(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, buildFactsResponse{
		ToolVersionID:      facts.ToolVersionID,
		State:              facts.State,
		Revision:           facts.Revision,
		SourceSHA256:       facts.SourceSHA256,
		DependencyLockSHA:  facts.DependencyLockSHA,
		ManifestSHA256:     facts.ManifestSHA256,
		BuildLogArtifactID: facts.BuildLogArtifactID,
		SBOMArtifactID:     facts.SBOMArtifactID,
		BuildBundleDigest:  facts.BuildBundleDigest,
		BuildErrorCode:     facts.BuildErrorCode,
		BuildStartedAt:     timeText(facts.BuildStartedAt),
		BuildFinishedAt:    timeText(facts.BuildFinishedAt),
		ReviewDecision:     facts.ReviewDecision,
		ReviewReason:       facts.ReviewReason,
		ReviewedAt:         timeText(facts.ReviewedAt),
		RevokedAt:          timeText(facts.RevokedAt),
		RevokeReason:       facts.RevokeReason,
	})
}

// ---------------------------------------------------------------------------
// internal job-scoped build binding
// ---------------------------------------------------------------------------

type internalToolBuildRequest struct {
	AttemptToken string `json:"attempt_token"`
}

func (r internalToolBuildRequest) attemptToken() string { return r.AttemptToken }

// toolBuildBindingView is the non-contract internal body. dependency_lock is the
// canonical JSON the sandbox receives, not the JSONB projection re-serialised by
// PostgreSQL.
type toolBuildBindingView struct {
	ToolVersionID    string          `json:"tool_version_id"`
	SourceArtifactID string          `json:"source_artifact_id"`
	SourceSHA256     string          `json:"source_sha256"`
	DependencyLock   json.RawMessage `json:"dependency_lock"`
	ManifestSHA256   string          `json:"manifest_sha256"`
}

func (s *Server) handleInternalToolBuild(w http.ResponseWriter, r *http.Request) {
	var request internalToolBuildRequest
	job, ok := s.withJobData(w, r, &request)
	if !ok {
		return
	}
	binding, err := s.toolVersions.ResolveBuildBinding(r.Context(), job)
	if err != nil {
		var conflict *toolversions.ConflictError
		if errors.As(err, &conflict) {
			writeError(w, r, http.StatusConflict, conflict.Code, conflict.Message)
			return
		}
		s.internalError(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, toolBuildBindingView{
		ToolVersionID:    binding.ToolVersionID,
		SourceArtifactID: binding.SourceArtifactID,
		SourceSHA256:     binding.SourceSHA256,
		DependencyLock:   binding.DependencyLock,
		ManifestSHA256:   binding.ManifestSHA256,
	})
}
