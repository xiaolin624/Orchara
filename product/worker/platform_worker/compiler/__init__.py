"""F04A Python compiler: defensive linear validator + LangGraph stub proof.

The Go compiler/validator remains the publish authority. This package is the
defence-in-depth copy the Python side will use when it starts executing nodes
(T042/T043); today it proves the F04A linear subset and its declared data flow
with stub adapters only. It imports nothing from ``probes/`` at runtime.
"""

from .graph import (
    FAILED,
    NEEDS_RECONCILIATION,
    SUCCEEDED,
    WAITING,
    LinearGraph,
    LinearGraphError,
    NodeAdapter,
    NodeContext,
    NodeOutcome,
    NodeRecord,
    RunResult,
    build_linear_graph,
    run_linear_graph,
)
from .stub import (
    StubAgentAdapter,
    StubEndAdapter,
    StubPythonAdapter,
    StubStartAdapter,
    stub_adapters,
)
from .validate import (
    F04A_ORDER,
    Problem,
    ValidationResult,
    problems_of,
    validate,
    validate_definition,
)

__all__ = [
    "FAILED",
    "F04A_ORDER",
    "LinearGraph",
    "LinearGraphError",
    "NEEDS_RECONCILIATION",
    "NodeAdapter",
    "NodeContext",
    "NodeOutcome",
    "NodeRecord",
    "Problem",
    "RunResult",
    "SUCCEEDED",
    "StubAgentAdapter",
    "StubEndAdapter",
    "StubPythonAdapter",
    "StubStartAdapter",
    "ValidationResult",
    "WAITING",
    "build_linear_graph",
    "problems_of",
    "run_linear_graph",
    "stub_adapters",
    "validate",
    "validate_definition",
]
