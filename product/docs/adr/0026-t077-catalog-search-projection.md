# ADR 0026 — F08/T077 目录检索非契约面、投影/权威分离与索引落后回退

- 状态：accepted
- 日期：2026-11-05
- 责任票：T077（GitHub #78，父 F08）；复用 ADR 0021（T068 发布）、ADR 0022（T070 撤销）、
  ADR 0023（T074 请求/会话）
- 影响范围：`db/migrations/0045_catalog_index.sql`、`internal/catalog/{index,rebuild}.go`、
  `internal/background/{job.go,handlers/catalog_index.go}`、`internal/api/{server,catalog}.go`、
  `cmd/admin/catalog.go`、`apps/web/src/{catalog.html,assets/catalog.js}`、`apps/web/src/apps.html`

## 背景

契约（`contracts/openapi.json`）的 `listApplications` 是
`{items:[Application], next_cursor}`，`Application` 是 `additionalProperties:false` 的
`{id,name,active_version_id}`；没有任何 catalog search operation，也没有用途卡字段。
`contracts/database.sql` 声明的 `application_catalog_entries` 含 `embedding vector`，但本机
PostgreSQL 16.4 没有 pgvector（T055 在 `knowledge_chunks` 已记录同一事实）。规格
（`ROUTING-MEMORY-KNOWLEDGE.md` §5/§9/§27、`BACKGROUND-JOBS.md` §24、`DATA-MODEL.md` §20/§56）
要求：先取得当前可读且未撤销的已发布版本，再做能力卡检索；目录变更经事务 outbox 更新索引；
检索返回后再次核对版本/ACL；索引未覆盖时回退到授权范围全文检索；目录索引与私人知识索引
分表、分授权逻辑、分重建路径；不能先对全库生成候选再在 UI 隐藏。

## 决策

1. **检索走独立的非契约端点，契约响应零变化。** 新增
   `GET /api/v1/catalog/search`，响应
   `{items:[{application_id,application_name,version_id,version_no,manifest_sha256,
   capability_card}], next_cursor, retrieval}`；`retrieval` 含
   `{source,stale,index_state,projection_epoch,rechecked,rejected_by_recheck}`。
   `GET /api/v1/applications` 的参数、schema 与序列化保持不变（测试逐字段断言）。
   页面为 `GET /catalog` + `/assets/catalog.js`。沿用 ADR 0005 的非契约桥先例：
   不改 `docs/spec/**` / `contracts/**`，`python tools/sync_spec.py --check` 保持干净。
2. **投影表只存可重建的派生事实，并显式记录与契约的偏差（0045）。**
   `application_catalog_entries` 保留契约的 `application_version_id`(PK)、`search_text`、
   `source_manifest_sha256`、`updated_at`，**不建** `embedding_model_revision`/`embedding`：
   pgvector 不可用，T077 只实现全文路径，ANN/向量空间属于 T064/T067，不在目录里长第二套
   未测向量。新增 `application_id`（让 ACL 谓词在 LIMIT 前进 SQL；读取时用权威
   version/application 重新核对）与生成列 `ts`（`to_tsvector('simple', search_text)` + GIN）。
   `platform.catalog_index_state` 是单行台账（`id='catalog'`）：`state`、`projection_epoch`、
   `rebuild_started_at`、`rebuilt_at`、`last_error_code`/`last_error_at`、
   `expected_count`/`indexed_count`。
3. **ACL 与发布过滤在 SQL 内、排名/LIMIT 之前。** 索引路径与回退路径都把
   `applicationReadPredicate`（owner 或未过期 read grant）、`applications.archived_at IS NULL`、
   `application_versions.revoked_at IS NULL`（索引路径）放进同一条 WHERE；索引路径还要求
   `e.application_id = v.application_id` 且 `e.source_manifest_sha256 = v.manifest_sha256`。
   排序是确定的两级 keyset：用途文本命中（rank 0）先于元数据命中（rank 1），同级
   `version_no DESC, id DESC`；cursor 编码 `(rank, version_no, id)`，因此翻页在索引/回退模式
   切换时仍是同一全序。不可见/撤销行不能占页槽或把可见行挤出首页。
