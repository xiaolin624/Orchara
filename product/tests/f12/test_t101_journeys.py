"""T101 (GitHub #102): five key journeys replayed against the real stack.

The suite covers the ticket's five journeys: request -> Run -> result, write
approval, personal memory, knowledge ingestion, and named service accounts.
Every step uses the product's real entry point: HTTP ``/api/v1/**`` (plus the
documented internal mTLS protocol for worker/supervisor evidence), a real
PostgreSQL 16 database, the real object store, the real ``cmd/supervisor exec
--once`` and ``cmd/maintenance run --once`` binaries. The database and the object
directory are read only to assert the **independent truth** of a side effect; no
journey writes business rows to set up its own state.

Known product gaps are executed and recorded as ``blocked`` instead of skipped;
see ``reports/f12/T101-journeys.md``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:  # pragma: no cover - pytest already prepends it
    sys.path.insert(0, str(HERE))

import t101_support as t101  # noqa: E402
from journeys import (  # noqa: E402
    j101_approvals,
    j101_knowledge,
    j101_memory,
    j101_requests,
    j101_service_accounts,
    j101_service_key_write,
)


@pytest.fixture
def t101_stack(tmp_path: Path, go_binaries: dict[str, Path]):
    """One migrated + bootstrapped database behind one real API process, per test.

    Each journey owns a fresh database: the golden draft, the golden tool version
    and the quota counter are database-global fixtures the product suites assume
    are created once, so sharing a database between journeys would make one
    journey's seed collide with the next one's.
    """
    stack, server, dbname = t101.build_stack(tmp_path, go_binaries, label="journey")
    try:
        yield stack
    finally:
        t101.drop_stack(server, dbname)


def test_service_account_journey(t101_stack) -> None:
    with t101.journey(
        t101.J5_SERVICE_ACCOUNT,
        "服务账号：createServiceAccount -> createApiKey -> scope/ACL/负责人三重门控",
    ) as record:
        result = j101_service_accounts.run(t101_stack, record)
        record.note("result", result)
        record.mark_passed()


def test_write_approval_journey(t101_stack) -> None:
    with t101.journey(
        t101.J2_APPROVAL,
        "写操作审批：proposal 参数绑定 -> 人类批准 -> 恰好一次真实外部写；拒绝/过期/取消/丢回执边界",
    ) as record:
        result = j101_approvals.run(t101_stack, record)
        record.note("result", result)
        record.mark_passed()


def test_knowledge_ingestion_journey(t101_stack) -> None:
    with t101.journey(
        t101.J4_KNOWLEDGE,
        "知识摄取：上传 -> finalize -> ingest -> ready -> 检索 -> 引用可解析；删除后 404 不回退缓存",
    ) as record:
        result = j101_knowledge.run(t101_stack, record)
        record.note("result", result)
        record.mark_passed()


def test_personal_memory_journey(t101_stack) -> None:
    with t101.journey(
        t101.J3_MEMORY,
        "个人记忆：候选 propose -> 人类 confirm -> 运行时按预算召回 -> 修订/删除后立即停止召回",
    ) as record:
        result = j101_memory.run(t101_stack, record)
        record.note("result", result)
        record.mark_passed()


def test_request_journeys(t101_stack) -> None:
    """J1b first (the documented F08<->F04B gap), then J1 on the same published app."""
    with t101.journey(
        t101.J1_NATURAL,
        "请求(自然语言) -> Run：产品缺口实测（聊天正文当 Run 输入 -> node_input_invalid）",
    ) as record:
        result = j101_requests.run_natural_language(t101_stack, record)
        record.note("result", result)
        record.mark_passed()
    with t101.journey(
        t101.J1_REQUEST,
        "请求 -> Run -> 结果：显式固定版本入口 + 真实 supervisor + 独立 Decimal 真值",
    ) as record:
        result = j101_requests.run_request_to_result(t101_stack, record)
        record.note("result", result)
        record.mark_passed()


def test_service_key_write_journeys(t101_stack) -> None:
    """J6 closes the AC2 gap; J6b records the graph-dispatch gap as ``blocked``.

    J6 is the ticket's missing journey: a service key (API-key scope), holding a
    resource grant, submits a *genuinely published* version; the Run belongs to
    the service principal; the write-risk operation's approval resolves to the
    service account's responsible human; that human approves; the loopback
    endpoint receives exactly one physical write. J6b runs the same published
    version through the real supervisor graph executor and records that the graph
    itself emits no connector operation (a product gap, not a test workaround).
    """
    with t101.journey(
        t101.J6_SERVICE_WRITE,
        "服务 key + grant 提交已发布版本 -> 负责人批准写操作 -> 恰好一次真实外部写；缺 scope/ACL/人类批准均被拒",
    ) as record:
        result = j101_service_key_write.run(t101_stack, record)
        record.note("result", result["result"])
        record.mark_passed()
    with t101.journey(
        t101.J6B_GRAPH_DISPATCH,
        "图分发缺口：同一已发布版本真实执行成功但图本身不产生任何 connector operation（blocked）",
    ) as record:
        result = j101_service_key_write.run_graph_dispatch_gap(
            t101_stack, record, result
        )
        record.note("result", result)
