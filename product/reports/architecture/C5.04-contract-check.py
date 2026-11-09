"""C5.04 repository-wide AST check for the temporary support-contract entries.

The ticket deletes migration-only aliases, one-execution forwards and generic
observation re-exports. Before deleting anything this script proves, over the
whole ``tests`` tree, which of those entries still have external callers and
which are still defined. The report records three states:

* ``red``: callers > 0 and definitions > 0 (the starting point);
* ``migrated``: callers == 0, definitions > 0 (callers moved first);
* ``clean``: callers == 0 and definitions == 0 (the entries are gone).

Usage (from the ``product`` directory)::

    python reports/architecture/C5.04-contract-check.py --phase all

Exit code 1 means at least one violation for the requested phase.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

PRODUCT_ROOT = Path(__file__).resolve().parents[2]
TESTS_ROOT = PRODUCT_ROOT / "tests"

# The exact deletion contract. Names are module-level entries of the ticket module.
TARGETS: dict[str, tuple[str, ...]] = {
    "t024": ("T024Stack",),
    "t029": ("T029Stack",),
    "t031": ("T031Stack",),
    "t033": (
        "T033Stack",
        "FinalizeWorker",
        "rootfs_digest",
        "supervisor_exec_env",
        "run_supervisor_exec",
        "exec_report",
        "artifact_content",
        "output_artifact_id",
        "wait_run_state",
    ),
    "t037": ("T037Stack", "artifact_content", "rootfs_digest"),
    "t042": (
        "run_linear_once",
        "node_results",
        "node_result_rows",
        "node_events",
        "wait_node_results",
        "content_of",
    ),
    "t052": ("wait_run_state",),
    "t083": (
        "run_once",
        "node_results",
        "node_data_rows",
        "node_data_document",
        "node_states",
        "sse_node_states",
        "final_output_document",
    ),
    "t085": (
        "run_once",
        "node_results",
        "node_data_rows",
        "node_data_document",
        "node_states",
        "final_output_document",
    ),
    "t086": ("run_once",),
    "t087": (
        "output_artifact_id",
        "node_result_rows",
        "node_states",
        "node_events",
        "artifact_document",
    ),
    "t088": ("run_once", "node_states", "final_output_document"),
    "t089": ("node_results", "node_data_document", "node_states"),
    "t091": (
        "node_results",
        "node_data_rows",
        "node_data_document",
        "node_states",
        "sse_node_states",
        "final_output_document",
        "node_result_rows",
        "output_artifact_id",
    ),
}

SKIP_DIRS = {"__pycache__", ".pytest_cache"}


def python_files(root: Path):
    for path in sorted(root.rglob("*.py")):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        yield path


def display(path: Path) -> str:
    return path.relative_to(PRODUCT_ROOT).as_posix()


def callees(tree: ast.AST, module: str, names: tuple[str, ...]):
    """Attribute and from-import uses of one module's target names."""
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.value.id == module and node.attr in names:
                found.append((node.lineno, f"{module}.{node.attr}"))
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            if node.level == 0 and node.module == f"support.{module}":
                for alias in node.names:
                    if alias.name in names or alias.name == "*":
                        found.append(
                            (node.lineno, f"from support.{module} import {alias.name}")
                        )
    return found


def definitions(tree: ast.AST, names: tuple[str, ...]):
    """Module-level definitions/assignments of one module's target names."""
    found = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in names:
                    found.append((node.lineno, target.id, "assignment"))
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id in names:
                found.append((node.lineno, node.target.id, "assignment"))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name in names:
                found.append((node.lineno, node.name, "definition"))
    return found


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--phase",
        choices=("callers", "definitions", "all"),
        default="all",
        help="which contract dimension to check",
    )
    parser.add_argument("--json", action="store_true", help="emit one JSON object")
    args = parser.parse_args()

    trees: dict[str, ast.AST] = {}
    for module in TARGETS:
        path = TESTS_ROOT / "support" / f"{module}.py"
        trees[module] = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))

    caller_sites: list[dict] = []
    for path in python_files(TESTS_ROOT):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for module, names in TARGETS.items():
            # The module's own definition file may reference its entries; those are
            # definitions, not external callers.
            if path.name == f"{module}.py":
                continue
            for lineno, symbol in callees(tree, module, names):
                caller_sites.append(
                    {"file": display(path), "line": lineno, "symbol": symbol}
                )

    definition_sites: list[dict] = []
    for module, names in TARGETS.items():
        for lineno, name, kind in definitions(trees[module], names):
            definition_sites.append(
                {
                    "file": display(TESTS_ROOT / "support" / f"{module}.py"),
                    "line": lineno,
                    "symbol": f"{module}.{name}",
                    "kind": kind,
                }
            )

    result = {
        "phase": args.phase,
        "callers": caller_sites,
        "definitions": definition_sites,
        "caller_count": len(caller_sites),
        "definition_count": len(definition_sites),
    }
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f"phase={args.phase}")
        print(f"callers={len(caller_sites)}")
        for site in caller_sites:
            print(f"  CALLER {site['file']}:{site['line']} {site['symbol']}")
        print(f"definitions={len(definition_sites)}")
        for site in definition_sites:
            print(f"  DEFINITION {site['file']}:{site['line']} {site['symbol']}")

    failed = 0
    if args.phase in ("callers", "all"):
        failed += len(caller_sites)
    if args.phase in ("definitions", "all"):
        failed += len(definition_sites)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
