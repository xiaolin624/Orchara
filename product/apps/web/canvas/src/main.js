/*
 * F04B/T083 canvas: a real Vue 3 + Vue Flow app bundled to a committed asset.
 *
 * The canvas authors a DAG of start/python/agent/model/condition/join/end nodes.
 * A condition node fans out through named ports; a plain node may fan out only
 * when a join declares it as its fork; only a join may merge several inputs.
 * Illegal connections are refused with an explanation, and everything the
 * server reports is located (node field or edge) instead of a bare 422.
 *
 * F04B/T086 adds the model panel: a fixed (never latest) passed profile, a strict
 * {{name}} prompt over bound inputs, the text/json response mode and the node's
 * output_schema as the response schema (there is no response_schema config key).
 *
 * The page owns save; this app owns graph editing, the config panels, the
 * validate call (POST /applications/{id}/validate) and export. Pure logic lives
 * in ./lib/*.js and is executed by Node tests; this file is the DOM/Vue wiring.
 */

import { createApp, h, reactive, nextTick } from 'vue';
import { VueFlow, applyNodeChanges, applyEdgeChanges, Handle, Position } from '@vue-flow/core';
import { Background } from '@vue-flow/background';
import { Controls } from '@vue-flow/controls';
import '@vue-flow/core/dist/style.css';
import '@vue-flow/core/dist/theme-default.css';
import '@vue-flow/controls/dist/style.css';
import './canvas.css';

import {
  CONDITION_OPS,
  JOIN_MODES,
  MODEL_RESPONSE_MODES,
  NODE_LABELS,
  NODE_TYPES,
  REPEATABLE_NODE_TYPES,
  addNode,
  canConnect,
  conditionPorts,
  defaultDefinition,
  defaultLayout,
  definitionToGraph,
  edgeIdFor,
  edgePortOf,
  emptySchema,
  graphToDefinition,
  layoutFromGraph,
  regionProblems,
  removeNode,
  retrieveOutputSchema,
} from './lib/dsl.js';
import { legalSources, sourceGroups, sourceType } from './lib/sources.js';
import { mapValidationErrors } from './lib/errors.js';
import { exportFilename, exportPayload } from './lib/export.js';
import {
  CALL_APP_BINDING_MODES,
  callAppApplicationOptions,
  callAppChildSchemas,
  callAppInputRows,
  callAppOutputSchemaFor,
  callAppProblems,
  callAppVersionOptions,
} from './lib/callapp.js';

const SCHEMA_TYPES = ['string', 'integer', 'number', 'boolean', 'object', 'array'];

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function applicationIdFromPath(pathname) {
  const match = /^\/apps\/([0-9a-fA-F-]{36})\/edit\/?$/.exec(String(pathname || ''));
  return match ? match[1] : '';
}

function fieldElement(root, field) {
  const escaped = String(field).replace(/["\\]/g, '\\$&');
  return root.querySelector('[data-field="' + escaped + '"]');
}

function emptySchemaWithId() {
  return { type: 'object', properties: {} };
}

function normalizeSchema(raw) {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
    return emptySchema();
  }
  if (raw.type !== 'object') {
    return { type: 'object', properties: {}, ...raw };
  }
  return raw;
}

function setSchemaProperty(schema, name, type, required) {
  const next = clone(schema || emptySchemaWithId());
  next.type = 'object';
  next.properties = next.properties && typeof next.properties === 'object' ? next.properties : {};
  next.properties[name] = { ...(next.properties[name] || {}), type };
  const requiredList = new Set(Array.isArray(next.required) ? next.required : []);
  if (required) {
    requiredList.add(name);
  } else {
    requiredList.delete(name);
  }
  next.required = [...requiredList].sort();
  return next;
}

function removeSchemaProperty(schema, name) {
  const next = clone(schema || emptySchemaWithId());
  if (next.properties) {
    delete next.properties[name];
  }
  if (Array.isArray(next.required)) {
    next.required = next.required.filter((item) => item !== name);
  }
  return next;
}

function renderSchemaRows(definition, root, state, schema, options) {
  const properties = schema.properties && typeof schema.properties === 'object' ? schema.properties : {};
  const required = new Set(Array.isArray(schema.required) ? schema.required : []);
  const rows = Object.keys(properties).sort().map((name) =>
    h('div', { class: 'schema-row', key: 'schema-' + name }, [
      h('input', {
        type: 'text',
        value: name,
        'data-field': 'schema.' + name + '.name',
        onInput: (event) => options.onRename(name, event.target.value),
      }),
      h('select', {
        value: properties[name].type || 'string',
        'data-field': 'schema.' + name + '.type',
        onChange: (event) => options.onType(name, event.target.value),
      }, SCHEMA_TYPES.map((type) => h('option', { value: type }, type))),
      h('label', { class: 'inline' }, [
        h('input', {
          type: 'checkbox',
          checked: required.has(name),
          'data-field': 'schema.' + name + '.required',
          onChange: (event) => options.onRequired(name, event.target.checked),
        }),
        ' required',
      ]),
      h('button', {
        type: 'button',
        class: 'link danger',
        'data-field': 'schema.' + name + '.remove',
        onClick: () => options.onRemove(name),
      }, '删除'),
    ]),
  );
  const draftName = state.schemaDrafts[options.draftKey] || '';
  return [
    ...(rows.length ? rows : [h('p', { class: 'hint' }, '尚未声明任何字段。')]),
    h('div', { class: 'schema-row add' }, [
      h('input', {
        type: 'text',
        placeholder: '新字段名',
        value: draftName,
        'data-field': options.draftKey + '.new',
        onInput: (event) => { state.schemaDrafts[options.draftKey] = event.target.value; },
      }),
      h('button', {
        type: 'button',
        onClick: () => {
          const name = String(state.schemaDrafts[options.draftKey] || '').trim();
          if (!name) {
            state.status = '字段名不能为空。';
            state.statusKind = 'bad';
            return;
          }
          state.schemaDrafts[options.draftKey] = '';
          options.onAdd(name);
        },
      }, '添加字段'),
    ]),
  ];
}

function renderSourceSelect(definition, nodeId, value, onChange, fieldName) {
  const groups = sourceGroups(definition, nodeId);
  const options = [h('option', { value: '' }, '— 选择合法来源 —')];
  for (const group of groups) {
    const children = group.options.map((entry) =>
      h('option', { value: entry.ref },
        entry.label + ' (' + entry.type + ') — ' + group.label),
    );
    options.push(h('optgroup', { label: group.label }, children));
  }
  if (value && !groups.some((group) => group.options.some((entry) => entry.ref === value))) {
    options.push(h('option', { value, disabled: true }, value + '（不再合法）'));
  }
  return h('select', {
    value: value || '',
    'data-field': fieldName,
    onChange: (event) => onChange(event.target.value),
  }, options);
}

function renderMappingEditor(definition, state, node) {
  const inputs = node.inputs && typeof node.inputs === 'object' ? node.inputs : {};
  const names = Object.keys(inputs).sort();
  const draftKey = node.id + '.inputName';
  return [
    h('h3', {}, '输入映射'),
    h('p', { class: 'hint' },
      '只提供应用输入属性与合法上游节点的输出；分支节点在 join 前只能通过 '
      + 'node:<join>#/results/<source> 读取，自身和下游不会出现在列表里。'),
    ...(names.length === 0
      ? [h('p', { class: 'hint' }, '还没有映射。工具/Agent 的输入名需要与节点输入名一致。')]
      : names.map((name) => h('div', { class: 'mapping-row', key: 'map-' + name }, [
          h('code', { class: 'mapping-name' }, name),
          renderSourceSelect(
            definition,
            node.id,
            inputs[name] && inputs[name].ref,
            (ref) => {
              const next = clone(node.inputs || {});
              if (ref) {
                next[name] = { ref };
              } else {
                delete next[name];
              }
              node.inputs = next;
              commit(state);
            },
            'inputs.' + name,
          ),
          h('span', { class: 'type-chip' },
            inputs[name] && inputs[name].ref ? sourceType(definition, inputs[name].ref) : 'literal'),
          h('button', {
            type: 'button',
            class: 'link danger',
            'data-field': 'inputs.' + name + '.remove',
            onClick: () => {
              const next = clone(node.inputs || {});
              delete next[name];
              node.inputs = next;
              commit(state);
            },
          }, '删除'),
        ]))),
    h('div', { class: 'schema-row add' }, [
      h('input', {
        type: 'text',
        placeholder: '新输入名',
        value: state.inputDrafts[draftKey] || '',
        'data-field': 'inputs.new',
        onInput: (event) => { state.inputDrafts[draftKey] = event.target.value; },
      }),
      h('button', {
        type: 'button',
        onClick: () => {
          const name = String(state.inputDrafts[draftKey] || '').trim();
          if (!name) {
            state.status = '输入名不能为空。';
            state.statusKind = 'bad';
            return;
          }
          state.inputDrafts[draftKey] = '';
          node.inputs = { ...(node.inputs || {}), [name]: { ref: '' } };
          commit(state);
        },
      }, '添加输入'),
    ]),
  ];
}

function renderOutputSchemaEditor(definition, state, node, title) {
  const text = JSON.stringify(node.output_schema || emptySchemaWithId(), null, 2);
  return [
    h('h3', {}, title || '输出 Schema'),
    h('textarea', {
      class: 'schema-json',
      spellcheck: 'false',
      'data-field': 'output_schema',
      value: state.schemaText[node.id] !== undefined ? state.schemaText[node.id] : text,
      onInput: (event) => {
        const value = event.target.value;
        state.schemaText[node.id] = value;
        try {
          const parsed = JSON.parse(value);
          if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
            throw new Error('必须是 JSON 对象');
          }
          node.output_schema = parsed;
          state.schemaErrors[node.id] = '';
          commit(state);
        } catch (error) {
          state.schemaErrors[node.id] = '输出 Schema 不是合法 JSON 对象：' + error.message;
        }
      },
    }),
    state.schemaErrors[node.id]
      ? h('p', { class: 'error-text', 'data-field': 'output_schema.error' }, state.schemaErrors[node.id])
      : h('p', { class: 'hint' }, '输出 Schema 是对象类型，属性名就是下游可引用的字段。'),
  ];
}

