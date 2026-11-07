"""T110 / F13B security evidence support: attacks, real blocking layers and truth.

The T110 acceptance is a *platform security regression* against the real stack.
Every attack recorded here must name the layer that actually refused it, from the
closed set the ticket uses::

    os | network | proxy | app_validation | schema

and carry four things the brief requires:

* the **input** (what was sent) and its independently recomputed sha256,
* the **expected** pre-registered outcome versus the **observed** one,
* the **response** the real component returned (status, stable error code,
  request id, body sha256),
* the **independent truth** (PostgreSQL rows, sealed object bytes, the loopback
  endpoint's own request log, the process/OS view) plus the commands used.

Two rendering helpers live here too, because both are executed as **real
committed bytes**:

* ``xss_harness`` loads ``apps/web/src/assets/memories.js`` in a Node ``vm`` and
  calls the real ``renderMemory``/``renderCandidate`` with JSON that came from the
  real API. Its DOM is a deliberately small, recording shim, **not** a browser:
  it detects which DOM sinks the committed page code uses, it does not prove
  browser layout or the browser's own HTML parser. The report says so plainly.
* ``csv_export_probe`` imports the committed preset tool module and calls its
  real ``_write_outputs`` export writer in a subprocess, so the produced
  ``summary.csv`` bytes are the committed writer's bytes.

The evidence JSON is written only when ``F13B_WRITE_EVIDENCE=1`` (the T101
convention), so an ordinary pytest run never rewrites a committed evidence file.
Every string is passed through ``tools/evidence_redaction``; the writer refuses
to persist a document that still contains a credential-shaped value.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import socket
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path

TESTS_ROOT = Path(__file__).resolve().parents[1]
PRODUCT_ROOT = TESTS_ROOT.parent
TOOLS_ROOT = PRODUCT_ROOT / "tools"
WORKER_ROOT = PRODUCT_ROOT / "worker"
for _entry in (str(TOOLS_ROOT), str(TESTS_ROOT), str(WORKER_ROOT)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from evidence_redaction import find_violations, redact_value  # noqa: E402

#: Mirrors the T101 evidence convention: only an explicit evidence run rewrites.
WRITE_EVIDENCE_ENV = "F13B_WRITE_EVIDENCE"
EVIDENCE_JSON = PRODUCT_ROOT / "reports" / "f13b" / "T110-security.json"

#: The closed blocking-layer vocabulary the ticket requires.
LAYERS = ("os", "network", "proxy", "app_validation", "schema")

#: Attack families (EVALUATION.md §5 + SECURITY.md §3–§4).
FAMILIES = (
    "identity",
    "ssrf",
    "malicious_document",
    "xss",
    "csv_formula",
    "prompt_injection",
)

ENVIRONMENT = {
    "profile": "dev-stub (ORCHARA_PROFILE=dev-stub; no vendor credentials or endpoint)",
    "postgres": "PostgreSQL 16.4 at 127.0.0.1:55432; one throwaway database per stack",
    "api": "compiled cmd/api: real mTLS internal listener, real object store, HMAC signed links",
    "connector": "real loopback HTTP endpoint owned by the test; its request log is the independent ledger",
    "maintenance": "compiled cmd/maintenance running the real artifact_finalize/document_ingest/tool_run handlers",
    "dom": "Node vm + a minimal hand-written DOM/fetch shim executing the committed page bytes; "
           "NOT Playwright, NOT a real browser DOM",
    "model": "no vendor model is used: the knowledge answer path is the documented labelled "
             "extractive stub (dev-stub profile)",
    "sandbox": "WSL2/runsc is NOT exercised here (script/build isolation is T111's Linux sandbox slice)",
    "not_run": [
        "a real browser (Playwright/Chromium) parsing and executing the page HTML/CSS",
        "a real vendor connector endpoint (loopback stands in for the deployment-defined endpoint)",
        "a real vendor model obeying or resisting an injected instruction (no credentials/endpoint)",
        "OS/network sandbox escape from a script (T111 reruns the build/script isolation on Linux)",
        "Excel/LibreOffice opening the exported CSV (the bytes are read and parsed by Python csv)",
        "DNS rebinding with a live changing resolver (the static resolution guard is tested)",
    ],
}


# ---------------------------------------------------------------------------
# independent hashing / small utilities
# ---------------------------------------------------------------------------
def sha256_hex(value: str | bytes) -> str:
    data = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(data).hexdigest()


def canonical_hash(value) -> str:
    """sha256 of canonical JSON (sorted keys, no ASCII escaping)."""
    canonical = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def response_fact(response) -> dict:
    """The response facts an attack record keeps (never the raw body only)."""
    fact: dict = {
        "status": int(response.status),
        "body_bytes": len(response.raw),
        "body_sha256": sha256_hex(response.raw),
    }
    body = None
    try:
        body = response.json
    except (ValueError, json.JSONDecodeError):
        body = None
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        error = body["error"]
        fact["code"] = error.get("code")
        fact["request_id"] = error.get("request_id")
        fact["message"] = error.get("message")
        if isinstance(error.get("details"), dict):
            fact["details"] = redact_value(error["details"])
    elif isinstance(body, dict):
        fact["body_keys"] = sorted(body)
    return fact


def csv_rows(text: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(text, newline="")))


def csv_risky_cells(rows: list[list[str]]) -> list[dict]:
    """Cells whose first byte is a spreadsheet formula/metacharacter prefix."""
    risky: list[dict] = []
    for row_index, row in enumerate(rows):
        for column_index, value in enumerate(row):
            if value[:1] in ("=", "+", "-", "@", "\t", "\r"):
                risky.append(
                    {
                        "row": row_index,
                        "column": column_index,
                        "first_byte": repr(value[:1]),
                        "value": value,
                    }
                )
    return risky


# ---------------------------------------------------------------------------
# the attack record
# ---------------------------------------------------------------------------
def _short(value, limit: int = 600) -> str:
    redacted = redact_value(value)
    text = (
        redacted
        if isinstance(redacted, str)
        else json.dumps(redacted, ensure_ascii=False, default=str)
    )
    return text if len(text) <= limit else text[: limit - 3] + "..."


@dataclass
class Attack:
    """One attempted attack and the evidence for its (non-)success."""

    attack_id: str
    title: str
    family: str
    target: str
    expected: list[str] = field(default_factory=list)
    expected_layer: str = "app_validation"
    status: str = "not_run"
    input: dict = field(default_factory=dict)
    input_sha256: str = ""
    observed: dict = field(default_factory=dict)
    blocked_at: str | None = None
    blocking_detail: str = ""
    response: dict = field(default_factory=dict)
    independent_truth: dict = field(default_factory=dict)
    assertions: list[dict] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
    not_run: list[str] = field(default_factory=list)
    failure: str | None = None
    started_at: float = field(default_factory=time.time)
    duration_s: float = 0.0

    # -- recording ----------------------------------------------------------
    def check(self, name: str, condition, detail=None) -> None:
        ok = bool(condition)
        self.assertions.append(
            {"name": name, "ok": ok, "detail": None if detail is None else _short(detail)}
        )
        assert ok, f"{self.attack_id}: {name} failed: {detail}"

    def observe(self, key: str, value) -> None:
        self.observed[key] = redact_value(value)

    def truth(self, key: str, value) -> None:
        self.independent_truth[key] = redact_value(value)

    def http(self, label: str, response) -> dict:
        """Record one response under ``observed[label]`` and return its facts."""
        fact = response_fact(response)
        self.observed[label] = fact
        return fact

    def with_input(self, payload) -> "Attack":
        self.input = redact_value(payload)
        self.input_sha256 = canonical_hash(
            payload if isinstance(payload, (dict, list)) else {"value": payload}
        )
        return self

    def command(self, text: str) -> None:
        self.commands.append(text)

    def block(self, layer: str, detail: str) -> None:
        """Record the real layer that refused the attack (validated vocabulary)."""
        assert layer in LAYERS, f"{self.attack_id}: unknown blocking layer {layer!r}"
        self.blocked_at = layer
        self.blocking_detail = detail
        if self.status == "not_run":
            self.status = "blocked"

    def mark(self, status: str) -> None:
        assert status in {"blocked", "partial", "finding", "observed", "not_run", "failed"}
        self.status = status

    def mark_not_run(self, reason: str) -> None:
        self.status = "not_run"
        self.not_run.append(reason)

    # -- evidence -----------------------------------------------------------
    def record(self) -> dict:
        return redact_value(
            {
                "attack_id": self.attack_id,
                "title": self.title,
                "family": self.family,
                "target": self.target,
                "status": self.status,
                "expected_layer": self.expected_layer,
                "blocked_at": self.blocked_at,
                "blocking_detail": self.blocking_detail,
                "input": self.input,
                "input_sha256": self.input_sha256,
                "expected": self.expected,
                "observed": self.observed,
                "response": self.response,
                "independent_truth": self.independent_truth,
                "assertions": self.assertions,
                "assertion_count": len(self.assertions),
                "commands": self.commands,
                "not_run": self.not_run,
                "duration_s": self.duration_s,
                "failure": self.failure,
            }
        )

    def persist(self) -> None:
        self.duration_s = round(time.time() - self.started_at, 2)
        _ATTACKS[self.attack_id] = self
        if os.environ.get(WRITE_EVIDENCE_ENV) != "1":
            return
        existing: dict[str, dict] = {}
        if EVIDENCE_JSON.exists():
            try:
                payload = json.loads(EVIDENCE_JSON.read_text(encoding="utf-8"))
                for item in payload.get("attacks", []):
                    existing[item["attack_id"]] = item
            except (json.JSONDecodeError, KeyError):  # pragma: no cover - corrupt file
                existing = {}
        existing[self.attack_id] = self.record()
        ordered = [existing[key] for key in sorted(existing)]
        family_counts: dict[str, int] = {}
        layer_counts: dict[str, int] = {}
        status_counts: dict[str, int] = {}
        for item in ordered:
            family_counts[item["family"]] = family_counts.get(item["family"], 0) + 1
            layer = item.get("blocked_at") or "none"
            layer_counts[layer] = layer_counts.get(layer, 0) + 1
            status_counts[item["status"]] = status_counts.get(item["status"], 0) + 1
        document = {
            "ticket": "T110",
            "issue": 111,
            "title": "Platform identity, file, egress and injection security regression",
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "product_commit": _git_commit(),
            "product_commit_note": (
                "HEAD checked out when this evidence run started; the ticket's test "
                "sources are committed on the same branch"
            ),
            "environment": ENVIRONMENT,
            "summary": {
                "attack_count": len(ordered),
                "by_family": family_counts,
                "by_blocking_layer": layer_counts,
                "by_status": status_counts,
            },
            "attacks": ordered,
        }
        text = json.dumps(document, indent=2, ensure_ascii=False) + "\n"
        leaks = find_violations(text)
        if leaks:  # pragma: no cover - a credential leak must fail the evidence run
            raise AssertionError(f"credential-shaped values in evidence: {leaks}")
        EVIDENCE_JSON.parent.mkdir(parents=True, exist_ok=True)
        EVIDENCE_JSON.write_text(text, encoding="utf-8")

    def __enter__(self) -> "Attack":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc is not None and self.status in {"not_run", "blocked", "observed", "partial"}:
            self.status = "failed"
            if self.failure is None:
                self.failure = f"{exc_type.__name__}: {exc}"
            if os.environ.get("F13B_VERBOSE_FAILURES") == "1":
                self.observed["traceback"] = traceback.format_exc(limit=8)
        self.persist()
        return False


_ATTACKS: dict[str, Attack] = {}


def start(
    attack_id: str,
    title: str,
    *,
    family: str,
    target: str,
    expected: list[str] | None = None,
    expected_layer: str = "app_validation",
    input: dict | None = None,
) -> Attack:
    """Create and pre-register one attack (its expectations come first)."""
    assert family in FAMILIES, family
    assert expected_layer in LAYERS, expected_layer
    attack = Attack(
        attack_id=attack_id,
        title=title,
        family=family,
        target=target,
        expected=list(expected or []),
        expected_layer=expected_layer,
        not_run=list(ENVIRONMENT["not_run"]),
    )
    if input is not None:
        attack.with_input(input)
    _ATTACKS[attack_id] = attack
    return attack


def records() -> dict[str, dict]:
    return {key: value.record() for key, value in _ATTACKS.items()}


def layer_counts() -> dict[str, int]:
    counts: dict[str, int] = {}
    for attack in _ATTACKS.values():
        key = attack.blocked_at or "none"
        counts[key] = counts.get(key, 0) + 1
    return counts


def _git_commit() -> str | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(PRODUCT_ROOT), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=30,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):  # pragma: no cover
        return None
    if completed.returncode != 0:
        return None
    return (completed.stdout or "").strip() or None


# ---------------------------------------------------------------------------
# Node harness: the committed page bytes under a recording DOM shim
# ---------------------------------------------------------------------------
def run_node(
    node_binary: str,
    tmp_path: Path,
    script: str,
    *,
    name: str = "t110-harness.js",
    timeout: int = 120,
) -> dict:
    """Execute one harness file and return its final JSON line.

    The harness prints exactly one JSON object on stdout; a non-zero exit code
    and/or a missing object is a hard failure, never a skip.
    """
    path = tmp_path / name
    path.write_text(script, encoding="utf-8", newline="\n")
    completed = subprocess.run(
        [node_binary, str(path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        stdin=subprocess.DEVNULL,
    )
    if completed.returncode != 0:
        raise AssertionError(
            f"node harness {name} failed (exit {completed.returncode}):\n"
            f"--- stdout ---\n{completed.stdout}\n--- stderr ---\n{completed.stderr}"
        )
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    assert lines, f"node harness {name} printed nothing:\n{completed.stderr}"
    try:
        payload = json.loads(lines[-1])
    except json.JSONDecodeError as exc:  # pragma: no cover - a broken harness
        raise AssertionError(
            f"node harness {name} did not end with JSON ({exc}):\n{lines[-1]}"
        ) from exc
    assert isinstance(payload, dict), payload
    payload["_stdout"] = completed.stdout
    return payload


XSS_HARNESS = r"""
'use strict';

