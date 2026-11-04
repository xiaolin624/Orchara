/*
 * F04A/T040 canvas: a real Vue 3 + Vue Flow app bundled to a committed asset.
 *
 * The canvas is the primary editor. It shows exactly four node types
 * (start/python/agent/end), refuses every non-linear connection with an
 * explanation, and keeps the JSON view (the page's textareas) in sync so the
 * existing save/ETag/conflict behaviour in editor.js is unchanged.
 *
 * The page owns save; this app owns graph editing, the config panel, the
 * validate call (POST /applications/{id}/validate) and export. Pure logic lives
 * in ./lib/*.js and is executed by Node tests; this file is the DOM/Vue wiring.
 */

import { createApp, h, reactive, nextTick, ref } from 'vue';
import { VueFlow, applyNodeChanges, applyEdgeChanges } from '@vue-flow/core';
import { Background } from '@vue-flow/background';
import { Controls } from '@vue-flow/controls';
import '@vue-flow/core/dist/style.css';
import '@vue-flow/core/dist/theme-default.css';
import '@vue-flow/controls/dist/style.css';
import './canvas.css';

import {
  NODE_LABELS,
  NODE_TYPES,
  addNode,
  canConnect,
  defaultDefinition,
  defaultLayout,
  definitionToGraph,
  emptySchema,
  graphToDefinition,
  layoutFromGraph,
  linearChainProblems,
  removeNode,
} from './lib/dsl.js';
import { legalSources, sourceGroups, sourceType } from './lib/sources.js';
import { mapValidationErrors } from './lib/errors.js';
import { exportFilename, exportJSON } from './lib/export.js';

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
    h('p', { class: 'hint' }, '只提供应用输入属性与上游节点输出属性；下游和自身不会出现在列表里。'),
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

function renderOutputSchemaEditor(definition, state, node) {
  const text = JSON.stringify(node.output_schema || emptySchemaWithId(), null, 2);
  const errorKey = node.id + '.schema';
  return [
    h('h3', {}, '输出 Schema'),
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
    h('p', { class: 'hint' }, 'subapp_versions 固定为空数组（F04A 不支持子应用调用）。'),
    ...renderMappingEditor(definition, state, node),
    ...renderOutputSchemaEditor(definition, state, node),
  ];
}

function renderErrorList(state) {
  if (!state.errors.length) {
    return h('div', { class: 'errors ok', 'data-field': 'errors' },
      state.manifestHash
        ? ['校验通过。服务端 manifest SHA256: ', h('code', { id: 'canvas-manifest-hash' }, state.manifestHash)]
        : '尚未发现定位错误。');
  }
  return h('div', { class: 'errors', id: 'canvas-errors' },
    state.errors.map((error) =>
      h('button', {
        type: 'button',
        class: 'error-item',
        key: 'error-' + error.index,
        'data-node-id': error.nodeId,
        'data-field': 'error.' + error.index,
        onClick: () => {
          state.selectedId = error.nodeId;
          state.status = '[' + error.code + '] ' + error.message;
          state.statusKind = 'bad';
          nextTick(() => {
            const element = fieldElement(state.root, error.field);
            if (element && typeof element.focus === 'function') {
              element.focus();
            }
          });
        },
      }, [
        h('strong', {}, '[' + error.code + '] '),
        error.message + ' ',
        h('code', {}, error.pointer || error.field),
        error.nodeId ? h('span', { class: 'hint' }, ' · 跳转到 ' + error.nodeId) : null,
      ]),
    ));
}

