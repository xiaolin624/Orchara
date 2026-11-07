package capabilities

// F06/T044: the run-scoped streaming model call.
//
// Streaming is a temporary projection of one logical model operation. Every
// physical stream attempt is a real platform.operation_attempts row; its row id
// is the frame's generation_id, so a frame can always be traced back to the exact
// physical attempt that produced it. A mid-stream failure never concatenates
// text or tool deltas: the failed generation is abandoned, and a retry starts a
// brand-new generation from the beginning. Only the successful generation's
// complete content is validated (exactly like T041) and sealed as the one receipt.
//
// The durable Run SSE is untouched: deltas are not run events and are never
// persisted. The in-process owner preview (internal/api) is a temporary buffer
// with a short TTL and a single-process limitation.

import (
	"context"
	"encoding/json"
	"strings"
	"time"

	"orchara/platform/internal/modelproxy"
)

// Stream frame types.
const (
	FrameDelta     = "delta"
	FrameToolDelta = "tool_delta"
	FrameFinal     = "final"
	FrameError     = "error"
)

// CodeStreamCancelled records a stream stopped by the consumer (client gone or
// the run cancelled). It is a ledger error code, never a provider claim.
const CodeStreamCancelled = "stream_cancelled"

// ToolCallFrame is the tool-call fragment shape on the wire. The UI may show a
// fragment only as pending; it is never an executable call.
type ToolCallFrame struct {
	Index             int    `json:"index"`
	ID                string `json:"id,omitempty"`
	Name              string `json:"name,omitempty"`
	ArgumentsFragment string `json:"arguments_fragment,omitempty"`
}

// StreamError is the safe terminal/retry error body. It never carries a
// provider payload, URL or credential.
type StreamError struct {
	Code    string `json:"code"`
	Message string `json:"message"`
}

// Frame is one temporary model-stream frame. Every frame carries the logical
// operation and the physical generation it belongs to. Type is delta,
// tool_delta, final or error.
type Frame struct {
	Type         string         `json:"type"`
	OperationID  string         `json:"operation_id"`
	GenerationID string         `json:"generation_id"`
	Text         string         `json:"text,omitempty"`
	ToolCall     *ToolCallFrame `json:"tool_call,omitempty"`
	Data         map[string]any `json:"data,omitempty"`
	// ReceiptArtifactID is set on final only: the sealed T041 receipt.
	ReceiptArtifactID string         `json:"receipt_artifact_id,omitempty"`
	Usage             map[string]any `json:"usage,omitempty"`
	// Retryable marks a mid-stream error after which a new generation will be
	// started. A retryable error does not close the owner preview; the new
	// generation replaces the old draft.
	Retryable bool         `json:"retryable,omitempty"`
	Error     *StreamError `json:"error,omitempty"`
}

// isTerminalStreamFrame reports whether a frame closes an owner preview.
func isTerminalStreamFrame(frame Frame) bool {
	if frame.Type == FrameFinal {
		return true
	}
	return frame.Type == FrameError && !frame.Retryable
}

// streamToolCall is one buffered fragment set. Identity may arrive in any
// fragment; arguments are concatenated in order.
type streamToolCall struct {
	id        string
	name      string
	arguments strings.Builder
}

// streamGeneration is one physical attempt's buffer. It is created fresh per
// attempt: there is deliberately no method to merge two generations.
type streamGeneration struct {
	operationID  string
	generationID string
	text         strings.Builder
	calls        map[int]*streamToolCall
	order        []int
}

func newStreamGeneration(operationID, generationID string) *streamGeneration {
	return &streamGeneration{
		operationID:  operationID,
		generationID: generationID,
		calls:        map[int]*streamToolCall{},
	}
}

func (g *streamGeneration) addText(text string) {
	g.text.WriteString(text)
}

func (g *streamGeneration) addToolDelta(delta modelproxy.ToolCallDelta) {
	call := g.calls[delta.Index]
	if call == nil {
		call = &streamToolCall{}
		g.calls[delta.Index] = call
		g.order = append(g.order, delta.Index)
	}
	if call.id == "" {
		call.id = delta.ID
	}
	if call.name == "" {
		call.name = delta.Name
	}
	call.arguments.WriteString(delta.ArgumentsFragment)
}

func (g *streamGeneration) textValue() string { return g.text.String() }

