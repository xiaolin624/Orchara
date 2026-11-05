package catalog

// F07/T075: fixed sub-application delegation at publication time.
//
// A fixed child version is declared in exactly two places: a `call_app` node's
// `config.application_version`, and an `agent` node's `config.subapp_versions`
// allowed set. Both join the frozen manifest's `subapp_versions` closure
// (collectDependencyRefs) and are read back through platform.version_dependencies
// (F02B writes the edges when it publishes a version). This file adds the two
// rules the validator was missing:
//
//  1. I/O mapping compatibility. A `call_app` node's `inputs` is an explicit map
//     of child application input fields to bindings; every required child input
//     must be mapped, no undeclared field may be invented (unless the child's
//     frozen schema explicitly allows additional properties), and every binding
//     whose type is statically knowable must be assignable to the child's
//     declared property type. The node's own `output_schema` must be a
//     refinement of the child version's frozen `output_schema`: it may expose
//     fewer fields, never one the child does not declare, and never a type the
//     child cannot produce. The child's schema is frozen in the version's
//     manifest, so "the child changed its schema" can never silently re-point a
//     published parent: a new child version is a different id with a different
//     frozen schema.
//  2. Child limits. `policy.max_child_calls` bounds the number of fixed child
//     references the definition declares (each call_app node once; each distinct
//     version in an agent's allowed set once) and `policy.max_child_depth`
//     bounds the real depth of the version_dependencies closure reachable from
//     them (a direct child is depth 1). The closure is read from the real table,
//     not inferred from the draft, so a grandchild published under a child
//     version is counted.
//
// Everything is a locateable validation error: `pointer` is the exact
// /nodes/<i>/... or /policy/... field, and `node_id` is filled when the pointer
// names a node. Nothing here creates a version, run or snapshot: these are
// publication-gate errors, exactly like the readiness/cycle codes beside them.
//
// Error codes added by this file:
//
//	io_mapping_unknown_field   a mapped input field the child version does not declare
//	io_mapping_missing_field   a required child input field that is not mapped
//	io_mapping_type_mismatch   a binding whose statically known type the child schema refuses
//	io_output_unknown_field    a declared node output the child version never produces
//	io_output_type_mismatch    a declared node output type the child schema cannot produce
//	child_calls_exceeded       more declared fixed child references than policy.max_child_calls
//	child_depth_exceeded       a fixed child version whose real closure is deeper than policy.max_child_depth
//
// A referenced version whose stored manifest has no frozen definition is
// dependency_not_ready: only a published execution manifest can be called, and
// without one the mapping cannot be checked at all.

import (
	"encoding/json"
	"fmt"
	"math"
	"sort"
	"strings"

	"orchara/platform/internal/uuid"
)

// Delegation validation error codes (T075). They extend the dependency
// vocabulary in manifest.go and are stable for the validate/debug clients.
const (
	CodeIOMappingUnknownField = "io_mapping_unknown_field"
	CodeIOMappingMissingField = "io_mapping_missing_field"
	CodeIOMappingTypeMismatch = "io_mapping_type_mismatch"
	CodeIOOutputUnknownField  = "io_output_unknown_field"
	CodeIOOutputTypeMismatch  = "io_output_type_mismatch"
	CodeChildCallsExceeded    = "child_calls_exceeded"
	CodeChildDepthExceeded    = "child_depth_exceeded"
)

// checkChildDelegation runs both T075 gates in a deterministic order: every
// call_app I/O mapping problem in node/document order, then the declared child
// limits. It returns nil when the definition may be compiled.
func checkChildDelegation(
	document map[string]any,
	refs []dependencyRef,
	subapps map[string]subappDependency,
	edges map[string][]string,
) []ExecutableError {
	located := checkCallAppMappings(document, subapps)
	located = append(located, checkChildLimits(document, refs, edges)...)
	return located
}

// ---------------------------------------------------------------------------
// I/O mapping
// ---------------------------------------------------------------------------

