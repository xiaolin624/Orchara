/*
 * F04A/T040 legal variable sources.
 *
 * A node may read:
 *   - application input properties:  input#/a/b
 *   - output_schema properties of an *upstream* node: node:<id>#/x
 * It may never read itself or a downstream node. The Go validator is still the
 * authority; this module only decides what the selector offers.
 */

export function escapePointer(token) {
  return String(token).replace(/~/g, '~0').replace(/\//g, '~1');
}

export function unescapePointer(token) {
  return String(token).replace(/~1/g, '/').replace(/~0/g, '~');
}

function schemaProperties(schema, baseRef, basePath, out) {
  if (!schema || typeof schema !== 'object') {
    return;
  }
  const properties = schema.properties;
  if (!properties || typeof properties !== 'object') {
    return;
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

/**
 * Every legal source for a node, in definition order: application input first,
 * then upstream nodes from the nearest to the farthest.
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
  for (const upstreamId of upstreamNodeIds(definition, nodeId)) {
    const upstream = definition.nodes.find((item) => item.id === upstreamId);
    if (!upstream) {
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
  return sources;
}

export function isLegalSource(definition, nodeId, ref) {
  return legalSources(definition, nodeId).some((entry) => entry.ref === ref);
}

export function sourceOf(definition, nodeId, ref) {
  return legalSources(definition, nodeId).find((entry) => entry.ref === ref) || null;
}

export function sourceType(definition, ref) {
  if (typeof ref !== 'string') {
    return 'unknown';
  }
  const hash = ref.indexOf('#');
  if (hash < 0) {
    return 'unknown';
  }
  const head = ref.slice(0, hash);
  const pointer = ref.slice(hash + 1);
  let schema;
  if (head === 'input') {
    schema = definition.input_schema;
  } else if (head.startsWith('node:')) {
    const node = (definition.nodes || []).find((item) => item.id === head.slice(5));
    schema = node ? node.output_schema : null;
  }
  for (const token of pointer.split('/').filter((part) => part !== '')) {
    if (!schema || typeof schema !== 'object') {
      return 'unknown';
    }
    const properties = schema.properties;
    if (properties && Object.prototype.hasOwnProperty.call(properties, unescapePointer(token))) {
      schema = properties[unescapePointer(token)];
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
