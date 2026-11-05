"""F11/T072 real-stack helpers: copy cleanup and the backup deletion ledger.

Everything here drives the real surfaces: the compiled ``cmd/admin`` ledger
export/replay, the compiled ``cmd/maintenance copies`` pass, the real PostgreSQL
16 database and the real filesystem. The helpers only invoke those binaries and
read the authoritative rows (and real files) back; no ledger, cleanup result or
checkpoint row is faked.

Vocabulary mirrored from ``internal/deletion`` / migration 0045:

* scope ``checkpoints``        copy classes ``checkpoint``, ``summary``
* scope ``sandbox_temp``       copy class  ``sandbox_temp``
* scope ``third_party_history`` copy class ``third_party_history``
* scope ``backup_catalog``     copy classes ``backup_catalog``, ``backup_media``,
                               ``vendor_copy``, ``downloaded_copy``
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import uuid
from pathlib import Path

from support import f00b, t029, t041, t069
from support import stack as st

LEDGER_FORMAT = "orchara.deletion-ledger.v1"

COPY_SCOPES = ("checkpoints", "sandbox_temp", "third_party_history", "backup_catalog")
COPY_CLASSES = (
    "checkpoint",
    "summary",
    "sandbox_temp",
    "third_party_history",
    "backup_catalog",
    "backup_media",
    "vendor_copy",
    "downloaded_copy",
)

ENTRY_KINDS = (
    "tombstone",
    "scope_cleared",
    "scope_absent",
    "scope_failed",
    "scope_external",
    "job_completed",
)

_LEDGER_COLUMNS = (
    "sequence, deletion_job_id::text AS deletion_job_id, resource_type, "
    "resource_id::text AS resource_id, deletion_revision, entry_kind, scope, "
    "copy_class, scope_key, record::text AS record, record_sha256, prev_sha256, "
    "created_at::text AS created_at"
)


# ---------------------------------------------------------------------------
# cmd/admin invocation
# ---------------------------------------------------------------------------
def _run_binary(binary: Path, args: list[str], *, dsn: str | None, timeout: int = 180):
    return subprocess.run(
        [str(binary), *args],
        cwd=st.PRODUCT_ROOT,
        env=st.admin_env(dsn),
        capture_output=True,
        text=True,
        timeout=timeout,
        stdin=subprocess.DEVNULL,
    )


def _last_json(output: str) -> dict:
    for line in reversed(output.splitlines()):
        stripped = line.strip()
        if stripped.startswith("{"):
            return json.loads(stripped)
    raise AssertionError(f"no JSON line in output:\n{output}")


def export_ledger(stack, path: Path, *, expect_ok: bool = True):
    """Run the real ``orchara-admin deletion export`` and parse its JSON summary."""
    completed = _run_binary(
        stack.binaries["admin"], ["deletion", "export", "--file", str(path)],
        dsn=stack.dsn,
    )
    if expect_ok:
        assert completed.returncode == 0, completed.stdout + completed.stderr
        return completed, _last_json(completed.stdout)
    return completed, None


def replay_ledger(
    admin_binary: Path,
    dsn: str,
    path: Path,
    *,
    check_only: bool = False,
    expect_ok: bool = True,
):
    """Run the real ``orchara-admin deletion replay`` against an explicit DSN."""
    args = ["deletion", "replay", "--file", str(path)]
    if check_only:
        args.append("--check-only")
    completed = _run_binary(admin_binary, args, dsn=dsn)
    if expect_ok:
        assert completed.returncode == 0, completed.stdout + completed.stderr
        return completed, _last_json(completed.stdout)
    return completed, None


def ledger_file_entries(path: Path) -> tuple[dict, list[dict]]:
    """Parse one exported ledger file into (header, entries)."""
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    header = json.loads(lines[0])
    return header, [json.loads(line) for line in lines[1:]]


def write_ledger_file(path: Path, header: dict, entries: list[dict]) -> None:
    payload = [json.dumps(header, sort_keys=True)]
    payload.extend(json.dumps(entry, sort_keys=True) for entry in entries)
    path.write_text("\n".join(payload) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# authoritative ledger rows
# ---------------------------------------------------------------------------
def ledger_rows(dbname: str, *, deletion_job_id: str | None = None) -> list[dict]:
    clause = ""
    if deletion_job_id is not None:
        clause = " WHERE deletion_job_id = " + st.quote_literal(deletion_job_id) + "::uuid"
    return st.sql_rows(
        dbname,
        "SELECT " + _LEDGER_COLUMNS + " FROM platform.deletion_ledger"
        + clause + " ORDER BY sequence",
    )


def ledger_count(dbname: str) -> int:
    return int(st.sql_scalar(dbname, "SELECT count(*) FROM platform.deletion_ledger"))


def ledger_record(entry: dict) -> dict:
    value = entry.get("record")
    return json.loads(value) if isinstance(value, str) else dict(value or {})


def entries_for_job(dbname: str, job_id: str) -> list[dict]:
    return ledger_rows(dbname, deletion_job_id=job_id)


def entry_of(entries: list[dict], *, copy_class: str | None = None, entry_kind: str | None = None):
    matches = [
        entry
        for entry in entries
        if (copy_class is None or entry["copy_class"] == copy_class)
        and (entry_kind is None or entry["entry_kind"] == entry_kind)
    ]
    assert len(matches) == 1, (copy_class, entry_kind, matches)
    return matches[0]


def canonical_sha256(payload) -> str:
    """RFC 8785 canonical JSON of a small test payload -> sha256 hex."""
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# cmd/maintenance copies
# ---------------------------------------------------------------------------
def copies(
    stack,
    *,
    sandbox_root: Path,
    extra_args: list[str] | None = None,
    timeout: int = 180,
):
    """Run the real ``orchara-maintenance copies`` pass once."""
    env = st.admin_env(stack.dsn)
    env.pop("ORCHARA_DB_DSN", None)
    env["ORCHARA_MAINTENANCE_DSN"] = stack.dsn
    args = [
        str(stack.binaries["maintenance"]),
        "copies",
        "--sandbox-temp-root",
        str(sandbox_root),
        "--sandbox-age",
        "0s",
    ]
    args.extend(extra_args or [])
    completed = subprocess.run(
        args,
        cwd=st.PRODUCT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        stdin=subprocess.DEVNULL,
    )
    return completed, f00b.parse_json_lines(completed.stdout)


def copies_ok(stack, *, sandbox_root: Path, extra_args: list[str] | None = None):
    completed, reports = copies(stack, sandbox_root=sandbox_root, extra_args=extra_args)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return completed, reports


def copies_summary(reports: list[dict]) -> dict:
    summaries = [report for report in reports if "status" in report and "jobs" in report]
    assert summaries, f"no copies summary in {reports}"
    return summaries[-1]


# ---------------------------------------------------------------------------
# checkpoint schema fixture (the framework's out-of-band tables)
# ---------------------------------------------------------------------------
CHECKPOINT_SCHEMA_SQL = """
CREATE SCHEMA IF NOT EXISTS checkpoint;
CREATE TABLE IF NOT EXISTS checkpoint.checkpoints (
    thread_id            text NOT NULL,
    checkpoint_ns        text NOT NULL DEFAULT '',
    checkpoint_id        text NOT NULL,
    parent_checkpoint_id text,
    type                 text,
    checkpoint           jsonb,
    metadata             jsonb,
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
);
CREATE TABLE IF NOT EXISTS checkpoint.checkpoint_blobs (
    thread_id     text NOT NULL,
    checkpoint_ns text NOT NULL DEFAULT '',
    channel       text NOT NULL,
    version       text NOT NULL,
    type          text,
    blob          bytea,
    PRIMARY KEY (thread_id, checkpoint_ns, channel, version)
);
CREATE TABLE IF NOT EXISTS checkpoint.checkpoint_writes (
    thread_id     text NOT NULL,
    checkpoint_ns text NOT NULL DEFAULT '',
    checkpoint_id text NOT NULL,
    task_id       text NOT NULL,
    idx           integer NOT NULL,
    channel       text NOT NULL,
    type          text,
    blob          bytea,
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
);
"""


def create_checkpoint_schema(dbname: str) -> None:
    """Create the out-of-band ``checkpoint`` schema the framework owns.

    The product must never create it (DATA-MODEL.md §"checkpoint 由指定版本
    ``langgraph-checkpoint-postgres`` 在独立 schema 管理"), so the test creates the
    three documented tables itself and states that provenance here. It is exactly
    what the framework migration role would run.
    """
    st.sql(dbname, CHECKPOINT_SCHEMA_SQL)


def drop_checkpoint_schema(dbname: str) -> None:
    st.sql(dbname, "DROP SCHEMA IF EXISTS checkpoint CASCADE")


def checkpoint_schema_present(dbname: str) -> bool:
    return st.sql_scalar(dbname, "SELECT to_regclass('checkpoint.checkpoints') IS NOT NULL") == "t"


def insert_checkpoint_rows(
    dbname: str,
    thread_id: str,
    *,
    label: str,
    payload: bytes = b'{"state":"checkpoint-body"}',
) -> None:
    """Three real framework rows for one thread (checkpoint/blob/write)."""
    st.sql(
        dbname,
        "INSERT INTO checkpoint.checkpoint_blobs (thread_id, checkpoint_ns, channel, version, type, blob) "
        f"VALUES ({st.quote_literal(thread_id)}, '', 'messages', '1', 'json', "
        f"decode({st.quote_literal(payload.hex())}, 'hex'));"
        "INSERT INTO checkpoint.checkpoint_writes (thread_id, checkpoint_ns, checkpoint_id, task_id, idx, channel, type, blob) "
        f"VALUES ({st.quote_literal(thread_id)}, '', {st.quote_literal(label)}, 'task-1', 0, 'messages', 'json', "
        f"decode({st.quote_literal(payload.hex())}, 'hex'));"
        "INSERT INTO checkpoint.checkpoints (thread_id, checkpoint_ns, checkpoint_id, type, checkpoint, metadata) "
        f"VALUES ({st.quote_literal(thread_id)}, '', {st.quote_literal(label)}, 'json', '{{\"v\":1}}'::jsonb, "
        "jsonb_build_object('source','input','step',1));",
    )


def checkpoint_row_count(dbname: str, table: str, *, thread_id: str | None = None) -> int:
    clause = ""
    if thread_id is not None:
        clause = " WHERE thread_id = " + st.quote_literal(thread_id)
    return int(st.sql_scalar(dbname, f"SELECT count(*) FROM checkpoint.{table}{clause}"))


def checkpoint_thread_id(dbname: str, run_id: str) -> str:
    rows = st.sql_rows(
        dbname,
        "SELECT checkpoint_thread_id::text AS thread_id FROM platform.runs "
        f"WHERE id = {st.quote_literal(run_id)}::uuid",
    )
    assert len(rows) == 1, rows
    return rows[0]["thread_id"]


# ---------------------------------------------------------------------------
# real runs: delete the run's input artifact after a real attempt exit
# ---------------------------------------------------------------------------
def claimed_run_with_exited_attempt(
    stack, *, execution_unit_id: str = "t041-unit", input_value: dict | None = None
):
    """A real claimed run whose one attempt exited (started=false).

    Claiming creates the real ``run_attempts`` row (the supervisor's execution
    unit identity). ``started=false`` is the documented "the process never
    started" exit: the attempt is released/revoked and the Run returns to
    ``queued``, so the artifact delete then cancels it through T036 and no live
    attempt can own a sandbox workspace.
    """
    owner = t041.owner(stack)
    run = t029.queued_run(stack, owner, input_value=input_value)
    claimed = t029.execution_client(stack, "supervisor").claim_job(
        worker_id="t041-supervisor", execution_unit_id=execution_unit_id
    )
    assert claimed.status == 200, claimed.text
    lease = claimed.json
    assert lease["run_id"] == run["id"], (lease, run)
    client = t029.execution_client(stack, "supervisor")
    exited = client.confirm_exit(
        lease["attempt_token"],
        execution_unit_id=execution_unit_id,
        evidence=t029.exit_evidence(lease, started=False, reason="spawn_failed"),
    )
    assert exited.status == 200, exited.text
    return owner, run, lease


def delete_run_input_artifact(stack, owner, run_id: str) -> dict:
    """Delete the run's real input artifact -> one T069 job referencing the run."""
    artifact_id = t069.run_rows(stack.dbname, run_id)["input_artifact_id"]
    assert artifact_id, t069.run_rows(stack.dbname, run_id)
    return t069.deletion_of(
        t069.delete_resource(owner.creator, t069.RESOURCE_ARTIFACT, artifact_id)
    )


def attempt_units(dbname: str, run_id: str) -> list[str]:
    return [
        row["execution_unit_id"]
        for row in st.sql_rows(
            dbname,
            "SELECT execution_unit_id FROM platform.run_attempts "
            f"WHERE run_id = {st.quote_literal(run_id)}::uuid "
            "AND execution_unit_id IS NOT NULL ORDER BY started_at, execution_unit_id",
        )
    ]


def attempt_live(dbname: str, run_id: str) -> bool:
    return (
        st.sql_scalar(
            dbname,
            "SELECT count(*) FROM platform.run_attempts "
            f"WHERE run_id = {st.quote_literal(run_id)}::uuid "
            "AND exited_at IS NULL AND revoked_at IS NULL",
        )
        != "0"
    )


# ---------------------------------------------------------------------------
# real sandbox staging leftovers
# ---------------------------------------------------------------------------
def make_sandbox_leftover(root: Path, unit_id: str, content: bytes, *, label: str = "leftover") -> Path:
    """A crash leftover exactly where the executor stages it.

    ``internal/sandbox`` names the host workspace ``orchara-sandbox-<sandboxID>-``
    and the sandbox id is derived from the attempt's execution unit id, so this is
    the real per-attempt path naming (not a guess).
    """
    root.mkdir(parents=True, exist_ok=True)
    directory = Path(str(root / f"orchara-sandbox-{unit_id}-{uuid.uuid4().hex[:8]}"))
    directory.mkdir(parents=True)
    (directory / "config.json").write_text('{"ociVersion":"1.0.2"}', encoding="utf-8")
    (directory / "input.json").write_bytes(content)
    (directory / label).write_bytes(content)
    return directory


def make_unrelated_leftover(root: Path, content: bytes = b"legit") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    directory = root / f"orchara-sandbox-unit-unrelated-{uuid.uuid4().hex[:8]}"
    directory.mkdir()
    (directory / "input.json").write_bytes(content)
    return directory


def directory_bytes(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def t071_scopes_cleared(dbname: str, job_id: str) -> None:
    """Simulate the exact shared scope transition T071 performs for its scopes.

    T071 (object/index/cache purge) is a different ticket on a parallel branch in
    this batch. Its documented transition is: remove the scope from
    ``remaining_scopes`` and append it to ``completed_scopes`` in the same
    transaction that records the purge evidence. This helper writes that row shape
    so T072's *job completion* rule can be exercised end to end without inventing
    a second T071 implementation.
    """
    t071 = ("object_store", "search_index", "retrieval_cache")
    rows = st.sql_rows(
        dbname,
        "SELECT remaining_scopes::text AS remaining, completed_scopes::text AS completed "
        "FROM platform.deletion_jobs "
        f"WHERE id = {st.quote_literal(job_id)}::uuid",
    )
    assert len(rows) == 1, rows
    remaining = [scope for scope in json.loads(rows[0]["remaining"]) if scope not in t071]
    completed = sorted(set(json.loads(rows[0]["completed"])) | set(t071))
    st.sql(
        dbname,
        "UPDATE platform.deletion_jobs "
        f"SET remaining_scopes = {st.quote_literal(json.dumps(remaining))}::jsonb, "
        f"completed_scopes = {st.quote_literal(json.dumps(completed))}::jsonb "
        f"WHERE id = {st.quote_literal(job_id)}::uuid",
    )


# ---------------------------------------------------------------------------
# a fresh "restored" database for ledger replay
# ---------------------------------------------------------------------------
def restored_database(admin_binary: Path) -> st.FreshDatabase:
    """A second empty database migrated by the compiled ``cmd/admin``."""
    database = st.create_database("orchara_t072_restore_")
    st.migrate_or_fail(admin_binary, database.dsn)
    return database


def seed_restored_principal(dsn_db: str, principal_id: str, *, display_name: str = "restored owner") -> None:
    st.sql(
        dsn_db,
        "INSERT INTO platform.principals (id, kind, display_name) VALUES "
        f"({st.quote_literal(principal_id)}::uuid, 'user', {st.quote_literal(display_name)})",
    )


def seed_restored_rows(
    dbname: str,
    *,
    owner_id: str,
    artifact_id: str,
    object_key: str,
    conversation_id: str,
    artifact_revision: int = 1,
) -> None:
    """The pre-deletion rows a backup restore would bring back.

    Only the rows the ledger replay needs to reproduce the decisions: the owner
    principal plus one artifact and one conversation in their pre-tombstone state
    (``artifact_revision`` is the revision the backup holds, i.e. the live
    revision just before the deletion bumped it). The object bytes are not needed
    to replay a logical deletion decision.
    """
    st.sql(
        dbname,
        "INSERT INTO platform.artifacts "
        "(id, owner_id, object_key, purpose, media_type, size_bytes, state, revision) VALUES "
        f"({st.quote_literal(artifact_id)}::uuid, {st.quote_literal(owner_id)}::uuid, "
        f"{st.quote_literal(object_key)}, 'task_input', 'text/plain', 12, 'ready', {int(artifact_revision)});",
    )
    st.sql(
        dbname,
        "INSERT INTO platform.conversations (id, owner_id, revision) VALUES "
        f"({st.quote_literal(conversation_id)}::uuid, {st.quote_literal(owner_id)}::uuid, 1)",
    )


def seed_restored_document(
    dbname: str,
    *,
    owner_id: str,
    source_artifact_id: str,
    knowledge_base_id: str,
    document_id: str,
    revision: int = 1,
) -> None:
    """The pre-deletion knowledge rows a backup restore would bring back.

    The document is ``uploaded`` rather than ``ready``: the restored target only
    has to prove the row exists and is alive so the replayed decision is a real
    state change. A ``ready`` row would also need the live index-version rows of
    the original ingestion, which are outside the ledger's decision.
    """
    st.sql(
        dbname,
        "INSERT INTO platform.knowledge_bases (id, name, owner_id, revision) VALUES "
        f"({st.quote_literal(knowledge_base_id)}::uuid, 'restored knowledge', "
        f"{st.quote_literal(owner_id)}::uuid, 1);",
    )
    st.sql(
        dbname,
        "INSERT INTO platform.knowledge_documents "
        "(id, knowledge_base_id, source_artifact_id, revision, state) VALUES "
        f"({st.quote_literal(document_id)}::uuid, {st.quote_literal(knowledge_base_id)}::uuid, "
        f"{st.quote_literal(source_artifact_id)}::uuid, {int(revision)}, 'uploaded')",
    )


def restored_document_row(dbname: str, document_id: str) -> dict:
    rows = st.sql_rows(
        dbname,
        "SELECT id::text AS id, state, revision, deleted_at::text AS deleted_at "
        "FROM platform.knowledge_documents "
        f"WHERE id = {st.quote_literal(document_id)}::uuid",
    )
    assert len(rows) == 1, rows
    return rows[0]


def restored_artifact_row(dbname: str, artifact_id: str) -> dict:
    rows = st.sql_rows(
        dbname,
        "SELECT id::text AS id, state, revision, deleted_at::text AS deleted_at, "
        "purged_at::text AS purged_at FROM platform.artifacts "
        f"WHERE id = {st.quote_literal(artifact_id)}::uuid",
    )
    assert len(rows) == 1, rows
    return rows[0]


def restored_conversation_row(dbname: str, conversation_id: str) -> dict:
    rows = st.sql_rows(
        dbname,
        "SELECT id::text AS id, revision, deleted_at::text AS deleted_at "
        "FROM platform.conversations "
        f"WHERE id = {st.quote_literal(conversation_id)}::uuid",
    )
    assert len(rows) == 1, rows
    return rows[0]


# ---------------------------------------------------------------------------
# assertions shared by the copy-cleanup tests
# ---------------------------------------------------------------------------
def assert_scope_cleared(job: dict, scopes: tuple[str, ...] = COPY_SCOPES) -> None:
    remaining = t069.remaining_scopes(job)
    completed = t069.completed_scopes(job)
    for scope in scopes:
        assert scope not in remaining, (scope, remaining)
        assert scope in completed, (scope, completed)


def assert_read_refuses_artifact(stack, owner, artifact_id: str) -> None:
    """Every real artifact byte/metadata read still refuses the deleted row."""
    session = owner.creator
    detail = session.client.get("/api/v1/artifacts/" + artifact_id)
    assert detail.status == 404, detail.text
    download = session.client.get("/api/v1/artifacts/" + artifact_id + "/download")
    assert download.status == 404, download.text
    content = session.client.get("/api/v1/artifacts/" + artifact_id + "/content")
    assert content.status == 404, content.text


__all__ = [
    "COPY_CLASSES",
    "COPY_SCOPES",
    "ENTRY_KINDS",
    "LEDGER_FORMAT",
    "assert_read_refuses_artifact",
    "assert_scope_cleared",
    "attempt_live",
    "attempt_units",
    "checkpoint_row_count",
    "checkpoint_schema_present",
    "checkpoint_thread_id",
    "claimed_run_with_exited_attempt",
    "copies",
    "copies_ok",
    "copies_summary",
    "create_checkpoint_schema",
    "delete_run_input_artifact",
    "directory_bytes",
    "drop_checkpoint_schema",
    "entries_for_job",
    "entry_of",
    "export_ledger",
    "insert_checkpoint_rows",
    "ledger_count",
    "ledger_file_entries",
    "ledger_record",
    "ledger_rows",
    "make_sandbox_leftover",
    "make_unrelated_leftover",
    "replay_ledger",
    "restored_artifact_row",
    "restored_conversation_row",
    "restored_database",
    "restored_document_row",
    "seed_restored_document",
    "seed_restored_principal",
    "seed_restored_rows",
    "t071_scopes_cleared",
    "write_ledger_file",
]