/*
 * T110 XSS harness. NOT a browser and NOT Playwright.
 *
 * It loads the *committed* apps/web/src/assets/memories.js bytes into a Node vm
 * and calls the real renderMemory/renderCandidate with the JSON the real API
 * returned. The DOM is a small recording shim: it records every element tag
 * created, every write to innerHTML/outerHTML, every setAttribute, every
 * addEventListener and every src/href/on* property assignment. A positive control
 * at the end assigns innerHTML on a shim element, proving the shim notices an
 * HTML parsing sink when one is used.
 */

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const webDir = __WEB_DIR__;
const memories = __MEMORIES__;
const candidates = __CANDIDATES__;

const records = {
  created_tags: [],
  inner_html: [],
  outer_html: [],
  attributes: [],
  listeners: [],
  handler_properties: [],
  url_properties: [],
  navigations: [],
  fetch_calls: [],
  document_listeners: [],
  value_assignments: [],
  text_content_calls: 0,
  script_executions: 0,
  html_parse_calls: [],
};

function noteValue(target, name, value) {
  records.value_assignments.push({
    tag: target.tagName,
    name: name,
    bytes: String(value).length,
    starts_with: String(value).slice(0, 24),
  });
}

class El {
  constructor(tag, id) {
    this.tagName = String(tag || 'div').toUpperCase();
    this.id = id || '';
    this.children = [];
    this._listeners = {};
    this.className = '';
    this.textContent = '';
    this.value = '';
    this.rows = 0;
    this.type = '';
    this.hidden = false;
    this.dataset = {};
    this.attrs = {};
    this.style = {};
    records.created_tags.push(this.tagName);
  }
  set innerHTML(value) {
    records.inner_html.push({ tag: this.tagName, value: String(value) });
    records.html_parse_calls.push('innerHTML');
  }
  get innerHTML() {
    return '';
  }
  set outerHTML(value) {
    records.outer_html.push({ tag: this.tagName, value: String(value) });
    records.html_parse_calls.push('outerHTML');
  }
  get outerHTML() {
    return '';
  }
  set src(value) {
    records.url_properties.push({ tag: this.tagName, name: 'src', value: String(value) });
  }
  set href(value) {
    records.url_properties.push({ tag: this.tagName, name: 'href', value: String(value) });
  }
  set onerror(value) {
    records.handler_properties.push({ tag: this.tagName, name: 'onerror' });
  }
  set onload(value) {
    records.handler_properties.push({ tag: this.tagName, name: 'onload' });
  }
  set onclick(value) {
    records.handler_properties.push({ tag: this.tagName, name: 'onclick' });
  }
  set onmouseover(value) {
    records.handler_properties.push({ tag: this.tagName, name: 'onmouseover' });
  }
  setAttribute(name, value) {
    const key = String(name);
    records.attributes.push({ tag: this.tagName, name: key, value: String(value) });
    if (key.toLowerCase().startsWith('on')) {
      records.handler_properties.push({ tag: this.tagName, name: key });
    }
    if ((key === 'src' || key === 'href') && String(value).toLowerCase().startsWith('javascript:')) {
      records.url_properties.push({ tag: this.tagName, name: key, value: String(value) });
    }
    this.attrs[key] = String(value);
  }
  removeAttribute(name) {
    delete this.attrs[String(name)];
  }
  appendChild(child) {
    this.children.push(child);
    return child;
  }
  append(...nodes) {
    for (const node of nodes) {
      this.children.push(typeof node === 'string' ? { textContent: node, children: [] } : node);
    }
  }
  replaceChildren(...nodes) {
    this.children = nodes;
  }
  insertAdjacentHTML(position, html) {
    records.html_parse_calls.push('insertAdjacentHTML');
    records.inner_html.push({ tag: this.tagName, position: String(position), value: String(html) });
  }
  addEventListener(type, fn) {
    records.listeners.push({ tag: this.tagName, type: String(type) });
    (this._listeners[String(type)] = this._listeners[String(type)] || []).push(fn);
  }
  click() {
    for (const fn of this._listeners.click || []) fn({ preventDefault() {} });
  }
  querySelector() {
    return null;
  }
}

