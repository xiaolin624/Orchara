# ADR 0001: 产品代码位于规格仓库内的 `product/`

- 状态：已接受
- 日期：2026-11-01
- 相关：D00 / T001，`BOOTSTRAP.md` §1

## 背景

规格 `BOOTSTRAP.md` 建议在规格同级新建独立产品仓库（默认 `agent-platform`），理由是把只读规格与
可写产品分开。但在本项目的实际协作方式下：

- GitHub Issue（`xiaolin624/Orchara`）跟踪的正是本仓库的票；
- 规格 `docs/agents/issue-tracker.md` 要求"issue 关闭须具备相应完成证据"，证据与票同源最可靠；
- 用户明确选择在本仓库内实现。

## 决定

产品代码放在本仓库 `product/`，规格保持在仓库根的只读位置，并复制到 `product/docs/spec/` 作为
实现依据与比对基线。

## 后果

- 好处：票、证据、实现同仓，闭合 issue 时证据路径可直接引用；减少两仓同步。
- 代价：规格与产品同仓，存在"顺手改规格"的风险。因此：
  - `product/tools/sync_spec.py` 生成 SHA-256 清单，`--check` 非零即失败（F00 漂移门禁）；
  - 修改规格必须同时改仓库根的生成源（`tools/build_*.py`）并重建，再重新同步；
  - 规格校验器会扫描全仓 Markdown 相对链接，所以**用户态工具链不得放在仓库内**
    （放仓库内会让 `validate_contracts.py` 报错），见 D00 报告第 4 节。
