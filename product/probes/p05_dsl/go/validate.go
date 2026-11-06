// P05/T007 — restricted workflow-DSL validator, Go side.
//
// This is the second, independent implementation of the rules in
// ../python/validate.py. Both read ../rules.json and both must produce
// byte-identical verdicts over ../corpus.json; the shared corpus is the
// contract between them, not this file.
//
// Pipeline (same stages, same order, same precedence):
//
//  1. document shape and the restricted JSON-Schema subset
//  2. identity and endpoints
//  3. DAG properties
//  4. structured regions (pairing, ports, cross-region edges, join sources)
//  5. references, pointers, dominance and missing values
//
// The first stage that reports anything decides the verdict; the reported codes
// keep detection order. A valid document yields two separate outputs: the
// canonical *data* (NormalizedWorkflow) and the publishable *artifact*
// (WorkflowArtifact) whose provenance is part of its digest.
package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
)

// ---------------------------------------------------------------------------
// value helpers — absence and an explicit null stay distinguishable
// ---------------------------------------------------------------------------
func asObject(value any) (map[string]any, bool) {
	object, ok := value.(map[string]any)
	return object, ok
}

func asArray(value any) ([]any, bool) {
	array, ok := value.([]any)
	return array, ok
}

func asString(value any) (string, bool) {
	text, ok := value.(string)
	return text, ok
}

func asNumber(value any) (float64, bool) {
	number, ok := value.(float64)
	if !ok || math.IsNaN(number) || math.IsInf(number, 0) {
		return 0, false
	}
	return number, true
}

func isInteger(value any) bool {
	number, ok := asNumber(value)
	return ok && number == math.Trunc(number)
}

