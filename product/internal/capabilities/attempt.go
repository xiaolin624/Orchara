package capabilities

// C2.01/C2.02: the private unified model-attempt lifecycle.
//
// Invoke and Stream keep their external authorization, idempotency and operation
// semantics; both share this operation session. The session owns the prepared
// operation identity and drives the bounded physical attempts: candidate
// resolution, deployment availability, actual-candidate price/reservation, the
// atomic pre-dispatch hold, cancellation checks, typed failure classification,
// backoff, the one compatible fallback and the ledger closing.
//
// Content handling is injected as modelContentHandler because the synchronous
// complete-response projection and the independent stream generations are a
// meaningful difference, not something to hide in the driver. An error returned
// by Complete is terminal: the provider answered, so the driver never retries or
// fails over after the content phase took over.
//
// There is deliberately no public execution-plan type, no second ledger and no
// configurable retry/billing policy: the bound is the accepted three primary
// physical attempts plus one compatible fallback attempt.

import (
	"context"

	"orchara/platform/internal/budget"
	"orchara/platform/internal/modelproxy"
)

// modelCandidate is one resolved, dispatchable candidate: the profile row, the
// provider class allowed to dispatch it, and the actual-candidate price facts of
// its physical attempts.
type modelCandidate struct {
	Profile     *ModelProfile
	Candidate   *modelproxy.Candidate
	Provider    modelproxy.Provider
	Reservation *budget.ReserveRequest
	Pricing     *modelPrice
}

// modelAttemptInfo is the identity of one begun physical attempt handed to the
// content handler. Attempt is the physical attempt number and AttemptID is the
// platform.operation_attempts row id (the streaming generation id), so content
// produced by this attempt can always be traced to the exact ledger row.
type modelAttemptInfo struct {
	Candidate *modelproxy.Candidate
	Provider  modelproxy.Provider
	Attempt   int
	AttemptID string
	Pricing   *modelPrice
	// Fallback is set only for the single compatible-fallback attempt: the
	// receipt and the operation must record that the primary did not answer.
	Fallback *fallbackFact
}

// modelNotice identifies why the driver is about to start another physical
// attempt. The streaming content turns it into the provisional frame the
// consumer sees; the synchronous content has no provisional notification.
type modelNotice struct {
	Kind string
	Code string
}

const (
	// NoticeRetry precedes another primary attempt after a typed transient
	// failure with retries left.
	NoticeRetry = "retry"
	// NoticeFallback precedes the operation's single compatible fallback after
	// the primary's availability failure.
	NoticeFallback = "fallback"
)

// modelContentHandler is the content half of one model operation. Call performs
// exactly one physical provider request; Complete consumes an answer that
// arrived. A Complete error is platform-side and terminal, including the
// accepted rule that a complete answer failing validation or sealing still
// settles its real usage.
//
// Notice is the delivery point of a retry/fallback notification: a returned
// error means the consumer stopped, so the driver closes the operation and
// dispatches nothing further (C2.02). Failed reports a terminal provider
// failure that was already written to the ledger, so the streaming content can
// emit its last frame; it cannot change the outcome. CancelCode is the stable
// ledger code of a cancelled/stopped attempt (caller_cancelled by default,
// stream_cancelled for the streaming entry).
type modelContentHandler struct {
	Call       func(ctx context.Context, attempt modelAttemptInfo) (modelproxy.ProviderResponse, error)
	Complete   func(ctx context.Context, attempt modelAttemptInfo, response modelproxy.ProviderResponse) (*Result, error)
	Notice     func(ctx context.Context, attempt modelAttemptInfo, notice modelNotice) error
	Failed     func(attempt modelAttemptInfo, failure modelFailure)
	CancelCode string
}

func (handler modelContentHandler) notify(
	ctx context.Context,
	attempt modelAttemptInfo,
	notice modelNotice,
) error {
	if handler.Notice == nil {
		return nil
	}
	return handler.Notice(ctx, attempt, notice)
}

func (handler modelContentHandler) failed(attempt modelAttemptInfo, failure modelFailure) {
	if handler.Failed != nil {
		handler.Failed(attempt, failure)
	}
}

func (handler modelContentHandler) cancelCode() string {
	if handler.CancelCode != "" {
		return handler.CancelCode
	}
	return CodeCallerCancelled
}

// modelFailure is the classification of one failed physical call before anything
// is written. Retryable and FallbackEligible are separate decisions: only a typed
// transient failure retries; an availability-class adapter fault may be failed
// over without spending the primary's retry budget; a permission/Schema/
// tool-argument refusal is neither and stops immediately.
type modelFailure struct {
	Code             string
	Cancelled        bool
	Retryable        bool
	FallbackEligible bool
	Reason           string
}

