/*
 * F04B/T083 canvas DSL <-> graph pure module.
 *
 * No Vue, no DOM, no fetch: the Node module tests execute these functions
 * directly. The canvas app and the page's save path both use them, so the
 * object that is saved is exactly the object the graph shows.
 *
 * The graph is a DAG. A condition node fans out through named ports; any other
 * node may fan out only when a join declares it as its ``fork_id``. Only a join
 * may have several incoming edges. The Go validator is still the authority:
 * ``regionProblems`` mirrors the same rules locally so the canvas can explain
 * where a connection is illegal and how to repair it.
 *
 * F04B/T086 adds the ``model`` node: one controlled generation whose fixed
 * profile, strict ``{{name}}`` prompt and response mode live in its config, and
 * whose "response schema" is the ordinary ``output_schema`` (there is no
 * ``response_schema`` config key).
 */

export const NODE_TYPES = [
  'start', 'python', 'agent', 'model', 'condition', 'join', 'approval', 'end',
];

export const NODE_LABELS = {
  start: '开始',
  python: 'Python 工具',
  agent: 'Agent',
  model: '模型',
  condition: '条件分支',
  join: '并行汇合',
  approval: '人工审批',
  end: '结束',
};

/** Types the palette may add more than once; start and end stay unique. */
export const REPEATABLE_NODE_TYPES = [
  'python', 'agent', 'model', 'condition', 'join', 'approval',
];

/** The comparison vocabulary of a condition case (workflow.schema.json). */
export const CONDITION_OPS = ['eq', 'ne', 'gt', 'gte', 'lt', 'lte', 'in', 'exists'];

/** The only join mode in the contract. */
export const JOIN_MODES = ['all_active'];

/** The model node's response modes (workflow.schema.json config enum). */
export const MODEL_RESPONSE_MODES = ['json', 'text'];

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