func sortedKeys(object map[string]any) []string {
	keys := make([]string, 0, len(object))
	for key := range object {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	return keys
}

func escapePointer(token string) string {
	return strings.ReplaceAll(strings.ReplaceAll(token, "~", "~0"), "/", "~1")
}

// ---------------------------------------------------------------------------
// rule tables
// ---------------------------------------------------------------------------
type FieldSpec struct {
	Kind     string   `json:"kind"`
	Min      *float64 `json:"min"`
	Max      *float64 `json:"max"`
	MinLen   *int     `json:"min_len"`
	MaxLen   *int     `json:"max_len"`
	MinItems *int     `json:"min_items"`
	Values   []string `json:"values"`
}

type ConfigSpec struct {
	Required []string             `json:"required"`
	Fields   map[string]FieldSpec `json:"fields"`
}

type RuleEntry struct {
	Code    string `json:"code"`
	Stage   int    `json:"stage"`
	Meaning string `json:"meaning"`
}

type Rules struct {
	Probe        string `json:"probe"`
	ProbeVersion string `json:"probe_version"`
	SchemaID     string `json:"schema_id"`
	DSLVersion   string `json:"dsl_version"`
	Limit        struct {
		MinNodes       int `json:"min_nodes"`
		MaxNodes       int `json:"max_nodes"`
		MaxEdges       int `json:"max_edges"`
		MaxSchemaDepth int `json:"max_schema_depth"`
	} `json:"limit"`
	Patterns         map[string]string     `json:"patterns"`
	DocumentKeys     []string              `json:"document_keys"`
	NodeKeys         []string              `json:"node_keys"`
	EdgeKeys         []string              `json:"edge_keys"`
	EdgeRequiredKeys []string              `json:"edge_required_keys"`
	NodeTypes        []string              `json:"node_types"`
	Config           map[string]ConfigSpec `json:"config"`
	Condition        struct {
		Ops              []string `json:"ops"`
		NumericOps       []string `json:"numeric_ops"`
		ListOps          []string `json:"list_ops"`
		ValuelessOps     []string `json:"valueless_ops"`
		CaseKeys         []string `json:"case_keys"`
		CaseRequiredKeys []string `json:"case_required_keys"`
		MinCases         int      `json:"min_cases"`
	} `json:"condition"`
	Policy ConfigSpec `json:"policy"`
	Schema struct {
		RootType string   `json:"root_type"`
		Keywords []string `json:"keywords"`
	} `json:"schema"`
	Reasons []RuleEntry `json:"reasons"`

	sha256   string
	compiled map[string]*regexp.Regexp
	// unanchored keeps the patterns as written, for scanning (template
	// variables); compiled wraps them in ^(?:...)$ for whole-value validation.
	unanchored map[string]*regexp.Regexp
	types      map[string]bool
}

// LoadRules reads and compiles ../rules.json.
func LoadRules(path string) (*Rules, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	rules := &Rules{}
	if err := json.Unmarshal(raw, rules); err != nil {
		return nil, fmt.Errorf("rules %s: %w", path, err)
	}
	rules.sha256 = SHA256Hex(raw)
	rules.compiled = make(map[string]*regexp.Regexp, len(rules.Patterns))
	rules.unanchored = make(map[string]*regexp.Regexp, len(rules.Patterns))
	for name, source := range rules.Patterns {
		raw, err := regexp.Compile(source)
		if err != nil {
			return nil, fmt.Errorf("rules pattern %q: %w", name, err)
		}
		rules.unanchored[name] = raw
		compiled, err := regexp.Compile("^(?:" + source + ")$")
		if err != nil {
			return nil, fmt.Errorf("rules pattern %q: %w", name, err)
		}
		rules.compiled[name] = compiled
	}
	rules.types = make(map[string]bool, len(rules.NodeTypes))
	for _, name := range rules.NodeTypes {
		rules.types[name] = true
	}
	return rules, nil
}

func (r *Rules) matches(pattern, value string) bool {
	compiled, ok := r.compiled[pattern]
	return ok && compiled.MatchString(value)
}

func contains(values []string, wanted string) bool {
	for _, value := range values {
		if value == wanted {
			return true
		}
	}
	return false
}

// ---------------------------------------------------------------------------
// result types
// ---------------------------------------------------------------------------
type Problem struct {
	Code    string
	Pointer string
}

type problemList struct {
	items []Problem
}

func (p *problemList) add(code, pointer string) {
	for _, existing := range p.items {
		if existing.Code == code && existing.Pointer == pointer {
			return
		}
	}
	p.items = append(p.items, Problem{Code: code, Pointer: pointer})
}

func (p *problemList) empty() bool { return len(p.items) == 0 }

// NormalizedWorkflow is the *data*: the canonical normal form of a document.
type NormalizedWorkflow struct {
	Document  map[string]any
	Canonical []byte
	SHA256    string
}

// WorkflowArtifact is the *artifact*: the descriptor a publisher would register.
type WorkflowArtifact struct {
	Descriptor map[string]any
	Canonical  []byte
	SHA256     string
}

type ValidationResult struct {
	InputSHA256 string
	Problems    []Problem
	Data        *NormalizedWorkflow
	Artifact    *WorkflowArtifact
}

func (r *ValidationResult) Verdict() string {
	if len(r.Problems) > 0 {
		return "invalid"
	}
	return "valid"
}

func (r *ValidationResult) Reason() string {
	if len(r.Problems) == 0 {
		return ""
	}
	return r.Problems[0].Code
}

func (r *ValidationResult) Reasons() []string {
	ordered := make([]string, 0, len(r.Problems))
	for _, problem := range r.Problems {
		if !contains(ordered, problem.Code) {
			ordered = append(ordered, problem.Code)
		}
	}
	return ordered
}

// ---------------------------------------------------------------------------
// stage 1 — document shape and restricted schema
// ---------------------------------------------------------------------------
func stageSchema(document any, rules *Rules, problems *problemList) bool {
	doc, ok := asObject(document)
	if !ok {
		problems.add("bad_document", "")
		return false
	}
	for _, key := range sortedKeys(doc) {
		if !contains(rules.DocumentKeys, key) {
			problems.add("bad_document", "/"+escapePointer(key))
			return false
		}
	}
	for _, key := range rules.DocumentKeys {
		if _, present := doc[key]; !present {
			problems.add("bad_document", "")
			return false
		}
	}
	if version, _ := asString(doc["dsl_version"]); version != rules.DSLVersion {
		problems.add("bad_document", "/dsl_version")
		return false
	}
	inputSchema, ok := asObject(doc["input_schema"])
	if !ok {
		problems.add("bad_document", "/input_schema")
		return false
	}
	outputSchema, ok := asObject(doc["output_schema"])
	if !ok {
		problems.add("bad_document", "/output_schema")
		return false
	}
	nodesAny, ok := asArray(doc["nodes"])
	if !ok {
		problems.add("bad_document", "/nodes")
		return false
	}
	if len(nodesAny) < rules.Limit.MinNodes || len(nodesAny) > rules.Limit.MaxNodes {
		problems.add("bad_document", "/nodes")
		return false
	}
	nodes := make([]map[string]any, 0, len(nodesAny))
	for position, item := range nodesAny {
		node, ok := asObject(item)
		if !ok {
			problems.add("bad_document", fmt.Sprintf("/nodes/%d", position))
			return false
		}
		for _, key := range sortedKeys(node) {
			if !contains(rules.NodeKeys, key) {
				problems.add("bad_document", fmt.Sprintf("/nodes/%d/%s", position, escapePointer(key)))
				return false
			}
		}
		for _, key := range rules.NodeKeys {
			if _, present := node[key]; !present {
				problems.add("bad_document", fmt.Sprintf("/nodes/%d", position))
				return false
			}
		}
		if _, ok := asString(node["id"]); !ok {
			problems.add("bad_document", fmt.Sprintf("/nodes/%d", position))
			return false
		}
		if _, ok := asString(node["type"]); !ok {
			problems.add("bad_document", fmt.Sprintf("/nodes/%d", position))
			return false
		}
		if _, ok := asObject(node["inputs"]); !ok {
			problems.add("bad_document", fmt.Sprintf("/nodes/%d", position))
			return false
		}
		if _, ok := asObject(node["config"]); !ok {
			problems.add("bad_document", fmt.Sprintf("/nodes/%d", position))
			return false
		}
		if _, ok := asObject(node["output_schema"]); !ok {
			problems.add("bad_document", fmt.Sprintf("/nodes/%d/output_schema", position))
			return false
		}
		nodes = append(nodes, node)
	}
	edgesAny, ok := asArray(doc["edges"])
	if !ok {
		problems.add("bad_document", "/edges")
		return false
	}
	if len(edgesAny) > rules.Limit.MaxEdges {
		problems.add("bad_document", "/edges")
		return false
	}
	edges := make([]map[string]any, 0, len(edgesAny))
	for position, item := range edgesAny {
		edge, ok := asObject(item)
		if !ok {
			problems.add("bad_document", fmt.Sprintf("/edges/%d", position))
			return false
		}
		for _, key := range rules.EdgeRequiredKeys {
			if _, present := edge[key]; !present {
				problems.add("bad_document", fmt.Sprintf("/edges/%d", position))
				return false
			}
		}
		if _, ok := asString(edge["source"]); !ok {
			problems.add("bad_document", fmt.Sprintf("/edges/%d", position))
			return false
		}
		if _, ok := asString(edge["target"]); !ok {
			problems.add("bad_document", fmt.Sprintf("/edges/%d", position))
			return false
		}
		edges = append(edges, edge)
	}
	policy, ok := asObject(doc["policy"])
	if !ok {
		problems.add("bad_document", "/policy")
		return false
	}
	for _, key := range rules.Policy.Required {
		if _, present := policy[key]; !present {
			problems.add("bad_document", "/policy")
			return false
		}
	}

	// --- finer, per-field checks -------------------------------------------
	checkSchema(inputSchema, rules, "/input_schema", 1, problems)
	checkSchema(outputSchema, rules, "/output_schema", 1, problems)
	for position, node := range nodes {
		checkNode(node, position, rules, problems)
	}
	for position, edge := range edges {
		for _, key := range sortedKeys(edge) {
			if !contains(rules.EdgeKeys, key) {
				problems.add("bad_edge", fmt.Sprintf("/edges/%d/%s", position, escapePointer(key)))
			}
		}
		if port, present := edge["port"]; present {
			if _, ok := asString(port); !ok {
				problems.add("bad_edge", fmt.Sprintf("/edges/%d/port", position))
			}
		}
	}
	for _, name := range sortedKeys(policy) {
		spec, known := rules.Policy.Fields[name]
		if !known {
			problems.add("bad_policy", "/policy/"+escapePointer(name))
			continue
		}
		checkField(policy[name], spec, rules, "/policy/"+escapePointer(name), problems, "bad_policy")
	}
	return true
}

func checkNode(node map[string]any, position int, rules *Rules, problems *problemList) {
	at := fmt.Sprintf("/nodes/%d", position)
	id, _ := asString(node["id"])
	if !rules.matches("node_id", id) {
		problems.add("bad_id", at)
	}
	nodeType, _ := asString(node["type"])
	if !rules.types[nodeType] {
		problems.add("unknown_type", at)
		return // config/output whitelist unknown: do not guess
	}
	inputs, _ := asObject(node["inputs"])
	if nodeType == "start" && len(inputs) > 0 {
		problems.add("bad_binding", at+"/inputs")
	}
	for _, name := range sortedKeys(inputs) {
		if !bindingIsValid(inputs[name], rules) {
			problems.add("bad_binding", at+"/inputs/"+escapePointer(name))
		}
	}
	outputSchema, _ := asObject(node["output_schema"])
	checkSchema(outputSchema, rules, at+"/output_schema", 1, problems)
	config, _ := asObject(node["config"])
	checkConfig(nodeType, config, rules, at, problems)
}

func bindingIsValid(binding any, rules *Rules) bool {
	object, ok := asObject(binding)
	if !ok {
		return false
	}
	if len(object) == 1 {
		if _, ok := object["literal"]; ok {
			return true
		}
		if reference, ok := asString(object["ref"]); ok {
			return rules.matches("ref", reference)
		}
	}
	return false
}

func checkSchema(schema any, rules *Rules, at string, depth int, problems *problemList) {
	object, isObject := asObject(schema)
	if !isObject {
		problems.add("bad_schema", at)
		return
	}
	rootType, _ := asString(object["type"])
	if rootType != rules.Schema.RootType {
		problems.add("bad_schema", at)
	}
	walkSchema(object, rules, at, depth, problems)
}

func walkSchema(node map[string]any, rules *Rules, pointer string, level int, problems *problemList) {
	if level > rules.Limit.MaxSchemaDepth {
		problems.add("bad_schema", pointer)
		return
	}
	for _, key := range sortedKeys(node) {
		if !contains(rules.Schema.Keywords, key) {
			problems.add("bad_schema", pointer+"/"+escapePointer(key))
		}
	}
	if declared, present := node["type"]; present {
		switch typed := declared.(type) {
		case string:
			// a single type name is always acceptable in the restricted subset
		case []any:
			valid := len(typed) >= 1 && len(typed) <= 2
			hasNull := false
			for _, item := range typed {
				name, ok := asString(item)
				if !ok {
					valid = false
					break
				}
				if name == "null" {
					hasNull = true
				}
			}
			if len(typed) == 2 && !hasNull {
				valid = false
			}
			if !valid {
				problems.add("bad_schema", pointer+"/type")
			}
		default:
			problems.add("bad_schema", pointer+"/type")
		}
	}
	if properties, present := node["properties"]; present {
		propertyMap, ok := asObject(properties)
		if !ok {
			problems.add("bad_schema", pointer+"/properties")
		} else {
			for _, name := range sortedKeys(propertyMap) {
				child, ok := asObject(propertyMap[name])
				if !ok {
					problems.add("bad_schema", pointer+"/properties/"+escapePointer(name))
					continue
				}
				walkSchema(child, rules, pointer+"/properties/"+escapePointer(name), level+1, problems)
			}
		}
	}
	if items, present := node["items"]; present {
		child, ok := asObject(items)
		if !ok {
			problems.add("bad_schema", pointer+"/items")
		} else {
			walkSchema(child, rules, pointer+"/items", level+1, problems)
		}
	}
	if additional, present := node["additionalProperties"]; present {
		if _, isBool := additional.(bool); !isBool {
			child, ok := asObject(additional)
			if !ok {
				problems.add("bad_schema", pointer+"/additionalProperties")
			} else {
				walkSchema(child, rules, pointer+"/additionalProperties", level+1, problems)
			}
		}
	}
	if required, present := node["required"]; present {
		array, ok := asArray(required)
		valid := ok
		if ok {
			for _, item := range array {
				if _, ok := asString(item); !ok {
					valid = false
					break
				}
			}
		}
		if !valid {
			problems.add("bad_schema", pointer+"/required")
		}
	}
	for _, key := range []string{"minLength", "maxLength", "minItems", "maxItems"} {
		if value, present := node[key]; present && !isInteger(value) {
			problems.add("bad_schema", pointer+"/"+key)
		}
	}
	for _, key := range []string{"minimum", "maximum"} {
		if value, present := node[key]; present {
			if _, ok := asNumber(value); !ok {
				problems.add("bad_schema", pointer+"/"+key)
			}
		}
	}
	if value, present := node["uniqueItems"]; present {
		if _, ok := value.(bool); !ok {
			problems.add("bad_schema", pointer+"/uniqueItems")
		}
	}
	if value, present := node["enum"]; present {
		if _, ok := asArray(value); !ok {
			problems.add("bad_schema", pointer+"/enum")
		}
	}
	for _, key := range []string{"title", "description", "format"} {
		if value, present := node[key]; present {
			if _, ok := asString(value); !ok {
				problems.add("bad_schema", pointer+"/"+key)
			}
		}
	}
}

func checkConfig(nodeType string, config map[string]any, rules *Rules, at string, problems *problemList) {
	spec, known := rules.Config[nodeType]
	if !known {
		return
	}
	names := map[string]bool{}
	for name := range config {
		names[name] = true
	}
	for _, name := range spec.Required {
		names[name] = true
	}
	ordered := make([]string, 0, len(names))
	for name := range names {
		ordered = append(ordered, name)
	}
	sort.Strings(ordered)
	for _, name := range ordered {
		where := at + "/config"
		field, known := spec.Fields[name]
		if !known {
			problems.add("bad_config", where+"/"+escapePointer(name))
			continue
		}
		value, present := config[name]
		if !present {
			problems.add("bad_config", where)
			continue
		}
		if field.Kind == "condition_cases" {
			checkCases(value, rules, where+"/cases", problems)
			continue
		}
		checkField(value, field, rules, where+"/"+escapePointer(name), problems, "bad_config")
	}
}

func checkCases(cases any, rules *Rules, at string, problems *problemList) {
	array, ok := asArray(cases)
	if !ok || len(array) < rules.Condition.MinCases {
		problems.add("bad_config", at)
		return
	}
	for position, item := range array {
		where := fmt.Sprintf("%s/%d", at, position)
		object, ok := asObject(item)
		if !ok {
			problems.add("bad_config", where)
			continue
		}
		for _, key := range sortedKeys(object) {
			if !contains(rules.Condition.CaseKeys, key) {
				problems.add("bad_config", where+"/"+escapePointer(key))
			}
		}
		for _, key := range rules.Condition.CaseRequiredKeys {
			if _, present := object[key]; !present {
				problems.add("bad_config", where)
			}
		}
		if value, present := object["input"]; present {
			if _, ok := asString(value); !ok {
				problems.add("bad_config", where+"/input")
			}
		}
		if value, present := object["port"]; present {
			if _, ok := asString(value); !ok {
				problems.add("bad_config", where+"/port")
			}
		}
		if value, present := object["op"]; present {
			op, ok := asString(value)
			if !ok || !contains(rules.Condition.Ops, op) {
				problems.add("bad_config", where+"/op")
			}
		}
	}
}

func checkField(value any, spec FieldSpec, rules *Rules, at string, problems *problemList, code string) {
	switch spec.Kind {
	case "string":
		text, ok := asString(value)
		if !ok {
			problems.add(code, at)
			return
		}
		if spec.MinLen != nil && len([]rune(text)) < *spec.MinLen {
			problems.add(code, at)
		}
		if spec.MaxLen != nil && len([]rune(text)) > *spec.MaxLen {
			problems.add(code, at)
		}
	case "uuid":
		text, ok := asString(value)
		if !ok || !rules.matches("uuid", text) {
			problems.add(code, at)
		}
	case "budget":
		text, ok := asString(value)
		if !ok || !rules.matches("budget", text) {
			problems.add(code, at)
		}
	case "enum":
		text, ok := asString(value)
		if !ok || !contains(spec.Values, text) {
			problems.add(code, at)
		}
	case "integer":
		if !isInteger(value) {
			problems.add(code, at)
			return
		}
		number, _ := asNumber(value)
		if (spec.Min != nil && number < *spec.Min) || (spec.Max != nil && number > *spec.Max) {
			problems.add(code, at)
		}
	case "uuid_array", "string_array":
		array, ok := asArray(value)
		if !ok {
			problems.add(code, at)
			return
		}
		if spec.MinItems != nil && len(array) < *spec.MinItems {
			problems.add(code, at)
		}
		seen := map[string]bool{}
		for _, item := range array {
			text, ok := asString(item)
			if !ok {
				problems.add(code, at)
				return
			}
			if spec.Kind == "uuid_array" && !rules.matches("uuid", text) {
				problems.add(code, at)
				return
			}
			if seen[text] {
				problems.add(code, at)
				return
			}
			seen[text] = true
		}
	default:
		problems.add(code, at)
	}
}

// ---------------------------------------------------------------------------
// stage 2 — identity and endpoints
// ---------------------------------------------------------------------------
func stageIdentity(document map[string]any, rules *Rules, problems *problemList) bool {
	nodes, _ := asArray(document["nodes"])
	edges, _ := asArray(document["edges"])
	seen := map[string]bool{}
	for position, item := range nodes {
		node, _ := asObject(item)
		id, _ := asString(node["id"])
		if seen[id] {
			problems.add("duplicate_id", fmt.Sprintf("/nodes/%d", position))
		}
		seen[id] = true
	}
	for position, item := range edges {
		edge, _ := asObject(item)
		source, _ := asString(edge["source"])
		target, _ := asString(edge["target"])
		if !seen[source] || !seen[target] {
			problems.add("unknown_endpoint", fmt.Sprintf("/edges/%d", position))
		}
	}
	starts, ends := 0, 0
	for _, item := range nodes {
		node, _ := asObject(item)
		nodeType, _ := asString(node["type"])
		if nodeType == "start" {
			starts++
		}
		if nodeType == "end" {
			ends++
		}
	}
	if starts != 1 || ends != 1 {
		problems.add("start_end_count", "")
	}
	return problems.empty()
}

// ---------------------------------------------------------------------------
// graph context
// ---------------------------------------------------------------------------
type graph struct {
	nodes []map[string]any
	index map[string]int
	adj   map[string][]string
	radj  map[string][]string
	topo  []string
	start string
	end   string
	dom   map[string]map[string]bool
	pdom  map[string]map[string]bool
}

func buildGraph(nodes []map[string]any, edges []map[string]any, start, end string) *graph {
	g := &graph{
		nodes: nodes,
		index: map[string]int{},
		adj:   map[string][]string{},
		radj:  map[string][]string{},
		start: start,
		end:   end,
		dom:   map[string]map[string]bool{},
		pdom:  map[string]map[string]bool{},
	}
	adjacency := map[string]map[string]bool{}
	reverse := map[string]map[string]bool{}
	for position, node := range nodes {
		id, _ := asString(node["id"])
		g.index[id] = position
		adjacency[id] = map[string]bool{}
		reverse[id] = map[string]bool{}
	}
	for _, edge := range edges {
		source, _ := asString(edge["source"])
		target, _ := asString(edge["target"])
		adjacency[source][target] = true
		reverse[target][source] = true
	}
	for id, values := range adjacency {
		g.adj[id] = sortedByIndex(values, g.index)
	}
	for id, values := range reverse {
		g.radj[id] = sortedByIndex(values, g.index)
	}
	indegree := map[string]int{}
	queue := make([]string, 0, len(nodes))
	for _, node := range nodes {
		id, _ := asString(node["id"])
		indegree[id] = len(reverse[id])
		if indegree[id] == 0 {
			queue = append(queue, id)
		}
	}
	for len(queue) > 0 {
		current := queue[0]
		queue = queue[1:]
		g.topo = append(g.topo, current)
		for _, target := range g.adj[current] {
			indegree[target]--
			if indegree[target] == 0 {
				queue = append(queue, target)
			}
		}
	}
	if len(g.topo) != len(nodes) {
		return g // cyclic: dominance is undefined, the caller reports "cycle"
	}
	for _, id := range g.topo {
		parents := g.radj[id]
		if len(parents) == 0 {
			g.dom[id] = map[string]bool{id: true}
			continue
		}
		common := cloneSet(g.dom[parents[0]])
		for _, parent := range parents[1:] {
			intersect(common, g.dom[parent])
		}
		common[id] = true
		g.dom[id] = common
	}
	for position := len(g.topo) - 1; position >= 0; position-- {
		id := g.topo[position]
		children := g.adj[id]
		if len(children) == 0 {
			g.pdom[id] = map[string]bool{id: true}
			continue
		}
		common := cloneSet(g.pdom[children[0]])
		for _, child := range children[1:] {
			intersect(common, g.pdom[child])
		}
		common[id] = true
		g.pdom[id] = common
	}
	return g
}

func sortedByIndex(values map[string]bool, index map[string]int) []string {
	items := make([]string, 0, len(values))
	for value := range values {
		items = append(items, value)
	}
	sort.SliceStable(items, func(i, j int) bool { return index[items[i]] < index[items[j]] })
	return items
}

func cloneSet(source map[string]bool) map[string]bool {
	clone := make(map[string]bool, len(source)+1)
	for key := range source {
		clone[key] = true
	}
	return clone
}

func intersect(target, other map[string]bool) {
	for key := range target {
		if !other[key] {
			delete(target, key)
		}
	}
}

func (g *graph) ancestors(nodeID string) map[string]bool {
	seen := map[string]bool{}
	pending := []string{nodeID}
	for len(pending) > 0 {
		current := pending[len(pending)-1]
		pending = pending[:len(pending)-1]
		for _, parent := range g.radj[current] {
			if !seen[parent] {
				seen[parent] = true
				pending = append(pending, parent)
			}
		}
	}
	return seen
}

// ---------------------------------------------------------------------------
// stage 3 — DAG properties
// ---------------------------------------------------------------------------
func stageGraph(document map[string]any, rules *Rules, problems *problemList) *graph {
	nodesAny, _ := asArray(document["nodes"])
	edgesAny, _ := asArray(document["edges"])
	nodes := make([]map[string]any, 0, len(nodesAny))
	for _, item := range nodesAny {
		node, _ := asObject(item)
		nodes = append(nodes, node)
	}
	edges := make([]map[string]any, 0, len(edgesAny))
	for _, item := range edgesAny {
		edge, _ := asObject(item)
		edges = append(edges, edge)
	}
	start, end := "", ""
	for _, node := range nodes {
		nodeType, _ := asString(node["type"])
		id, _ := asString(node["id"])
		if nodeType == "start" {
			start = id
		}
		if nodeType == "end" {
			end = id
		}
	}
	g := buildGraph(nodes, edges, start, end)
	if len(g.topo) != len(nodes) {
		problems.add("cycle", "")
		return nil
	}
	reachable := map[string]bool{start: true}
	pending := []string{start}
	for len(pending) > 0 {
		current := pending[len(pending)-1]
		pending = pending[:len(pending)-1]
		for _, target := range g.adj[current] {
			if !reachable[target] {
				reachable[target] = true
				pending = append(pending, target)
			}
		}
	}
	for position, node := range nodes {
		id, _ := asString(node["id"])
		if !reachable[id] {
			problems.add("unreachable_node", fmt.Sprintf("/nodes/%d", position))
		}
	}
	toEnd := map[string]bool{end: true}
	pending = []string{end}
	for len(pending) > 0 {
		current := pending[len(pending)-1]
		pending = pending[:len(pending)-1]
		for _, parent := range g.radj[current] {
			if !toEnd[parent] {
				toEnd[parent] = true
				pending = append(pending, parent)
			}
		}
	}
	for position, node := range nodes {
		id, _ := asString(node["id"])
		if !toEnd[id] {
			problems.add("cannot_reach_end", fmt.Sprintf("/nodes/%d", position))
		}
	}
	for position, node := range nodes {
		id, _ := asString(node["id"])
		nodeType, _ := asString(node["type"])
		if len(g.radj[id]) >= 2 && nodeType != "join" {
			problems.add("merge_without_join", fmt.Sprintf("/nodes/%d", position))
		}
	}
	return g
}

// ---------------------------------------------------------------------------
// stage 4 — structured regions
// ---------------------------------------------------------------------------
func stageRegions(document map[string]any, rules *Rules, g *graph, problems *problemList) {
	nodesAny, _ := asArray(document["nodes"])
	edgesAny, _ := asArray(document["edges"])
	nodes := make([]map[string]any, 0, len(nodesAny))
	for _, item := range nodesAny {
		node, _ := asObject(item)
		nodes = append(nodes, node)
	}
	edges := make([]map[string]any, 0, len(edgesAny))
	for _, item := range edgesAny {
		edge, _ := asObject(item)
		edges = append(edges, edge)
	}

	// 4a: pairing (structural only; the dominance requirement is enforced edge
	// by edge in 4c, which localises the offending edge).
	declaredForks := map[string]bool{}
	for _, node := range nodes {
		nodeType, _ := asString(node["type"])
		if nodeType != "join" {
			continue
		}
		config, _ := asObject(node["config"])
		forkID, _ := asString(config["fork_id"])
		declaredForks[forkID] = true
	}
	for position, node := range nodes {
		nodeType, _ := asString(node["type"])
		if nodeType != "condition" {
			continue
		}
		id, _ := asString(node["id"])
		config, _ := asObject(node["config"])
		joinID, _ := asString(config["join_id"])
		joinPosition, known := g.index[joinID]
		valid := known
		if valid {
			join, _ := asObject(nodes[joinPosition])
			if joinType, _ := asString(join["type"]); joinType != "join" {
				valid = false
			} else {
				joinConfig, _ := asObject(join["config"])
				if forkID, _ := asString(joinConfig["fork_id"]); forkID != id {
					valid = false
				}
			}
		}
		if !valid {
			problems.add("invalid_region", fmt.Sprintf("/nodes/%d/config/join_id", position))
		}
	}
	for position, node := range nodes {
		nodeType, _ := asString(node["type"])
		if nodeType != "join" {
			continue
		}
		at := fmt.Sprintf("/nodes/%d/config/fork_id", position)
		config, _ := asObject(node["config"])
		forkID, _ := asString(config["fork_id"])
		id, _ := asString(node["id"])
		forkPosition, known := g.index[forkID]
		if !known || forkID == id {
			problems.add("invalid_region", at)
			continue
		}
		fork, _ := asObject(nodes[forkPosition])
		if forkType, _ := asString(fork["type"]); forkType == "condition" {
			forkConfig, _ := asObject(fork["config"])
			if joinID, _ := asString(forkConfig["join_id"]); joinID != id {
				problems.add("invalid_region", at)
			}
		} else if len(g.adj[forkID]) < 2 {
			problems.add("invalid_region", at)
		}
	}
	for position, node := range nodes {
		nodeType, _ := asString(node["type"])
		if nodeType == "condition" || nodeType == "join" {
			continue
		}
		id, _ := asString(node["id"])
		if len(g.adj[id]) >= 2 && !declaredForks[id] {
			problems.add("invalid_region", fmt.Sprintf("/nodes/%d", position))
		}
	}
	if !problems.empty() {
		return
	}

	type region struct{ fork, join string }
	regions := []region{}
	for _, node := range nodes {
		nodeType, _ := asString(node["type"])
		if nodeType != "join" {
			continue
		}
		config, _ := asObject(node["config"])
		forkID, _ := asString(config["fork_id"])
		id, _ := asString(node["id"])
		regions = append(regions, region{fork: forkID, join: id})
	}
	sort.SliceStable(regions, func(i, j int) bool {
		if regions[i].fork != regions[j].fork {
			return regions[i].fork < regions[j].fork
		}
		return regions[i].join < regions[j].join
	})

	// 4b: exit ports
	for position, node := range nodes {
		nodeType, _ := asString(node["type"])
		if nodeType != "condition" {
			continue
		}
		config, _ := asObject(node["config"])
		allowed := map[string]bool{}
		casesArray, _ := asArray(config["cases"])
		for _, item := range casesArray {
			entry, _ := asObject(item)
			port, _ := asString(entry["port"])
			allowed[port] = true
		}
		if defaultPort, ok := asString(config["default_port"]); ok {
			allowed[defaultPort] = true
		}
		actual := map[string]bool{}
		id, _ := asString(node["id"])
		for edgePosition, edge := range edges {
			source, _ := asString(edge["source"])
			if source != id {
				continue
			}
			port := "default"
			if explicit, present := edge["port"]; present {
				if text, ok := asString(explicit); ok {
					port = text
				}
			}
			actual[port] = true
			if !allowed[port] {
				problems.add("bad_port", fmt.Sprintf("/edges/%d", edgePosition))
			}
		}
		missing := make([]string, 0, len(allowed))
		for port := range allowed {
			if !actual[port] {
				missing = append(missing, port)
			}
		}
		sort.Strings(missing)
		for range missing {
			problems.add("bad_port", fmt.Sprintf("/nodes/%d/config/cases", position))
		}
	}
	for position, edge := range edges {
		source, _ := asString(edge["source"])
		sourcePosition := g.index[source]
		sourceNode := nodes[sourcePosition]
		sourceType, _ := asString(sourceNode["type"])
		if sourceType == "condition" {
			continue
		}
		if port, present := edge["port"]; present {
			if text, ok := asString(port); ok && text != "default" {
				problems.add("bad_port", fmt.Sprintf("/edges/%d", position))
			}
		}
	}
	if !problems.empty() {
		return
	}

	// 4c: cross-region edges
	for _, current := range regions {
		inside := map[string]bool{}
		for _, node := range nodes {
			id, _ := asString(node["id"])
			if id == current.fork || id == current.join {
				continue
			}
			if g.dom[id][current.fork] && g.pdom[id][current.join] {
				inside[id] = true
			}
		}
		for edgePosition, edge := range edges {
			source, _ := asString(edge["source"])
			target, _ := asString(edge["target"])
			sourceInside := inside[source]
			targetInside := inside[target]
			illegal := false
			switch {
			case sourceInside && !targetInside && target != current.join:
				illegal = true
			case targetInside && !sourceInside && source != current.fork:
				illegal = true
			case source == current.fork && target != current.join && !targetInside:
				illegal = true
			case target == current.join && source != current.fork && !sourceInside:
				illegal = true
			}
			if illegal {
				problems.add("cross_region_edge", fmt.Sprintf("/edges/%d", edgePosition))
			}
		}
	}
	if !problems.empty() {
		return
	}

	// 4d: join source coverage
	for position, node := range nodes {
		nodeType, _ := asString(node["type"])
		if nodeType != "join" {
			continue
		}
		config, _ := asObject(node["config"])
		id, _ := asString(node["id"])
		required := stringArray(config["required_sources"])
		optional := stringArray(config["optional_sources"])
		declared := append(append([]string{}, required...), optional...)
		seen := map[string]bool{}
		duplicate := false
		for _, name := range declared {
			if seen[name] {
				duplicate = true
			}
			seen[name] = true
		}
		overlap := false
		for _, name := range required {
			if contains(optional, name) {
				overlap = true
			}
		}
		actual := map[string]bool{}
		for _, edge := range edges {
			target, _ := asString(edge["target"])
			if target != id {
				continue
			}
			source, _ := asString(edge["source"])
			actual[source] = true
		}
		actualList := make([]string, 0, len(actual))
		for name := range actual {
			actualList = append(actualList, name)
		}
		sort.Strings(actualList)
		declaredList := make([]string, 0, len(seen))
		for name := range seen {
			declaredList = append(declaredList, name)
		}
		sort.Strings(declaredList)
		if duplicate || overlap || !equalStrings(declaredList, actualList) {
			problems.add("bad_join_sources", fmt.Sprintf("/nodes/%d/config/required_sources", position))
		}
		// Optional convergence is limited to pure read/compute nodes; a
		// write-capable, human-waiting or model branch must be rejected here
		// rather than published and failed closed at run time.
		for _, name := range optional {
			sourcePosition, known := g.index[name]
			if !known {
				continue
			}
			sourceType, _ := asString(nodes[sourcePosition]["type"])
			if !optionalSourceTypes[sourceType] {
				problems.add("bad_join_sources",
					fmt.Sprintf("/nodes/%d/config/optional_sources", position))
			}
		}
	}
}

// optionalSourceTypes is the publish-time twin of internal/presetexec's
// optionalFailureNodeTypes: only these node types are pure reads or pure
// computation, so only their explicit failures may be converged as error data.
var optionalSourceTypes = map[string]bool{
	"python":    true,
	"condition": true,
	"join":      true,
	"retrieve":  true,
}

func stringArray(value any) []string {
	array, ok := asArray(value)
	if !ok {
		return nil
	}
	items := make([]string, 0, len(array))
	for _, item := range array {
		if text, ok := asString(item); ok {
			items = append(items, text)
		}
	}
	return items
}

func equalStrings(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	for index := range a {
		if a[index] != b[index] {
			return false
		}
	}
	return true
}

// ---------------------------------------------------------------------------
// stage 5 — references, pointers, dominance, missing values
// ---------------------------------------------------------------------------
func unescapePointer(token string) (string, bool) {
	var builder strings.Builder
	for index := 0; index < len(token); index++ {
		char := token[index]
		if char != '~' {
			builder.WriteByte(char)
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

// pointerIsDeclared reports whether the JSON Pointer is reachable inside the
// declared schema. Absence is only rejected when the schema proves it: an open
// object cannot prove a field missing.
func pointerIsDeclared(schema any, pointer string) bool {
	if pointer == "" {
		return true
	}
	if !strings.HasPrefix(pointer, "/") {
		return false
	}
	current := schema
	for _, raw := range strings.Split(pointer[1:], "/") {
		token, ok := unescapePointer(raw)
		if !ok {
			return false
		}
		next, ok := descend(current, token)
		if !ok {
			return false
		}
		current = next
	}
	return true
}

func descend(schema any, token string) (any, bool) {
	object, ok := asObject(schema)
	if !ok {
		return nil, false
	}
	if properties, present := object["properties"]; present {
		if propertyMap, ok := asObject(properties); ok {
			if child, present := propertyMap[token]; present {
				return child, true
			}
		}
	}
	if additional, present := object["additionalProperties"]; present {
		if child, ok := asObject(additional); ok {
			return child, true
		}
		if flag, ok := additional.(bool); ok && !flag {
			return nil, false
		}
	}
	if declared, _ := asString(object["type"]); declared == "array" {
		if isDigits(token) {
			if items, present := object["items"]; present {
				if child, ok := asObject(items); ok {
					return child, true
				}
			}
			return map[string]any{}, true
		}
	}
	if _, present := object["type"]; !present {
		return map[string]any{}, true
	}
	if declared, _ := asString(object["type"]); declared == "object" {
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

func templateVariables(template string, rules *Rules) []string {
	compiled, ok := rules.unanchored["template_variable"]
	if !ok {
		return nil
	}
	found := []string{}
	for _, match := range compiled.FindAllStringSubmatch(template, -1) {
		if len(match) < 2 {
			continue
		}
		if !contains(found, match[1]) {
			found = append(found, match[1])
		}
	}
	return found
}

func stageReferences(document map[string]any, rules *Rules, g *graph, problems *problemList) {
	nodesAny, _ := asArray(document["nodes"])
	nodes := make([]map[string]any, 0, len(nodesAny))
	for _, item := range nodesAny {
		node, _ := asObject(item)
		nodes = append(nodes, node)
	}
	inputSchema := document["input_schema"]

	// 5a: bindings
	for position, node := range nodes {
		id, _ := asString(node["id"])
		inputs, _ := asObject(node["inputs"])
		for _, name := range sortedKeys(inputs) {
			binding, _ := asObject(inputs[name])
			at := fmt.Sprintf("/nodes/%d/inputs/%s", position, escapePointer(name))
			if _, literal := binding["literal"]; literal {
				continue
			}
			reference, _ := asString(binding["ref"])
			head, pointer, _ := strings.Cut(reference, "#")
			if head == "input" {
				if !pointerIsDeclared(inputSchema, pointer) {
					problems.add("bad_pointer", at)
				}
				continue
			}
			target := strings.TrimPrefix(head, "node:")
			targetPosition, known := g.index[target]
			if !known {
				problems.add("unknown_ref", at)
				continue
			}
			targetSchema := nodes[targetPosition]["output_schema"]
			if !pointerIsDeclared(targetSchema, pointer) {
				problems.add("bad_pointer", at)
				continue
			}
			if target == id || !g.ancestors(id)[target] {
				problems.add("non_ancestor_ref", at)
			} else if !g.dom[id][target] {
				// ancestor on only one live path: the value is absent when that
				// path is skipped, so reject instead of inventing a null
				problems.add("missing_value", at)
			}
		}
	}

	// 5b: prompt templates may only read already bound inputs
	for position, node := range nodes {
		nodeType, _ := asString(node["type"])
		if nodeType != "model" {
			continue
		}
		config, _ := asObject(node["config"])
		template, _ := asString(config["prompt_template"])
		inputs, _ := asObject(node["inputs"])
		for _, variable := range templateVariables(template, rules) {
			if _, bound := inputs[variable]; !bound {
				problems.add("missing_value", fmt.Sprintf("/nodes/%d/config/prompt_template", position))
			}
		}
	}

	// 5c: every required application output must be mapped by end
	endPosition := g.index[g.end]
	outputSchema, _ := asObject(document["output_schema"])
	if required, present := outputSchema["required"]; present {
		names := stringArray(required)
		sort.Strings(names)
		for _, name := range names {
			inputs, _ := asObject(nodes[endPosition]["inputs"])
			if _, bound := inputs[name]; !bound {
				problems.add("missing_value",
					fmt.Sprintf("/nodes/%d/inputs/%s", endPosition, escapePointer(name)))
			}
		}
	}

	// 5d: condition cases compare already bound inputs with statically legal ops
	for position, node := range nodes {
		nodeType, _ := asString(node["type"])
		if nodeType != "condition" {
			continue
		}
		config, _ := asObject(node["config"])
		casesArray, _ := asArray(config["cases"])
		inputs, _ := asObject(node["inputs"])
		for casePosition, item := range casesArray {
			entry, _ := asObject(item)
			at := fmt.Sprintf("/nodes/%d/config/cases/%d", position, casePosition)
			input, _ := asString(entry["input"])
			bound, present := inputs[input]
			if !present {
				problems.add("missing_value", at)
				continue
			}
			op, _ := asString(entry["op"])
			if contains(rules.Condition.ValuelessOps, op) {
				continue
			}
			value, hasValue := entry["value"]
			if !hasValue {
				problems.add("missing_value", at)
				continue
			}
			if contains(rules.Condition.NumericOps, op) {
				if _, ok := asNumber(value); !ok {
					problems.add("type_mismatch", at)
					continue
				}
				binding, _ := asObject(bound)
				if literal, ok := binding["literal"]; ok {
					if _, ok := asNumber(literal); !ok {
						problems.add("type_mismatch", at)
					}
				}
			} else if contains(rules.Condition.ListOps, op) {
				if _, ok := asArray(value); !ok {
					problems.add("type_mismatch", at)
				}
			}
		}
	}
}

// ---------------------------------------------------------------------------
// normalisation and publication
// ---------------------------------------------------------------------------
var setValuedConfig = map[string][]string{
	"agent":    {"subapp_versions", "tool_versions"},
	"join":     {"optional_sources", "required_sources"},
	"retrieve": {"knowledge_base_ids"},
}

// Normalise returns the canonical normal form of a *validated* document:
// nodes ordered by id, every edge carrying its materialised port, set-valued
// config arrays ordered. Nothing else moves; no input binding is invented and
// no absent value becomes null.
func Normalise(document map[string]any, rules *Rules) (*NormalizedWorkflow, error) {
	normal := map[string]any{}
	for _, key := range rules.DocumentKeys {
		normal[key] = document[key]
	}
	nodesAny, _ := asArray(document["nodes"])
	nodes := make([]map[string]any, 0, len(nodesAny))
	for _, item := range nodesAny {
		node, _ := asObject(item)
		config, _ := asObject(node["config"])
		normalisedConfig := make(map[string]any, len(config))
		for key, value := range config {
			normalisedConfig[key] = value
		}
		for _, field := range setValuedConfig[typeOf(node)] {
			values := stringArray(config[field])
			sort.Strings(values)
			items := make([]any, len(values))
			for index, value := range values {
				items[index] = value
			}
			normalisedConfig[field] = items
		}
		nodes = append(nodes, map[string]any{
			"config":        normalisedConfig,
			"id":            node["id"],
			"inputs":        node["inputs"],
			"output_schema": node["output_schema"],
			"type":          node["type"],
		})
	}
	sort.SliceStable(nodes, func(i, j int) bool {
		left, _ := asString(nodes[i]["id"])
		right, _ := asString(nodes[j]["id"])
		return left < right
	})
	normal["nodes"] = mapSlice(nodes)

	edgesAny, _ := asArray(document["edges"])
	edges := make([]map[string]any, 0, len(edgesAny))
	for _, item := range edgesAny {
		edge, _ := asObject(item)
		port := "default"
		if explicit, present := edge["port"]; present {
			if text, ok := asString(explicit); ok {
				port = text
			}
		}
		source, _ := asString(edge["source"])
		target, _ := asString(edge["target"])
		edges = append(edges, map[string]any{
			"port":   port,
			"source": source,
			"target": target,
		})
	}
	sort.SliceStable(edges, func(i, j int) bool {
		leftSource, _ := asString(edges[i]["source"])
		rightSource, _ := asString(edges[j]["source"])
		if leftSource != rightSource {
			return leftSource < rightSource
		}
		leftTarget, _ := asString(edges[i]["target"])
		rightTarget, _ := asString(edges[j]["target"])
		if leftTarget != rightTarget {
			return leftTarget < rightTarget
		}
		leftPort, _ := asString(edges[i]["port"])
		rightPort, _ := asString(edges[j]["port"])
		return leftPort < rightPort
	})
	normal["edges"] = mapSlice(edges)

	canonical, err := Canonicalize(normal)
	if err != nil {
		return nil, err
	}
	payload := []byte(canonical)
	return &NormalizedWorkflow{Document: normal, Canonical: payload, SHA256: SHA256Hex(payload)}, nil
}

func typeOf(node map[string]any) string {
	value, _ := asString(node["type"])
	return value
}

func mapSlice(items []map[string]any) []any {
	out := make([]any, len(items))
	for index, item := range items {
		out[index] = item
	}
	return out
}

// BuildProvenance assembles the provenance that the artifact digest covers.
func BuildProvenance(rules *Rules, inputSHA256, dataSHA256, corpusSHA256, rulesSHA256 string) map[string]any {
	return map[string]any{
		"corpus_sha256": corpusSHA256,
		"data_sha256":   dataSHA256,
		"dsl_version":   rules.DSLVersion,
		"input_sha256":  inputSHA256,
		"probe":         rules.Probe,
		"probe_version": rules.ProbeVersion,
		"rules_sha256":  rulesSHA256,
		"schema_id":     rules.SchemaID,
	}
}

// Publish wraps data in the publishable descriptor. Provenance cannot touch data.
func Publish(data *NormalizedWorkflow, provenance map[string]any) (*WorkflowArtifact, error) {
	merged := make(map[string]any, len(provenance)+1)
	for key, value := range provenance {
		merged[key] = value
	}
	merged["data_sha256"] = data.SHA256
	descriptor := map[string]any{
		"data_sha256": data.SHA256,
		"dsl_version": merged["dsl_version"],
		"provenance":  merged,
		"schema_id":   merged["schema_id"],
	}
	canonical, err := Canonicalize(descriptor)
	if err != nil {
		return nil, err
	}
	payload := []byte(canonical)
	return &WorkflowArtifact{Descriptor: descriptor, Canonical: payload, SHA256: SHA256Hex(payload)}, nil
}

// ---------------------------------------------------------------------------
// validation entry point
// ---------------------------------------------------------------------------
type ValidationOptions struct {
	InputSHA256  string
	CorpusSHA256 string
	RulesSHA256  string
}

// Validate runs the five stages and, when the document is valid, normalises and
// publishes it.
func Validate(document any, rules *Rules, options ValidationOptions) (*ValidationResult, error) {
	inputSHA := options.InputSHA256
	if inputSHA == "" {
		canonical, err := Canonicalize(document)
		if err != nil {
			return nil, err
		}
		inputSHA = SHA256Hex([]byte(canonical))
	}
	problems := &problemList{}
	if !stageSchema(document, rules, problems) || !problems.empty() {
		return &ValidationResult{InputSHA256: inputSHA, Problems: problems.items}, nil
	}
	doc, _ := asObject(document)
	if !stageIdentity(doc, rules, problems) {
		return &ValidationResult{InputSHA256: inputSHA, Problems: problems.items}, nil
	}
	g := stageGraph(doc, rules, problems)
	if !problems.empty() || g == nil {
		return &ValidationResult{InputSHA256: inputSHA, Problems: problems.items}, nil
	}
	stageRegions(doc, rules, g, problems)
	if !problems.empty() {
		return &ValidationResult{InputSHA256: inputSHA, Problems: problems.items}, nil
	}
	stageReferences(doc, rules, g, problems)
	if !problems.empty() {
		return &ValidationResult{InputSHA256: inputSHA, Problems: problems.items}, nil
	}
	data, err := Normalise(doc, rules)
	if err != nil {
		return nil, err
	}
	provenance := BuildProvenance(rules, inputSHA, data.SHA256, options.CorpusSHA256, options.RulesSHA256)
	artifact, err := Publish(data, provenance)
	if err != nil {
		return nil, err
	}
	return &ValidationResult{
		InputSHA256: inputSHA,
		Problems:    nil,
		Data:        data,
		Artifact:    artifact,
	}, nil
}

// WireResult is the shared wire form. Absent data stays absent, never null.
func WireResult(result *ValidationResult, name string) map[string]any {
	out := map[string]any{}
	if name != "" {
		out["name"] = name
	}
	out["verdict"] = result.Verdict()
	out["input_sha256"] = result.InputSHA256
	if len(result.Problems) > 0 {
		out["reason"] = result.Reason()
		reasons := make([]any, 0, len(result.Reasons()))
		for _, code := range result.Reasons() {
			reasons = append(reasons, code)
		}
		problems := make([]any, 0, len(result.Problems))
		for _, problem := range result.Problems {
			problems = append(problems, map[string]any{"code": problem.Code, "pointer": problem.Pointer})
		}
		out["reasons"] = reasons
		out["problems"] = problems
		return out
	}
	out["data_sha256"] = result.Data.SHA256
	out["data_canonical"] = string(result.Data.Canonical)
	out["artifact_sha256"] = result.Artifact.SHA256
	return out
}

// ---------------------------------------------------------------------------
// corpus
// ---------------------------------------------------------------------------
type CorpusCase struct {
	Name              string
	Source            string
	Verdict           string
	Input             any
	InputSHA256       string
	ExpectedHash      string
	ExpectedCanonical string
	Reason            string
	Reasons           []string
}

type Corpus struct {
	Probe        string
	ProbeVersion string
	Cases        []CorpusCase
}

// CheckCorpusShape enforces the corpus invariants: every case pins a verdict
// and an input hash, invalid cases pin an ordered reason list, valid cases pin
// the normal-form hash, and null never stands in for "absent".
func CheckCorpusShape(payload any) []string {
	failures := []string{}
	corpus, ok := asObject(payload)
	if !ok {
		return []string{"corpus is not an object"}
	}
	for _, key := range sortedKeys(corpus) {
		if !contains([]string{"cases", "probe", "probe_version"}, key) {
			failures = append(failures, fmt.Sprintf("unexpected corpus key %q", key))
		}
	}
	for _, key := range []string{"cases", "probe", "probe_version"} {
		if _, present := corpus[key]; !present {
			failures = append(failures, fmt.Sprintf("missing corpus key %q", key))
		}
	}
	cases, ok := asArray(corpus["cases"])
	if !ok || len(cases) == 0 {
		return append(failures, "corpus has no cases")
	}
	names := map[string]bool{}
	for position, item := range cases {
		where := fmt.Sprintf("cases[%d]", position)
		entry, ok := asObject(item)
		if !ok {
			failures = append(failures, where+": not an object")
			continue
		}
		allowed := []string{"expected_canonical", "expected_hash", "input", "input_sha256",
			"name", "reason", "reasons", "source", "verdict"}
		for _, key := range sortedKeys(entry) {
			if !contains(allowed, key) {
				failures = append(failures, fmt.Sprintf("%s: unexpected key %q", where, key))
			}
		}
		for _, key := range []string{"name", "input", "verdict", "input_sha256"} {
			if _, present := entry[key]; !present {
				failures = append(failures, fmt.Sprintf("%s: missing key %q", where, key))
			}
		}
		for _, key := range sortedKeys(entry) {
			if entry[key] == nil {
				failures = append(failures, fmt.Sprintf("%s.%s: null is not a value here", where, key))
			}
		}
		name, nameOK := asString(entry["name"])
		if !nameOK || name == "" {
			failures = append(failures, where+": bad name")
		} else if names[name] {
			failures = append(failures, fmt.Sprintf("%s: duplicate name %q", where, name))
		} else {
			names[name] = true
		}
		verdict, _ := asString(entry["verdict"])
		switch verdict {
		case "valid":
			if _, present := entry["reason"]; present {
				failures = append(failures, where+": a valid case must not pin a reason")
			}
			if _, present := entry["reasons"]; present {
				failures = append(failures, where+": a valid case must not pin a reason")
			}
			hash, ok := asString(entry["expected_hash"])
			if !ok || hash == "" {
				failures = append(failures, where+": no expected_hash")
			}
		case "invalid":
			if _, present := entry["expected_hash"]; present {
				failures = append(failures, where+": an invalid case has no normal form to hash")
			}
			reason, ok := asString(entry["reason"])
			if !ok || reason == "" {
				failures = append(failures, where+": no reason")
			}
			reasons, ok := asArray(entry["reasons"])
			if !ok || len(reasons) == 0 {
				failures = append(failures, where+": reasons must start with reason")
			} else if first, _ := asString(reasons[0]); first != reason {
				failures = append(failures, where+": reasons must start with reason")
			}
		default:
			failures = append(failures, where+": verdict must be valid or invalid")
		}
	}
	return failures
}

// ParseCorpus converts a shape-checked payload into typed cases.
func ParseCorpus(payload any) (*Corpus, error) {
	corpus, ok := asObject(payload)
	if !ok {
		return nil, errors.New("corpus is not an object")
	}
	parsed := &Corpus{}
	parsed.Probe, _ = asString(corpus["probe"])
	parsed.ProbeVersion, _ = asString(corpus["probe_version"])
	cases, _ := asArray(corpus["cases"])
	for _, item := range cases {
		entry, _ := asObject(item)
		record := CorpusCase{}
		record.Name, _ = asString(entry["name"])
		record.Source, _ = asString(entry["source"])
		record.Verdict, _ = asString(entry["verdict"])
		record.Input = entry["input"]
		record.InputSHA256, _ = asString(entry["input_sha256"])
		record.ExpectedHash, _ = asString(entry["expected_hash"])
		record.ExpectedCanonical, _ = asString(entry["expected_canonical"])
		record.Reason, _ = asString(entry["reason"])
		record.Reasons = stringArray(entry["reasons"])
		parsed.Cases = append(parsed.Cases, record)
	}
	return parsed, nil
}

// RunCorpus validates every case and returns the shared wire envelope.
func RunCorpus(corpus *Corpus, corpusSHA256 string, rules *Rules) (map[string]any, error) {
	results := make([]any, 0, len(corpus.Cases))
	for _, entry := range corpus.Cases {
		result, err := Validate(entry.Input, rules, ValidationOptions{
			InputSHA256:  entry.InputSHA256,
			CorpusSHA256: corpusSHA256,
			RulesSHA256:  rules.sha256,
		})
		if err != nil {
			return nil, fmt.Errorf("case %s: %w", entry.Name, err)
		}
		results = append(results, WireResult(result, entry.Name))
	}
	return map[string]any{
		"cases":         results,
		"corpus_sha256": corpusSHA256,
		"probe_version": rules.ProbeVersion,
		"rules_sha256":  rules.sha256,
	}, nil
}

// CheckExpectations compares results with the pinned expectations.
func CheckExpectations(corpus *Corpus, output map[string]any) []string {
	failures := []string{}
	byName := map[string]*CorpusCase{}
	for index := range corpus.Cases {
		byName[corpus.Cases[index].Name] = &corpus.Cases[index]
	}
	results, _ := asArray(output["cases"])
	for _, item := range results {
		entry, _ := asObject(item)
		name, _ := asString(entry["name"])
		expected := byName[name]
		if expected == nil {
			failures = append(failures, name+": result for an unknown case")
			continue
		}
		verdict, _ := asString(entry["verdict"])
		if verdict != expected.Verdict {
			failures = append(failures, fmt.Sprintf("%s: verdict %s != %s", name, verdict, expected.Verdict))
			continue
		}
		if inputSHA, _ := asString(entry["input_sha256"]); inputSHA != expected.InputSHA256 {
			failures = append(failures, name+": input hash differs from the pinned value")
		}
		if expected.Verdict == "valid" {
			if dataSHA, _ := asString(entry["data_sha256"]); dataSHA != expected.ExpectedHash {
				failures = append(failures, name+": normal-form hash differs from the pinned value")
			}
			if expected.ExpectedCanonical != "" {
				if text, _ := asString(entry["data_canonical"]); text != expected.ExpectedCanonical {
					failures = append(failures, name+": normal form differs from the pinned text")
				}
			}
			continue
		}
		if reason, _ := asString(entry["reason"]); reason != expected.Reason {
			failures = append(failures, fmt.Sprintf("%s: reason %s != %s", name, reason, expected.Reason))
		}
		reasons := stringArray(entry["reasons"])
		if !equalStrings(reasons, expected.Reasons) {
			failures = append(failures, fmt.Sprintf("%s: reasons %v != %v", name, reasons, expected.Reasons))
		}
	}
	return failures
}

// ResolveRulesPath mirrors the Python default: rules.json next to the corpus.
func ResolveRulesPath(corpusPath, explicit string) string {
	if explicit != "" {
		return explicit
	}
	return filepath.Join(filepath.Dir(corpusPath), "rules.json")
}
