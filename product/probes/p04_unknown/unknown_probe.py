#!/usr/bin/env python3
"""P04/T018: a write whose receipt was lost must still happen exactly once.

The scenario is the one that corrupts real systems: the external service applied
the write and the response never arrived. The caller therefore cannot tell
"applied" from "not applied", and blindly retrying creates a duplicate.

This probe models it honestly:

* a stub service records every applied write in an **independent ledger** and
  then closes the connection without responding (lost receipt);
* the client marks the operation ``unknown`` — neither success nor failure;
* reconciliation reads the ledger out-of-band: if the write is there, the
  operation is completed **without resending**;
* if the ledger has no proof either way, the operation stays ``unknown`` and is
  never auto-resent, because absence of a record is not proof of non-delivery.

The ledger is the independent source of truth; the client's own state is not
trusted to decide whether a write happened.
"""

from __future__ import annotations

import argparse
import http.server
import json
import socket
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

OperationState = Literal["completed", "unknown", "failed"]


# --------------------------------------------------------------------------
# independent ledger
# --------------------------------------------------------------------------
class Ledger:
    """Out-of-band record of writes the external service actually applied."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def record(self, key: str, payload: dict[str, Any]) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps({"key": key, "payload": payload, "at": time.time()}) + "\n")

    def entries(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        return [
            json.loads(line)
            for line in self.path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def count_for(self, key: str) -> int:
        return sum(1 for entry in self.entries() if entry["key"] == key)


# --------------------------------------------------------------------------
# stub service that loses receipts
# --------------------------------------------------------------------------
class _DropResponseHandler(http.server.BaseHTTPRequestHandler):
    ledger: Ledger  # injected by the server factory

    def do_POST(self) -> None:  # noqa: N802 - stdlib naming
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        key = body.get("key", "")
        payload = body.get("payload", {})
        # The write is applied and recorded in the independent ledger...
        self.ledger.record(key, payload)
        # ...and then the receipt is lost on purpose.
        self.close_connection = True

    def log_message(self, *_args: Any) -> None:  # silence the default logger
        return


class LossyService:
    """Local HTTP service that applies writes but never answers."""

    def __init__(self, ledger: Ledger) -> None:
        handler = type("Handler", (_DropResponseHandler,), {"ledger": ledger})
        self._server = http.server.HTTPServer(("127.0.0.1", 0), handler)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self) -> "LossyService":
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/apply"


# --------------------------------------------------------------------------
# client side
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Operation:
    key: str
    payload: dict[str, Any]
    state: OperationState
    sends: int
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "state": self.state,
            "sends": self.sends,
            "detail": self.detail,
        }


def send_write(url: str, key: str, payload: dict[str, Any], timeout: float = 2.0) -> tuple[str, str]:
    """Send the write once. Returns (outcome, detail).

    ``outcome`` is ``delivered`` or ``unknown``. A timeout or a dropped
    connection is *unknown*: the write may well have been applied.
    """
    body = json.dumps({"key": key, "payload": payload}).encode("utf-8")
    request = urllib.request.Request(url, data=body, method="POST", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return "delivered", f"HTTP {response.status}"
    except (urllib.error.URLError, ConnectionResetError, socket.timeout, TimeoutError) as exc:
        return "unknown", f"{type(exc).__name__}: {exc}"


def reconcile(ledger: Ledger, key: str) -> tuple[bool, int]:
    """Ask the independent ledger whether the write landed."""
    count = ledger.count_for(key)
    return count > 0, count


def recover(ledger: Ledger, key: str, initial: Operation, *, proof_of_non_send: bool = False) -> Operation:
    """Decide what to do about an unknown operation.

    Only proof that the write was *not* sent allows a resend; otherwise the
    operation stays unknown so a human can reconcile out of band.
    """
    if initial.state != "unknown":
        return initial

    landed, count = reconcile(ledger, key)
    if landed:
        return Operation(
            key=key,
            payload=initial.payload,
            state="completed",
            sends=initial.sends,
            detail=f"reconciled from ledger ({count} record(s)); not resent",
        )

    if proof_of_non_send:
        return Operation(
            key=key,
            payload=initial.payload,
            state="failed",
            sends=initial.sends,
            detail="proved not sent; safe to resend on the next attempt",
        )

    return Operation(
        key=key,
        payload=initial.payload,
        state="unknown",
        sends=initial.sends,
        detail="no ledger record and no proof of non-send; must not auto-resend",
    )


def run_scenario(ledger_path: Path) -> dict[str, Any]:
    """Drive the losing-receipt scenario end to end."""
    ledger = Ledger(ledger_path)
    if ledger_path.exists():
        ledger_path.unlink()

    key = "op-exactly-once"
    payload = {"amount": "120.50"}

    with LossyService(ledger) as service:
        outcome, detail = send_write(service.url, key, payload)

    initial = Operation(
        key=key,
        payload=payload,
        state="completed" if outcome == "delivered" else "unknown",
        sends=1,
        detail=detail,
    )
    final = recover(ledger, key, initial)

    return {
        "probe": "p04-unknown-receipt",
        "task": "P04 / T018",
        "initial": initial.as_dict(),
        "final": final.as_dict(),
        "ledger_entries": ledger.count_for(key),
        "assertions": {
            "receipt_was_lost": initial.state == "unknown",
            "unknown_is_not_success_or_failure": initial.state == "unknown",
            "ledger_shows_exactly_one_delivery": ledger.count_for(key) == 1,
            "recovered_without_resending": final.state == "completed" and final.sends == 1,
            "never_resent": final.sends == 1,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="P04 lost-receipt probe")
    parser.add_argument("--ledger", type=Path, default=Path("reports/p04/unknown-ledger.jsonl"))
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    report = run_scenario(args.ledger)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if all(report["assertions"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