export function conditionPorts(config) {
  const ports = [];
  const seen = new Set();
  const add = (port) => {
    if (typeof port === 'string' && port !== '' && !seen.has(port)) {
      seen.add(port);
      ports.push(port);
    }
  };
  const cases = config && Array.isArray(config.cases) ? config.cases : [];
  for (const item of cases) {
    if (item && typeof item === 'object') {
      add(item.port);
    }
  }
  if (config && typeof config === 'object') {
    add(config.default_port);
  }
  return ports;
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
  } else if (type === 'model') {
    // The contract's closed model config set. The response schema is the node's
    // output_schema, so it is deliberately absent here.
    base.config = {
      model_profile_version: '',
      prompt_template: '',
      response_mode: 'json',
    };
  } else if (type === 'condition') {
    base.config = {
      join_id: '',
      default_port: 'no',
      cases: [{ input: '', op: 'eq', value: true, port: 'yes' }],
    };
  } else if (type === 'join') {
    base.config = {
      mode: 'all_active',
      fork_id: '',
      required_sources: [],
      optional_sources: [],
    };
  } else if (type === 'approval') {
    // Contract config is exactly {title, expires_seconds}; the proposal is a
    // bound input, never a config field. The default is an (editable) empty
    // object literal so the node is complete on the canvas.
    base.inputs = { proposal: { literal: {} } };
    base.config = { title: '', expires_seconds: 3600 };
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
  model: { x: 400, y: 320 },
  condition: { x: 280, y: 320 },
  join: { x: 600, y: 320 },
  approval: { x: 420, y: 440 },
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

/** Stable canvas edge id from the definition order; errors reuse it. */
export function edgeIdFor(edge, index) {
  return String(edge && edge.source) + '->' + String(edge && edge.target) + ':' + index;
}

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function configOf(node) {
  return node && node.config && typeof node.config === 'object' && !Array.isArray(node.config)
    ? node.config
    : {};
}

function typeOfNode(node) {
  return (node && node.data && node.data.nodeType) || (node && node.type) || '';
}

/**
 * Build the Vue Flow graph. A condition node carries one *named source handle*
 * per declared port (cases[].port then default_port, de-duplicated); every other
 * node keeps a single implicit handle. Edges keep their port as the source
 * handle, a ``data.port`` and a display label, so the port is visible and
 * editable on the canvas.
 */
export function definitionToGraph(definition, layout) {
  const positions = positionsOf(definition, layout);
  const sourceNodes = definition.nodes || [];
  const sourceEdges = definition.edges || [];
  const typeById = new Map(sourceNodes.map((node) => [node.id, node.type]));
  const incoming = new Map();
  for (const edge of sourceEdges) {
    if (!incoming.has(edge.target)) {
      incoming.set(edge.target, []);
    }
    incoming.get(edge.target).push(edge.source);
  }
  const nodes = sourceNodes.map((node) => {
    const type = node.type;
    const data = {
      label: NODE_LABELS[type] || type,
      nodeType: type,
      config: clone(configOf(node)),
    };
    if (type === 'condition') {
      data.ports = conditionPorts(configOf(node));
    }
    if (type === 'join') {
      data.sourceLabels = [...(incoming.get(node.id) || [])];
    }
    return {
      id: node.id,
      type: 'default',
      label: NODE_LABELS[type] || type,
      class: 'orchara-node orchara-node-' + type,
      position: positions[node.id] || { x: 0, y: 0 },
      data,
    };
  });
  const edges = sourceEdges.map((edge, index) => {
    const port = typeof edge.port === 'string' && edge.port !== '' ? edge.port : '';
    const isConditionSource = typeById.get(edge.source) === 'condition';
    const handle = isConditionSource ? port : '';
    return {
      id: edgeIdFor(edge, index),
      source: edge.source,
      target: edge.target,
      class: 'orchara-edge' + (port ? ' orchara-edge-port' : ''),
      label: handle,
      sourceHandle: handle || undefined,
      targetHandle: 'in',
      data: { port },
    };
  });
  return { nodes, edges };
}

// ---------------------------------------------------------------------------
// Ordering: a topological order, never a truncated linear chain
// ---------------------------------------------------------------------------
function topologicalOrder(items, edges, idOf) {
  const ids = items.map(idOf);
  const index = new Map(ids.map((id, position) => [id, position]));
  const adjacency = new Map(ids.map((id) => [id, []]));
  const indegree = new Map(ids.map((id) => [id, 0]));
  for (const edge of edges || []) {
    if (!index.has(edge.source) || !index.has(edge.target)) {
      continue;
    }
    const targets = adjacency.get(edge.source);
    if (targets.includes(edge.target)) {
      continue; // duplicate edges do not affect ordering
    }
    targets.push(edge.target);
    indegree.set(edge.target, indegree.get(edge.target) + 1);
  }
  for (const targets of adjacency.values()) {
    targets.sort((a, b) => index.get(a) - index.get(b));
  }
  const queue = ids.filter((id) => indegree.get(id) === 0);
  const order = [];
  while (queue.length > 0) {
    const current = queue.shift();
    order.push(current);
    for (const target of adjacency.get(current)) {
      indegree.set(target, indegree.get(target) - 1);
      if (indegree.get(target) === 0) {
        queue.push(target);
      }
    }
  }
  // A cycle leaves nodes out: fall back to declaration order instead of
  // dropping them from the saved document.
  return order.length === ids.length ? order : ids;
}

/** The chain order from the start node, falling back to declaration order. */
export function chainOrder(definition) {
  const nodes = definition.nodes || [];
  return topologicalOrder(nodes, definition.edges || [], (node) => node.id);
}

/**
 * Rebuild the definition from the canvas state. Configs are merged by node id,
 * so dragging or reconnecting never loses a config value; only the graph shape
 * and the canvas layout are changed. Edge ports are preserved and join source
 * lists are pruned of nodes that no longer exist.
 */
export function graphToDefinition(graphNodes, graphEdges, previous) {
  const previousById = new Map(
    (previous && previous.nodes ? previous.nodes : []).map((node) => [node.id, node]),
  );
  const sourceNodes = Array.isArray(graphNodes) ? graphNodes : [];
  const sourceEdges = Array.isArray(graphEdges) ? graphEdges : [];
  const typeById = new Map(sourceNodes.map((node) => [node.id, typeOfNode(node)]));
  const byId = new Map();
  for (const flowNode of sourceNodes) {
    const id = flowNode.id;
    const nodeType = typeOfNode(flowNode) || previousById.get(id)?.type || 'python';
    const old = previousById.get(id) || defaultNode(nodeType, id);
    byId.set(id, {
      id,
      type: nodeType,
      // Deep-copy the parts merged by id: the rebuilt definition must not alias
      // the previous one the page still holds in its textareas.
      inputs: old.inputs && typeof old.inputs === 'object' ? clone(old.inputs) : {},
      output_schema:
        old.output_schema && typeof old.output_schema === 'object'
          ? clone(old.output_schema)
          : emptySchema(),
      config: old.config && typeof old.config === 'object' ? clone(old.config) : {},
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
  // A join must never keep a source that no longer exists on the canvas.
  for (const node of draft.nodes) {
    if (node.type !== 'join' || !node.config || typeof node.config !== 'object') {
      continue;
    }
    const required = Array.isArray(node.config.required_sources) ? node.config.required_sources : [];
    const optional = Array.isArray(node.config.optional_sources) ? node.config.optional_sources : [];
    const keptRequired = required.filter((id) => known.has(id) && id !== node.id);
    const keptOptional = optional.filter(
      (id) => known.has(id) && id !== node.id && !keptRequired.includes(id),
    );
    node.config = {
      ...node.config,
      required_sources: [...new Set(keptRequired)],
      optional_sources: [...new Set(keptOptional)],
    };
  }
  for (const edge of sourceEdges) {
    if (known.has(edge.source) && known.has(edge.target)) {
      const out = { source: edge.source, target: edge.target };
      let port = '';
      if (edge.data && typeof edge.data.port === 'string') {
        port = edge.data.port;
      }
      if (port === '' && typeById.get(edge.source) === 'condition'
          && typeof edge.sourceHandle === 'string' && edge.sourceHandle !== 'out') {
        port = edge.sourceHandle;
      }
      if (port !== '') {
        out.port = port;
      }
      draft.edges.push(out);
    }
  }
  return draft;
}

export function chainOrderFromGraph(nodes, edges) {
  return topologicalOrder(nodes || [], edges || [], (node) => node.id);
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
// connection rules: a DAG with declared forks, condition ports and joins
// ---------------------------------------------------------------------------
function flowTypeById(nodes) {
  return new Map((nodes || []).map((node) => [node.id, typeOfNode(node)]));
}

function flowConfig(node) {
  return node && node.data && node.data.config && typeof node.data.config === 'object'
    ? node.data.config
    : {};
}

function declaredForkIds(nodes) {
  const declared = new Set();
  for (const node of nodes || []) {
    if (typeOfNode(node) !== 'join') {
      continue;
    }
    const forkId = flowConfig(node).fork_id;
    if (typeof forkId === 'string' && forkId !== '') {
      declared.add(forkId);
    }
  }
  return declared;
}

export function edgePortOf(edge) {
  if (edge && edge.data && typeof edge.data.port === 'string' && edge.data.port !== '') {
    return edge.data.port;
  }
  if (edge && typeof edge.sourceHandle === 'string' && edge.sourceHandle !== ''
      && edge.sourceHandle !== 'out') {
    return edge.sourceHandle;
  }
  return '';
}

export function connectionProblem(nodes, edges, connection) {
  const source = connection && connection.source;
  const target = connection && connection.target;
  if (!source || !target) {
    return { code: 'invalid_connection', reason: '连接缺少起点或终点。' };
  }
  if (source === target) {
    return { code: 'self_loop', reason: '节点不能连接到自己。' };
  }
  const nodeList = nodes || [];
  const edgeList = edges || [];
  const byId = new Map(nodeList.map((node) => [node.id, node]));
  const sourceNode = byId.get(source);
  const targetNode = byId.get(target);
  if (!sourceNode || !targetNode) {
    return { code: 'unknown_node', reason: '连接引用了不存在的节点。' };
  }
  const sourceType = typeOfNode(sourceNode);
  const targetType = typeOfNode(targetNode);
  if (sourceType === 'end') {
    return { code: 'end_has_no_output', reason: '结束节点没有输出，不能连出。' };
  }
  if (targetType === 'start') {
    return { code: 'start_has_no_input', reason: '开始节点没有输入，不能连入。' };
  }

  const requested = typeof connection.sourceHandle === 'string'
    && connection.sourceHandle !== '' && connection.sourceHandle !== 'out'
    ? connection.sourceHandle
    : (typeof connection.port === 'string' ? connection.port : '');
  const outgoing = edgeList.filter((edge) => edge.source === source);
  const sameTarget = edgeList.filter((edge) => edge.source === source && edge.target === target);
  let port = '';

  if (sourceType === 'condition') {
    const allowed = conditionPorts(flowConfig(sourceNode));
    if (requested !== '') {
      if (!allowed.includes(requested)) {
        return {
          code: 'bad_port',
          reason: '端口 "' + requested + '" 不在该条件节点的 cases/default_port 中；可用端口：'
            + (allowed.length ? allowed.join('、') : '（无）') + '。',
        };
      }
      port = requested;
    } else {
      const used = new Set(outgoing.map(edgePortOf).filter((value) => value !== ''));
      port = allowed.find((candidate) => !used.has(candidate)) || '';
      if (port === '') {
        return {
          code: 'no_free_port',
          reason: '该条件节点的所有端口都已连线；请新增 case 端口或删除一条出边。',
        };
      }
    }
    if (outgoing.some((edge) => edgePortOf(edge) === port)) {
      return {
        code: 'port_exists',
        reason: '端口 "' + port + '" 已经用于另一条出边了；同一个条件端口只能连接一条边。',
      };
    }
  } else if (sameTarget.length > 0) {
    return { code: 'duplicate_edge', reason: '这条连接已经存在。' };
  }

  const incoming = edgeList.filter((edge) => edge.target === target);
  if (incoming.length >= 1 && targetType !== 'join') {
    return {
      code: 'merge_without_join',
      reason: '目标节点 ' + target + ' 已有输入，只有 join 可以多入；请先创建配对 join 合并分支。',
    };
  }

  if (sourceType !== 'condition') {
    const declared = declaredForkIds(nodeList);
    if (outgoing.length >= 1 && !declared.has(source)) {
      return {
        code: 'fork_requires_join',
        reason: '多出口需要唯一配对 join 声明 fork：请先创建 join 并把 fork_id 设为 '
          + source + '，或只保留一条出边。',
      };
    }
  }
  if (createsCycle(edgeList, source, target)) {
    return { code: 'cycle', reason: '这条连接会形成环，工作流必须是无环图。' };
  }
  return { ok: true, code: 'ok', reason: '', port: sourceType === 'condition' ? port : '' };
}

export function canConnect(nodes, edges, connection) {
  const problem = connectionProblem(nodes, edges, connection);
  return problem.ok === true ? problem : { ...problem, ok: false };
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

// ---------------------------------------------------------------------------
// region analysis (dominance / post-dominance, as the Go validator computes it)
// ---------------------------------------------------------------------------
function buildGraph(nodes, edges) {
  const index = new Map();
  nodes.forEach((node, position) => index.set(node.id, position));
  const adjacency = new Map(nodes.map((node) => [node.id, new Set()]));
  const reverse = new Map(nodes.map((node) => [node.id, new Set()]));
  for (const edge of edges || []) {
    if (!adjacency.has(edge.source) || !adjacency.has(edge.target)) {
      continue;
    }
    adjacency.get(edge.source).add(edge.target);
    reverse.get(edge.target).add(edge.source);
  }
  const indegree = new Map(nodes.map((node) => [node.id, reverse.get(node.id).size]));
  const queue = nodes.filter((node) => indegree.get(node.id) === 0).map((node) => node.id);
  const topo = [];
  while (queue.length > 0) {
    const current = queue.shift();
    topo.push(current);
    const targets = [...adjacency.get(current)].sort((a, b) => index.get(a) - index.get(b));
    for (const target of targets) {
      indegree.set(target, indegree.get(target) - 1);
      if (indegree.get(target) === 0) {
        queue.push(target);
      }
    }
  }
  const dom = new Map();
  const pdom = new Map();
  if (topo.length === nodes.length) {
    for (const id of topo) {
      const parents = [...reverse.get(id)];
      if (parents.length === 0) {
        dom.set(id, new Set([id]));
        continue;
      }
      let common = null;
      for (const parent of parents) {
        const values = dom.get(parent);
        common = common === null
          ? new Set(values)
          : new Set([...common].filter((value) => values.has(value)));
      }
      common.add(id);
      dom.set(id, common);
    }
    for (const id of [...topo].reverse()) {
      const children = [...adjacency.get(id)];
      if (children.length === 0) {
        pdom.set(id, new Set([id]));
        continue;
      }
      let common = null;
      for (const child of children) {
        const values = pdom.get(child);
        common = common === null
          ? new Set(values)
          : new Set([...common].filter((value) => values.has(value)));
      }
      common.add(id);
      pdom.set(id, common);
    }
  }
  return { index, adjacency, reverse, topo, dom, pdom };
}

function reachableFrom(adjacency, id) {
  const seen = new Set();
  const pending = [id];
  while (pending.length > 0) {
    const current = pending.pop();
    for (const next of adjacency.get(current) || []) {
      if (!seen.has(next)) {
        seen.add(next);
        pending.push(next);
      }
    }
  }
  return seen;
}

function reaches(adjacency, id, target) {
  return reachableFrom(adjacency, id).has(target);
}

/**
 * Every declared (fork, join) region with the nodes that live strictly inside
 * it. Used by the source selector to hide pre-join branch outputs.
 */
export function regionPairs(definition) {
  const nodes = definition && Array.isArray(definition.nodes) ? definition.nodes : [];
  const edges = definition && Array.isArray(definition.edges) ? definition.edges : [];
  if (nodes.length === 0) {
    return [];
  }
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const graph = buildGraph(nodes, edges);
  const complete = graph.topo.length === nodes.length;
  const pairs = [];
  for (const joinNode of nodes) {
    if (joinNode.type !== 'join') {
      continue;
    }
    let forkId = configOf(joinNode).fork_id;
    if (typeof forkId !== 'string' || forkId === '' || !byId.has(forkId)) {
      const pairedCondition = nodes.find(
        (node) => node.type === 'condition' && configOf(node).join_id === joinNode.id,
      );
      forkId = pairedCondition ? pairedCondition.id : '';
    }
    if (forkId === '' || !byId.has(forkId) || forkId === joinNode.id) {
      continue;
    }
    const inside = new Set();
    for (const node of nodes) {
      if (node.id === forkId || node.id === joinNode.id) {
        continue;
      }
      let isInside;
      if (complete && graph.dom.has(node.id) && graph.pdom.has(node.id)) {
        isInside = graph.dom.get(node.id).has(forkId) && graph.pdom.get(node.id).has(joinNode.id);
      } else {
        isInside = reaches(graph.adjacency, forkId, node.id)
          && reaches(graph.reverse, joinNode.id, node.id);
      }
      if (isInside) {
        inside.add(node.id);
      }
    }
    pairs.push({ forkId, joinId: joinNode.id, inside });
  }
  return pairs;
}

/**
 * Dominator sets for a complete acyclic definition, or null when the graph is
 * still being authored (a missing connection makes dominance unknowable).
 * ``dominates`` is used by the source selector to avoid offering an upstream
 * value that a skipped path could leave unset.
 */
export function dominanceOf(definition) {
  const nodes = definition && Array.isArray(definition.nodes) ? definition.nodes : [];
  const edges = definition && Array.isArray(definition.edges) ? definition.edges : [];
  if (nodes.length === 0) {
    return null;
  }
  const graph = buildGraph(nodes, edges);
  if (graph.topo.length !== nodes.length) {
    return null;
  }
  return graph.dom;
}

/** Node ids that must be read through their paired join, never directly. */
export function branchNodeIds(definition) {
  const branch = new Set();
  for (const pair of regionPairs(definition)) {
    for (const id of pair.inside) {
      branch.add(id);
    }
  }
  return branch;
}

// ---------------------------------------------------------------------------
// regionProblems: a local mirror of the server's hard rules with repair hints
// ---------------------------------------------------------------------------
function problem(code, pointer, message, extra) {
  return {
    code,
    pointer: pointer || '',
    message,
    nodeId: extra && extra.nodeId ? extra.nodeId : '',
    edgeIndex: extra && typeof extra.edgeIndex === 'number' ? extra.edgeIndex : null,
  };
}

function sortedStrings(values) {
  return [...values].sort();
}

function sameList(a, b) {
  return JSON.stringify(a) === JSON.stringify(b);
}

function invalidDefinition() {
  return [problem('bad_document', '', '画布定义必须包含 nodes 与 edges 数组。')];
}

/**
 * Why the current definition is not a publishable F04B graph (empty when it is).
 *
 * Mirrors Go's validation stages 2–4 (identity, DAG properties, structured
 * regions) in detection order and returns locatable problems:
 *   {code, message, pointer?, nodeId?, edgeIndex?}
 * Stage 5 (references, types, missing values) is server-owned: the canvas stays
 * silent there instead of pretending to be the authority.
 */
export function regionProblems(definition) {
  const problems = [];
  if (!definition || typeof definition !== 'object'
      || !Array.isArray(definition.nodes) || !Array.isArray(definition.edges)) {
    return invalidDefinition();
  }
  const nodes = definition.nodes;
  const edges = definition.edges;

  // Stage 2: identity.
  const seen = new Set();
  let identityBroken = false;
  nodes.forEach((node, position) => {
    const id = node && node.id;
    if (typeof id !== 'string' || id === '') {
      problems.push(problem('bad_id', '/nodes/' + position, '节点缺少合法的 id。'));
      identityBroken = true;
      return;
    }
    if (seen.has(id)) {
      problems.push(problem('duplicate_id', '/nodes/' + position,
        '节点 id "' + id + '" 重复了；每个节点必须有唯一 id。', { nodeId: id }));
      identityBroken = true;
    }
    seen.add(id);
  });
  edges.forEach((edge, position) => {
    if (!edge || !seen.has(edge.source) || !seen.has(edge.target)) {
      problems.push(problem('unknown_endpoint', '/edges/' + position,
        '边 #' + position + ' 引用了不存在的节点。', {
          nodeId: edge && seen.has(edge.source) ? edge.source : '',
          edgeIndex: position,
        }));
      identityBroken = true;
    }
  });
  if (identityBroken) {
    return problems;
  }
  const starts = nodes.filter((node) => node.type === 'start');
  const ends = nodes.filter((node) => node.type === 'end');
  if (starts.length !== 1 || ends.length !== 1) {
    problems.push(problem('start_end_count', '',
      '画布必须恰好有一个开始节点和一个结束节点（当前 ' + starts.length + ' 个开始、'
      + ends.length + ' 个结束）。'));
    return problems;
  }

  // Stage 3: DAG properties.
  const graph = buildGraph(nodes, edges);
  if (graph.topo.length !== nodes.length) {
    problems.push(problem('cycle', '', '连接形成了环；工作流必须是有向无环图。'));
    return problems;
  }
  const reachable = reachableFrom(graph.adjacency, starts[0].id);
  const canReachEnd = reachableFrom(graph.reverse, ends[0].id);
  nodes.forEach((node, position) => {
    if (node.id !== starts[0].id && !reachable.has(node.id)) {
      problems.push(problem('unreachable_node', '/nodes/' + position,
        '节点 ' + node.id + ' 无法从开始节点到达。', { nodeId: node.id }));
    }
    if (node.id !== ends[0].id && !canReachEnd.has(node.id)) {
      problems.push(problem('cannot_reach_end', '/nodes/' + position,
        '节点 ' + node.id + ' 无法到达结束节点。', { nodeId: node.id }));
    }
    if (graph.reverse.get(node.id).size >= 2 && node.type !== 'join') {
      problems.push(problem('merge_without_join', '/nodes/' + position,
        '节点 ' + node.id + ' 有多个上游分支；需要先创建配对 join 汇合，不能直接在普通节点合并。',
        { nodeId: node.id }));
    }
  });
  if (problems.length > 0) {
    return problems;
  }

  // Stage 4a: structural pairing.
  const index = graph.index;
  const declaredForks = new Set();
  for (const node of nodes) {
    if (node.type !== 'join') {
      continue;
    }
    const forkId = configOf(node).fork_id;
    if (typeof forkId === 'string' && forkId !== '') {
      declaredForks.add(forkId);
    }
  }
  nodes.forEach((node, position) => {
    if (node.type !== 'condition') {
      return;
    }
    const joinId = configOf(node).join_id;
    const joinPosition = index.get(joinId);
    let valid = joinPosition !== undefined;
    if (valid) {
      const joinNode = nodes[joinPosition];
      if (joinNode.type !== 'join' || configOf(joinNode).fork_id !== node.id) {
        valid = false;
      }
    }
    if (!valid) {
      problems.push(problem('invalid_region', '/nodes/' + position + '/config/join_id',
        '条件节点 ' + node.id + ' 的 join_id 必须指向唯一配对 join，且该 join 的 fork_id 必须指回 '
        + node.id + '（双向配对）。', { nodeId: node.id }));
    }
  });
  nodes.forEach((node, position) => {
    if (node.type !== 'join') {
      return;
    }
    const at = '/nodes/' + position + '/config/fork_id';
    const forkId = configOf(node).fork_id;
    const forkPosition = index.get(forkId);
    if (forkPosition === undefined || forkId === node.id) {
      problems.push(problem('invalid_region', at,
        'join ' + node.id + ' 的 fork_id 必须指向一个真实存在的分叉节点。', { nodeId: node.id }));
      return;
    }
    const forkNode = nodes[forkPosition];
    if (forkNode.type === 'condition') {
      if (configOf(forkNode).join_id !== node.id) {
        problems.push(problem('invalid_region', at,
          'join ' + node.id + ' 声明 fork_id=' + forkId + '，但该条件节点的 join_id 没有指回 '
          + node.id + '（双向配对）。', { nodeId: node.id }));
      }
    } else if (graph.adjacency.get(forkId).size < 2) {
      problems.push(problem('invalid_region', at,
        'join ' + node.id + ' 声明的 fork_id=' + forkId + ' 只有一条出边；普通 fork 必须至少分出两条分支。',
        { nodeId: node.id }));
    }
  });
  nodes.forEach((node, position) => {
    if (node.type === 'condition' || node.type === 'join') {
      return;
    }
    if (graph.adjacency.get(node.id).size >= 2 && !declaredForks.has(node.id)) {
      problems.push(problem('invalid_region', '/nodes/' + position,
        '节点 ' + node.id + ' 有多条出边，但没有任何 join 把 fork_id 声明为它；请创建配对 join。',
        { nodeId: node.id }));
    }
  });
  if (problems.length > 0) {
    return problems;
  }

  // Stage 4b: condition exit ports.
  const conditionEdges = [];
  nodes.forEach((node, position) => {
    if (node.type !== 'condition') {
      return;
    }
    const config = configOf(node);
    const allowed = conditionPorts(config);
    const allowedSet = new Set(allowed);
    const actual = new Set();
    edges.forEach((edge, edgePosition) => {
      if (edge.source !== node.id) {
        return;
      }
      const port = typeof edge.port === 'string' && edge.port !== '' ? edge.port : 'default';
      actual.add(port);
      if (!allowedSet.has(port)) {
        problems.push(problem('bad_port', '/edges/' + edgePosition,
          '边 ' + node.id + '→' + edge.target + ' 的端口 "' + port
          + '" 与 cases/default_port 不一致；可用端口：'
          + (allowed.length ? allowed.join('、') : '（无）') + '。',
          { nodeId: node.id, edgeIndex: edgePosition }));
      }
    });
    for (const port of sortedStrings(allowedSet)) {
      if (!actual.has(port)) {
        problems.push(problem('bad_port', '/nodes/' + position + '/config/cases',
          '条件节点 ' + node.id + ' 的端口 "' + port
          + '" 没有对应出边；每个 case 端口和 default_port 都必须连线。', { nodeId: node.id }));
      }
    }
  });
  edges.forEach((edge, edgePosition) => {
    const sourceNode = nodes[index.get(edge.source)];
    if (!sourceNode || sourceNode.type === 'condition') {
      return;
    }
    if (Object.prototype.hasOwnProperty.call(edge, 'port') && edge.port !== 'default') {
      problems.push(problem('bad_port', '/edges/' + edgePosition,
        '只有条件节点的出边可以设置 port；' + edge.source + ' 的这条出边应删除端口。',
        { nodeId: edge.source, edgeIndex: edgePosition }));
    }
  });
  if (problems.length > 0) {
    return problems;
  }

  // Stage 4c: cross-region edges.
  const regions = nodes
    .filter((node) => node.type === 'join')
    .map((node) => [configOf(node).fork_id, node.id])
    .sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : a[1] < b[1] ? -1 : a[1] > b[1] ? 1 : 0));
  for (const [forkId, joinId] of regions) {
    const inside = new Set();
    for (const nodeId of index.keys()) {
      if (nodeId === forkId || nodeId === joinId) {
        continue;
      }
      if (graph.dom.get(nodeId).has(forkId) && graph.pdom.get(nodeId).has(joinId)) {
        inside.add(nodeId);
      }
    }
    edges.forEach((edge, edgePosition) => {
      const sourceInside = inside.has(edge.source);
      const targetInside = inside.has(edge.target);
      let illegal = false;
      if (sourceInside && !targetInside && edge.target !== joinId) {
        illegal = true;
      } else if (targetInside && !sourceInside && edge.source !== forkId) {
        illegal = true;
      } else if (edge.source === forkId && edge.target !== joinId && !targetInside) {
        illegal = true;
      } else if (edge.target === joinId && edge.source !== forkId && !sourceInside) {
        illegal = true;
      }
      if (illegal) {
        problems.push(problem('cross_region_edge', '/edges/' + edgePosition,
          '边 ' + edge.source + '→' + edge.target + ' 跨越了 region 边界：'
          + '它必须连到配对 join（' + joinId + '），或在 join 前汇合；region 的入口是 '
          + forkId + '。', { nodeId: edge.source, edgeIndex: edgePosition }));
      }
    });
  }
  if (problems.length > 0) {
    return problems;
  }

  // Stage 4d: join source coverage.
  nodes.forEach((node, position) => {
    if (node.type !== 'join') {
      return;
    }
    const config = configOf(node);
    const required = Array.isArray(config.required_sources) ? config.required_sources : [];
    const optional = Array.isArray(config.optional_sources) ? config.optional_sources : [];
    const declared = [...required, ...optional];
    const actual = sortedStrings(new Set(
      edges.filter((edge) => edge.target === node.id).map((edge) => edge.source),
    ));
    const uniqueDeclared = new Set(declared);
    const overlap = required.some((id) => optional.includes(id));
    if (declared.length !== uniqueDeclared.size || overlap
        || !sameList(sortedStrings(uniqueDeclared), actual)) {
      problems.push(problem('bad_join_sources', '/nodes/' + position + '/config/required_sources',
        'join ' + node.id + ' 的 required_sources/optional_sources 必须恰好覆盖所有直接前驱且互斥；'
        + '当前声明 ' + JSON.stringify(declared) + '，直接前驱是 ' + JSON.stringify(actual) + '。',
        { nodeId: node.id }));
    }
  });
  return problems;
}

// ---------------------------------------------------------------------------
// canvas mutation helpers
// ---------------------------------------------------------------------------
function nextNodeId(nodes, type) {
  const used = new Set((nodes || []).map((node) => node.id));
  let counter = (nodes || []).length + 1;
  let id = type + counter;
  while (used.has(id)) {
    counter += 1;
    id = type + counter;
  }
  return id;
}

export function addNode(graphNodes, graphEdges, type, layout) {
  const nodes = graphNodes || [];
  if (!NODE_TYPES.includes(type)) {
    return { ok: false, code: 'unknown_type', reason: '画布不支持节点类型 ' + type + '。' };
  }
  if (!REPEATABLE_NODE_TYPES.includes(type)
      && nodes.some((node) => typeOfNode(node) === type)) {
    return {
      ok: false,
      code: 'node_exists',
      reason: '画布只允许一个' + (NODE_LABELS[type] || type) + '节点。',
    };
  }
  const id = type === 'start' || type === 'end' ? type : nextNodeId(nodes, type);
  const sameType = nodes.filter((node) => typeOfNode(node) === type).length;
  const position = DEFAULT_POSITIONS[type]
    ? { ...DEFAULT_POSITIONS[type], y: DEFAULT_POSITIONS[type].y + 120 * sameType }
    : { x: 280 + 240 * nodes.length, y: 320 };
  return {
    ok: true,
    node: {
      id,
      type: 'default',
      label: NODE_LABELS[type],
      class: 'orchara-node orchara-node-' + type,
      position,
      data: {
        label: NODE_LABELS[type],
        nodeType: type,
        config: clone(defaultNode(type, id).config),
        ...(type === 'condition' ? { ports: conditionPorts(defaultNode(type, id).config) } : {}),
      },
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