// checkCallAppMappings checks every call_app node against the frozen definition
// of the published child version it pins.
func checkCallAppMappings(
	document map[string]any, subapps map[string]subappDependency,
) []ExecutableError {
	nodes, _ := document["nodes"].([]any)
	if len(nodes) == 0 {
		return nil
	}
	located := make([]ExecutableError, 0)
	for position, rawNode := range nodes {
		node, ok := rawNode.(map[string]any)
		if !ok {
			continue
		}
		if nodeType, _ := node["type"].(string); nodeType != "call_app" {
			continue
		}
		nodeID, _ := node["id"].(string)
		config, _ := node["config"].(map[string]any)
		versionID, _ := config["application_version"].(string)
		if !uuid.Valid(versionID) {
			// dependency_invalid_id is reported by the readiness pass; a value
			// that is not an id has no frozen schema to check against.
			continue
		}
		row, present := subapps[normalizeDependencyID(versionID)]
		if !present {
			// dependency_not_found was already reported; nothing to compare.
			continue
		}
		childInput, childOutput, ok := childDefinitionSchemas(row.definition)
		if !ok {
			located = append(located, ExecutableError{
				NodeID:  nodeID,
				Code:    CodeDependencyNotReady,
				Pointer: fmt.Sprintf("/nodes/%d/config/application_version", position),
				Message: "the referenced published version has no frozen execution definition; its fixed I/O schema cannot be checked, so it is not callable",
			})
			continue
		}
		inputs, _ := node["inputs"].(map[string]any)
		outputSchema, _ := node["output_schema"].(map[string]any)
		located = append(located, checkCallAppInputs(document, nodes, position, nodeID, inputs, childInput)...)
		located = append(located, checkCallAppOutputs(position, nodeID, outputSchema, childOutput)...)
	}
	return located
}

// checkCallAppInputs applies the explicit-mapping rule to one node.
func checkCallAppInputs(
	document map[string]any, nodes []any, position int, nodeID string,
	mapped map[string]any, childInput map[string]any,
) []ExecutableError {
	childProperties, _ := childInput["properties"].(map[string]any)
	located := make([]ExecutableError, 0)

	for _, field := range sortedMapKeys(mapped) {
		binding, _ := mapped[field].(map[string]any)
		declared, present := childProperties[field]
		if !present {
			if childAcceptsAdditionalInputs(childInput) {
				continue
			}
			located = append(located, ExecutableError{
				NodeID:  nodeID,
				Code:    CodeIOMappingUnknownField,
				Pointer: fmt.Sprintf("/nodes/%d/inputs/%s", position, escapeJSONPointer(field)),
				Message: "the child version " + nodeID + " does not declare the input field \"" + field +
					"\" in its frozen input_schema; the mapping may only name declared child inputs",
			})
			continue
		}
		childSchema, ok := declared.(map[string]any)
		if !ok {
			continue
		}
		sourceType := bindingType(document, nodes, binding)
		if schemaTypeAccepts(childSchema, sourceType) {
			continue
		}
		located = append(located, ExecutableError{
			NodeID:  nodeID,
			Code:    CodeIOMappingTypeMismatch,
			Pointer: fmt.Sprintf("/nodes/%d/inputs/%s", position, escapeJSONPointer(field)),
			Message: "the child input \"" + field + "\" is declared as " + describeSchemaTypes(childSchema) +
				" but the binding produces " + sourceType + "; the mapping must be type compatible",
		})
	}

	// A required child input with no binding would reach the child as a missing
	// value. The contract never invents an implicit null, so reject it here.
	required, _ := childInput["required"].([]any)
	for _, rawField := range required {
		field, ok := rawField.(string)
		if !ok {
			continue
		}
		if _, bound := mapped[field]; bound {
			continue
		}
		located = append(located, ExecutableError{
			NodeID:  nodeID,
			Code:    CodeIOMappingMissingField,
			Pointer: fmt.Sprintf("/nodes/%d/inputs/%s", position, escapeJSONPointer(field)),
			Message: "the child version requires the input field \"" + field +
				"\" but the call_app node does not map it; required child inputs are never defaulted",
		})
	}
	return located
}

