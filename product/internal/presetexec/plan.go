package presetexec

// F04A/T042: the linear start -> python -> end plan and its two validation
// layers.
//
// The sealed manifest's `definition` is the authority for what a run may do. The
// legacy T033 path only looked for "exactly one python node"; this file adds the
// explicit plan the ticket needs:
//
//	start --edge--> python --edge--> end
//
// The plan is deliberately closed. Exactly one start, one end and one python
// node, exactly the two chain edges, and any other node type is a deterministic
// refusal: branches, joins, multiple python nodes, model/retrieve/condition/
// approval nodes and (until T043) agent nodes are not executable by this build.
// The agent refusal has its own code so T043 can replace it without changing the
// graph parser.
//
// Two validators live here:
//   - bindings: JSON Pointer resolution against the validated start output and
//     upstream node outputs (`input#/...`, `node:<id>#/...`), where a missing or
//     wrongly-typed value is a located node_input_invalid failure.
//   - a restricted JSON Schema subset (type/properties/required/additionalProperties/
//     items/enum) for the definition input/output and the python node output.
//     The subset is intentionally small: the platform validates structure, it
//     does not implement a general JSON Schema engine (documented in the report).

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"reflect"
	"strconv"
	"strings"
)

// planBinding is one sealed node input binding: exactly one of literal or ref.
type planBinding struct {
	Literal json.RawMessage `json:"literal"`
	Ref     string          `json:"ref"`
}

// linearPlan is the parsed and proven chain.
type linearPlan struct {
	definition *manifestDefinition
	start      *manifestNode
	python     *manifestNode
	end        *manifestNode
	// order is the execution order: start, python, end.
	order []*manifestNode
}

// planError is a plan refusal with its stable run error code.
type planError struct {
	code    string
	message string
}

func (e *planError) Error() string { return e.message }

// buildLinearPlan parses the sealed definition and proves it is exactly one
// start -> python -> end chain naming the fixed preset tool.
func buildLinearPlan(definition *manifestDefinition) (*linearPlan, error) {
	if definition == nil || len(definition.Nodes) == 0 {
		return nil, &planError{code: CodeToolUnsupportedGraph,
			message: "the sealed definition carries no nodes"}
	}
	byID := make(map[string]*manifestNode, len(definition.Nodes))
	var start, end, python *manifestNode
	for index := range definition.Nodes {
		node := &definition.Nodes[index]
		if node.ID == "" {
			return nil, &planError{code: CodeToolUnsupportedGraph,
				message: "a sealed node has no id"}
		}
		if _, duplicate := byID[node.ID]; duplicate {
			return nil, &planError{code: CodeToolUnsupportedGraph,
				message: "the sealed definition repeats node id " + node.ID}
		}
		byID[node.ID] = node
		switch node.Type {
		case "start":
			if start != nil {
				return nil, &planError{code: CodeToolUnsupportedGraph,
					message: "the sealed definition has more than one start node"}
			}
			start = node
		case "end":
			if end != nil {
				return nil, &planError{code: CodeToolUnsupportedGraph,
					message: "the sealed definition has more than one end node"}
			}
			end = node
		case "python":
			if python != nil {
				return nil, &planError{code: CodeToolUnsupportedGraph,
					message: "the sealed definition has more than one python node; this executor runs one"}
			}
			python = node
		case "agent":
			// T043 owns agent nodes. This build never pretends to run one: the
			// legacy T033 golden graph (start/python/agent/end) is still served
			// by the legacy single-python path, but a graph this plan is asked
			// to execute may not contain an agent node.
			return nil, &planError{code: CodeAgentNodeUnsupported,
				message: "the sealed definition contains an agent node; agent execution is T043 and is not supported by this build"}
		default:
			return nil, &planError{code: CodeToolUnsupportedGraph,
				message: "the sealed definition contains a " + node.Type +
					" node; this executor runs exactly start, python and end"}
		}
	}
	if start == nil || end == nil || python == nil {
		return nil, &planError{code: CodeToolUnsupportedGraph,
			message: "the sealed definition must contain exactly one start, one python and one end node"}
	}
	if len(definition.Nodes) != 3 {
		return nil, &planError{code: CodeToolUnsupportedGraph,
			message: fmt.Sprintf("the sealed definition has %d nodes; this executor runs exactly three",
				len(definition.Nodes))}
	}
	// The chain: exactly start->python and python->end, in either edge order.
	if len(definition.Edges) != 2 {
		return nil, &planError{code: CodeToolUnsupportedGraph,
			message: fmt.Sprintf("the sealed definition has %d edges; exactly two chain edges are required",
				len(definition.Edges))}
	}
	seen := make(map[string]bool, 2)
	for _, edge := range definition.Edges {
		key := edge.Source + "\x00" + edge.Target
		if seen[key] {
			return nil, &planError{code: CodeToolUnsupportedGraph,
				message: "the sealed definition repeats edge " + edge.Source + "->" + edge.Target}
		}
		seen[key] = true
		if !((edge.Source == start.ID && edge.Target == python.ID) ||
			(edge.Source == python.ID && edge.Target == end.ID)) {
			return nil, &planError{code: CodeToolUnsupportedGraph,
				message: "the sealed definition is not a single start -> python -> end chain: " +
					edge.Source + "->" + edge.Target}
		}
	}
	var toolConfig struct {
		ToolVersion string `json:"tool_version"`
	}
	if err := json.Unmarshal(python.Config, &toolConfig); err != nil {
		return nil, &planError{code: CodeToolUnsupportedGraph,
			message: "the python node has no readable tool_version"}
	}
	if toolConfig.ToolVersion != PresetToolID {
		return nil, &planError{code: CodeToolUnsupportedTool,
			message: "the python node references tool " + toolConfig.ToolVersion + ", not the fixed preset"}
	}
	return &linearPlan{
		definition: definition,
		start:      start,
		python:     python,
		end:        end,
		order:      []*manifestNode{start, python, end},
	}, nil
}

