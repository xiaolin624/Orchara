/*
 * F04B/T089 call_app authoring helpers.
 *
 * A call_app node pins one published child application *version* and maps an
 * explicit, minimal input object from bindings that are exactly one of:
 *
 *   {literal: <JSON>}          a developer-authored constant
 *   {ref: "input#/a"}          an application-input JSON Pointer
 *   {ref: "node:<id>#/a"}      an upstream node's declared output
 *
 * The panel offers only versions the catalog/version endpoints return (already
 * ACL-filtered by the server, never "latest"); the child's frozen input/output
 * schemas come from the published version's manifest definition, and the node's
 * own output_schema stays editable (defaulted from the child schema).
 *
 * The Go publication gate is the authority on the mapping (it re-checks the
 * frozen child schemas and locates io_mapping_* at `/nodes/<i>/inputs/<name>`).
 * The local hints here only explain the same static facts early; an unknown
 * static type is never guessed.
 *
 * These helpers are a copy of the pure half of the editor sidebar's call_app
 * code (`apps/web/src/assets/editor.js`): the sidebar is a classic script and
 * cannot import this ESM module, and it must keep its documented behaviour, so
 * the page keeps its own copy. Only the canvas-owned surface (picker/rows/local
 * hints) is added here.
 *
 * No Vue, no DOM, no fetch: executed directly by the Node module tests.
 */

import { escapePointer, sourceType, isLegalSource } from './sources.js';

export const CALL_APP_NODE_ID = /^[A-Za-z][A-Za-z0-9_-]{0,63}$/;
export const CALL_APP_VERSION_ID =
  /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/;
export const CALL_APP_NODE_REF = /^[A-Za-z][A-Za-z0-9_-]*#\/.*$/;

/** The binding modes the panel offers, in display order. */
export const CALL_APP_BINDING_MODES = ['none', 'input', 'literal', 'node'];

export function callAppFailure(code, message) {
  const failure = new Error(message);
  failure.code = 'call_app.' + code;
  return failure;
}

// ---------------------------------------------------------------------------
// catalog / version views
// ---------------------------------------------------------------------------
/** The applications a picker may offer: the server already applied the ACL. */
export function callAppApplicationOptions(payload) {
  const items = payload && Array.isArray(payload.items) ? payload.items : [];
  return items
    .map((item) => ({ id: String(item.id), name: String(item.name || item.id) }))
    .sort((left, right) => (left.name < right.name ? -1 : left.name > right.name ? 1 : 0));
}

/** The published versions of one application, newest first, as the API lists them. */
export function callAppVersionOptions(payload) {
  const items = payload && Array.isArray(payload.items) ? payload.items : [];
  return items
    .filter((item) => item && CALL_APP_VERSION_ID.test(String(item.id || '')))
    .map((item) => ({
      id: String(item.id),
      applicationId: String(item.application_id || ''),
      versionNo: Number(item.version_no) || 0,
      manifestSha256: String(item.manifest_sha256 || ''),
      label: 'v' + String(item.version_no) + ' · ' +
        String(item.manifest_sha256 || '').slice(0, 12),
      version: item,
    }));
}

function manifestOf(versionView) {
  if (!versionView || typeof versionView !== 'object') {
    return null;
  }
  const manifest = versionView.manifest;
  if (manifest && typeof manifest === 'object' && !Array.isArray(manifest)) {
    return manifest;
  }
  if (typeof manifest === 'string') {
    try {
      const parsed = JSON.parse(manifest);
      return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : null;
    } catch (error) {
      return null;
    }
  }
  return null;
}

/**
 * The child version's frozen input/output schemas, or null when the row carries
 * no frozen definition (such a version cannot be called: the mapping would be
 * checked against nothing, and the server reports dependency_not_ready).
 */
export function callAppChildSchemas(versionView) {
  const manifest = manifestOf(versionView);
  const definition = manifest && typeof manifest === 'object' ? manifest.definition : null;
  if (!definition || typeof definition !== 'object' || Array.isArray(definition)) {
    return null;
  }
  const inputSchema = definition.input_schema;
  const outputSchema = definition.output_schema;
  if (!inputSchema || typeof inputSchema !== 'object' || Array.isArray(inputSchema)) {
    return null;
  }
  if (!outputSchema || typeof outputSchema !== 'object' || Array.isArray(outputSchema)) {
    return null;
  }
  return { inputSchema, outputSchema };
}

