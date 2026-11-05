/*
 * F04B/T088 canvas pure-module tests, executed by Node from pytest.
 *
 * The retrieve panel is authored from the same pure modules as the rest of the
 * canvas: the palette/default config live in dsl.js, the legal query/output
 * sources in sources.js and the export allowlist in export.js. These tests pin
 * them against a concrete start -> retrieve -> end definition; the browser panel
 * wiring is covered by the committed-bundle marker test.
 *
 * Run with:
 *   node tests/f04b/canvas_t088.test.mjs
 */

import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import {
  NODE_LABELS,
  NODE_TYPES,
  REPEATABLE_NODE_TYPES,
  addNode,
  defaultDefinition,
  defaultNode,
  definitionToGraph,
  graphToDefinition,
  retrieveOutputSchema,
} from '../../apps/web/canvas/src/lib/dsl.js';
import {
  isLegalSource,
  legalSources,
  sourceType,
} from '../../apps/web/canvas/src/lib/sources.js';
import { CONFIG_ALLOWLIST, exportPayload } from '../../apps/web/canvas/src/lib/export.js';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const PRODUCT = path.resolve(HERE, '..', '..');

let checks = 0;
function test(name, body) {
  try {
    body();
    checks += 1;
    console.log('ok ' + checks + ' - ' + name);
  } catch (error) {
    console.error('not ok - ' + name);
    throw error;
  }
}

function byId(definition, id) {
  return definition.nodes.find((node) => node.id === id);
}

/** A definition the retrieve panel authors. */
function retrieveDefinition() {
  const retrieve = defaultNode('retrieve', 'search');
  retrieve.config = {
    knowledge_base_ids: ['aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'],
    top_k: 3,
  };
  retrieve.inputs = { query: { ref: 'input#/question' } };
  retrieve.output_schema = retrieveOutputSchema();
  return {
    dsl_version: '1.0',
    input_schema: {
      type: 'object',
      properties: { question: { type: 'string' } },
      required: ['question'],
      additionalProperties: false,
    },
    output_schema: { type: 'object' },
    nodes: [
      { id: 'start', type: 'start', inputs: {}, config: {}, output_schema: { type: 'object' } },
      retrieve,
      {
        id: 'end',
        type: 'end',
        inputs: {
          citations: { ref: 'node:search#/citations' },
          items: { ref: 'node:search#/items' },
          count: { ref: 'node:search#/count' },
        },
        config: {},
        output_schema: { type: 'object' },
      },
    ],
    edges: [
      { source: 'start', target: 'search' },
      { source: 'search', target: 'end' },
    ],
    policy: defaultDefinition().policy,
  };
}

test('palette offers the retrieve node with its contract default config', () => {
  assert.ok(NODE_TYPES.includes('retrieve'), NODE_TYPES);
  assert.equal(NODE_LABELS.retrieve, '知识检索');
  assert.ok(REPEATABLE_NODE_TYPES.includes('retrieve'), REPEATABLE_NODE_TYPES);

  const node = defaultNode('retrieve', 'search');
  assert.deepEqual(node.config, { knowledge_base_ids: [], top_k: 5 });
  assert.deepEqual(node.inputs, {});

  const graph = definitionToGraph(retrieveDefinition(), null);
  const first = addNode(graph.nodes, graph.edges, 'retrieve', {});
  assert.equal(first.ok, true);
  assert.notEqual(first.node.id, 'search');
  const second = addNode([...graph.nodes, first.node], graph.edges, 'retrieve', {});
  assert.equal(second.ok, true, 'retrieve nodes are repeatable');
  assert.deepEqual(second.node.data.config, { knowledge_base_ids: [], top_k: 5 });
});

