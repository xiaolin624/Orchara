package presetexec

// F04B/T088: the retrieve node on the graph path.
//
// The executor never talks to the knowledge service directly: it asks the API
// through the new internal retrieval route (the Client seam here), and the server
// applies each knowledge base's ACL before ranking. These unit tests pin the
// node's config/binding refusals, the citation-preserving output shape, the
// no-result success and the failure codes. The real PostgreSQL + API + real
// knowledge ingestion evidence lives in tests/f04b/test_t088_retrieve.py.

import (
	"context"
	"encoding/json"
	"errors"
	"strconv"
	"strings"
	"testing"
)

// retrieveDefinition is the T088 acceptance shape:
//
//	start -> search(retrieve) -> end
//
// The end node consumes only the citations/count projection, so the test proves
// the node output reaches a downstream step unchanged.
const retrieveDefinition = `{
  "dsl_version": "1.0",
  "input_schema": {"type":"object","properties":{"question":{"type":"string"}},
                   "required":["question"],"additionalProperties":false},
  "output_schema": {"type":"object"},
  "nodes": [
    {"id":"start","type":"start","inputs":{},"config":{},"output_schema":{"type":"object"}},
    {"id":"search","type":"retrieve",
     "inputs":{"query":{"ref":"input#/question"}},
     "config":{"knowledge_base_ids":["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"],"top_k":3},
     "output_schema":{"type":"object",
       "properties":{"items":{"type":"array"},"citations":{"type":"array"},"count":{"type":"integer"}},
       "required":["items","citations","count"],"additionalProperties":false}},
    {"id":"end","type":"end",
     "inputs":{"citations":{"ref":"node:search#/citations"},"count":{"ref":"node:search#/count"}},
     "config":{},"output_schema":{"type":"object"}}
  ],
  "edges": [{"source":"start","target":"search"},{"source":"search","target":"end"}],
  "policy": {"max_active_seconds":900,"max_child_depth":0,"max_child_calls":0,
             "budget_limit":"1.00","currency":"USD"}
}`

// parallelRetrieveDefinition runs two retrieve nodes as two branches of one fork
// and merges them at one join:
//
//	start -> left_search  \
//	      -> right_search  -> join -> end
const parallelRetrieveDefinition = `{
  "dsl_version": "1.0",
  "input_schema": {"type":"object"},
  "output_schema": {"type":"object"},
  "nodes": [
    {"id":"start","type":"start","inputs":{},"config":{},"output_schema":{"type":"object"}},
    {"id":"left_search","type":"retrieve",
     "inputs":{"query":{"ref":"input#/left"}},
     "config":{"knowledge_base_ids":["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"],"top_k":2},
     "output_schema":{"type":"object"}},
    {"id":"right_search","type":"retrieve",
     "inputs":{"query":{"ref":"input#/right"}},
     "config":{"knowledge_base_ids":["bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"],"top_k":2},
     "output_schema":{"type":"object"}},
    {"id":"join","type":"join","inputs":{},
     "config":{"fork_id":"start","mode":"all_active",
               "required_sources":["left_search","right_search"],"optional_sources":[]},
     "output_schema":{"type":"object"}},
    {"id":"end","type":"end","inputs":{"results":{"ref":"node:join#/results"}},
     "config":{},"output_schema":{"type":"object"}}
  ],
  "edges": [
    {"source":"start","target":"left_search"},
    {"source":"start","target":"right_search"},
    {"source":"left_search","target":"join"},
    {"source":"right_search","target":"join"},
    {"source":"join","target":"end"}
  ],
  "policy": {"max_active_seconds":900,"max_child_depth":0,"max_child_calls":0,
             "budget_limit":"1.00","currency":"USD"}
}`

const retrieveSpan = `{"start_char":0,"end_char":5,"start_line":1,"end_line":1}`

func retrieveCitation(documentID string, revision int64) []byte {
	return []byte(`{"document_id":"` + documentID + `","document_revision":` +
		strconv.FormatInt(revision, 10) +
		`,"chunk_no":0,"span":` + retrieveSpan +
		`,"offset_unit":"utf8","source_artifact_id":"11111111-1111-4111-8111-111111111111",` +
		`"source_sha256":"` + strings.Repeat("ab", 32) + `","source_media_type":"text/plain",` +
		`"text_normalization":"none","parser_version":"text/paragraph/1",` +
		`"chunker_version":"text/paragraph/1","embedding_model_revision":"dev-stub"}`)
}