function renderStartPanel(definition, state, node) {
  const schema = definition.input_schema || emptySchemaWithId();
  return [
    h('h3', {}, '应用输入 Schema'),
    h('p', { class: 'hint' }, '开始节点没有输入；这里声明应用运行时可以提供的输入字段。'),
    ...renderSchemaRows(definition, state.root, state, schema, {
      draftKey: 'input_schema',
      onAdd: (name) => {
        definition.input_schema = setSchemaProperty(definition.input_schema, name, 'string', false);
        commit(state);
      },
      onRename: (name, next) => {
        const trimmed = String(next || '').trim();
        if (!trimmed || trimmed === name) {
          return;
        }
        const nextSchema = clone(definition.input_schema || emptySchemaWithId());
        nextSchema.properties = nextSchema.properties || {};
        nextSchema.properties[trimmed] = nextSchema.properties[name];
        delete nextSchema.properties[name];
        nextSchema.required = (nextSchema.required || []).map((item) => item === name ? trimmed : item);
        definition.input_schema = nextSchema;
        commit(state);
      },
      onType: (name, type) => {
        definition.input_schema = setSchemaProperty(
          definition.input_schema, name, type,
          (definition.input_schema.required || []).includes(name),
        );
        commit(state);
      },
      onRequired: (name, required) => {
        definition.input_schema = setSchemaProperty(
          definition.input_schema, name,
          definition.input_schema.properties[name].type || 'string', required,
        );
        commit(state);
      },
      onRemove: (name) => {
        definition.input_schema = removeSchemaProperty(definition.input_schema, name);
        commit(state);
      },
    }),
  ];
}

function renderEndPanel(definition, state, node) {
  const schema = definition.output_schema || emptySchemaWithId();
  const properties = schema.properties && typeof schema.properties === 'object' ? schema.properties : {};
  return [
    h('h3', {}, '应用输出声明'),
    h('p', { class: 'hint' }, '结束节点把上游字段映射成应用输出；声明的必填输出必须全部映射。'),
    ...renderSchemaRows(definition, state.root, state, schema, {
      draftKey: 'output_schema',
      onAdd: (name) => {
        definition.output_schema = setSchemaProperty(definition.output_schema, name, 'string', false);
        commit(state);
      },
      onRename: (name, next) => {
        const trimmed = String(next || '').trim();
        if (!trimmed || trimmed === name) {
          return;
        }
        const nextSchema = clone(definition.output_schema || emptySchemaWithId());
        nextSchema.properties[trimmed] = nextSchema.properties[name];
        delete nextSchema.properties[name];
        nextSchema.required = (nextSchema.required || []).map((item) => item === name ? trimmed : item);
        definition.output_schema = nextSchema;
        commit(state);
      },
      onType: (name, type) => {
        definition.output_schema = setSchemaProperty(
          definition.output_schema, name, type,
          (definition.output_schema.required || []).includes(name),
        );
        commit(state);
      },
      onRequired: (name, required) => {
        definition.output_schema = setSchemaProperty(
          definition.output_schema, name,
          definition.output_schema.properties[name].type || 'string', required,
        );
        commit(state);
      },
      onRemove: (name) => {
        definition.output_schema = removeSchemaProperty(definition.output_schema, name);
        commit(state);
      },
    }),
    h('h3', {}, '输出映射（来自合法上游字段）'),
    ...Object.keys(properties).sort().map((name) => h('div', { class: 'mapping-row', key: 'out-' + name }, [
      h('code', { class: 'mapping-name' }, name),
      renderSourceSelect(
        definition,
        node.id,
        node.inputs[name] && node.inputs[name].ref,
        (ref) => {
          const next = clone(node.inputs || {});
          if (ref) {
            next[name] = { ref };
          } else {
            delete next[name];
          }
          node.inputs = next;
          commit(state);
        },
        'inputs.' + name,
      ),
      h('span', { class: 'type-chip' },
        node.inputs[name] && node.inputs[name].ref ? sourceType(definition, node.inputs[name].ref) : '未映射'),
    ])),
  ];
}

function renderPythonPanel(definition, state, node) {
  const tools = state.tools.filter((tool) => tool.state === 'ready' && tool.approved === true);
  const selected = node.config.tool_version || '';
  const tool = tools.find((item) => item.id === selected);
  return [
    h('h3', {}, 'Python 工具版本'),
    h('p', { class: 'hint' }, '只列出 ready 且已批准的工具版本（GET /api/v1/tools）。'),
    h('select', {
      'data-field': 'config.tool_version',
      value: selected,
      onChange: (event) => {
        node.config = { ...node.config, tool_version: event.target.value };
        const picked = tools.find((item) => item.id === event.target.value);
        if (picked && picked.output_schema && typeof picked.output_schema === 'object'
            && !Array.isArray(picked.output_schema)) {
          node.output_schema = normalizeSchema(picked.output_schema);
        }
        commit(state);
      },
    }, [
      h('option', { value: '' }, state.catalogsState === 'loading' ? '正在加载工具…' : '— 选择工具 —'),
      ...tools.map((item) => h('option', { value: item.id },
        item.id + (item.state ? ' (' + item.state + ')' : ''))),
    ]),
    state.catalogsError ? h('p', { class: 'error-text' }, state.catalogsError) : null,
    tool ? h('div', { class: 'schema-preview' }, [
      h('h4', {}, '工具输入 Schema（只读）'),
      h('pre', {}, JSON.stringify(tool.input_schema || {}, null, 2)),
      h('h4', {}, '工具输出 Schema（只读）'),
      h('pre', {}, JSON.stringify(tool.output_schema || {}, null, 2)),
    ]) : null,
    ...renderMappingEditor(definition, state, node),
    ...renderOutputSchemaEditor(definition, state, node),
  ];
}

function renderAgentPanel(definition, state, node) {
  const models = state.models.filter((model) => model.validation_state === 'passed');
  const tools = state.tools.filter((tool) => tool.state === 'ready' && tool.approved === true);
  const selectedTools = Array.isArray(node.config.tool_versions) ? node.config.tool_versions : [];
  return [
    h('h3', {}, '模型 Profile'),
    h('p', { class: 'hint' }, '只列出 validation_state=passed 的模型（GET /api/v1/models）。'),
    h('select', {
      'data-field': 'config.model_profile_version',
      value: node.config.model_profile_version || '',
      onChange: (event) => {
        node.config = { ...node.config, model_profile_version: event.target.value };
        commit(state);
      },
    }, [
      h('option', { value: '' }, state.catalogsState === 'loading' ? '正在加载模型…' : '— 选择模型 —'),
      ...models.map((model) => h('option', { value: model.id },
        (model.name || model.id) + ' [' + (model.validation_state || 'unknown') + ']')),
    ]),
    h('label', { class: 'field-label' }, 'instructions'),
    h('textarea', {
      'data-field': 'config.instructions',
      value: node.config.instructions || '',
      onInput: (event) => {
        node.config = { ...node.config, instructions: event.target.value };
        commit(state);
      },
    }),
    h('label', { class: 'field-label' }, 'max_turns'),
    h('input', {
      type: 'number',
      min: 1,
      max: 50,
      'data-field': 'config.max_turns',
      value: node.config.max_turns === undefined ? 8 : node.config.max_turns,
      onInput: (event) => {
        node.config = { ...node.config, max_turns: Number(event.target.value) };
        commit(state);
      },
    }),
    h('label', { class: 'field-label' }, '允许的工具版本（ready+approved）'),
    h('select', {
      multiple: true,
      size: Math.min(5, Math.max(2, tools.length)),
      'data-field': 'config.tool_versions',
      onChange: (event) => {
        const values = Array.from(event.target.selectedOptions).map((option) => option.value);
        node.config = { ...node.config, tool_versions: values };
        commit(state);
      },
    }, tools.map((tool) => h('option', {
      value: tool.id,
      selected: selectedTools.includes(tool.id),
    }, tool.id))),
    h('p', { class: 'hint' }, 'subapp_versions 固定为空数组（本票不提供子应用调用入口）。'),
    ...renderMappingEditor(definition, state, node),
    ...renderOutputSchemaEditor(definition, state, node),
  ];
}

// ---------------------------------------------------------------------------
// F04B/T088: retrieve panel
// ---------------------------------------------------------------------------

function updateRetrieveConfig(state, node, patch) {
  node.config = { ...(node.config || {}), ...patch };
  commit(state);
}

function renderRetrievePanel(definition, state, node) {
  const config = node.config || {};
  const bases = Array.isArray(state.knowledgeBases) ? state.knowledgeBases : [];
  const selectedBases = Array.isArray(config.knowledge_base_ids) ? config.knowledge_base_ids : [];
  const knownBases = new Set(bases.map((base) => base.id));
  const missingBases = selectedBases.filter((id) => !knownBases.has(id));
  const queryRef = node.inputs && node.inputs.query && node.inputs.query.ref
    ? node.inputs.query.ref
    : '';
  return [
    h('h3', {}, '知识库'),
    h('p', { class: 'hint' },
      '只列出当前账号可见的知识库（GET /api/v1/knowledge-bases）；运行时仍按 owner ACL 重新过滤，'
      + '看不见的知识库会被静默排除，不会泄露它是否存在。'),
    h('select', {
      multiple: true,
      size: Math.min(6, Math.max(2, bases.length)),
      'data-field': 'config.knowledge_base_ids',
      onChange: (event) => {
        const values = Array.from(event.target.selectedOptions).map((option) => option.value);
        updateRetrieveConfig(state, node, { knowledge_base_ids: values });
      },
    }, bases.map((base) => h('option', {
      value: base.id,
      selected: selectedBases.includes(base.id),
    }, (base.name || base.id) + ' · ' + base.id))),
    missingBases.length
      ? h('p', { class: 'hint', 'data-field': 'config.knowledge_base_ids.stale' },
          '已选但当前不可见的知识库会保留：' + missingBases.join('、')
          + '。运行时会按 ACL 排除，不会出现在结果里。')
      : null,
    h('label', { class: 'field-label' }, 'top_k（1–20）'),
    h('input', {
      type: 'number',
      min: 1,
      max: 20,
      'data-field': 'config.top_k',
      value: config.top_k === undefined ? 5 : config.top_k,
      onInput: (event) => {
        updateRetrieveConfig(state, node, { top_k: Number(event.target.value) });
      },
    }),
    h('h3', {}, 'query 输入'),
    h('p', { class: 'hint' },
      'retrieve 没有 query 配置：必须把一个字符串来源绑定到输入名 query（应用输入或合法上游字段）。'
      + '没有绑定或类型不是 string 时，服务端执行以 node_input_invalid 失败。'),
    h('div', { class: 'mapping-row' }, [
      h('code', { class: 'mapping-name' }, 'query'),
      renderSourceSelect(definition, node.id, queryRef, (ref) => {
        const next = clone(node.inputs || {});
        if (ref) {
          next.query = { ref };
        } else {
          delete next.query;
        }
        node.inputs = next;
        commit(state);
      }, 'inputs.query'),
      h('span', { class: 'type-chip' }, queryRef ? sourceType(definition, queryRef) : '未绑定'),
    ]),
    ...renderOutputSchemaEditor(definition, state, node),
    h('p', { class: 'hint' },
      '输出模板固定为 items/citations/count：items 保留完整平台字段与 citation JSON，'
      + 'citations 是文档/版本/span/短摘要投影。填入模板后，下游才能选择 '
      + 'node:<id>#/citations、#/items 与 #/count。'),
    h('button', {
      type: 'button',
      'data-field': 'output_schema.template',
      onClick: () => {
        node.output_schema = retrieveOutputSchema();
        delete state.schemaText[node.id];
        state.schemaErrors[node.id] = '';
        commit(state);
      },
    }, '填入 retrieve 输出模板'),
  ];
}

