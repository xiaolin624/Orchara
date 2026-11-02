package background

import "context"

// Notifier is the advisory wakeup seam for background work.
//
// PostgreSQL is the authority: a job is durable the moment the producer's
// transaction commits, and every maintenance process claims it by polling. A
// notification only shortens the delay before the next claim; it is never the
// delivery mechanism. Consequently:
//
//   - Notify must be non-blocking. A slow or absent consumer may not stall an
//     enqueue or an upload completion.
//   - Notify must not return an error. Losing a wakeup is an expected event (the
//     bus is cleared, the subscriber restarts, the process is a short-lived CLI),
//     and it must never turn a committed job into a failed request.
//   - An implementation is free to drop every notification. The next poll still
//     claims the job, which is exactly what the T022 acceptance test exercises.
//
// This batch has no Redis adapter (there is no Redis instance on the development
// host): the seam exists so a later ticket can add one without touching the
// producer paths, and the only shipped in-process implementation deliberately
// drops when nothing in the same process is listening.
type Notifier interface {
	Notify(ctx context.Context, job *Job)
}

// NopNotifier is the default: it accepts every job and drops it. It is what a
// process that has no wakeup transport installs, and it makes "wakeup lost" the
// ordinary case instead of an error path.
type NopNotifier struct{}

// Notify implements Notifier.
func (NopNotifier) Notify(context.Context, *Job) {}

// InProcessNotifier is a same-process wakeup channel.
//
// It is not a transport: a notification sent by one process is never visible to
// another. The API process installs it with no consumer, so every wakeup is
// dropped and recorded through onDrop; the real maintenance process still claims
// the job through PostgreSQL polling. A future Redis adapter implements
// Notifier by publishing and the maintenance process subscribes; neither the
// producer code nor this contract changes.
//
// Deliveries is the receive side. An unbuffered channel is deliberate: Notify
// drops unless a consumer is already waiting, so a notification can never queue
// unboundedly in a process that does not consume it.
type InProcessNotifier struct {
	deliveries chan *Job
	onDrop     func()
}

// NewInProcessNotifier builds the notifier. onDrop, when non-nil, is called for
// every dropped notification; the caller decides whether to log once, count, or
// ignore it.
func NewInProcessNotifier(onDrop func()) *InProcessNotifier {
	return &InProcessNotifier{deliveries: make(chan *Job), onDrop: onDrop}
}

// Deliveries is the channel a same-process consumer may select on.
func (n *InProcessNotifier) Deliveries() <-chan *Job {
	if n == nil {
		return nil
	}
	return n.deliveries
}

// Notify implements Notifier without ever blocking.
func (n *InProcessNotifier) Notify(_ context.Context, job *Job) {
	if n == nil {
		return
	}
	select {
	case n.deliveries <- job:
	default:
		if n.onDrop != nil {
			n.onDrop()
		}
	}
}

// notify delivers one committed job through the configured notifier. A nil
// notifier (for example a zero-value Controller built by a unit test) behaves
// like NopNotifier rather than panicking.
func notify(ctx context.Context, notifier Notifier, job *Job) {
	if notifier == nil {
		return
	}
	notifier.Notify(ctx, job)
}
