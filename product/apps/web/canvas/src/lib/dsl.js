/*
 * F04A/T040 canvas DSL <-> graph pure module.
 *
 * No Vue, no DOM, no fetch: the Node module test executes these functions
 * directly. The canvas app and the page's save path both use them, so the
 * object that is saved is exactly the object the graph shows.
 */

export const NODE_TYPES = ['start', 'python', 'agent', 'end'];

export const NODE_LABELS = {
  start: '开始',
  python: 'Python 工具',
  agent: 'Agent',
  end: '结束',
};

export function emptySchema() {
  return { type: 'object', properties: {} };
}

export function defaultPolicy() {
  return {
    budget_limit: '1.00',
    currency: 'USD',
    max_active_seconds: 300,
    max_child_calls: 0,
    max_child_depth: 0,
  };
}

export function defaultNode(type, id) {
  const base = {
    id,
    type,
    inputs: {},
    output_schema: emptySchema(),
    config: {},
  };
  if (type === 'python') {
    base.config = { tool_version: '' };
  } else if (type === 'agent') {
    base.config = {
      instructions: '',
      max_turns: 8,
      model_profile_version: '',
      subapp_versions: [],
      tool_versions: [],
    };
  }
  return base;
}

export function defaultDefinition() {
  return {
    dsl_version: '1.0',
    input_schema: emptySchema(),
    output_schema: emptySchema(),
    nodes: [
      defaultNode('start', 'start'),
      defaultNode('python', 'python1'),
      defaultNode('agent', 'agent1'),
      defaultNode('end', 'end'),
    ],
    edges: [
      { source: 'start', target: 'python1' },
      { source: 'python1', target: 'agent1' },
      { source: 'agent1', target: 'end' },
    ],
    policy: defaultPolicy(),
  };
}

export const DEFAULT_POSITIONS = {
  start: { x: 40, y: 160 },
  python: { x: 280, y: 160 },
  agent: { x: 520, y: 160 },
  end: { x: 760, y: 160 },
};

export function defaultLayout(definition) {
  const source = definition && Array.isArray(definition.nodes) ? definition.nodes : [];
  const positions = {};
  let middle = 0;
  for (const node of source) {
    if (DEFAULT_POSITIONS[node.type]) {
      positions[node.id] = { ...DEFAULT_POSITIONS[node.type] };
    } else {
      positions[node.id] = { x: 280 + 240 * middle, y: 160 };
      middle += 1;
    }
  }
  return { version: 1, positions };
}

export function positionsOf(definition, layout) {
  const stored = layout && typeof layout.positions === 'object' && layout.positions !== null
    ? layout.positions
    : {};
  const result = {};
  let middle = 0;
  for (const node of definition.nodes || []) {
    const position = stored[node.id];
    const valid = position && typeof position.x === 'number' && typeof position.y === 'number';
    if (valid) {
      result[node.id] = { x: position.x, y: position.y };
    } else if (node.type === 'start' || node.type === 'end') {
      result[node.id] = { ...DEFAULT_POSITIONS[node.type] };
    } else {
      result[node.id] = { x: 280 + 240 * middle, y: 160 };
      middle += 1;
    }
  }
  return result;
}

export function definitionToGraph(definition, layout) {
  const positions = positionsOf(definition, layout);
  const nodes = (definition.nodes || []).map((node) => ({
    id: node.id,
    type: 'default',
    label: NODE_LABELS[node.type] || node.type,
    class: 'orchara-node orchara-node-' + node.type,
    position: positions[node.id] || { x: 0, y: 0 },
    data: { label: NODE_LABELS[node.type] || node.type, nodeType: node.type },
  }));
  const edges = (definition.edges || []).map((edge, index) => ({
    id: edge.source + '->' + edge.target + ':' + index,
    source: edge.source,
    target: edge.target,
    class: 'orchara-edge',
  }));
  return { nodes, edges };
}

/** The chain order from the start node, falling back to declaration order. */
export function chainOrder(definition) {
  const nodes = definition.nodes || [];
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const start = nodes.find((node) => node.type === 'start');
  const outgoing = new Map();
  for (const edge of definition.edges || []) {
    if (!outgoing.has(edge.source)) {
      outgoing.set(edge.source, []);
    }
    outgoing.get(edge.source).push(edge.target);
  }
  const order = [];
  const seen = new Set();
  let current = start ? start.id : null;
  while (current && byId.has(current) && !seen.has(current)) {
    seen.add(current);
    order.push(current);
    const next = outgoing.get(current) || [];
    current = next.length === 1 ? next[0] : null;
  }
  for (const node of nodes) {
    if (!seen.has(node.id)) {
      order.push(node.id);
    }
  }
  return order;
}