// ---------------------------------------------------------------------------
// F04B/T086: model panel — one controlled generation
// ---------------------------------------------------------------------------

function renderModelPanel(definition, state, node) {
  // Only a passed profile is usable. A non-admin GET /api/v1/models already hides
  // pending/failed/revoked profiles; the filter keeps the rule explicit and the
  // server re-proves the fixed version on invoke (never "latest").
  const models = state.models.filter((model) => model.validation_state === 'passed');
  const config = node.config && typeof node.config === 'object' ? node.config : {};
  const template = typeof config.prompt_template === 'string' ? config.prompt_template : '';
  const boundNames = Object.keys(node.inputs && typeof node.inputs === 'object' ? node.inputs : {}).sort();
  const insertVariable = (name) => {
    const current = typeof node.config.prompt_template === 'string' ? node.config.prompt_template : '';
    node.config = { ...node.config, prompt_template: current + '{{' + name + '}}' };
    commit(state);
  };
  return [
    h('h3', {}, '模型 Profile（固定版本，不接受 latest）'),
    h('p', { class: 'hint' }, '只列出 validation_state=passed 的模型（GET /api/v1/models）。'),
    h('select', {
      'data-field': 'config.model_profile_version',
      value: config.model_profile_version || '',
      onChange: (event) => {
        node.config = { ...node.config, model_profile_version: event.target.value };
        commit(state);
      },
    }, [
      h('option', { value: '' }, state.catalogsState === 'loading' ? '正在加载模型…' : '— 选择模型 —'),
      ...models.map((model) => h('option', { value: model.id },
        (model.name || model.id) + ' [' + (model.validation_state || 'unknown') + ']')),
    ]),
    state.catalogsError ? h('p', { class: 'error-text' }, state.catalogsError) : null,
    h('label', { class: 'field-label' }, 'prompt_template（只允许 {{name}} 取已绑定输入）'),
    h('textarea', {
      'data-field': 'config.prompt_template',
      value: template,
      onInput: (event) => {
        node.config = { ...node.config, prompt_template: event.target.value };
        commit(state);
      },
    }),
    h('div', { class: 'variable-inserts' }, [
      h('span', { class: 'hint' }, '插入变量：'),
      ...boundNames.map((name) => h('button', {
        type: 'button',
        class: 'chip',
        'data-insert-variable': name,
        onClick: () => insertVariable(name),
      }, '{{' + name + '}}')),
      boundNames.length === 0
        ? h('span', { class: 'hint' }, '先在下方输入映射里绑定输入，模板才能引用 {{name}}。')
        : null,
    ]),
    h('p', { class: 'hint' },
      '模板只做 {{name}} 替换：属性链/表达式/未绑定变量会被执行器拒绝为 node_prompt_invalid；'
      + '对象与数组按 JSON 渲染。'),
    h('label', { class: 'field-label' }, 'response_mode'),
    h('select', {
      'data-field': 'config.response_mode',
      value: config.response_mode || 'json',
      onChange: (event) => {
        node.config = { ...node.config, response_mode: event.target.value };
        commit(state);
      },
    }, MODEL_RESPONSE_MODES.map((mode) => h('option', { value: mode }, mode))),
    // The response schema IS the node's output_schema; it is not a config key.
    ...renderOutputSchemaEditor(definition, state, node, '响应 Schema（= node.output_schema）'),
    ...renderMappingEditor(definition, state, node),
  ];
}

// ---------------------------------------------------------------------------
// F04B: condition / join panels
// ---------------------------------------------------------------------------

function caseValueText(value) {
  if (value === undefined) {
    return '';
  }
  try {
    return JSON.stringify(value);
  } catch (error) {
    return '';
  }
}

function updateCase(state, node, index, patch) {
  const cases = clone(Array.isArray(node.config.cases) ? node.config.cases : []);
  cases[index] = { ...(cases[index] || {}), ...patch };
  if (patch.op === 'exists' && Object.prototype.hasOwnProperty.call(cases[index], 'value')) {
    delete cases[index].value;
  }
  node.config = { ...node.config, cases };
  commit(state);
}

function renderConditionPanel(definition, state, node) {
  const config = node.config || {};
  const cases = Array.isArray(config.cases) ? config.cases : [];
  const joins = definition.nodes.filter((item) => item.type === 'join');
  const ports = conditionPorts(config);
  const connected = new Set(
    state.flowEdges.filter((edge) => edge.source === node.id)
      .map((edge) => edgePortOf(edge)).filter((value) => value !== ''),
  );
  const inputOptions = Object.keys(node.inputs || {}).sort();
  const rows = cases.map((item, index) => {
    const draftKey = node.id + '.caseValue.' + index;
    const valueText = state.caseValueDrafts[draftKey] !== undefined
      ? state.caseValueDrafts[draftKey]
      : caseValueText(item.value);
    return h('div', { class: 'case-row', key: 'case-' + index, 'data-case-index': index }, [
      h('span', { class: 'case-index' }, String(index + 1)),
      h('input', {
        type: 'text',
        placeholder: '输入名',
        list: 'condition-input-list-' + node.id,
        value: item.input || '',
        'data-field': 'config.cases.' + index + '.input',
        onInput: (event) => updateCase(state, node, index, { input: event.target.value }),
      }),
      h('select', {
        'data-field': 'config.cases.' + index + '.op',
        value: item.op || 'eq',
        onChange: (event) => updateCase(state, node, index, { op: event.target.value }),
      }, CONDITION_OPS.map((op) => h('option', { value: op }, op))),
      h('input', {
        type: 'text',
        placeholder: item.op === 'exists' ? '（exists 不需要 value）' : 'JSON 值（如 true / "x" / 3）',
        value: valueText,
        disabled: item.op === 'exists',
        'data-field': 'config.cases.' + index + '.value',
        onInput: (event) => {
          const text = event.target.value;
          state.caseValueDrafts[draftKey] = text;
          if (text.trim() === '') {
            const casesNext = clone(node.config.cases || []);
            delete casesNext[index].value;
            node.config = { ...node.config, cases: casesNext };
            commit(state);
            return;
          }
          try {
            const parsed = JSON.parse(text);
            delete state.caseValueDrafts[draftKey];
            updateCase(state, node, index, { value: parsed });
          } catch (error) {
            state.status = 'case ' + (index + 1) + ' 的 value 不是合法 JSON：' + error.message;
            state.statusKind = 'bad';
          }
        },
      }),
      h('input', {
        type: 'text',
        placeholder: '端口',
        value: item.port || '',
        'data-field': 'config.cases.' + index + '.port',
        onInput: (event) => updateCase(state, node, index, { port: event.target.value }),
      }),
      h('button', {
        type: 'button',
        class: 'link',
        'data-field': 'config.cases.' + index + '.up',
        disabled: index === 0,
        onClick: () => {
          const next = clone(node.config.cases || []);
          [next[index - 1], next[index]] = [next[index], next[index - 1]];
          node.config = { ...node.config, cases: next };
          commit(state);
        },
      }, '↑'),
      h('button', {
        type: 'button',
        class: 'link',
        'data-field': 'config.cases.' + index + '.down',
        disabled: index === cases.length - 1,
        onClick: () => {
          const next = clone(node.config.cases || []);
          [next[index + 1], next[index]] = [next[index], next[index + 1]];
          node.config = { ...node.config, cases: next };
          commit(state);
        },
      }, '↓'),
      h('button', {
        type: 'button',
        class: 'link danger',
        'data-field': 'config.cases.' + index + '.remove',
        onClick: () => {
          const next = clone(node.config.cases || []);
          next.splice(index, 1);
          node.config = { ...node.config, cases: next };
          commit(state);
        },
      }, '删除'),
    ]);
  });
  return [
    h('h3', {}, '条件分支'),
    h('p', { class: 'hint' },
      'cases 按顺序求值，首个匹配决定唯一出口；其余分支整段 skipped。端口必须与出边一致，每个出边都要带 port。'),
    h('datalist', { id: 'condition-input-list-' + node.id },
      inputOptions.map((name) => h('option', { value: name }))),
    h('label', { class: 'field-label' }, '配对 join（join_id）'),
    h('select', {
      'data-field': 'config.join_id',
      value: config.join_id || '',
      onChange: (event) => {
        const joinId = event.target.value;
        node.config = { ...node.config, join_id: joinId };
        const target = definition.nodes.find((item) => item.id === joinId && item.type === 'join');
        if (target) {
          target.config = { ...target.config, fork_id: node.id };
        }
        commit(state);
      },
    }, [
      h('option', { value: '' }, joins.length ? '— 选择配对 join —' : '尚未创建 join 节点'),
      ...joins.map((join) => h('option', { value: join.id },
        join.id + (join.config && join.config.fork_id ? '（fork: ' + join.config.fork_id + '）' : ''))),
    ]),
    h('label', { class: 'field-label' }, 'default_port'),
    h('input', {
      type: 'text',
      value: config.default_port || '',
      'data-field': 'config.default_port',
      onInput: (event) => {
        node.config = { ...node.config, default_port: event.target.value };
        commit(state);
      },
    }),
    h('h3', {}, 'cases（顺序即优先级）'),
    ...(rows.length ? rows : [h('p', { class: 'hint' }, '至少需要一个 case（契约要求 minItems=1）。')]),
    h('div', { class: 'schema-row add' }, [
      h('button', {
        type: 'button',
        'data-field': 'config.cases.add',
        onClick: () => {
          const next = clone(node.config.cases || []);
          next.push({ input: '', op: 'eq', value: true, port: 'port' + (next.length + 1) });
          node.config = { ...node.config, cases: next };
          commit(state);
        },
      }, '添加 case'),
    ]),
    h('h4', {}, '端口预览'),
    h('div', { class: 'port-preview', 'data-field': 'config.ports' },
      ports.length
        ? ports.map((port) => h('span', {
            class: 'port-chip' + (connected.has(port) ? ' connected' : ''),
            'data-port': port,
          }, port + (connected.has(port) ? ' ✓ 已连线' : '（未连线）')))
        : [h('span', { class: 'hint' }, '还没有可用端口：请填写 case port 或 default_port。')]),
    ...renderMappingEditor(definition, state, node),
    ...renderOutputSchemaEditor(definition, state, node),
  ];
}