// ---------------------------------------------------------------------------
// mapping rows
// ---------------------------------------------------------------------------
/** One editable row per declared child input field. */
export function callAppInputRows(inputSchema, mapped) {
  const properties = inputSchema && typeof inputSchema.properties === 'object' && inputSchema.properties
    ? inputSchema.properties
    : {};
  const required = new Set(Array.isArray(inputSchema && inputSchema.required) ? inputSchema.required : []);
  const existing = mapped && typeof mapped === 'object' ? mapped : {};
  return Object.keys(properties).sort().map((field) => {
    const declared = properties[field] && typeof properties[field] === 'object' ? properties[field] : {};
    const binding = existing[field];
    let mode = 'none';
    let value = '';
    if (binding && typeof binding === 'object' && !Array.isArray(binding)) {
      if (Object.prototype.hasOwnProperty.call(binding, 'literal')) {
        mode = 'literal';
        value = JSON.stringify(binding.literal);
      } else if (typeof binding.ref === 'string') {
        if (binding.ref.startsWith('input#')) {
          mode = 'input';
          value = binding.ref.slice('input#'.length) || '/' + field;
        } else if (binding.ref.startsWith('node:')) {
          mode = 'node';
          value = binding.ref.slice('node:'.length);
        }
      }
    }
    return {
      field,
      type: typeof declared.type === 'string' ? declared.type : 'any',
      required: required.has(field),
      mode,
      value,
    };
  });
}

/** One editable row per declared child output field. */
export function callAppOutputRows(outputSchema, declaredOutput) {
  const properties = outputSchema && typeof outputSchema.properties === 'object' && outputSchema.properties
    ? outputSchema.properties
    : {};
  const required = new Set(Array.isArray(outputSchema && outputSchema.required) ? outputSchema.required : []);
  const declaredProperties = declaredOutput && typeof declaredOutput.properties === 'object'
    && declaredOutput.properties
    ? declaredOutput.properties
    : {};
  return Object.keys(properties).sort().map((field) => {
    const declared = properties[field] && typeof properties[field] === 'object' ? properties[field] : {};
    const chosen = declaredProperties[field] && typeof declaredProperties[field] === 'object'
      ? declaredProperties[field]
      : null;
    return {
      field,
      type: typeof declared.type === 'string' ? declared.type : 'string',
      required: required.has(field),
      include: Boolean(chosen),
      declaredType: chosen && typeof chosen.type === 'string' ? chosen.type : '',
    };
  });
}

/**
 * One binding from the panel's mode/value pair, or null for "not mapped".
 *
 * Literal values are JSON (so a string needs quotes, exactly like the DSL); an
 * application-input pointer is normalised to a leading slash; a node reference is
 * `<node id>#<pointer>` and is prefixed with `node:`.
 */
export function callAppBinding(mode, value) {
  const kind = String(mode || 'none');
  const text = String(value === undefined || value === null ? '' : value).trim();
  if (kind === 'none') {
    return null;
  }
  if (kind === 'input') {
    const pointer = text === '' ? '' : (text.startsWith('/') ? text : '/' + text);
    if (pointer === '' || pointer === '/') {
      throw callAppFailure('input_pointer', '应用输入引用需要一个 JSON Pointer，例如 /source');
    }
    return { ref: 'input#' + pointer };
  }
  if (kind === 'node') {
    const reference = text.startsWith('node:') ? text.slice('node:'.length) : text;
    if (!CALL_APP_NODE_REF.test(reference)) {
      throw callAppFailure('node_ref', '节点输出引用必须写成 <节点 id>#/<字段>，例如 python1#/report');
    }
    return { ref: 'node:' + reference };
  }
  if (kind === 'literal') {
    if (text === '') {
      throw callAppFailure('literal', '字面量需要一个 JSON 值（字符串请加引号，例如 "eu"）');
    }
    try {
      return { literal: JSON.parse(text) };
    } catch (error) {
      throw callAppFailure('literal', '字面量不是合法 JSON：' + error.message);
    }
  }
  throw callAppFailure('mode', '未知的映射方式：' + kind);
}

/** Build the call_app node from the panel form. Invalid fields throw located reasons. */
export function callAppNodeFromForm(form) {
  const source = form || {};
  const nodeId = String(source.nodeId === undefined || source.nodeId === null ? '' : source.nodeId).trim();
  if (!CALL_APP_NODE_ID.test(nodeId)) {
    throw callAppFailure('node_id', '节点 id 必须匹配 [A-Za-z][A-Za-z0-9_-]{0,63}');
  }
  const versionId = String(source.versionId === undefined || source.versionId === null
    ? '' : source.versionId).trim();
  if (!CALL_APP_VERSION_ID.test(versionId)) {
    throw callAppFailure('version', '请选择一个已发布版本（固定版本，不接受 latest）');
  }
  const inputs = {};
  for (const row of source.inputRows || []) {
    const binding = callAppBinding(row.mode, row.value);
    if (binding !== null) {
      inputs[row.field] = binding;
    }
  }
  const properties = {};
  const required = [];
  for (const row of source.outputRows || []) {
    if (!row.include) {
      continue;
    }
    properties[row.field] = { type: row.type || 'string' };
    if (row.required) {
      required.push(row.field);
    }
  }
  const outputSchema = { type: 'object', properties };
  if (required.length > 0) {
    outputSchema.required = required.sort();
  }
  return {
    id: nodeId,
    type: 'call_app',
    inputs,
    output_schema: outputSchema,
    config: { application_version: versionId },
  };
}