const byId = new Map();
const document = {
  getElementById(id) {
    if (!byId.has(id)) byId.set(id, new El('div', id));
    return byId.get(id);
  },
  createElement(tag) {
    return new El(tag);
  },
  createTextNode(text) {
    return { textContent: String(text), children: [] };
  },
  querySelector() {
    return null;
  },
  querySelectorAll() {
    return [];
  },
  addEventListener(type, fn) {
    records.document_listeners.push(String(type));
  },
  write() {
    records.html_parse_calls.push('document.write');
  },
};

const window = {
  location: {
    assign(url) {
      records.navigations.push(String(url));
    },
  },
  confirm() {
    return false;
  },
  open(url) {
    records.navigations.push(String(url));
  },
};

function recordedFetch(url, options) {
  records.fetch_calls.push(String(url));
  return Promise.reject(new Error('unexpected fetch ' + url));
}

const context = {
  console: console,
  document: document,
  window: window,
  fetch: recordedFetch,
  TextDecoder: TextDecoder,
  TextEncoder: TextEncoder,
  setTimeout(fn) {
    return 0;
  },
  clearTimeout() {},
  requireLogin: async () => ({ roles: ['user'] }),
  csrf: async () => 'csrf',
  api: async (pathname) => {
    throw new Error('unexpected api ' + pathname);
  },
  apiFull: async (pathname) => {
    throw new Error('unexpected apiFull ' + pathname);
  },
};