function renderJoinPanel(definition, state, node) {
  const config = node.config || {};
  const outDegree = new Map();
  for (const edge of definition.edges || []) {
    outDegree.set(edge.source, (outDegree.get(edge.source) || 0) + 1);
  }
  const forkCandidates = definition.nodes
    .filter((item) => item.id !== node.id && item.type !== 'end' && item.type !== 'join'
      && (outDegree.get(item.id) || 0) >= 1)
    .sort((a, b) => (outDegree.get(b.id) || 0) - (outDegree.get(a.id) || 0));
  const predecessors = [...new Set(
    (definition.edges || []).filter((edge) => edge.target === node.id).map((edge) => edge.source),
  )].sort();
  const predecessorLabels = new Map(definition.nodes.map((item) => [item.id, item.type]));
  const required = Array.isArray(config.required_sources) ? config.required_sources : [];
  const optional = Array.isArray(config.optional_sources) ? config.optional_sources : [];
  const declared = [...required, ...optional];
  const missing = predecessors.filter((id) => !declared.includes(id));
  const extra = declared.filter((id) => !predecessors.includes(id));
  const selectFor = (field, selected, other, onChange, tagFor) => h('select', {
    multiple: true,
    size: Math.min(6, Math.max(2, predecessors.length)),
    'data-field': field,
    onChange: (event) => {
      onChange(Array.from(event.target.selectedOptions).map((option) => option.value));
    },
  }, predecessors.map((id) => h('option', {
    value: id,
    selected: selected.includes(id),
  }, id + ' (' + (predecessorLabels.get(id) || '?') + ')'
    + (other.includes(id) ? ' — 已在一侧' : '')
    + (tagFor ? tagFor(id) : ''))));
  return [
    h('h3', {}, '并行汇合（join）'),
    h('p', { class: 'hint' },
      'join 只等待已激活的分支；required 分支失败会 fail-fast，optional 仅允许纯读/计算分支。'),
    h('label', { class: 'field-label' }, 'mode'),
    h('input', {
      type: 'text',
      value: config.mode || 'all_active',
      readonly: true,
      'data-field': 'config.mode',
    }),
    h('p', { class: 'hint' }, '（契约只有 all_active 一种模式）'),
    h('label', { class: 'field-label' }, 'fork_id（配对分叉节点）'),
    h('select', {
      'data-field': 'config.fork_id',
      value: config.fork_id || '',
      onChange: (event) => {
        const forkId = event.target.value;
        node.config = { ...node.config, fork_id: forkId };
        const fork = definition.nodes.find((item) => item.id === forkId);
        if (fork && fork.type === 'condition') {
          fork.config = { ...fork.config, join_id: node.id };
        }
        commit(state);
      },
    }, [
      h('option', { value: '' },
        forkCandidates.length ? '— 选择分叉节点 —' : '画布上还没有可分叉的节点（先连一条出边）'),
      ...forkCandidates.map((fork) => h('option', { value: fork.id },
        fork.id + ' (' + fork.type + ')' + (fork.config && fork.config.join_id
          ? '（paired join: ' + fork.config.join_id + '）' : ''))),
    ]),
    h('h4', {}, 'required_sources（必需分支）'),
    selectFor('config.required_sources', required, optional, (values) => {
      node.config = {
        ...node.config,
        required_sources: values,
        optional_sources: optional.filter((id) => !values.includes(id)),
      };
      commit(state);
    }),
    h('h4', {}, 'optional_sources（可选分支）'),
    selectFor('config.optional_sources', optional, required, (values) => {
      node.config = {
        ...node.config,
        optional_sources: values,
        required_sources: required.filter((id) => !values.includes(id)),
      };
      commit(state);
    }, () => ' — optional：仅纯读/计算错误可收敛'),
    h('p', {
      class: 'hint',
      'data-field': 'config.optional_sources.convergence',
    }, optional.length
      ? '已选 optional：' + optional.join('、')
        + '。只有 python/condition/join 的纯读/计算明确错误会被收敛为 join 结果里的错误数据；'
        + 'agent/call_app 失败、能力/权限拒绝、预算、审批、未知写与持久化失败不会降级，Run 仍失败。'
      : 'optional 只允许纯读/计算（python/condition/join）的明确错误被收敛为错误数据；'
        + '写/越权/预算/审批失败不会降级。'),
    h('p', {
      class: 'hint',
      'data-field': 'config.join_sources.coverage',
    }, '直接前驱：' + (predecessors.length ? predecessors.join('、') : '（无）')
      + '；未覆盖：' + (missing.length ? missing.join('、') : '无')
      + '；多余：' + (extra.length ? extra.join('、') : '无')),
  ];
}

function renderApprovalPanel(definition, state, node) {
  const config = node.config || {};
  const inputs = node.inputs && typeof node.inputs === 'object' ? node.inputs : {};
  const binding = inputs.proposal && typeof inputs.proposal === 'object' ? inputs.proposal : {};
  const boundRef = typeof binding.ref === 'string' ? binding.ref : '';
  const literal = binding.literal !== undefined ? JSON.stringify(binding.literal, null, 2) : '';
  const draftKey = node.id + '.proposalLiteral';
  const literalValue = Object.prototype.hasOwnProperty.call(state.proposalDrafts, draftKey)
    ? state.proposalDrafts[draftKey]
    : literal;
  return [
    h('h3', {}, '人工审批'),
    h('p', { class: 'hint', 'data-field': 'approval.boundary' },
      '审批只代表业务决定，不授权后续外部写：恢复后继续本次运行，但后面的写工具仍需'
      + '各自的 operation 审批（服务端能力代理逐次校验）。'),
    h('label', { class: 'field-label' }, '标题 title（必填，最多 200 字符）'),
    h('input', {
      type: 'text',
      maxlength: 200,
      'data-field': 'config.title',
      value: config.title || '',
      onInput: (event) => {
        const value = String(event.target.value || '').trim();
        node.config = { ...node.config, title: value };
        commit(state);
      },
    }),
    h('p', { class: 'hint' }, '标题为空时发布校验会拒绝该节点。'),
    h('label', { class: 'field-label' }, '有效期 expires_seconds（60..86400 秒）'),
    h('input', {
      type: 'number',
      min: 60,
      max: 86400,
      'data-field': 'config.expires_seconds',
      value: config.expires_seconds === undefined ? 3600 : config.expires_seconds,
      onInput: (event) => {
        node.config = { ...node.config, expires_seconds: Number(event.target.value) };
        commit(state);
      },
    }),
    h('h3', {}, '审批提议（proposal 输入，JSON 对象）'),
    h('p', { class: 'hint' },
      '提议由使用者审批时原样展示；可以绑定一个合法上游对象，或直接编写字面量对象。'),
    h('label', { class: 'field-label' }, '来源绑定（proposal ref）'),
    renderSourceSelect(
      definition,
      node.id,
      boundRef,
      (ref) => {
        const next = clone(node.inputs || {});
        if (ref) {
          next.proposal = { ref };
        } else if (!Object.prototype.hasOwnProperty.call(next, 'proposal')
            || next.proposal.literal === undefined) {
          next.proposal = { literal: {} };
        }
        node.inputs = next;
        delete state.proposalDrafts[draftKey];
        commit(state);
      },
      'inputs.proposal.ref',
    ),
    h('label', { class: 'field-label' }, boundRef ? '字面量（设置会清除来源绑定）' : '字面量 proposal（JSON 对象）'),
    h('textarea', {
      rows: 6,
      'data-field': 'inputs.proposal.literal',
      value: literalValue,
      onInput: (event) => {
        const text = String(event.target.value || '');
        state.proposalDrafts[draftKey] = text;
        let parsed;
        try {
          parsed = JSON.parse(text);
        } catch (error) {
          state.status = 'proposal 字面量不是合法 JSON：' + (error && error.message ? error.message : '解析失败');
          state.statusKind = 'bad';
          return;
        }
        if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
          state.status = 'proposal 字面量必须是 JSON 对象（不是数组或标量）。';
          state.statusKind = 'bad';
          return;
        }
        node.inputs = { ...(node.inputs || {}), proposal: { literal: parsed } };
        state.status = '已更新 proposal 字面量。';
        state.statusKind = 'ok';
        refreshLocalProblems(state);
        emitChange(state);
      },
    }),
    h('p', { class: 'hint', 'data-field': 'inputs.proposal.kind' },
      boundRef
        ? '当前为来源绑定：' + boundRef
        : (binding.literal !== undefined ? '当前为字面量对象。' : '尚未设置 proposal 输入。')),
  ];
}

// ---------------------------------------------------------------------------
// F04B/T089: call_app panel (fixed published child version + explicit mapping)
// ---------------------------------------------------------------------------

function callAppPanelOf(state, nodeId) {
  if (!state.callApp.byNode[nodeId]) {
    state.callApp.byNode[nodeId] = {
      applicationId: '',
      versions: [],
      loading: false,
      versionView: null,
      schemas: null,
      error: '',
    };
  }
  return state.callApp.byNode[nodeId];
}

