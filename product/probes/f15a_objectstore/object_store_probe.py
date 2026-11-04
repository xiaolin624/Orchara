#!/usr/bin/env python3
"""F15A/T053 — synthetic S3-compatible object-store compatibility probe.

Purpose: a deployer can decide, with reproducible evidence, whether a candidate
S3-compatible service can back the platform's object store. Incompatible
implementations are rejected by the probe (exit 1), not accepted by hand-waving.

Exit codes
    0  every assertion passed (or was honestly not_run, e.g. no versioning)
    1  a real compatibility violation was observed
    2  configuration/environment failure — including "no candidate endpoint"

Configuration
    Non-secret flags: --endpoint, --region, --bucket, --tenant-prefix,
    --timeout, --short-ttl, --expiry-skew, --retries, --ca-file, --out/--emit.
    Credentials come **only** from the environment (a process command line is
    visible to every local user)::

        F15A_S3_ENDPOINT            required unless --endpoint
        F15A_S3_BUCKET              required unless --bucket
        F15A_S3_REGION              default us-east-1
        F15A_S3_ACCESS_KEY          required (runtime credential)
        F15A_S3_SECRET_KEY          required (runtime credential)
        F15A_S3_SESSION_TOKEN       optional
        F15A_S3_PROVISION_ACCESS_KEY / _SECRET_KEY / _SESSION_TOKEN
                                    optional; required to seed sealed objects and
                                    to assert sealed lifecycle boundaries
        F15A_PREFIX                 tenant key prefix, default orchara-probe
        F15A_S3_TIMEOUT             seconds, default 10
        F15A_S3_EXPIRY_SKEW         seconds of documented candidate clock grace
        F15A_S3_RETRIES             retries for transient transport/5xx failures
        F15A_S3_CA_FILE             private CA bundle for an internal endpoint
        F15A_PROFILE                evidence label (e.g. candidate, fixture)

Evidence is JSON written with --out; no credential value and no object body ever
appears in it (sizes, SHA-256 digests and probe-generated key names only).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

PROBE_ROOT = Path(__file__).resolve().parent
if str(PROBE_ROOT) not in sys.path:
    sys.path.insert(0, str(PROBE_ROOT))

from f15a_probe import __version__  # noqa: E402
from f15a_probe import config, runner  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Synthetic S3-compatible compatibility probe (F15A/T053)",
        epilog=(
            "Credentials are read from the environment only, never from the command "
            "line and never written to evidence: F15A_S3_ACCESS_KEY, "
            "F15A_S3_SECRET_KEY, F15A_S3_SESSION_TOKEN, "
            "F15A_S3_PROVISION_ACCESS_KEY, F15A_S3_PROVISION_SECRET_KEY. "
            "Target/timing values may come from flags or F15A_S3_ENDPOINT, "
            "F15A_S3_BUCKET, F15A_S3_REGION, F15A_PREFIX, F15A_S3_TIMEOUT."
        ),
    )
    parser.add_argument("--endpoint", help="http(s)://host:port of the candidate S3 service")
    parser.add_argument("--region", help="SigV4 region (default us-east-1)")
    parser.add_argument("--bucket", help="bucket to probe")
    parser.add_argument(
        "--tenant-prefix",
        "--prefix",
        dest="tenant_prefix",
        help="tenant key prefix inside the bucket (default orchara-probe)",
    )
    parser.add_argument(
        "--out",
        "--emit",
        dest="out",
        help="write the JSON evidence report to this path",
    )
    parser.add_argument("--timeout", type=float, help="per-request timeout in seconds")
    parser.add_argument(
        "--short-ttl",
        type=int,
        dest="short_ttl",
        help="presigned URL TTL for the expiry assertion, seconds (default 2)",
    )
    parser.add_argument(
        "--expiry-skew",
        type=float,
        dest="expiry_skew",
        help="documented clock-skew grace of the candidate, seconds (default 0)",
    )
    parser.add_argument("--retries", type=int, help="transient-failure retries (default 2)")
    parser.add_argument("--ca-file", dest="ca_file", help="private CA bundle for HTTPS")
    parser.add_argument("--profile", help="evidence label, e.g. candidate or fixture")
    parser.add_argument("--version", action="store_true", help="print the probe version")
    return parser


def _emit(report: dict, out: str | None) -> Path | None:
    if not out:
        return None
    path = Path(out)
    if path.parent and str(path.parent):
        path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return path


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.version:
        print(f"f15a-s3-compat {__version__}")
        return 0

    try:
        target = config.load_target(args)
    except config.ConfigError as exc:
        endpoint_configured = bool(
            getattr(args, "endpoint", None) or os.environ.get("F15A_S3_ENDPOINT")
        )
        report = runner.config_blocked_report(exc, endpoint_configured=endpoint_configured)
        path = _emit(report, args.out)
        for line in runner.summary_lines(report):
            print(line)
        if path is not None:
            print(f"report: {path}")
        return report["exit_code"]

    report = runner.run_probe(target)
    path = _emit(report, args.out)
    for line in runner.summary_lines(report):
        print(line)
    if path is not None:
        print(f"report: {path}")
    return int(report["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