function renderApp(state) {
  const nodes = state.flowNodes;
  const edges = state.flowEdges;
  const selected = state.definition.nodes.find((node) => node.id === state.selectedId);

  return h('div', { class: 'orchara-canvas' }, [
    h('div', { class: 'canvas-toolbar' }, [
      h('div', { class: 'palette', role: 'group', 'aria-label': '节点面板' },
        NODE_TYPES.map((type) => h('button', {
          type: 'button',
          class: 'palette-button',
          'data-node-type': type,
          disabled: state.definition.nodes.some((node) => node.type === type),
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
          },
        }, '+ ' + NODE_LABELS[type]))),
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
        }, '导出 {definition, layout}'),
      ]),
    ]),
    h('div', { class: 'canvas-body' }, [
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
            const verdict = canConnect(nodes, edges, connection);
            if (!verdict.ok) {
              state.status = '拒绝连接：' + verdict.reason + ' [' + verdict.code + ']';
              state.statusKind = 'bad';
              return;
            }
            state.flowEdges = [...edges, {
              id: connection.source + '->' + connection.target + ':' + edges.length,
              source: connection.source,
              target: connection.target,
              class: 'orchara-edge',
            }];
            rebuild(state);
            state.status = '已连接 ' + connection.source + ' → ' + connection.target + '。';
            state.statusKind = 'ok';
          },
          'onNodeClick': ({ node }) => {
            state.selectedId = node.id;
          },
          'onNodeDragStop': () => syncLayout(state),
          'onPaneClick': () => { state.selectedId = ''; },
        }, () => [h(Background), h(Controls)]),
      ),
      h('aside', { class: 'config-panel', 'aria-live': 'polite' }, [
        h('div', { class: 'panel-head' }, [
          h('h2', {}, selected ? (NODE_LABELS[selected.type] + ' · ' + selected.id) : '选择节点配置'),
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
            },
          }, '删除节点') : null,
        ]),
        !selected
          ? h('p', { class: 'hint' },
              '点击画布节点进行配置。只允许 start → python → agent → end 的单条线性链；' +
              '拖动移动节点，连接端口拉线，Delete 删除选中节点。')
          : h('div', { class: 'panel-fields' }, [
              selected.type === 'start' ? renderStartPanel(state.definition, state, selected) : null,
              selected.type === 'python' ? renderPythonPanel(state.definition, state, selected) : null,
              selected.type === 'agent' ? renderAgentPanel(state.definition, state, selected) : null,
              selected.type === 'end' ? renderEndPanel(state.definition, state, selected) : null,
              h('h3', {}, '布局'),
              h('p', { class: 'hint' },
                '位置保存在独立的 layout.positions，不进入执行定义，也不改变发布哈希。'),
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

function syncLayout(state) {
  state.layout = layoutFromGraph(state.flowNodes, state.layout);
  emitChange(state);
}

function rebuild(state) {
  state.definition = graphToDefinition(state.flowNodes, state.flowEdges, state.definition);
  state.layout = layoutFromGraph(state.flowNodes, state.layout);
  emitChange(state);
}

function emitChange(state) {
  if (typeof state.options.onChange === 'function') {
    state.options.onChange(clone(state.definition), clone(state.layout));
  }
}

function commit(state) {
  emitChange(state);
}

async function validate(state) {
  state.status = '正在请求服务端校验…';
  state.statusKind = 'pending';
  state.manifestHash = '';
  const globalApi = typeof window !== 'undefined' ? window.apiFull : undefined;
  const globalCSRF = typeof window !== 'undefined' ? window.csrf : undefined;
  if (typeof globalApi !== 'function' || state.applicationId === '') {
    state.status = '页面 API 助手不可用，无法校验。';
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
      state.manifestHash = result.headers && typeof result.headers.get === 'function'
        ? (result.headers.get('X-Manifest-SHA256') || '')
        : '';
      state.status = '校验通过。' + (state.manifestHash ? ' manifest SHA256 已显示。' : '');
      state.statusKind = 'ok';
      return;
    }
    state.status = '校验失败：' + state.errors.length + ' 个定位错误。点击错误跳转到节点字段。';
    state.statusKind = 'bad';
    if (state.errors.length > 0) {
      state.selectedId = state.errors[0].nodeId || state.selectedId;
      nextTick(() => {
        const element = fieldElement(state.root, state.errors[0].field);
        if (element && typeof element.focus === 'function') {
          element.focus();
        }
      });
    }
  } catch (error) {
    state.status = '校验请求失败 [' + (error.code || 'error') + '] ' + error.message;
    state.statusKind = 'bad';
  }
}

function downloadExport(state) {
  try {
    const text = exportJSON(state.definition, state.layout);
    const link = document.createElement('a');
    const blob = new Blob([text], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    link.href = url;
    link.download = exportFilename(state.applicationId);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
    state.status = '已导出 {definition, layout}（不含凭据/运行输入/记忆数据）。';
    state.statusKind = 'ok';
  } catch (error) {
    state.status = '导出被拒绝 [' + (error.code || 'error') + '] ' + error.message;
    state.statusKind = 'bad';
  }
}

async function loadCatalogs(state) {
  const globalApi = typeof window !== 'undefined' ? window.apiFull : undefined;
  if (typeof globalApi !== 'function') {
    state.catalogsState = 'error';
    state.catalogsError = '页面 API 助手不可用，工具/模型选择器为空。';
    return;
  }
  try {
    const [tools, models] = await Promise.all([
      globalApi('/api/v1/tools?limit=100'),
      globalApi('/api/v1/models?limit=100'),
    ]);
    state.tools = Array.isArray(tools.payload && tools.payload.items) ? tools.payload.items : [];
    state.models = Array.isArray(models.payload && models.payload.items) ? models.payload.items : [];
    state.catalogsState = 'ready';
  } catch (error) {
    state.catalogsState = 'error';
    state.catalogsError = '加载工具/模型目录失败 [' + (error.code || 'error') + '] ' + error.message;
  }
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
    status: '画布已就绪。',
    statusKind: '',
    errors: [],
    manifestHash: '',
    tools: [],
    models: [],
    catalogsState: 'loading',
    catalogsError: '',
    schemaDrafts: {},
    inputDrafts: {},
    schemaText: {},
    schemaErrors: {},
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

  const onKeydown = (event) => {
    const tag = event.target && event.target.tagName ? event.target.tagName.toLowerCase() : '';
    if (['input', 'textarea', 'select'].includes(tag)) {
      return;
    }
    if ((event.key === 'Delete' || event.key === 'Backspace') && state.selectedId) {
      const removed = removeNode(state.flowNodes, state.flowEdges, state.selectedId);
      state.flowNodes = removed.nodes;
      state.flowEdges = removed.edges;
      state.selectedId = '';
      rebuild(state);
      event.preventDefault();
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
      state.errors = [];
      state.manifestHash = '';
      state.status = '已加载服务器草稿。';
      state.statusKind = 'ok';
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
    destroy() {
      window.removeEventListener('keydown', onKeydown);
      app.unmount();
    },
  };
}

if (typeof window !== 'undefined') {
  window.OrcharaCanvas = { mount };
}
