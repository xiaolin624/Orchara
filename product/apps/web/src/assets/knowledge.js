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
  '.pdf': 'application/pdf',
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
  ingest_unsupported_media_type: '该格式暂不支持解析（本切片支持 txt/md/csv/xlsx/pdf）',
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
  pdf_no_text_layer: 'PDF 没有可用文字层（或文字量低于门槛）：首版不做 OCR，不会当作解析成功',
  pdf_invalid: 'PDF 结构无法读取（交叉引用/对象/页面损坏）',
  pdf_encrypted: 'PDF 已加密：首版不解密，需先导出无密码版本',
  ingest_source_unavailable: '源文件尚未就绪或已被删除',
  ingest_source_changed: '源文件版本已变化，本次摄取结果未激活',
  ingest_too_many_chunks: '片段数量超过本切片上限',
  ingest_chunk_artifact_timeout: '片段封存超时，可稍后重试',
  ingest_incomplete: '片段封存不完整，未激活任何索引',
  ingest_citation_invalid: '片段引用不一致，索引未激活',
  ingest_cancelled: '摄取已取消',
  embedding_provider_not_configured: '未配置 embedding 提供方，无法生成向量',
  stale_revision: '源版本或索引版本已变化：这次旧构建不会激活任何索引',
  embedding_model_changed: '部署的 embedding 模型已变化：任务未写入任何向量',
  source_deleted: '源文件已删除，旧任务不能复活文档',
  index_build_in_progress: '已有索引构建在进行中，请等待或稍后重试',
  provider_not_configured: '未配置 embedding 提供方',
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

/**
 * The T061 non-contract rebuild entry. Rebuilding without a body re-indexes the
 * current source revision under the deployment's embedding model; an optional
 * source artifact id replaces the source revision (the old active index stays
 * readable until the guard activates the new one).
 */
function reindexDocumentPath(documentID) {
  return knowledgeDocumentPath(documentID) + '/reindex';
}