func classifyModelFailure(ctx context.Context, err error) modelFailure {
	code, cancelled := modelCallFailureCode(ctx, err)
	if cancelled {
		return modelFailure{Code: code, Cancelled: true}
	}
	reason, eligible := fallbackReasonFor(err)
	return modelFailure{
		Code:             code,
		Retryable:        modelproxy.IsTransient(err),
		FallbackEligible: eligible,
		Reason:           reason,
	}
}

// modelAttemptSession is the private operation session of one prepared model
// operation. It is not an execution plan: callers still use Invoke/Stream and
// learn nothing about attempts, reservations or settlement order.
type modelAttemptSession struct {
	service     *Service
	run         *runContext
	input       Input
	spec        *capabilitySpec
	operationID string
	// streaming selects the StreamProvider seam for candidate resolution, so a
	// profile whose provider cannot stream is refused before any attempt row is
	// created instead of being discovered after a physical dispatch.
	streaming bool
}

func newModelAttemptSession(
	service *Service,
	run *runContext,
	input Input,
	spec *capabilitySpec,
	operationID string,
	streaming bool,
) *modelAttemptSession {
	return &modelAttemptSession{
		service: service, run: run, input: input, spec: spec,
		operationID: operationID, streaming: streaming,
	}
}

// candidate resolves one candidate profile into dispatchable facts. Availability
// is decided before any reservation or attempt row exists; the reservation and
// pricing always use this candidate's own profile (T046).
func (session *modelAttemptSession) candidate(profile *ModelProfile) (*modelCandidate, *Error) {
	service := session.service
	if service.Providers == nil {
		return nil, capFailure(503, CodeProviderNotConfigured, "no model provider is configured")
	}
	candidate := &modelproxy.Candidate{
		ID: profile.ID, Provider: profile.Provider, ModelID: profile.ModelID,
		EndpointRef: profile.EndpointRef, SecretRef: profile.SecretRef,
		Config:          profile.ConfigJSON,
		ValidationState: profile.ValidationState,
	}
	provider, err := session.providerFor(candidate)
	if err != nil {
		return nil, providerFailure(err)
	}
	if err := provider.Available(candidate); err != nil {
		return nil, providerFailure(err)
	}
	reservation, capErr := reservationFor(profile, session.run, session.input.Params)
	if capErr != nil {
		return nil, capErr
	}
	pricing, capErr := priceForProfile(profile, session.run.Currency)
	if capErr != nil {
		return nil, capErr
	}
	return &modelCandidate{
		Profile: profile, Candidate: candidate, Provider: provider,
		Reservation: reservation, Pricing: pricing,
	}, nil
}

// providerFor chooses the delivery seam of the session. The synchronous path
// asks for the Provider (Call); the streaming path asks for the StreamProvider
// (Stream). Both answers come from the same provider set and the same candidate
// gates, so the delivery mode never weakens availability or authorization.
func (session *modelAttemptSession) providerFor(candidate *modelproxy.Candidate) (modelproxy.Provider, error) {
	if session.streaming {
		return session.service.Providers.StreamingFor(candidate)
	}
	return session.service.Providers.For(candidate)
}

// refuse closes an operation whose candidate could not begin a physical attempt.
// The refusal happened before any attempt row, reservation or provider call, so
// the operation records why it stopped instead of staying `authorized` (a
// phantom a reader could mistake for an in-flight call). The typed failure is
// returned unchanged once the close committed; a close that cannot commit is
// returned instead, because an unclosed ledger must never look like a clean
// refusal.
func (session *modelAttemptSession) refuse(ctx context.Context, capErr *Error) *Error {
	if cleanup := session.service.closeOperation(ctx, session.operationID, capErr.Code); cleanup != nil {
		return cleanup
	}
	return capErr
}

// begin opens exactly one physical attempt atomically: the operation CAS, the
// conservative reservation and the attempt row (beginModelAttempt). A budget
// refusal therefore leaves no attempt row and no provider call; it is recorded
// by that existing implementation.
func (session *modelAttemptSession) begin(
	ctx context.Context,
	candidate *modelCandidate,
	attempt int,
	fallback *fallbackFact,
) (modelAttemptInfo, *Error) {
	attemptID, capErr := session.service.beginModelAttempt(
		ctx, session.run, session.operationID, session.input.AttemptToken,
		attempt, candidate.Reservation,
	)
	if capErr != nil {
		return modelAttemptInfo{}, capErr
	}
	return modelAttemptInfo{
		Candidate: candidate.Candidate, Provider: candidate.Provider,
		Attempt: attempt, AttemptID: attemptID, Pricing: candidate.Pricing,
		Fallback: fallback,
	}, nil
}

