# F13B/T103 — hard checker vs quality score on one recorded result

This probe directory holds the evidence behind ticket T103 (GitHub #104): *the
same recorded evaluation result is judged by a deterministic hard checker and by
a quality score, and a high quality score cannot override a hard failure at the
real publication gate.*

Everything here is test/probe-only. No product semantics, contract, spec or
migration changed.

## What is here

| file | purpose |
|---|---|
| `compare.py` | Independent hard-verdict recompute from `cases[].assertions` + `cases[].facts`, the deterministic local quality proxy `heuristic-local/1`, an outbound-connect counter and an optional observed publication-gate decision. |
| `adapter_probe.py` | Real install/import/invocation attempts for DeepEval, Ragas and Promptfoo in isolated venvs/npm prefixes, with telemetry/endpoint inspection and a loopback egress trap. |
| `results/adapter-matrix.json` | The produced adapter matrix (accepted / blocked / eliminated / not_run) with raw error tails. |
| `results/compare-real-recorded.json` | `compare.py` output for the real runner report (quality slot `not_installed`). |
| `results/compare-quality-override-refused.json` | `compare.py` output for the re-sealed failed report with `quality.score=1.0`, including the real gate refusal. |
| `evidence/t103-real-recorded-report.json` | The genuine sealed report the real runner produced (one hard pass, one hard fail). |
| `evidence/t103-quality-override-report.json` | The same case evidence re-sealed through the real completion guard with `quality.score=1.0` and status `failed`. |
| `evidence/t103-gate-refusal.json` | The raw `POST /api/v1/applications/{id}/submit` response (HTTP 422 `evaluation_not_passed`). |
| `evidence/t103-reverse-control-summary.json` | Passed five-category report with `quality.score=0.0`: HTTP 202, review created. |

## `compare.py`

```powershell
python probes/f13b_quality/compare.py --report evidence/t103-real-recorded-report.json --json
python probes/f13b_quality/compare.py --report evidence/t103-quality-override-report.json \
    --gate-result evidence/t103-gate-refusal.json --json
```

The hard verdict is **recomputed**: it never reads the declared `status`, the
`quality` slot or a database. Per-assertion truth is recomputed from
`expected`/`actual` (`run_state`, `effect_count`, `json_equals`,
`decimal_equals`, `citation_sources`, `failure_reason`), and a case passes only
when it declares at least one assertion, every assertion holds, and its run
reached a completed state. The declared status is compared afterwards
(`agrees_with_declared_status`) so a disagreement is visible instead of trusted.

`heuristic-local/1` is a deterministic local proxy score over the same evidence
(completeness, evaluated assertions, explainability). It is **not** a judge and
its score is never an input to the hard verdict. A real judge run is
`blocked`/`not_run` on this host: there is no approved judge endpoint or
credential (`ORCHARA_PROFILE=dev-stub` only).

## `adapter_probe.py`

```powershell
python probes/f13b_quality/adapter_probe.py \
    --work-dir E:\Orchara\.scratch\f13b\T103\adapter-work \
    --report E:\Orchara\.scratch\f13b\T103\t103-real-recorded-report.json \
    --out probes/f13b_quality/results/adapter-matrix.json --json
```

Each adapter is installed for real in an isolated venv/npm prefix. Every
invocation runs behind a loopback TCP trap (standard proxy variables point at it)
so `outbound_connections` is an independent observation, not a claim. A missing
package, unreachable registry, or absent judge endpoint is recorded with its raw
error tail as `blocked`; nothing is reported as eliminated unless the observed
evidence justifies it and nothing is ever reported as a judge run.

The evidence run for this ticket used network access for package installation
(PyPI/npm). That is package-registry traffic; it carries no evaluation content.
The observed evaluation-time egress for every adapter is in the matrix.

## Reproduce the real-stack evidence

```powershell
cd product
$env:ORCHARA_T103_EVIDENCE_DIR = 'E:\Orchara\.scratch\f13b\T103'
python -m pytest tests/f13b/test_t103_quality_gate.py -q
```

`ORCHARA_T103_EVIDENCE_DIR` only redirects where the sealed report bytes are
exported; without it the test writes them under pytest's `tmp_path`.
