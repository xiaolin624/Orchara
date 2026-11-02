package background

import (
	"context"
	"sync/atomic"
	"testing"
	"time"
)

// TestNopNotifierDropsEveryWakeup pins the default: a process without a wakeup
// transport accepts notifications and does nothing. Losing them must not be an
// error path, because every maintenance process still claims through PostgreSQL.
func TestNopNotifierDropsEveryWakeup(t *testing.T) {
	var notifier Notifier = NopNotifier{}
	notifier.Notify(context.Background(), &Job{ID: "11111111-1111-4111-8111-111111111111"})
}

// TestInProcessNotifierDropsInsteadOfBlocking is the "no consumer" case the API
// process runs in this batch: the channel is unbuffered and nobody is receiving,
// so every Notify must return immediately, report the drop, and never stall the
// producer.
func TestInProcessNotifierDropsInsteadOfBlocking(t *testing.T) {
	var drops int32
	notifier := NewInProcessNotifier(func() { atomic.AddInt32(&drops, 1) })
	job := &Job{ID: "11111111-1111-4111-8111-111111111111"}

	done := make(chan struct{})
	go func() {
		notifier.Notify(context.Background(), job)
		close(done)
	}()
	select {
	case <-done:
	case <-time.After(2 * time.Second):
		t.Fatal("Notify blocked with no consumer")
	}
	if got := atomic.LoadInt32(&drops); got != 1 {
		t.Fatalf("dropped %d notifications, want 1", got)
	}
}

// TestInProcessNotifierDeliversToASameProcessConsumer proves the channel is a
// real seam and not dead code: a receiver on Deliveries gets the job.
//
// The retry loop is intentional: an unbuffered send succeeds only when the
// receiver is already waiting, which is exactly the drop semantics the notifier
// documents. The test retries until the scheduling race resolves.
func TestInProcessNotifierDeliversToASameProcessConsumer(t *testing.T) {
	notifier := NewInProcessNotifier(nil)
	job := &Job{ID: "11111111-1111-4111-8111-111111111111"}
	received := make(chan *Job, 1)
	go func() { received <- <-notifier.Deliveries() }()

	deadline := time.Now().Add(2 * time.Second)
	for {
		notifier.Notify(context.Background(), job)
		select {
		case got := <-received:
			if got != job {
				t.Fatalf("delivered %+v, want the same job", got)
			}
			return
		default:
		}
		if time.Now().After(deadline) {
			t.Fatal("a waiting consumer never received the notification")
		}
		time.Sleep(time.Millisecond)
	}
}

// TestNotifyToleratesANilNotifier keeps a zero-value Controller safe: notify is
// a hook, never a required dependency.
func TestNotifyToleratesANilNotifier(t *testing.T) {
	notify(context.Background(), nil, &Job{ID: "11111111-1111-4111-8111-111111111111"})
	var inProcess *InProcessNotifier
	inProcess.Notify(context.Background(), &Job{})
}
