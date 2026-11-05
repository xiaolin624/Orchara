package catalog

// F07/T078: spawn-time re-check of the T075 delegation gate.
//
// T075 validated the mapping and the declared child limits at publication time.
// A child Run is created long after that: the draft may have moved on, the child
// version may have been revoked, and the executor sends a concrete mapped input
// object over the internal wire. The spawn endpoint must therefore re-prove the
// same facts against the *frozen* parent manifest, not against a live draft, and
// it must not invent a second copy of the mapping/limit rules.
//
// CheckChildSpawn is that re-check. It receives the parent Run's frozen
// `definition`, the requested child version id and the child version's frozen
// `definition` (both read from the sealed execution snapshots), and:
//
//  1. proves the requested version is still declared by the frozen definition
//     (`child_not_in_dependency_closure`). This is the "父版本固定子依赖" proof:
//     the closure is the sealed manifest, never the live draft or the router.
//  2. runs T075's `checkCallAppMappings` again against the child's frozen input
//     and output schemas, exactly as publication did.
//  3. runs T075's `checkChildLimits` again over the declared references (the
//     static count/depth rule; the runtime depth of this particular Run is
//     measured separately from `runs.parent_run_id`).
//
// It also returns the call_app nodes that pin the version and the sorted field
// set each one maps, so the transport can prove the supplied input object carries
// exactly one declared mapping and nothing else. The mapping is read from the
// sealed definition; a caller cannot add a field by sending it.

import (
	"encoding/json"
	"sort"
	"strings"

	"orchara/platform/internal/jcs"
	"orchara/platform/internal/jsonschema"
	"orchara/platform/internal/uuid"
)

// CodeChildNotInClosure is the spawn-time refusal for a version the parent's
// frozen manifest does not name. T075 has no equivalent because at publication
// time every reference is, by construction, in the document being compiled.
const CodeChildNotInClosure = "child_not_in_dependency_closure"

// Spawn-time located codes. They extend the T075 vocabulary for the two facts
// that only exist once a Run is executing.
const (
	CodeChildInputMappingMismatch = "child_input_mapping_mismatch"
	CodeChildInputInvalid         = "child_input_invalid"
	CodeChildOutputInvalid        = "child_output_schema_invalid"
)

// ChildCallMapping is one call_app node of the frozen parent definition that
// pins the requested child version.
type ChildCallMapping struct {
	NodeID string
	// Fields are the child input field names the declaration allows, sorted.
	// For a call_app node these are exactly the mapped fields (an exact match is
	// required). For an agent's subapp tool they are the child input schema's
	// declared properties (a subset match is allowed: the model chooses which
	// declared inputs to supply).
	Fields []string
	// Subset is true for an agent sub-app tool, false for a call_app mapping.
	Subset bool
}

// CheckChildSpawn re-proves T075's delegation gate for one child version against
// the frozen parent definition. It returns the declaring mappings (call_app
// nodes and agent sub-app tools) and every located problem. An empty problem
// list means the version is callable.
func CheckChildSpawn(
	definition map[string]any, childVersionID string, childDefinition map[string]any,
) ([]ChildCallMapping, []ExecutableError) {
	version := normalizeDependencyID(childVersionID)
	if version == "" || !uuid.Valid(version) {
		return nil, []ExecutableError{{
			Code:    CodeChildNotInClosure,
			Pointer: "/nodes",
			Message: "the spawn request names no child application version",
		}}
	}
	refs := collectDependencyRefs(definition)
	referenced := false
	for _, ref := range refs {
		if ref.kind == dependencySubapp && normalizeDependencyID(ref.id) == version {
			referenced = true
			break
		}
	}
	if !referenced {
		return nil, []ExecutableError{{
			Code:    CodeChildNotInClosure,
			Pointer: "/nodes",
			Message: "the parent's frozen manifest does not declare the child application version " +
				version + "; a Run may only call a fixed sub-application version its own sealed definition pins",
		}}
	}

	encoded, err := json.Marshal(childDefinition)
	if err != nil {
		return nil, []ExecutableError{{
			Code:    CodeDependencyNotReady,
			Pointer: "/nodes",
			Message: "the child version's frozen definition cannot be read",
		}}
	}
	subapps := map[string]subappDependency{version: {definition: encoded}}
	located := make([]ExecutableError, 0, 2)
	located = append(located, checkCallAppMappings(definition, subapps)...)
	// The edges are not needed here: this is the declared-limit re-check (a
	// direct child counts as one level). The runtime depth of this Run comes from
	// runs.parent_run_id and is enforced by the children service.
	located = append(located, checkChildLimits(definition, refs, nil)...)
	return childMappings(definition, version, childDefinition), located
}

