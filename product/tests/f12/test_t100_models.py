"""T100 (#101) journey 2 acceptance: model registration, validation and report reads.

Real HTTP + real ``cmd/maintenance`` + real PostgreSQL/object store; the only
stub is the documented ``dev-stub`` model adapter (reported as stub, never as a
real vendor result). See ``journeys/j100_models.py``.
"""

from __future__ import annotations

import sys
from pathlib import Path

_JOURNEYS = Path(__file__).resolve().parent / "journeys"
if str(_JOURNEYS) not in sys.path:
    sys.path.insert(0, str(_JOURNEYS))

from j100_models import run_models_journey  # noqa: E402
from t100_support import environment_facts, journey_log, t100_stack  # noqa: E402,F401


def test_journey_2_model_registration_validation_and_report(t100_stack) -> None:
    log = journey_log()
    log.set_environment(environment_facts(t100_stack))
    with log.journey(
        "T100-J2",
        title="模型登记 → validate → 评测读取 → 通过（dev-stub）",
        entry_commands=[
            "cmd/maintenance run --once --kinds model_validate (real compiled binary)",
            "cmd/maintenance run --kinds artifact_finalize (real compiled binary beside it, "
            "required because the validation handler waits for its own sealed report)",
        ],
        entry_http=[
            "POST /api/v1/models {provider:stub, model_id:stub-model-1, endpoint_ref, "
            "secret_ref, config, data_destination} (admin + CSRF)",
            "POST /api/v1/models/{id}/validate (admin + CSRF + Idempotency-Key)",
            "GET /api/v1/models/{id}",
            "GET /api/v1/evaluations/{evaluation_id}",
            "GET /api/v1/evaluation-reports/{report_id}",
            "GET /api/v1/models (non-admin visibility)",
            "POST /api/v1/models + /validate as a creator (403 boundary)",
            "POST /api/v1/models/{id}/validate without Idempotency-Key (400 boundary)",
            "POST /api/v1/models + /validate for an incompatible stub model and for an "
            "undeployed real provider (failure boundaries)",
        ],
        truth_sources=[
            "platform.model_profile_versions (validation_state, evaluation_report_id, manifest)",
            "platform.evaluation_jobs (state, report_id, background_job_id, error_code)",
            "platform.evaluation_reports (status, manifest_sha256, dataset_sha256)",
            "object directory: sealed compatibility document + sealed dataset bytes (sha256)",
            "platform.platform_model_calls (the maintenance handler's model-call ledger)",
            "platform.background_jobs (kind/state/attempt_count of model_validate)",
            "cmd/maintenance stdout JSON reports + exit code",
        ],
    ) as journey:
        run_models_journey(t100_stack, journey)
