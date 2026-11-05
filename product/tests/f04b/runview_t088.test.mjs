/*
 * F04B/T088 run-view pure-module tests for the retrieve node's citations.
 *
 * `apps/web/src/assets/runview.js` is the classic script the debug page loads; it
 * is read as bytes and executed in a `vm` context, so these assertions run the
 * real shipped source. The data shape is the documented non-contract retrieve
 * output (`reports/f04b/T088-retrieve-node.md`): items + a citations projection
 * whose chunk ids open the real `GET /api/v1/knowledge-citations/{chunk_id}` route
 * (404 after revocation must say "unavailable", never show cached text).
 *
 * Run with:
 *   node tests/f04b/runview_t088.test.mjs
 */

import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const PRODUCT = path.resolve(HERE, '..', '..');
const RUNVIEW_SOURCE = path.join(PRODUCT, 'apps', 'web', 'src', 'assets', 'runview.js');

/** Cross-realm plain copy: objects built inside the vm have another prototype. */
function plain(value) {
  return value === undefined ? undefined : JSON.parse(JSON.stringify(value));
}

class El {
  constructor(tag) {
    this.tagName = String(tag || 'div').toUpperCase();
    this.children = [];
    this._listeners = {};
    this.className = '';
    this.textContent = '';
    this.hidden = false;
    this.disabled = false;
    this.dataset = {};
    this.attrs = {};
    this.parentNode = null;
  }

  appendChild(child) {
    child.parentNode = this;
    this.children.push(child);
    return child;
  }

  append(...nodes) {
    for (const node of nodes) this.appendChild(node);
  }

  replaceChildren(...nodes) {
    this.children = [];
    for (const node of nodes) this.appendChild(node);
  }

  addEventListener(type, fn) {
    (this._listeners[type] = this._listeners[type] || []).push(fn);
  }

  setAttribute(name, value) {
    this.attrs[name] = String(value);
  }

  click() {
    for (const fn of this._listeners.click || []) fn({ preventDefault() {} });
  }
}

function loadRunView(extraContext) {
  const document = {
    getElementById() {
      return null;
    },
    createElement(tag) {
      return new El(tag);
    },
  };
  const context = Object.assign(
    { console, document, setTimeout, clearTimeout },
    extraContext || {},
  );
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(RUNVIEW_SOURCE, 'utf8'), context, {
    filename: 'runview.js',
  });
  return context;
}

function retrieveDefinition() {
  return {
    dsl_version: '1.0',
    input_schema: { type: 'object', properties: { question: { type: 'string' } } },
    output_schema: { type: 'object' },
    nodes: [
      { id: 'start', type: 'start', inputs: {}, config: {}, output_schema: { type: 'object' } },
      {
        id: 'search',
        type: 'retrieve',
        inputs: { query: { ref: 'input#/question' } },
        config: { knowledge_base_ids: ['aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'], top_k: 3 },
        output_schema: { type: 'object' },
      },
      { id: 'end', type: 'end', inputs: {}, config: {}, output_schema: { type: 'object' } },
    ],
    edges: [
      { source: 'start', target: 'search' },
      { source: 'search', target: 'end' },
    ],
  };
}

function retrieveOutput(count) {
  const items = [];
  const citations = [];
  for (let index = 0; index < count; index += 1) {
    const chunk = 'chunk-' + (index + 1);
    const documentId = 'document-' + (index + 1);
    const span = { start_char: index * 10, end_char: index * 10 + 9, start_line: index + 1, end_line: index + 2 };
    items.push({
      chunk_id: chunk,
      document_id: documentId,
      document_revision: 2,
      document_state: 'ready',
      chunk_no: index,
      span,
      excerpt: '段落 ' + (index + 1),
      citation: { document_id: documentId, document_revision: 2, chunk_no: index, span },
    });
    citations.push({
      chunk_id: chunk,
      document_id: documentId,
      document_revision: 2,
      span,
      excerpt: '段落 ' + (index + 1),
    });
  }
  return { items, citations, count };
}

function retrieveModel(context, output) {
  const nodeStates = {
    start: { state: 'succeeded', attempt_no: 1 },
    search: { state: 'succeeded', attempt_no: 1 },
    end: { state: 'succeeded', attempt_no: 1 },
  };
  const nodeResults = {
    items: [
      {
        node_id: 'search',
        kind: 'data',
        artifact_id: 'a-search',
        label: 'results',
        media_type: 'application/json',
        size_bytes: 128,
        sha256: 'a'.repeat(64),
      },
    ],
  };
  return context.buildRunViewModel(
    retrieveDefinition(),
    nodeStates,
    nodeResults,
    { 'a-search': output },
    { state: 'succeeded', error_code: '' },
  );
}

function findCitationsRow(model) {
  const row = model.rows.find((item) => item.nodeId === 'search');
  assert.ok(row, 'the model has no retrieve row: ' + JSON.stringify(plain(model.rows)));
  return row;
}

function findLinks(element, found) {
  const links = found || [];
  for (const child of element.children || []) {
    if (typeof child.className === 'string' && child.className.includes('run-node-citation')) {
      links.push(child);
    }
    findLinks(child, links);
  }
  return links;
}

let checks = 0;
async function test(name, body) {
  await body();
  checks += 1;
  console.log('ok ' + checks + ' - ' + name);
}

