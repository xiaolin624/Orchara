/*
 * F04B/T083 export sanitizer.
 *
 * Export downloads {definition, layout} as before, but now with a *positive*
 * allowlist: only the contract's per-node-type config keys survive. Unknown
 * node types and unknown config keys are dropped and reported in ``dropped``
 * (path + reason) instead of being smuggled into the downloaded draft, so the
 * export cannot carry a capability the canvas never displayed (UX U5/U6).
 *
 * The negative secret denylist stays as a second line of defence: a credential,
 * endpoint or run-input key anywhere in the document refuses the export
 * outright. The filter is not an authorization boundary (the server still
 * validates everything it receives).
 */

import { escapePointer } from './sources.js';

const SENSITIVE_KEY = /(secret|token|password|passwd|api[_-]?key|credential|bearer|oauth|memory|personal|run[_-]?input|endpoint)/i;

/** Contract config keys per node type; T091 extends this table for the rest. */
export const CONFIG_ALLOWLIST = {
  start: [],
  python: ['tool_version'],
  agent: [
    'model_profile_version',
    'instructions',
    'tool_versions',
    'subapp_versions',
    'max_turns',
  ],
  condition: ['join_id', 'default_port', 'cases'],
  join: ['mode', 'fork_id', 'required_sources', 'optional_sources'],
  call_app: ['application_version'],
  end: [],
};

const TOP_LEVEL_KEYS = ['dsl_version', 'input_schema', 'output_schema', 'nodes', 'edges', 'policy'];
const NODE_KEYS = ['id', 'type', 'inputs', 'output_schema', 'config'];
const EDGE_KEYS = ['source', 'target', 'port'];
const CASE_KEYS = ['input', 'op', 'value', 'port'];

export class ExportRefused extends Error {
  constructor(code, path, message) {
    super(message);
    this.name = 'ExportRefused';
    this.code = code;
    this.path = path;
  }
}

function walk(value, path, visit) {
  if (Array.isArray(value)) {
    value.forEach((item, index) => walk(item, path + '/' + index, visit));
    return;
  }
  if (value && typeof value === 'object') {
    for (const key of Object.keys(value)) {
      visit(key, path + '/' + key);
      walk(value[key], path + '/' + key, visit);
    }
  }
}

export function assertExportSafe(definition, layout) {
  walk(definition, '#/definition', (key, path) => {
    if (SENSITIVE_KEY.test(key)) {
      throw new ExportRefused(
        'sensitive_key',
        path,
        '导出被拒绝：definition 含有凭据/端点/运行输入/记忆类字段 ' + JSON.stringify(key) + '。',
      );
    }
  });
  walk(layout, '#/layout', (key, path) => {
    if (SENSITIVE_KEY.test(key)) {
      throw new ExportRefused(
        'sensitive_key',
        path,
        '导出被拒绝：layout 含有凭据/端点/运行输入/记忆类字段 ' + JSON.stringify(key) + '。',
      );
    }
  });
  return true;
}

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function sanitizeCase(item, path, dropped) {
  if (!item || typeof item !== 'object' || Array.isArray(item)) {
    return item;
  }
  const cleaned = {};
  for (const key of Object.keys(item)) {
    if (CASE_KEYS.includes(key)) {
      cleaned[key] = item[key];
    } else {
      dropped.push({ path: path + '/' + escapePointer(key), reason: 'unknown_config_key' });
    }
  }
  return cleaned;
}

/**
 * Strip a definition down to the documented shape. Returns the cleaned
 * definition and a deterministic ``dropped`` report (document order, then
 * sorted config keys) so the caller can show exactly what was omitted.
 */
export function sanitizeDefinition(definition) {
  const dropped = [];
  const source = definition && typeof definition === 'object' && !Array.isArray(definition)
    ? definition
    : {};
  const cleaned = {};
  for (const key of Object.keys(source)) {
    if (TOP_LEVEL_KEYS.includes(key)) {
      cleaned[key] = source[key];
    } else {
      dropped.push({ path: '/' + escapePointer(key), reason: 'unknown_field' });
    }
  }

  const sourceNodes = Array.isArray(source.nodes) ? source.nodes : [];
  cleaned.nodes = sourceNodes.map((node, index) => {
    const target = {};
    const entry = node && typeof node === 'object' && !Array.isArray(node) ? node : {};
    for (const key of Object.keys(entry)) {
      if (NODE_KEYS.includes(key)) {
        target[key] = entry[key];
      } else {
        dropped.push({ path: '/nodes/' + index + '/' + escapePointer(key), reason: 'unknown_field' });
      }
    }
    const type = typeof entry.type === 'string' ? entry.type : '';
    const allow = CONFIG_ALLOWLIST[type];
    const config = entry.config && typeof entry.config === 'object' && !Array.isArray(entry.config)
      ? entry.config
      : {};
    const cleanConfig = {};
    for (const key of Object.keys(config).sort()) {
      if (!allow) {
        dropped.push({
          path: '/nodes/' + index + '/config/' + escapePointer(key),
          reason: 'unknown_node_type',
        });
        continue;
      }
      if (!allow.includes(key)) {
        dropped.push({
          path: '/nodes/' + index + '/config/' + escapePointer(key),
          reason: 'unknown_config_key',
        });
        continue;
      }
      cleanConfig[key] = config[key];
    }
    if (allow && type === 'condition' && Array.isArray(cleanConfig.cases)) {
      cleanConfig.cases = cleanConfig.cases.map((item, position) =>
        sanitizeCase(item, '/nodes/' + index + '/config/cases/' + position, dropped));
    }
    if (Object.prototype.hasOwnProperty.call(entry, 'config')) {
      target.config = cleanConfig;
    }
    return target;
  });

  const sourceEdges = Array.isArray(source.edges) ? source.edges : [];
  cleaned.edges = sourceEdges.map((edge, index) => {
    const entry = edge && typeof edge === 'object' && !Array.isArray(edge) ? edge : {};
    const target = {};
    for (const key of Object.keys(entry)) {
      if (EDGE_KEYS.includes(key)) {
        target[key] = entry[key];
      } else {
        dropped.push({ path: '/edges/' + index + '/' + escapePointer(key), reason: 'unknown_field' });
      }
    }
    if (!Object.prototype.hasOwnProperty.call(target, 'port')) {
      delete target.port;
    }
    return target;
  });
  return { definition: cleaned, dropped };
}

export function exportPayload(definition, layout) {
  assertExportSafe(definition, layout);
  const sanitized = sanitizeDefinition(definition);
  const payload = {
    definition: sanitized.definition,
    layout: clone(layout && typeof layout === 'object' ? layout : {}),
  };
  if (sanitized.dropped.length > 0) {
    payload.dropped = sanitized.dropped;
  }
  return payload;
}

export function exportJSON(definition, layout) {
  return JSON.stringify(exportPayload(definition, layout), null, 2);
}

export function exportFilename(applicationId) {
  return 'orchara-canvas-' + (applicationId || 'draft') + '.json';
}
