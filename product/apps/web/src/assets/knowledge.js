/*
 * F10/T055 knowledge page helpers.
 *
 * The page is a real client of the contract paths plus the documented
 * non-contract retrieval surface (ADR 0010):
 *
 *   POST /api/v1/knowledge-bases                              -> 201 {id,state}
 *   GET  /api/v1/knowledge-bases                              -> {items,next_cursor}
 *   POST /api/v1/knowledge-bases/{id}/documents               -> 202 {id,state}
 *   GET  /api/v1/knowledge-bases/{id}/documents               -> {items,next_cursor}
 *   GET  /api/v1/knowledge-documents/{id}                     -> KnowledgeDocument
 *   GET  /api/v1/knowledge-documents/{id}/diagnostics         -> ingestion facts
 *   POST /api/v1/knowledge-bases/{id}/retrieval-queries       -> ranked chunks
 *   GET  /api/v1/knowledge-citations/{chunk_id}               -> one cited chunk
 *
 * The pure helpers live here so the Node page test can execute them. All
 * display strings are produced from data with String(); the page writes them
 * with textContent, never innerHTML. Nothing is stored in browser storage or
 * the URL.
 */

const KNOWLEDGE_MEDIA_TYPES = Object.freeze({
  '.txt': 'text/plain',
  '.md': 'text/markdown',
  '.csv': 'text/csv',
  '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
});

const DEFAULT_TOP_K = 8;
const MAX_TOP_K = 50;

const KNOWLEDGE_STATE_TEXT = Object.freeze({
  uploaded: '已上传',
  scanning: '扫描/排队中',
  indexing: '解析与索引中',
  ready: '可检索',
  failed: '摄取失败',
  deleting: '删除中',
  deleted: '已删除',
});

const KNOWLEDGE_FAILURE_TEXT = Object.freeze({
  ingest_empty_content: '解析结果为空：文档没有非空白正文，不能标记为可检索',
  ingest_unsupported_media_type: '该格式暂不支持解析（本切片支持 txt/md/csv/xlsx）',
  ingest_invalid_encoding: '内容不是合法的 UTF-8 文本',
  ingest_invalid_csv: 'CSV 格式无法解析（引号/换行不完整），已定位到出错行',
  ingest_invalid_xlsx: 'XLSX 结构无效（压缩包/工作簿/共享字符串/工作表），无法解析',
  ingest_formula_not_supported: '单元格含公式：首版不执行公式，也不会把缓存值当作内容',
  ingest_archive_limit_exceeded: 'XLSX 压缩包超过条目数/解压总量/压缩比上限',
  ingest_archive_path_traversal: 'XLSX 压缩包含路径穿越条目',
  ingest_macro_present: 'XLSX 含宏（vbaProject.bin / macrosheet），首版拒绝',
  ingest_dtd_not_supported: 'XLSX/XML 含 DTD 或实体声明，首版拒绝',
  ingest_worksheet_limit_exceeded: 'XLSX 工作表数量超过上限',
  ingest_row_limit_exceeded: '数据行数超过上限',
  ingest_cell_limit_exceeded: '单元格数量超过上限',
  ingest_column_limit_exceeded: '列数超过上限',
  ingest_row_too_large: '单行内容超过上限（行不会被拆成多个片段）',
  ingest_source_unavailable: '源文件尚未就绪或已被删除',
  ingest_source_changed: '源文件版本已变化，本次摄取结果未激活',
  ingest_too_many_chunks: '片段数量超过本切片上限',
  ingest_chunk_artifact_timeout: '片段封存超时，可稍后重试',
  ingest_incomplete: '片段封存不完整，未激活任何索引',
  ingest_citation_invalid: '片段引用不一致，索引未激活',
  ingest_cancelled: '摄取已取消',
  embedding_provider_not_configured: '未配置 embedding 提供方，无法生成向量',
  provider_not_configured: '未配置 embedding 提供方',
  source_deleted: '源文件已删除',
  artifact_not_ready: '源文件尚未完成封存',
});

function encodeSegment(value) {
  return encodeURIComponent(String(value === undefined || value === null ? '' : value));
}

function knowledgeBasesPath() {
  return '/api/v1/knowledge-bases';
}

function knowledgeBasePath(id) {
  return knowledgeBasesPath() + '/' + encodeSegment(id);
}

function knowledgeDocumentsPath(baseID) {
  return knowledgeBasePath(baseID) + '/documents';
}

function knowledgeDocumentPath(documentID) {
  return '/api/v1/knowledge-documents/' + encodeSegment(documentID);
}

function knowledgeDocumentDiagnosticsPath(documentID) {
  return knowledgeDocumentPath(documentID) + '/diagnostics';
}

function retrievalQueriesPath(baseID) {
  return knowledgeBasePath(baseID) + '/retrieval-queries';
}

function knowledgeCitationPath(chunkID) {
  return '/api/v1/knowledge-citations/' + encodeSegment(chunkID);
}

