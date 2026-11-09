package presetexec

// F06/T047: the agent node's authorized-context wiring.
//
// These helpers are deliberately thin. The builder in internal/context owns the
// budget, the truncation order and the protected-fact vocabulary; this file only
// tells it which values the current node execution resolved, which sealed nodes
// produced them, and which model profile budget applies. No value is read from
// the database here: the execution's namespace is the scheduler's snapshot of the
// already-published sealed upstream outputs, and the scope is the sealed node-id
// snapshot taken when the execution started.
//
// C4.01: the agent no longer receives a synthetic linear plan. The binding source
// kind is derived from the sealed definition's node type, and the authorization
// scope is the explicit sealed scope of this attempt.

import (
	"errors"
	"strings"

	agentcontext "orchara/platform/internal/context"
)

// The documented fallbacks when a model profile config predates the explicit
// budget fields. They mirror the capability server's own output default; the
// context window default is the conservative platform default, and both are
// recorded in the trace's budget.
const (
	DefaultAgentContextWindow   int64 = 128000
	DefaultAgentMaxOutputTokens int64 = 4096
)

// agentContextScope is the run-derived authorization scope: only the sealed
// nodes of this one attempt, and no free-standing artifact ids. The agent never
// receives another run's data because it cannot name one.
func agentContextScope(exec *nodeExecution) agentcontext.Scope {
	ids := make([]string, 0, len(exec.scopeNodeIDs)+1)
	seen := make(map[string]bool, len(exec.scopeNodeIDs)+1)
	for _, id := range exec.scopeNodeIDs {
		if id == "" || seen[id] {
			continue
		}
		seen[id] = true
		ids = append(ids, id)
	}
	if exec.node != nil && exec.node.ID != "" && !seen[exec.node.ID] {
		ids = append(ids, exec.node.ID)
	}
	return agentcontext.Scope{
		NodeIDs:     ids,
		ArtifactIDs: []string{},
	}
}

// resolvedAgentInputs turns the sealed bindings into named, typed inputs. The
// source kind is derived from the sealed ref and the sealed definition's node type,
// never from a caller guess: `input#` and `node:<start>#` are start sources, a
// `node:<python>#` ref is a python source and a literal is a creator constant. Any
// other upstream node output is a controlled node output ("tool"); the builder
// still authorizes it only because its node id is in the run scope.
func resolvedAgentInputs(
	exec *nodeExecution, bindings map[string]any,
) []agentcontext.ResolvedInput {
	labels := make([]string, 0, len(bindings))
	for label := range bindings {
		labels = append(labels, label)
	}
	sortStrings(labels)
	inputs := make([]agentcontext.ResolvedInput, 0, len(labels))
	for _, label := range labels {
		source := agentcontext.Source{Kind: "literal", Label: label}
		if binding, present := exec.node.Inputs[label]; present && binding.Ref != "" {
			base, _, _ := strings.Cut(binding.Ref, "#")
			switch {
			case base == "input":
				source.Kind = "start"
				source.NodeID = exec.startNodeID
			case strings.HasPrefix(base, "node:"):
				origin := strings.TrimPrefix(base, "node:")
				source.NodeID = origin
				switch exec.sealedNodeType(origin) {
				case "start":
					source.Kind = "start"
				case "python":
					source.Kind = "python"
				default:
					source.Kind = "tool"
				}
			}
		}
		inputs = append(inputs, agentcontext.ResolvedInput{
			Label: label, Source: source, Value: bindings[label],
		})
	}
	return inputs
}

// modelContextBudget returns the explicit profile budget, applying the
// documented defaults only when the profile leaves a field unset.
func modelContextBudget(fact ModelFact) agentcontext.ModelConfig {
	model := agentcontext.ModelConfig{
		ContextWindow: fact.ContextWindow, MaxOutputTokens: fact.MaxOutputTokens,
	}
	if model.ContextWindow < 1 {
		model.ContextWindow = DefaultAgentContextWindow
	}
	if model.MaxOutputTokens < 1 {
		model.MaxOutputTokens = DefaultAgentMaxOutputTokens
	}
	return model
}

// contextFailureCode maps a builder refusal to the stable run error code. The
// builder's codes are already platform-safe; anything unexpected is a config
// refusal, never a provider code.
func contextFailureCode(err error) string {
	var typed *agentcontext.Error
	if errors.As(err, &typed) && safePlatformCode(typed.Code) {
		return typed.Code
	}
	return CodeAgentConfigInvalid
}

// agentContextTraceDocument is the durable trace artifact written once per agent
// node. It has identifiers, reasons, key names and token counts only; no message
// text, memory text or credential can reach it.
type agentContextTraceDocument struct {
	Version int                  `json:"version"`
	NodeID  string               `json:"node_id"`
	Turns   []agentcontext.Trace `json:"turns"`
}

// sortStrings sorts in place without pulling an extra dependency into the hot
// agent path; determinism is the only requirement.
func sortStrings(values []string) {
	for index := 1; index < len(values); index++ {
		for cursor := index; cursor > 0 && values[cursor] < values[cursor-1]; cursor-- {
			values[cursor], values[cursor-1] = values[cursor-1], values[cursor]
		}
	}
}
