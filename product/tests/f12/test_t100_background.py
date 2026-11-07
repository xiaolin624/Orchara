"""T100 (#101) journey 5 acceptance: background job claim -> heartbeat -> result.

The real ``tool_run`` handler under the real ``cmd/maintenance`` process, plus the
precise internal protocol (``claimBackgroundJob`` -> ``heartbeatBackgroundJob``
-> ``completeBackgroundJob``) on a second real job, with retry/recovery and
duplicate-delivery boundaries. Independent truth from PostgreSQL and the sealed
object bytes; Decimal facts are recomputed by this test. See
``journeys/j100_background.py``.
"""

from __future__ import annotations

import sys
from pathlib import Path

_JOURNEYS = Path(__file__).resolve().parent / "journeys"
if str(_JOURNEYS) not in sys.path:
    sys.path.insert(0, str(_JOURNEYS))

from j100_background import run_background_journey  # noqa: E402
from t100_support import environment_facts, journey_log, t100_stack  # noqa: E402,F401


def test_journey_5_background_job_claim_heartbeat_result(t100_stack) -> None:
    log = journey_log()
    log.set_environment(environment_facts(t100_stack))
    with log.journey(
        "T100-J5",
        title="后台任务：领取 → 心跳 → 结果（真实 tool_run 处理器 + 内部协议）",
        entry_commands=[
            "cmd/maintenance run --once --kinds tool_run,artifact_finalize (real compiled binary, "
            "two real pools; the preset tool runs as a real Python subprocess)",
        ],
        entry_http=[
            "POST /api/v1/tool-runs (owner + CSRF + Idempotency-Key)",
            "GET /api/v1/tool-runs/{job_id}",
            "GET /api/v1/artifacts/{id}/content (sealed tool summary)",
            "GET /api/v1/background-jobs/{job_id} (contract read of the same job)",
            "POST /internal/v1/background/jobs/claim (maintenance mTLS certificate; "
            "worker/supervisor certificates are refused)",
            "POST /internal/v1/background/jobs/{job_id}/heartbeat",
            "POST /internal/v1/background/jobs/{job_id}/result (retryable failure -> retry; "
            "terminal business failure; duplicate event; different event)",
        ],
        truth_sources=[
            "platform.background_jobs (kind, state, attempt_count, lease_token, worker_id, "
            "result_event_id, failure_class, error_code, finished_at, available_at)",
            "platform.background_job_artifacts + platform.artifacts (tool_summary/tool_artifact)",
            "object directory: sealed summary bytes (sha256) and their Decimal facts",
            "platform.idempotency_keys (the /tool-runs receipt row)",
            "independent Decimal truth computed by the test from the input CSV bytes",
            "cmd/maintenance stdout JSON job reports and exit code",
        ],
    ) as journey:
        run_background_journey(t100_stack, journey)
