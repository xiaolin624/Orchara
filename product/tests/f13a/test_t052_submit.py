"""T052 acceptance: the public evaluateDraft submission.

Everything here goes through the real HTTP API of the compiled ``cmd/api``: the
real session cookie + CSRF, the real Idempotency-Key receipt table, the real
sealed dataset artifact and the real strict parser. No handler is stubbed.
"""

from __future__ import annotations

from support import f00b, t028, t052
from support import stack as st
from support.fixtures import (  # noqa: F401 - pytest fixtures
    api_server,
    fresh_database,
    go_binaries,
    migrated_database,
)


def _submitted(stack, *, assertions=None, cases=None, owner=None):
    owner = owner or t052.minimal_owner(stack)
    dataset = t052.dataset_document(
        cases
        or [
            t052.evaluation_case(
                case_id="case-1",
                assertions=assertions
                or [t052.assertion_run_state("succeeded"), t052.assertion_effect_count("test.send", 0)],
            )
        ]
    )
    artifact_id = t052.upload_dataset(stack, owner.creator, dataset)
    response = t052.submit_evaluation(
        owner.creator,
        owner.application_id,
        draft_revision=t052.draft_revision(owner.creator, owner.application_id),
        dataset_artifact_id=artifact_id,
    )
    return owner, artifact_id, response


def test_submit_returns_the_six_field_evaluation_job(t052_stack) -> None:
    owner, dataset_id, response = _submitted(t052_stack)
    assert response.status == 202, response.text
    body = response.json
    assert set(body) == t052.EVALUATION_FIELDS, body
    assert body["kind"] == "application"
    assert body["state"] == "queued"
    assert body["report_id"] is None
    assert body["error_code"] is None

    row = t052.evaluation_row(t052_stack.dbname, body["id"])
    assert row["owner_id"] == owner.creator.principal_id
    assert row["dataset_artifact_id"] == dataset_id
    assert row["application_id"] == owner.application_id
    assert row["snapshot_id"] is not None
    assert row["progress_total"] == 1
    # The submission creates a queued job, never a report or a run.
    assert t052.report_count(t052_stack.dbname) == 0
    assert t028.count_rows(t052_stack.dbname, 'runs') == 0


def test_the_same_idempotency_key_replays_the_stored_body(t052_stack) -> None:
    owner = t052.minimal_owner(t052_stack)
    artifact_id = t052.upload_dataset(
        t052_stack,
            owner.creator,
        t052.dataset_document(
            [t052.evaluation_case(case_id="case-1", assertions=[t052.assertion_run_state("succeeded")])]
        ),
    )
    key = f00b.new_uuid()
    first = t052.submit_evaluation(
        owner.creator,
        owner.application_id,
        draft_revision=t052.draft_revision(owner.creator, owner.application_id),
        dataset_artifact_id=artifact_id,
        idempotency_key=key,
    )
    assert first.status == 202, first.text
    second = t052.submit_evaluation(
        owner.creator,
        owner.application_id,
        draft_revision=t052.draft_revision(owner.creator, owner.application_id),
        dataset_artifact_id=artifact_id,
        idempotency_key=key,
    )
    assert second.status == 202, second.text
    assert second.json == first.json
    assert t052.evaluation_count(t052_stack.dbname) == 1

    # The same key with a different request is a conflict, not a replay.
    other = t052.upload_dataset(
        t052_stack,
            owner.creator,
        t052.dataset_document(
            [t052.evaluation_case(case_id="case-2", assertions=[t052.assertion_run_state("cancelled")])]
        ),
    )
    conflict = t052.submit_evaluation(
        owner.creator,
        owner.application_id,
        draft_revision=t052.draft_revision(owner.creator, owner.application_id),
        dataset_artifact_id=other,
        idempotency_key=key,
    )
    st.assert_error(conflict, 409, "idempotency_conflict")
    assert t052.evaluation_count(t052_stack.dbname) == 1


def test_a_missing_idempotency_key_is_refused(t052_stack) -> None:
    owner = t052.minimal_owner(t052_stack)
    artifact_id = t052.upload_dataset(
        t052_stack,
            owner.creator,
        t052.dataset_document(
            [t052.evaluation_case(case_id="case-1", assertions=[t052.assertion_run_state("succeeded")])]
        ),
    )
    response = t052.submit_evaluation(
        owner.creator,
        owner.application_id,
        draft_revision=t052.draft_revision(owner.creator, owner.application_id),
        dataset_artifact_id=artifact_id,
        omit_key=True,
    )
    st.assert_error(response, 400, "idempotency_key_required")
    assert t052.evaluation_count(t052_stack.dbname) == 0