// locatedError is a binding/schema failure at a documented pointer. The HTTP
// report never shows it raw; the run error code carries the stable class.
type locatedError struct {
	Pointer string
	Message string
}

func (e *locatedError) Error() string {
	if e.Pointer == "" {
		return e.Message
	}
	return e.Pointer + ": " + e.Message
}

// resolveNodeBindings resolves one node's sealed `inputs` against the outputs
// available before it runs. `available` is keyed by node id; the start node is
// additionally reachable through the documented `input` namespace.
func resolveNodeBindings(
	node *manifestNode, available map[string]any,
) (map[string]any, error) {
	resolved := make(map[string]any, len(node.Inputs))
	for name, binding := range node.Inputs {
		pointer := node.ID + "." + name
		switch {
		case len(binding.Literal) > 0 && binding.Ref != "":
			return nil, &locatedError{Pointer: pointer,
				Message: "a binding must be exactly one of literal or ref"}
		case binding.Ref != "":
			value, err := resolveReference(binding.Ref, available)
			if err != nil {
				return nil, &locatedError{Pointer: pointer, Message: err.Error()}
			}
			resolved[name] = value
		case len(binding.Literal) > 0:
			var value any
			decoder := json.NewDecoder(bytes.NewReader(binding.Literal))
			decoder.UseNumber()
			if err := decoder.Decode(&value); err != nil {
				return nil, &locatedError{Pointer: pointer, Message: "the literal is not valid JSON"}
			}
			resolved[name] = normaliseNumbers(value)
		default:
			return nil, &locatedError{Pointer: pointer,
				Message: "a binding must be exactly one of literal or ref"}
		}
	}
	return resolved, nil
}

// resolveReference resolves `input#/ptr` or `node:<id>#/ptr`.
func resolveReference(ref string, available map[string]any) (any, error) {
	base, pointer, _ := strings.Cut(ref, "#")
	if base == "" {
		return nil, errors.New("the ref has no base; use input#/... or node:<id>#/...")
	}
	var root any
	ok := false
	if base == "input" {
		root, ok = available["input"]
	} else if strings.HasPrefix(base, "node:") {
		root, ok = available[strings.TrimPrefix(base, "node:")]
	}
	if !ok {
		return nil, errors.New("the reference names " + base + ", which is not an upstream output")
	}
	value, err := applyJSONPointer(root, pointer)
	if err != nil {
		return nil, err
	}
	return value, nil
}

