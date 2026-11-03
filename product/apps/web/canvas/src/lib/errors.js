/*
 * F04A/T040 validation-error mapping.
 *
 * The server's located errors are {node_id?, code, pointer, message}. The canvas
 * must select the node and focus the config field instead of only printing the
 * HTTP status. This module is pure so the Node test can pin the mapping,
 * including errors that name a node but carry an out-of-date pointer and
 * document-level errors that name no node.
 */

import { unescapePointer } from './sources.js';

export function nodeIndexFromPointer(pointer) {
  const match = /^\/nodes\/(\d+)(?:\/|$)/.exec(String(pointer || ''));
  return match ? Number(match[1]) : null;
}

export function nodeIdForError(definition, error) {
  const nodes = definition && Array.isArray(definition.nodes) ? definition.nodes : [];
  if (error && error.node_id) {
    return nodes.some((node) => node.id === error.node_id) ? error.node_id : '';
  }
  const index = nodeIndexFromPointer(error && error.pointer);
  return index !== null && index >= 0 && index < nodes.length ? nodes[index].id : '';
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

export function mapValidationErrors(definition, errors) {
  const list = Array.isArray(errors) ? errors : [];
  return list.map((error, index) => {
    const nodeId = nodeIdForError(definition, error);
    return {
      index,
      nodeId,
      field: fieldForError(error && error.pointer),
      pointer: error && error.pointer ? error.pointer : '',
      code: (error && error.code) || 'validation_failed',
      message: (error && error.message) || 'the server refused this definition',
      documentLevel: nodeId === '',
    };
  });
}

export function errorsForNode(mapped, nodeId) {
  return (mapped || []).filter((error) => error.nodeId === nodeId);
}

export function documentLevelErrors(mapped) {
  return (mapped || []).filter((error) => error.documentLevel);
}
