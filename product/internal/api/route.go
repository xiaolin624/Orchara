package api

// F08/T079: the job-scoped routing protocol.
//
//	POST /internal/v1/background/jobs/{job_id}/route
//
// The compiled `route_request` handler calls this endpoint with its live attempt
// token. The server proves the lease (withJobData), verifies the job kind, and then
// executes exactly one routing round as the server: authorized candidate query,
// governed control-model call, validation of the model's suggestion and the one
// interaction-or-Run commit. User text never reaches the handler; the handler
// cannot supply a candidate or a decision.
//
// This is a non-contract internal extension, like the tool-build/document-ingest
// bindings. It is reachable only on the mTLS listener with a maintenance
// certificate, and only for a live lease.

import (
	"errors"
	"net/http"

	"orchara/platform/internal/background"
	"orchara/platform/internal/routing"
)

type internalRouteRequest struct {
	AttemptToken string `json:"attempt_token"`
}

func (r internalRouteRequest) attemptToken() string { return r.AttemptToken }

type routeView struct {
	RequestID     string `json:"request_id"`
	State         string `json:"state"`
	Result        string `json:"result"`
	ErrorCode     string `json:"error_code,omitempty"`
	InteractionID string `json:"interaction_id,omitempty"`
	CorrelationID string `json:"correlation_id,omitempty"`
	RunID         string `json:"run_id,omitempty"`
	ControlCalls  int    `json:"control_calls"`
}

func (s *Server) handleInternalRoute(w http.ResponseWriter, r *http.Request) {
	var request internalRouteRequest
	job, ok := s.withJobData(w, r, &request)
	if !ok {
		return
	}
	if job.Kind != background.KindRouteRequest {
		writeError(w, r, http.StatusUnprocessableEntity, "route_not_allowed",
			"this background job kind may not run the router")
		return
	}
	if s.routing == nil {
		writeError(w, r, http.StatusServiceUnavailable, "routing_unavailable",
			"the routing service is not configured on this server")
		return
	}
	result, err := s.routing.Advance(r.Context(), job.ID, job.TargetID, job.OwnerID)
	switch {
	case errors.Is(err, routing.ErrRequestNotFound):
		// Same answer as a stale job: the endpoint never reveals whether another
		// principal's request exists.
		writeError(w, r, http.StatusNotFound, "route_request_not_found",
			"that request does not exist for this job")
		return
	case routing.IsTransient(err):
		// The handler maps this to transient_internal and the job retry table
		// bounds it; the request stays in routing and keeps its slot.
		writeError(w, r, http.StatusServiceUnavailable, "route_model_transient",
			"the routing control model is temporarily unavailable")
		return
	case err != nil:
		s.logger.Printf("route request_id=%s job_id=%s: %v", job.TargetID, job.ID, err)
		s.internalError(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, routeView{
		RequestID:     result.RequestID,
		State:         result.State,
		Result:        result.Result,
		ErrorCode:     result.ErrorCode,
		InteractionID: result.InteractionID,
		CorrelationID: result.CorrelationID,
		RunID:         result.RunID,
		ControlCalls:  result.ControlCalls,
	})
}
