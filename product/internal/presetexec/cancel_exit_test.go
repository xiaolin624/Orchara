package presetexec

// F13B/T114 acceptance defect CANCEL-RACE-SLOT-LEAK (GitHub #115): a result
// proposal refused while the Run is not executing (cancelling/terminal/deadline)
// used to end the executor without confirming the attempt exit. The refused call
// wrote nothing, so the Run stayed cancelling with a live attempt and an
// allocated execution slot until the lease sweep parked it in recovery_pending.
//
// These are the seam tests for the fixed contract: every failure path that
// proposes a result must, when the platform refuses the proposal, still certify
// the attempt exit. The live token then lets the one finalizer commit the Run's
// real terminal state (cancel > proposal) and release the slot; a token the
// platform already considers dead answers stale_attempt and the executor returns
// with a readable combined diagnostic instead of looping.
//
// The real PostgreSQL + mTLS + cmd/supervisor evidence lives in
// tests/f13b/test_t114_cancel_exit.py.

import (
	"context"
	"strings"
	"testing"

	"orchara/platform/internal/sandbox"
)

// codedClientError mirrors the supervisor's internal API error shape: a stable
// contract code (run_state_conflict, run_terminal, stale_attempt, ...) plus a
// message that never carries a credential.
type codedClientError struct{ code string }

func (e *codedClientError) Error() string { return "internal API 409 " + e.code + ": refused" }

// ErrorCode is the same accessor the supervisor's internalError exposes.
func (e *codedClientError) ErrorCode() string { return e.code }

// refusedProposalCodes are the contract refusals T114 observed. Each of them means
// the proposal was not stored, so the attempt still has to leave.
var refusedProposalCodes = []string{
	"run_state_conflict", // the Run is cancelling (or waiting/finalizing)
	"run_terminal",       // the Run already committed a terminal state
	"stale_attempt",      // another attempt owns the lease
}

// refusedPathCase builds each executor failure path that submits a result
// proposal. The returned client has the refusal scripted, its Run observed in
// cancelling, and the fake finalizer commits cancelled on the exit.
type refusedPathCase struct {
	name string
	// build wires the scripted client/sandbox/inputs for the failure path.
	build func(t *testing.T, refusal error) (*fakeClient, *fakeSandbox, *fakeInputs)
	// wantCode is the failure code the path proposes before being refused.
	wantCode string
}

func refusedPathCases() []refusedPathCase {
	cancelState := func(client *fakeClient, refusal error) {
		client.runState = "cancelling"
		client.runStateAfterExit = "cancelled"
		client.submitErr = refusal
	}
	return []refusedPathCase{
		{
			name: "refuse before any sandbox",
			build: func(t *testing.T, refusal error) (*fakeClient, *fakeSandbox, *fakeInputs) {
				// Two python nodes: the plan refuses this graph before the sandbox.
				lease, manifest, _ := successLease(t, PresetToolID, 2)
				client := &fakeClient{lease: lease, manifest: manifest}
				cancelState(client, refusal)
				return client, &fakeSandbox{}, &fakeInputs{}
			},
			wantCode: CodeToolUnsupportedGraph,
		},
		{
			name: "failRun after a started sandbox",
			build: func(t *testing.T, refusal error) (*fakeClient, *fakeSandbox, *fakeInputs) {
				lease, manifest, _ := successLease(t, PresetToolID, 1)
				input := inputArtifact()
				client := &fakeClient{
					lease: lease, manifest: manifest, inputs: []InputArtifact{input},
					inputBytes: []byte(salesCSV),
				}
				cancelState(client, refusal)
				box := &fakeSandbox{
					outcome: sandbox.Outcome{
						SandboxStarted: true, CleanupVerified: true,
						Cleanup: sandbox.CleanupFacts{WorkspaceRemoved: true},
					},
					err: sandbox.ErrTimeout,
				}
				return client, box, &fakeInputs{inputs: client.inputs}
			},
			wantCode: CodeToolTimeout,
		},
		{
			name: "failAfterStart after a platform refusal",
			build: func(t *testing.T, refusal error) (*fakeClient, *fakeSandbox, *fakeInputs) {
				lease, manifest, _ := successLease(t, PresetToolID, 1)
				input := inputArtifact()
				client := &fakeClient{
					lease: lease, manifest: manifest, inputs: []InputArtifact{input},
					inputBytes: []byte(salesCSV),
					// The cancel lands between the sandbox and the registration: the
					// artifact registration is refused with 409 run_state_conflict.
					registerErr: &codedClientError{code: "run_state_conflict"},
				}
				cancelState(client, refusal)
				return client, successSandbox(), &fakeInputs{inputs: client.inputs}
			},
			wantCode: CodeSandboxFailed,
		},
		{
			name: "failAfterStart after a refused succeeded proposal",
			build: func(t *testing.T, refusal error) (*fakeClient, *fakeSandbox, *fakeInputs) {
				lease, manifest, _ := successLease(t, PresetToolID, 1)
				input := inputArtifact()
				client := &fakeClient{
					lease: lease, manifest: manifest, inputs: []InputArtifact{input},
					inputBytes: []byte(salesCSV),
				}
				cancelState(client, refusal)
				// The succeeded proposal itself is refused first, then the failed
				// fallback proposal is refused for the same reason.
				return client, successSandbox(), &fakeInputs{inputs: client.inputs}
			},
			wantCode: CodeSandboxFailed,
		},
		{
			name: "graph model node refused while publishing its data",
			build: func(t *testing.T, refusal error) (*fakeClient, *fakeSandbox, *fakeInputs) {
				// T114's exact shape: the model capability call returns after the
				// cancel intent landed, and the node's data upload/registration is
				// refused with 409 run_state_conflict. The scheduler commits the
				// failure through the same refuse path.
				document := modelGraphDocument(t, nil)
				lease, manifest := graphLease(t, document)
				client := graphClient(t, lease, manifest, `{"task":"cancel race"}`)
				client.invokeCapability = modelSuccess(map[string]any{
					"text": "stub text", "tool_calls": []any{},
					"structured": map[string]any{"summary": "stub-summary"},
				})
				client.registerErr = &codedClientError{code: "run_state_conflict"}
				cancelState(client, refusal)
				return client, successSandbox(), &fakeInputs{inputs: client.inputs}
			},
			wantCode: CodeSandboxFailed,
		},
	}
}