// completeToolCalls returns only calls whose identity is present and whose
// argument fragments form a complete JSON object. Incomplete or malformed calls
// are dropped here: they can never reach the final projection, and therefore can
// never be executed. Schema/name validation still runs on the survivors exactly
// like T041.
func (g *streamGeneration) completeToolCalls() []any {
	calls := make([]any, 0, len(g.calls))
	for _, index := range g.order {
		call := g.calls[index]
		if call == nil || strings.TrimSpace(call.id) == "" || strings.TrimSpace(call.name) == "" {
			continue
		}
		raw := strings.TrimSpace(call.arguments.String())
		var arguments map[string]any
		if raw == "" || json.Unmarshal([]byte(raw), &arguments) != nil {
			continue
		}
		calls = append(calls, map[string]any{
			"id":        call.id,
			"name":      call.name,
			"arguments": arguments,
		})
	}
	return calls
}

// frameForChunk projects one provider chunk. The generation's buffer is updated
// before the frame is emitted so the final projection is built from the exact
// streamed content.
func (g *streamGeneration) frameForChunk(chunk modelproxy.Chunk) Frame {
	frame := Frame{
		OperationID:  g.operationID,
		GenerationID: g.generationID,
	}
	switch chunk.Type {
	case modelproxy.StreamToolDelta:
		frame.Type = FrameToolDelta
		if len(chunk.ToolCalls) > 0 {
			delta := chunk.ToolCalls[0]
			g.addToolDelta(delta)
			frame.ToolCall = &ToolCallFrame{
				Index:             delta.Index,
				ID:                delta.ID,
				Name:              delta.Name,
				ArgumentsFragment: delta.ArgumentsFragment,
			}
		}
	default:
		frame.Type = FrameDelta
		frame.Text = chunk.Text
		g.addText(chunk.Text)
	}
	return frame
}

// Stream performs one logical streaming model call.
//
// role is the mTLS capacity; only the worker capacity may stream. The same
// authorization/operation/params/idempotency rules as Invoke apply (the model
// branch), including the run manifest's model-profile allowlist and the
// model/tool/connector kind-ambiguity refusal: a replay emits only the sealed
// final frame and never calls a provider. A tool/connector request is refused
// with 422 capability_kind_unsupported before any ledger row is written.
//
// emit receives frames in wire order. If emit fails the provider stream is
// stopped and no success is ever committed.
func (s *Service) Stream(
	ctx context.Context,
	runID, role string,
	input Input,
	emit func(Frame) error,
) (*Result, error) {
	if strings.TrimSpace(role) != "worker" {
		return nil, capFailure(403, "forbidden", "only the worker capacity may stream a run capability")
	}
	normalized, invalid := normalizeInput(runID, input)
	if invalid != nil {
		return nil, invalid
	}
	if normalized.CapabilityKind != "model" {
		return nil, capFailure(422, CodeKindUnsupported,
			"only model capabilities can be streamed")
	}
	run, capErr := s.loadAuthorizedRun(ctx, normalized.RunID, normalized.AttemptToken)
	if capErr != nil {
		return nil, capErr
	}
	// The stream path is the same capability authorization seam as Invoke: the
	// manifest allowlist and the three-kind ambiguity rule run before the model
	// profile is even loaded. Checking existence/passed/revoked alone would let a
	// worker stream any passed profile UUID (not frozen into this run's manifest)
	// or a UUID that is also a tool/connector.
	spec, capErr := s.resolveCapability(ctx, run, normalized)
	if capErr != nil {
		return nil, capErr
	}
	if spec.Kind != "model" {
		return nil, capFailure(422, CodeKindUnsupported,
			"only model capabilities can be streamed")
	}
	canonical, digest, err := canonicalParams(normalized.Params)
	if err != nil {
		return nil, validationFailure("params", "params must be canonical JSON")
	}
	prepared, capErr := s.prepareOperation(ctx, run, normalized, spec, canonical, digest)
	if capErr != nil {
		return nil, capErr
	}
	if prepared.replay != nil {
		generationID := s.replayedGenerationID(ctx, prepared.operationID)
		if prepared.replay.State != "succeeded" {
			// Replaying a failed operation must not be presented as a successful
			// final. The recorded safe error code is the honest terminal frame.
			code := CodeInternal
			if recorded, ok := prepared.replay.Data["error_code"].(string); ok && recorded != "" {
				code = recorded
			}
			_ = emit(Frame{
				Type: FrameError, OperationID: prepared.operationID, GenerationID: generationID,
				Error: &StreamError{Code: code, Message: "the recorded model stream operation did not succeed"},
			})
			return prepared.replay, nil
		}
		if err := emit(finalFrame(prepared.replay, generationID)); err != nil {
			return nil, capFailure(503, CodeStreamCancelled, "the replay stream consumer stopped")
		}
		return prepared.replay, nil
	}
	return s.dispatchStream(ctx, run, normalized, spec, prepared.operationID, emit)
}