// checkCallAppOutputs applies the refinement rule to one node's output schema.
func checkCallAppOutputs(
	position int, nodeID string, nodeOutput, childOutput map[string]any,
) []ExecutableError {
	nodeProperties, _ := nodeOutput["properties"].(map[string]any)
	childProperties, _ := childOutput["properties"].(map[string]any)
	located := make([]ExecutableError, 0)
	for _, field := range sortedMapKeys(nodeProperties) {
		declared, present := childProperties[field]
		if !present {
			located = append(located, ExecutableError{
				NodeID:  nodeID,
				Code:    CodeIOOutputUnknownField,
				Pointer: fmt.Sprintf("/nodes/%d/output_schema/properties/%s", position, escapeJSONPointer(field)),
				Message: "the child version does not declare the output field \"" + field +
					"\" in its frozen output_schema; the platform only guarantees declared child outputs",
			})
			continue
		}
		childSchema, ok := declared.(map[string]any)
		if !ok {
			continue
		}
		nodeSchema, _ := nodeProperties[field].(map[string]any)
		nodeType := primitiveSchemaType(nodeSchema)
		if schemaTypeAccepts(childSchema, nodeType) {
			continue
		}
		located = append(located, ExecutableError{
			NodeID:  nodeID,
			Code:    CodeIOOutputTypeMismatch,
			Pointer: fmt.Sprintf("/nodes/%d/output_schema/properties/%s", position, escapeJSONPointer(field)),
			Message: "the child output \"" + field + "\" is declared as " + describeSchemaTypes(childSchema) +
				" but this node declares " + nodeType + "; the declaration must be a refinement of the child schema",
		})
	}
	return located
}

// childDefinitionSchemas extracts the frozen input/output schemas from a stored
// version's manifest definition object. A version whose manifest carries no
// definition (or one without both object schemas) cannot be called: the mapping
// would be checked against nothing.
func childDefinitionSchemas(raw []byte) (map[string]any, map[string]any, bool) {
	if len(raw) == 0 {
		return nil, nil, false
	}
	var definition map[string]any
	if err := json.Unmarshal(raw, &definition); err != nil || definition == nil {
		return nil, nil, false
	}
	input, okInput := definition["input_schema"].(map[string]any)
	output, okOutput := definition["output_schema"].(map[string]any)
	if !okInput || !okOutput {
		return nil, nil, false
	}
	return input, output, true
}

// childAcceptsAdditionalInputs reports whether the child's frozen schema
// explicitly allows fields it does not declare (bare `additionalProperties:
// true` or an additionalProperties schema). An absent keyword is treated as
// closed for mapping purposes: the platform only sends explicitly declared
// fields to a child, so an undeclared mapping is a mistake even where JSON
// Schema would tolerate it.
func childAcceptsAdditionalInputs(childInput map[string]any) bool {
	additional, present := childInput["additionalProperties"]
	if !present {
		return false
	}
	if flag, ok := additional.(bool); ok {
		return flag
	}
	_, ok := additional.(map[string]any)
	return ok
}

// bindingType resolves the statically declarable JSON type of one input binding
// ("" when it cannot be known statically, e.g. a producer schema with no type).
func bindingType(document map[string]any, nodes []any, binding map[string]any) string {
	if value, present := binding["literal"]; present {
		return jsonTypeName(value)
	}
	reference, _ := binding["ref"].(string)
	head, pointer, _ := strings.Cut(reference, "#")
	var producer map[string]any
	switch {
	case head == "input":
		producer, _ = document["input_schema"].(map[string]any)
	case strings.HasPrefix(head, "node:"):
		target := strings.TrimPrefix(head, "node:")
		for _, rawNode := range nodes {
			node, ok := rawNode.(map[string]any)
			if !ok {
				continue
			}
			if id, _ := node["id"].(string); id == target {
				producer, _ = node["output_schema"].(map[string]any)
				break
			}
		}
	}
	if producer == nil {
		return ""
	}
	if pointer != "" {
		resolved, ok := resolveSchemaPointer(producer, pointer)
		if !ok {
			return ""
		}
		producer = resolved
	}
	return primitiveSchemaType(producer)
}

// primitiveSchemaType is the single declared type name of a schema, or "" when
// the schema declares no type or a union (which the mapping never guesses).
func primitiveSchemaType(schema map[string]any) string {
	types := schemaTypeNames(schema)
	if len(types) != 1 {
		return ""
	}
	return types[0]
}

// schemaTypeNames reads the restricted subset's `type` keyword (a string or an
// array of strings).
func schemaTypeNames(schema map[string]any) []string {
	switch typed := schema["type"].(type) {
	case string:
		return []string{typed}
	case []any:
		names := make([]string, 0, len(typed))
		for _, item := range typed {
			if name, ok := item.(string); ok {
				names = append(names, name)
			}
		}
		return names
	}
	return nil
}

