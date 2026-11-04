"""Run every assertion, decide the process verdict, and assemble the evidence report.

Exit codes are the contract: ``0`` all assertions hold, ``1`` a real compatibility
violation, ``2`` the probe could not do its job (configuration or environment).
A missing candidate endpoint therefore ends as ``blocked``/exit 2, never as a
green run.
"""

from __future__ import annotations

import hashlib
import platform
import secrets
import sys
import time
from datetime import datetime, timezone

from . import __version__
from .assertions import (
    ASSERTION_TITLES,
    ASSERTIONS,
    BLOCKED,
    FAILED,
    NOT_RUN,
    PASSED,
    AssertionResult,
    ProbeContext,
)
from .config import ConfigError, ProbeEnvironmentError, Target
from .httpclient import S3Client

EXIT_PASSED = 0
EXIT_VIOLATION = 1
EXIT_BLOCKED = 2

LIMITS = [
    "a passing run is a compatibility observation, not a licence, backup/restore or capacity review",
    "TLS, proxy and signature behaviour is only what the configured endpoint actually did",
    "IAM policy is asserted only for the two configured credentials; other roles are untested",
    "object versioning is asserted only when the candidate exposes it (otherwise not_run)",
    "backup/restore, cross-region replication and server-side encryption are not exercised",
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def environment_profile() -> dict:
    return {
        "python": sys.version.split()[0],
        "python_implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "probe_version": __version__,
    }


def _summary(assertions: list[AssertionResult]) -> dict:
    counts = {PASSED: 0, FAILED: 0, BLOCKED: 0, NOT_RUN: 0}
    for result in assertions:
        counts[result.status] = counts.get(result.status, 0) + 1
    return counts


def _verdict(assertions: list[AssertionResult]) -> tuple[str, int]:
    if any(result.status == FAILED for result in assertions):
        return "failed", EXIT_VIOLATION
    if any(result.status == BLOCKED for result in assertions):
        return "blocked", EXIT_BLOCKED
    return "passed", EXIT_PASSED


def _base_report(target: Target | None, run_id: str, started_at: str) -> dict:
    return {
        "probe": "f15a-s3-compat",
        "probe_version": __version__,
        "ticket": "T053/F15A",
        "scope": "candidate-s3",
        "run_id": run_id,
        "started_at": started_at,
        "environment": environment_profile(),
        "config": target.public_dict() if target is not None else {},
        "assertions": [],
        "summary": {PASSED: 0, FAILED: 0, BLOCKED: 0, NOT_RUN: 0},
        "client_stats": {"requests": 0, "retries": 0},
        "cleanup": {"attempted": 0, "deleted": 0, "failed": []},
        "limits": LIMITS,
    }


def config_blocked_report(error: ConfigError, *, endpoint_configured: bool) -> dict:
    timestamp = _now()
    report = _base_report(None, secrets.token_hex(8), timestamp)
    report.update(
        {
            "status": "blocked",
            "exit_code": EXIT_BLOCKED,
            "finished_at": timestamp,
            "duration_ms": 0,
            "blocked_reason": error.as_dict(),
            "config": {
                "endpoint_configured": endpoint_configured,
                "note": "invalid or missing configuration is never written to evidence verbatim",
            },
        }
    )
    return report


def _cleanup(ctx: ProbeContext) -> dict:
    if not ctx.target.provision_configured:
        return {"attempted": 0, "deleted": 0, "failed": [], "skipped": "no provisioning credentials"}
    keys = sorted({key for _, key in ctx.created_keys})
    deleted = 0
    failed: list[dict] = []
    for key in keys:
        try:
            response = ctx.client.request("DELETE", key, credential="provision")
        except ProbeEnvironmentError as exc:
            failed.append({"key": key, "error": exc.code})
            continue
        if response.status in (200, 204, 404):
            deleted += 1
        else:
            failed.append({"key": key, "status": response.status})
    return {"attempted": len(keys), "deleted": deleted, "failed": failed}


def run_probe(target: Target) -> dict:
    started_at = _now()
    started = time.monotonic()
    run_id = secrets.token_hex(8)
    client = S3Client(target)
    ctx = ProbeContext(target=target, client=client, run_id=run_id)
    report = _base_report(target, run_id, started_at)

    # A single unsigned request proves the endpoint is reachable before any
    # assertion claims something about it; transport failure is exit 2.
    try:
        client.request(
            "GET",
            f"{target.tenant_prefix}/sealed/{secrets.token_hex(32)}",
            anonymous=True,
        )
    except ProbeEnvironmentError as exc:
        report.update(
            {
                "status": "blocked",
                "exit_code": EXIT_BLOCKED,
                "finished_at": _now(),
                "duration_ms": int((time.monotonic() - started) * 1000),
                "blocked_reason": exc.as_dict(),
                "client_stats": {"requests": client.request_count, "retries": client.retry_count},
            }
        )
        return report

    results: list[AssertionResult] = []
    environment_failure: ProbeEnvironmentError | None = None
    for assertion in ASSERTIONS:
        try:
            results.append(assertion(ctx))
        except ProbeEnvironmentError as exc:
            environment_failure = exc
            evaluated = {result.id for result in results}
            for assertion_id, title in ASSERTION_TITLES.items():
                if assertion_id in evaluated:
                    continue
                results.append(
                    AssertionResult(
                        id=assertion_id,
                        title=title,
                        status=BLOCKED,
                        reason=f"not evaluated: environment failure ({exc.code})",
                    )
                )
            break

    report["assertions"] = [result.as_dict() for result in results]
    report["cleanup"] = _cleanup(ctx)
    report["client_stats"] = {
        "requests": client.request_count,
        "retries": client.retry_count,
    }
    report["input_hashes"] = {
        tag: {"sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body)}
        for tag, body in sorted(ctx.bodies.items())
    }
    report["finished_at"] = _now()
    report["duration_ms"] = int((time.monotonic() - started) * 1000)

    if environment_failure is not None:
        report.update(
            {
                "status": "blocked",
                "exit_code": EXIT_BLOCKED,
                "blocked_reason": environment_failure.as_dict(),
                "summary": _summary(results),
            }
        )
        return report

    status, exit_code = _verdict(results)
    report.update(
        {"status": status, "exit_code": exit_code, "summary": _summary(results)}
    )
    if status == "blocked":
        blocked = [result for result in results if result.status == BLOCKED]
        report["blocked_reason"] = {
            "code": "assertions_blocked",
            "message": "; ".join(f"{result.id}: {result.reason}" for result in blocked),
        }
    return report


def summary_lines(report: dict) -> list[str]:
    lines = [
        "f15a object-store compatibility probe "
        f"v{report['probe_version']} ({report['ticket']})",
    ]
    config = report.get("config", {})
    if config.get("endpoint"):
        lines.append(
            f"target: {config['endpoint']} bucket={config.get('bucket')} "
            f"region={config.get('region')} prefix={config.get('tenant_prefix')}"
        )
    lines.append(f"status: {report['status']} (exit {report['exit_code']})")
    if report.get("blocked_reason"):
        lines.append(
            f"blocked: {report['blocked_reason'].get('code')}: "
            f"{report['blocked_reason'].get('message')}"
        )
    summary = report.get("summary", {})
    if summary:
        lines.append(
            "assertions: "
            + ", ".join(f"{count} {name}" for name, count in summary.items())
        )
    for assertion in report.get("assertions", []):
        lines.append(f"  [{assertion['status']}] {assertion['id']}: {assertion['reason']}")
    return lines
