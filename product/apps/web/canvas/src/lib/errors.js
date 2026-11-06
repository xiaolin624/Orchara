/*
 * F04B/T083 validation-error mapping.
 *
 * The server's located errors are {node_id?, code, pointer, message}. The canvas
 * must select the node and focus the config field instead of only printing the
 * HTTP status, and an illegal *edge* must highlight the connection: ``/edges/<i>``
 * is resolved through the saved definition to its source/target and canvas edge
 * id. The module is pure so the Node test can pin the mapping, including errors
 * that name a node but carry an out-of-date pointer and document-level errors.
 *
 * Every mapped error also carries a Chinese repair hint (UX §2: explain "where
 * this cannot connect and how to fix it", never a bare 422). This is guidance
 * only; the Go validator stays the authority.
 */

import { edgeIdFor } from './dsl.js';
import { unescapePointer } from './sources.js';

export function nodeIndexFromPointer(pointer) {
  const match = /^\/nodes\/(\d+)(?:\/|$)/.exec(String(pointer || ''));
  return match ? Number(match[1]) : null;
}

export function edgeIndexFromPointer(pointer) {
  const match = /^\/edges\/(\d+)(?:\/|$)/.exec(String(pointer || ''));
  return match ? Number(match[1]) : null;
}

export function nodeIdForError(definition, error) {
  const nodes = definition && Array.isArray(definition.nodes) ? definition.nodes : [];
  if (error && error.node_id) {
    return nodes.some((node) => node.id === error.node_id) ? error.node_id : '';
  }
  const index = nodeIndexFromPointer(error && error.pointer);
  if (index !== null && index >= 0 && index < nodes.length) {
    return nodes[index].id;
  }
  const edgeIndex = edgeIndexFromPointer(error && error.pointer);
  const edges = definition && Array.isArray(definition.edges) ? definition.edges : [];
  if (edgeIndex !== null && edgeIndex >= 0 && edgeIndex < edges.length) {
    return edges[edgeIndex].source || '';
  }
  return '';
}

/**
 * The config-panel field key for an error. The canvas uses these keys as
 * ``data-field`` attributes so a click can focus the exact input.
 */
export function fieldForError(pointer) {
  const text = String(pointer || '');
  const index = nodeIndexFromPointer(text);
  if (index === null) {
    const parts = text.split('/').filter((part) => part !== '');
    return parts.length > 0 ? unescapePointer(parts[0]) : 'document';
  }
  const parts = text.split('/').filter((part) => part !== '').slice(2).map(unescapePointer);
  if (parts.length === 0) {
    return 'node';
  }
  if (parts[0] === 'inputs') {
    return parts.length > 1 ? 'inputs.' + parts[1] : 'inputs';
  }
  if (parts[0] === 'config') {
    return parts.length > 1 ? 'config.' + parts[1] : 'config';
  }
  if (parts[0] === 'output_schema') {
    return parts.length > 1 ? 'output_schema.' + parts.slice(1).join('.') : 'output_schema';
  }
  return parts.join('.');
}

/** Chinese repair guidance for the stable validation codes the canvas sees. */
export function fixHintForCode(code) {
  switch (String(code || '')) {
    case 'cross_region_edge':
      return '这条连接跨越了 region 边界；请连到配对 join 或删除这条边。';
    case 'invalid_region':
      return '条件节点与 join 必须双向配对（condition.join_id ↔ join.fork_id）；请检查配对面板。';
    case 'bad_port':
      return '端口与 cases/default_port 不一致；请让每条出边的端口都出现在条件端口列表里。';
    case 'bad_join_sources':
      return 'join 来源必须覆盖所有直接前驱且 required/optional 互斥；请勾选完整。';
    case 'merge_without_join':
      return '多个上游分支不能直接合并；请先创建配对 join 再连入。';
    case 'fork_requires_join':
      return '多出口需要唯一配对 join 声明 fork；请创建 join 并把 fork_id 指向该节点。';
    case 'port_exists':
      return '同一个条件端口只能连接一条出边；请换一个端口。';
    case 'no_free_port':
      return '条件节点的端口都已连线；请新增 case/default_port 或删除一条出边。';
    case 'missing_value':
      return '这个值在某些执行路径上可能没有被赋值；请改读 join 后按来源区分的结果。';
    case 'start_end_count':
      return '画布必须恰好有一个开始节点和一个结束节点。';
    case 'cycle':
      return '连接形成了环；工作流必须是有向无环图，请删除回边。';
    case 'unreachable_node':
      return '该节点无法从开始节点到达；请补上入口连接。';
    case 'cannot_reach_end':
      return '该节点无法到达结束节点；请补上通往 join/结束节点的连接。';
    case 'dependency_not_ready':
      return '引用的工具/模型/子应用版本未就绪；请选择 ready 且已批准的固定版本。';
    case 'duplicate_id':
      return '节点 id 重复；请删除重复节点或重新添加。';
    case 'unknown_endpoint':
      return '连接引用了不存在的节点；请删除这条边并重新连接。';
    case 'non_ancestor_ref':
      return '只能引用当前节点上游的值；请改用合法来源。';
    case 'unknown_ref':
      return '引用的节点或字段不存在；请重新选择合法来源。';
    case 'bad_pointer':
      return '引用的字段不在上游输出 Schema 中；请检查指针或补全输出 Schema。';
    case 'type_mismatch':
      return '比较值的类型与输入不一致；条件比较不做类型强转。';
    default:
      return '请按服务端给出的 pointer 定位节点/边并修正配置；画布不会代替服务端校验。';
  }
}

export function mapValidationErrors(definition, errors) {
  const list = Array.isArray(errors) ? errors : [];
  const nodes = definition && Array.isArray(definition.nodes) ? definition.nodes : [];
  const edges = definition && Array.isArray(definition.edges) ? definition.edges : [];
  return list.map((error, index) => {
    const pointer = error && error.pointer ? error.pointer : '';
    const edgeIndex = edgeIndexFromPointer(pointer);
    const edge = edgeIndex !== null && edgeIndex >= 0 && edgeIndex < edges.length
      ? edges[edgeIndex]
      : null;
    const nodeId = nodeIdForError(definition, error);
    let field = fieldForError(pointer);
    if (edge) {
      const sourceNode = nodes.find((node) => node.id === edge.source);
      field = sourceNode && sourceNode.type === 'condition' ? 'edge.port' : 'edge';
    }
    return {
      index,
      nodeId,
      field,
      pointer,
      code: (error && error.code) || 'validation_failed',
      message: (error && error.message) || '服务端拒绝了这个定义',
      hint: fixHintForCode(error && error.code),
      edgeIndex: edge ? edgeIndex : null,
      edgeId: edge ? edgeIdFor(edge, edgeIndex) : '',
      source: edge ? edge.source : '',
      target: edge ? edge.target : '',
      documentLevel: nodeId === '' && edge === null,
    };
  });
}

export function errorsForNode(mapped, nodeId) {
  return (mapped || []).filter((error) => error.nodeId === nodeId);
}

export function documentLevelErrors(mapped) {
  return (mapped || []).filter((error) => error.documentLevel);
}
