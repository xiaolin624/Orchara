"""The T066 verdict rule: keep PostgreSQL unless the isolated evidence says more.

The default is deliberately conservative, because the ticket says a first look is
not adoption:

* Mem0 did not run                -> ``insufficient_evidence`` (never adoption);
* its deletion still holds copies -> ``do_not_adopt`` (the governance gate fails,
  and unclean deletion alone disqualifies formal adoption);
* it cannot prove its own stores purgeable (``cleanup.unverifiable_copies`` or
  ``cleanup.deletion_sufficient: false``)
                                  -> ``do_not_adopt`` (F11.05/T076: "we saw no copy"
  is not "the copy is gone"; a component with no deletion path is refused even when
  the post-delete enumeration happens to be empty);
* no recall or latency benefit    -> ``do_not_adopt`` (keep PostgreSQL);
* a benefit seen only in stub mode-> ``insufficient_evidence`` (a no-model,
  ``infer=False``, local-embedder run cannot justify a vendor dependency);
* benefit + clean copies + real evidence, but F11 not passed
                                  -> ``insufficient_evidence`` (formal adoption
  still waits for the F11 deletion-governance gate);
* all of the above and F11 passed -> ``adopt``.
"""

from __future__ import annotations

F11_GATE = (
    "F11 deletion governance: physical copy purge, retention/backup policy and "
    "model-data egress approval for a vendor memory store"
)

F11_COMPONENT_DELETION_GATE = (
    "F11 deletion governance, component half: every body copy the component keeps in its "
    "own stores (history, docstore, vector store, cache, logs) must be provably purgeable "
    "by the platform, not merely absent from one post-delete enumeration"
)

LATENCY_GAIN_RATIO = 0.8  # a 20%+ p95 improvement is the minimum measurable win


def deletion_proof(cleanup: dict) -> dict:
    """The F11.05/T076 deletion test for one component.

    Two independent facts make a component deletion-insufficient:

    ``residual_body_copies``
        what the post-delete enumeration actually found still present;
    ``unverifiable_copies``
        what the component cannot prove it purged -- an opaque store, a log or cache
        with no deletion path, or an explicit ``deletion_sufficient: false``.

    Either one fails the component gate. A negative enumeration alone is not proof:
    a store nobody can read is still a store the platform cannot purge after a user
    deletes their data.
    """
    if not isinstance(cleanup, dict):
        cleanup = {}
    residual = [str(item) for item in (cleanup.get("residual_body_copies") or [])]
    unverifiable = [str(item) for item in (cleanup.get("unverifiable_copies") or [])]
    if cleanup.get("deletion_sufficient") is False and not unverifiable:
        unverifiable = ["deletion_sufficient:false"]
    evidence = residual + unverifiable
    return {
        "sufficient": not evidence,
        "residual_body_copies": residual,
        "unverifiable_body_copies": unverifiable,
        "evidence": evidence,
    }


def _metric(system: dict, key: str, default: float = 0.0) -> float:
    """Read one metric from a reduced dict or from a full system record.

    The decision is tested against small dicts and used by the runner against the
    full record, where recall lives under ``recall`` and latency under ``latency``.
    Supporting both shapes here keeps the rule testable without duplicating it.
    """
    sources: list[dict] = [system]
    if key in ("hit_at_1", "hit_at_3", "hit_at_k"):
        sources.append(system.get("recall") or {})
    if key == "p95_ms":
        sources.append(system.get("latency") or {})
    for source in sources:
        if not isinstance(source, dict):
            continue
        value = source.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return default