test('the retrieve output template declares items/citations/count', () => {
  const schema = retrieveOutputSchema();
  assert.equal(schema.type, 'object');
  assert.deepEqual(Object.keys(schema.properties).sort(), ['citations', 'count', 'items']);
  assert.deepEqual(schema.required.sort(), ['citations', 'count', 'items']);
  assert.equal(schema.properties.items.type, 'array');
  assert.equal(schema.properties.items.items.type, 'object');
  assert.equal(schema.properties.citations.type, 'array');
  const item = schema.properties.items.items.properties;
  for (const key of [
    'chunk_id', 'document_id', 'document_revision', 'document_state', 'chunk_no',
    'span', 'excerpt', 'citation',
  ]) {
    assert.ok(item[key], 'items item is missing ' + key);
  }
  assert.ok(!item.text && !item.score, 'the template must not offer the full body or the vector score');
  const citation = schema.properties.citations.items.properties;
  for (const key of ['chunk_id', 'document_id', 'document_revision', 'span', 'excerpt']) {
    assert.ok(citation[key], 'citation item is missing ' + key);
  }
  assert.ok(!citation.text, 'the citations projection must carry an excerpt, never the full body');

  const definition = retrieveDefinition();
  const endRefs = legalSources(definition, 'end').map((entry) => entry.ref);
  assert.ok(endRefs.includes('node:search#/citations'), endRefs);
  assert.ok(endRefs.includes('node:search#/items'), endRefs);
  assert.ok(endRefs.includes('node:search#/count'), endRefs);
  assert.equal(sourceType(definition, 'node:search#/citations'), 'array<object>');
  assert.equal(sourceType(definition, 'node:search#/items'), 'array<object>');
  assert.equal(sourceType(definition, 'node:search#/count'), 'integer');
  assert.ok(isLegalSource(definition, 'end', 'node:search#/citations'));
});

test('the query is a legal string input binding, never config', () => {
  const definition = retrieveDefinition();
  const searchRefs = legalSources(definition, 'search').map((entry) => entry.ref);
  assert.ok(searchRefs.includes('input#/question'), searchRefs);
  assert.equal(sourceType(definition, 'input#/question'), 'string');
  assert.ok(isLegalSource(definition, 'search', 'input#/question'));
  assert.equal(byId(definition, 'search').config.query, undefined);
});

test('a retrieve node survives the graph -> definition round trip', () => {
  const definition = retrieveDefinition();
  const graph = definitionToGraph(definition, null);
  const rebuilt = graphToDefinition(graph.nodes, graph.edges, definition);
  assert.deepEqual(byId(rebuilt, 'search'), byId(definition, 'search'));
  assert.deepEqual(rebuilt.edges, definition.edges);
});

test('the export allowlist keeps retrieve config and drops an inline query', () => {
  assert.deepEqual(
    [...(CONFIG_ALLOWLIST.retrieve || [])].sort(),
    ['knowledge_base_ids', 'top_k'],
  );
  const definition = retrieveDefinition();
  definition.nodes[1].config.query = 'inline';
  definition.nodes[1].config.cache = true;
  const payload = exportPayload(definition, {});
  assert.deepEqual(payload.definition.nodes[1].config, {
    knowledge_base_ids: ['aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'],
    top_k: 3,
  });
  const paths = (payload.dropped || []).map((entry) => entry.path);
  assert.ok(paths.includes('/nodes/1/config/query'), paths);
  assert.ok(paths.includes('/nodes/1/config/cache'), paths);
  assert.ok(
    (payload.dropped || []).every((entry) => entry.reason !== 'unknown_node_type'),
    payload.dropped,
  );
});

test('the committed bundle ships the retrieve panel', () => {
  const bundle = fs.readFileSync(path.join(PRODUCT, 'apps', 'web', 'src', 'assets', 'canvas.js'), 'utf8');
  for (const marker of [
    '知识检索',
    'config.knowledge_base_ids',
    'config.top_k',
    'knowledge-bases?limit=100',
    'output_schema.template',
    'retrieveOutputSchema',
  ]) {
    assert.ok(bundle.includes(marker), 'the bundle is missing ' + marker);
  }
});

console.log('PASS ' + checks + ' canvas T088 module tests');
