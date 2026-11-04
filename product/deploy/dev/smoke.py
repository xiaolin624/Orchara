#!/usr/bin/env python3
"""F15A/T057 dev smoke: sales, failure, cancel and a stub model profile.

Everything here goes through the **public HTTP API** of a running development
deployment (``deploy/dev/bringup.py up``): real login, real CSRF, real signed
upload links, real ``POST /api/v1/tool-runs`` jobs executed by a real
``cmd/maintenance`` process, real artifact downloads and real debug Run/cancel
transitions. The summary truth is computed here with :class:`decimal.Decimal`
from the CSV bytes, independently of the tool under test; the sealed model
compatibility report is read back through the API and its ``stub: true`` marker
is what the page renders.

Three honest boundaries are recorded in the evidence instead of being hidden:

* the present-page logic is executed by Node against the real API data (a DOM
  browser is not run);
* ``cmd/supervisor exec`` (gVisor debug execution) is *not* exercised: WSL has
  ``runsc`` but WSL is not an accepted isolation host, so the formal
  arbitrary-script sandbox is ``blocked``, not proven;
* Redis/S3/real-model-credentials are absent, so those dimensions are blocked.

The module is import-clean for the acceptance tests: :func:`run` returns a fully
factual evidence dictionary and raises ``AssertionError`` the moment the product
disagrees with the expected behaviour.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import decimal
import hashlib
import io
import json
import os
import platform
import secrets
import shutil
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

PRODUCT_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = PRODUCT_ROOT.parent
TOOLS_ROOT = PRODUCT_ROOT / "tools"
DEPLOY_DEV = Path(__file__).resolve().parent
for entry in (str(TOOLS_ROOT), str(DEPLOY_DEV)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import dev_http  # noqa: E402
from dev_http import HttpClient, expect_error  # noqa: E402

try:  # the Go locator is a repo tool; the smoke works without it (versions record "unavailable")
    import toolchain  # noqa: E402
except ImportError:  # pragma: no cover - deploy tree copied without tools/
    toolchain = None

PROFILE = "dev-stub"
TOOL = "sales_stats"
WEB_DIR = PRODUCT_ROOT / "apps" / "web" / "src"
EXAMPLES = REPO_ROOT / "examples" / "sales"
SCHEMA = "orchara.f15a.dev-smoke/1"
ADMIN_PASSWORD_ENV = "ORCHARA_DEV_ADMIN_PASSWORD"
COMPILER_VERSION = "orchara-compiler/1.0.0"

TOOL_SUMMARY = "tool_summary"
TOOL_ARTIFACT = "tool_artifact"
DIAGNOSTICS = "diagnostics"
TERMINAL_JOB_STATES = {"succeeded", "failed", "cancelled", "unknown"}
TERMINAL_RUN_STATES = {"succeeded", "failed", "cancelled", "needs_reconciliation"}

# The documented public sales sample (WORKFLOW-SPEC §6). The smoke still derives
# its own truth from the CSV bytes; this literal is an independent known-good.
EXPECTED_SALES = {
    "total": "250.00",
    "regions": {"东区": "200.00", "西区": "50.00"},
    "order_count": 3,
}

MINIMAL_POLICY = {
    "max_active_seconds": 900,
    "max_child_depth": 3,
    "max_child_calls": 12,
    "budget_limit": "1.00",
    "currency": "USD",
}


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def sha256_hex(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def wait_until(predicate, *, timeout: float, what: str, interval: float = 0.1):  # noqa: ANN001
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            value = predicate()
        except AssertionError as exc:  # a predicate may report its own refusal
            last = exc
            value = None
        else:
            if value is not None:
                return value
        time.sleep(interval)
    raise AssertionError(f"timed out after {timeout}s waiting for {what}; last: {last}")


def new_idempotency_key(prefix: str) -> str:
    return f"{prefix}-{secrets.token_urlsafe(18)}"


# ---------------------------------------------------------------------------
# real session
# ---------------------------------------------------------------------------
class Session:
    def __init__(self, client: HttpClient, csrf: str, principal_id: str, login: str) -> None:
        self.client = client
        self.csrf = csrf
        self.principal_id = principal_id
        self.login = login

    def get(self, path: str, **kwargs):
        return self.client.get(path, **kwargs)

    def post(self, path: str, body=None, *, csrf: bool = True, headers: dict | None = None):  # noqa: ANN001
        return self.client.post(path, body=body, csrf=self.csrf if csrf else None, headers=headers)

    def request(self, method: str, path: str, **kwargs):
        if kwargs.get("csrf") is None and method not in ("GET", "HEAD"):
            kwargs["csrf"] = self.csrf
        return self.client.request(method, path, **kwargs)


def sign_in(base_url: str, login: str, password: str) -> Session:
    client = HttpClient(base_url)
    response = client.post("/api/v1/auth/login", body={"login": login, "password": password})
    assert response.status == 200, f"login as {login!r} failed: {response.text[:500]}"
    payload = response.json
    assert payload.get("csrf_token"), payload
    assert client.cookie(), "login did not set the session cookie"
    return Session(client, payload["csrf_token"], payload["principal_id"], login)


def invite_and_activate(
    base_url: str, admin: Session, login: str, password: str, roles: tuple[str, ...] = ("creator",)
) -> Session:
    invitation = admin.post(
        "/api/v1/admin/users",
        {"login": login, "display_name": login.replace(".", " ").title(), "roles": list(roles)},
    )
    assert invitation.status == 201, invitation.text[:500]
    activation = HttpClient(base_url).post(
        "/api/v1/auth/activate",
        body={"activation_token": invitation.json["activation_token"], "password": password},
    )
    assert activation.status == 200, activation.text[:500]
    return sign_in(base_url, login, password)


# ---------------------------------------------------------------------------
# independent sales truth (Decimal, never the tool's own output)
# ---------------------------------------------------------------------------
def independent_sales_truth(csv_bytes: bytes) -> dict:
    text = csv_bytes.decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(text)))
    assert rows, "the sales smoke input is empty"
    required = {"order_id", "date", "region", "amount"}
    assert required <= set(rows[0]), f"the smoke input lacks a required column: {sorted(required - set(rows[0]))}"
    total = decimal.Decimal("0")
    regions: dict[str, decimal.Decimal] = {}
    seen_orders: set[str] = set()
    for row in rows:
        order = (row.get("order_id") or "").strip()
        assert order and order not in seen_orders, f"the smoke input has a duplicate/blank order id: {order!r}"
        seen_orders.add(order)
        assert (row.get("region") or "").strip(), "the smoke input has a blank region"
        amount = decimal.Decimal((row.get("amount") or "").strip())
        total += amount
        region = row["region"].strip()
        regions[region] = regions.get(region, decimal.Decimal("0")) + amount
    return {
        "total": f"{total:.2f}",
        "regions": {region: f"{value:.2f}" for region, value in regions.items()},
        "order_count": len(rows),
    }


# ---------------------------------------------------------------------------
# artifacts and tool runs
# ---------------------------------------------------------------------------
def upload_artifact(
    session: Session, content: bytes, *, media_type: str, purpose: str = "task_input", timeout: float = 90
) -> dict:
    created = session.post(
        "/api/v1/artifacts/uploads",
        {"media_type": media_type, "size_bytes": len(content), "purpose": purpose},
    )
    assert created.status == 201, created.text[:500]
    ticket = created.json
    assert set(ticket) == {"artifact_id", "upload_url", "expires_at"}, ticket
    put = HttpClient(session.client.base_url).request(
        "PUT", ticket["upload_url"], raw_body=content, headers={"Content-Type": media_type}, absolute=True
    )
    assert put.status in (200, 204), f"PUT to the signed staging link returned {put.status}: {put.text[:300]}"
    digest = sha256_hex(content)
    completed = session.post(f"/api/v1/artifacts/{ticket['artifact_id']}/complete", {"sha256": digest})
    assert completed.status == 202, completed.text[:500]

    def ready():
        response = session.get(f"/api/v1/artifacts/{ticket['artifact_id']}")
        if response.status != 200:
            return None
        if response.json["state"] == "ready":
            return response.json
        return None

    view = wait_until(ready, timeout=timeout, what=f"artifact {ticket['artifact_id']} to become ready")
    assert view["state"] == "ready", view
    return {
        "artifact_id": ticket["artifact_id"],
        "sha256": digest,
        "size_bytes": len(content),
        "media_type": media_type,
        "state": view["state"],
    }


def create_tool_run(session: Session, artifact_id: str, *, tool: str = TOOL):
    return session.post(
        "/api/v1/tool-runs",
        {"tool": tool, "artifact_id": artifact_id},
        headers={"Idempotency-Key": new_idempotency_key("f15a-tool")},
    )


def wait_tool_run(session: Session, job_id: str, *, timeout: float = 120) -> dict:
    def terminal():
        response = session.get(f"/api/v1/tool-runs/{job_id}")
        if response.status != 200:
            return None
        return response.json if response.json["state"] in TERMINAL_JOB_STATES else None

    return wait_until(terminal, timeout=timeout, what=f"tool run {job_id} to reach a terminal state")


def tool_artifact(view: dict, purpose: str) -> dict | None:
    matches = [artifact for artifact in view.get("artifacts", []) if artifact.get("purpose") == purpose]
    assert len(matches) <= 1, matches
    return matches[0] if matches else None


def download_artifact(session: Session, artifact_id: str) -> bytes:
    response = session.get(f"/api/v1/artifacts/{artifact_id}/content")
    assert response.status == 200, f"artifact {artifact_id} content failed: HTTP {response.status}: {response.text[:300]}"
    return response.raw


def wait_for_artifact_ready(session: Session, artifact_id: str, *, timeout: float = 30) -> dict:
    """Wait for a produced artifact to be sealed.

    The tool_run failure path publishes its diagnostics *best effort* and reports
    the failure immediately, so the artifact can still be ``scanning`` when the
    job becomes terminal. The smoke waits for the real finalize state instead of
    pretending the bytes were readable at the instant the job failed; the page
    fix recorded in the report makes the same wait user-facing.
    """

    def ready():
        response = session.get(f"/api/v1/artifacts/{artifact_id}")
        if response.status != 200:
            return None
        view = response.json
        if view["state"] == "ready":
            return view
        if view["state"] in ("quarantined", "deleted"):
            raise AssertionError(f"artifact {artifact_id} is {view['state']}: {view}")
        return None

    return wait_until(ready, timeout=timeout, what=f"artifact {artifact_id} to become ready")



def parse_summary_csv(payload: bytes) -> dict[str, str]:
    rows = list(csv.DictReader(io.StringIO(payload.decode("utf-8"))))
    assert rows, "summary.csv has no rows"
    for row in rows:
        assert set(row) == {"region", "total"}, row
    return {row["region"]: row["total"] for row in rows}


# ---------------------------------------------------------------------------
# model registration / stub validation
# ---------------------------------------------------------------------------
def model_registration_body(suffix: str) -> dict:
    return {
        "name": "f15a stub candidate " + suffix,
        "provider": "stub",
        "model_id": "stub-model-1",
        "endpoint_ref": "stub-endpoint",
        "secret_ref": "stub-credential",
        "config": {
            "kind": "generation",
            "protocol": "chat_compatible",
            "context_window": 8192,
            "max_output_tokens": 1024,
            "timeout_ms": 30000,
            "price": {"currency": "USD", "input_per_million": "0", "output_per_million": "0"},
        },
        "data_destination": "local-stub",
    }


# ---------------------------------------------------------------------------
# internal mTLS access (the claim boundary after a cancel)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class InternalAccess:
    url: str
    ca: str
    client_cert: str
    client_key: str
    role: str = "supervisor"


def internal_claim_no_work(internal: InternalAccess, *, execution_unit_id: str) -> int:
    """Claim one run through the real internal mTLS protocol and return the HTTP status.

    204 is the assertion that the cancelled Run's job is neither claimable nor
    requeued; 200 would mean a worker actually leased it.
    """
    import http.client

    parsed = urllib.parse.urlsplit(internal.url)
    assert parsed.scheme == "https" and parsed.hostname, internal.url
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_verify_locations(cafile=internal.ca)
    context.load_cert_chain(certfile=internal.client_cert, keyfile=internal.client_key)
    context.check_hostname = True
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    body = json.dumps(
        {
            "worker_id": "f15a-smoke",
            "supported_compilers": [COMPILER_VERSION],
            "execution_unit_id": execution_unit_id,
        }
    ).encode("utf-8")
    connection = http.client.HTTPSConnection(
        parsed.hostname, parsed.port or 443, context=context, timeout=30
    )
    try:
        connection.request(
            "POST",
            "/internal/v1/jobs/claim",
            body=body,
            headers={
                "Content-Type": "application/json",
                "X-Internal-Role": internal.role,
            },
        )
        response = connection.getresponse()
        raw = response.read()
        return response.status, raw.decode("utf-8", errors="replace")
    finally:
        connection.close()


# ---------------------------------------------------------------------------
# page data and the Node execution of the real page assets
# ---------------------------------------------------------------------------
PAGE_ASSETS = ("assets/app.js", "assets/attachments.js", "assets/runs.js", "assets/models.js")

NODE_HARNESS = r"""
'use strict';
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const { webcrypto } = require('crypto');

