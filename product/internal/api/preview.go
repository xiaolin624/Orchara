package api

// F06/T044: the in-process owner preview hub.
//
// The temporary model stream (POST /internal/v1/runs/{id}/model-stream) and the
// owner preview (GET /api/v1/runs/{id}/model-preview) share process memory only.
// Nothing here is persisted, and nothing here is a run event: the durable Run SSE
// (GET /runs/{id}/events) remains the authority for committed facts. The hub is
// deliberately a single-process structure; a multi-process deployment would need
// a real fan-out (documented limitation, not faked).

import (
	"sync"
	"time"

	"orchara/platform/internal/capabilities"
)

const (
	// previewFrameLimit bounds one run's ring buffer. A stream emits a handful of
	// frames in this build; the limit keeps an abusive producer bounded.
	previewFrameLimit = 256
	// previewSubscriberBuffer is a single preview client's queue. A client slower
	// than 64 frames is dropped and may reconnect (it then replays the buffer).
	previewSubscriberBuffer = 64
	// previewTTL is how long a completed preview stays replayable. The preview is
	// not history: the persistent run snapshot/events are.
	previewTTL = 60 * time.Second
)

type previewHub struct {
	mu    sync.Mutex
	now   func() time.Time
	runs  map[string]*previewRun
	limit int
	ttl   time.Duration
}

type previewRun struct {
	generationID string
	frames       []capabilities.Frame
	terminal     bool
	updated      time.Time
	subscribers  map[chan capabilities.Frame]struct{}
}

func newPreviewHub() *previewHub {
	return &previewHub{
		now:   time.Now,
		runs:  map[string]*previewRun{},
		limit: previewFrameLimit,
		ttl:   previewTTL,
	}
}

// Publish adds one frame to the run's current generation. A frame from a new
// generation clears the whole buffer first: old deltas must never be shown next
// to (or concatenated with) the new generation's content.
func (h *previewHub) Publish(runID string, frame capabilities.Frame) {
	if runID == "" || frame.GenerationID == "" {
		return
	}
	h.mu.Lock()
	defer h.mu.Unlock()
	h.pruneLocked()
	state := h.stateLocked(runID)
	if state.generationID != frame.GenerationID {
		state.generationID = frame.GenerationID
		state.frames = nil
		state.terminal = false
	}
	state.frames = append(state.frames, frame)
	if len(state.frames) > h.limit {
		state.frames = state.frames[len(state.frames)-h.limit:]
	}
	state.updated = h.now()
	if terminalStreamFrame(frame) {
		state.terminal = true
	}
	for subscriber := range state.subscribers {
		select {
		case subscriber <- frame:
		default:
			// Slow consumer: close it rather than block the provider stream. The
			// browser reconnects and replays the current buffer; dropping is
			// honest, inventing order would not be.
			delete(state.subscribers, subscriber)
			close(subscriber)
		}
	}
}

// Subscribe returns the current generation's snapshot and a live channel. A nil
// channel means the preview is already terminal and the caller must close after
// replaying the snapshot. hasGeneration is false when this run has not streamed
// yet (the caller may wait for the first frame).
func (h *previewHub) Subscribe(runID string) (
	snapshot []capabilities.Frame,
	live <-chan capabilities.Frame,
	cancel func(),
	hasGeneration bool,
) {
	h.mu.Lock()
	defer h.mu.Unlock()
	h.pruneLocked()
	state := h.stateLocked(runID)
	snapshot = append([]capabilities.Frame(nil), state.frames...)
	hasGeneration = state.generationID != ""
	if state.terminal {
		return snapshot, nil, func() {}, hasGeneration
	}
	channel := make(chan capabilities.Frame, previewSubscriberBuffer)
	state.subscribers[channel] = struct{}{}
	cancel = func() {
		h.mu.Lock()
		defer h.mu.Unlock()
		if _, ok := state.subscribers[channel]; ok {
			delete(state.subscribers, channel)
			close(channel)
		}
	}
	return snapshot, channel, cancel, hasGeneration
}

func (h *previewHub) stateLocked(runID string) *previewRun {
	state := h.runs[runID]
	if state == nil {
		state = &previewRun{subscribers: map[chan capabilities.Frame]struct{}{}}
		h.runs[runID] = state
	}
	return state
}

// pruneLocked drops idle, unobserved buffers after the TTL. A state with a live
// subscriber is never pruned: the subscriber would silently stop receiving.
func (h *previewHub) pruneLocked() {
	now := h.now()
	for runID, state := range h.runs {
		if len(state.subscribers) > 0 {
			continue
		}
		if now.Sub(state.updated) > h.ttl {
			delete(h.runs, runID)
		}
	}
}

// terminalStreamFrame mirrors capabilities' classification locally: a retryable
// error is followed by a new generation, so it does not close the preview.
func terminalStreamFrame(frame capabilities.Frame) bool {
	if frame.Type == capabilities.FrameFinal {
		return true
	}
	return frame.Type == capabilities.FrameError && !frame.Retryable
}