// schemaTypeAccepts applies the contract's type-compatibility rule: identical
// type names, plus the widening integer -> number. An unknown source type or a
// schema that constrains nothing is accepted (the check never guesses).
func schemaTypeAccepts(childSchema map[string]any, sourceType string) bool {
	names := schemaTypeNames(childSchema)
	if len(names) == 0 || sourceType == "" {
		return true
	}
	for _, name := range names {
		if name == sourceType {
			return true
		}
		if name == "number" && sourceType == "integer" {
			return true
		}
	}
	return false
}

func describeSchemaTypes(schema map[string]any) string {
	names := schemaTypeNames(schema)
	if len(names) == 0 {
		return "any type"
	}
	return strings.Join(names, "|")
}

// jsonTypeName classifies one decoded JSON value for the type-compatibility rule.
// Decoded numbers are float64, so an integral value is reported as integer and a
// fractional one as number: a literal 1 is assignable to an integer property, a
// literal 1.5 is not.
func jsonTypeName(value any) string {
	switch typed := value.(type) {
	case nil:
		return "null"
	case string:
		return "string"
	case bool:
		return "boolean"
	case float64:
		if typed == math.Trunc(typed) && !math.IsInf(typed, 0) && !math.IsNaN(typed) {
			return "integer"
		}
		return "number"
	case json.Number:
		if parsed, err := typed.Float64(); err == nil && parsed == math.Trunc(parsed) {
			return "integer"
		}
		return "number"
	case []any:
		return "array"
	case map[string]any:
		return "object"
	}
	return ""
}

// resolveSchemaPointer walks a producer schema by a JSON Pointer, mirroring the
// workflow validator's descend rule (properties -> additionalProperties ->
// array items -> open object). It returns the schema node the pointer names.
func resolveSchemaPointer(schema map[string]any, pointer string) (map[string]any, bool) {
	if !strings.HasPrefix(pointer, "/") {
		return nil, false
	}
	var current any = schema
	for _, raw := range strings.Split(pointer[1:], "/") {
		token, ok := unescapeJSONPointer(raw)
		if !ok {
			return nil, false
		}
		next, ok := descendSchema(current, token)
		if !ok {
			return nil, false
		}
		current = next
	}
	resolved, ok := current.(map[string]any)
	if !ok {
		return nil, false
	}
	return resolved, true
}

func descendSchema(schema any, token string) (any, bool) {
	object, ok := schema.(map[string]any)
	if !ok {
		return nil, false
	}
	if properties, present := object["properties"]; present {
		if propertyMap, ok := properties.(map[string]any); ok {
			if child, present := propertyMap[token]; present {
				return child, true
			}
		}
	}
	if additional, present := object["additionalProperties"]; present {
		if child, ok := additional.(map[string]any); ok {
			return child, true
		}
		if flag, ok := additional.(bool); ok && !flag {
			return nil, false
		}
	}
	if declared, _ := object["type"].(string); declared == "array" {
		if isDigits(token) {
			if items, present := object["items"]; present {
				if child, ok := items.(map[string]any); ok {
					return child, true
				}
			}
			return map[string]any{}, true
		}
	}
	if _, present := object["type"]; !present {
		return map[string]any{}, true
	}
	if declared, _ := object["type"].(string); declared == "object" {
		return map[string]any{}, true
	}
	return nil, false
}

func isDigits(text string) bool {
	if text == "" {
		return false
	}
	for _, char := range text {
		if char < '0' || char > '9' {
			return false
		}
	}
	return true
}

// escapeJSONPointer escapes one JSON Pointer token (RFC 6901).
func escapeJSONPointer(token string) string {
	return strings.ReplaceAll(strings.ReplaceAll(token, "~", "~0"), "/", "~1")
}

func unescapeJSONPointer(token string) (string, bool) {
	if !strings.Contains(token, "~") {
		return token, true
	}
	var builder strings.Builder
	for index := 0; index < len(token); index++ {
		if token[index] != '~' {
			builder.WriteByte(token[index])
			continue
		}
		if index+1 >= len(token) {
			return "", false
		}
		switch token[index+1] {
		case '0':
			builder.WriteByte('~')
		case '1':
			builder.WriteByte('/')
		default:
			return "", false
		}
		index++
	}
	return builder.String(), true
}

