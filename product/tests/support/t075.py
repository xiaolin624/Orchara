"""F07/T075 real-stack helpers: fixed sub-application pins, I/O mapping, limits.

The stack is T025's: a throwaway PostgreSQL database migrated by the real
``cmd/admin`` chain, the compiled ``cmd/api`` behind real HTTP, the real
filesystem object store and the real compiler inputs. The child I/O schemas the
mapping is checked against come from the child version's frozen manifest.

**What is real and what is materialised.** ``seal_version`` drives the real
validator (``POST /api/v1/applications/{id}/validate``) over the real draft,
reads the exact canonical manifest bytes the compiler sealed into the object
store and stores a version row that references those bytes — the shape the F02B
publisher stores. It then writes the ``platform.version_dependencies`` edges the
publisher writes from the frozen ``manifest.subapp_versions`` set. The one step
that is *not* executed is the publication review transaction itself: it requires
a passed five-category evaluation report, and the only graph runtime
(``internal/presetexec``) refuses every node type outside
start/python/agent/end (``plan.go:123-127``), so no definition containing
``call_app`` can pass an application evaluation before T078 implements child
runs. Every record returned by ``seal_version`` carries
``published_by="publisher-shape-fixture"`` and the tests say so; none of them
claim a completed publication review.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from support import f00b, t025
from support import stack as st

POLICY = {
    "budget_limit": "1.00",
    "currency": "USD",
    "max_active_seconds": 300,
    "max_child_calls": 12,
    "max_child_depth": 3,
}

CHILD_INPUT_SCHEMA = {
    "type": "object",
    "properties": {"source": {"type": "string"}},
    "required": ["source"],
}

CHILD_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "report": {"type": "string"},
        "summary": {"type": "object"},
    },
    "required": ["report", "summary"],
}


def creator(stack):
    admin = t025.admin_session(stack)
    login = "t075-creator-" + f00b.new_uuid()[:8]
    return admin, t025.invite_and_activate(stack, admin, login)


def start_node(node_id: str = "start") -> dict:
    return {
        "id": node_id,
        "type": "start",
        "inputs": {},
        "output_schema": {"type": "object"},
        "config": {},
    }


def end_node(node_id: str = "end") -> dict:
    return {
        "id": node_id,
        "type": "end",
        "inputs": {},
        "output_schema": {"type": "object"},
        "config": {},
    }


def chain_definition(
    nodes: list[dict],
    *,
    input_schema: dict | None = None,
    output_schema: dict | None = None,
    policy: dict | None = None,
) -> dict:
    """start -> nodes... -> end, one linear edge chain."""
    chain = [start_node()] + nodes + [end_node()]
    edges = [
        {"source": chain[index]["id"], "target": chain[index + 1]["id"]}
        for index in range(len(chain) - 1)
    ]
    return {
        "dsl_version": "1.0",
        "input_schema": input_schema or {"type": "object"},
        "output_schema": output_schema or {"type": "object"},
        "nodes": chain,
        "edges": edges,
        "policy": dict(POLICY if policy is None else policy),
    }


def call_app_node(
    version_id: str,
    *,
    node_id: str = "child",
    inputs: dict | None = None,
    output_schema: dict | None = None,
) -> dict:
    return {
        "id": node_id,
        "type": "call_app",
        "inputs": inputs if inputs is not None else {"source": {"ref": "input#/source"}},
        "output_schema": output_schema or {"type": "object"},
        "config": {"application_version": version_id},
    }


def model_node(model_version_id: str, *, node_id: str = "analysis") -> dict:
    return {
        "id": node_id,
        "type": "model",
        "inputs": {"task": {"ref": "input#/source"}},
        "output_schema": {
            "type": "object",
            "properties": {
                "report": {"type": "string"},
                "summary": {"type": "object"},
            },
        },
        "config": {
            "model_profile_version": model_version_id,
            "prompt_template": "{{task}}",
            "response_mode": "json",
        },
    }


def child_definition(
    model_version_id: str,
    *,
    input_schema: dict | None = None,
    output_schema: dict | None = None,
    max_active_seconds: int = 300,
) -> dict:
    """A start -> model -> end child whose frozen I/O schema is explicit."""
    output = dict(output_schema or CHILD_OUTPUT_SCHEMA)
    end = end_node()
    end["inputs"] = {
        "report": {"ref": "node:analysis#/report"},
        "summary": {"ref": "node:analysis#/summary"},
    }
    policy = dict(POLICY)
    policy["max_active_seconds"] = max_active_seconds
    return {
        "dsl_version": "1.0",
        "input_schema": input_schema or CHILD_INPUT_SCHEMA,
        "output_schema": output,
        "nodes": [start_node(), model_node(model_version_id), end],
        "edges": [
            {"source": "start", "target": "analysis"},
            {"source": "analysis", "target": "end"},
        ],
        "policy": policy,
    }


def save_definition(session, application_id: str, definition: dict, *, card: dict | None = None):
    draft = t025.get_draft(session, application_id)
    assert draft.status == 200, draft.text
    return t025.save_draft(
        session,
        application_id,
        draft.header("ETag"),
        definition=definition,
        capability_card=card if card is not None else t025.full_card(),
    )


def submit(session, definition: dict):
    """Create one application, save the definition and validate it."""
    application = t025.create_application_or_fail(session, "t075 delegation case")
    saved = save_definition(session, application["id"], definition)
    assert saved.status == 200, saved.text
    return application["id"], t025.validate(session, application["id"])


def validated_manifest(stack, session, application_id: str) -> dict:
    """Validate one draft and return its exact sealed manifest artifact facts."""
    response = t025.validate(session, application_id)
    assert response.status == 200, response.text
    assert response.json == {"valid": True, "errors": []}, response.json
    rows = t025.manifest_artifacts(stack.dbname, application_id)
    assert rows, "validate did not seal a manifest artifact"
    row = rows[-1]
    content = (Path(stack.object_dir) / row["object_key"]).read_bytes()
    assert hashlib.sha256(content).hexdigest() == row["sha256"], (
        "the sealed manifest bytes do not match their recorded hash"
    )
    return {
        "manifest": json.loads(content),
        "bytes": content,
        "sha256": row["manifest_sha256"],
        "artifact_id": row["artifact_id"],
        "artifact_sha256": row["sha256"],
    }


def seal_version(
    stack,
    session,
    application_id: str,
    *,
    version_no: int = 1,
) -> dict:
    """Store one version the way the F02B publisher stores it.

    Real validate -> exact sealed manifest bytes -> application_versions row ->
    one version_dependencies edge per frozen subapp version. The publication
    review transaction is the one skipped step (see the module docstring).
    """
    sealed = validated_manifest(stack, session, application_id)
    owner_id = session.principal_id
    version_id = t025.seed_published_version(
        stack.dbname,
        owner_id,
        application_id,
        version_no=version_no,
        manifest=sealed["manifest"],
        manifest_sha256=sealed["sha256"],
        manifest_artifact_id=sealed["artifact_id"],
    )
    children = sealed["manifest"].get("subapp_versions") or []
    for child in children:
        t025.seed_version_dependency(stack.dbname, version_id, child)
    return {
        "id": version_id,
        "application_id": application_id,
        "version_no": version_no,
        "manifest": sealed["manifest"],
        "manifest_sha256": sealed["sha256"],
        "manifest_artifact_id": sealed["artifact_id"],
        "subapp_versions": list(children),
        "published_by": "publisher-shape-fixture",
    }


def artifact_bytes(stack, artifact_id: str) -> bytes:
    rows = st.sql_rows(
        stack.dbname,
        "SELECT object_key, sha256 FROM platform.artifacts "
        f"WHERE id = {st.quote_literal(artifact_id)}::uuid",
    )
    assert rows, artifact_id
    content = (Path(stack.object_dir) / rows[0]["object_key"]).read_bytes()
    assert hashlib.sha256(content).hexdigest() == rows[0]["sha256"], (
        "the sealed artifact bytes changed"
    )
    return content


def version_row(dbname: str, version_id: str) -> dict:
    rows = st.sql_rows(
        dbname,
        "SELECT id::text AS id, application_id::text AS application_id, version_no, "
        "manifest::text AS manifest, manifest_sha256, manifest_artifact_id::text AS manifest_artifact_id, "
        "revoked_at::text AS revoked_at, created_at::text AS created_at "
        "FROM platform.application_versions "
        f"WHERE id = {st.quote_literal(version_id)}::uuid",
    )
    assert len(rows) == 1, rows
    return rows[0]


def dependency_edges(dbname: str, parent_version_id: str) -> list[str]:
    rows = st.sql_rows(
        dbname,
        "SELECT child_version_id::text AS child_version_id "
        "FROM platform.version_dependencies "
        f"WHERE parent_version_id = {st.quote_literal(parent_version_id)}::uuid "
        "ORDER BY child_version_id",
    )
    return [row["child_version_id"] for row in rows]


def get_version(session, version_id: str):
    return session.client.get(f"/api/v1/versions/{version_id}")


def list_versions(session, application_id: str):
    return session.client.get(f"/api/v1/applications/{application_id}/versions")


def list_applications(session):
    return session.client.get("/api/v1/applications?limit=100")


def application_ids(response) -> list[str]:
    assert response.status == 200, response.text
    return [item["id"] for item in response.json["items"]]


def errors_of(response) -> list[dict]:
    return t025.validation_errors(response)


def assert_error(errors: list[dict], *, code: str, pointer: str, node_id: str | None = None) -> dict:
    matches = [
        error
        for error in errors
        if error["code"] == code and error["pointer"] == pointer
        and (node_id is None or error.get("node_id") == node_id)
    ]
    assert matches, (code, pointer, node_id, errors)
    return matches[0]


__all__ = [name for name in dir() if not name.startswith("_")]
