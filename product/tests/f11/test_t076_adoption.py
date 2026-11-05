"""F11/T076 (#77): Mem0 deletion insufficiency blocks adoption.

The Q45 gate is the committed T066 ``do_not_adopt`` decision plus the executable
rule in ``experiments/mem0/mem0exp``. This suite extends that same rule (it does
not invent a parallel gate): a component that cannot prove its own stores purgeable
is refused by the adoption decision *and* by the record validator, even when one
post-delete enumeration happens to find no body copy.

No fresh Mem0 run is executed here: the isolated venv is not a product dependency
and is not guaranteed on this host, so the *committed* T066 evidence is re-checked
instead. A new Mem0 deletion attempt is ``not_run`` (report §4).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PRODUCT_ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT = PRODUCT_ROOT / "experiments" / "mem0"
if str(EXPERIMENT) not in sys.path:
    sys.path.insert(0, str(EXPERIMENT))

from mem0exp import decision as decision_module  # noqa: E402
from mem0exp import records  # noqa: E402

COMMITTED_EVIDENCE = EXPERIMENT / "evidence" / "t066-both.json"


def _pg() -> dict:
    return {"status": "run", "hit_at_1": 0.5, "hit_at_3": 0.75, "p95_ms": 10.0, "cleanup": {}}


def _mem0(*, cleanup: dict) -> dict:
    """A Mem0 side with a real, measurable benefit and the given cleanup fact."""
    return {
        "status": "run",
        "evidence_level": "real",
        "recall": {"hit_at_1": 1.0, "hit_at_3": 1.0},
        "latency": {"p95_ms": 2.0},
        "cleanup": cleanup,
    }


def _measured(cleanup: dict, *, evidence_level: str = "real") -> dict:
    """A complete system record: the validator requires the full measurement shape."""
    return records.system_run(
        evidence_level=evidence_level,
        recall={
            "hit_at_1": 1.0,
            "hit_at_3": 1.0,
            "positive_queries": 8,
            "negative_queries": 4,
            "forbidden_hits": 0,
        },
        latency={"p50_ms": 1.0, "p95_ms": 2.0, "runs": 168},
        cleanup=cleanup,
        commands=["python experiments/mem0/run_experiment.py both"],
    )


def test_a_component_that_cannot_show_a_clean_purge_is_refused_even_without_residual() -> None:
    # A negative enumeration alone used to be enough to pass the gate. It is not: an
    # opaque store the platform cannot purge is a deletion hole even when the one
    # enumeration found nothing.
    verdict = decision_module.decide(
        _pg(),
        _mem0(
            cleanup={
                "residual_body_copies": [],
                "unverifiable_copies": ["vendor:server-side-log"],
            }
        ),
        f11_gate_passed=True,
    )
    assert verdict["decision"] == "do_not_adopt", verdict
    assert verdict["governance"]["deletion_sufficient"] is False, verdict["governance"]
    assert verdict["gates"]["f11_component_deletion"]["passed"] is False, verdict["gates"]
    assert verdict["gates"]["f11_component_deletion"]["evidence"] == ["vendor:server-side-log"]
    assert any("purgeable" in reason for reason in verdict["reasons"]), verdict["reasons"]

    # The same component with a clean, provable cleanup *is* adoptable: the gate is
    # about the deletion proof, not about refusing vendors categorically.
    adoptable = decision_module.decide(
        _pg(),
        _mem0(cleanup={"residual_body_copies": [], "unverifiable_copies": []}),
        f11_gate_passed=True,
    )
    assert adoptable["decision"] == "adopt", adoptable
    assert adoptable["gates"]["f11_component_deletion"]["passed"] is True
    assert adoptable["governance"]["deletion_sufficient"] is True

    # An explicit declaration is authoritative too.
    declared = decision_module.decide(
        _pg(),
        _mem0(cleanup={"residual_body_copies": [], "deletion_sufficient": False}),
        f11_gate_passed=True,
    )
    assert declared["decision"] == "do_not_adopt", declared
    assert declared["gates"]["f11_component_deletion"]["evidence"] == ["deletion_sufficient:false"]


def test_the_record_validator_refuses_an_adopt_that_cannot_prove_deletion() -> None:
    record = records.sample_record(
        pg=_measured({"residual_body_copies": []}),
        mem0=_measured(
            # One enumeration found no copy, but the component cannot prove its
            # server-side log is purgeable.
            {"residual_body_copies": [], "unverifiable_copies": ["log:vendor-side"]}
        ),
        decision={
            "decision": "adopt",
            "reasons": ["looks good"],
            "remaining_gates": [],
            "gates": {
                "f11": {"passed": True, "reason": "delivered"},
                "f11_component_deletion": {"passed": True, "reason": "claimed"},
            },
        },
    )
    errors = records.validate(record)
    assert any("cannot prove its body copies purgeable" in error for error in errors), errors
    # Claiming the component gate passed while listing an unpurged copy is itself a
    # contradiction the validator refuses.
    assert any("cannot pass while" in error for error in errors), errors

    # The honest verdict for the same facts is do_not_adopt, and then the record is
    # valid even with the unpurged copy listed.
    verdict = decision_module.decide(
        _pg(),
        _mem0(cleanup={"residual_body_copies": [], "unverifiable_copies": ["log:vendor-side"]}),
        f11_gate_passed=True,
    )
    assert verdict["decision"] == "do_not_adopt", verdict
    record["decision"] = verdict
    assert records.validate(record) == [], records.validate(record)

    # The stock adopt record (clean, real, gate passed) still validates: the
    # extension refuses only a record that cannot prove deletion.
    clean = records.sample_record(
        pg=_measured({"residual_body_copies": []}),
        mem0=_measured({"residual_body_copies": []}),
        decision={
            "decision": "adopt",
            "reasons": ["benefit + clean"],
            "remaining_gates": [],
            "gates": {
                "f11": {"passed": True, "reason": "delivered"},
                "f11_component_deletion": {"passed": True, "reason": "proved"},
            },
        },
    )
    assert records.validate(clean) == [], records.validate(clean)

    # An adopt that simply omits the component gate is refused as unproven.
    missing_gate = json.loads(json.dumps(clean))
    del missing_gate["decision"]["gates"]["f11_component_deletion"]
    assert any(
        "component-deletion gate" in error for error in records.validate(missing_gate)
    ), records.validate(missing_gate)


def test_the_committed_t066_evidence_still_does_not_adopt() -> None:
    assert COMMITTED_EVIDENCE.exists(), "the executed T066 result is part of the deliverable"
    record = json.loads(COMMITTED_EVIDENCE.read_text(encoding="utf-8"))
    assert records.validate(record) == [], records.validate(record)
    assert record["decision"]["decision"] == "do_not_adopt", record["decision"]
    assert record["decision"]["gates"]["f11"]["passed"] is False
    residual = record["systems"]["mem0"]["cleanup"]["residual_body_copies"]
    assert residual, residual
    # The extension's own predicate refuses the committed residual set too.
    proof = decision_module.deletion_proof(record["systems"]["mem0"]["cleanup"])
    assert proof["sufficient"] is False, proof
    assert proof["residual_body_copies"] == residual