vm.createContext(context);
vm.runInContext(
  fs.readFileSync(path.join(webDir, 'assets', 'memories.js'), 'utf8'),
  context,
  { filename: 'memories.js' }
);

function textOf(node) {
  if (node === null || node === undefined) return '';
  if (typeof node === 'string') return node;
  let text = String(node.textContent === undefined || node.textContent === null ? '' : node.textContent);
  for (const child of node.children || []) text += textOf(child);
  return text;
}

function tagsOf(node, out) {
  if (node === null || node === undefined || typeof node === 'string') return out;
  if (node.tagName) out.push(node.tagName);
  for (const child of node.children || []) tagsOf(child, out);
  return out;
}

function textareaValues(node, out) {
  if (node === null || node === undefined || typeof node === 'string') return out;
  if (node.tagName === 'TEXTAREA') {
    out.push(String(node.value === undefined ? '' : node.value));
  }
  for (const child of node.children || []) textareaValues(child, out);
  return out;
}

const renderings = [];

for (const memory of memories) {
  const item = document.createElement('li');
  context.renderMemory(item, memory);
  renderings.push({
    kind: 'memory',
    id: memory.id,
    text: textOf(item),
    textarea_values: textareaValues(item, []),
    tags: tagsOf(item, []),
  });
}

for (const candidate of candidates) {
  const item = document.createElement('li');
  context.renderCandidate(item, candidate);
  renderings.push({
    kind: 'candidate',
    id: candidate.id,
    text: textOf(item),
    textarea_values: textareaValues(item, []),
    tags: tagsOf(item, []),
  });
}