/** The declared media type for a knowledge upload, by extension only. */
function mediaTypeForKnowledgeFile(fileName) {
  const name = String(fileName || '');
  const dot = name.lastIndexOf('.');
  if (dot < 0) {
    return { ok: false, error: '文件需要 .txt / .md / .csv / .xlsx 扩展名' };
  }
  const extension = name.slice(dot).toLowerCase();
  const mediaType = KNOWLEDGE_MEDIA_TYPES[extension];
  if (!mediaType) {
    return { ok: false, error: '本切片支持 .txt / .md / .csv / .xlsx 知识资料' };
  }
  return { ok: true, mediaType: mediaType };
}

/** POST /api/v1/knowledge-bases body. */
function buildKnowledgeBaseBody(name) {
  const trimmed = String(name || '').trim();
  if (!trimmed) {
    return { ok: false, error: '知识库名称不能为空' };
  }
  if (Array.from(trimmed).length > 200) {
    return { ok: false, error: '知识库名称最多 200 字符' };
  }
  return { ok: true, body: { name: trimmed } };
}

/** POST /documents body: the existing artifact upload flow supplies the id. */
function buildIngestBody(artifactID) {
  const value = String(artifactID || '').trim();
  if (!value) {
    return { ok: false, error: '请先完成 txt/md 上传' };
  }
  return { ok: true, body: { source_artifact_id: value } };
}

/** POST retrieval-queries body. top_k defaults to 8 and is bounded 1..50. */
function buildRetrievalBody(query, topK) {
  const normalized = String(query || '').trim();
  if (!normalized) {
    return { ok: false, error: '检索问题不能为空' };
  }
  if (Array.from(normalized).length > 2000) {
    return { ok: false, error: '检索问题最多 2000 字符' };
  }
  let value = DEFAULT_TOP_K;
  if (topK !== undefined && topK !== null && String(topK).trim() !== '') {
    value = Number(topK);
    if (!Number.isInteger(value) || value < 1 || value > MAX_TOP_K) {
      return { ok: false, error: 'top_k 必须是 1..' + MAX_TOP_K + ' 的整数' };
    }
  }
  return { ok: true, body: { query: normalized, top_k: value } };
}

function formatSpan(span) {
  const value = span || {};
  const start = Number(value.start_char);
  const end = Number(value.end_char);
  const startLine = Number(value.start_line);
  const endLine = Number(value.end_line);
  if (!Number.isFinite(start) || !Number.isFinite(end)) {
    return '字符范围未知';
  }
  if (!Number.isFinite(startLine) || !Number.isFinite(endLine)) {
    return '字符 ' + start + '–' + end;
  }
  return '字符 ' + start + '–' + end + ' / 行 ' + startLine + '–' + endLine;
}

/**
 * The row range of a tabular citation (T059). CSV row numbers are logical record
 * numbers (header = row 1); XLSX row numbers are the worksheet's own rows, so a
 * range can have gaps and row_count may be smaller than end-start+1.
 */
function formatRowRange(citation) {
  const value = citation || {};
  const start = Number(value.row_start);
  const end = Number(value.row_end);
  if (!Number.isFinite(start) || !Number.isFinite(end) || start <= 0 || end < start) {
    return '';
  }
  let text = '表格行 ' + start + '–' + end;
  const headerRow = Number(value.header_row);
  if (Number.isFinite(headerRow) && headerRow > 0) {
    text += '（表头行 ' + headerRow + '）';
  }
  if (value.sheet_name) {
    text = '工作表 ' + String(value.sheet_name) + ' · ' + text;
  }
  const rowCount = Number(value.row_count);
  if (Number.isFinite(rowCount) && rowCount > 0 && rowCount !== end - start + 1) {
    text += ' · 共 ' + rowCount + ' 行';
  }
  return text;
}

function describeFailureDetail(detail) {
  const value = detail || null;
  if (!value) {
    return '';
  }
  const parts = [];
  if (value.sheet) {
    parts.push('工作表 ' + String(value.sheet));
  }
  if (Number(value.row) > 0) {
    parts.push('行 ' + Number(value.row));
  }
  if (value.column) {
    parts.push('列 ' + String(value.column));
  }
  if (value.cell) {
    parts.push('单元格 ' + String(value.cell));
  }
  if (value.part) {
    parts.push('位置 ' + String(value.part));
  }
  if (value.limit) {
    parts.push('上限 ' + String(value.limit));
  }
  if (Number(value.value) > 0) {
    parts.push('实测值 ' + Number(value.value));
  }
  return parts.join(' · ');
}

function documentStateText(state) {
  const key = String(state || '');
  return KNOWLEDGE_STATE_TEXT[key] || key || '未知状态';
}

function failureText(code) {
  const key = String(code || '');
  if (!key) {
    return '';
  }
  return KNOWLEDGE_FAILURE_TEXT[key] || key;
}

/** A document is polled until a terminal state; failed is terminal and diagnosable. */
function shouldPollDocument(state) {
  const key = String(state || '');
  return key === 'uploaded' || key === 'scanning' || key === 'indexing';
}