func retrievedItem(chunkID, documentID, text string) RetrievedItem {
	return RetrievedItem{
		ChunkID:          chunkID,
		DocumentID:       documentID,
		DocumentRevision: 2,
		DocumentState:    "ready",
		ChunkNo:          0,
		Span:             json.RawMessage(retrieveSpan),
		Text:             text,
		Score:            0.75,
		Citation:         retrieveCitation(documentID, 2),
	}
}

// scriptedRetrieve installs the Client's retrieval seam on the fake client.
func scriptedRetrieve(
	client *fakeClient, result *RetrieveResult, err error,
) {
	client.retrieve = func(RetrieveRequest) (*RetrieveResult, error) {
		return result, err
	}
}

// codedRetrieveError mirrors the supervisor client's internal API error: the
// error code is the server's stable envelope code.
type codedRetrieveError struct{ code string }

func (e codedRetrieveError) Error() string     { return "internal API refusal: " + e.code }
func (e codedRetrieveError) ErrorCode() string { return e.code }

// ---------------------------------------------------------------------------
// plan: config revalidation
// ---------------------------------------------------------------------------

func TestParseRetrieveConfigAcceptsTheContractShape(t *testing.T) {
	config, err := parseRetrieveConfig(json.RawMessage(
		`{"knowledge_base_ids":["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",` +
			`"bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"],"top_k":5}`))
	if err != nil {
		t.Fatalf("parseRetrieveConfig: %v", err)
	}
	if config.TopK != 5 || len(config.KnowledgeBaseIDs) != 2 {
		t.Fatalf("config = %+v", config)
	}
}

func TestParseRetrieveConfigRejectsBadConfigs(t *testing.T) {
	cases := map[string]string{
		"no config":        `null`,
		"no base ids":      `{"top_k":5}`,
		"empty base ids":   `{"knowledge_base_ids":[],"top_k":5}`,
		"duplicate base":   `{"knowledge_base_ids":["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa","aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"],"top_k":5}`,
		"bad uuid":         `{"knowledge_base_ids":["not-a-uuid"],"top_k":5}`,
		"top_k zero":       `{"knowledge_base_ids":["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"],"top_k":0}`,
		"top_k too large":  `{"knowledge_base_ids":["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"],"top_k":21}`,
		"query in config":  `{"knowledge_base_ids":["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"],"top_k":5,"query":"inline"}`,
		"unknown extra":    `{"knowledge_base_ids":["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"],"top_k":5,"cache":true}`,
		"not an object":    `[]`,
		"top_k not number": `{"knowledge_base_ids":["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"],"top_k":"5"}`,
	}
	for name, raw := range cases {
		if _, err := parseRetrieveConfig(json.RawMessage(raw)); err == nil {
			t.Fatalf("%s: config was accepted", name)
		}
	}
}

func TestBuildGraphPlanAcceptsRetrieveNodes(t *testing.T) {
	plan := mustGraphPlan(t, retrieveDefinition)
	node := plan.nodes["search"]
	if node == nil || node.retrieve == nil {
		t.Fatalf("the retrieve node was not parsed: %+v", node)
	}
	if node.retrieve.TopK != 3 || len(node.retrieve.KnowledgeBaseIDs) != 1 {
		t.Fatalf("retrieve config = %+v", node.retrieve)
	}
	if !hasGraphConstructs(definitionDocument(t, retrieveDefinition)) {
		t.Fatal("a retrieve definition must select the graph path")
	}
}

func TestBuildGraphPlanRejectsABadRetrieveConfig(t *testing.T) {
	document := definitionDocument(t, retrieveDefinition)
	for index := range document.Definition.Nodes {
		if document.Definition.Nodes[index].ID == "search" {
			document.Definition.Nodes[index].Config =
				json.RawMessage(`{"knowledge_base_ids":[],"top_k":3}`)
		}
	}
	_, err := buildGraphPlan(document)
	if err == nil {
		t.Fatal("an empty knowledge_base_ids list must be refused")
	}
	if code := planErrorCode(t, err); code != CodeBadConfig {
		t.Fatalf("code = %q, want bad_config (%v)", code, err)
	}
}

// ---------------------------------------------------------------------------
// execution: citations, no result, failures
// ---------------------------------------------------------------------------