/**
 * Rebuild the definition from the canvas state. Configs are merged by node id,
 * so dragging or reconnecting never loses a config value; only the chain shape
 * and the canvas layout are changed.
 */
export function graphToDefinition(graphNodes, graphEdges, previous) {
  const previousById = new Map((previous && previous.nodes ? previous.nodes : []).map((n) => [n.id, n]));
  const sourceNodes = Array.isArray(graphNodes) ? graphNodes : [];
  const sourceEdges = Array.isArray(graphEdges) ? graphEdges : [];
  const byId = new Map();
  for (const flowNode of sourceNodes) {
    const id = flowNode.id;
    const nodeType = (flowNode.data && flowNode.data.nodeType) || previousById.get(id)?.type || 'python';
    const old = previousById.get(id) || defaultNode(nodeType, id);
    byId.set(id, {
      id,
      type: nodeType,
      inputs: old.inputs && typeof old.inputs === 'object' ? old.inputs : {},
      output_schema:
        old.output_schema && typeof old.output_schema === 'object' ? old.output_schema : emptySchema(),
      config: old.config && typeof old.config === 'object' ? old.config : {},
    });
  }
  const draft = {
    ...(previous || {}),
    dsl_version: (previous && previous.dsl_version) || '1.0',
    input_schema: (previous && previous.input_schema) || emptySchema(),
    output_schema: (previous && previous.output_schema) || emptySchema(),
    policy: (previous && previous.policy) || defaultPolicy(),
    nodes: [],
    edges: [],
  };
  const order = chainOrderFromGraph(sourceNodes, sourceEdges);
  for (const id of order) {
    if (byId.has(id)) {
      draft.nodes.push(byId.get(id));
      byId.delete(id);
    }
  }
  for (const node of byId.values()) {
    draft.nodes.push(node);
  }
  const known = new Set(draft.nodes.map((node) => node.id));
  for (const edge of sourceEdges) {
    if (known.has(edge.source) && known.has(edge.target)) {
      draft.edges.push({ source: edge.source, target: edge.target });
    }
  }
  return draft;
}

export function chainOrderFromGraph(nodes, edges) {
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const start = nodes.find((node) => (node.data && node.data.nodeType) === 'start');
  const outgoing = new Map();
  for (const edge of edges) {
    if (!outgoing.has(edge.source)) {
      outgoing.set(edge.source, []);
    }
    outgoing.get(edge.source).push(edge.target);
  }
  const order = [];
  const seen = new Set();
  let current = start ? start.id : null;
  while (current && byId.has(current) && !seen.has(current)) {
    seen.add(current);
    order.push(current);
    const next = outgoing.get(current) || [];
    current = next.length === 1 ? next[0] : null;
  }
  for (const node of nodes) {
    if (!seen.has(node.id)) {
      order.push(node.id);
    }
  }
  return order;
}

export function layoutFromGraph(graphNodes, previousLayout) {
  const base = previousLayout && typeof previousLayout === 'object' ? previousLayout : {};
  const positions = { ...(base.positions || {}) };
  for (const node of graphNodes || []) {
    const position = node.position || (node.computedPosition || null);
    if (position && typeof position.x === 'number' && typeof position.y === 'number') {
      positions[node.id] = { x: position.x, y: position.y };
    }
  }
  return { ...base, version: 1, positions };
}

// ---------------------------------------------------------------------------
// linear-chain enforcement (the UI's explanation path)
// ---------------------------------------------------------------------------
export function connectionProblem(nodes, edges, connection) {
  const source = connection && connection.source;
  const target = connection && connection.target;
  if (!source || !target) {
    return { code: 'invalid_connection', reason: '连接缺少起点或终点。' };
  }
  if (source === target) {
    return { code: 'self_loop', reason: '节点不能连接到自己。' };
  }
  const byId = new Map((nodes || []).map((node) => [node.id, node]));
  const sourceNode = byId.get(source);
  const targetNode = byId.get(target);
  if (!sourceNode || !targetNode) {
    return { code: 'unknown_node', reason: '连接引用了不存在的节点。' };
  }
  if (sourceNode.data && sourceNode.data.nodeType === 'end') {
    return { code: 'end_has_no_output', reason: '结束节点没有输出，不能连出。' };
  }
  if (targetNode.data && targetNode.data.nodeType === 'start') {
    return { code: 'start_has_no_input', reason: '开始节点没有输入，不能连入。' };
  }
  if ((edges || []).some((edge) => edge.source === source && edge.target === target)) {
    return { code: 'duplicate_edge', reason: '这条连接已经存在。' };
  }
  if ((edges || []).some((edge) => edge.source === source)) {
    return { code: 'outgoing_exists', reason: 'F04A 只允许一条线性链：源节点已有唯一输出了。' };
  }
  if ((edges || []).some((edge) => edge.target === target)) {
    return { code: 'incoming_exists', reason: 'F04A 只允许一条线性链：目标节点已有唯一输入了。' };
  }
  if (createsCycle(edges, source, target)) {
    return { code: 'cycle', reason: '这条连接会形成环，线性链必须无环。' };
  }
  return { ok: true, code: 'ok', reason: '' };
}