// sortedMapKeys is the deterministic member order of a JSON object.
func sortedMapKeys(object map[string]any) []string {
	keys := make([]string, 0, len(object))
	for key := range object {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	return keys
}

// ---------------------------------------------------------------------------
// child limits
// ---------------------------------------------------------------------------

// checkChildLimits enforces policy.max_child_calls (declared fixed child
// references) and policy.max_child_depth (real closure depth) over the
// definition and the version_dependencies closure. Structural validation has
// already proven both fields are integers in 0..12 / 0..3.
func checkChildLimits(
	document map[string]any, refs []dependencyRef, edges map[string][]string,
) []ExecutableError {
	policy, _ := document["policy"].(map[string]any)
	if maxCalls, present := integerMember(policy, "max_child_calls"); present {
		if count := declaredChildCallCount(document); count > maxCalls {
			return []ExecutableError{{
				Code:    CodeChildCallsExceeded,
				Pointer: "/policy/max_child_calls",
				Message: fmt.Sprintf(
					"the definition declares %d fixed child references, but policy.max_child_calls is %d; child calls are only allowed within the published policy",
					count, maxCalls),
			}}
		}
	}
	maxDepth, present := integerMember(policy, "max_child_depth")
	if !present {
		return nil
	}
	for _, ref := range refs {
		if ref.kind != dependencySubapp || !uuid.Valid(ref.id) {
			continue
		}
		id := normalizeDependencyID(ref.id)
		depth := childClosureDepth(id, edges)
		if depth <= maxDepth {
			continue
		}
		return []ExecutableError{{
			NodeID:  ref.nodeID,
			Code:    CodeChildDepthExceeded,
			Pointer: ref.pointer,
			Message: fmt.Sprintf(
				"the fixed child version %s closes over %d child level(s), but policy.max_child_depth is %d; the closure comes from platform.version_dependencies, not from the draft",
				id, depth, maxDepth),
		}}
	}
	return nil
}

// declaredChildCallCount counts the fixed child references the definition
// declares: every call_app node once, and each distinct version in one agent's
// subapp_versions allowed set once (a set declaration, not one call per
// duplicate entry).
func declaredChildCallCount(document map[string]any) int {
	nodes, _ := document["nodes"].([]any)
	count := 0
	for _, rawNode := range nodes {
		node, ok := rawNode.(map[string]any)
		if !ok {
			continue
		}
		nodeType, _ := node["type"].(string)
		config, _ := node["config"].(map[string]any)
		switch nodeType {
		case "call_app":
			count++
		case "agent":
			seen := map[string]bool{}
			values, _ := config["subapp_versions"].([]any)
			for _, raw := range values {
				value, ok := raw.(string)
				if !ok {
					continue
				}
				id := normalizeDependencyID(value)
				if id == "" || seen[id] {
					continue
				}
				seen[id] = true
				count++
			}
		}
	}
	return count
}

// childClosureDepth returns the number of child generations reachable below one
// fixed child version: the direct child is 1, its direct children make 2, and so
// on. It memoises each node's height and tolerates a cyclic edge set (the cycle
// is rejected by checkSubappCycles before this runs) so it can never recurse
// forever.
func childClosureDepth(root string, edges map[string][]string) int {
	height := make(map[string]int, len(edges))
	visiting := make(map[string]bool, len(edges))
	var visit func(node string) int
	visit = func(node string) int {
		if known, ok := height[node]; ok {
			return known
		}
		if visiting[node] {
			// A back edge: the cycle is reported elsewhere; do not count it.
			return 0
		}
		visiting[node] = true
		deepest := 0
		for _, child := range edges[node] {
			if below := visit(child); below > deepest {
				deepest = below
			}
		}
		visiting[node] = false
		height[node] = deepest + 1
		return height[node]
	}
	return visit(root)
}

// integerMember reads one decoded JSON integer member.
func integerMember(object map[string]any, key string) (int, bool) {
	value, present := object[key]
	if !present || object == nil {
		return 0, false
	}
	switch typed := value.(type) {
	case float64:
		return int(typed), true
	case int:
		return typed, true
	case json.Number:
		if parsed, err := typed.Int64(); err == nil {
			return int(parsed), true
		}
	}
	return 0, false
}