const real_inner_html = records.inner_html.slice();
const real_attributes = records.attributes.slice();
const real_handler_properties = records.handler_properties.slice();
const real_url_properties = records.url_properties.slice();

// Positive control: the shim must notice an HTML-parsing sink if the page ever
// used one. It does not prove a browser would execute the payload; it proves the
// harness is sensitive to the sink the page must avoid.
const control = document.createElement('div');
control.innerHTML = '<img src=x onerror="globalThis.__t110_xss__=1">';

const out = {
  renderings: renderings,
  real_inner_html: real_inner_html,
  real_attributes: real_attributes,
  real_handler_properties: real_handler_properties,
  real_url_properties: real_url_properties,
  inner_html_count: real_inner_html.length,
  outer_html_count: records.outer_html.length,
  attribute_count: real_attributes.length,
  handler_property_count: real_handler_properties.length,
  url_property_count: real_url_properties.length,
  listener_types: records.listeners.map((item) => item.type),
  listener_count: records.listeners.length,
  document_listeners: records.document_listeners,
  navigations: records.navigations,
  fetch_calls: records.fetch_calls,
  value_assignment_count: records.value_assignments.length,
  value_assignment_names: records.value_assignments.map((item) => item.name),
  created_tags: records.created_tags,
  script_executions: records.script_executions,
  executed_global: typeof globalThis.__t110_xss__,
  control_inner_html: records.inner_html.length - real_inner_html.length,
  control_tag: control.tagName,
};

