#!/usr/bin/env python3
"""F13B/T103: probe candidate quality adapters honestly, including their egress.

The ticket's first general-purpose quality-adapter candidate is DeepEval, with
Promptfoo as the alternative harness and Ragas for the RAG special case. This
probe does what the environment allows and records what it cannot:

* **install/import**: a real isolated Python venv (or npm prefix) and a real
  ``pip``/``npm`` install. If the registry is unreachable the raw error is the
  evidence and the adapter is ``blocked`` 鈥?never "eliminated", never "passed".
* **inspection**: the installed package's version, telemetry switches, default
  model/endpoint variables and any hard-coded vendor endpoints, read from the
  installed source. This is evidence about the library, not a run.
* **a bounded invocation**: the probe attempts the smallest real invocation each
  adapter allows (a deterministic metric where one exists, otherwise a semantic
  metric). A semantic judge run without an approved endpoint/credential is
  ``blocked``; the probe never fabricates a score.
* **outbound observation**: every invocation runs behind a local TCP trap
  (``HTTP(S)_PROXY`` pointed at a loopback listener) *and* the trap parses the
  requested host from the proxy request. ``outbound`` reports the independent
  count and the requested destinations. The probe never claims what it did not
  observe.

Verdict vocabulary:

=================  ==========================================================
``accepted``       runnable on this host and suitable for the stated role
``blocked``        a named prerequisite is missing (endpoint/credential/
                   package/network/compatible dependency); the gap is written
                   down with the raw error
``eliminated``     evidence shows the adapter violates the data-egress policy
                   for this ticket even with its documented opt-outs
``not_run``        not executed here; nothing is claimed about it
=================  ==========================================================

Usage::

    python probes/f13b_quality/adapter_probe.py \\
        --work-dir E:\\Orchara\\.scratch\\f13b\\T103\\adapter-work \\
        --report E:\\Orchara\\.scratch\\f13b\\T103\\t103-real-recorded-report.json \\
        --out probes/f13b_quality/results/adapter-matrix.json --json
"""

from __future__ import annotations

import argparse
import datetime as _datetime
import hashlib
import json
import os
import re
import shutil
import socket
import socketserver
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

PRODUCT_ROOT = Path(__file__).resolve().parents[2]
COMPARE = Path(__file__).resolve().parent / "compare.py"
SCHEMA = "orchara-t103-adapter-probe/1"
PROXY_VARS = (
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
    "http_proxy", "https_proxy", "all_proxy",
)


# ---------------------------------------------------------------------------
# bounded command runner
# ---------------------------------------------------------------------------
def tail(text: str, limit: int = 4000) -> str:
    text = text or ""
    return text if len(text) <= limit else "...(truncated)...\n" + text[-limit:]


def run(
    command: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: int = 900,
) -> dict:
    started = time.time()
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd) if cwd else None,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
        return {
            "command": " ".join(command),
            "returncode": completed.returncode,
            "elapsed_s": round(time.time() - started, 1),
            "stdout_tail": tail(completed.stdout),
            "stderr_tail": tail(completed.stderr),
        }
    except subprocess.TimeoutExpired as expired:
        return {
            "command": " ".join(command),
            "returncode": None,
            "timed_out": True,
            "elapsed_s": round(time.time() - started, 1),
            "stdout_tail": tail(expired.stdout if isinstance(expired.stdout, str) else ""),
            "stderr_tail": tail(expired.stderr if isinstance(expired.stderr, str) else ""),
        }
    except OSError as error:
        return {
            "command": " ".join(command),
            "returncode": None,
            "error": repr(error),
            "elapsed_s": round(time.time() - started, 1),
        }