const DATA = __DATA__;
const context = {
  fetch: async () => ({ ok: false, status: 0, text: async () => '{}', headers: { get: () => null } }),
  console,
  setTimeout,
  clearTimeout,
  crypto: webcrypto,
  window: { location: { assign: () => {} } },
};
vm.createContext(context);
for (const asset of ['assets/app.js', 'assets/attachments.js', 'assets/runs.js', 'assets/models.js']) {
  vm.runInContext(fs.readFileSync(DATA.webDir + '/' + asset, 'utf8'), context, { filename: asset });
}

// Cross-realm values come back from the vm context, so compare their JSON form
// (the same technique the T030 page test uses) instead of prototype identity.
assert.strictEqual(JSON.stringify(context.summaryRows(DATA.summary)), JSON.stringify(DATA.expectedRows));
assert.strictEqual(JSON.stringify(context.summaryTotals(DATA.summary)), JSON.stringify(DATA.expectedTotals));
assert.strictEqual(JSON.stringify(context.toolDownloadLinks({ artifacts: DATA.artifacts })), JSON.stringify(DATA.expectedLinks));

const diagnostics = context.diagnosticsFields(DATA.diagnostics);
assert.strictEqual(diagnostics.code, DATA.expectedDiagnostics.code);
assert.strictEqual(diagnostics.retryable, false);
assert.ok(diagnostics.message.length > 0 && diagnostics.fix.length > 0);