function globalApi() {
  return typeof window !== 'undefined' ? window.apiFull : undefined;
}

async function loadCallAppApplications(state) {
  const api = globalApi();
  if (typeof api !== 'function') {
    state.callApp.state = 'error';
    state.callApp.error = '页面 API 助手不可用，子应用选择器为空。';
    return;
  }
  try {
    const result = await api('/api/v1/applications?limit=100');
    state.callApp.applications = callAppApplicationOptions(result.payload);
    state.callApp.state = 'ready';
  } catch (error) {
    state.callApp.state = 'error';
    state.callApp.error = '加载可调用应用失败 [' + (error.code || 'error') + '] ' + error.message;
  }
}

async function loadCallAppVersions(state, node, applicationId) {
  const panel = callAppPanelOf(state, node.id);
  panel.applicationId = applicationId;
  panel.versions = [];
  panel.error = '';
  if (!applicationId) {
    return;
  }
  const api = globalApi();
  if (typeof api !== 'function') {
    return;
  }
  panel.loading = true;
  try {
    const result = await api(
      '/api/v1/applications/' + encodeURIComponent(applicationId) + '/versions?limit=100',
    );
    panel.versions = callAppVersionOptions(result.payload);
    if (panel.versions.length === 0) {
      panel.error = '这个应用还没有已发布版本，不能作为固定子应用。';
    }
  } catch (error) {
    panel.error = '加载版本失败 [' + (error.code || 'error') + '] ' + error.message;
  } finally {
    panel.loading = false;
  }
}

/** Load the frozen manifest of a node's pinned version (schemas for the rows). */
async function ensureCallAppPinned(state, node) {
  if (!node || node.type !== 'call_app') {
    return;
  }
  const panel = callAppPanelOf(state, node.id);
  const versionId = node.config && typeof node.config.application_version === 'string'
    ? node.config.application_version
    : '';
  if (!versionId || (panel.versionView && panel.versionView.id === versionId)) {
    return;
  }
  const api = globalApi();
  if (typeof api !== 'function') {
    return;
  }
  panel.loading = true;
  panel.error = '';
  try {
    const result = await api('/api/v1/versions/' + encodeURIComponent(versionId));
    panel.versionView = result.payload;
    panel.schemas = callAppChildSchemas(result.payload);
    const applicationId = result.payload && typeof result.payload.application_id === 'string'
      ? result.payload.application_id
      : '';
    if (applicationId && applicationId !== panel.applicationId) {
      await loadCallAppVersions(state, node, applicationId);
    }
  } catch (error) {
    panel.error = '读取固定版本失败 [' + (error.code || 'error') + '] ' + error.message;
  } finally {
    panel.loading = false;
  }
}

/** Kick the pinned-version load when the selected node is a call_app. */
function ensureCallAppForSelection(state, nodeId) {
  const node = state.definition.nodes.find((item) => item.id === nodeId);
  if (node && node.type === 'call_app') {
    ensureCallAppPinned(state, node).catch(() => {});
  }
}

function selectCallAppVersion(state, node, versionId) {
  const panel = callAppPanelOf(state, node.id);
  const entry = panel.versions.find((candidate) => candidate.id === versionId);
  panel.versionView = entry ? entry.version : null;
  panel.schemas = entry ? callAppChildSchemas(entry.version) : null;
  node.config = { ...node.config, application_version: versionId };
  const declared = node.output_schema && node.output_schema.properties
    ? Object.keys(node.output_schema.properties).length
    : 0;
  if (entry && panel.schemas && declared === 0) {
    // Default the parent declaration to the child's frozen output schema; it
    // stays editable (a refinement, never a widening).
    node.output_schema = callAppOutputSchemaFor(entry.version);
  }
  commit(state);
}

function defaultLiteralFor(type) {
  switch (type) {
    case 'integer':
    case 'number':
      return 0;
    case 'boolean':
      return false;
    case 'array':
      return [];
    case 'object':
      return {};
    default:
      return '';
  }
}

function callAppRefValue(mode, value) {
  if (mode === 'input') {
    return 'input#' + (value || '');
  }
  if (mode === 'node') {
    return 'node:' + (value || '');
  }
  return '';
}

/** A source selector restricted to the binding mode (input# or node:). */
function renderCallAppSourceSelect(definition, nodeId, mode, value, onChange, fieldName) {
  const prefix = mode === 'input' ? 'input#' : 'node:';
  const groups = sourceGroups(definition, nodeId)
    .map((group) => ({
      ...group,
      options: group.options.filter((entry) => entry.ref.startsWith(prefix)),
    }))
    .filter((group) => group.options.length > 0);
  const options = [h('option', { value: '' }, mode === 'input'
    ? '— 选择应用输入 —'
    : '— 选择上游节点字段 —')];
  for (const group of groups) {
    options.push(h('optgroup', { label: group.label }, group.options.map((entry) =>
      h('option', { value: entry.ref }, entry.label + ' (' + entry.type + ')'))));
  }
  if (value && !groups.some((group) => group.options.some((entry) => entry.ref === value))) {
    options.push(h('option', { value, disabled: true }, value + '（不可用）'));
  }
  return h('select', {
    value: value || '',
    'data-field': fieldName,
    onChange: (event) => onChange(event.target.value),
  }, options);
}

function setCallAppInputBinding(state, node, field, binding) {
  const next = clone(node.inputs || {});
  if (binding === null) {
    delete next[field];
  } else {
    next[field] = binding;
  }
  node.inputs = next;
  commit(state);
}

function renderCallAppInputRow(definition, state, node, row, problems) {
  const fieldProblems = problems.filter((problem) => problem.field === 'inputs.' + row.field);
  const draftKey = node.id + '.literal.' + row.field;
  const draft = state.callApp.drafts[draftKey];
  const text = draft !== undefined ? draft : row.value;
  const onMode = (mode) => {
    delete state.callApp.drafts[draftKey];
    if (mode === 'none') {
      setCallAppInputBinding(state, node, row.field, null);
      return;
    }
    if (mode === 'literal') {
      setCallAppInputBinding(state, node, row.field, { literal: defaultLiteralFor(row.type) });
      return;
    }
    setCallAppInputBinding(state, node, row.field, { ref: '' });
  };
  let valueEditor;
  if (row.mode === 'input' || row.mode === 'node') {
    valueEditor = renderCallAppSourceSelect(
      definition,
      node.id,
      row.mode,
      callAppRefValue(row.mode, row.value),
      (ref) => {
        if (!ref) {
          setCallAppInputBinding(state, node, row.field, null);
          return;
        }
        setCallAppInputBinding(state, node, row.field, { ref });
      },
      'callApp.' + node.id + '.inputs.' + row.field + '.value',
    );
  } else if (row.mode === 'literal') {
    valueEditor = h('input', {
      type: 'text',
      value: text,
      placeholder: row.type === 'string' ? '"eu"' : 'JSON 值',
      'data-field': 'callApp.' + node.id + '.inputs.' + row.field + '.literal',
      onInput: (event) => {
        const raw = event.target.value;
        state.callApp.drafts[draftKey] = raw;
        try {
          const parsed = JSON.parse(raw);
          setCallAppInputBinding(state, node, row.field, { literal: parsed });
        } catch (error) {
          state.status = '子输入 ' + row.field + ' 的字面量不是合法 JSON：' + error.message;
          state.statusKind = 'bad';
        }
      },
    });
  } else {
    valueEditor = h('span', { class: 'hint' }, '未映射');
  }
  return h('div', {
    class: 'callapp-row',
    key: 'callapp-' + row.field,
    'data-field': 'inputs.' + row.field,
  }, [
    h('div', { class: 'callapp-row-head' }, [
      h('code', { class: 'mapping-name' }, row.field),
      h('span', { class: 'type-chip' }, row.type + (row.required ? ' · 子应用必填' : '')),
    ]),
    h('div', { class: 'callapp-row-fields' }, [
      h('select', {
        value: row.mode,
        'data-field': 'callApp.' + node.id + '.inputs.' + row.field + '.mode',
        onChange: (event) => onMode(event.target.value),
      }, CALL_APP_BINDING_MODES.map((mode) => h('option', { value: mode }, ({
        none: '不映射',
        input: '应用输入',
        literal: '字面量 JSON',
        node: '节点输出',
      })[mode]))),
      valueEditor,
    ]),
    ...fieldProblems.map((problem) => h('p', {
      class: 'error-text',
      'data-field': 'callApp.problem.' + row.field,
    }, '[' + problem.code + '] ' + problem.message)),
  ]);
}