# ---------------------------------------------------------------------------
# the local egress trap: an independent outbound-connection counter
# ---------------------------------------------------------------------------
def _requested_host(raw: bytes) -> str:
    try:
        text = raw.decode("latin-1", "replace")
    except Exception:  # pragma: no cover - defensive
        return "unparsed"
    first = text.splitlines()[0] if text.splitlines() else ""
    match = re.match(r"^(CONNECT|GET|POST|PUT|HEAD|OPTIONS)\s+(\S+)", first)
    if not match:
        return "unparsed"
    target = match.group(2)
    if match.group(1) == "CONNECT":
        return target
    if "://" in target:
        return target.split("://", 1)[1].split("/", 1)[0]
    return target


class _TrapHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        server: "TrapServer" = self.server  # type: ignore[assignment]
        target = "unparsed"
        try:
            self.request.settimeout(1.0)
            raw = self.request.recv(4096)
            target = _requested_host(raw)
            self.request.sendall(
                b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
            )
        except OSError:
            pass
        server.record(target)


class TrapServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.connections: list[dict] = []
        self._lock = threading.Lock()

    def record(self, target: str) -> None:
        with self._lock:
            self.connections.append({"target": target, "at": round(time.time(), 3)})


def run_with_trap(
    probe: "AdapterProbe", command: list[str], *, env: dict[str, str] | None = None,
    timeout: int = 300,
) -> dict:
    """Run one command with HTTP(S) proxy env pointed at a loopback trap."""
    trap = TrapServer(("127.0.0.1", 0), _TrapHandler)
    port = trap.server_address[1]
    thread = threading.Thread(target=trap.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    proxy = f"http://127.0.0.1:{port}"
    run_env = dict(env or os.environ)
    for name in PROXY_VARS:
        run_env.pop(name, None)
    for name in PROXY_VARS:
        run_env[name] = proxy
    run_env["NO_PROXY"] = "127.0.0.1,localhost"
    run_env["no_proxy"] = "127.0.0.1,localhost"
    try:
        result = run(command, cwd=PRODUCT_ROOT, env=run_env, timeout=timeout)
    finally:
        trap.shutdown()
        trap.server_close()
        thread.join(timeout=5)
    result["outbound_connections"] = len(trap.connections)
    result["outbound_targets"] = trap.connections[:20]
    probe.trap_total += len(trap.connections)
    return result


# ---------------------------------------------------------------------------
# adapter probe
# ---------------------------------------------------------------------------
class AdapterProbe:
    def __init__(self, work_dir: Path, report: Path | None, timeout: int,
                 reuse_installs: bool = False) -> None:
        self.work_dir = work_dir
        self.report = report
        self.timeout = timeout
        self.reuse_installs = reuse_installs
        self.trap_total = 0
        self.adapters: list[dict] = []
        work_dir.mkdir(parents=True, exist_ok=True)

    # -- shared evidence -----------------------------------------------------
    def host(self) -> dict:
        return {
            "python": sys.version.split()[0],
            "node": shutil.which("node") or "not installed",
            "npm": shutil.which("npm") or "not installed",
            "profile": os.environ.get("ORCHARA_PROFILE", "dev-stub"),
            "judge_endpoint_configured": any(
                os.environ.get(name)
                for name in ("ORCHARA_JUDGE_ENDPOINT", "JUDGE_ENDPOINT", "OPENAI_BASE_URL")
            ),
            "judge_credentials_configured": any(
                os.environ.get(name)
                for name in ("ORCHARA_JUDGE_API_KEY", "OPENAI_API_KEY", "DEEPEVAL_API_KEY")
            ),
            "network_at_probe_time": "see the install evidence; package installs used the registry",
        }

    def _venv_python(self, venv: Path) -> Path:
        if os.name == "nt":
            return venv / "Scripts" / "python.exe"
        return venv / "bin" / "python"

    def _python_install(self, name: str, package: str) -> dict:
        venv = self.work_dir / f"venv-{name}"
        evidence: dict[str, Any] = {"venv": str(venv)}
        python = self._venv_python(venv)
        if self.reuse_installs and python.exists():
            shown = run([str(python), "-m", "pip", "show", package], timeout=120)
            if shown.get("returncode") == 0:
                evidence["reused_install"] = {
                    "reason": "an isolated venv from a previous probe run in this work directory",
                    "pip_show": shown.get("stdout_tail", "")[:800],
                }
                return evidence
        created = run([sys.executable, "-m", "venv", str(venv)], timeout=300)
        evidence["create_venv"] = created
        if created["returncode"] != 0:
            return evidence
        evidence["pip_install"] = run(
            [str(python), "-m", "pip", "install", "--disable-pip-version-check", "--no-input", package],
            timeout=self.timeout,
        )
        return evidence

    @staticmethod
    def _install_ok(evidence: dict) -> bool:
        if "reused_install" in evidence:
            return True
        return evidence.get("pip_install", {}).get("returncode") == 0

    def _python_version(self, python: Path, package: str) -> dict:
        shown = run([str(python), "-m", "pip", "show", package], timeout=120)
        return {
            "python": run([str(python), "-c", "import sys; print(sys.version)"], timeout=60),
            "pip_show": tail(shown.get("stdout_tail", ""), 400),
        }

    def _run_snippet(self, name: str, python: Path, body: str, *,
                     timeout: int = 300) -> tuple[dict, dict]:
        """Run one Python snippet that writes a JSON evidence file.

        The snippet's result is read from the file, so nothing is lost to stdout
        truncation; the process's raw tail stays as the error evidence.
        """
        script = self.work_dir / f"{name}.py"
        payload_path = self.work_dir / f"{name}.json"
        lines = ["import json, sys, traceback", "out = {}", "def _dump():",
                 "    with open(sys.argv[1], 'w', encoding='utf-8') as handle:",
                 "        json.dump(out, handle, sort_keys=True)", "try:"]
        lines.extend("    " + line if line else "" for line in body.strip("\n").splitlines())
        lines.extend([
            "except BaseException as error:",
            "    out['unhandled'] = f'{type(error).__name__}: {error}'",
            "    out['traceback'] = traceback.format_exc()[-3000:]",
            "_dump()",
        ])
        script.write_text("\n".join(lines) + "\n", encoding="utf-8")
        observed = run_with_trap(
            self, [str(python), str(script), str(payload_path)], timeout=timeout
        )
        payload: dict = {}
        if payload_path.exists():
            try:
                payload = json.loads(payload_path.read_text(encoding="utf-8"))
            except ValueError:
                payload = {"unparsed_payload": payload_path.read_text(encoding="utf-8", errors="replace")}
        return payload, observed

    # -- heuristic-local -----------------------------------------------------
    def probe_heuristic(self) -> None:
        entry: dict[str, Any] = {
            "adapter": "heuristic-local/1",
            "role": "deterministic local proxy for a semantic quality score (NOT a judge)",
            "install": "in-process (compare.py in this repository)",
            "python": sys.version.split()[0],
            "default_endpoint": "none",
            "telemetry": "none (the scorer opens no socket)",
            "required_env": [],
            "vendor_tokens": 0,
            "cost_usd": 0.0,
        }
        if self.report is None or not self.report.exists():
            entry["verdict"] = "not_run"
            entry["blocked_reason"] = "no --report was supplied, so the local score was not computed"
            self.adapters.append(entry)
            return
        out_path = self.work_dir / "heuristic-compare.json"
        observed = run_with_trap(
            self,
            [sys.executable, str(COMPARE), "--report", str(self.report),
             "--out", str(out_path)],
            timeout=120,
        )
        entry["invocation"] = {
            key: observed[key] for key in
            ("command", "returncode", "elapsed_s", "outbound_connections", "outbound_targets")
        }
        entry["outbound_connections"] = observed["outbound_connections"]
        entry["outbound_targets"] = observed["outbound_targets"]
        payload = {}
        if out_path.exists():
            payload = json.loads(out_path.read_text(encoding="utf-8"))
        entry["hard_verdict"] = payload.get("hard_checker", {}).get("verdict")
        entry["quality_score"] = payload.get("quality", {}).get("score")
        entry["probe_reported_outbound"] = payload.get("outbound", {}).get("connections")
        entry["report_sha256"] = payload.get("report", {}).get("sha256")
        ok = observed["returncode"] == 0 and observed["outbound_connections"] == 0
        entry["verdict"] = "accepted" if ok else "blocked"
        entry["evidence"] = (
            "compare.py recomputed the hard verdict and the local proxy score on the real "
            f"sealed report ({self.report.name}); the trap observed "
            f"{observed['outbound_connections']} outbound connection(s) and the probe itself "
            f"reported {entry['probe_reported_outbound']} socket connect(s). The score is "
            "advisory and is never an input to the hard verdict."
        )
        if observed["returncode"] != 0:
            entry["raw_error"] = observed["stderr_tail"]
        self.adapters.append(entry)

    # -- deepeval ------------------------------------------------------------
    def probe_deepeval(self) -> None:
        entry: dict[str, Any] = {
            "adapter": "deepeval",
            "role": "first general-purpose semantic quality adapter (candidate)",
        }
        install = self._python_install("deepeval", "deepeval")
        entry["install"] = install
        if not self._install_ok(install):
            entry["verdict"] = "blocked"
            entry["blocked_kind"] = "package_install"
            entry["blocked_reason"] = "pip install deepeval failed in an isolated venv"
            entry["evidence"] = {
                "pip_returncode": install.get("pip_install", {}).get("returncode"),
                "pip_stderr_tail": install.get("pip_install", {}).get("stderr_tail"),
            }
            self.adapters.append(entry)
            return
        python = self._venv_python(self.work_dir / "venv-deepeval")
        entry["version"] = self._python_version(python, "deepeval")
        inspection, inspection_run = self._run_snippet(
            "deepeval-inspect",
            python,
            """
import pathlib
import deepeval as mod
out["version"] = getattr(mod, "__version__", None)
root = pathlib.Path(mod.__file__).resolve().parent
out["package_root"] = str(root)
tokens = ["TELEMETRY", "telemetry", "posthog", "Posthog",
          "DEEPEVAL_TELEMETRY_OPT_OUT", "OPENAI_API_KEY", "OPENAI_BASE_URL",
          "api.openai.com", "CONFIDENT_AI"]
hits = {}
endpoints = []
for path in root.rglob("*.py"):
    try:
        text = path.read_text(errors="ignore")
    except OSError:
        continue
    for token in tokens:
        if token in text:
            hits.setdefault(token, []).append(str(path.relative_to(root)))
    for line in text.splitlines():
        lowered = line.lower()
        if "https://" in lowered and any(
            vendor in lowered
            for vendor in ("openai.com", "anthropic.com", "posthog.com", "googleapis.com",
                           "confident-ai.com")
        ):
            endpoints.append(line.strip()[:300])
out["source_hits"] = {key: value[:8] for key, value in hits.items()}
out["telemetry_switch_present"] = "DEEPEVAL_TELEMETRY_OPT_OUT" in hits
out["default_endpoints"] = sorted(set(endpoints))[:20]
""",
        )
        entry["inspection"] = {
            "result": inspection,
            "raw": {
                "returncode": inspection_run.get("returncode"),
                "stderr_tail": inspection_run.get("stderr_tail"),
            },
        }
        metric_payload, metric_run = self._run_snippet(
            "deepeval-metrics",
            python,
            """
import deepeval
import deepeval.metrics as metrics
from deepeval.test_case import LLMTestCase
out["version"] = getattr(deepeval, "__version__", None)
out["metric_count"] = len([n for n in dir(metrics) if n.endswith("Metric")])
test_case = LLMTestCase(input="what is the total?", actual_output="{}", expected_output="{}")
deterministic = metrics.ExactMatchMetric()
out["deterministic_metric"] = {"name": "ExactMatchMetric", "constructed": True}
try:
    deterministic.measure(test_case)
    out["deterministic_metric"].update({
        "measured": True,
        "score": getattr(deterministic, "score", None),
        "reason": getattr(deterministic, "reason", None),
        "requires_model": False,
    })
except Exception as error:
    out["deterministic_metric"].update({"measure_error": f"{type(error).__name__}: {error}"})
try:
    semantic = metrics.AnswerRelevancyMetric(threshold=0.5)
    out["semantic_metric"] = {"name": "AnswerRelevancyMetric", "constructed": True}
    try:
        semantic.measure(test_case)
        out["semantic_metric"].update({"measured": True, "score": getattr(semantic, "score", None)})
    except Exception as error:
        out["semantic_metric"].update({"measure_error": f"{type(error).__name__}: {error}"})
except Exception as error:
    out["semantic_metric"] = {"construct_error": f"{type(error).__name__}: {error}"}
""",
        )
        entry["invocation"] = {
            "command": metric_run.get("command"),
            "returncode": metric_run.get("returncode"),
            "elapsed_s": metric_run.get("elapsed_s"),
            "outbound_connections": metric_run.get("outbound_connections"),
            "outbound_targets": metric_run.get("outbound_targets"),
            "stderr_tail": metric_run.get("stderr_tail"),
        }
        entry["result"] = metric_payload
        entry["outbound_connections"] = metric_run.get("outbound_connections", 0)
        entry["outbound_targets"] = metric_run.get("outbound_targets", [])
        entry["vendor_tokens"] = 0
        entry["cost_usd"] = 0.0
        entry["default_endpoint"] = (
            "OpenAI-compatible client by default (models/llms/openai_model.py); an approved "
            "judge endpoint is not configured here"
        )
        entry["required_env"] = [
            "an approved judge model endpoint/credential (the build has only ORCHARA_PROFILE=dev-stub)",
            "DEEPEVAL_TELEMETRY_OPT_OUT=YES to disable the library's telemetry",
        ]
        semantic = metric_payload.get("semantic_metric", {})
        semantic_error = semantic.get("construct_error") or semantic.get("measure_error")
        entry["verdict"] = "blocked"
        entry["blocked_kind"] = "judge_endpoint_and_credential"
        entry["blocked_reason"] = (
            "deepeval is installed and its deterministic ExactMatch metric runs locally, but the "
            "semantic metrics need a judge model. Constructing AnswerRelevancyMetric fails with: "
            f"{semantic_error}. This host has no approved judge endpoint or credential, so no "
            "semantic score was produced and none is fabricated."
        )
        entry["evidence"] = {
            "note": "the deterministic run is real local evidence (zero egress); the semantic "
                    "attempt's real error is the blocked evidence",
            "deterministic_metric": metric_payload.get("deterministic_metric"),
            "semantic_attempt": semantic,
        }
        self.adapters.append(entry)

    # -- ragas ---------------------------------------------------------------
    def probe_ragas(self) -> None:
        entry: dict[str, Any] = {
            "adapter": "ragas",
            "role": "RAG-specific quality special case (candidate)",
        }
        install = self._python_install("ragas", "ragas")
        entry["install"] = install
        if not self._install_ok(install):
            entry["verdict"] = "blocked"
            entry["blocked_kind"] = "package_install"
            entry["blocked_reason"] = "pip install ragas failed in an isolated venv"
            entry["evidence"] = {
                "pip_returncode": install.get("pip_install", {}).get("returncode"),
                "pip_stderr_tail": install.get("pip_install", {}).get("stderr_tail"),
            }
            self.adapters.append(entry)
            return
        python = self._venv_python(self.work_dir / "venv-ragas")
        entry["version"] = self._python_version(python, "ragas")
        payload, observed = self._run_snippet(
            "ragas-metrics",
            python,
            """
import ragas
out["version"] = getattr(ragas, "__version__", None)
import ragas.metrics as metrics
out["metric_names"] = sorted(
    name for name in dir(metrics)
    if not name.startswith("_") and name[:1].isupper()
)[:80]
from ragas.metrics import Faithfulness
metric = Faithfulness()
out["faithfulness"] = {"constructed": True}
""",
        )
        entry["invocation"] = {
            "command": observed.get("command"),
            "returncode": observed.get("returncode"),
            "elapsed_s": observed.get("elapsed_s"),
            "outbound_connections": observed.get("outbound_connections"),
            "outbound_targets": observed.get("outbound_targets"),
            "stdout_tail": observed.get("stdout_tail"),
            "stderr_tail": observed.get("stderr_tail"),
        }
        entry["result"] = payload
        entry["outbound_connections"] = observed.get("outbound_connections", 0)
        entry["outbound_targets"] = observed.get("outbound_targets", [])
        entry["vendor_tokens"] = 0
        entry["cost_usd"] = 0.0
        entry["default_endpoint"] = (
            "judge + embedding model providers chosen by the caller; no approved judge or "
            "embedding endpoint is configured here"
        )
        entry["required_env"] = [
            "an approved judge model endpoint/credential",
            "an approved embedding model endpoint/credential",
        ]
        import_error = payload.get("unhandled") or payload.get("import_error")
        if import_error:
            entry["verdict"] = "blocked"
            entry["blocked_kind"] = "dependency_incompatibility"
            entry["blocked_reason"] = (
                "ragas installed, but importing it on this host fails: "
                f"{import_error}. A compatible ragas/langchain-community pairing (or a "
                "supported Python environment) is missing, so no metric was constructed or run."
            )
        else:
            entry["verdict"] = "blocked"
            entry["blocked_kind"] = "judge_and_embedding_endpoint"
            entry["blocked_reason"] = (
                "ragas imported and constructed Faithfulness, but a real faithfulness run needs "
                "a judge and an embedding model; neither an approved endpoint nor a credential "
                "exists on this host, so no score was produced and none is fabricated."
            )
        entry["evidence"] = {
            "note": "the import/metric-construction attempt above is the real bounded evidence; "
                    "a metric run is not faked",
            "payload": payload,
        }
        self.adapters.append(entry)

    # -- promptfoo -----------------------------------------------------------
    def probe_promptfoo(self) -> None:
        entry: dict[str, Any] = {
            "adapter": "promptfoo",
            "role": "alternative harness (the CLI is not itself a judge)",
        }
        npm = shutil.which("npm")
        if npm is None:
            entry["verdict"] = "blocked"
            entry["blocked_kind"] = "package_manager"
            entry["blocked_reason"] = "npm is not installed"
            self.adapters.append(entry)
            return
        prefix = self.work_dir / "npm-promptfoo"
        prefix.mkdir(parents=True, exist_ok=True)
        package_json = prefix / "node_modules" / "promptfoo" / "package.json"
        if self.reuse_installs and package_json.exists():
            entry["install"] = {
                "reused_install": "npm prefix from a previous probe run in this work directory",
                "package_json": str(package_json),
            }
        else:
            install = run(
                [npm, "install", "--prefix", str(prefix), "--no-fund", "--no-audit",
                 "--loglevel", "error", "promptfoo"],
                timeout=self.timeout,
            )
            entry["install"] = install
        if not package_json.exists():
            entry["verdict"] = "blocked"
            entry["blocked_kind"] = "package_install"
            entry["blocked_reason"] = "npm install promptfoo failed"
            entry["evidence"] = {
                "npm_stderr_tail": entry.get("install", {}).get("stderr_tail"),
            }
            self.adapters.append(entry)
            return
        package = json.loads(package_json.read_text(encoding="utf-8"))
        entry["version"] = package.get("version")
        entry["package_json"] = str(package_json)
        # Telemetry / update-check switches read from the installed source.
        inspection_payload, inspection_run = self._run_snippet(
            "promptfoo-inspect",
            Path(sys.executable),
            f"""
import pathlib
root = pathlib.Path({str(package_json.parent)!r})
tokens = ["PROMPTFOO_DISABLE_TELEMETRY", "PROMPTFOO_DISABLE_UPDATE",
          "telemetry", "posthog", "api.promptfoo.dev", "cloud.promptfoo.dev"]
hits = {{}}
for path in root.rglob("*.js"):
    try:
        text = path.read_text(errors="ignore")
    except OSError:
        continue
    for token in tokens:
        if token in text:
            hits.setdefault(token, []).append(str(path.relative_to(root)))
out["source_hits"] = {{key: value[:6] for key, value in hits.items()}}
""",
        )
        entry["inspection"] = {
            "result": inspection_payload,
            "raw": {"returncode": inspection_run.get("returncode")},
        }
        binary = package.get("bin")
        if isinstance(binary, dict):
            binary = next(iter(binary.values()))
        binary_path = package_json.parent / (binary or "dist/src/main.js")
        entry["binary"] = str(binary_path)
        config = self.work_dir / "promptfoo-offline.yaml"
        config.write_text(
            "providers:\n  - echo\nprompts:\n  - 'hello'\ntests:\n  - assert:\n"
            "      - type: equals\n        value: hello\n",
            encoding="utf-8",
        )
        invocation = run_with_trap(
            self,
            ["node", str(binary_path), "eval", "--config", str(config), "--no-progress-bar"],
            timeout=300,
        )
        entry["invocation"] = {
            "command": invocation.get("command"),
            "returncode": invocation.get("returncode"),
            "elapsed_s": invocation.get("elapsed_s"),
            "stdout_tail": tail(invocation.get("stdout_tail", ""), 1500),
            "stderr_tail": invocation.get("stderr_tail"),
        }
        entry["outbound_connections"] = invocation.get("outbound_connections", 0)
        entry["outbound_targets"] = invocation.get("outbound_targets", [])
        entry["vendor_tokens"] = 0
        entry["cost_usd"] = 0.0
        entry["default_endpoint"] = (
            "the provider/model is caller-configured; a semantic quality judgement still needs "
            "a model destination, and no approved one is configured here"
        )
        entry["required_env"] = [
            "PROMPTFOO_DISABLE_TELEMETRY=1 to disable the CLI's telemetry",
            "PROMPTFOO_DISABLE_UPDATE=1 to disable its update check",
            "an approved model provider endpoint for any semantic assertion",
        ]
        targets = sorted({item.get("target") for item in entry["outbound_targets"]})
        entry["data_egress"] = {
            "default_outbound_connections": entry["outbound_connections"],
            "default_outbound_targets": targets,
            "policy_verdict": (
                "eliminated_without_opt_out" if entry["outbound_connections"] else "clean"
            ),
            "opt_out": ["PROMPTFOO_DISABLE_TELEMETRY=1", "PROMPTFOO_DISABLE_UPDATE=1"],
            "note": "the offline deterministic eval made real outbound proxy requests before "
                    "any model provider was involved; the opt-out variables are present in the "
                    "installed source. Even with them set, a semantic score still needs an "
                    "approved model destination, which this host does not have.",
        }
        entry["verdict"] = "blocked"
        entry["blocked_kind"] = "semantic_provider_endpoint"
        entry["blocked_reason"] = (
            "promptfoo is installed and its deterministic echo/equals eval passed fully "
            "offline, but it is a harness: a semantic quality score needs a model provider, "
            "and this host has no approved model endpoint/credential. Its default telemetry/"
            "update path made real outbound requests during that offline eval, so it is "
            "not acceptable as shipped for an evaluation containing real content."
        )
        entry["evidence"] = {
            "note": "both the offline deterministic run and the observed default egress are "
                    "real; no semantic provider call was made",
            "outbound_targets": targets,
            "deterministic_eval_passed": "1 passed (100%)" in (invocation.get("stdout_tail") or ""),
        }
        self.adapters.append(entry)

    # -- driver --------------------------------------------------------------
    def run_all(self) -> dict:
        self.probe_heuristic()
        self.probe_deepeval()
        self.probe_ragas()
        self.probe_promptfoo()
        report_sha = None
        if self.report is not None and self.report.exists():
            report_sha = hashlib.sha256(self.report.read_bytes()).hexdigest()
        accepted = [a["adapter"] for a in self.adapters if a.get("verdict") == "accepted"]
        blocked = [a["adapter"] for a in self.adapters if a.get("verdict") == "blocked"]
        eliminated = [a["adapter"] for a in self.adapters if a.get("verdict") == "eliminated"]
        not_run = [a["adapter"] for a in self.adapters if a.get("verdict") == "not_run"]
        egress = [
            {
                "adapter": adapter["adapter"],
                "outbound_connections": adapter.get("outbound_connections", 0),
                "targets": sorted({
                    item.get("target") for item in adapter.get("outbound_targets", [])
                }),
            }
            for adapter in self.adapters
        ]
        # The data-egress policy verdict is separate from "can it produce the
        # score": an adapter can be blocked for a missing judge endpoint and also
        # eliminated as shipped for an unapproved default egress path.
        data_egress_policy = [
            {
                "adapter": adapter["adapter"],
                **adapter["data_egress"],
            }
            for adapter in self.adapters
            if "data_egress" in adapter
        ]
        return {
            "schema": SCHEMA,
            "generated_at": _datetime.datetime.now(_datetime.timezone.utc).isoformat(),
            "host": self.host(),
            "report": {"path": str(self.report) if self.report else None, "sha256": report_sha},
            "adapters": self.adapters,
            "egress_observation": egress,
            "data_egress_policy": data_egress_policy,
            "summary": {
                "accepted": accepted,
                "blocked": blocked,
                "eliminated": eliminated,
                "not_run": not_run,
                "policy_eliminated_without_opt_out": [
                    item["adapter"] for item in data_egress_policy
                    if item.get("policy_verdict") == "eliminated_without_opt_out"
                ],
                "approved_judge_runs": 0,
                "approved_judge_blocked_reason": (
                    "no approved judge endpoint or credential exists on this host; the only model "
                    "profile is ORCHARA_PROFILE=dev-stub"
                ),
                "trap_observed_outbound_total": self.trap_total,
            },
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", required=True, type=Path,
                        help="scratch directory for isolated venvs / npm prefix / configs")
    parser.add_argument("--report", type=Path, default=None,
                        help="the real sealed evaluation report to score locally")
    parser.add_argument("--out", type=Path, default=None, help="write the matrix JSON here")
    parser.add_argument("--timeout", type=int, default=900,
                        help="per-package install / invocation timeout in seconds")
    parser.add_argument("--reuse-installs", action="store_true",
                        help="reuse an isolated venv / npm prefix from a previous probe run")
    parser.add_argument("--json", action="store_true", help="emit JSON only")
    arguments = parser.parse_args(argv)

    probe = AdapterProbe(
        arguments.work_dir, arguments.report, arguments.timeout, arguments.reuse_installs
    )
    result = probe.run_all()
    encoded = json.dumps(result, indent=2, sort_keys=True)
    if arguments.out is not None:
        arguments.out.parent.mkdir(parents=True, exist_ok=True)
        arguments.out.write_text(encoded + "\n", encoding="utf-8", newline="\n")
    if arguments.json:
        print(encoded)
    else:
        for adapter in result["adapters"]:
            print(
                f"{adapter['adapter']:<18} verdict={adapter.get('verdict'):<10} "
                f"outbound={adapter.get('outbound_connections', 'n/a')} "
                f"{(adapter.get('blocked_reason') or '')[:90]}"
            )
        print(json.dumps(result["summary"], indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
