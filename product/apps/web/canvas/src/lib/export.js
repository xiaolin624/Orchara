/*
 * F04A/T040 export sanitizer.
 *
 * Export downloads exactly what the page would PUT: {definition, layout}. The
 * definition contract has no field for secrets, run inputs, memory or personal
 * data, but the check is explicit so a future config field cannot smuggle a
 * credential into an export unnoticed. Layout is view-only and never part of
 * the execution hash.
 */

const SENSITIVE_KEY = /(secret|token|password|passwd|api[_-]?key|credential|bearer|oauth|memory|personal|run[_-]?input)/i;

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
        '导出被拒绝：definition 含有凭据/运行输入/记忆类字段 ' + JSON.stringify(key) + '。',
      );
    }
  });
  walk(layout, '#/layout', (key, path) => {
    if (SENSITIVE_KEY.test(key)) {
      throw new ExportRefused(
        'sensitive_key',
        path,
        '导出被拒绝：layout 含有凭据/运行输入/记忆类字段 ' + JSON.stringify(key) + '。',
      );
    }
  });
  return true;
}

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

export function exportPayload(definition, layout) {
  const payload = {
    definition: clone(definition && typeof definition === 'object' ? definition : {}),
    layout: clone(layout && typeof layout === 'object' ? layout : {}),
  };
  assertExportSafe(payload.definition, payload.layout);
  return payload;
}

export function exportJSON(definition, layout) {
  return JSON.stringify(exportPayload(definition, layout), null, 2);
}

export function exportFilename(applicationId) {
  return 'orchara-canvas-' + (applicationId || 'draft') + '.json';
}