/** The call_app panel's view of one selected child version. */
export function callAppPanelModel(versionView) {
  const schemas = callAppChildSchemas(versionView);
  if (!schemas) {
    return {
      callable: false,
      reason: '这个版本没有冻结的 definition，不能作为固定子应用调用（服务端会以 dependency_not_ready 拒绝）',
      inputSchema: null,
      outputSchema: null,
      inputRows: [],
      outputRows: [],
    };
  }
  return {
    callable: true,
    reason: '',
    inputSchema: schemas.inputSchema,
    outputSchema: schemas.outputSchema,
    inputRows: callAppInputRows(schemas.inputSchema),
    outputRows: callAppOutputRows(schemas.outputSchema),
  };
}

/** The panel state of one existing call_app node (null when the id is not one). */
export function callAppPrefillFromNode(definition, nodeId, versionView) {
  const nodes = definition && Array.isArray(definition.nodes) ? definition.nodes : [];
  const node = nodes.find((candidate) => candidate && candidate.id === nodeId);
  if (!node || node.type !== 'call_app') {
    return null;
  }
  const versionId = node.config && typeof node.config.application_version === 'string'
    ? node.config.application_version
    : '';
  const schemas = callAppChildSchemas(versionView);
  return {
    nodeId: node.id,
    versionId,
    inputRows: schemas ? callAppInputRows(schemas.inputSchema, node.inputs) : [],
    outputRows: schemas ? callAppOutputRows(schemas.outputSchema, node.output_schema) : [],
  };
}

// ---------------------------------------------------------------------------
// local hints (the server remains the authority)
// ---------------------------------------------------------------------------
function jsonTypeOf(value) {
  if (Array.isArray(value)) {
    return 'array';
  }
  if (value === null) {
    return 'null';
  }
  const type = typeof value;
  if (type === 'number') {
    return Number.isInteger(value) ? 'integer' : 'number';
  }
  return type; // string | boolean | object
}

function normaliseStaticType(type) {
  if (typeof type !== 'string' || type === '') {
    return 'unknown';
  }
  if (type.startsWith('array')) {
    return 'array';
  }
  if (type.includes('|') || type.includes('<')) {
    return 'unknown';
  }
  return type;
}

function schemaTypeOf(schema) {
  if (!schema || typeof schema !== 'object') {
    return 'unknown';
  }
  if (typeof schema.type === 'string') {
    return schema.type;
  }
  return 'unknown';
}

function typeAccepts(childType, bindingType) {
  if (childType === 'unknown' || childType === 'any' || bindingType === 'unknown') {
    return true; // never guess: the server type gate decides
  }
  if (childType === 'number') {
    return bindingType === 'number' || bindingType === 'integer';
  }
  if (childType === 'integer') {
    return bindingType === 'integer';
  }
  if (childType === 'array') {
    return bindingType === 'array';
  }
  return childType === bindingType;
}

function bindingShape(binding) {
  if (!binding || typeof binding !== 'object' || Array.isArray(binding)) {
    return null;
  }
  if (Object.prototype.hasOwnProperty.call(binding, 'literal')) {
    return { kind: 'literal', value: binding.literal };
  }
  if (typeof binding.ref === 'string' && binding.ref !== '') {
    return { kind: 'ref', ref: binding.ref };
  }
  return null;
}

function describeBindingType(definition, shape) {
  if (!shape) {
    return 'unknown';
  }
  if (shape.kind === 'literal') {
    return jsonTypeOf(shape.value);
  }
  return normaliseStaticType(sourceType(definition, shape.ref));
}

/**
 * Located local hints for one call_app node: the pinned version must be a fixed
 * UUID with a frozen definition, every mapped field must exist in the child's
 * frozen input schema, required child inputs must be mapped, and a binding whose
 * statically known type the child schema refuses is flagged. A reference that is
 * not a legal source of this node is flagged as such.
 *
 * Returned items are `{code, field, message}`; `field` uses the panel/data-field
 * vocabulary (`config.application_version`, `inputs.<name>`), while the server's
 * authoritative pointer is `/nodes/<i>/inputs/<name>`.
 */