function renderCallAppPanel(definition, state, node) {
  const panel = callAppPanelOf(state, node.id);
  const config = node.config || {};
  const versionId = typeof config.application_version === 'string' ? config.application_version : '';
  const schemas = panel.schemas;
  const loadingPinned = Boolean(versionId) && panel.loading && !schemas;
  const problems = loadingPinned ? [] : callAppProblems(node, definition, schemas);
  const inputRows = schemas ? callAppInputRows(schemas.inputSchema, node.inputs) : [];
  const applications = state.callApp.applications;
  const versionOptions = [...panel.versions];
  if (versionId && !versionOptions.some((entry) => entry.id === versionId)) {
    versionOptions.push({ id: versionId, label: '当前固定版本 ' + versionId.slice(0, 8) + '…（列表外）' });
  }
  const pinnedView = panel.versionView;
  return [
    h('h3', {}, '固定子版本'),
    h('p', { class: 'hint' },
      '只列出服务端允许当前用户读取的应用与已发布版本；节点固定一个版本 id，不接受 latest，'
      + '也不存在“跟随最新版本”。'),
    h('label', { class: 'field-label' }, '子应用'),
    h('select', {
      'data-field': 'callApp.application',
      value: panel.applicationId,
      onChange: (event) => {
        loadCallAppVersions(state, node, event.target.value)
          .then(() => commit(state))
          .catch(() => {});
      },
    }, [
      h('option', { value: '' }, state.callApp.state === 'loading'
        ? '正在加载可调用应用…'
        : (applications.length ? '— 选择可调用应用 —' : '没有可调用的应用')),
      ...applications.map((entry) => h('option', { value: entry.id },
        entry.name + ' · ' + entry.id.slice(0, 8))),
    ]),
    state.callApp.error ? h('p', { class: 'error-text' }, state.callApp.error) : null,
    h('label', { class: 'field-label' }, '已发布版本'),
    h('select', {
      'data-field': 'config.application_version',
      value: versionId,
      onChange: (event) => selectCallAppVersion(state, node, event.target.value),
    }, [
      h('option', { value: '' }, panel.loading ? '正在加载版本…' : '— 选择固定版本 —'),
      ...versionOptions.map((entry) => h('option', { value: entry.id }, entry.label)),
    ]),
    panel.error ? h('p', { class: 'error-text' }, panel.error) : null,
    versionId
      ? h('div', { class: 'schema-preview' }, [
          h('h4', {}, '固定版本事实'),
          h('p', { class: 'hint', 'data-field': 'callApp.version' },
            'version id: ' + versionId
            + (pinnedView && pinnedView.manifest_sha256
              ? ' · manifest sha256: ' + pinnedView.manifest_sha256
              : (panel.loading ? ' · 正在读取冻结 manifest…' : ''))),
        ])
      : null,
    h('h3', {}, '输入映射（显式、最少字段）'),
    h('p', { class: 'hint' },
      '输入名来自子版本冻结的 input_schema；每个输入绑定字面量、应用输入或上游节点输出，'
      + '平台不会自动补父聊天历史/个人记忆。'),
    ...(schemas
      ? (inputRows.length
        ? inputRows.map((row) => renderCallAppInputRow(definition, state, node, row, problems))
        : [h('p', { class: 'hint' }, '该子版本没有声明输入字段。')])
      : [h('p', { class: 'hint' }, versionId
        ? '正在读取子版本的冻结 Schema…'
        : '选择固定版本后这里显示子版本的输入字段与映射。')]),
    h('h3', {}, '输出 Schema'),
    h('pre', { class: 'schema-json readonly', 'data-field': 'callApp.childOutputSchema' },
      schemas ? JSON.stringify(schemas.outputSchema, null, 2) : '（尚未选择固定版本）'),
    h('p', { class: 'hint' }, '以上是子版本冻结的输出 Schema（只读）；父节点输出可编辑，默认取子版本 Schema。'),
    schemas
      ? h('button', {
          type: 'button',
          'data-field': 'callApp.useChildOutputSchema',
          onClick: () => {
            node.output_schema = callAppOutputSchemaFor(panel.versionView);
            commit(state);
          },
        }, '使用子版本输出 Schema')
      : null,
    ...renderOutputSchemaEditor(definition, state, node),
    problems.length
      ? h('div', { class: 'errors', 'data-field': 'callApp.problems' },
          problems.map((problem, index) => h('p', {
            class: 'error-text',
            key: 'callapp-problem-' + index,
            'data-field': 'callApp.problem.' + index,
          }, '[' + problem.code + '] ' + problem.message)))
      : h('p', { class: 'hint', 'data-field': 'callApp.problems.ok' },
          loadingPinned
            ? '正在读取固定版本的冻结 Schema…'
            : '映射与输出声明在本地检查通过；服务端校验仍是权威。'),
  ];
}

function renderEdgePanel(state, edge, index) {
  const definition = state.definition;
  const sourceNode = definition.nodes.find((node) => node.id === edge.source);
  const targetNode = definition.nodes.find((node) => node.id === edge.target);
  if (!sourceNode) {
    return [h('p', { class: 'hint' }, '这条连接引用了不存在的节点。')];
  }
  const isCondition = sourceNode.type === 'condition';
  const allowed = conditionPorts(sourceNode.config || {});
  const current = edgePortOf(edge);
  return [
    h('h3', {}, '连接 · ' + edge.source + ' → ' + edge.target),
    h('p', { class: 'hint' },
      (targetNode ? '目标类型 ' + targetNode.type + '；' : '')
      + (isCondition ? '条件出边必须带端口。' : '非条件出边没有端口。')),
    isCondition ? h('label', { class: 'field-label' }, 'port') : null,
    isCondition ? h('input', {
      type: 'text',
      value: current,
      'data-field': 'edge.port',
      onInput: (event) => {
        const port = event.target.value;
        const next = {
          ...edge,
          label: port,
          sourceHandle: port || undefined,
          data: { ...(edge.data || {}), port },
          class: 'orchara-edge' + (port ? ' orchara-edge-port' : ''),
        };
        state.flowEdges = state.flowEdges.map((item) => (item.id === edge.id ? next : item));
        rebuild(state);
        if (port && !allowed.includes(port)) {
          state.status = '端口 "' + port + '" 不在该条件节点的 cases/default_port 中；可用：'
            + (allowed.length ? allowed.join('、') : '（无）');
          state.statusKind = 'bad';
        } else {
          state.status = '已更新边 ' + edge.source + ' → ' + edge.target + ' 的端口为 "' + port + '"。';
          state.statusKind = 'ok';
        }
      },
    }) : h('p', { class: 'hint' }, '端口只对条件节点的出边生效。'),
    isCondition ? h('p', { class: 'hint', 'data-field': 'edge.ports' },
      '可用端口：' + (allowed.length ? allowed.join('、') : '（无）')) : null,
    h('button', {
      type: 'button',
      class: 'link danger',
      'data-field': 'edge.remove',
      onClick: () => {
        state.flowEdges = state.flowEdges.filter((item) => item.id !== edge.id);
        state.selectedEdgeId = '';
        rebuild(state);
        state.status = '已删除连接 ' + edge.source + ' → ' + edge.target + '。';
        state.statusKind = 'ok';
      },
    }, '删除这条连接'),
    h('p', { class: 'hint' }, '边 #' + index + '（definition.edges[' + index + ']）'),
  ];
}

// ---------------------------------------------------------------------------
// validation / export
// ---------------------------------------------------------------------------

function refreshLocalProblems(state) {
  state.localProblems = regionProblems(state.definition);
  return state.localProblems;
}

function renderErrorList(state) {
  const serverErrors = state.errors;
  if (serverErrors.length) {
    return h('div', { class: 'errors', id: 'canvas-errors' },
      serverErrors.map((error) =>
        h('button', {
          type: 'button',
          class: 'error-item' + (error.edgeIndex !== null ? ' error-edge' : ''),
          key: 'error-' + error.index,
          'data-node-id': error.nodeId,
          'data-code': error.code,
          'data-edge-id': error.edgeId || '',
          'data-field': 'error.' + error.index,
          onClick: () => focusError(state, error),
        }, [
          h('strong', {}, '[' + error.code + '] '),
          error.message + ' ',
          error.source && error.target
            ? h('code', {}, error.source + '→' + error.target + ' ')
            : null,
          h('code', {}, error.pointer || error.field),
          h('span', { class: 'error-hint' }, ' 修复：' + error.hint),
          error.nodeId ? h('span', { class: 'hint' }, ' · 定位到 ' + error.nodeId) : null,
        ]),
      ));
  }
  if (state.localProblems.length) {
    return h('div', { class: 'errors', id: 'canvas-local-problems' },
      state.localProblems.map((problem, index) =>
        h('button', {
          type: 'button',
          class: 'error-item' + (problem.edgeIndex !== null ? ' error-edge' : ''),
          key: 'local-' + index,
          'data-node-id': problem.nodeId,
          'data-code': problem.code,
          'data-field': 'local.' + index,
          onClick: () => focusLocalProblem(state, problem),
        }, [
          h('strong', {}, '[本地提示 ' + problem.code + '] '),
          problem.message + ' ',
          h('code', {}, problem.pointer || ''),
          problem.nodeId ? h('span', { class: 'hint' }, ' · 定位到 ' + problem.nodeId) : null,
        ]),
      ));
  }
  return h('div', { class: 'errors ok', 'data-field': 'errors' },
    state.manifestHash
      ? ['校验通过。服务端 manifest SHA256: ', h('code', { id: 'canvas-manifest-hash' }, state.manifestHash)]
      : '尚未发现定位错误。');
}

function focusError(state, error) {
  if (error.edgeIndex !== null) {
    const edge = state.flowEdges[error.edgeIndex] || null;
    state.highlightedEdgeId = error.edgeId || (edge ? edge.id : '');
    state.selectedEdgeId = state.highlightedEdgeId;
    state.selectedId = '';
    state.status = '[' + error.code + '] ' + (error.source && error.target
      ? error.source + '→' + error.target + '：' : '') + error.message;
    state.statusKind = 'bad';
    scrollCanvasIntoView(state);
    return;
  }
  state.highlightedEdgeId = '';
  state.selectedId = error.nodeId;
  state.selectedEdgeId = '';
  state.status = '[' + error.code + '] ' + error.message;
  state.statusKind = 'bad';
  ensureCallAppForSelection(state, error.nodeId);
  nextTick(() => {
    const element = fieldElement(state.root, error.field);
    if (element && typeof element.focus === 'function') {
      element.focus();
      scrollCanvasIntoView(state);
    }
  });
}

function focusLocalProblem(state, problem) {
  focusError(state, {
    code: problem.code,
    message: problem.message,
    hint: '',
    field: problem.code === 'bad_port' || problem.code === 'cross_region_edge' ? 'edge.port' : 'config',
    pointer: problem.pointer,
    nodeId: problem.nodeId,
    edgeIndex: problem.edgeIndex,
    edgeId: problem.edgeIndex !== null && state.flowEdges[problem.edgeIndex]
      ? state.flowEdges[problem.edgeIndex].id
      : '',
    source: '',
    target: '',
  });
}

function scrollCanvasIntoView(state) {
  const stage = state.root && state.root.querySelector ? state.root.querySelector('.canvas-stage') : null;
  if (stage && typeof stage.scrollIntoView === 'function') {
    stage.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }
}