process.stdout.write(JSON.stringify(out) + '\n');
"""


def xss_harness(
    web_dir: Path, *, memories: list[dict], candidates: list[dict]
) -> str:
    return (
        XSS_HARNESS.replace("__WEB_DIR__", json.dumps(str(web_dir)))
        .replace("__MEMORIES__", json.dumps(memories, ensure_ascii=False))
        .replace("__CANDIDATES__", json.dumps(candidates, ensure_ascii=False))
    )


# ---------------------------------------------------------------------------
# CSV export probe: the committed tool writer, invoked in a subprocess
# ---------------------------------------------------------------------------
CSV_EXPORT_PROBE = r"""
import json
import pathlib
import sys

sys.path.insert(0, sys.argv[3])
from platform_worker.nodes import sales_stats

out = pathlib.Path(sys.argv[1])
artifacts = out / "artifacts"
artifacts.mkdir(parents=True, exist_ok=True)
summary = json.loads(sys.argv[2])
sales_stats._write_outputs(str(out / "output.json"), str(artifacts), summary)
payload = (artifacts / "summary.csv").read_bytes()
print(json.dumps({
    "csv_text": payload.decode("utf-8"),
    "csv_sha256": __import__("hashlib").sha256(payload).hexdigest(),
    "csv_bytes": len(payload),
}))
"""


def csv_export_probe(python_binary: str, tmp_path: Path, summary: dict) -> dict:
    """Call the committed export writer on a summary dict; return the CSV bytes.

    The summary this helper passes is deliberately **not reachable** through the
    real tool input parser (which refuses such text earlier). It exists to test
    the export writer's own escaping, i.e. the defence-in-depth layer behind the
    parser. The report states that distinction.
    """
    script = tmp_path / "t110_csv_export_probe.py"
    script.write_text(CSV_EXPORT_PROBE, encoding="utf-8", newline="\n")
    completed = subprocess.run(
        [
            python_binary,
            str(script),
            str(tmp_path),
            json.dumps(summary, ensure_ascii=False),
            str(WORKER_ROOT),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        stdin=subprocess.DEVNULL,
    )
    if completed.returncode != 0:
        raise AssertionError(
            "csv export probe failed "
            f"(exit {completed.returncode}):\n{completed.stdout}\n{completed.stderr}"
        )
    return json.loads(completed.stdout.strip().splitlines()[-1])


# ---------------------------------------------------------------------------
# network truth: is a TCP port accepting connections from this host?
# ---------------------------------------------------------------------------
def port_open(host: str, port: int, *, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


__all__ = [
    "Attack",
    "ENVIRONMENT",
    "EVIDENCE_JSON",
    "FAMILIES",
    "LAYERS",
    "WRITE_EVIDENCE_ENV",
    "canonical_hash",
    "csv_export_probe",
    "csv_risky_cells",
    "csv_rows",
    "layer_counts",
    "port_open",
    "records",
    "response_fact",
    "run_node",
    "sha256_hex",
    "start",
    "xss_harness",
]
