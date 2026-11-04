"""T057 slice 1: the deliverable bring-up entry starts a clean dev environment.

The seam under test is the deployer-facing entry ``deploy/dev/bringup.py``: a
fresh PostgreSQL database is created, the real ``cmd/admin migrate`` and
``cmd/admin bootstrap`` run, the compiled ``cmd/api`` serves the real Web pages,
and the compiled ``cmd/maintenance`` process is running the real background
pools. Nothing here is a test-only shortcut.
"""

from __future__ import annotations

from support import stack as st


def test_the_bring_up_creates_a_fresh_migrated_database(dev_stack) -> None:
    chain = st.migration_chain(dev_stack.dbname)
    assert chain, "the bring-up applied no migrations"
    # Continuity is the stable invariant: 0001..NNNN with no gap. The tail is
    # deliberately not pinned; later tickets append migrations.
    assert list(chain) == [f"{number:04d}" for number in range(1, len(chain) + 1)]

    users = int(
        st.sql(
            dev_stack.dbname,
            "SELECT count(*) FROM platform.users WHERE login = "
            + st.quote_literal(dev_stack.admin_login),
        )
    )
    assert users == 1, "the bring-up did not leave exactly one bootstrap administrator"


def test_the_api_is_ready_and_serves_the_real_pages(dev_stack) -> None:
    ready = dev_stack.http.get("/health/ready")
    assert ready.status == 200, ready.text
    assert ready.json["status"] == "ready", ready.text
    assert ready.json["checks"]["database"]["status"] == "ok", ready.text

    for path in ("/", "/login", "/apps", "/attachments", "/runs", "/models"):
        page = dev_stack.http.get(path)
        assert page.status == 200, (path, page.status, page.text[:200])
        assert "text/html" in page.header("Content-Type"), path


def test_the_profile_is_explicit_and_stub_results_are_marked(dev_stack) -> None:
    status = dev_stack.http.get("/api/status")
    assert status.status == 200, status.text
    payload = status.json
    assert payload["profile"] == "dev-stub", payload
    assert payload["stub_results"] is True, payload
    assert payload["status"] == "ready", payload


def test_the_status_page_renders_the_profile_and_the_stub_flag(dev_stack) -> None:
    """AC2: a stub result must be identifiable from the product UI, not only a report."""
    page = dev_stack.http.get("/")
    assert page.status == 200, page.text
    body = page.text
    for marker in ('id="environment-profile"', 'id="environment-stub"', "status.stub_results"):
        assert marker in body, f"the status page does not visibly mark the environment: {marker!r}"


def test_the_maintenance_process_is_running_the_real_pools(dev_stack) -> None:
    assert dev_stack.maintenance is not None
    assert dev_stack.maintenance.poll() is None, "the maintenance process exited"
    log = dev_stack.maintenance_log()
    assert "run: pools=" in log, log[-2000:]
    assert "system_io[" in log and "work[" in log, log[-2000:]