func TestGraphRunRetrievePublishesCitationsAndPassesThemDownstream(t *testing.T) {
	document := definitionDocument(t, retrieveDefinition)
	lease, manifest := graphLease(t, document)
	client := graphClient(t, lease, manifest, `{"question":"north sales"}`)
	scriptedRetrieve(client, &RetrieveResult{
		Items:      []RetrievedItem{retrievedItem("chunk-1", "document-1", "north 1234")},
		Requested:  1,
		Authorized: 1,
	}, nil)

	report := Run(context.Background(),
		baseDeps(t, client, &fakeInputs{inputs: client.inputs}, successSandbox()))
	if report.Outcome != "succeeded" || report.ErrorCode != "" {
		t.Fatalf("report = %+v", report)
	}
	if len(client.retrieveRequests) != 1 {
		t.Fatalf("retrieval calls = %+v", client.retrieveRequests)
	}
	request := client.retrieveRequests[0]
	if request.Query != "north sales" || request.TopK != 3 ||
		len(request.KnowledgeBaseIDs) != 1 ||
		request.KnowledgeBaseIDs[0] != "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa" {
		t.Fatalf("retrieval request = %+v", request)
	}

	output := artifactData(t, client, "search")
	if len(output) != 3 {
		t.Fatalf("output keys = %v", keysOf(output))
	}
	items, _ := output["items"].([]any)
	if len(items) != 1 {
		t.Fatalf("items = %v", output["items"])
	}
	item, _ := items[0].(map[string]any)
	wantItemKeys := []string{
		"chunk_id", "chunk_no", "citation", "document_id", "document_revision",
		"document_state", "excerpt", "span",
	}
	if got := sortedStringKeys(item); strings.Join(got, ",") != strings.Join(wantItemKeys, ",") {
		t.Fatalf("item keys = %v", got)
	}
	if item["document_id"] != "document-1" || item["chunk_id"] != "chunk-1" ||
		item["document_revision"] != float64(2) || item["excerpt"] != "north 1234" {
		t.Fatalf("item = %v", item)
	}
	span, _ := item["span"].(map[string]any)
	if span["start_line"] != float64(1) || span["end_char"] != float64(5) {
		t.Fatalf("span = %v", span)
	}
	citation, _ := item["citation"].(map[string]any)
	if citation["document_id"] != "document-1" ||
		citation["document_revision"] != float64(2) {
		t.Fatalf("citation = %v", citation)
	}

	citations, _ := output["citations"].([]any)
	if len(citations) != 1 {
		t.Fatalf("citations = %v", output["citations"])
	}
	projection, _ := citations[0].(map[string]any)
	wantCitationKeys := []string{"chunk_id", "document_id", "document_revision", "excerpt", "span"}
	if got := sortedStringKeys(projection); strings.Join(got, ",") != strings.Join(wantCitationKeys, ",") {
		t.Fatalf("citation projection keys = %v", got)
	}
	if projection["excerpt"] != "north 1234" {
		t.Fatalf("citation projection = %v", projection)
	}
	if projection["chunk_id"] != "chunk-1" || projection["document_id"] != "document-1" ||
		projection["document_revision"] != float64(2) {
		t.Fatalf("citation projection = %v", projection)
	}
	if output["count"] != float64(1) {
		t.Fatalf("count = %v", output["count"])
	}

	// The node is a data result labeled `results`; the run sealed the end output.
	rows := nodeResultRows(client, "search")
	if len(rows) != 1 || rows[0].Kind != "data" || rows[0].Label != "results" {
		t.Fatalf("node result rows = %+v", rows)
	}
	states := observedNodeStates(client)
	if got := strings.Join(states["search"], ","); got != "pending,running,succeeded" {
		t.Fatalf("search states = %v", states["search"])
	}
	if len(client.proposals) != 1 || client.proposals[0].SuggestedState != "succeeded" {
		t.Fatalf("proposals = %+v", client.proposals)
	}
}

func TestRetrieveOutputExcerptsLongChunksWithoutTheVectorScore(t *testing.T) {
	body := strings.Repeat("华", maxRetrieveExcerptRunes) + "被删掉的尾部"
	output, err := retrieveOutputDocument([]RetrievedItem{
		retrievedItem("chunk-long", "document-1", body),
	})
	if err != nil {
		t.Fatalf("retrieveOutputDocument: %v", err)
	}
	items, _ := output["items"].([]any)
	item, _ := items[0].(map[string]any)
	if _, present := item["score"]; present {
		t.Fatalf("the node output must not expose the vector score: %v", item)
	}
	if _, present := item["text"]; present {
		t.Fatalf("the node output must not expose the full chunk body: %v", item)
	}
	want := strings.Repeat("华", maxRetrieveExcerptRunes)
	if item["excerpt"] != want {
		t.Fatalf("excerpt = %q (%d runes), want %d runes",
			item["excerpt"], len([]rune(item["excerpt"].(string))), maxRetrieveExcerptRunes)
	}
	citations, _ := output["citations"].([]any)
	projection, _ := citations[0].(map[string]any)
	if projection["excerpt"] != want {
		t.Fatalf("citation excerpt = %v", projection)
	}
	if output["count"] != float64(1) {
		t.Fatalf("count = %v", output["count"])
	}
}

