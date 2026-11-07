"""T100 (#101) journey 1 acceptance: account bring-up to login.

Real entry points only: the compiled ``cmd/admin`` CLIs and real HTTP to the
compiled ``cmd/api``; independent truth from PostgreSQL. See
``journeys/j100_account.py`` for the step-by-step implementation and
``reports/f12/T100-journeys.md`` for the recorded run.
"""

from __future__ import annotations

import sys
from pathlib import Path

_JOURNEYS = Path(__file__).resolve().parent / "journeys"
if str(_JOURNEYS) not in sys.path:
    sys.path.insert(0, str(_JOURNEYS))

from j100_account import run_account_journey  # noqa: E402
from t100_support import environment_facts, journey_log, t100_stack  # noqa: E402,F401


def test_journey_1_account_bringup_invite_activate_login(t100_stack) -> None:
    log = journey_log()
    log.set_environment(environment_facts(t100_stack))
    with log.journey(
        "T100-J1",
        title="账号启动：migrate → bootstrap → 邀请 → 激活 → 登录",
        entry_commands=[
            "cmd/admin migrate (real compiled binary, stack fixture)",
            "cmd/admin bootstrap --login admin --display-name 'Platform Admin' (real binary, "
            "password on stdin)",
            "cmd/admin bootstrap --login admin2 (boundary replay of the same real binary)",
        ],
        entry_http=[
            "POST /api/v1/auth/login {login: admin}",
            "GET /api/v1/auth/me (admin)",
            "POST /api/v1/admin/users {login, display_name, roles:[creator]} (admin + CSRF)",
            "POST /api/v1/auth/activate {activation_token, password}",
            "POST /api/v1/auth/login {login: t100.creator-*}",
            "GET /api/v1/auth/me (creator)",
            "POST /api/v1/admin/users (anonymous; creator; duplicate login; missing CSRF; bad role)",
            "POST /api/v1/auth/activate (unknown token; weak password; expired token; reused token)",
            "POST /api/v1/auth/login (wrong password)",
        ],
        truth_sources=[
            "platform.schema_migrations (continuous chain, real cmd/admin migrate)",
            "platform.principals / platform.users / platform.role_bindings / platform.audit_events "
            "(bootstrap-once + invitation facts)",
            "platform.activation_tokens (token_hash = sha256(raw), consumed_at, expires_at)",
            "platform.sessions (token_hash/csrf_hash = sha256(raw), revoked_at)",
            "cmd/admin bootstrap stdout/stderr + exit code",
        ],
    ) as journey:
        run_account_journey(t100_stack, journey)
        journey.mark_passed()