// TestRefusedProposalStillConfirmsTheAttemptExit is the CANCEL-RACE-SLOT-LEAK
// regression: every failure path, for every contract refusal, must still call
// ConfirmExit exactly once with the identity evidence. That exit is what lets the
// finalizer commit cancelled and release the slot.
func TestRefusedProposalStillConfirmsTheAttemptExit(t *testing.T) {
	for _, code := range refusedProposalCodes {
		for _, testCase := range refusedPathCases() {
			t.Run(testCase.name+"/"+code, func(t *testing.T) {
				client, box, inputs := testCase.build(t, &codedClientError{code: code})
				deps := baseDeps(t, client, inputs, box)
				deps.Facts = currentFacts()
				report := Run(context.Background(), deps)

				if len(client.exits) != 1 {
					t.Fatalf("a refused proposal must still confirm the attempt exit: exits=%+v report=%+v",
						client.exits, report)
				}
				evidence := client.exits[0].Evidence
				if started, _ := evidence["started"].(bool); !started {
					t.Fatalf("the exit evidence must certify the claimed attempt: %+v", evidence)
				}
				if report.RunState != "cancelled" {
					t.Fatalf("run state = %q, want cancelled (report=%+v)", report.RunState, report)
				}
				if report.Outcome != "cancelled" || report.ErrorCode != "cancel_requested" {
					t.Fatalf("the platform committed cancelled; report = %+v", report)
				}
				if report.Error != "" || report.Status != "executed" {
					t.Fatalf("a confirmed cancellation is not an executor error: %+v", report)
				}
				if len(client.proposals) != 0 {
					t.Fatalf("a refused proposal must never be stored: %+v", client.proposals)
				}
				if client.submitCalls == 0 {
					t.Fatal("the failure path did not even attempt its proposal")
				}
			})
		}
	}
}

// TestSucceededProposalRefusedWhileCancellingStillConfirmsExit covers the exact
// T114 window: the executor finished its work and proposed succeeded, but the
// cancel intent had landed first. The proposal refusal is not a reason to strand
// the attempt.
func TestSucceededProposalRefusedWhileCancellingStillConfirmsExit(t *testing.T) {
	lease, manifest, _ := successLease(t, PresetToolID, 1)
	input := inputArtifact()
	client := &fakeClient{
		lease: lease, manifest: manifest, inputs: []InputArtifact{input},
		inputBytes: []byte(salesCSV),
		runState:   "cancelling", runStateAfterExit: "cancelled",
		submitErr: &codedClientError{code: "run_state_conflict"},
	}
	report := Run(context.Background(), baseDeps(t, client, &fakeInputs{inputs: client.inputs}, successSandbox()))
	if len(client.exits) != 1 {
		t.Fatalf("exits = %+v, report = %+v", client.exits, report)
	}
	if report.Outcome != "cancelled" || report.RunState != "cancelled" {
		t.Fatalf("report = %+v", report)
	}
	if len(client.proposals) != 0 {
		t.Fatalf("proposals = %+v", client.proposals)
	}
}

