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

// C2.02: the streaming entry now uses the same private model-attempt session as
// Invoke. The streaming content half below supplies the real StreamProvider and
// the per-generation frames; candidate resolution, the bounded attempts, the
// retry/fallback classification, the ledger closing and the dense attempt
// numbering are the shared driver's.

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"

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
	session := newModelAttemptSession(s, run, normalized, spec, prepared.operationID, true)
	content := &streamModelContent{
		service: s, run: run, input: normalized, spec: spec,
		operationID: prepared.operationID, emit: emit,
	}
	return session.dispatch(ctx, modelContentHandler{
		Call:       content.call,
		Complete:   content.complete,
		Notice:     content.notice,
		Failed:     content.failed,
		CancelCode: CodeStreamCancelled,
	})
}

// errStreamConsumerStopped marks a physical stream attempt aborted because the
// frame consumer stopped (client disconnect, closed response writer). It is a
// cancellation, never a provider claim: the driver must not retry, must not
// fail over and must not record a provider error for it.
var errStreamConsumerStopped = errors.New("the model stream consumer stopped")

// streamModelContent is the streaming content half of the shared attempt
// session. Call performs exactly one physical stream attempt and projects its
// chunks into frames of that attempt's generation; Complete validates the
// completed generation and seals it through the same finishModelSuccess as the
// synchronous path. The driver owns candidates, attempts, retries, fallback and
// the ledger; this type never decides to retry or fail over.
type streamModelContent struct {
	service     *Service
	run         *runContext
	input       Input
	spec        *capabilitySpec
	operationID string
	emit        func(Frame) error
	// generation is the buffer of the attempt currently being called. The driver
	// calls Call and Complete for one attempt at a time, and every attempt gets a
	// new buffer, so content never crosses generations.
	generation *streamGeneration
}

// call performs one physical stream attempt. Every chunk is buffered into this
// attempt's generation and emitted as a frame tagged with the attempt row id
// (the generation id). An emit error stops the provider and is reported as a
// consumer stop, so the driver closes the attempt as a cancellation.
func (content *streamModelContent) call(
	ctx context.Context,
	attempt modelAttemptInfo,
) (modelproxy.ProviderResponse, error) {
	provider, ok := attempt.Provider.(modelproxy.StreamProvider)
	if !ok {
		// candidate() resolves the streaming seam before any attempt row exists;
		// this guard only keeps a programming error from being read as a
		// provider failure.
		return modelproxy.ProviderResponse{}, modelproxy.ErrAdapterUnavailable
	}
	generation := newStreamGeneration(content.operationID, attempt.AttemptID)
	content.generation = generation
	response, streamErr := provider.Stream(ctx, modelproxy.CallRequest{
		Candidate: attempt.Candidate,
		Params:    content.input.Params,
		Attempt:   attempt.Attempt,
	}, func(chunk modelproxy.Chunk) error {
		if err := content.emit(generation.frameForChunk(chunk)); err != nil {
			return fmt.Errorf("%w: %w", errStreamConsumerStopped, err)
		}
		return nil
	})
	return response, streamErr
}

// complete consumes an answer that arrived. The streamed draft must be exactly
// the provider's complete content, and only complete tool calls from this
// generation may enter the final projection. The sealing/settlement error of a
// complete answer is terminal and its real usage is settled by the same
// finishModelSuccess rules as the synchronous path.
func (content *streamModelContent) complete(
	ctx context.Context,
	attempt modelAttemptInfo,
	response modelproxy.ProviderResponse,
) (*Result, error) {
	generation := content.generation
	if generation == nil || generation.generationID != attempt.AttemptID {
		return nil, capFailure(500, CodeInternal, "the streamed generation is missing")
	}
	finalText, _ := response.Data["text"].(string)
	if generation.textValue() != finalText {
		// The complete provider answer exists; its usage is real consumption. The
		// close runs on the bounded cleanup context so a consumer that vanished
		// while the answer was assembled cannot leave the attempt dispatching; a
		// close that cannot commit is reported instead of the assembly refusal.
		message := "the streamed content does not match the final provider content"
		if attempt.Fallback != nil {
			message = "the streamed fallback content does not match the final provider content"
		}
		if cleanup := content.service.closeAfterProviderFailure(ctx, content.operationID,
			attempt.Attempt, CodeProviderResponseInvalid, usageProjection(response.Data),
			attempt.Pricing,
		); cleanup != nil {
			_ = content.emit(Frame{
				Type: FrameError, OperationID: content.operationID, GenerationID: attempt.AttemptID,
				Error: &StreamError{Code: cleanup.Code, Message: message},
			})
			return nil, cleanup
		}
		_ = content.emit(Frame{
			Type: FrameError, OperationID: content.operationID, GenerationID: attempt.AttemptID,
			Error: &StreamError{Code: CodeProviderResponseInvalid, Message: message},
		})
		return nil, capFailure(422, CodeProviderResponseInvalid, message)
	}
	data := make(map[string]any, len(response.Data))
	for key, value := range response.Data {
		data[key] = value
	}
	// An incomplete tool call was already dropped by the buffer: it can never
	// reach final validation, the receipt or execution.
	data["tool_calls"] = generation.completeToolCalls()
	response.Data = data
	result, sealErr := content.service.finishModelSuccess(ctx, content.run, content.input,
		content.spec, content.operationID, attempt.Attempt, response,
		attempt.Fallback, attempt.Pricing)
	if sealErr != nil {
		code := CodeInternal
		message := "the streamed model answer could not be sealed"
		if typed, ok := AsError(sealErr); ok {
			code, message = typed.Code, typed.Message
		}
		_ = content.emit(Frame{
			Type: FrameError, OperationID: content.operationID, GenerationID: attempt.AttemptID,
			Error: &StreamError{Code: code, Message: message},
		})
		return nil, sealErr
	}
	if err := content.emit(finalFrame(result, attempt.AttemptID)); err != nil {
		// The receipt is already sealed; the success stands even though this
		// consumer disappeared before reading the final frame.
		return result, nil
	}
	return result, nil
}

// notice emits the provisional frame of a retry or of the compatible fallback.
// Its delivery failure is returned unchanged: the driver closes the operation
// and dispatches nothing further.
func (content *streamModelContent) notice(
	_ context.Context,
	attempt modelAttemptInfo,
	notice modelNotice,
) error {
	message := "the model stream failed transiently; a new generation will start"
	if notice.Kind == NoticeFallback {
		message = "the primary model profile is unavailable; a compatible fallback will answer"
	}
	return content.emit(Frame{
		Type: FrameError, OperationID: content.operationID, GenerationID: attempt.AttemptID,
		Retryable: true,
		Error:     &StreamError{Code: notice.Code, Message: message},
	})
}

// failed emits the terminal frame of a provider failure that the driver already
// recorded. A failure to deliver it cannot change the ledger outcome.
func (content *streamModelContent) failed(attempt modelAttemptInfo, failure modelFailure) {
	message := "the model provider stream did not complete"
	if attempt.Fallback != nil {
		message = "the fallback model provider stream did not complete"
	}
	_ = content.emit(Frame{
		Type: FrameError, OperationID: content.operationID, GenerationID: attempt.AttemptID,
		Error: &StreamError{Code: failure.Code, Message: message},
	})
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
