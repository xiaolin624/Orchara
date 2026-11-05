"""F11/T069: a deleted memory is never recalled by a later agent turn.

The recall path is not an HTTP read, so the oracle is the same one F09/T063 uses:
one real debug run executed by the compiled supervisor against the real object
store, its durable context trace read back through the owner-only node-results and
artifact-content routes, and the sealed model request. A kept memory is the
positive control: it proves recall really read sealed bytes in this run, so the
deleted marker's absence is not an empty-read artefact.
"""

from __future__ import annotations

from support import t063, t069


def test_a_deleted_memory_is_not_recalled_by_a_later_agent_turn(t069_stack) -> None:
    stack = t069_stack
    run_owner = t063.owner(stack)
    application_id = t063.application_for(run_owner)
    kept_text = "marker-t069-kept: the user prefers concise weekly summaries."
    deleted_text = "marker-t069-deleted: the user prefers paper invoices."

    kept = t069.create_memory(stack, run_owner.creator, application_id, kept_text)
    doomed = t069.create_memory(stack, run_owner.creator, application_id, deleted_text)
    t069.deletion_of(
        t069.delete_resource(run_owner.creator, t069.RESOURCE_MEMORY, doomed["id"])
    )

    run = t063.submit_run(stack, run_owner, application_id)
    completed, report = t063.run_agent_once(stack)
    assert completed.returncode == 0, (completed.stderr, report)

    trace = t063.trace_document(run_owner.creator, stack, run["id"])
    trace_text = t063.trace_text(trace)
    # The trace names only the source ids/labels (never the body); the deleted
    # memory is not even a source, while the kept one is.
    source_ids = {
        source.get("memory_id")
        for turn in trace["turns"]
        for source in turn.get("sources", [])
        if source.get("kind") == "memory"
    }
    assert kept["id"] in source_ids, source_ids
    assert doomed["id"] not in source_ids, source_ids
    assert "marker-t069-deleted" not in trace_text, trace_text

    messages = t063.messages_text(t063.model_messages(stack, run["id"]))
    assert "marker-t069-kept" in messages, messages
    assert "marker-t069-deleted" not in messages, messages
    t063.assert_no_content_leak(trace, [deleted_text])