async function validate(state) {
  state.status = '正在请求服务端校验…';
  state.statusKind = 'pending';
  state.manifestHash = '';
  const globalApi = typeof window !== 'undefined' ? window.apiFull : undefined;
  const globalCSRF = typeof window !== 'undefined' ? window.csrf : undefined;
  if (typeof globalApi !== 'function' || state.applicationId === '') {
    refreshLocalProblems(state);
    state.status = '页面 API 助手不可用，无法请求服务端校验；已显示本地规则提示。';
    state.statusKind = 'bad';
    return;
  }
  try {
    const token = typeof globalCSRF === 'function' ? await globalCSRF() : null;
    const result = await globalApi(
      '/api/v1/applications/' + encodeURIComponent(state.applicationId) + '/validate',
      { method: 'POST', csrf: token },
    );
    state.errors = mapValidationErrors(state.definition, result.payload && result.payload.errors);
    if (result.payload && result.payload.valid === true) {
      state.errors = [];
      state.manifestHash = result.headers && typeof result.headers.get === 'function'
        ? (result.headers.get('X-Manifest-SHA256') || '')
        : '';
      refreshLocalProblems(state);
      state.status = '校验通过。' + (state.localProblems.length
        ? '（本地仍有 ' + state.localProblems.length + ' 条提示，以服务端为准。）'
        : (state.manifestHash ? ' manifest SHA256 已显示。' : ''));
      state.statusKind = 'ok';
      return;
    }
    state.status = '校验失败：' + state.errors.length + ' 个定位错误。点击错误跳转到节点字段或高亮连接。';
    state.statusKind = 'bad';
    if (state.errors.length > 0) {
      focusError(state, state.errors[0]);
    }
  } catch (error) {
    refreshLocalProblems(state);
    state.status = '校验请求失败 [' + (error.code || 'error') + '] ' + error.message;
    state.statusKind = 'bad';
  }
}