// replayedGenerationID names the physical attempt that produced a replayed
// result. It falls back to the logical operation id only if the attempt row is
// unexpectedly absent (the frame must still carry an identity).
func (s *Service) replayedGenerationID(ctx context.Context, operationID string) string {
	var attemptID string
	err := s.DB.QueryRowContext(ctx, `
		SELECT id::text FROM platform.operation_attempts
		 WHERE operation_id = $1::uuid AND state = 'succeeded'
		 ORDER BY attempt_no DESC LIMIT 1`, operationID).Scan(&attemptID)
	if err != nil || attemptID == "" {
		return operationID
	}
	return attemptID
}

func finalFrame(result *Result, generationID string) Frame {
	frame := Frame{
		Type:         FrameFinal,
		OperationID:  result.OperationID,
		GenerationID: generationID,
		Data:         result.Data,
	}
	if result.ReceiptArtifactID != nil {
		frame.ReceiptArtifactID = *result.ReceiptArtifactID
	}
	if usage, ok := result.Data["usage"].(map[string]any); ok {
		frame.Usage = usage
	} else if raw := result.Data["usage"]; raw != nil {
		// finishModelSuccess stores the validated projection (a typed struct), not
		// a map; the frame must carry the same JSON object, never a Go type name.
		if encoded, err := json.Marshal(raw); err == nil {
			var usage map[string]any
			if json.Unmarshal(encoded, &usage) == nil {
				frame.Usage = usage
			}
		}
	}
	return frame
}