await test('a retrieve row exposes its citations with the real citation route', () => {
  const context = loadRunView();
  const model = retrieveModel(context, retrieveOutput(2));
  const row = findCitationsRow(model);
  assert.equal(row.retrieve, true);
  assert.equal(row.retrieveCount, 2);
  assert.equal(row.retrieveCitations.length, 2, plain(row.retrieveCitations));
  assert.equal(row.retrieveCitations[0].chunkId, 'chunk-1');
  assert.equal(row.retrieveCitations[0].path, '/api/v1/knowledge-citations/chunk-1');
  assert.ok(row.retrieveCitations[0].line.includes('document-1'), row.retrieveCitations[0].line);
  assert.ok(row.retrieveCitations[0].line.includes('第 1–2 行'), row.retrieveCitations[0].line);
  assert.ok(row.retrieveCitations[0].line.includes('段落 1'), row.retrieveCitations[0].line);
  assert.ok(row.retrieveText.includes('2'), row.retrieveText);
  assert.ok(model.sourceText.includes('引用'), model.sourceText);
});

await test('a citation carries an excerpt and never the full stored body', () => {
  const context = loadRunView();
  const output = retrieveOutput(1);
  // An older/hand-made artifact may still carry `text`; the page reads only the
  // documented bounded `excerpt`, so no stored body is rendered from node data.
  delete output.citations[0].excerpt;
  output.citations[0].text = '旧正文不应显示';
  const row = findCitationsRow(retrieveModel(context, output));
  assert.equal(row.retrieveCitations.length, 1);
  assert.ok(!row.retrieveCitations[0].line.includes('旧正文'), row.retrieveCitations[0].line);
});

await test('an empty retrieve result says so without pretending it failed', () => {
  const context = loadRunView();
  const model = retrieveModel(context, { items: [], citations: [], count: 0 });
  const row = findCitationsRow(model);
  assert.equal(row.state, 'succeeded');
  assert.equal(row.retrieveCount, 0);
  assert.equal(row.retrieveCitations.length, 0);
  assert.ok(row.retrieveText.includes('无结果'), row.retrieveText);
});

await test('the stable retrieve failure codes have honest hints', () => {
  const context = loadRunView();
  const unavailable = context.formatRunError({ state: 'failed', error_code: 'retrieve_unavailable' });
  assert.ok(unavailable.hint.length > 0, unavailable);
  assert.ok(unavailable.hint.includes('检索'), unavailable.hint);
  const denied = context.formatRunError({ state: 'failed', error_code: 'retrieve_denied' });
  assert.ok(denied.hint.length > 0, denied);
});

await test('the row renders one citation link per citation', () => {
  const context = loadRunView();
  const model = retrieveModel(context, retrieveOutput(2));
  const row = findCitationsRow(model);
  const tableRow = context.runViewRowElement(row);
  const links = findLinks(tableRow);
  assert.equal(links.length, 2, links.length);
  assert.equal(links[0].href, '/api/v1/knowledge-citations/chunk-1');
  assert.equal(links[0].target, '_blank');
  assert.ok(links[0].textContent.includes('document-1'), links[0].textContent);
});

await test('opening a citation shows the real source text', async () => {
  const requested = [];
  const context = loadRunView({
    fetch: async (url) => {
      requested.push(url);
      return {
        ok: true,
        status: 200,
        async json() {
          return {
            chunk_id: 'chunk-1',
            document_id: 'document-1',
            document_revision: 2,
            span: { start_char: 0, end_char: 9, start_line: 1, end_line: 2 },
            text: '真实正文：华北地区十月的销售额',
            citation: { document_id: 'document-1', document_revision: 2 },
          };
        },
      };
    },
  });
  const container = context.document.createElement('div');
  const link = context.document.createElement('a');
  container.appendChild(link);
  const citation = {
    chunkId: 'chunk-1',
    documentId: 'document-1',
    documentRevision: '2',
    span: '第 1–2 行',
    text: '段落 1',
    path: '/api/v1/knowledge-citations/chunk-1',
  };
  const result = await context.runViewOpenCitation(link, citation);
  assert.equal(requested[0], '/api/v1/knowledge-citations/chunk-1');
  assert.ok(result && result.opened, result);
  const details = container.children.find((child) => child.tagName === 'DETAILS');
  assert.ok(details, container.children.map((child) => child.tagName));
  const pre = details.children.find((child) => child.tagName === 'PRE');
  assert.ok(pre.textContent.includes('华北地区十月的销售额'), pre.textContent);
});

await test('a revoked citation says unavailable and never shows cached text', async () => {
  const context = loadRunView({
    fetch: async () => ({
      ok: false,
      status: 404,
      async json() {
        return { error: { code: 'resource_not_found', message: 'gone' } };
      },
    }),
  });
  const container = context.document.createElement('div');
  const link = context.document.createElement('a');
  container.appendChild(link);
  const result = await context.runViewOpenCitation(link, {
    chunkId: 'chunk-1',
    documentId: 'document-1',
    documentRevision: '2',
    span: '第 1–2 行',
    text: '旧摘要',
    path: '/api/v1/knowledge-citations/chunk-1',
  });
  assert.ok(result && result.unavailable, result);
  assert.ok(link.className.includes('unavailable'), link.className);
  const status = container.children.find((child) =>
    String(child.className).includes('run-node-citation-status'));
  assert.ok(status, container.children.map((child) => child.className));
  assert.ok(status.textContent.includes('不可用'), status.textContent);
  assert.ok(!status.textContent.includes('旧摘要'), status.textContent);
});

console.log('PASS ' + checks + ' runview T088 tests');