// TestRefusedProposalWithDeadTokenReturnsSafely proves the other half of the
// contract: when the platform already considers the token dead (the Attempt
// exited, was revoked or superseded), the exit confirmation is refused with
// stale_attempt. The executor must report a readable combined diagnostic, make
// exactly one attempt, and never panic or leak the attempt token.
func TestRefusedProposalWithDeadTokenReturnsSafely(t *testing.T) {
	for _, code := range refusedProposalCodes {
		t.Run(code, func(t *testing.T) {
			lease, manifest, _ := successLease(t, PresetToolID, 1)
			input := inputArtifact()
			client := &fakeClient{
				lease: lease, manifest: manifest, inputs: []InputArtifact{input},
				inputBytes: []byte(salesCSV),
				runState:   "recovery_pending",
				submitErr:  &codedClientError{code: code},
				exitErr:    &codedClientError{code: "stale_attempt"},
			}
			box := &fakeSandbox{
				outcome: sandbox.Outcome{
					SandboxStarted: true, CleanupVerified: true,
					Cleanup: sandbox.CleanupFacts{WorkspaceRemoved: true},
				},
				err: sandbox.ErrTimeout,
			}
			report := Run(context.Background(), baseDeps(t,
				client, &fakeInputs{inputs: client.inputs}, box))

			if len(client.exits) != 1 {
				t.Fatalf("the exit must be attempted exactly once: exits=%+v", client.exits)
			}
			if report.Status != "failed" || report.Outcome != "error" {
				t.Fatalf("report = %+v", report)
			}
			if !strings.Contains(report.Error, code) || !strings.Contains(report.Error, "stale_attempt") {
				t.Fatalf("the diagnostic must name both refusals: %q", report.Error)
			}
			if strings.Contains(report.Error, lease.AttemptToken) {
				t.Fatalf("the report must never carry the attempt token: %q", report.Error)
			}
			if report.ErrorCode != CodeToolTimeout {
				t.Fatalf("the failure code changed: %q", report.ErrorCode)
			}
			if len(client.proposals) != 0 {
				t.Fatalf("proposals = %+v", client.proposals)
			}
		})
	}
}

// TestRefusedProposalWithExitConfirmedButRunNotCancelled covers the second half
// of endRefusedProposal (F13B/T114; B2 review fix S6): the proposal was refused,
// the attempt exit WAS confirmed, but the one finalizer did not commit
// `cancelled`. That is the deadline shape - the deadline finalizer observed the
// active-time limit first and committed `failed(deadline_exceeded)` - and any
// other non-cancelled terminal/parked state behaves the same. The executor must
// report the finalizer's state with its own failure code, not claim a
// cancellation it did not observe.
func TestRefusedProposalWithExitConfirmedButRunNotCancelled(t *testing.T) {
	cases := []struct {
		name       string
		finalState string
		refusal    string
	}{
		{"deadline finalizer committed failed", "failed", "run_state_conflict"},
		{"parked for reconciliation", "needs_reconciliation", "run_terminal"},
	}
	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			lease, manifest, _ := successLease(t, PresetToolID, 1)
			input := inputArtifact()
			client := &fakeClient{
				lease: lease, manifest: manifest, inputs: []InputArtifact{input},
				inputBytes: []byte(salesCSV),
				// The cancel/deadline intent landed before the proposal: the Run is
				// no longer executing, so the result proposal is refused.
				runState:          "cancelling",
				runStateAfterExit: testCase.finalState,
				submitErr:         &codedClientError{code: testCase.refusal},
			}
			box := &fakeSandbox{
				outcome: sandbox.Outcome{
					SandboxStarted: true, CleanupVerified: true,
					Cleanup: sandbox.CleanupFacts{WorkspaceRemoved: true},
				},
				err: sandbox.ErrTimeout,
			}
			report := Run(context.Background(), baseDeps(t,
				client, &fakeInputs{inputs: client.inputs}, box))

			if len(client.exits) != 1 {
				t.Fatalf("the refused proposal must still confirm the exit: exits=%+v report=%+v",
					client.exits, report)
			}
			if started, _ := client.exits[0].Evidence["started"].(bool); !started {
				t.Fatalf("the exit evidence must certify the claimed attempt: %+v", client.exits[0].Evidence)
			}
			if report.RunState != testCase.finalState {
				t.Fatalf("run state = %q, want the finalizer's state %q", report.RunState, testCase.finalState)
			}
			if report.Outcome != "failed" {
				t.Fatalf("a non-cancelled finalizer state must be reported as failed: %+v", report)
			}
			// Report.Status=="executed" is the executor's own success at doing its
			// job (it proposed, was refused, and still certified the exit); the
			// Run's state is the finalizer's decision above.
			if report.Status != "executed" {
				t.Fatalf("the executor completed its attempt; Status = %q (report=%+v)", report.Status, report)
			}
			if report.ErrorCode != CodeToolTimeout {
				t.Fatalf("the executor's own failure code changed: %q", report.ErrorCode)
			}
			for _, fragment := range []string{"the proposal was refused", testCase.refusal, "the attempt exit was confirmed"} {
				if !strings.Contains(report.Error, fragment) {
					t.Fatalf("the diagnostic must contain %q: %q", fragment, report.Error)
				}
			}
			if strings.Contains(report.Error, "cancelled") {
				t.Fatalf("the executor must not claim a cancellation it did not observe: %q", report.Error)
			}
			if len(client.proposals) != 0 {
				t.Fatalf("a refused proposal must never be stored: %+v", client.proposals)
			}
		})
	}
}