def test_an_unknown_body_member_is_a_located_422(t052_stack) -> None:
    owner = t052.minimal_owner(t052_stack)
    artifact_id = t052.upload_dataset(
        t052_stack,
            owner.creator,
        t052.dataset_document(
            [t052.evaluation_case(case_id="case-1", assertions=[t052.assertion_run_state("succeeded")])]
        ),
    )
    response = t052.submit_evaluation(
        owner.creator,
        owner.application_id,
        draft_revision=t052.draft_revision(owner.creator, owner.application_id),
        dataset_artifact_id=artifact_id,
        body_extra={"split": "dev"},
    )
    error = st.assert_error(response, 422, "validation_failed")
    assert error["details"]["field"] == "split", error


def test_a_zero_assertion_case_is_refused_at_submit_time(t052_stack) -> None:
    owner, _dataset_id, response = _submitted(
        t052_stack,
            cases=[
            t052.evaluation_case(case_id="ok", assertions=[t052.assertion_run_state("succeeded")]),
            t052.evaluation_case(case_id="empty-checker", assertions=[]),
        ],
    )
    error = st.assert_error(response, 422, "validation_failed")
    located = error["details"]["errors"]
    assert located[0]["code"] == "dataset_empty_assertions", error
    assert located[0]["pointer"] == "/cases/1/assertions", error
    # Nothing was created for a refused dataset.
    assert t052.evaluation_count(t052_stack.dbname) == 0


def test_a_duplicate_case_id_is_refused_with_its_pointer(t052_stack) -> None:
    owner, _dataset_id, response = _submitted(
        t052_stack,
            cases=[
            t052.evaluation_case(case_id="same", assertions=[t052.assertion_run_state("succeeded")]),
            t052.evaluation_case(case_id="same", assertions=[t052.assertion_run_state("cancelled")]),
        ],
    )
    error = st.assert_error(response, 422, "validation_failed")
    located = error["details"]["errors"][0]
    assert located["code"] == "dataset_duplicate_case", error
    assert located["pointer"] == "/cases/1/case_id", error


def test_a_schema_violation_is_refused_with_its_pointer(t052_stack) -> None:
    bad = t052.evaluation_case(case_id="bad", assertions=[t052.assertion_run_state("succeeded")])
    bad["target"] = {"kind": "published_version", "id": "v1", "extra": True}
    owner, _dataset_id, response = _submitted(t052_stack, cases=[bad])
    error = st.assert_error(response, 422, "validation_failed")
    located = error["details"]["errors"][0]
    assert located["code"] == "dataset_unknown_field", error
    assert located["pointer"] == "/cases/0/target/extra", error


def test_a_stale_draft_revision_is_a_conflict(t052_stack) -> None:
    owner = t052.minimal_owner(t052_stack)
    artifact_id = t052.upload_dataset(
        t052_stack,
            owner.creator,
        t052.dataset_document(
            [t052.evaluation_case(case_id="case-1", assertions=[t052.assertion_run_state("succeeded")])]
        ),
    )
    response = t052.submit_evaluation(
        owner.creator,
        owner.application_id,
        draft_revision=t052.draft_revision(owner.creator, owner.application_id) + 1,
        dataset_artifact_id=artifact_id,
    )
    error = st.assert_error(response, 409, "revision_conflict")
    assert error["details"]["current_revision"] != error["details"]["requested_revision"], error


def test_another_principals_application_or_dataset_is_refused(t052_stack) -> None:
    owner_a = t052.minimal_owner(t052_stack)
    owner_b = t052.other_creator(t052_stack, login="t052-other-" + f00b.new_uuid()[:8])
    artifact_b = t052.upload_dataset(
        t052_stack,
            owner_b.creator,
        t052.dataset_document(
            [t052.evaluation_case(case_id="case-1", assertions=[t052.assertion_run_state("succeeded")])]
        ),
    )
    # B may not submit an evaluation on A's application: the answer is the same
    # 404 as an unknown id, so ids cannot be enumerated.
    cross_app = t052.submit_evaluation(
        owner_b.creator,
        owner_a.application_id,
        draft_revision=1,
        dataset_artifact_id=artifact_b,
    )
    st.assert_error(cross_app, 404, "resource_not_found")

    # A may not borrow B's dataset artifact.
    cross_dataset = t052.submit_evaluation(
        owner_a.creator,
        owner_a.application_id,
        draft_revision=t052.draft_revision(owner_a.creator, owner_a.application_id),
        dataset_artifact_id=artifact_b,
    )
    error = st.assert_error(cross_dataset, 422, "validation_failed")
    assert error["details"]["field"] == "dataset_artifact_id", error
    assert "another principal" in error["message"], error
    assert t052.evaluation_count(t052_stack.dbname) == 0
