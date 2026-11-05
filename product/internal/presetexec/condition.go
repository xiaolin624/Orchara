package presetexec

// F04B/T083: the condition node evaluator.
//
// The DSL has no expression language: a condition walks its sealed `cases` in
// order and selects the first port whose operator holds, otherwise the
// `default_port`. The operand is the case's named entry in the node's resolved
// `inputs` map (never a JSON Pointer, never a shell/JQ/Python expression).
//
// Comparison is strictly JSON-typed. `eq`/`ne` compare type and value, so
// `true != "true"`, `1 != "1"` and a decimal money string never equals a number;
// `1` and `1.0` are the same JSON number and are equal. `gt/gte/lt/lte` accept
// JSON numbers only: a string (including a decimal amount such as "120.50"), a
// bool, null, an array or an object is a `condition_operand_invalid` failure
// rather than a guessed coercion. `in` requires a JSON array; `exists` takes no
// value and tests "bound and non-null".
//
// The node output is the T083 convention documented in the F04B report and
// consumed by the run view and the later F04B tickets:
//
//	{"port": "<selected>", "matched_case": <0-based index>|"default"}

import (
	"fmt"
	"reflect"
)

// nodeError is a located node execution refusal with its stable run code. The
// pointer is the definition path the publish validator would have reported.
type nodeError struct {
	Code    string
	Pointer string
	Message string
}

func (e *nodeError) Error() string {
	if e.Pointer == "" {
		return e.Message
	}
	return e.Pointer + ": " + e.Message
}

// codeOfNodeError maps a node refusal to its stable run code.
func codeOfNodeError(err error) string {
	if typed, ok := err.(*nodeError); ok && typed.Code != "" {
		return typed.Code
	}
	return CodeNodeInputInvalid
}

// evaluateCondition resolves the selected port for one condition node. `bindings`
// is the node's fully resolved input map; the node's `inputs` entries were
// resolved and validated before this call.
func evaluateCondition(node *graphNode, bindings map[string]any) (map[string]any, error) {
	if node == nil || node.condition == nil {
		return nil, &nodeError{Code: CodeBadConfig, Message: "the node is not a condition"}
	}
	for position, testCase := range node.condition.Cases {
		bound, present := bindings[testCase.Input]
		if !present {
			return nil, &nodeError{
				Code:    CodeNodeInputInvalid,
				Pointer: fmt.Sprintf("/nodes/%d/inputs/%s", node.index, testCase.Input),
				Message: "the condition case names an input that is not bound",
			}
		}
		matched, err := conditionMatches(node, position, testCase, bound)
		if err != nil {
			return nil, err
		}
		if matched {
			return map[string]any{
				"port":         testCase.Port,
				"matched_case": float64(position),
			}, nil
		}
	}
	return map[string]any{
		"port":         node.condition.DefaultPort,
		"matched_case": "default",
	}, nil
}

// conditionMatches evaluates one case. Only the case's own operand is compared;
// no other binding is read and no environment/expression is ever consulted.
func conditionMatches(
	node *graphNode, position int, testCase conditionCase, bound any,
) (bool, error) {
	pointer := fmt.Sprintf("/nodes/%d/config/cases/%d", node.index, position)
	missing := func() error {
		return &nodeError{
			Code:    CodeMissingValue,
			Pointer: pointer + "/value",
			Message: fmt.Sprintf("the %s case has no value", testCase.Op),
		}
	}
	operandInvalid := func(message string) error {
		return &nodeError{
			Code:    CodeConditionOperandInvalid,
			Pointer: pointer,
			Message: message,
		}
	}
	switch testCase.Op {
	case "eq", "ne":
		if !testCase.HasValue {
			return false, missing()
		}
		equal := jsonValuesEqual(bound, testCase.Value)
		if testCase.Op == "ne" {
			return !equal, nil
		}
		return equal, nil
	case "gt", "gte", "lt", "lte":
		if !testCase.HasValue {
			return false, missing()
		}
		left, leftOK := bound.(float64)
		right, rightOK := testCase.Value.(float64)
		if !leftOK || !rightOK {
			return false, operandInvalid(fmt.Sprintf(
				"the %s comparison needs JSON numbers, got %T and %T", testCase.Op, bound, testCase.Value))
		}
		switch testCase.Op {
		case "gt":
			return left > right, nil
		case "gte":
			return left >= right, nil
		case "lt":
			return left < right, nil
		default:
			return left <= right, nil
		}
	case "in":
		if !testCase.HasValue {
			return false, missing()
		}
		list, ok := testCase.Value.([]any)
		if !ok {
			return false, operandInvalid(fmt.Sprintf(
				"the in operator needs a JSON array, got %T", testCase.Value))
		}
		for _, candidate := range list {
			if jsonValuesEqual(bound, candidate) {
				return true, nil
			}
		}
		return false, nil
	case "exists":
		if testCase.HasValue {
			return false, &nodeError{
				Code:    CodeBadConfig,
				Pointer: pointer + "/value",
				Message: "the exists operator takes no value",
			}
		}
		return bound != nil, nil
	default:
		return false, &nodeError{
			Code:    CodeBadConfig,
			Pointer: pointer + "/op",
			Message: fmt.Sprintf("the condition op %q is not supported", testCase.Op),
		}
	}
}

// jsonValuesEqual compares two decoded JSON values by type and value. Decoded
// numbers are float64 on both sides (the executor normalises json.Number), so
// `1` and `1.0` are the same JSON number while `1` and `"1"` are never equal.
func jsonValuesEqual(left, right any) bool {
	return reflect.DeepEqual(left, right)
}
