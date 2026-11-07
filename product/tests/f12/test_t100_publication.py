"""T100 (#101) journey 3 acceptance: draft -> evaluation -> review -> immutable version.

The real compiler, the real five-category runner (maintenance pools + per-case
``cmd/supervisor exec --once`` in WSL2/runsc) and the real publication decision
are all exercised; independent truth comes from PostgreSQL, the sealed object
bytes and the contract reads. The known F08<->F04B chat-input gap is recorded as
a blocked capability, not as a passed journey. See
``journeys/j100_publication.py``.
"""

from __future__ import annotations

import sys
from pathlib import Path

_JOURNEYS = Path(__file__).resolve().parent / "journeys"
if str(_JOURNEYS) not in sys.path:
    sys.path.insert(0, str(_JOURNEYS))

from j100_publication import run_publication_journey  # noqa: E402
from t100_support import environment_facts, journey_log, t100_stack  # noqa: E402,F401


def test_journey_3_draft_evaluation_publication(t100_stack, tmp_path: Path) -> None:
    log = journey_log()
    log.set_environment(environment_facts(t100_stack))
    with log.journey(
        "T100-J3",
        title="草稿 → validate → 真实评测 → 审核 → 不可变发布版本",
        entry_commands=[
            "cmd/maintenance run --once (real full-kind pools: application_evaluate + "
            "artifact_finalize; started by T068's runner helper)",
            "cmd/supervisor exec --once (real compiled supervisor per evaluation case; "
            "WSL2/runsc sandbox)",
        ],
        entry_http=[
            "POST /api/v1/admin/users + POST /api/v1/auth/activate + /auth/login (creator)",
            "POST /api/v1/applications",
            "GET/PUT /api/v1/applications/{id}/draft (If-Match revision)",
            "POST /api/v1/applications/{id}/validate",
            "POST /api/v1/applications/{id}/evaluations (Idempotency-Key; 5-case dataset artifact)",
            "GET /api/v1/evaluations/{id}",
            "GET /api/v1/evaluation-reports/{report_id}",
            "POST /api/v1/applications/{id}/publication-preview (non-contract route; pre-submit read)",
            "POST /api/v1/applications/{id}/submit",
            "POST /api/v1/reviews/{id}/decision (non-admin; mismatched hash; admin approve; replay)",
            "GET /api/v1/versions/{version_id}, GET /api/v1/applications/{id}",
            "GET/PUT /api/v1/applications/{id}/draft + POST /validate (immutability re-check)",
            "POST /api/v1/conversations + POST /api/v1/requests (chat-text gap probe, explicit "
            "application_version_id) + GET /api/v1/runs/{id} + node-results",
        ],
        truth_sources=[
            "platform.manifest_artifacts + object directory sealed manifest bytes (sha256)",
            "platform.evaluation_jobs / platform.evaluation_reports / sealed report document",
            "platform.publication_reviews (frozen snapshot, report binding, decision)",
            "platform.application_versions + sealed manifest artifact bytes (immutability)",
            "platform.applications.active_version_id, platform.outbox, platform.audit_events",
            "platform.runs (chat-input gap: state/error_code/output_artifact_id) and artifacts",
            "cmd/maintenance + cmd/supervisor stdout JSON reports and exit codes",
        ],
    ) as journey:
        run_publication_journey(t100_stack, journey, tmp_path)
