"""F04A/T049 acceptance: another user runs the sales template and downloads a
verified report.

The product path is exercised end to end on the real stack:

* a dev-stub model profile registered and validated through the real T039 HTTP
  endpoints and the real ``model_validate`` + ``artifact_finalize`` processes;
* the real ``orchara-admin seed-sales-template`` command, which resolves the
  actual tool/model UUIDs and reads the real ``sales_stats.py`` bytes;
* a second creator calling the non-contract instantiate endpoint over HTTP;
* the real debug submission, the compiled supervisor, the real WSL2/runsc
  sandbox for the python and agent nodes, the real capability ledger, the
  owner-only node-results read and artifact downloads.

The truth checker is test-side and independent: it recomputes the public sample
with :class:`decimal.Decimal` from literal rows and fails if the tool's summary,
the exported CSV or the report invent/drop an amount. The model provider is the
documented deterministic stub (labelled stub in the ledger); every tool byte,
snapshot, run and artifact is real.
"""

from __future__ import annotations

import hashlib
import json
import secrets

import pytest

from support import stack as st
from support import t027, t033, t039, t041, t049
from support.fixtures import f06_stack  # noqa: F401 - pytest fixture


@pytest.fixture(scope="module", autouse=True)
def require_sandbox() -> None:
    if not t033.sandbox_available():
        pytest.skip(
            "WSL2/runsc is unavailable: `wsl -d Ubuntu-24.04 -u root -e bash -lc "
            "'runsc --version'` failed; build the rootfs with "
            "deploy/sandbox/build_python_rootfs.sh"
        )


def creator(stack, admin, *, login: str | None = None, roles: tuple[str, ...] = ("creator",)):
    return t027.invite_and_activate(
        stack, admin, login or ("t049-" + secrets.token_hex(5)), roles=roles
    )


def seed_once(stack, admin):
    model_id = t049.create_passed_model(stack, admin)
    summary = t049.seed_template(stack, model_id)
    return model_id, summary


def template_row(dbname: str) -> dict:
    rows = st.sql_rows(
        dbname,
        "SELECT id, name, application_id::text AS application_id, "
        "definition::text AS definition, capability_card::text AS capability_card "
        "FROM platform.application_templates WHERE id = 'sales'",
    )
    assert len(rows) == 1, rows
    row = rows[0]
    row["definition"] = json.loads(row["definition"])
    row["capability_card"] = json.loads(row["capability_card"])
    return row


def draft_row(dbname: str, application_id: str) -> dict:
    rows = st.sql_rows(
        dbname,
        "SELECT revision, execution_revision, definition::text AS definition, "
        "capability_card::text AS capability_card "
        "FROM platform.application_drafts "
        f"WHERE application_id = {st.quote_literal(application_id)}::uuid",
    )
    assert len(rows) == 1, rows
    row = rows[0]
    row["definition"] = json.loads(row["definition"])
    row["capability_card"] = json.loads(row["capability_card"])
    return row