// dispatchStream is the server-owned retry loop. Only a typed transient failure
// with attempts remaining starts a new generation; every other failure is final.
func (s *Service) dispatchStream(
	ctx context.Context,
	run *runContext,
	input Input,
	spec *capabilitySpec,
	operationID string,
	emit func(Frame) error,
) (*Result, error) {
	if s.Providers == nil {
		return nil, capFailure(503, CodeProviderNotConfigured, "no model provider is configured")
	}
	candidate := &modelproxy.Candidate{
		ID: spec.Profile.ID, Provider: spec.Profile.Provider, ModelID: spec.Profile.ModelID,
		EndpointRef: spec.Profile.EndpointRef, SecretRef: spec.Profile.SecretRef,
		Config:          spec.Profile.ConfigJSON,
		ValidationState: spec.Profile.ValidationState,
	}
	provider, err := s.Providers.StreamingFor(candidate)
	if err != nil {
		return nil, providerFailure(err)
	}
	reservation, capErr := reservationFor(spec.Profile, run, input.Params)
	if capErr != nil {
		return nil, capErr
	}
	pricing, capErr := priceForProfile(spec.Profile, run.Currency)
	if capErr != nil {
		return nil, capErr
	}
	for attempt := 1; attempt <= maxPhysicalAttempts; attempt++ {
		attemptID, capErr := s.beginModelAttempt(ctx, run, operationID, input.AttemptToken, attempt, reservation)
		if capErr != nil {
			return nil, capErr
		}
		generation := newStreamGeneration(operationID, attemptID)
		var consumerStopped error
		response, streamErr := provider.Stream(ctx, modelproxy.CallRequest{
			Candidate: candidate,
			Params:    input.Params,
			Attempt:   attempt,
		}, func(chunk modelproxy.Chunk) error {
			frame := generation.frameForChunk(chunk)
			if err := emit(frame); err != nil {
				consumerStopped = err
				return err
			}
			return nil
		})
		if streamErr != nil {
			if consumerStopped != nil || ctx.Err() != nil {
				// The consumer went away: stop here, never commit success. The
				// attempt is closed as failed and the operation is failed, not
				// left dispatching for a reader to mistake for an in-flight call.
				// The cleanup must not inherit the cancelled request context, or
				// the ledger write itself would be cancelled and the row would be
				// stuck in dispatching.
				cleanupCtx, cancel := context.WithTimeout(context.WithoutCancel(ctx), 5*time.Second)
				_ = s.failAttemptAndOperation(cleanupCtx, operationID, attempt, CodeStreamCancelled)
				cancel()
				return nil, capFailure(503, CodeStreamCancelled,
					"the model stream was stopped before it completed")
			}
			transient := modelproxy.IsTransient(streamErr)
			code := CodeProviderTransient
			if !transient {
				code = providerErrorCode(streamErr)
			}
			if transient && attempt < maxPhysicalAttempts {
				if capErr := s.resetAttemptForRetry(ctx, operationID, attempt, code); capErr != nil {
					return nil, capErr
				}
				// The failed generation gets a terminal-for-itself frame with
				// retryable=true; the next generation's frames replace it. The
				// old text is never appended to the new generation's text.
				_ = emit(Frame{
					Type: FrameError, OperationID: operationID, GenerationID: generation.generationID,
					Retryable: true,
					Error: &StreamError{Code: code,
						Message: "the model stream failed transiently; a new generation will start"},
				})
				continue
			}
			// T045: the primary's bounded retries are exhausted by an
			// availability fault. A compatible fallback gets one generation of
			// its own; the failed primary generation's text is never appended to
			// it. A permission/schema answer is terminal and never falls back.
			// F06/T046: release this generation's reservation first, so the
			// fallback is gated against the budget that is actually free.
			if reason, eligible := fallbackReasonFor(streamErr); eligible {
				if capErr := s.resetAttemptForRetry(ctx, operationID, attempt, code); capErr != nil {
					return nil, capErr
				}
				if selection := s.selectFallback(ctx, run, spec, input.Params, reason); selection != nil {
					_ = emit(Frame{
						Type: FrameError, OperationID: operationID, GenerationID: generation.generationID,
						Retryable: true,
						Error: &StreamError{Code: code,
							Message: "the primary model profile is unavailable; a compatible fallback will answer"},
					})
					return s.dispatchStreamFallback(ctx, run, input, spec, operationID,
						maxPhysicalAttempts+1, selection, emit)
				}
			}
			_ = s.failAttemptAndOperation(ctx, operationID, attempt, code)
			_ = emit(Frame{
				Type: FrameError, OperationID: operationID, GenerationID: generation.generationID,
				Error: &StreamError{Code: code,
					Message: "the model provider stream did not complete"},
			})
			return nil, capFailure(503, code, "the model provider stream did not complete")
		}

		// Success: verify the streamed draft is exactly the provider's complete
		// content, then validate the assembled complete tool calls exactly as
		// T041 does. An incomplete call was already dropped by the buffer.
		finalText, _ := response.Data["text"].(string)
		if generation.textValue() != finalText {
			// The complete provider answer exists; its usage is real consumption.
			_ = s.failModelAttemptAfterProvider(ctx, operationID, attempt,
				CodeProviderResponseInvalid, usageProjection(response.Data), pricing)
			_ = emit(Frame{
				Type: FrameError, OperationID: operationID, GenerationID: generation.generationID,
				Error: &StreamError{Code: CodeProviderResponseInvalid,
					Message: "the streamed content does not match the final provider content"},
			})
			return nil, capFailure(422, CodeProviderResponseInvalid,
				"the streamed content does not match the final provider content")
		}
		data := make(map[string]any, len(response.Data))
		for key, value := range response.Data {
			data[key] = value
		}
		data["tool_calls"] = generation.completeToolCalls()
		response.Data = data
		result, sealErr := s.finishModelSuccess(ctx, run, input, spec, operationID, attempt, response, nil, pricing)
		if sealErr != nil {
			code := CodeInternal
			message := "the streamed model answer could not be sealed"
			if typed, ok := AsError(sealErr); ok {
				code, message = typed.Code, typed.Message
			}
			_ = emit(Frame{
				Type: FrameError, OperationID: operationID, GenerationID: generation.generationID,
				Error: &StreamError{Code: code, Message: message},
			})
			return nil, sealErr
		}
		if err := emit(finalFrame(result, generation.generationID)); err != nil {
			// The receipt is already sealed; the success stands even though this
			// consumer disappeared before reading the final frame.
			return result, nil
		}
		return result, nil
	}
	return nil, capFailure(503, CodeProviderTransient, "the model provider exhausted its attempts")
}