// F15A/T057 defect fix: a failure's diagnostics artifact may still be scanning.
assert.strictEqual(context.shouldPollToolArtifact({ state: 'scanning' }, 0, 3), true);
assert.strictEqual(context.shouldPollToolArtifact({ state: 'ready' }, 0, 3), false);
assert.strictEqual(context.shouldPollToolArtifact({ state: 'quarantined' }, 0, 3), false);
assert.strictEqual(context.shouldPollToolArtifact({ state: 'scanning' }, 3, 3), false);

const cancel = context.describeRunCancel(DATA.cancelRun, { cancelRequestedAt: DATA.cancelHeader });
assert.strictEqual(cancel.visible, true);
assert.strictEqual(cancel.terminal, true);
assert.ok(cancel.line.indexOf('已停止') >= 0, cancel.line);
assert.strictEqual(context.mergeRunSnapshot(DATA.cancelRun, { state: 'succeeded' }).state, 'cancelled');

const report = context.describeReport(DATA.sealedReport);
assert.strictEqual(report.stub, true);
assert.strictEqual(report.provider, 'stub');
assert.strictEqual(report.status, 'passed');
assert.strictEqual(report.compatible, true);

process.stdout.write('f15a page logic ok\n');
"""


def run_page_logic(*, web_dir: Path, summary: dict, truth: dict, artifacts: list[dict],
                   diagnostics: dict, cancel_run: dict, cancel_header: str,
                   sealed_report: dict) -> dict:
    node = shutil.which("node")
    if node is None:
        return {"status": "not_run", "reason": "node is not on PATH; the page logic could not be executed"}
    expected_rows = [
        {"region": region, "total": truth["regions"][region]} for region in summary.get("regions", {})
    ]
    expected_links = []
    csv_artifact = tool_artifact({"artifacts": artifacts}, TOOL_ARTIFACT)
    if csv_artifact is not None:
        expected_links.append(
            {"label": "下载 summary.csv", "url": f"/api/v1/artifacts/{csv_artifact['id']}/content"}
        )
    summary_artifact = tool_artifact({"artifacts": artifacts}, TOOL_SUMMARY)
    if summary_artifact is not None:
        expected_links.append(
            {"label": "下载 summary.json", "url": f"/api/v1/artifacts/{summary_artifact['id']}/content"}
        )
    data = {
        "webDir": str(web_dir),
        "summary": summary,
        "expectedRows": expected_rows,
        "expectedTotals": {"total": truth["total"], "orderCount": str(truth["order_count"])},
        "artifacts": artifacts,
        "expectedLinks": expected_links,
        "diagnostics": {
            "error_code": diagnostics["error_code"],
            "message": diagnostics["message"],
            "fix": diagnostics["fix"],
            "retryable": diagnostics.get("retryable", True),
            "location": diagnostics.get("location"),
        },
        "expectedDiagnostics": {
            "code": diagnostics["error_code"],
            "retryable": diagnostics.get("retryable", True),
        },
        "cancelRun": cancel_run,
        "cancelHeader": cancel_header,
        "sealedReport": sealed_report,
    }
    with tempfile.TemporaryDirectory(prefix="f15a-node-") as directory:
        harness = Path(directory) / "f15a-pages.js"
        harness.write_text(
            NODE_HARNESS.replace("__DATA__", json.dumps(data, ensure_ascii=False)), encoding="utf-8"
        )
        completed = subprocess.run(
            [node, str(harness)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            stdin=subprocess.DEVNULL,
        )
    assert completed.returncode == 0, (
        "the real page assets disagreed with the smoke data:\n"
        f"--- stdout ---\n{completed.stdout}\n--- stderr ---\n{completed.stderr}"
    )
    assert "f15a page logic ok" in completed.stdout, completed.stdout
    return {
        "status": "passed",
        "node": node,
        "assets": list(PAGE_ASSETS),
        "checks": [
            "summaryRows matches the independent Decimal truth",
            "summaryTotals matches the independent Decimal truth",
            "toolDownloadLinks points at the real artifact content paths",
            "diagnosticsFields renders the real rejection as safe text",
            "shouldPollToolArtifact waits for the real scanning/ready/quarantined states",
            "describeRunCancel says 已停止 for the real cancelled Run",
            "mergeRunSnapshot never flips a terminal cancelled Run back",
            "describeReport shows provider=stub and stub=true",
        ],
        "stdout": completed.stdout.strip(),
    }


# ---------------------------------------------------------------------------
# the smoke
# ---------------------------------------------------------------------------
def run(
    *,
    base_url: str,
    admin_login: str,
    admin_password: str,
    web_dir: Path = WEB_DIR,
    examples_dir: Path = EXAMPLES,
    internal: InternalAccess | None = None,
    environment: dict | None = None,
    command: str = "",
    require_node: bool = False,
) -> dict:
    base_url = base_url.rstrip("/")
    started_at = _now()
    evidence: dict = {
        "schema": SCHEMA,
        "ticket": "T057",
        "issue": 58,
        "feature": "F15A",
        "status": "passed",
        "command": command,
        "started_at": started_at,
        "steps": [],
        "artifacts": {},
        "defects": [],
        "blocked": [
            {
                "id": "container_runtime",
                "owner": "F15A/F15B deployment",
                "reason": "no Docker/Compose on the reference host; this bring-up is a "
                          "process + PostgreSQL deployment, not a container deployment",
            },
            {
                "id": "linux_isolation",
                "owner": "F13B",
                "reason": "WSL Ubuntu-24.04 has runsc, but WSL is not an accepted isolation host "
                          "(ADR 0002 / BOOTSTRAP.md:71); the formal arbitrary-script sandbox was not "
                          "exercised and the preset tool ran through its Python subprocess path",
            },
            {
                "id": "model_credentials",
                "owner": "deployment",
                "reason": "no approved model provider endpoint/credential exists; only the stub "
                          "provider under the dev-stub profile is usable",
            },
            {
                "id": "s3_object_store",
                "owner": "F15A/#54",
                "reason": "no S3/MinIO endpoint; the local filesystem store is the real object store "
                          "here and is not an S3-compatibility claim",
            },
            {
                "id": "redis_wakeup",
                "owner": "F00B/F15B",
                "reason": "no Redis on the host; the platform polls PostgreSQL and no Redis adapter was run",
            },
        ],
        "not_run": [
            {
                "id": "supervisor_sandbox_exec",
                "reason": "cmd/supervisor exec --once needs an accepted Linux isolation host (blocked: "
                          "linux_isolation); this smoke needs no debug tool execution",
            },
            {
                "id": "cmd_worker",
                "reason": "cmd/worker is a liveness-only skeleton that claims no work in this build; the "
                          "real async loop started by the bring-up is cmd/maintenance run",
            },
            {
                "id": "evaluation_ui",
                "reason": "no evaluation page exists in this build",
            },
            {
                "id": "browser_dom",
                "reason": "the real page assets were executed under Node with real API data; a "
                          "DOM-driving browser was not run",
            },
        ],
    }

    def step(name: str, status: str = "passed", **facts) -> dict:
        record = {"name": name, "status": status, "at": _now(), **facts}
        evidence["steps"].append(record)
        return record

    secrets_seen: list[str] = [admin_password]

    # -- 1. environment / profile ------------------------------------------
    anonymous = HttpClient(base_url)
    status_response = anonymous.get("/api/status")
    assert status_response.status == 200, status_response.text[:500]
    status_payload = status_response.json
    assert status_payload.get("profile") == PROFILE, status_payload
    assert status_payload.get("stub_results") is True, status_payload
    assert status_payload.get("status") == "ready", status_payload
    ready = anonymous.get("/health/ready")
    assert ready.status == 200 and ready.json["status"] == "ready", ready.text[:300]

    pages = {}
    for path in ("/", "/login", "/apps", "/attachments", "/runs", "/models"):
        page = anonymous.get(path)
        assert page.status == 200 and "text/html" in (page.header("Content-Type") or ""), (path, page.status)
        pages[path] = {"status": page.status, "sha256": sha256_hex(page.raw), "bytes": len(page.raw)}
    # AC2: the human status page renders the profile and the stub flag from /api/status.
    index_body = anonymous.get("/").text
    for marker in ('id="environment-profile"', 'id="environment-stub"', "stub_results"):
        assert marker in index_body, f"the status page does not visibly mark the profile/stub: {marker!r}"
    step(
        "environment",
        profile=status_payload["profile"],
        stub_results=status_payload["stub_results"],
        service_version=status_payload.get("version"),
        checks=status_payload.get("checks"),
        pages=pages,
        stub_visible_in=[
            "GET /api/status (profile=dev-stub, stub_results=true)",
            "GET / (status page renders the profile and stub flag from /api/status)",
            "the sealed model compatibility report (provider=stub, stub=true) rendered by /models",
        ],
    )

    # -- 2. identity --------------------------------------------------------
    admin = sign_in(base_url, admin_login, admin_password)
    creator_password = "t057-creator-" + secrets.token_urlsafe(18)
    foreign_password = "t057-foreign-" + secrets.token_urlsafe(18)
    secrets_seen.extend([creator_password, foreign_password])
    creator = invite_and_activate(
        base_url, admin, "f15a-creator-" + secrets.token_hex(4), creator_password, roles=("creator",)
    )
    foreign = invite_and_activate(
        base_url, admin, "f15a-foreign-" + secrets.token_hex(4), foreign_password, roles=("user",)
    )
    step(
        "identity",
        admin_principal=admin.principal_id,
        creator_login=creator.login,
        creator_principal=creator.principal_id,
        foreign_login=foreign.login,
    )

    # -- 3. linear sales run ------------------------------------------------
    csv_bytes = (examples_dir / "input.csv").read_bytes()
    truth = independent_sales_truth(csv_bytes)
    assert truth == EXPECTED_SALES, f"the independent Decimal truth disagrees with the documented sample: {truth}"
    uploaded = upload_artifact(creator, csv_bytes, media_type="text/csv")
    created = create_tool_run(creator, uploaded["artifact_id"])
    assert created.status == 202, created.text[:500]
    assert set(created.json) == {"id", "state", "tool", "artifact_id"}, created.json
    job_id = created.json["id"]
    view = wait_tool_run(creator, job_id)
    assert view["state"] == "succeeded", f"the sales tool run did not succeed: {view}"
    assert view["error_code"] is None, view
    summary_artifact = tool_artifact(view, TOOL_SUMMARY)
    csv_artifact = tool_artifact(view, TOOL_ARTIFACT)
    assert summary_artifact is not None and csv_artifact is not None, view
    download_artifact(creator, summary_artifact["id"])  # ensure readable before recording
    summary_bytes = download_artifact(creator, summary_artifact["id"])
    summary_csv = download_artifact(creator, csv_artifact["id"])
    summary = json.loads(summary_bytes.decode("utf-8"))
    assert summary["total"] == truth["total"], (summary, truth)
    assert summary["regions"] == truth["regions"], (summary, truth)
    assert summary["order_count"] == truth["order_count"], (summary, truth)
    assert parse_summary_csv(summary_csv) == truth["regions"], (parse_summary_csv(summary_csv), truth)
    assert summary_artifact["media_type"] == "application/json", summary_artifact
    assert csv_artifact["media_type"] == "text/csv", csv_artifact
    evidence["artifacts"]["sales"] = {
        "input_sha256": uploaded["sha256"],
        "tool_run_id": job_id,
        "summary_artifact_id": summary_artifact["id"],
        "summary_sha256": sha256_hex(summary_bytes),
        "summary_bytes": len(summary_bytes),
        "csv_artifact_id": csv_artifact["id"],
        "csv_sha256": sha256_hex(summary_csv),
        "csv_bytes": len(summary_csv),
    }
    step(
        "sales",
        tool_run_id=job_id,
        state=view["state"],
        error_code=view["error_code"],
        artifact_sha256={
            "summary.json": sha256_hex(summary_bytes),
            "summary.csv": sha256_hex(summary_csv),
        },
        independent_truth=truth,
        summary=summary,
        summary_csv=parse_summary_csv(summary_csv),
    )

    # -- 4. failure examples -------------------------------------------------
    failure_cases = [
        ("missing_column", b"order_id,region,amount\no-1,east,10.00\n"),
        (
            "duplicate_order_id",
            b"order_id,date,region,amount\no-1,2026-01-02,east,10.00\no-1,2026-01-03,west,20.00\n",
        ),
    ]
    failures = []
    first_diagnostics = None
    for expected_code, content in failure_cases:
        uploaded_bad = upload_artifact(creator, content, media_type="text/csv")
        created_bad = create_tool_run(creator, uploaded_bad["artifact_id"])
        assert created_bad.status == 202, created_bad.text[:500]
        bad_view = wait_tool_run(creator, created_bad.json["id"])
        assert bad_view["state"] == "failed", f"the {expected_code} input was not rejected: {bad_view}"
        assert bad_view["error_code"] == expected_code, bad_view
        diagnostics_artifact = tool_artifact(bad_view, DIAGNOSTICS)
        assert diagnostics_artifact is not None, bad_view
        assert tool_artifact(bad_view, TOOL_SUMMARY) is None, bad_view
        # See wait_for_artifact_ready: the failure is reported before the best-effort
        # diagnostics upload has necessarily been finalized.
        wait_started = time.time()
        wait_for_artifact_ready(creator, diagnostics_artifact["id"])
        ready_wait_ms = int((time.time() - wait_started) * 1000)
        diagnostics_bytes = download_artifact(creator, diagnostics_artifact["id"])
        document = json.loads(diagnostics_bytes.decode("utf-8"))
        assert document["status"] == "rejected", document
        assert document["error_code"] == expected_code, document
        assert document["retryable"] is False, document
        assert document["message"] and document["fix"], document
        assert set(document["input"]) == {"artifact_id", "media_type", "size_bytes", "sha256"}, document
        if first_diagnostics is None:
            first_diagnostics = document
        failures.append(
            {
                "case": expected_code,
                "tool_run_id": created_bad.json["id"],
                "state": bad_view["state"],
                "error_code": bad_view["error_code"],
                "diagnostics_ready_wait_ms": ready_wait_ms,
                "diagnostics_sha256": sha256_hex(diagnostics_bytes),
                "diagnostics": document,
            }
        )
    evidence["artifacts"]["failure"] = failures
    evidence["defects"].append(
        {
            "id": "failure_diagnostics_artifact_race",
            "status": "fixed",
            "where": "apps/web/src/assets/attachments.js",
            "detail": "The tool_run failure path publishes diagnostics best effort and reports the "
                      "failure immediately, so the diagnostics artifact can still be scanning when the "
                      "run is already terminal. The page fetched the artifact content at that instant "
                      "and showed a 409 instead of the explanation. The page now polls the real "
                      "artifact state (bounded) before rendering diagnostics; the smoke performs the "
                      "same wait and records the measured delay.",
        }
    )
    step(
        "failure",
        cases=[{key: case[key] for key in ("case", "tool_run_id", "state", "error_code")} for case in failures],
        diagnostics_ready_wait_ms={case["case"]: case["diagnostics_ready_wait_ms"] for case in failures},
        explainable=[{"error_code": case["error_code"], "message": case["diagnostics"]["message"]} for case in failures],
    )

    # -- 5. cancel example ---------------------------------------------------
    application = creator.post("/api/v1/applications", {"name": "F15A cancel smoke " + secrets.token_hex(4)})
    assert application.status == 201, application.text[:500]
    application_id = application.json["id"]
    draft = creator.get(f"/api/v1/applications/{application_id}/draft")
    assert draft.status == 200 and draft.header("ETag"), draft.text[:300]
    definition = {
        "dsl_version": "1.0",
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object"},
        "nodes": [
            {"id": "start", "type": "start", "inputs": {}, "output_schema": {"type": "object"}, "config": {}},
            {"id": "end", "type": "end", "inputs": {}, "output_schema": {"type": "object"}, "config": {}},
        ],
        "edges": [{"source": "start", "target": "end"}],
        "policy": dict(MINIMAL_POLICY),
    }
    capability_card = {
        "purpose": "summarise sales",
        "includes": ["aggregation"],
        "excludes": ["external writes"],
        "required_inputs": ["sales csv"],
        "expected_outputs": ["report"],
        "examples": ["monthly report", "weekly digest"],
    }
    saved = creator.request(
        "PUT",
        f"/api/v1/applications/{application_id}/draft",
        body={
            "definition": definition,
            "layout": {"viewport": {"x": 0, "y": 0, "zoom": 1}},
            "capability_card": capability_card,
        },
        headers={"If-Match": draft.header("ETag")},
    )
    assert saved.status == 200, saved.text[:500]
    revision = int(creator.get(f"/api/v1/applications/{application_id}/draft").header("ETag").strip('"'))
    submitted = creator.post(
        f"/api/v1/applications/{application_id}/debug-runs",
        {"draft_revision": revision, "input": {"text": "hello"}, "artifact_ids": []},
        headers={"Idempotency-Key": new_idempotency_key("f15a-debug")},
    )
    assert submitted.status == 202, submitted.text[:500]
    run = submitted.json
    assert run["state"] == "queued" and run["kind"] == "debug", run
    run_path = f"/api/v1/runs/{run['id']}"
    expect_error(anonymous.get(run_path), 401, "unauthenticated", "an anonymous Run read")
    expect_error(
        foreign.post(f"{run_path}/cancel"), 404, "resource_not_found", "a foreign owner's cancel"
    )
    cancelled = creator.post(f"{run_path}/cancel", body={})
    assert cancelled.status == 202, cancelled.text[:500]
    assert cancelled.json["state"] == "cancelled", cancelled.json
    cancel_header = cancelled.header("X-Run-Cancel-Requested-At")
    assert cancel_header, "the cancel response carries no X-Run-Cancel-Requested-At"
    detail = creator.get(run_path)
    assert detail.status == 200, detail.text[:300]
    assert detail.json["state"] == "cancelled", detail.json
    assert detail.json["phase"] == "idle", detail.json
    assert detail.json["output_artifact_id"] is None, detail.json
    assert detail.json["error_code"] is None, detail.json
    claim_evidence = None
    if internal is not None:
        claim_status, claim_body = internal_claim_no_work(internal, execution_unit_id="f15a-smoke-after-cancel")
        assert claim_status == 204, (
            "a cancelled Run's job must not be claimable (expected 204, got "
            f"{claim_status}): {claim_body[:300]}"
        )
        claim_evidence = {"status": claim_status, "meaning": "no claimable run after the cancel"}
    evidence["artifacts"]["cancel"] = {
        "application_id": application_id,
        "run_id": run["id"],
        "state": detail.json["state"],
        "cancel_requested_at": cancel_header,
        "claim_after_cancel": claim_evidence,
    }
    step(
        "cancel",
        application_id=application_id,
        run_id=run["id"],
        state=detail.json["state"],
        phase=detail.json["phase"],
        cancel_requested_at=cancel_header,
        anonymous_read="401 unauthenticated",
        foreign_cancel="404 resource_not_found",
        claim_after_cancel=claim_evidence,
    )

    # -- 6. explicit model profile (stub) ------------------------------------
    registration = model_registration_body(secrets.token_hex(4))
    registered = admin.post("/api/v1/models", registration)
    assert registered.status == 201, registered.text[:500]
    assert set(registered.json) == {"id", "state"} and registered.json["state"] == "pending", registered.json
    model_id = registered.json["id"]
    expect_error(
        creator.post("/api/v1/models", model_registration_body(secrets.token_hex(4))),
        403,
        "forbidden",
        "a non-admin model registration",
    )
    accepted = admin.post(
        f"/api/v1/models/{model_id}/validate",
        headers={"Idempotency-Key": new_idempotency_key("f15a-model")},
    )
    assert accepted.status == 202, accepted.text[:500]
    evaluation = accepted.json
    assert set(evaluation) == {"id", "kind", "state", "background_job_id", "report_id", "error_code"}, evaluation
    assert evaluation["kind"] == "model", evaluation

    def evaluation_terminal():
        response = admin.get(f"/api/v1/evaluations/{evaluation['id']}")
        if response.status != 200:
            return None
        return response.json if response.json["state"] in {"succeeded", "failed", "cancelled"} else None

    finished = wait_until(
        evaluation_terminal, timeout=120, what=f"model evaluation {evaluation['id']} to finish"
    )
    assert finished["state"] == "succeeded", finished
    assert finished["report_id"], finished
    model_view = admin.get(f"/api/v1/models/{model_id}")
    assert model_view.status == 200, model_view.text[:300]
    assert model_view.json["validation_state"] == "passed", model_view.json
    assert model_view.json["evaluation_report_id"] == finished["report_id"], model_view.json
    report = admin.get(f"/api/v1/evaluation-reports/{finished['report_id']}")
    assert report.status == 200, report.text[:300]
    assert set(report.json) == {"id", "status", "manifest_sha256", "result_artifact_id"}, report.json
    assert report.json["status"] == "passed", report.json
    sealed_bytes = download_artifact(admin, report.json["result_artifact_id"])
    sealed = json.loads(sealed_bytes.decode("utf-8"))
    assert sealed["provider"] == "stub" and sealed["stub"] is True, sealed
    assert sealed["compatible"] is True, sealed
    assert model_view.json["data_destination"] == "local-stub", model_view.json
    evidence["artifacts"]["model_profile"] = {
        "model_profile_version_id": model_id,
        "evaluation_job_id": evaluation["id"],
        "report_id": finished["report_id"],
        "report_artifact_id": report.json["result_artifact_id"],
        "sealed_report_sha256": sha256_hex(sealed_bytes),
        "sealed_report_bytes": len(sealed_bytes),
        "provider": sealed["provider"],
        "stub": sealed["stub"],
    }
    step(
        "model_profile",
        model_profile_version_id=model_id,
        evaluation_job_id=evaluation["id"],
        state=finished["state"],
        report_id=finished["report_id"],
        report_status=report.json["status"],
        provider=sealed["provider"],
        stub=sealed["stub"],
        data_destination=model_view.json["data_destination"],
        non_admin_registration="403 forbidden",
    )

    # -- 7. real page logic under Node --------------------------------------
    page_logic = run_page_logic(
        web_dir=web_dir,
        summary=summary,
        truth=truth,
        artifacts=view["artifacts"],
        diagnostics=first_diagnostics,
        cancel_run=detail.json,
        cancel_header=cancel_header,
        sealed_report=sealed,
    )
    if page_logic["status"] == "passed":
        step("page_logic", **page_logic)
    else:
        step("page_logic", status="not_run", reason=page_logic.get("reason", "node unavailable"))
        if require_node:
            raise AssertionError(page_logic.get("reason", "node is unavailable"))
        evidence["not_run"].append(
            {"id": "page_logic", "reason": page_logic.get("reason", "node is unavailable")}
        )

    # -- 8. versions and environment ---------------------------------------
    go_version = "unavailable"
    if toolchain is not None:
        try:
            completed = subprocess.run(
                [toolchain.go_binary(), "version"],
                capture_output=True,
                text=True,
                timeout=60,
                stdin=subprocess.DEVNULL,
            )
            if completed.returncode == 0:
                go_version = completed.stdout.strip()
        except OSError:
            go_version = "unavailable"
    node_version = "unavailable"
    node = shutil.which("node")
    if node is not None:
        completed = subprocess.run(
            [node, "--version"],
            capture_output=True,
            text=True,
            timeout=60,
            stdin=subprocess.DEVNULL,
        )
        if completed.returncode == 0:
            node_version = completed.stdout.strip()
    evidence["versions"] = {
        "python": platform.python_version(),
        "go": go_version,
        "node": node_version,
        "postgres": (environment or {}).get("database", {}).get("server_version", "unavailable"),
        "os": platform.platform(),
    }
    evidence["profile"] = {
        "name": PROFILE,
        "explicit": True,
        "stub_results": True,
        "stub_visible_in_product_output": [
            "/api/status: profile=dev-stub, stub_results=true",
            "/ (status page): renders the profile and stub flag",
            "model compatibility report: provider=stub, stub=true",
        ],
    }
    evidence["environment"] = environment or {}
    evidence["finished_at"] = _now()

    # -- 9. no credential may reach the evidence ---------------------------
    serialized = json.dumps(evidence, ensure_ascii=False)
    for secret in secrets_seen:
        assert secret and secret not in serialized, "a credential leaked into the evidence"
    assert "postgresql://" not in serialized, "a DSN leaked into the evidence"
    assert "PGPASSWORD" not in serialized, "a password environment name leaked into the evidence"
    try:
        import pg_dev

        pg_password = pg_dev.load_secret()
        assert pg_password not in serialized, "the PostgreSQL password leaked into the evidence"
    except Exception:  # noqa: BLE001 - .env.local is optional for a remote smoke run
        pass
    return evidence


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def print_summary(evidence: dict) -> None:
    print("")
    print(f"[smoke] {evidence.get('schema')} status={evidence.get('status')} profile={evidence.get('profile', {}).get('name')}")
    for step in evidence.get("steps", []):
        print(f"[smoke]   {step['name']:<16} {step['status']}")
    for blocked in evidence.get("blocked", []):
        print(f"[smoke]   blocked   {blocked['id']}: {blocked['reason'][:120]}")
    for skipped in evidence.get("not_run", []):
        print(f"[smoke]   not_run   {skipped['id']}: {skipped['reason'][:120]}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="run the F15A/T057 sales/failure/cancel/model-profile smoke against a running API"
    )
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--admin-login", default="admin")
    parser.add_argument("--admin-password-env", default=ADMIN_PASSWORD_ENV)
    parser.add_argument("--out", default=None, help="write the machine-readable run record here")
    parser.add_argument("--internal-url", default=None, help="internal mTLS base URL (claim boundary check)")
    parser.add_argument("--ca", default=None)
    parser.add_argument("--client-cert", default=None)
    parser.add_argument("--client-key", default=None)
    parser.add_argument("--client-role", default="supervisor")
    args = parser.parse_args(argv)
    password = os.environ.get(args.admin_password_env)
    if not password:
        if not sys.stdin.isatty():
            print(f"[smoke] set {args.admin_password_env} (never argv)", file=sys.stderr)
            return 2
        import getpass

        password = getpass.getpass("administrator password (hidden): ")
    internal = None
    if args.internal_url:
        required = {"ca": args.ca, "client-cert": args.client_cert, "client-key": args.client_key}
        missing = [name for name, value in required.items() if not value]
        if missing:
            print(f"[smoke] internal flags incomplete: missing {', '.join(missing)}", file=sys.stderr)
            return 2
        internal = InternalAccess(
            url=args.internal_url,
            ca=args.ca,
            client_cert=args.client_cert,
            client_key=args.client_key,
            role=args.client_role,
        )
    evidence = run(
        base_url=args.base_url,
        admin_login=args.admin_login,
        admin_password=password,
        internal=internal,
        command="python deploy/dev/smoke.py " + " ".join(argv if argv is not None else sys.argv[1:]),
    )
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"[smoke] evidence written to {out_path}")
    print_summary(evidence)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