def test_seed_registry_and_instantiate_boundaries(f06_stack) -> None:
    admin = t027.admin_session(f06_stack)
    admin_id = admin.principal_id

    # A pending model can never be seeded: the command refuses instead of
    # writing a fake validation_state.
    pending = t039.register_or_fail(admin)
    refused = st.run_admin(
        f06_stack.binaries["admin"],
        ["seed-sales-template", "--owner-login", t049.ADMIN_LOGIN, "--model-version-id", pending["id"]],
        dsn=f06_stack.dsn,
    )
    assert refused.returncode != 0, (refused.stdout, refused.stderr)
    assert "not passed" in refused.stderr, refused.stderr
    assert st.sql(f06_stack.dbname, "SELECT count(*) FROM platform.application_templates") == "0"

    model_id, first = seed_once(f06_stack, admin)
    second = t049.seed_template(f06_stack, model_id)
    assert second == first, (first, second)
    assert st.sql(f06_stack.dbname, "SELECT count(*) FROM platform.application_templates") == "1"
    assert (
        st.sql(
            f06_stack.dbname,
            f"SELECT count(*) FROM platform.tool_versions "
            f"WHERE id = {st.quote_literal(t049.PRESET_TOOL_ID)}::uuid",
        )
        == "1"
    )

    # The tool row is real: ready, read-risk, approved, and its image digest is
    # the SHA-256 of the actual script the sandbox executes.
    tool = st.sql_rows(
        f06_stack.dbname,
        "SELECT state, risk_class, image_digest, approved_by::text AS approved_by, "
        "approved_at::text AS approved_at FROM platform.tool_versions "
        f"WHERE id = {st.quote_literal(t049.PRESET_TOOL_ID)}::uuid",
    )[0]
    assert tool["state"] == "ready", tool
    assert tool["risk_class"] == "read", tool
    assert tool["approved_by"] == admin_id, tool
    assert tool["approved_at"], tool
    script = (
        t033.PRODUCT_ROOT / "worker" / "platform_worker" / "nodes" / "sales_stats.py"
    ).read_bytes()
    assert tool["image_digest"] == "sha256:" + hashlib.sha256(script).hexdigest(), tool

    # The stored definition pins real ids; no fixture placeholder survives.
    row = template_row(f06_stack.dbname)
    assert row["application_id"] == first["application_id"], row
    definition = row["definition"]
    nodes = {node["id"]: node for node in definition["nodes"]}
    assert set(nodes) == {"start", "stats", "explain", "end"}, nodes
    assert nodes["stats"]["config"]["tool_version"] == t049.PRESET_TOOL_ID, nodes["stats"]
    assert nodes["explain"]["config"]["model_profile_version"] == model_id, nodes["explain"]
    assert nodes["stats"]["inputs"]["file"]["ref"] == "node:start#/artifact"
    stored = json.dumps(definition)
    assert "22222222-2222-4222-8222-222222222222" not in stored
    assert "11111111-1111-4111-8111-111111111111" in stored
    source_draft = draft_row(f06_stack.dbname, row["application_id"])
    assert source_draft["revision"] == 1 and source_draft["execution_revision"] == 1, source_draft
    assert source_draft["definition"] == definition, source_draft
    assert row["capability_card"] == source_draft["capability_card"], row
    assert "销售" in row["capability_card"]["purpose"], row["capability_card"]

    # The second creator copies the template into a private application. The
    # copied draft is revision 1 and carries the exact stored definition/card.
    second_creator = creator(f06_stack, admin)
    instance = t049.instantiate_or_fail(second_creator)
    assert instance["id"] != row["application_id"]
    copy_draft = t027.get_draft(second_creator, instance["id"])
    assert copy_draft.status == 200, copy_draft.text
    assert copy_draft.json["definition"] == definition, copy_draft.json
    assert copy_draft.json["capability_card"] == row["capability_card"], copy_draft.json
    copied = draft_row(f06_stack.dbname, instance["id"])
    assert copied["revision"] == 1, copied

    # Unknown templates are 404, a non-creator is refused, and no session is 401.
    unknown = t049.instantiate(second_creator, "does-not-exist")
    assert unknown.status == 404, unknown.text
    assert unknown.json["error"]["code"] == "template_not_found", unknown.json

    no_role = creator(f06_stack, admin, roles=("user",))
    refused_create = t049.instantiate(no_role)
    assert refused_create.status == 403, refused_create.text
    assert refused_create.json["error"]["code"] == "creator_role_required", refused_create.json

    anonymous = st.HttpClient(f06_stack.server.base_url).post(
        "/api/v1/templates/sales/applications", body={}
    )
    assert anonymous.status == 401, anonymous.text

    # Instantiating never publishes anything: no version and no review exists.
    assert (
        st.sql(
            f06_stack.dbname,
            "SELECT count(*) FROM platform.application_versions "
            f"WHERE application_id = {st.quote_literal(instance['id'])}::uuid",
        )
        == "0"
    )
    assert (
        st.sql(
            f06_stack.dbname,
            "SELECT count(*) FROM platform.publication_reviews "
            f"WHERE application_id = {st.quote_literal(instance['id'])}::uuid",
        )
        == "0"
    )


def csv_row(*, duplicate: bool = False) -> bytes:
    rows = [
        ("order_id", "date", "region", "amount"),
        ("A1", "2026-01-01", "东区", "120.50"),
        ("A2", "2026-01-02", "东区", "79.50"),
        ("A3", "2026-01-03", "西区", "50.00"),
    ]
    if duplicate:
        rows = rows[:2] + [("A1", "2026-01-04", "东区", "10.00")] + rows[3:]
    return ("\n".join(",".join(row) for row in rows) + "\n").encode("utf-8")