function describeDocument(document) {
  const value = document || {};
  const state = String(value.state || '');
  return {
    id: String(value.id || ''),
    knowledgeBaseId: String(value.knowledge_base_id || ''),
    sourceArtifactId: String(value.source_artifact_id || ''),
    state: state,
    stateText: documentStateText(state),
    revision: Number(value.revision || 0),
    terminal: !shouldPollDocument(state),
    searchable: state === 'ready',
  };
}

/**
 * A retrieval item is only shown as a citation when the citation document
 * actually agrees with the returned chunk. A fabricated or stale citation is
 * rendered as invalid instead of being repeated as if it were traceable.
 */
function describeChunk(item) {
  const value = item || {};
  const citation = value.citation || {};
  const span = value.span || citation.span || {};
  const chunkNo = Number(value.chunk_no);
  const revision = Number(value.document_revision);
  const documentID = String(value.document_id || '');
  const rowStart = Number(citation.row_start);
  const rowEnd = Number(citation.row_end);
  const rowRangeOk = !(Number.isFinite(rowStart) && rowStart > 0) ||
    (Number.isFinite(rowEnd) && rowEnd >= rowStart);
  const citationOk =
    String(citation.document_id || '') === documentID &&
    Number(citation.document_revision) === revision &&
    Number(citation.chunk_no) === chunkNo &&
    Number((citation.span || {}).start_char) === Number(span.start_char) &&
    Number((citation.span || {}).end_char) === Number(span.end_char) &&
    rowRangeOk;
  const rowRangeText = citationOk ? formatRowRange(citation) : '';
  return {
    chunkId: String(value.chunk_id || ''),
    documentId: documentID,
    documentRevision: revision,
    documentState: String(value.document_state || ''),
    chunkNo: Number.isFinite(chunkNo) ? chunkNo : 0,
    score: Number(value.score || 0),
    text: String(value.text || ''),
    spanText: formatSpan(span),
    rowRangeText: rowRangeText,
    citationOk: citationOk,
    citationText: citationOk
      ? '引用：文档 ' + documentID + ' · 版本 ' + revision + ' · 第 ' + chunkNo + ' 片段 · '
        + formatSpan(span) + (rowRangeText ? ' · ' + rowRangeText : '')
      : '引用不一致：不展示为该片段的来源',
  };
}

function describeDiagnostics(diagnostics) {
  const value = diagnostics || {};
  const job = value.background_job || null;
  return {
    state: String(value.state || ''),
    stateText: documentStateText(value.state),
    revision: Number(value.revision || 0),
    activeRevision: value.active_revision === null || value.active_revision === undefined
      ? null
      : Number(value.active_revision),
    errorCode: value.error_code ? String(value.error_code) : '',
    errorText: failureText(value.error_code),
    detail: value.detail || null,
    detailText: describeFailureDetail(value.detail),
    chunkCount: Number(value.chunk_count || 0),
    parserVersion: value.parser_version ? String(value.parser_version) : '',
    chunkerVersion: value.chunker_version ? String(value.chunker_version) : '',
    embeddingModelRevision: value.embedding_model_revision
      ? String(value.embedding_model_revision)
      : '',
    jobState: job ? String(job.state || '') : '',
    jobErrorCode: job && job.error_code ? String(job.error_code) : '',
    jobAttempts: job ? Number(job.attempt_count || 0) : 0,
  };
}

/** The retrieval envelope projects items and the versions the answer used. */
function describeRetrieval(payload) {
  const value = payload || {};
  const items = Array.isArray(value.items) ? value.items.map(describeChunk) : [];
  const retrieval = value.retrieval || {};
  return {
    items: items,
    empty: items.length === 0,
    emptyText: items.length === 0 ? '没有检索到可读片段（不是错误）' : '',
    embeddingModelRevision: String(retrieval.embedding_model_revision || ''),
    topK: Number(retrieval.top_k || 0),
    aclEpoch: Number(retrieval.acl_epoch || 0),
  };
}

/** One short status line for the document panel. */
function describeIngestion(document, diagnostics) {
  const doc = describeDocument(document);
  const diag = describeDiagnostics(diagnostics);
  const lines = [
    '状态：' + doc.stateText + '（revision ' + doc.revision + '）',
  ];
  if (diag.errorCode) {
    lines.push('失败码：' + diag.errorCode);
    lines.push('说明：' + diag.errorText);
  }
  if (diag.detailText) {
    lines.push('失败位置：' + diag.detailText);
  }
  lines.push('已写入片段：' + diag.chunkCount);
  if (diag.parserVersion) {
    lines.push('解析器：' + diag.parserVersion);
  }
  if (diag.embeddingModelRevision) {
    lines.push('embedding：' + diag.embeddingModelRevision);
  }
  if (diag.jobState) {
    lines.push('后台任务：' + diag.jobState + ' · 尝试 ' + diag.jobAttempts + ' 次');
  }
  return { states: doc, diagnostics: diag, text: lines.join('\n') };
}

function describeFailure(error) {
  if (!error) {
    return '未知错误';
  }
  return '[' + (error.code || 'error') + '] ' + (error.message || String(error));
}