def decide(
    pg: dict | None,
    mem0: dict | None,
    *,
    f11_gate_passed: bool,
    f11_reason: str = F11_GATE,
) -> dict:
    """Return the decision record for one comparison."""
    gates = {
        "f11": {"passed": bool(f11_gate_passed), "reason": f11_reason},
    }
    remaining_gates = ["F11"] if not f11_gate_passed else []

    if not isinstance(pg, dict) or pg.get("status") != "run":
        return {
            "decision": "insufficient_evidence",
            "reasons": [
                "the PostgreSQL baseline did not run in this invocation; there is no comparison",
            ],
            "benefit": {},
            "governance": {},
            "gates": gates,
            "remaining_gates": remaining_gates,
        }

    if not isinstance(mem0, dict) or mem0.get("status") != "run":
        reason = ""
        if isinstance(mem0, dict):
            reason = str(mem0.get("reason") or "").strip()
        detail = f" ({reason})" if reason else ""
        return {
            "decision": "insufficient_evidence",
            "reasons": [
                "Mem0 did not run" + detail
                + "; an unexecuted comparison is not adoption evidence, so PostgreSQL stays",
            ],
            "benefit": {},
            "governance": {},
            "gates": gates,
            "remaining_gates": remaining_gates,
        }

    pg_hit1 = _metric(pg, "hit_at_1")
    pg_hit3 = _metric(pg, "hit_at_3")
    mem0_hit1 = _metric(mem0, "hit_at_1")
    mem0_hit3 = _metric(mem0, "hit_at_3")
    pg_p95 = _metric(pg, "p95_ms")
    mem0_p95 = _metric(mem0, "p95_ms")

    recall_gain = mem0_hit1 > pg_hit1 or mem0_hit3 > pg_hit3
    p95_ratio = (mem0_p95 / pg_p95) if pg_p95 > 0 else None
    latency_gain = p95_ratio is not None and p95_ratio <= LATENCY_GAIN_RATIO
    benefit = {
        "recall_gain": recall_gain,
        "latency_gain": latency_gain,
        "pg_hit_at_1": pg_hit1,
        "pg_hit_at_3": pg_hit3,
        "mem0_hit_at_1": mem0_hit1,
        "mem0_hit_at_3": mem0_hit3,
        "pg_p95_ms": pg_p95,
        "mem0_p95_ms": mem0_p95,
        "p95_ratio": p95_ratio,
        "latency_gain_threshold_ratio": LATENCY_GAIN_RATIO,
    }

    residual = list((mem0.get("cleanup") or {}).get("residual_body_copies") or [])
    proof = deletion_proof(mem0.get("cleanup"))
    gates["f11_component_deletion"] = {
        "passed": proof["sufficient"],
        "reason": F11_COMPONENT_DELETION_GATE,
        "evidence": proof["evidence"],
    }
    governance = {
        "clean": not proof["evidence"],
        "residual_body_copies": residual,
        "unverifiable_body_copies": proof["unverifiable_body_copies"],
        "deletion_sufficient": proof["sufficient"],
        "evidence_level": mem0.get("evidence_level"),
    }
    if proof["evidence"]:
        if residual:
            reasons = [
                "deletion still leaves body copies in Mem0's own stores: "
                + ", ".join(str(item) for item in residual)
                + "; a memory store that cannot show a clean copy purge is disqualified"
            ]
        else:
            reasons = [
                "Mem0 cannot prove that its own stores are purgeable: "
                + ", ".join(str(item) for item in proof["unverifiable_body_copies"])
                + "; a store the platform cannot purge is a deletion hole even when one "
                "post-delete enumeration finds no copy, so the F11 component gate fails"
            ]
        if not recall_gain and not latency_gain:
            reasons.append(
                "Mem0 also shows no measurable benefit: recall is not better and p95 is "
                "not at least 20% lower than the deterministic PostgreSQL baseline"
            )
        return {
            "decision": "do_not_adopt",
            "reasons": reasons,
            "benefit": benefit,
            "governance": governance,
            "gates": gates,
            "remaining_gates": remaining_gates,
        }

    if not recall_gain and not latency_gain:
        return {
            "decision": "do_not_adopt",
            "reasons": [
                "no measurable benefit: Mem0's recall is not better than the shared "
                "deterministic PostgreSQL baseline and its p95 is not at least 20% lower; "
                "the default is to keep PostgreSQL and avoid a second memory store",
            ],
            "benefit": benefit,
            "governance": governance,
            "gates": gates,
            "remaining_gates": remaining_gates,
        }

    if mem0.get("evidence_level") != "real":
        return {
            "decision": "insufficient_evidence",
            "reasons": [
                "the only measured benefit comes from a stub Mem0 run (infer=False writes "
                "with a deterministic local embedder; no model provider), which cannot "
                "justify formal adoption; F11 is also still open",
            ],
            "benefit": benefit,
            "governance": governance,
            "gates": gates,
            "remaining_gates": remaining_gates,
        }

    if not f11_gate_passed:
        return {
            "decision": "insufficient_evidence",
            "reasons": [
                "Mem0 shows a measured benefit with clean copy cleanup, but the F11 "
                "deletion-governance gate has not passed; formal adoption is blocked on it",
            ],
            "benefit": benefit,
            "governance": governance,
            "gates": gates,
            "remaining_gates": remaining_gates,
        }

    return {
        "decision": "adopt",
        "reasons": [
            "Mem0 shows a measurable recall/latency benefit, its deletion leaves no body "
            "copies in the enumerated stores, and the F11 deletion-governance gate passed",
        ],
        "benefit": benefit,
        "governance": governance,
        "gates": gates,
        "remaining_gates": [],
    }
