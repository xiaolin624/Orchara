"""T100 (#101) journey 4 acceptance: worker terminal dual evidence in both orders.

The real internal mTLS protocol (worker/supervisor/maintenance certificates) on
Runs created by the real debug-submit HTTP entry, plus the product's own
``cmd/supervisor exec --once`` variant when WSL2/runsc is present. Independent
truth from PostgreSQL and the object store. See ``journeys/j100_worker_exit.py``.
"""

from __future__ import annotations

import sys
from pathlib import Path

_JOURNEYS = Path(__file__).resolve().parent / "journeys"
if str(_JOURNEYS) not in sys.path:
    sys.path.insert(0, str(_JOURNEYS))

from j100_worker_exit import run_worker_exit_journey  # noqa: E402
from t100_support import environment_facts, journey_log, t100_stack  # noqa: E402,F401


def test_journey_4_worker_result_and_exit_dual_evidence(t100_stack) -> None:
    log = journey_log()
    log.set_environment(environment_facts(t100_stack))
    with log.journey(
        "T100-J4",
        title="worker 终结双证据：submitWorkerResult + confirmAttemptExit（两种顺序）",
        entry_commands=[
            "cmd/maintenance run --once --kinds artifact_finalize (real binary: upload "
            "completion -> sealed output artifact)",
            "cmd/supervisor exec --once (real product executor variant; WSL2/runsc)",
        ],
        entry_http=[
            "POST /api/v1/applications + PUT /draft (real creator/application)",
            "POST /api/v1/applications/{id}/debug-runs (real debug Run)",
            "POST /internal/v1/jobs/claim (real supervisor mTLS client certificate)",
            "POST /internal/v1/runs/{id}/artifact-uploads + signed PUT + complete + "
            "POST /internal/v1/runs/{id}/artifacts (real output registration)",
            "POST /internal/v1/runs/{run_id}/result (worker certificate; result-first and "
            "exit-first)",
            "POST /internal/v1/attempts/{token}/exited (supervisor certificate; both orders)",
            "GET /api/v1/runs/{id}, GET /api/v1/runs/{id}/node-results",
        ],
        truth_sources=[
            "platform.runs (state, phase, revision, finished_at, output_artifact_id, error_code)",
            "platform.jobs (state, current_attempt_token) and platform.run_attempts "
            "(reported_result, exited_at, revoked_at)",
            "platform.quota_counters + platform.quota_allocations (one released slot)",
            "platform.run_artifacts (exactly input+output links)",
            "object directory: sealed output artifact bytes + sha256",
            "cmd/supervisor exec stdout JSON report; node results via the owner read",
            "independent Decimal truth computed by the test from the input CSV bytes",
        ],
    ) as journey:
        run_worker_exit_journey(t100_stack, journey)
        journey.mark_passed()
