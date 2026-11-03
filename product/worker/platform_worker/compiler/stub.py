"""Deterministic stub node adapters for the F04A linear graph.

These adapters are a test/development seam, **not** a production runtime. They
prove that the declared bindings carry values end to end:

* ``start`` echoes the run input object (the application input);
* ``python`` and ``agent`` build their output from the declared output schema:
  a same-named input passes through, otherwise a single input seeds a string
  output, otherwise a deterministic typed default is used;
* ``end`` returns the mapped output object unchanged.

Every value is derived from the definition, the node id and the resolved inputs,
so repeated execution of the same run input is deterministic. A real adapter
(T042/T043) replaces one of these through the same ``NodeAdapter`` interface.
"""

from __future__ import annotations

from typing import Any

from .graph import NodeAdapter, NodeContext, NodeOutcome


def _typed_default(declared: Any, node_id: str, name: str) -> Any:
    if declared == "string":
        return f"stub:{node_id}:{name}"
    if declared == "integer":
        return 0
    if declared == "number":
        return 0
    if declared == "boolean":
        return False
    if declared == "array":
        return []
    if declared == "object":
        return {}
    return None


def _stub_output(context: NodeContext) -> dict[str, Any]:
    schema = context.output_schema
    properties = schema.get("properties")
    properties = properties if isinstance(properties, dict) else {}
    string_inputs = [value for value in context.inputs.values() if isinstance(value, str)]
    output: dict[str, Any] = {}
    for name in sorted(properties):
        if name in context.inputs:
            output[name] = context.inputs[name]
            continue
        declared = properties[name].get("type") if isinstance(properties[name], dict) else None
        if declared == "string" and len(context.inputs) == 1 and string_inputs:
            output[name] = f"{context.node_id}: {string_inputs[0]}"
            continue
        output[name] = _typed_default(declared, context.node_id, name)
    return output


class StubStartAdapter:
    node_type = "start"

    def invoke(self, node: dict[str, Any], context: NodeContext) -> NodeOutcome:
        return NodeOutcome.succeeded(dict(context.inputs))


class StubPythonAdapter:
    node_type = "python"

    def invoke(self, node: dict[str, Any], context: NodeContext) -> NodeOutcome:
        return NodeOutcome.succeeded(_stub_output(context))


class StubAgentAdapter:
    node_type = "agent"

    def invoke(self, node: dict[str, Any], context: NodeContext) -> NodeOutcome:
        return NodeOutcome.succeeded(_stub_output(context))


class StubEndAdapter:
    node_type = "end"

    def invoke(self, node: dict[str, Any], context: NodeContext) -> NodeOutcome:
        return NodeOutcome.succeeded(dict(context.inputs))


def stub_adapters() -> dict[str, NodeAdapter]:
    """One adapter per F04A node type, keyed so a node id can override a type."""
    return {
        "start": StubStartAdapter(),
        "python": StubPythonAdapter(),
        "agent": StubAgentAdapter(),
        "end": StubEndAdapter(),
    }