// applyJSONPointer implements RFC 6901 against decoded JSON values.
func applyJSONPointer(root any, pointer string) (any, error) {
	if pointer == "" {
		return root, nil
	}
	if !strings.HasPrefix(pointer, "/") {
		return nil, errors.New("the JSON pointer must be empty or start with /")
	}
	current := root
	for _, raw := range strings.Split(strings.TrimPrefix(pointer, "/"), "/") {
		token, err := unescapePointerToken(raw)
		if err != nil {
			return nil, err
		}
		switch node := current.(type) {
		case map[string]any:
			next, found := node[token]
			if !found {
				return nil, errors.New("the JSON pointer " + pointer + " does not exist")
			}
			current = next
		case []any:
			index, convErr := strconv.Atoi(token)
			if convErr != nil || index < 0 || index >= len(node) {
				return nil, errors.New("the JSON pointer " + pointer + " is not a valid array index")
			}
			current = node[index]
		default:
			return nil, errors.New("the JSON pointer " + pointer + " traverses a scalar")
		}
	}
	return current, nil
}

func unescapePointerToken(token string) (string, error) {
	var builder strings.Builder
	for index := 0; index < len(token); index++ {
		if token[index] != '~' {
			builder.WriteByte(token[index])
			continue
		}
		if index+1 >= len(token) {
			return "", errors.New("the JSON pointer has a dangling ~")
		}
		switch token[index+1] {
		case '0':
			builder.WriteByte('~')
		case '1':
			builder.WriteByte('/')
		default:
			return "", errors.New("the JSON pointer has an invalid ~ escape")
		}
		index++
	}
	return builder.String(), nil
}

// validateSchema checks one decoded value against the restricted schema subset.
// An empty/nil schema allows anything (the definition's `{}` default).
func validateSchema(schema json.RawMessage, value any, pointer string) error {
	trimmed := bytes.TrimSpace(schema)
	if len(trimmed) == 0 || bytes.Equal(trimmed, []byte("null")) ||
		bytes.Equal(trimmed, []byte("true")) || bytes.Equal(trimmed, []byte("{}")) {
		return nil
	}
	var document map[string]any
	if err := json.Unmarshal(trimmed, &document); err != nil {
		return &locatedError{Pointer: pointer, Message: "the schema is not a JSON object"}
	}
	if len(document) == 0 {
		return nil
	}
	if rawType, present := document["type"]; present {
		expected, ok := rawType.(string)
		if !ok {
			return &locatedError{Pointer: pointer, Message: "the schema type is not a string"}
		}
		if !valueMatchesType(expected, value) {
			return &locatedError{Pointer: pointer,
				Message: "expected " + expected + " but found " + jsonTypeName(value)}
		}
	}
	if enumValue, present := document["enum"]; present {
		allowed, ok := enumValue.([]any)
		if !ok {
			return &locatedError{Pointer: pointer, Message: "the schema enum is not an array"}
		}
		matched := false
		for _, candidate := range allowed {
			if reflect.DeepEqual(normaliseNumbers(candidate), value) {
				matched = true
				break
			}
		}
		if !matched {
			return &locatedError{Pointer: pointer, Message: "the value is not one of the enum values"}
		}
	}
	switch typed := value.(type) {
	case map[string]any:
		if err := validateObjectSchema(document, typed, pointer); err != nil {
			return err
		}
	case []any:
		if rawItems, present := document["items"]; present {
			itemsSchema, err := json.Marshal(rawItems)
			if err != nil {
				return &locatedError{Pointer: pointer, Message: "the schema items are not encodable"}
			}
			for index, item := range typed {
				if err := validateSchema(itemsSchema, item, pointer+"/"+strconv.Itoa(index)); err != nil {
					return err
				}
			}
		}
	}
	return nil
}