export function canConnect(nodes, edges, connection) {
  const problem = connectionProblem(nodes, edges, connection);
  return problem.ok === true ? { ok: true, code: 'ok', reason: '' } : problem;
}

export function createsCycle(edges, source, target) {
  // Adding source -> target closes a cycle when target can already reach source.
  const adjacency = new Map();
  for (const edge of edges || []) {
    if (!adjacency.has(edge.source)) {
      adjacency.set(edge.source, []);
    }
    adjacency.get(edge.source).push(edge.target);
  }
  const pending = [target];
  const seen = new Set();
  while (pending.length > 0) {
    const current = pending.pop();
    if (current === source) {
      return true;
    }
    if (seen.has(current)) {
      continue;
    }
    seen.add(current);
    for (const next of adjacency.get(current) || []) {
      pending.push(next);
    }
  }
  return false;
}

/** Why the current graph is not an executable F04A chain (empty when it is). */
export function linearChainProblems(graphNodes, graphEdges) {
  const nodes = graphNodes || [];
  const edges = graphEdges || [];
  const problems = [];
  const starts = nodes.filter((node) => node.data && node.data.nodeType === 'start');
  const ends = nodes.filter((node) => node.data && node.data.nodeType === 'end');
  if (starts.length !== 1) {
    problems.push({ code: 'start_end_count', message: '画布必须恰好有一个开始节点。' });
  }
  if (ends.length !== 1) {
    problems.push({ code: 'start_end_count', message: '画布必须恰好有一个结束节点。' });
  }
  if (nodes.length !== 4) {
    problems.push({ code: 'linear_order', message: 'F04A 画布恰好需要四个节点。' });
  }
  const outgoing = new Map();
  const incoming = new Map();
  for (const edge of edges) {
    outgoing.set(edge.source, (outgoing.get(edge.source) || 0) + 1);
    incoming.set(edge.target, (incoming.get(edge.target) || 0) + 1);
  }
  for (const node of nodes) {
    const out = outgoing.get(node.id) || 0;
    const inc = incoming.get(node.id) || 0;
    const type = node.data && node.data.nodeType;
    if (out > 1 || inc > 1) {
      problems.push({ code: 'not_linear_chain', message: '节点 ' + node.id + ' 有多个连接。' });
    }
    if (type === 'start' && (inc !== 0 || out !== 1)) {
      problems.push({ code: 'not_linear_chain', message: '开始节点必须恰好有一个输出。' });
    }
    if (type === 'end' && (out !== 0 || inc !== 1)) {
      problems.push({ code: 'not_linear_chain', message: '结束节点必须恰好有一个输入。' });
    }
  }
  return problems;
}

// ---------------------------------------------------------------------------
// canvas mutation helpers
// ---------------------------------------------------------------------------
export function addNode(graphNodes, graphEdges, type, layout) {
  const nodes = graphNodes || [];
  const id = type === 'start' || type === 'end' ? type : type + (nodes.length + 1);
  if (nodes.some((node) => node.data && node.data.nodeType === type)) {
    return { ok: false, code: 'node_exists', reason: 'F04A 只允许每种节点各一个。' };
  }
  const position = DEFAULT_POSITIONS[type] || { x: 280 + 240 * nodes.length, y: 320 };
  return {
    ok: true,
    node: {
      id,
      type: 'default',
      label: NODE_LABELS[type],
      class: 'orchara-node orchara-node-' + type,
      position: { ...position },
      data: { label: NODE_LABELS[type], nodeType: type },
    },
    layout,
  };
}

export function removeNode(graphNodes, graphEdges, nodeId) {
  return {
    nodes: (graphNodes || []).filter((node) => node.id !== nodeId),
    edges: (graphEdges || []).filter((edge) => edge.source !== nodeId && edge.target !== nodeId),
  };
}

export function definitionProblems(definition) {
  const problems = [];
  const nodes = definition.nodes || [];
  for (const type of NODE_TYPES) {
    const count = nodes.filter((node) => node.type === type).length;
    if (count !== 1) {
      problems.push({ code: 'linear_order', message: '需要恰好一个 ' + type + ' 节点。' });
    }
  }
  const edges = definition.edges || [];
  if (edges.length !== 3) {
    problems.push({ code: 'not_linear_chain', message: 'F04A 线性链需要恰好三条连接。' });
  }
  return problems;
}