4. **陈旧判定以权威表为准，台账只是可观测性。** 仅当台账 `state='ready'` 且
   权威“可读、未撤销、未归档、有非空能力卡”的版本数等于“仍匹配当前 manifest hash 的合法
   投影行数”时才服务索引。`application_catalog_entries` 缺失、hash 不匹配、台账
   `absent/rebuilding/failed`、数量不一致都判 stale。手改台账计数不能让半成品看起来完整。
5. **落后时回退授权全文并二次查权。** stale 时改为对 `application_versions.capability_card`
   （只经过 ACL + 归档过滤）做全文匹配；返回前逐条再次读取
   `application_versions JOIN applications`（ACL + `revoked_at` + `archived_at` + 非空
   purpose），拒绝者计入 `rejected_by_recheck`。这是 `ROUTING:9` “检索返回后再次核对
   版本/ACL”的落点，也是测试可观察的“第二次授权检查”：回退候选里的已撤销版本被拒绝，
   而索引路径同样对每行执行独立复读。`capability_card` 不是带非空 `purpose` 的 JSON
   对象时同样被拒绝，避免
   泄漏畸形元数据：能力卡必须是带非空 `purpose` 的 JSON 对象，出现的五个数组字段必须是
   字符串数组。
6. **重建是可观察的真实进程。** `orchara-admin catalog rebuild [--all|--version UUID]`：
   全量重建先提交 `state='rebuilding'` 与新 `projection_epoch`（不读版本表），逐个版本一事务
   写入并以同一事务推进 `indexed_count`，最后提交 `state='ready'` 并在同一事务把
   `platform.outbox` 中待投递的 `catalog_index` 信号标为 delivered（`attempts+1`）。
   失败记录 `last_error_code`/`last_error_at` 且保持 `failed`；进程被中断则台账停在
   `rebuilding` 且保留 epoch/开始时间。两种情况下 search 都不会服务半成品，只回退。
   重复 migrate 是 no-op；外键、索引与零授权边界与 0007–0044 一致。
7. **增量接缝与服务端守卫。** `catalog.IndexVersionTx`（调用者事务内）与
   `catalog.IndexVersionNow` 从权威行重读后 upsert；已撤销/归档/不存在的版本删除旧投影行
   而不是写新行。`catalog.EnqueueIndexVersionTx` 以
   `<version_id>:<manifest_sha256>` 为 dedup key 写 `catalog_index` job（owner 为应用 owner），
   `catalog_index` handler 在写事务内复读权威行、校验 job 的 expected hash，非法能力卡返回
   business `catalog_source_invalid`。T068/T070 可在发布/撤销事务内调用该接缝；本票不依赖
   该钩子已存在：缺钩子只会让 search 继续回退，绝不会泄漏。
8. **目录索引与知识索引完全分离。** 不同表（`application_catalog_entries` /
   `catalog_index_state` vs `knowledge_chunks` / `knowledge_index_versions`）、不同 ACL 谓词
   （应用 owner/grant vs 文档 owner）、不同重建命令与失败台账；重建只读
   `application_versions`/`applications`/`outbox`，从不写知识表。测试用真实知识行计数与身份
   签名在重建前后比对。
9. **明确的限制。** `q` 最长 200 字符；`%`/`_` 被转义为字面量；中文按子串 `ILIKE` 匹配，
   `tsvector('simple')` 只承担词项部分，不宣称中文分词/相关度质量（`ROUTING:56` 的数据验证
   仍属检索后续票）。回退路径按已发布版本行搜索，不引入第二份正文。

## 后果

- T079 可直接复用 `catalog.SearchAuthorized` 返回的候选（`version_id` + 完整能力卡）作为
  路由候选快照；提交/派发仍必须走 `catalog.ResolveRunnableVersion(Tx)`（T074 已接），
  不得把搜索响应当作执行许可。
- T082 可复用同一目录读取展示“处理者版本”，但交接仍由 T082 的确认记录承担。
- T068/T070 若要即时增量更新，调用 `catalog.IndexVersionTx`（同事务）或
  `catalog.EnqueueIndexVersionTx`；无论是否接钩子，search 的权威复核都保证不越权。
- 运维面固定为 `orchara-admin catalog rebuild|index|status`；重建失败/中断的证据在
  `platform.catalog_index_state`，不在日志猜测。
- 拥有后续所有权的偏差：省略的 embedding 列与 ANN 质量归 T064/T067；目录搜索的中文相关性
  实验未运行，不得在报告/页面声称已验证。
