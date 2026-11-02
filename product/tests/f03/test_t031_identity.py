"""T031 acceptance: exit evidence is bound to the claimed execution identity.

A supervisor must echo the protocol version, the frozen manifest hash and the
worker image digest it actually ran. Any mismatch is 409
``execution_identity_mismatch`` before a single row changes: the run is not
terminal, the attempt stays live and the slot stays held. This is the guard
against "a new image certifies the old run" (ticket acceptance 1 and 2).
"""

from __future__ import annotations

from support import t029, t031
from support.fixtures import (  # noqa: F401 - pytest fixtures
    api_server,
    fresh_database,
    go_binaries,
    migrated_database,
    t029_stack,
)


def test_wrong_protocol_manifest_or_image_never_reaches_a_terminal(t029_stack) -> None:
    owner, lease, artifact = t031.claimed_with_output(t029_stack, execution_unit_id="unit-id")
    worker = t031.execution_client(t029_stack, "worker")
    supervisor = t031.execution_client(t029_stack, "supervisor")

    proposal = worker.submit_result(
        lease["run_id"],
        attempt_token=lease["attempt_token"],
        suggested_state="succeeded",
        output_artifact_id=artifact,
    )
    assert proposal.status == 200, proposal.text
    before = t029.run_state_row(t029_stack.dbname, lease["run_id"])

    wrong_identities = {
        "protocol_version": "2.0",
        "manifest_sha256": "f" * 64,
        "image_digest": "sha256:" + "b" * 64,
    }
    for field, value in wrong_identities.items():
        evidence = t029.exit_evidence(lease, **{field: value})
        response = supervisor.confirm_exit(
            lease["attempt_token"], execution_unit_id="unit-id", evidence=evidence
        )
        assert response.status == 409, (field, response.text)
        assert response.json["error"]["code"] == "execution_identity_mismatch", (
            field,
            response.text,
        )
        # Nothing moved: this is a refusal, not a recorded exit.
        attempt = t029.attempt_row(t029_stack.dbname, lease["attempt_token"])
        assert attempt["exited_at"] is None and attempt["revoked_at"] is None, (field, attempt)
        assert t029.run_state_row(t029_stack.dbname, lease["run_id"]) == before, field
        assert t029.job_row(t029_stack.dbname, lease["job_id"])["state"] == "leased", field
        assert t029.quota_counter(t029_stack.dbname)["used"] == 1, field

    # The same supervisor with the claimed identity can now finalize exactly once.
    correct = supervisor.confirm_exit(
        lease["attempt_token"],
        execution_unit_id="unit-id",
        evidence=t029.exit_evidence(lease, started=True, exit_code=0),
    )
    assert correct.status == 200, correct.text
    run = t029.run_state_row(t029_stack.dbname, lease["run_id"])
    assert run["state"] == "succeeded", run
    assert run["output_artifact_id"] == artifact, run
    assert t029.job_row(t029_stack.dbname, lease["job_id"])["state"] == "completed"
    assert t029.quota_counter(t029_stack.dbname)["used"] == 0