export function callAppProblems(node, definition, schemas) {
  const problems = [];
  const config = node && node.config && typeof node.config === 'object' ? node.config : {};
  const versionId = typeof config.application_version === 'string' ? config.application_version.trim() : '';
  if (!CALL_APP_VERSION_ID.test(versionId)) {
    return [{
      code: 'call_app.version',
      field: 'config.application_version',
      message: '请选择一个已发布的固定子应用版本（固定 UUID，不接受 latest）。',
    }];
  }
  if (!schemas) {
    return [{
      code: 'call_app.not_callable',
      field: 'config.application_version',
      message: '该版本没有冻结的 definition，不能作为固定子应用调用；服务端会以 dependency_not_ready 拒绝。',
    }];
  }
  const childProperties = schemas.inputSchema && typeof schemas.inputSchema.properties === 'object'
    && schemas.inputSchema.properties
    ? schemas.inputSchema.properties
    : {};
  const childRequired = Array.isArray(schemas.inputSchema && schemas.inputSchema.required)
    ? schemas.inputSchema.required
    : [];
  const additional = schemas.inputSchema && schemas.inputSchema.additionalProperties === true;
  const mapped = node && node.inputs && typeof node.inputs === 'object' ? node.inputs : {};
  for (const field of Object.keys(mapped)) {
    if (!Object.prototype.hasOwnProperty.call(childProperties, field)) {
      if (!additional) {
        problems.push({
          code: 'call_app.unknown_input',
          field: 'inputs.' + escapePointer(field),
          message: '子版本没有声明输入字段 "' + field + '"；映射只能命名子版本 input_schema 的字段。',
        });
      }
      continue;
    }
    const shape = bindingShape(mapped[field]);
    if (!shape) {
      problems.push({
        code: 'call_app.binding',
        field: 'inputs.' + escapePointer(field),
        message: '输入 "' + field + '" 的绑定必须是 {literal} 或 {ref} 之一。',
      });
      continue;
    }
    if (shape.kind === 'ref') {
      if (!isLegalSource(definition, node.id, shape.ref)) {
        problems.push({
          code: 'call_app.illegal_source',
          field: 'inputs.' + escapePointer(field),
          message: '引用 ' + shape.ref + ' 不是该节点当前路径上的合法来源；只能使用应用输入或上游节点的已声明输出。',
        });
        continue;
      }
    }
    const childType = schemaTypeOf(childProperties[field]);
    const bindingType = describeBindingType(definition, shape);
    if (!typeAccepts(childType, bindingType)) {
      problems.push({
        code: 'call_app.type_mismatch',
        field: 'inputs.' + escapePointer(field),
        message: '子输入 "' + field + '" 声明为 ' + childType + '，但绑定产生 ' + bindingType +
          '；映射必须类型兼容（服务端会再次校验）。',
      });
    }
  }
  for (const field of childRequired) {
    if (!Object.prototype.hasOwnProperty.call(mapped, field)) {
      problems.push({
        code: 'call_app.missing_required_input',
        field: 'inputs.' + escapePointer(field),
        message: '子应用要求输入 "' + field + '" 但没有映射；必填子输入不会被隐式补默认值。',
      });
    }
  }
  // The parent output declaration must stay a refinement of the child output.
  const childOutputProperties = schemas.outputSchema && typeof schemas.outputSchema.properties === 'object'
    && schemas.outputSchema.properties
    ? schemas.outputSchema.properties
    : {};
  const declaredOutput = node && node.output_schema && typeof node.output_schema.properties === 'object'
    && node.output_schema.properties
    ? node.output_schema.properties
    : {};
  for (const field of Object.keys(declaredOutput)) {
    if (!Object.prototype.hasOwnProperty.call(childOutputProperties, field)) {
      problems.push({
        code: 'call_app.unknown_output',
        field: 'output_schema.properties.' + escapePointer(field),
        message: '子版本输出没有声明 "' + field + '"；父节点输出只能是子版本输出的细化。',
      });
      continue;
    }
    const childType = schemaTypeOf(childOutputProperties[field]);
    const declaredType = schemaTypeOf(declaredOutput[field]);
    if (!typeAccepts(childType, declaredType)) {
      problems.push({
        code: 'call_app.output_type_mismatch',
        field: 'output_schema.properties.' + escapePointer(field),
        message: '子输出 "' + field + '" 声明为 ' + childType + '，父节点声明为 ' + declaredType +
          '；父声明必须是子 schema 的细化。',
      });
    }
  }
  return problems;
}

/** The default parent output_schema offered for a freshly picked child version. */
export function callAppOutputSchemaFor(versionView) {
  const schemas = callAppChildSchemas(versionView);
  if (!schemas) {
    return null;
  }
  return JSON.parse(JSON.stringify(schemas.outputSchema));
}