function buildReindexBody(sourceArtifactID) {
  const value = String(sourceArtifactID || '').trim();
  return { ok: true, body: value ? { source_artifact_id: value } : {} };
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
    return { ok: false, error: '文件需要 .txt / .md / .csv / .xlsx / .pdf 扩展名' };
  }
  const extension = name.slice(dot).toLowerCase();
  const mediaType = KNOWLEDGE_MEDIA_TYPES[extension];
  if (!mediaType) {
    return { ok: false, error: '本切片支持 .txt / .md / .csv / .xlsx / .pdf 知识资料' };
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

/**
 * The page attribution of a PDF citation (T060). Page numbers are 1-based; the
 * page-bounded chunker always cites exactly one page. A document whose text layer
 * is partial repeats the pages that had no text (`pages_without_text`), which the
 * page shows as empty pages instead of implying full coverage.
 */
function formatPageRange(citation) {
  const pdf = citation && citation.pdf;
  if (!pdf) {
    return '';
  }
  const start = Number(pdf.page_start);
  const end = Number(pdf.page_end);
  if (!Number.isFinite(start) || !Number.isFinite(end) || start <= 0 || end < start) {
    return '';
  }
  let text = start === end ? '第 ' + start + ' 页' : '第 ' + start + '–' + end + ' 页';
  const pageCount = Number(pdf.page_count);
  if (Number.isFinite(pageCount) && pageCount > 0) {
    text += '（共 ' + pageCount + ' 页）';
  }
  const empty = Array.isArray(pdf.pages_without_text)
    ? pdf.pages_without_text.map(Number).filter((page) => Number.isFinite(page) && page > 0)
    : [];
  if (empty.length > 0) {
    text += ' · 空白页 ' + empty.join('、');
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
  const pdf = citation.pdf && typeof citation.pdf === 'object' ? citation.pdf : null;
  const pdfStart = pdf ? Number(pdf.page_start) : 0;
  const pdfEnd = pdf ? Number(pdf.page_end) : 0;
  const pdfRangeOk = !pdf ||
    (Number.isFinite(pdfStart) && pdfStart > 0 && Number.isFinite(pdfEnd) &&
      pdfEnd >= pdfStart && Number(pdf.page_char_end) > Number(pdf.page_char_start));
  const citationOk =
    String(citation.document_id || '') === documentID &&
    Number(citation.document_revision) === revision &&
    Number(citation.chunk_no) === chunkNo &&
    Number((citation.span || {}).start_char) === Number(span.start_char) &&
    Number((citation.span || {}).end_char) === Number(span.end_char) &&
    rowRangeOk &&
    pdfRangeOk;
  const rowRangeText = citationOk ? formatRowRange(citation) : '';
  const pageRangeText = citationOk ? formatPageRange(citation) : '';
  const rangeText = [rowRangeText, pageRangeText].filter(Boolean).join(' · ');
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
    pageRangeText: pageRangeText,
    citationOk: citationOk,
    citationText: citationOk
      ? '引用：文档 ' + documentID + ' · 版本 ' + revision + ' · 第 ' + chunkNo + ' 片段 · '
        + formatSpan(span) + (rangeText ? ' · ' + rangeText : '')
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
    activeIndexVersion: value.active_index ? Number(value.active_index.version || 0) : 0,
    activeIndexModel: value.active_index
      ? String(value.active_index.embedding_model_revision || '')
      : '',
  };
}

/**
 * The index-version history of one document (T061). It shows the per-document
 * build version, the embedding model revision it was built with, the persisted
 * state/stage and chunk counter, the latest job attempts and the failure reason.
 * `active` marks the one version the activation pointer names; the page never
 * infers a percentage from these counters.
 */
const INDEX_STATE_TEXT = Object.freeze({
  building: '构建中',
  ready: '可检索',
  failed: '构建失败',
  superseded: '已被取代',
});

function describeIndexes(diagnostics) {
  const value = diagnostics || {};
  const raw = Array.isArray(value.indexes) ? value.indexes : [];
  const items = raw.map((item) => {
    const job = item.background_job || null;
    const version = Number(item.version || 0);
    const state = String(item.state || '');
    const stage = String(item.stage || state);
    const active = item.active === true;
    const model = String(item.embedding_model_revision || '');
    const chunkCount = Number(item.chunk_count || 0);
    const errorCode = item.error_code ? String(item.error_code) : '';
    const line = '索引 v' + version + ' · ' + model + ' · '
      + (INDEX_STATE_TEXT[state] || state)
      + (stage && stage !== state ? '（' + stage + '）' : '')
      + ' · 已写入片段 ' + chunkCount
      + (job
        ? ' · 任务 ' + String(job.state || '') + ' · 尝试 ' + Number(job.attempt_count || 0) + ' 次'
        : '')
      + (errorCode ? ' · 失败码 ' + errorCode : '')
      + (active ? ' · 当前激活' : '');
    return {
      version: version,
      state: state,
      stage: stage,
      active: active,
      embeddingModelRevision: model,
      chunkCount: chunkCount,
      errorCode: errorCode,
      jobState: job ? String(job.state || '') : '',
      jobAttempts: job ? Number(job.attempt_count || 0) : 0,
      line: line,
    };
  });
  const active = items.find((item) => item.active) || null;
  return {
    active: active,
    items: items,
    text: items.map((item) => item.line).join('\n'),
    activeText: active
      ? '当前激活索引 v' + active.version + ' · embedding=' + active.embeddingModelRevision
      : '没有激活的索引',
  };
}

/** The retrieval envelope projects items and the versions the answer used. */
function describeRetrieval(payload) {
  const value = payload || {};
  const items = Array.isArray(value.items) ? value.items.map(describeChunk) : [];
  const retrieval = value.retrieval || {};
  const index = retrieval.index || {};
  return {
    items: items,
    empty: items.length === 0,
    emptyText: items.length === 0 ? '没有检索到可读片段（不是错误）' : '',
    emptyNextText: items.length === 0
      ? '下一步：换一个关键词，或先确认资料已经“可检索”；空结果不会显示加载中，也不代表资料已删除。'
      : '',
    embeddingModelRevision: String(retrieval.embedding_model_revision || ''),
    activeIndexRevision: String(index.embedding_model_revision || ''),
    activeIndexes: Number(index.active_indexes || 0),
    excludedIndexes: Number(index.excluded_indexes || 0),
    indexText: '激活索引 ' + Number(index.active_indexes || 0)
      + ' 个 · embedding=' + String(index.embedding_model_revision || '')
      + (Number(index.excluded_indexes || 0) > 0
        ? ' · 其他模型 revision 的索引 ' + Number(index.excluded_indexes) + ' 个（不混用）'
        : ''),
    topK: Number(retrieval.top_k || 0),
    aclEpoch: Number(retrieval.acl_epoch || 0),
  };
}

/*
 * F12/T098 (AC2): opening a citation re-reads it. A 404/403 means the source is
 * revoked, deleted or no longer readable — the page must clear the body it showed
 * earlier and must never fall back to the retrieval/answer's cached citation text.
 * `clearBody` is the contract the page wiring follows; `text` carries the stable
 * code and the next step.
 */
function citationOpenSuccess(chunk) {
  const opened = describeChunk(chunk);
  return {
    available: opened.citationOk,
    clearBody: false,
    code: '',
    status: 200,
    text: opened.citationOk
      ? '引用当前可读（已按当前授权与文档版本重新校验）'
      : '引用不一致：不展示为该片段的来源',
    chunk: opened,
  };
}

function citationOpenFailure(error) {
  const status = Number((error && error.status) || 0);
  const code = String((error && error.code) || '');
  let reason;
  if (status === 404) {
    reason = '引用不可用：来源已被撤销或删除（resource_not_found）。';
  } else if (status === 403) {
    reason = '引用不可用：当前授权已不能读取这个来源。';
  } else {
    reason = '引用不可用：' + describeFailure(error);
  }
  return {
    available: false,
    clearBody: true,
    code: code,
    status: status,
    text: reason + ' 页面不会回退此前读到的正文；下一步：重新检索，或重新上传/授权资料。',
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
  const indexes = describeIndexes(diagnostics);
  if (indexes.active) {
    lines.push('当前激活索引：v' + indexes.active.version
      + ' · embedding=' + indexes.active.embeddingModelRevision);
  }
  if (indexes.items.length > 0) {
    lines.push(indexes.text);
  }
  return { states: doc, diagnostics: diag, indexes: indexes, text: lines.join('\n') };
}

/*
 * F10/T064: the grounded answer view.
 *
 * POST /api/v1/knowledge-bases/{id}/answer returns the answer only when every
 * citation came from this retrieval and was readable at assembly time. The
 * outcomes are distinct and must not be collapsed in the page:
 *
 *   answered               -> answer + citations (each opens the citation route)
 *   no_results             -> empty answer, NOT an error
 *   answer_without_citation-> refused: the generated answer cited nothing
 *   citation_not_found     -> refused: a citation is not in this retrieval
 *   citation_unreadable    -> refused: a citation is no longer readable now
 *
 * A citation is only displayed as a source when its provenance agrees with the
 * returned chunk AND its chunk id is in the answer's own retrieval set. Opening a
 * citation always re-reads it; a 404 is shown as "unavailable", never as the
 * answer's cached citation text.
 */
const ANSWER_OUTCOME_TEXT = Object.freeze({
  answered: '已回答：引用已通过服务端校验（来自本次检索且当前可读）',
  no_results: '没有检索到可读资料：返回空答案（不是错误）',
  answer_without_citation: '回答被拒绝：生成结果没有引用任何片段',
  citation_not_found: '回答被拒绝：引用不属于本次检索',
  citation_unreadable: '回答被拒绝：引用在返回前已不可读（已撤销或版本变化）',
});

function knowledgeAnswerPath(baseID) {
  return knowledgeBasePath(baseID) + '/answer';
}

/** POST /answer body: the same {query, top_k} bounds as the retrieval body. */
function buildAnswerBody(query, topK) {
  return buildRetrievalBody(query, topK);
}

function answerOutcomeText(outcome) {
  const key = String(outcome || '');
  return ANSWER_OUTCOME_TEXT[key] || (key || '未知结果');
}

/** The generation label is always shown; a stub is never presented as a model. */
function describeAnswerGeneration(generation) {
  const value = generation || {};
  const stub = value.stub === true;
  const version = String(value.version || '');
  const kind = String(value.kind || '');
  const modelProvider = String(value.model_provider || '');
  let text;
  if (stub) {
    text = '生成方式：抽取式 stub' + (version ? '（' + version + '）' : '')
      + '；未配置模型 provider';
  } else {
    text = '生成方式：' + (kind || '未知') + (version ? '（' + version + '）' : '');
  }
  return {
    kind: kind,
    version: version,
    stub: stub,
    modelProvider: modelProvider,
    text: text,
  };
}

/** The retrieval the answer is grounded in: its chunk-id set and versions. */
function describeAnswerRetrieval(retrieval) {
  const value = retrieval || {};
  const chunkIds = Array.isArray(value.chunk_ids) ? value.chunk_ids.map(String) : [];
  const revision = String(value.embedding_model_revision || '');
  const topK = Number(value.top_k || 0);
  const aclEpoch = Number(value.acl_epoch || 0);
  const index = value.index || {};
  const activeIndexRevision = String(index.embedding_model_revision || '');
  return {
    embeddingModelRevision: revision,
    activeIndexRevision: activeIndexRevision,
    activeIndexes: Number(index.active_indexes || 0),
    excludedIndexes: Number(index.excluded_indexes || 0),
    topK: topK,
    aclEpoch: aclEpoch,
    chunkIds: chunkIds,
    chunkCount: chunkIds.length,
    text: '本次检索：' + chunkIds.length + ' 个片段 · embedding=' + revision
      + ' · 激活索引=' + activeIndexRevision + ' · top_k=' + topK
      + ' · acl_epoch=' + aclEpoch,
  };
}

/**
 * One answer citation. It keeps the same citation-consistency rule as a
 * retrieval chunk and adds the retrieval-membership rule of the answer contract:
 * a chunk id outside this answer's retrieval is never presented as its source.
 */
function describeAnswerCitation(item, allowedChunkIDs) {
  const chunk = describeChunk(item);
  let allowed = null;
  if (allowedChunkIDs && typeof allowedChunkIDs.has === 'function') {
    allowed = allowedChunkIDs;
  } else if (Array.isArray(allowedChunkIDs)) {
    allowed = new Set(allowedChunkIDs.map((value) => String(value)));
  }
  const inThisRetrieval = allowed ? allowed.has(chunk.chunkId) : true;
  const citationOk = chunk.citationOk && inThisRetrieval;
  let citationText;
  if (!inThisRetrieval) {
    citationText = '引用不属于本次检索：不展示为该片段的来源';
  } else if (!chunk.citationOk) {
    citationText = '引用不一致：不展示为该片段的来源';
  } else {
    citationText = chunk.citationText;
  }
  return {
    chunkId: chunk.chunkId,
    documentId: chunk.documentId,
    documentRevision: chunk.documentRevision,
    chunkNo: chunk.chunkNo,
    score: chunk.score,
    text: chunk.text,
    spanText: chunk.spanText,
    citationOk: citationOk,
    inThisRetrieval: inThisRetrieval,
    citationText: citationText,
  };
}

/** The answer envelope projected for the page (no answer body on no_results). */
function describeAnswer(payload) {
  const value = payload || {};
  const outcome = String(value.outcome || '');
  const answer = value.answer || {};
  const retrieval = describeAnswerRetrieval(value.retrieval);
  const allowed = new Set(retrieval.chunkIds);
  const citations = Array.isArray(answer.citations)
    ? answer.citations.map((item) => describeAnswerCitation(item, allowed))
    : [];
  return {
    outcome: outcome,
    outcomeText: answerOutcomeText(outcome),
    empty: outcome === 'no_results',
    text: String(answer.text || ''),
    generation: describeAnswerGeneration(answer.generation),
    retrieval: retrieval,
    citations: citations,
    citationsOk: citations.every((citation) => citation.citationOk),
  };
}

/** A refused answer keeps its own code, status and message for the page. */
function describeAnswerRefusal(error) {
  const value = error || {};
  const code = String(value.code || '');
  const details = value.details || {};
  const outcome = String(details.outcome || code);
  return {
    code: code,
    status: Number(value.status || 0),
    message: String(value.message || ''),
    outcome: outcome,
    outcomeText: answerOutcomeText(outcome),
    chunkID: details.chunk_id ? String(details.chunk_id) : '',
  };
}

function describeFailure(error) {
  if (!error) {
    return '未知错误';
  }
  return '[' + (error.code || 'error') + '] ' + (error.message || String(error));
}
