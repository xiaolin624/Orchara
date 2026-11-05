/*
 * F04B/T083 legal variable sources.
 *
 * A node may read:
 *   - application input properties:  input#/a/b
 *   - output_schema properties of a dominating upstream node: node:<id>#/x
 *   - a join's envelope, by source:  node:<join>#/results/<source>[/value]
 *
 * It may never read itself or a downstream node, and branch outputs that sit
 * inside a region may only be read through the region's paired join (the
 * contract's "branch values are referenced by source after the join"). The Go
 * validator is still the authority; this module only decides what the selector
 * offers.
 */

import { branchNodeIds, dominanceOf, regionPairs } from './dsl.js';

export function escapePointer(token) {
  return String(token).replace(/~/g, '~0').replace(/\//g, '~1');
}

export function unescapePointer(token) {
  return String(token).replace(/~1/g, '/').replace(/~0/g, '~');
}

function schemaProperties(schema, baseRef, basePath, out) {
  if (!schema || typeof schema !== 'object') {
    return out;
  }
  const properties = schema.properties;
  if (!properties || typeof properties !== 'object') {
    return out;
  }
  for (const name of Object.keys(properties).sort()) {
    const child = properties[name];
    const ref = baseRef + '/' + escapePointer(name);
    const path = basePath ? basePath + '.' + name : name;
    out.push({
      ref,
      path,
      label: path,
      type: describeType(child),
    });
    if (child && child.type === 'object') {
      schemaProperties(child, ref, path, out);
    }
  }
  return out;
}

export function describeType(schema) {
  if (!schema || typeof schema !== 'object') {
    return 'unknown';
  }
  const type = schema.type;
  if (Array.isArray(type)) {
    return type.join(' | ');
  }
  if (type === 'array' && schema.items && schema.items.type) {
    return 'array<' + schema.items.type + '>';
  }
  return type || 'unknown';
}

function incomingEdges(definition) {
  const incoming = new Map();
  for (const edge of definition.edges || []) {
    if (!incoming.has(edge.target)) {
      incoming.set(edge.target, []);
    }
    incoming.get(edge.target).push(edge.source);
  }
  return incoming;
}

/** Ancestors of nodeId, nearest first, from the declared edges. */
export function upstreamNodeIds(definition, nodeId) {
  const incoming = incomingEdges(definition);
  const order = [];
  const seen = new Set([nodeId]);
  const pending = [...(incoming.get(nodeId) || [])];
  while (pending.length > 0) {
    const current = pending.shift();
    if (seen.has(current)) {
      continue;
    }
    seen.add(current);
    order.push(current);
    for (const parent of incoming.get(current) || []) {
      pending.push(parent);
    }
  }
  const position = new Map((definition.nodes || []).map((node, index) => [node.id, index]));
  order.sort((a, b) => (position.get(a) ?? 0) - (position.get(b) ?? 0));
  return order;
}

function joinEnvelopeSources(definition, nodeId) {
  const entries = [];
  const nodes = definition.nodes || [];
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const ancestors = new Set(upstreamNodeIds(definition, nodeId));
  const dom = dominanceOf(definition);
  for (const pair of regionPairs(definition)) {
    if (!ancestors.has(pair.joinId) || !byId.has(pair.joinId)) {
      continue;
    }
    // A join that does not dominate the reader can be skipped on some path;
    // reading it would be an implicit null, so the selector stays silent.
    if (dom !== null && !(dom.get(nodeId) || new Set()).has(pair.joinId)) {
      continue;
    }
    const joinLabel = 'join ' + pair.joinId;
    entries.push({
      ref: 'node:' + pair.joinId + '#/results',
      path: 'results',
      label: pair.joinId + '#/results（汇合封套）',
      type: 'object',
      source: joinLabel,
      sourceNodeId: pair.joinId,
    });
    const predecessors = [...new Set(
      (definition.edges || [])
        .filter((edge) => edge.target === pair.joinId)
        .map((edge) => edge.source),
    )];
    for (const sourceId of predecessors) {
      const sourceNode = byId.get(sourceId);
      const sourceType = sourceNode ? sourceNode.type : 'node';
      const valueType = describeType(sourceNode && sourceNode.output_schema);
      entries.push({
        ref: 'node:' + pair.joinId + '#/results/' + escapePointer(sourceId),
        path: 'results.' + sourceId,
        label: pair.joinId + '#/results/' + sourceId + '（' + sourceType + ' 结果封套）',
        type: 'object',
        source: joinLabel,
        sourceNodeId: pair.joinId,
      });
      entries.push({
        ref: 'node:' + pair.joinId + '#/results/' + escapePointer(sourceId) + '/value',
        path: 'results.' + sourceId + '.value',
        label: pair.joinId + '#/results/' + sourceId + '/value（' + sourceType + '）',
        type: valueType,
        source: joinLabel,
        sourceNodeId: pair.joinId,
      });
    }
  }
  return entries;
}

/**
 * Every legal source for a node, in definition order: application input first,
 * then upstream nodes, then the envelopes of dominating joins.
 */
export function legalSources(definition, nodeId) {
  const sources = [];
  if (!definition || !Array.isArray(definition.nodes)) {
    return sources;
  }
  const node = definition.nodes.find((item) => item.id === nodeId);
  if (!node || node.type === 'start') {
    return sources; // start has no inputs: the application input is the start.
  }
  for (const entry of schemaProperties(definition.input_schema, 'input#', '', [])) {
    sources.push({ ...entry, source: 'application input', sourceNodeId: '' });
  }
  const branch = branchNodeIds(definition);
  const dom = dominanceOf(definition);
  for (const upstreamId of upstreamNodeIds(definition, nodeId)) {
    const upstream = definition.nodes.find((item) => item.id === upstreamId);
    if (!upstream) {
      continue;
    }
    if (branch.has(upstreamId)) {
      // A branch node's value is only exposed through its paired join.
      continue;
    }
    if (dom !== null && !(dom.get(nodeId) || new Set()).has(upstreamId)) {
      // An ancestor that is not on every executable path may be skipped.
      continue;
    }
    for (const entry of schemaProperties(upstream.output_schema, 'node:' + upstreamId + '#', '', [])) {
      sources.push({
        ...entry,
        source: 'node ' + upstreamId + ' (' + upstream.type + ')',
        sourceNodeId: upstreamId,
      });
    }
  }
  for (const entry of joinEnvelopeSources(definition, nodeId)) {
    sources.push(entry);
  }
  return dedupe(sources);
}

function dedupe(entries) {
  const seen = new Set();
  const result = [];
  for (const entry of entries) {
    if (seen.has(entry.ref)) {
      continue;
    }
    seen.add(entry.ref);
    result.push(entry);
  }
  return result;
}

export function isLegalSource(definition, nodeId, ref) {
  return legalSources(definition, nodeId).some((entry) => entry.ref === ref);
}

export function sourceOf(definition, nodeId, ref) {
  return legalSources(definition, nodeId).find((entry) => entry.ref === ref) || null;
}

/** The declared type of a ref, including a join's envelope-by-source refs. */
export function sourceType(definition, ref) {
  if (typeof ref !== 'string') {
    return 'unknown';
  }
  const hash = ref.indexOf('#');
  if (hash < 0) {
    return 'unknown';
  }
  const head = ref.slice(0, hash);
  const tokens = ref.slice(hash + 1).split('/').filter((part) => part !== '').map(unescapePointer);
  if (head.startsWith('node:')) {
    const node = (definition.nodes || []).find((item) => item.id === head.slice(5));
    if (node && node.type === 'join' && tokens.length > 0 && tokens[0] === 'results') {
      if (tokens.length === 1) {
        return 'object';
      }
      if (tokens.length === 2) {
        return 'object';
      }
      if (tokens.length === 3 && tokens[2] === 'value') {
        const source = (definition.nodes || []).find((item) => item.id === tokens[1]);
        return describeType(source && source.output_schema);
      }
      return 'unknown';
    }
  }
  let schema;
  if (head === 'input') {
    schema = definition.input_schema;
  } else if (head.startsWith('node:')) {
    const node = (definition.nodes || []).find((item) => item.id === head.slice(5));
    schema = node ? node.output_schema : null;
  }
  for (const token of tokens) {
    if (!schema || typeof schema !== 'object') {
      return 'unknown';
    }
    const properties = schema.properties;
    if (properties && Object.prototype.hasOwnProperty.call(properties, token)) {
      schema = properties[token];
      continue;
    }
    if (schema.type === 'array' && /^\d+$/.test(token) && schema.items) {
      schema = schema.items;
      continue;
    }
    return 'unknown';
  }
  return describeType(schema);
}

/** Grouped selector options; empty groups are omitted. */
export function sourceGroups(definition, nodeId) {
  const sources = legalSources(definition, nodeId);
  const groups = new Map();
  for (const entry of sources) {
    const key = entry.sourceNodeId ? entry.sourceNodeId : 'input';
    if (!groups.has(key)) {
      groups.set(key, { key, label: entry.source, options: [] });
    }
    groups.get(key).options.push(entry);
  }
  return [...groups.values()];
}