func validateObjectSchema(document map[string]any, value map[string]any, pointer string) error {
	if requiredValue, present := document["required"]; present {
		required, ok := requiredValue.([]any)
		if !ok {
			return &locatedError{Pointer: pointer, Message: "the schema required list is not an array"}
		}
		for _, rawName := range required {
			name, ok := rawName.(string)
			if !ok {
				return &locatedError{Pointer: pointer, Message: "the schema required entry is not a string"}
			}
			if _, found := value[name]; !found {
				return &locatedError{Pointer: pointer + "/" + name, Message: "required property is missing"}
			}
		}
	}
	properties := map[string]json.RawMessage{}
	if rawProperties, present := document["properties"]; present {
		encoded, err := json.Marshal(rawProperties)
		if err != nil {
			return &locatedError{Pointer: pointer, Message: "the schema properties are not encodable"}
		}
		if err := json.Unmarshal(encoded, &properties); err != nil {
			return &locatedError{Pointer: pointer, Message: "the schema properties are not a map"}
		}
	}
	for name, propertySchema := range properties {
		child, found := value[name]
		if !found {
			continue
		}
		if err := validateSchema(propertySchema, child, pointer+"/"+name); err != nil {
			return err
		}
	}
	if rawAdditional, present := document["additionalProperties"]; present {
		switch additional := rawAdditional.(type) {
		case bool:
			if !additional {
				for name := range value {
					if _, declared := properties[name]; !declared {
						return &locatedError{Pointer: pointer + "/" + name,
							Message: "additional properties are not allowed"}
					}
				}
			}
		case map[string]any:
			encoded, err := json.Marshal(additional)
			if err != nil {
				return &locatedError{Pointer: pointer, Message: "additionalProperties is not encodable"}
			}
			for name, child := range value {
				if _, declared := properties[name]; declared {
					continue
				}
				if err := validateSchema(encoded, child, pointer+"/"+name); err != nil {
					return err
				}
			}
		default:
			return &locatedError{Pointer: pointer, Message: "additionalProperties is neither a bool nor a schema"}
		}
	}
	return nil
}

func valueMatchesType(expected string, value any) bool {
	switch expected {
	case "object":
		_, ok := value.(map[string]any)
		return ok
	case "array":
		_, ok := value.([]any)
		return ok
	case "string":
		_, ok := value.(string)
		return ok
	case "boolean":
		_, ok := value.(bool)
		return ok
	case "null":
		return value == nil
	case "integer":
		number, ok := value.(float64)
		return ok && !math.IsInf(number, 0) && math.Trunc(number) == number
	case "number":
		_, ok := value.(float64)
		return ok
	default:
		// An unknown type keyword is a schema the executor does not speak; fail
		// closed instead of silently accepting the value.
		return false
	}
}

func jsonTypeName(value any) string {
	switch typed := value.(type) {
	case nil:
		return "null"
	case map[string]any:
		return "object"
	case []any:
		return "array"
	case string:
		return "string"
	case bool:
		return "boolean"
	case float64:
		if math.Trunc(typed) == typed {
			return "integer"
		}
		return "number"
	default:
		return "unknown"
	}
}

// normaliseNumbers converts json.Number values (decoded with UseNumber) into the
// float64 form the schema validator speaks, recursively.
func normaliseNumbers(value any) any {
	switch typed := value.(type) {
	case json.Number:
		if number, err := typed.Float64(); err == nil {
			return number
		}
		return typed.String()
	case map[string]any:
		normalised := make(map[string]any, len(typed))
		for key, child := range typed {
			normalised[key] = normaliseNumbers(child)
		}
		return normalised
	case []any:
		normalised := make([]any, len(typed))
		for index, child := range typed {
			normalised[index] = normaliseNumbers(child)
		}
		return normalised
	default:
		return value
	}
}

// decodeJSONDocument decodes one JSON document into the normalised value shape
// (objects/arrays/scalars, numbers as float64) and rejects trailing data.
func decodeJSONDocument(payload []byte) (any, error) {
	decoder := json.NewDecoder(bytes.NewReader(payload))
	decoder.UseNumber()
	var value any
	if err := decoder.Decode(&value); err != nil {
		return nil, err
	}
	var trailing any
	if err := decoder.Decode(&trailing); !errors.Is(err, io.EOF) {
		return nil, errors.New("the document has trailing data")
	}
	return normaliseNumbers(value), nil
}