def test_csv_and_xlsx_runs_produce_the_verified_report(f06_stack) -> None:
    admin = t027.admin_session(f06_stack)
    model_id, _summary = seed_once(f06_stack, admin)
    owner = creator(f06_stack, admin)
    outsider = creator(f06_stack, admin)
    instance = t049.instantiate_or_fail(owner)
    application_id = instance["id"]

    reports: dict[str, dict] = {}
    for kind, content, media_type, file_name in (
        ("csv", t033.SALES_CSV, "text/csv", "sales.csv"),
        ("xlsx", t049.xlsx_bytes(t049.SAMPLE_ROWS), t049.XLSX_MEDIA_TYPE, "sales.xlsx"),
    ):
        artifact_id = t033.upload_artifact(
            f06_stack, owner, content, media_type=media_type
        )
        run = t049.submit_sales_run(
            owner, application_id, artifact_id, media_type=media_type, file_name=file_name
        )
        completed, report = t049.run_once(f06_stack)
        assert completed.returncode == 0, (completed.stderr, report)
        assert report["status"] == "executed", report
        assert report["outcome"] == "succeeded", report
        assert report["run_state"] == "succeeded", report

        body = t049.wait_node_results(owner, run["id"], expected=6)
        items = body["items"]
        assert len(items) == 6, items
        grouped = t049.grouped(items)
        assert set(grouped) == {"stats", "explain"}, grouped
        assert sorted(row["kind"] for row in grouped["stats"]) == ["data", "file", "file", "log"]
        assert sorted(row["kind"] for row in grouped["explain"]) == ["data", "file"], grouped["explain"]

        stats_data = t049.rows_of(grouped, "stats", "data")
        assert len(stats_data) == 1, stats_data
        summary = t049.content_json(owner, stats_data[0]["artifact_id"])
        assert summary == {
            "total": "250.00",
            "regions": {"东区": "200.00", "西区": "50.00"},
            "order_count": 3,
        }, summary

        stats_files = {
            row["label"]: row for row in t049.rows_of(grouped, "stats", "file")
        }
        assert set(stats_files) == {"summary.json", "summary.csv"}, stats_files
        summary_json = t049.content_json(owner, stats_files["summary.json"]["artifact_id"])
        summary_csv = t049.content_bytes(owner, stats_files["summary.csv"]["artifact_id"])
        assert summary_json == summary, summary_json
        assert summary_csv == t033.EXPECTED_SUMMARY_CSV, summary_csv

        explain_data = t049.rows_of(grouped, "explain", "data")
        assert len(explain_data) == 1, explain_data
        explain = t049.content_json(owner, explain_data[0]["artifact_id"])
        assert set(explain) == {"report", "totals"}, explain
        assert explain["totals"] == summary, explain

        output_id = t033.output_artifact_id(f06_stack.dbname, run["id"])
        assert output_id, run
        final = t049.content_json(owner, output_id)
        assert set(final) == {"summary", "report", "totals"}, final
        assert final["summary"] == summary, final
        assert final["report"] == explain["report"], final
        assert final["totals"] == summary, final

        # Report/body separation: the report body is its own artifact and never
        # the summary file; the end output is a third, distinct artifact.
        artifact_ids = {
            "summary.json": stats_files["summary.json"]["artifact_id"],
            "summary.csv": stats_files["summary.csv"]["artifact_id"],
            "stats.data": stats_data[0]["artifact_id"],
            "explain.data": explain_data[0]["artifact_id"],
            "end.output": output_id,
        }
        assert len(set(artifact_ids.values())) == 5, artifact_ids
        report_download = t049.download(owner, explain_data[0]["artifact_id"])
        assert report_download.status in (200, 302), report_download.status
        summary_download = t049.download(owner, stats_files["summary.json"]["artifact_id"])
        assert summary_download.status in (200, 302), summary_download.status

        # The independent Decimal checker: recomputes the public sample and
        # verifies summary.json, summary.csv, the report amounts and totals.
        t049.check_run_documents(summary, summary_csv, explain["report"], explain["totals"])

        # Node timeline: four nodes, the T043 linear shape.
        events = t042_node_events(f06_stack.dbname, run["id"])
        observed: dict[str, list[str]] = {}
        for event in events:
            payload = event["payload"]
            observed.setdefault(payload["node_id"], []).append(payload["state"])
        assert observed == {
            "start": ["pending", "running", "succeeded"],
            "stats": ["pending", "running", "succeeded"],
            "explain": ["pending", "running", "succeeded"],
            "end": ["pending", "running", "succeeded"],
        }, observed

        # Ledger: exactly one real model turn, under the passed profile id.
        operations = t041.operation_rows(f06_stack.dbname, run["id"])
        assert len(operations) == 1, operations
        operation = operations[0]
        assert operation["logical_key"] == "agent:explain:model:1", operation
        assert operation["capability_kind"] == "model", operation
        assert operation["capability_version"] == model_id, operation
        assert operation["state"] == "succeeded", operation
        attempts = t041.attempt_rows(f06_stack.dbname, operation["id"])
        assert len(attempts) == 1 and attempts[0]["state"] == "succeeded", attempts

        reports[kind] = {
            "run_id": run["id"],
            "summary": summary,
            "report": explain["report"],
            "summary_csv": summary_csv,
        }

    # CSV and XLSX are equivalent inputs: same verified totals and same report.
    assert reports["csv"]["summary"] == reports["xlsx"]["summary"]
    assert reports["csv"]["report"] == reports["xlsx"]["report"]
    assert reports["csv"]["summary_csv"] == reports["xlsx"]["summary_csv"]

    # The report never invents amounts: a perturbed copy is rejected by the
    # independent checker (proves the checker is not vacuous).
    perturbed_summary = dict(reports["csv"]["summary"])
    perturbed_summary["total"] = "251.00"
    with pytest.raises(t049.CheckerError):
        t049.check_summary(perturbed_summary)
    with pytest.raises(t049.CheckerError):
        t049.check_report(
            reports["csv"]["report"].replace("250.00", "999.00"),
            reports["csv"]["summary"],
        )

    # A third user cannot read the run, its node results or its artifacts.
    run_id = reports["csv"]["run_id"]
    assert t027.get_run(outsider, run_id).status == 404
    assert t042_node_results(outsider, run_id).status == 404
    summary_artifact = [
        row
        for row in t049.wait_node_results(owner, run_id, expected=6)["items"]
        if row["label"] == "summary.json"
    ][0]["artifact_id"]
    assert t033.artifact_content(outsider, summary_artifact).status in (403, 404)
    assert t033.artifact_download(outsider, summary_artifact).status in (403, 404)