// dispatch is the unified attempt driver of both delivery modes: up to three
// primary attempts and at most one compatible fallback. It delegates only the
// physical request and the content handling to content. The retry/fallback
// notification is a delivery: if it cannot be emitted the consumer is gone, so
// the operation is closed and nothing further is dispatched.
//
// Every ledger write this driver performs after a physical attempt is cleanup:
// it runs on the bounded context detached from the caller's request, so a caller
// that vanishes during the write can neither leave the attempt/operation
// dispatching nor cause a second dispatch. The live request context is re-checked
// before each next attempt/notification, so cancellation is honored even when the
// close itself was durable.
func (session *modelAttemptSession) dispatch(ctx context.Context, content modelContentHandler) (*Result, error) {
	primary, capErr := session.candidate(session.spec.Profile)
	if capErr != nil {
		return nil, session.refuse(ctx, capErr)
	}
	for attempt := 1; attempt <= maxPhysicalAttempts; attempt++ {
		// A caller cancellation that arrived before this attempt must not create a
		// ledger failure for a call that never happened (B2 review fix H2).
		if ctx.Err() != nil {
			return nil, session.service.finishCancelled(ctx, session.operationID, attempt, content.cancelCode())
		}
		info, capErr := session.begin(ctx, primary, attempt, nil)
		if capErr != nil {
			if ctx.Err() != nil {
				// The begin write may have rolled back with the dead request. Close
				// only a still-authorized operation (never overwrite a terminal
				// row) and report the cancellation; nothing was dispatched.
				if cleanup := session.service.closeIfAuthorized(ctx, session.operationID, content.cancelCode()); cleanup != nil {
					return nil, cleanup
				}
				return nil, cancelledFailure(content.cancelCode())
			}
			return nil, capErr
		}
		response, callErr := content.Call(ctx, info)
		if callErr == nil {
			return session.finishContent(ctx, content, info, response)
		}
		failure := classifyModelFailure(ctx, callErr)
		if failure.Cancelled {
			// The caller is gone: no retry, no fallback and no provider failure.
			return nil, session.service.finishCancelled(ctx, session.operationID, attempt, content.cancelCode())
		}
		if failure.Retryable && attempt < maxPhysicalAttempts {
			if capErr := session.service.resetAttemptForRetry(ctx, session.operationID, attempt, failure.Code); capErr != nil {
				return nil, capErr
			}
			if ctx.Err() != nil {
				// The caller vanished while the failed attempt was being closed: no
				// notice, no backoff and no next physical attempt.
				return nil, session.stopOperation(ctx, content)
			}
			// The failed generation's provisional frame. An emit failure means the
			// consumer stopped: the attempt is already closed with its provider
			// code, so only the operation needs its terminal reason. No next
			// generation is started.
			if err := content.notify(ctx, info, modelNotice{Kind: NoticeRetry, Code: failure.Code}); err != nil {
				return nil, session.stopOperation(ctx, content)
			}
			if err := sleepBackoff(ctx, attempt); err != nil {
				// The caller vanished during the bounded pause: the operation is
				// closed as a cancellation and no further attempt exists.
				return nil, session.stopOperation(ctx, content)
			}
			continue
		}
		if !failure.FallbackEligible {
			// Permission, authorisation, syntax, schema-validation, tool-argument
			// and policy failures are terminal: no retry, no fallback.
			if capErr := session.service.closeAttemptAndOperation(
				ctx, session.operationID, attempt, failure.Code,
			); capErr != nil {
				return nil, capErr
			}
			content.failed(info, failure)
			return nil, capFailure(503, failure.Code, "the model provider call did not complete")
		}
		// An availability fault that exhausted its retries, or an adapter-class
		// fault that is availability rather than a retryable transient. Release
		// this attempt's reservation before the fallback is gated against the
		// budget that is actually still free (T046).
		if capErr := session.service.resetAttemptForRetry(ctx, session.operationID, attempt, failure.Code); capErr != nil {
			return nil, capErr
		}
		if ctx.Err() != nil {
			// The close is durable, but the caller is gone: do not even evaluate
			// the fallback.
			return nil, session.stopOperation(ctx, content)
		}
		selection := session.service.selectFallback(
			ctx, session.run, session.spec, session.input.Params, failure.Reason,
			session.providerFor,
		)
		if ctx.Err() != nil {
			return nil, session.stopOperation(ctx, content)
		}
		if selection == nil {
			if cleanup := session.service.closeOperation(ctx, session.operationID, failure.Code); cleanup != nil {
				return nil, cleanup
			}
			content.failed(info, failure)
			return nil, capFailure(503, failure.Code, "the model provider exhausted its attempts")
		}
		if selection.Reason == "" {
			selection.Reason = FallbackReasonAvailability
		}
		// The failed generation's provisional switch frame. As with a retry, a
		// consumer that stopped must end the operation before the fallback is
		// dispatched.
		if err := content.notify(ctx, info, modelNotice{Kind: NoticeFallback, Code: failure.Code}); err != nil {
			return nil, session.stopOperation(ctx, content)
		}
		return session.dispatchFallback(ctx, selection, attempt+1, content)
	}
	return nil, capFailure(503, CodeProviderTransient, "the model provider exhausted its attempts")
}