// dispatchStreamFallback spends the operation's single fallback attempt on the
// streaming path, in a generation of its own. The primary generation's text is
// never concatenated with the fallback's: each frame names its generation and
// the final frame carries the receipt of the fallback answer only.
func (s *Service) dispatchStreamFallback(
	ctx context.Context,
	run *runContext,
	input Input,
	spec *capabilitySpec,
	operationID string,
	attempt int,
	selection *fallbackSelection,
	emit func(Frame) error,
) (*Result, error) {
	profile := selection.Profile
	candidate := &modelproxy.Candidate{
		ID: profile.ID, Provider: profile.Provider, ModelID: profile.ModelID,
		EndpointRef: profile.EndpointRef, SecretRef: profile.SecretRef,
		Config:          profile.ConfigJSON,
		ValidationState: profile.ValidationState,
	}
	provider, err := s.Providers.StreamingFor(candidate)
	if err != nil {
		_ = s.failOperation(ctx, operationID, providerErrorCode(err))
		_ = emit(Frame{
			Type: FrameError, OperationID: operationID,
			Error: &StreamError{Code: providerErrorCode(err),
				Message: "no provider can dispatch the compatible fallback"},
		})
		return nil, providerFailure(err)
	}
	reservation, capErr := reservationFor(profile, run, input.Params)
	if capErr != nil {
		_ = s.failOperation(ctx, operationID, capErr.Code)
		return nil, capErr
	}
	pricing, capErr := priceForProfile(profile, run.Currency)
	if capErr != nil {
		_ = s.failOperation(ctx, operationID, capErr.Code)
		return nil, capErr
	}
	attemptID, capErr := s.beginModelAttempt(ctx, run, operationID, input.AttemptToken, attempt, reservation)
	if capErr != nil {
		return nil, capErr
	}
	generation := newStreamGeneration(operationID, attemptID)
	var consumerStopped error
	response, streamErr := provider.Stream(ctx, modelproxy.CallRequest{
		Candidate: candidate,
		Params:    input.Params,
		Attempt:   attempt,
	}, func(chunk modelproxy.Chunk) error {
		frame := generation.frameForChunk(chunk)
		if err := emit(frame); err != nil {
			consumerStopped = err
			return err
		}
		return nil
	})
	if streamErr != nil {
		cleanupCtx, cancel := context.WithTimeout(context.WithoutCancel(ctx), 5*time.Second)
		defer cancel()
		if consumerStopped != nil || ctx.Err() != nil {
			_ = s.failAttemptAndOperation(cleanupCtx, operationID, attempt, CodeStreamCancelled)
			_ = emit(Frame{
				Type: FrameError, OperationID: operationID, GenerationID: generation.generationID,
				Error: &StreamError{Code: CodeStreamCancelled,
					Message: "the fallback model stream was stopped before it completed"},
			})
			return nil, capFailure(503, CodeStreamCancelled,
				"the fallback model stream was stopped before it completed")
		}
		code := providerErrorCode(streamErr)
		if modelproxy.IsTransient(streamErr) {
			code = CodeProviderTransient
		}
		_ = s.failAttemptAndOperation(cleanupCtx, operationID, attempt, code)
		_ = emit(Frame{
			Type: FrameError, OperationID: operationID, GenerationID: generation.generationID,
			Error: &StreamError{Code: code, Message: "the fallback model provider stream did not complete"},
		})
		return nil, capFailure(503, code, "the fallback model provider stream did not complete")
	}
	finalText, _ := response.Data["text"].(string)
	if generation.textValue() != finalText {
		// The complete fallback answer exists; its usage is real consumption.
		_ = s.failModelAttemptAfterProvider(ctx, operationID, attempt,
			CodeProviderResponseInvalid, usageProjection(response.Data), pricing)
		_ = emit(Frame{
			Type: FrameError, OperationID: operationID, GenerationID: generation.generationID,
			Error: &StreamError{Code: CodeProviderResponseInvalid,
				Message: "the streamed fallback content does not match the final provider content"},
		})
		return nil, capFailure(422, CodeProviderResponseInvalid,
			"the streamed fallback content does not match the final provider content")
	}
	data := make(map[string]any, len(response.Data))
	for key, value := range response.Data {
		data[key] = value
	}
	data["tool_calls"] = generation.completeToolCalls()
	response.Data = data
	fact := &fallbackFact{ProfileVersionID: profile.ID, Reason: selection.Reason}
	result, sealErr := s.finishModelSuccess(ctx, run, input, spec, operationID, attempt, response, fact, pricing)
	if sealErr != nil {
		code := CodeInternal
		message := "the streamed fallback answer could not be sealed"
		if typed, ok := AsError(sealErr); ok {
			code, message = typed.Code, typed.Message
		}
		_ = emit(Frame{
			Type: FrameError, OperationID: operationID, GenerationID: generation.generationID,
			Error: &StreamError{Code: code, Message: message},
		})
		return nil, sealErr
	}
	if err := emit(finalFrame(result, generation.generationID)); err != nil {
		return result, nil
	}
	return result, nil
}