def test_invalid_sales_inputs_are_rejected_by_the_preset_tool(f06_stack) -> None:
    admin = t027.admin_session(f06_stack)
    _model_id, _summary = seed_once(f06_stack, admin)
    owner = creator(f06_stack, admin)
    instance = t049.instantiate_or_fail(owner)

    cases = (
        ("duplicate", csv_row(duplicate=True), "duplicate_order_id"),
        (
            "negative",
            b"order_id,date,region,amount\nA1,2026-01-01,\xe4\xb8\x9c\xe5\x8c\xba,-5.00\n",
            "negative_amount",
        ),
        ("empty", b"order_id,date,region,amount\n", "empty_input"),
    )
    for name, content, expected_code in cases:
        artifact_id = t033.upload_artifact(f06_stack, owner, content)
        run = t049.submit_sales_run(
            owner, instance["id"], artifact_id, media_type="text/csv", file_name="sales.csv"
        )
        completed, report = t049.run_once(f06_stack)
        assert completed.returncode == 0, (name, completed.stderr, report)
        assert report["status"] == "executed", (name, report)
        assert report["outcome"] == "failed", (name, report)
        assert report.get("error_code", "") == expected_code, (name, report)
        assert report["run_state"] == "failed", (name, report)

        # A rejected input writes no summary artifacts: there is no report to
        # download and no verified statistic to cite.
        files = st.sql_rows(
            f06_stack.dbname,
            "SELECT o.label FROM platform.run_node_outputs AS o "
            f"WHERE o.run_id = {st.quote_literal(run['id'])}::uuid",
        )
        labels = {row["label"] for row in files}
        assert "summary.json" not in labels and "summary.csv" not in labels, (name, labels)
        assert t033.output_artifact_id(f06_stack.dbname, run["id"]) is None, (name, run)


# ---------------------------------------------------------------------------
# small local wrappers so the test reads in domain terms
# ---------------------------------------------------------------------------
def t042_node_results(session, run_id: str):
    return session.client.get(f"/api/v1/runs/{run_id}/node-results")


def t042_node_events(dbname: str, run_id: str) -> list[dict]:
    rows = st.sql_rows(
        dbname,
        "SELECT seq, type, payload::text AS payload FROM platform.run_events "
        f"WHERE run_id = {st.quote_literal(run_id)}::uuid AND type = 'node.status' "
        "ORDER BY seq",
    )
    for row in rows:
        row["payload"] = json.loads(row["payload"])
    return rows