func TestGraphRunRetrieveWithoutResultsStillSucceeds(t *testing.T) {
	document := definitionDocument(t, retrieveDefinition)
	lease, manifest := graphLease(t, document)
	client := graphClient(t, lease, manifest, `{"question":"unrelated"}`)
	scriptedRetrieve(client, &RetrieveResult{Items: nil, Requested: 1, Authorized: 1}, nil)

	report := Run(context.Background(),
		baseDeps(t, client, &fakeInputs{inputs: client.inputs}, successSandbox()))
	if report.Outcome != "succeeded" || report.ErrorCode != "" {
		t.Fatalf("an empty retrieval must not fail the run: %+v", report)
	}
	output := artifactData(t, client, "search")
	items, ok := output["items"].([]any)
	if !ok || len(items) != 0 {
		t.Fatalf("items = %v (want an empty array, never null)", output["items"])
	}
	citations, ok := output["citations"].([]any)
	if !ok || len(citations) != 0 {
		t.Fatalf("citations = %v (want an empty array, never null)", output["citations"])
	}
	if output["count"] != float64(0) {
		t.Fatalf("count = %v", output["count"])
	}
}

func TestGraphRunRetrieveMissingQueryIsALocatedNodeInputFailure(t *testing.T) {
	definition := strings.Replace(retrieveDefinition,
		`"inputs":{"query":{"ref":"input#/question"}}`, `"inputs":{}`, 1)
	definition = strings.Replace(definition,
		`"input_schema": {"type":"object","properties":{"question":{"type":"string"}},
                   "required":["question"],"additionalProperties":false},`,
		`"input_schema": {"type":"object"},`, 1)
	document := definitionDocument(t, definition)
	lease, manifest := graphLease(t, document)
	client := graphClient(t, lease, manifest, `{}`)
	scriptedRetrieve(client, &RetrieveResult{}, nil)

	report := Run(context.Background(),
		baseDeps(t, client, &fakeInputs{inputs: client.inputs}, successSandbox()))
	if report.Outcome != "refused" || report.ErrorCode != CodeNodeInputInvalid {
		t.Fatalf("report = %+v", report)
	}
	if !strings.Contains(report.Error, "/nodes/1/inputs/query") {
		t.Fatalf("the refusal must locate /nodes/1/inputs/query: %q", report.Error)
	}
	if len(client.retrieveRequests) != 0 {
		t.Fatalf("a missing query must not call retrieval: %+v", client.retrieveRequests)
	}
	states := observedNodeStates(client)
	if got := strings.Join(states["search"], ","); got != "pending,running,failed" {
		t.Fatalf("search states = %v", states["search"])
	}
}

func TestGraphRunRetrieveNonStringQueryIsNodeInputInvalid(t *testing.T) {
	definition := strings.Replace(retrieveDefinition,
		`{"type":"object","properties":{"question":{"type":"string"}},
                   "required":["question"],"additionalProperties":false}`,
		`{"type":"object","properties":{"question":{"type":"boolean"}},"additionalProperties":false}`, 1)
	document := definitionDocument(t, definition)
	lease, manifest := graphLease(t, document)
	client := graphClient(t, lease, manifest, `{"question":true}`)
	scriptedRetrieve(client, &RetrieveResult{}, nil)

	report := Run(context.Background(),
		baseDeps(t, client, &fakeInputs{inputs: client.inputs}, successSandbox()))
	if report.Outcome != "refused" || report.ErrorCode != CodeNodeInputInvalid {
		t.Fatalf("report = %+v", report)
	}
	if !strings.Contains(report.Error, "/nodes/1/inputs/query") {
		t.Fatalf("the refusal must locate the query binding: %q", report.Error)
	}
}