// childMappings returns every declaration that names one child version: a
// call_app node's exact mapping, and/or an agent node's allowed sub-app set.
func childMappings(
	definition map[string]any, version string, childDefinition map[string]any,
) []ChildCallMapping {
	nodes, _ := definition["nodes"].([]any)
	mappings := make([]ChildCallMapping, 0, 1)
	for _, rawNode := range nodes {
		node, ok := rawNode.(map[string]any)
		if !ok {
			continue
		}
		nodeID, _ := node["id"].(string)
		config, _ := node["config"].(map[string]any)
		switch nodeType, _ := node["type"].(string); nodeType {
		case "call_app":
			pinned, _ := config["application_version"].(string)
			if normalizeDependencyID(pinned) != version {
				continue
			}
			inputs, _ := node["inputs"].(map[string]any)
			fields := make([]string, 0, len(inputs))
			for field := range inputs {
				fields = append(fields, field)
			}
			sort.Strings(fields)
			mappings = append(mappings, ChildCallMapping{NodeID: nodeID, Fields: fields})
		case "agent":
			declared, _ := config["subapp_versions"].([]any)
			names := false
			for _, item := range declared {
				if value, ok := item.(string); ok && normalizeDependencyID(value) == version {
					names = true
					break
				}
			}
			if !names {
				continue
			}
			input, _, ok := ChildDefinitionSchemas(childDefinition)
			if !ok {
				continue
			}
			properties, _ := input["properties"].(map[string]any)
			fields := make([]string, 0, len(properties))
			for field := range properties {
				fields = append(fields, field)
			}
			sort.Strings(fields)
			mappings = append(mappings, ChildCallMapping{
				NodeID: nodeID, Fields: fields, Subset: true,
			})
		}
	}
	return mappings
}