// finishContent consumes an answer the provider already produced. A Complete
// error is platform-side and terminal: it never retries and never fails over.
// The content half settles the real usage and closes the attempt, but if that
// close could not commit (the caller died during sealing, a locked table) the
// attempt may still be dispatching. The driver then closes it with the bounded
// cleanup context - the provider's consumption must not disappear with the
// transport - and returns the cleanup failure instead of a content error that
// would imply a closed, accounted operation.
func (session *modelAttemptSession) finishContent(
	ctx context.Context,
	content modelContentHandler,
	info modelAttemptInfo,
	response modelproxy.ProviderResponse,
) (*Result, error) {
	result, err := content.Complete(ctx, info, response)
	if err == nil {
		return result, nil
	}
	code := CodeInternal
	if typed, ok := AsError(err); ok && typed.Code != "" {
		code = typed.Code
	}
	if cleanup := session.service.closeAfterProviderFailure(
		ctx, session.operationID, info.Attempt, code,
		usageProjection(response.Data), info.Pricing,
	); cleanup != nil {
		return nil, cleanup
	}
	return nil, err
}

// stopOperation closes an operation whose consumer disappeared after the
// current attempt was already closed by resetAttemptForRetry. The provider
// failure that was just recorded stays on the attempt row (the provider did
// fail); only the operation gets its terminal cancellation reason. A cleanup
// context detached from the dead request is used so the write is not cancelled
// itself, and no physical request is repeated. A close that cannot commit is
// returned as an unclosed-ledger failure instead of the cancellation answer.
func (session *modelAttemptSession) stopOperation(ctx context.Context, content modelContentHandler) *Error {
	if cleanup := session.service.closeOperation(ctx, session.operationID, content.cancelCode()); cleanup != nil {
		return cleanup
	}
	return cancelledFailure(content.cancelCode())
}

// dispatchFallback spends the operation's single fallback attempt. attempt is the
// next dense physical attempt number (the migration's per-operation rule); the
// documented three-retry-then-fallback path therefore still numbers it 4. A
// failed fallback attempt is recorded like any other physical attempt; the
// returned error names the fallback's own code, never a fabricated success. A
// fallback refusal before dispatch closes the operation exactly like the primary
// path.
func (session *modelAttemptSession) dispatchFallback(
	ctx context.Context,
	selection *fallbackSelection,
	attempt int,
	content modelContentHandler,
) (*Result, error) {
	candidate, capErr := session.candidate(selection.Profile)
	if capErr != nil {
		return nil, session.refuse(ctx, capErr)
	}
	if ctx.Err() != nil {
		return nil, session.service.finishCancelled(ctx, session.operationID, attempt, content.cancelCode())
	}
	info, capErr := session.begin(ctx, candidate, attempt, &fallbackFact{
		ProfileVersionID: selection.Profile.ID, Reason: selection.Reason,
	})
	if capErr != nil {
		if ctx.Err() != nil {
			if cleanup := session.service.closeIfAuthorized(ctx, session.operationID, content.cancelCode()); cleanup != nil {
				return nil, cleanup
			}
			return nil, cancelledFailure(content.cancelCode())
		}
		return nil, capErr
	}
	response, callErr := content.Call(ctx, info)
	if callErr == nil {
		return session.finishContent(ctx, content, info, response)
	}
	failure := classifyModelFailure(ctx, callErr)
	if failure.Cancelled {
		// The caller went away during the fallback attempt: close it as a
		// cancellation, never as a provider failure (B2 review fix H2).
		return nil, session.service.finishCancelled(ctx, session.operationID, attempt, content.cancelCode())
	}
	if capErr := session.service.closeAttemptAndOperation(
		ctx, session.operationID, attempt, failure.Code,
	); capErr != nil {
		return nil, capErr
	}
	content.failed(info, failure)
	return nil, capFailure(503, failure.Code, "the fallback model provider call did not complete")
}