function downloadExport(state) {
  try {
    const payload = exportPayload(state.definition, state.layout);
    const text = JSON.stringify(payload, null, 2);
    const link = document.createElement('a');
    const blob = new Blob([text], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    link.href = url;
    link.download = exportFilename(state.applicationId);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
    const dropped = Array.isArray(payload.dropped) ? payload.dropped.length : 0;
    state.status = '已导出 {definition, layout}（不含凭据/端点/运行输入/记忆数据）'
      + (dropped ? '；按白名单丢弃 ' + dropped + ' 个未展示字段。' : '。');
    state.statusKind = dropped ? 'pending' : 'ok';
    state.lastExportDropped = payload.dropped || [];
  } catch (error) {
    state.status = '导出被拒绝 [' + (error.code || 'error') + '] ' + error.message;
    state.statusKind = 'bad';
  }
}

async function loadCatalogs(state) {
  const globalApi = typeof window !== 'undefined' ? window.apiFull : undefined;
  if (typeof globalApi !== 'function') {
    state.catalogsState = 'error';
    state.catalogsError = '页面 API 助手不可用，工具/模型/知识库选择器为空。';
    return;
  }
  try {
    const [tools, models, knowledgeBases] = await Promise.all([
      globalApi('/api/v1/tools?limit=100'),
      globalApi('/api/v1/models?limit=100'),
      globalApi('/api/v1/knowledge-bases?limit=100'),
    ]);
    state.tools = Array.isArray(tools.payload && tools.payload.items) ? tools.payload.items : [];
    state.models = Array.isArray(models.payload && models.payload.items) ? models.payload.items : [];
    state.knowledgeBases = Array.isArray(knowledgeBases.payload && knowledgeBases.payload.items)
      ? knowledgeBases.payload.items
      : [];
    state.catalogsState = 'ready';
  } catch (error) {
    state.catalogsState = 'error';
    state.catalogsError = '加载工具/模型/知识库目录失败 [' + (error.code || 'error') + '] '
      + error.message;
  }
}

// ---------------------------------------------------------------------------
// graph <-> definition state
// ---------------------------------------------------------------------------

function syncGraphConfigs(state) {
  const byId = new Map(state.definition.nodes.map((node) => [node.id, node]));
  state.flowNodes = state.flowNodes.map((flowNode) => {
    const definitionNode = byId.get(flowNode.id);
    if (!definitionNode) {
      return flowNode;
    }
    const data = {
      ...flowNode.data,
      config: clone(definitionNode.config || {}),
    };
    if (definitionNode.type === 'condition') {
      data.ports = conditionPorts(definitionNode.config || {});
    }
    if (definitionNode.type === 'join') {
      data.sourceLabels = [...new Set(
        state.definition.edges
          .filter((edge) => edge.target === definitionNode.id)
          .map((edge) => edge.source),
      )];
    }
    return { ...flowNode, data };
  });
}

function syncLayout(state) {
  state.layout = layoutFromGraph(state.flowNodes, state.layout);
  emitChange(state);
}

function rebuild(state) {
  state.definition = graphToDefinition(state.flowNodes, state.flowEdges, state.definition);
  state.layout = layoutFromGraph(state.flowNodes, state.layout);
  syncGraphConfigs(state);
  refreshLocalProblems(state);
  emitChange(state);
}

function emitChange(state) {
  if (typeof state.options.onChange === 'function') {
    state.options.onChange(clone(state.definition), clone(state.layout));
  }
}

function commit(state) {
  syncGraphConfigs(state);
  refreshLocalProblems(state);
  emitChange(state);
}

// ---------------------------------------------------------------------------
// rendering
// ---------------------------------------------------------------------------

function renderFlowNode(props) {
  const data = (props && props.data) || {};
  const type = data.nodeType || 'python';
  const ports = type === 'condition'
    ? (Array.isArray(data.ports) && data.ports.length ? data.ports : conditionPorts(data.config || {}))
    : [];
  const handles = [];
  if (type !== 'start') {
    handles.push(h(Handle, {
      type: 'target',
      position: Position.Left,
      id: 'in',
      class: 'orchara-handle orchara-handle-target',
    }));
  }
  if (type === 'condition') {
    ports.forEach((port, index) => {
      const top = ((index + 1) / (ports.length + 1)) * 100;
      handles.push(h(Handle, {
        type: 'source',
        position: Position.Right,
        id: port,
        class: 'orchara-handle orchara-handle-source',
        style: { top: top + '%' },
      }));
      handles.push(h('span', {
        class: 'orchara-port-label',
        style: { top: top + '%' },
      }, port));
    });
  } else if (type !== 'end') {
    handles.push(h(Handle, {
      type: 'source',
      position: Position.Right,
      id: 'out',
      class: 'orchara-handle orchara-handle-source',
    }));
  }
  const height = type === 'condition'
    ? Math.max(48, ports.length * 26 + 28)
    : 48;
  const sourceLabels = type === 'join' && Array.isArray(data.sourceLabels)
    ? data.sourceLabels
    : [];
  return h('div', {
    class: 'orchara-flow-node orchara-node-' + type,
    style: { minHeight: height + 'px' },
  }, [
    nodeIcon(type),
    h('span', { class: 'orchara-flow-label' }, data.label || type),
    h('span', { class: 'orchara-flow-type' }, type),
    sourceLabels.length
      ? h('span', { class: 'orchara-flow-sources' }, '来源: ' + sourceLabels.join(' · '))
      : null,
    ...handles,
  ]);
}


// A small, consistent vector vocabulary for the ten existing node types.
function nodeIcon(type) {
  const symbols = {
    start: ['m9 5 10 7-10 7V5Z'],
    end: ['M6 6h12v12H6z'],
    python: ['m8 7-5 5 5 5', 'm16 7 5 5-5 5', 'm14 4-4 16'],
    agent: ['M5 7h14v13H5z', 'M12 3v4', 'M9 12v2', 'M15 12v2', 'M9 17h6'],
    model: ['m12 3 9 5-9 5-9-5 9-5Z', 'm3 12 9 5 9-5', 'm3 16 9 5 9-5'],
    retrieve: ['M3 4c4-1 7-1 9 1 2-2 5-2 9-1v15c-4-1-7-1-9 1-2-2-5-2-9-1V4Z', 'M12 5v15'],
    condition: ['m12 2 10 10-10 10L2 12 12 2Z', 'M12 8v5', 'M12 16h.01'],
    join: ['M5 3v4c0 3 7 3 7 6v8', 'M19 3v4c0 3-7 3-7 6', 'm8 17 4 4 4-4'],
    approval: ['M5 3h14v18H5z', 'm8 12 3 3 5-6'],
    call_app: ['M3 3h7v7H3z', 'M14 14h7v7h-7z', 'M14 5h5v5', 'm19 5-5 5'],
  };
  return h('span', { class: 'node-icon', 'aria-hidden': 'true' }, [
    h('svg', { viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': 1.7, 'stroke-linecap': 'round', 'stroke-linejoin': 'round' },
      (symbols[type] || symbols.model).map((d) => h('path', { d }))),
  ]);
}

function renderApp(state) {
  const nodes = state.flowNodes;
  const edges = state.highlightedEdgeId
    ? state.flowEdges.map((edge) => (edge.id === state.highlightedEdgeId
      ? { ...edge, class: (edge.class || 'orchara-edge') + ' orchara-edge-invalid' }
      : edge))
    : state.flowEdges;
  const selected = state.definition.nodes.find((node) => node.id === state.selectedId);
  const selectedEdgeIndex = state.selectedEdgeId
    ? state.flowEdges.findIndex((edge) => edge.id === state.selectedEdgeId)
    : -1;
  const selectedEdge = selectedEdgeIndex >= 0 ? state.flowEdges[selectedEdgeIndex] : null;
  const invalidNodeIds = new Set(state.errors.filter((error) => error.nodeId).map((error) => error.nodeId));

  return h('div', { class: 'orchara-canvas' }, [
    h('div', { class: 'canvas-toolbar' }, [
      h('div', { class: 'canvas-toolbar-heading' }, [h('strong', {}, '可视化编排'), h('span', {}, nodes.length + ' 个节点 · ' + edges.length + ' 条连线')]),
      h('div', { class: 'toolbar-actions' }, [
        h('button', {
          type: 'button',
          id: 'canvas-validate',
          class: 'primary',
          onClick: () => validate(state),
        }, '校验草稿'),
        h('button', {
          type: 'button',
          id: 'canvas-export',
          onClick: () => downloadExport(state),
        }, '导出工作流'),
      ]),
    ]),
    h('div', { class: 'canvas-body' }, [
      h('aside', { class: 'node-library', 'aria-label': '节点库' }, [h('h2', {}, '节点库'), h('p', { class: 'hint' }, '点击添加到画布'), h('div', { class: 'palette', role: 'group', 'aria-label': '节点面板' },
        NODE_TYPES.map((type) => h('button', {
          type: 'button',
          class: 'palette-button node-type-' + type,
          'data-node-type': type,
          disabled: !REPEATABLE_NODE_TYPES.includes(type)
            && state.definition.nodes.some((node) => node.type === type),
          onClick: () => {
            const result = addNode(nodes, edges, type, state.layout);
            if (!result.ok) {
              state.status = result.reason;
              state.statusKind = 'bad';
              return;
            }
            state.flowNodes = [...nodes, result.node];
            rebuild(state);
            state.selectedId = result.node.id;
            state.selectedEdgeId = '';
            state.status = '已添加 ' + NODE_LABELS[type] + '（' + result.node.id + '）。';
            state.statusKind = 'ok';
            ensureCallAppForSelection(state, result.node.id);
          },
        }, [nodeIcon(type), h('span', {}, NODE_LABELS[type]), h('span', { class: 'add-indicator', 'aria-hidden': 'true' }, '+')])))]),
      h('div', { class: 'canvas-stage' },
        h(VueFlow, {
          nodes,
          edges,
          fitViewOnInit: true,
          minZoom: 0.2,
          maxZoom: 2,
          'onNodesChange': (changes) => {
            state.flowNodes = applyNodeChanges(changes, state.flowNodes);
            syncLayout(state);
          },
          'onEdgesChange': (changes) => {
            state.flowEdges = applyEdgeChanges(changes, state.flowEdges);
            rebuild(state);
          },
          'onConnect': (connection) => {
            const verdict = canConnect(state.flowNodes, state.flowEdges, connection);
            if (!verdict.ok) {
              state.status = '拒绝连接：' + verdict.reason + ' [' + verdict.code + ']';
              state.statusKind = 'bad';
              return;
            }
            const port = verdict.port || '';
            const edge = {
              id: edgeIdFor({ source: connection.source, target: connection.target },
                state.flowEdges.length),
              source: connection.source,
              target: connection.target,
              sourceHandle: port || connection.sourceHandle || undefined,
              targetHandle: 'in',
              label: port,
              data: { port },
              class: 'orchara-edge' + (port ? ' orchara-edge-port' : ''),
            };
            state.flowEdges = [...state.flowEdges, edge];
            rebuild(state);
            state.status = '已连接 ' + connection.source + ' → ' + connection.target
              + (port ? '（端口 ' + port + '）' : '') + '。';
            state.statusKind = 'ok';
          },
          'onNodeClick': ({ node }) => {
            state.selectedId = node.id;
            state.selectedEdgeId = '';
            state.highlightedEdgeId = '';
            ensureCallAppForSelection(state, node.id);
          },
          'onEdgeClick': ({ edge }) => {
            state.selectedEdgeId = edge.id;
            state.selectedId = '';
          },
          'onNodeDragStop': () => syncLayout(state),
          'onPaneClick': () => {
            state.selectedId = '';
            state.selectedEdgeId = '';
          },
        }, {
          'node-default': (props) => renderFlowNode(props),
          default: () => [h(Background), h(Controls)],
        }),
      ),
      h('aside', { class: 'config-panel', 'aria-live': 'polite' }, [
        h('div', { class: 'panel-head' }, [
          h('h2', {}, selected
            ? (NODE_LABELS[selected.type] || selected.type) + ' · ' + selected.id
            : (selectedEdge ? '连接 · ' + selectedEdge.source + ' → ' + selectedEdge.target : '选择节点配置')),
          selected ? h('button', {
            type: 'button',
            class: 'link danger',
            id: 'canvas-delete-node',
            onClick: () => {
              const removed = removeNode(nodes, edges, selected.id);
              state.flowNodes = removed.nodes;
              state.flowEdges = removed.edges;
              state.selectedId = '';
              rebuild(state);
              state.status = '已删除节点 ' + selected.id + '；配对 join 的悬空来源已清理。';
              state.statusKind = 'ok';
            },
          }, '删除节点') : null,
        ]),
        !selected && !selectedEdge
          ? h('p', { class: 'hint' },
              '点击节点配置、点击连线编辑端口。条件节点按端口分出各分支，普通节点多出口前必须由 join 声明 fork；'
              + '只有 join 可以多入。Delete 删除选中的节点或连线。')
          : h('div', { class: 'panel-fields' }, [
              selected && selected.type === 'start' ? renderStartPanel(state.definition, state, selected) : null,
              selected && selected.type === 'python' ? renderPythonPanel(state.definition, state, selected) : null,
              selected && selected.type === 'agent' ? renderAgentPanel(state.definition, state, selected) : null,
              selected && selected.type === 'model' ? renderModelPanel(state.definition, state, selected) : null,
              selected && selected.type === 'retrieve' ? renderRetrievePanel(state.definition, state, selected) : null,
              selected && selected.type === 'call_app' ? renderCallAppPanel(state.definition, state, selected) : null,
              selected && selected.type === 'condition' ? renderConditionPanel(state.definition, state, selected) : null,
              selected && selected.type === 'join' ? renderJoinPanel(state.definition, state, selected) : null,
              selected && selected.type === 'approval' ? renderApprovalPanel(state.definition, state, selected) : null,
              selected && selected.type === 'end' ? renderEndPanel(state.definition, state, selected) : null,
              selected && !NODE_TYPES.includes(selected.type)
                ? [h('p', { class: 'hint' },
                    '该节点类型（' + selected.type + '）不在本画布的创作面板内；定义会原样保存，'
                    + '配置请用页面 JSON 视图编辑。')]
                : null,
              selectedEdge ? renderEdgePanel(state, selectedEdge, selectedEdgeIndex) : null,
              selected ? [
                h('h3', {}, '布局'),
                h('p', { class: 'hint' },
                  '位置保存在独立的 layout.positions，不进入执行定义，也不改变发布哈希。'),
              ] : null,
            ]),
        h('div', {
          id: 'canvas-status',
          class: 'status ' + (state.statusKind || ''),
          role: 'status',
        }, state.status || '画布已就绪。'),
        renderErrorList(state),
      ]),
    ]),
  ]);
}

function initialState(root, options) {
  const definition = options && options.definition && typeof options.definition === 'object'
    ? clone(options.definition)
    : defaultDefinition();
  if (!Array.isArray(definition.nodes) || definition.nodes.length === 0) {
    const fresh = defaultDefinition();
    definition.nodes = fresh.nodes;
    definition.edges = fresh.edges;
  }
  if (!definition.dsl_version) {
    definition.dsl_version = '1.0';
  }
  if (!definition.input_schema || typeof definition.input_schema !== 'object') {
    definition.input_schema = emptySchemaWithId();
  }
  if (!definition.output_schema || typeof definition.output_schema !== 'object') {
    definition.output_schema = emptySchemaWithId();
  }
  if (!definition.policy || typeof definition.policy !== 'object') {
    definition.policy = defaultDefinition().policy;
  }
  const layout = options && options.layout && typeof options.layout === 'object'
    && Object.keys(options.layout).length > 0
    ? clone(options.layout)
    : null;
  const effectiveLayout = layout && layout.positions ? layout : defaultLayout(definition);
  const graph = definitionToGraph(definition, effectiveLayout);
  return {
    root,
    options: options || {},
    applicationId: (options && options.applicationId) || applicationIdFromPath(window.location.pathname),
    definition,
    layout: effectiveLayout,
    flowNodes: graph.nodes,
    flowEdges: graph.edges,
    selectedId: 'start',
    selectedEdgeId: '',
    highlightedEdgeId: '',
    status: '画布已就绪。',
    statusKind: '',
    errors: [],
    localProblems: regionProblems(definition),
    manifestHash: '',
    tools: [],
    models: [],
    knowledgeBases: [],
    catalogsState: 'loading',
    catalogsError: '',
    callApp: {
      applications: [],
      state: 'loading',
      error: '',
      byNode: {},
      drafts: {},
    },
    schemaDrafts: {},
    inputDrafts: {},
    schemaText: {},
    schemaErrors: {},
    caseValueDrafts: {},
    proposalDrafts: {},
    lastExportDropped: [],
  };
}

export function mount(root, options) {
  if (!root) {
    throw new Error('canvas mount requires a root element');
  }
  const state = reactive(initialState(root, options));
  const app = createApp({ render: () => renderApp(state) });
  app.mount(root);
  emitChange(state);
  loadCatalogs(state);
  loadCallAppApplications(state);

  const onKeydown = (event) => {
    const tag = event.target && event.target.tagName ? event.target.tagName.toLowerCase() : '';
    if (['input', 'textarea', 'select'].includes(tag)) {
      return;
    }
    if ((event.key === 'Delete' || event.key === 'Backspace')) {
      if (state.selectedEdgeId) {
        state.flowEdges = state.flowEdges.filter((edge) => edge.id !== state.selectedEdgeId);
        state.selectedEdgeId = '';
        rebuild(state);
        event.preventDefault();
        return;
      }
      if (state.selectedId) {
        const removed = removeNode(state.flowNodes, state.flowEdges, state.selectedId);
        state.flowNodes = removed.nodes;
        state.flowEdges = removed.edges;
        state.selectedId = '';
        rebuild(state);
        event.preventDefault();
      }
    }
  };
  window.addEventListener('keydown', onKeydown);

  return {
    setDraft(definition, layout) {
      const next = initialState(root, {
        ...options,
        definition,
        layout,
      });
      state.definition = next.definition;
      state.layout = next.layout;
      state.flowNodes = next.flowNodes;
      state.flowEdges = next.flowEdges;
      state.selectedId = state.definition.nodes[0] ? state.definition.nodes[0].id : '';
      state.selectedEdgeId = '';
      state.highlightedEdgeId = '';
      state.errors = [];
      state.localProblems = next.localProblems;
      state.manifestHash = '';
      state.caseValueDrafts = {};
      state.schemaText = {};
      state.status = '已加载服务器草稿。';
      state.statusKind = 'ok';
      ensureCallAppForSelection(state, state.selectedId);
    },
    getDraft() {
      return { definition: clone(state.definition), layout: clone(state.layout) };
    },
    validate() {
      return validate(state);
    },
    exportDraft() {
      downloadExport(state);
    },
    regionProblems() {
      return regionProblems(state.definition);
    },
    destroy() {
      window.removeEventListener('keydown', onKeydown);
      app.unmount();
    },
  };
}

if (typeof window !== 'undefined') {
  window.OrcharaCanvas = { mount };
}