// MatchChildInputMapping finds one declared mapping that accepts the supplied
// input's keys. A call_app mapping is exact (the node declares every child input
// it maps); an agent sub-app tool accepts any subset of the child's declared
// input properties, and the child input schema still validates the values. It
// returns the mapping and the sorted supplied keys. ok is false when no
// declaration accepts the object; the caller reports the declared sets without
// echoing the supplied values.
func MatchChildInputMapping(
	mappings []ChildCallMapping, input map[string]any,
) (ChildCallMapping, []string, bool) {
	keys := make([]string, 0, len(input))
	for key := range input {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	// Exact call_app mappings win over an agent's subset declaration.
	for _, exact := range []bool{true, false} {
		for _, mapping := range mappings {
			if mapping.Subset == exact && mappingAccepts(mapping, keys) {
				return mapping, keys, true
			}
		}
	}
	return ChildCallMapping{}, keys, false
}

func mappingAccepts(mapping ChildCallMapping, keys []string) bool {
	if !mapping.Subset {
		return equalStrings(mapping.Fields, keys)
	}
	allowed := make(map[string]bool, len(mapping.Fields))
	for _, field := range mapping.Fields {
		allowed[field] = true
	}
	for _, key := range keys {
		if !allowed[key] {
			return false
		}
	}
	return true
}

// DescribeChildMappings renders the declared field sets for a located 422. It
// never includes values.
func DescribeChildMappings(mappings []ChildCallMapping) []string {
	described := make([]string, 0, len(mappings))
	for _, mapping := range mappings {
		described = append(described, mapping.NodeID+"=["+strings.Join(mapping.Fields, ", ")+"]")
	}
	sort.Strings(described)
	return described
}

// DefinitionOfManifest extracts the `definition` object of a frozen manifest.
// A manifest without one is not callable and the caller maps that to
// dependency_not_ready, never to an empty schema.
func DefinitionOfManifest(manifest []byte) (map[string]any, bool) {
	var document map[string]any
	if err := json.Unmarshal(manifest, &document); err != nil || document == nil {
		return nil, false
	}
	definition, ok := document["definition"].(map[string]any)
	if !ok || definition == nil {
		return nil, false
	}
	return definition, true
}

// ChildDefinitionSchemas returns the frozen input/output schema objects of a
// child version definition (the same read T075 uses).
func ChildDefinitionSchemas(definition map[string]any) (map[string]any, map[string]any, bool) {
	if definition == nil {
		return nil, nil, false
	}
	input, okInput := definition["input_schema"].(map[string]any)
	output, okOutput := definition["output_schema"].(map[string]any)
	if !okInput || !okOutput {
		return nil, nil, false
	}
	return input, output, true
}

// ChildSchemaHashes returns the SHA-256 identities of the child's frozen I/O
// schemas: RFC 8785 canonical bytes of each schema object. A definition without
// both objects has no identity and is refused at spawn time.
func ChildSchemaHashes(definition map[string]any) (string, string, bool) {
	input, output, ok := ChildDefinitionSchemas(definition)
	if !ok {
		return "", "", false
	}
	inputHash, err := canonicalObjectSHA256(input)
	if err != nil {
		return "", "", false
	}
	outputHash, err := canonicalObjectSHA256(output)
	if err != nil {
		return "", "", false
	}
	return inputHash, outputHash, true
}

// ValidateChildInput validates the mapped child input against the child's frozen
// input schema with the platform's shared restricted-subset validator.
func ValidateChildInput(definition map[string]any, input map[string]any) []ExecutableError {
	inputSchema, _, ok := ChildDefinitionSchemas(definition)
	if !ok {
		return []ExecutableError{{
			Code:    CodeDependencyNotReady,
			Pointer: "/input",
			Message: "the child version has no frozen input schema; its mapped input cannot be validated",
		}}
	}
	problems := jsonschema.ValidateValue(inputSchema, input)
	located := make([]ExecutableError, 0, len(problems))
	for _, problem := range problems {
		pointer := "/input" + problem.Field
		if problem.Field == "" {
			pointer = "/input"
		}
		located = append(located, ExecutableError{
			Code:    CodeChildInputInvalid,
			Pointer: pointer,
			Message: problem.Message,
		})
	}
	return located
}

// ValidateChildResult validates one terminal child result against the child's
// frozen output schema. It is the same gate the finalizer applies, exposed so the
// executor and the server validator cannot disagree.
func ValidateChildResult(definition map[string]any, result map[string]any) []ExecutableError {
	_, outputSchema, ok := ChildDefinitionSchemas(definition)
	if !ok {
		return []ExecutableError{{
			Code:    CodeDependencyNotReady,
			Pointer: "/result",
			Message: "the child version has no frozen output schema; its result cannot be validated",
		}}
	}
	problems := jsonschema.ValidateValue(outputSchema, result)
	located := make([]ExecutableError, 0, len(problems))
	for _, problem := range problems {
		pointer := "/result" + problem.Field
		if problem.Field == "" {
			pointer = "/result"
		}
		located = append(located, ExecutableError{
			Code:    CodeChildOutputInvalid,
			Pointer: pointer,
			Message: problem.Message,
		})
	}
	return located
}

func canonicalObjectSHA256(value map[string]any) (string, error) {
	encoded, err := json.Marshal(value)
	if err != nil {
		return "", err
	}
	canonical, err := jcs.CanonicalizeBytes(encoded)
	if err != nil {
		return "", err
	}
	return jcs.SHA256Hex(canonical), nil
}

// equalStrings compares two sorted string slices.
func equalStrings(left, right []string) bool {
	if len(left) != len(right) {
		return false
	}
	for index := range left {
		if left[index] != right[index] {
			return false
		}
	}
	return true
}