func TestGraphRunRetrieveServerFailuresUseTheStableCodes(t *testing.T) {
	cases := map[string]struct {
		err      error
		wantCode string
	}{
		"network": {err: errors.New("connection refused"), wantCode: "retrieve_unavailable"},
		"503":     {err: codedRetrieveError{code: "provider_not_configured"}, wantCode: "retrieve_unavailable"},
		"403":     {err: codedRetrieveError{code: "forbidden"}, wantCode: "retrieve_denied"},
	}
	for name, testCase := range cases {
		document := definitionDocument(t, retrieveDefinition)
		lease, manifest := graphLease(t, document)
		client := graphClient(t, lease, manifest, `{"question":"north"}`)
		scriptedRetrieve(client, nil, testCase.err)
		report := Run(context.Background(),
			baseDeps(t, client, &fakeInputs{inputs: client.inputs}, successSandbox()))
		if report.Outcome != "refused" || report.ErrorCode != testCase.wantCode {
			t.Fatalf("%s: report = %+v, want %s", name, report, testCase.wantCode)
		}
		if len(client.proposals) != 1 || client.proposals[0].SuggestedState != "failed" {
			t.Fatalf("%s: proposals = %+v", name, client.proposals)
		}
		states := observedNodeStates(client)
		if got := strings.Join(states["search"], ","); got != "pending,running,failed" {
			t.Fatalf("%s: search states = %v", name, states["search"])
		}
	}
}

func TestGraphRunRetrieveValidatesItsOutputSchema(t *testing.T) {
	definition := strings.Replace(retrieveDefinition,
		`"required":["items","citations","count"]`,
		`"required":["items","citations","count","nope"]`, 1)
	document := definitionDocument(t, definition)
	lease, manifest := graphLease(t, document)
	client := graphClient(t, lease, manifest, `{"question":"north"}`)
	scriptedRetrieve(client, &RetrieveResult{
		Items: []RetrievedItem{retrievedItem("chunk-1", "document-1", "north")},
	}, nil)
	report := Run(context.Background(),
		baseDeps(t, client, &fakeInputs{inputs: client.inputs}, successSandbox()))
	if report.Outcome != "refused" || report.ErrorCode != CodeNodeOutputInvalid {
		t.Fatalf("report = %+v", report)
	}
	if len(nodeResultRows(client, "search")) != 0 {
		t.Fatalf("an invalid node output must not be reported as a result: %+v", client.nodeResults)
	}
}

// ---------------------------------------------------------------------------
// concurrency: two retrieve nodes never share results
// ---------------------------------------------------------------------------

func TestGraphRunTwoRetrieveNodesEachKeepTheirOwnResults(t *testing.T) {
	document := definitionDocument(t, parallelRetrieveDefinition)
	lease, manifest := graphLease(t, document)
	client := graphClient(t, lease, manifest, `{"left":"left question","right":"right question"}`)
	client.retrieve = func(request RetrieveRequest) (*RetrieveResult, error) {
		chunkID, documentID := "chunk-left", "document-left"
		if request.Query == "right question" {
			chunkID, documentID = "chunk-right", "document-right"
		}
		return &RetrieveResult{
			Items:     []RetrievedItem{retrievedItem(chunkID, documentID, request.Query)},
			Requested: 1, Authorized: 1,
		}, nil
	}

	report := Run(context.Background(),
		baseDeps(t, client, &fakeInputs{inputs: client.inputs}, successSandbox()))
	if report.Outcome != "succeeded" || report.ErrorCode != "" {
		t.Fatalf("report = %+v", report)
	}
	if len(client.retrieveRequests) != 2 {
		t.Fatalf("retrieval calls = %+v", client.retrieveRequests)
	}
	joined := artifactData(t, client, "join")["results"].(map[string]any)
	left := joined["left_search"].(map[string]any)["value"].(map[string]any)
	right := joined["right_search"].(map[string]any)["value"].(map[string]any)
	if left["items"].([]any)[0].(map[string]any)["chunk_id"] != "chunk-left" {
		t.Fatalf("left branch picked up the other result: %v", left)
	}
	if right["items"].([]any)[0].(map[string]any)["chunk_id"] != "chunk-right" {
		t.Fatalf("right branch picked up the other result: %v", right)
	}
	if left["citations"].([]any)[0].(map[string]any)["document_id"] != "document-left" {
		t.Fatalf("left citations = %v", left["citations"])
	}
	if right["citations"].([]any)[0].(map[string]any)["document_id"] != "document-right" {
		t.Fatalf("right citations = %v", right["citations"])
	}
}

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------

func keysOf(document map[string]any) []string {
	return sortedStringKeys(document)
}

func sortedStringKeys(document map[string]any) []string {
	keys := make([]string, 0, len(document))
	for key := range document {
		keys = append(keys, key)
	}
	for left := 0; left < len(keys); left++ {
		for right := left + 1; right < len(keys); right++ {
			if keys[right] < keys[left] {
				keys[left], keys[right] = keys[right], keys[left]
			}
		}
	}
	return keys
}
