-- F08/T074 (#75): conversations, requests, the ordered conversation queue,
-- pending interactions and the confirmed handover record.
--
-- Baseline: 0014 created `runs.request_id` (UNIQUE) and `runs.conversation_id`
-- as plain uuid columns with a deferred FK, and `one_active_top_run_per_conversation`
-- (a partial UNIQUE over non-terminal top-level runs). `platform.conversations`,
-- `platform.requests` and `platform.interactions` do not exist yet. This migration
-- creates them and completes the three FKs 0011/0014 deliberately deferred.
--
-- Deliberate design decisions (all recorded in the T074 report/ADR):
--   1. The logical conversation slot is NOT one index. STATE-MACHINES.md §17 says a
--      `dispatched` request keeps its state after its Run terminates, so a partial
--      UNIQUE on `requests(state='dispatched')` would permanently block a
--      conversation after its first run. The slot is instead the conjunction of two
--      database facts, both of which are already or newly indexed:
--        * `one_active_top_run_per_conversation` (0014) forbids a second live
--          top-level Run in one conversation;
--        * `one_active_request_per_conversation` (below) forbids a second
--          pre-dispatch request (`routing`/`awaiting_input`/`awaiting_selection`)
--          in one conversation.
--      The submitting/cancelling transaction additionally locks the conversation
--      row, so a lost race becomes a queued request (or a 409) instead of a second
--      slot holder. See internal/requests for the one shared predicate.
--   2. `requests.requested_version_id` and `requests.control_calls` are NOT in
--      contracts/database.sql. The first records the explicit
--      `application_version_id` of the submit body (the fixed-version path);
--      the second is the F08/T079 control-call budget (IMPLEMENTATION-DETAILS.md
--      §45 caps it at 3 per request). Neither is exposed by the contract Request
--      projection.
--   3. `requests` has no `run_id` column: `runs.request_id` is UNIQUE (0014), so
--      the contract's `Request.run_id` is an exact LEFT JOIN, not a second fact
--      that could disagree.
--   4. `platform.request_handoffs` is not in contracts/database.sql. The contract's
--      `RequestInput.handoff` needs a durable confirmation record (who confirmed,
--      when, which source runs and which sealed summary artifact). The summary is
--      sealed as a normal artifact and linked to the new Run, so F11's deletion
--      scopes cover it without a second copy.
--   5. `interactions` is created here because a request in `awaiting_*` is a slot
--      holder and cancellation must invalidate its pending interaction
--      (`state='invalidated'`) in the same transaction. F08/T079 creates the rows;
--      this ticket only reads the pending projection and cancels it.
--   6. `requests.state` uses the contract's closed enum. The transition guards are
--      revision-CAS in Go; the CHECK only bounds the vocabulary.
--
-- Deferred FKs completed here (0014 wrote "F05/F07 must add ..."):
--   * `runs_request_id_fkey`, `runs_conversation_id_fkey`;
--   * `platform_model_calls_request_fkey` (0011 deferred it to the request owner).
-- Every existing run is a debug run with both columns NULL, so the FKs validate.
--
-- Zero grants: every statement is schema-qualified and `orchara_worker`/PUBLIC keep
-- no `platform` privileges, exactly like 0007-0043.

CREATE SCHEMA IF NOT EXISTS platform;

-- One user-visible conversation. `revision` is the contract Conversation field
-- (>=1); deletion bumps it and sets `deleted_at`, so the tombstone is the same
-- column every read predicate filters and the deletion job's revision is
-- meaningful.
CREATE TABLE IF NOT EXISTS platform.conversations (
    id         uuid PRIMARY KEY,
    owner_id   uuid NOT NULL REFERENCES platform.principals (id),
    revision   bigint NOT NULL DEFAULT 1 CHECK (revision >= 1),
    created_at timestamptz NOT NULL DEFAULT now(),
    deleted_at timestamptz
);

CREATE INDEX IF NOT EXISTS conversations_owner
    ON platform.conversations (owner_id, created_at DESC, id DESC);

-- One user request. The sealed message bytes are the authoritative input (the
-- request row never carries a second copy of the text); `artifact_ids` is the
-- explicitly referenced attachment set (the contract's RequestInput.artifact_ids,
-- stored as ids so a dispatch can link exactly those inputs and nothing else);
-- `requested_version_id` records an explicit fixed-version submission.
CREATE TABLE IF NOT EXISTS platform.requests (
    id                   uuid PRIMARY KEY,
    conversation_id      uuid NOT NULL REFERENCES platform.conversations (id),
    owner_id             uuid NOT NULL REFERENCES platform.principals (id),
    input_artifact_id    uuid NOT NULL REFERENCES platform.artifacts (id),
    artifact_ids         uuid[] NOT NULL DEFAULT '{}',
    state                text NOT NULL CHECK (state IN (
                             'queued', 'routing', 'awaiting_input', 'awaiting_selection',
                             'dispatched', 'cancelled', 'failed'
                         )),
    revision             bigint NOT NULL DEFAULT 1 CHECK (revision > 0),
    error_code           text,
    requested_version_id uuid REFERENCES platform.application_versions (id),
    control_calls        integer NOT NULL DEFAULT 0 CHECK (control_calls >= 0),
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now(),
    -- T079 caps the control-model budget at 3 per request
    -- (IMPLEMENTATION-DETAILS.md §45); the database is the backstop.
    CONSTRAINT requests_control_budget CHECK (control_calls <= 3)
);

-- The queue order: same conversation, oldest first. The read predicate uses
-- (created_at, id) so the order is total even for two rows in one millisecond.
CREATE INDEX IF NOT EXISTS requests_queue
    ON platform.requests (conversation_id, created_at, id);

-- At most one pre-dispatch slot holder per conversation (deviation 1). A
-- `dispatched` request is excluded on purpose: it stops holding the slot when its
-- Run terminates, which is not a state change of the request.
CREATE UNIQUE INDEX IF NOT EXISTS one_active_request_per_conversation
    ON platform.requests (conversation_id)
    WHERE state IN ('routing', 'awaiting_input', 'awaiting_selection');

CREATE INDEX IF NOT EXISTS requests_owner_time
    ON platform.requests (owner_id, created_at DESC, id DESC);

-- One pending interaction per awaiting request. The contract's
-- interaction.proposal_artifact_id is NOT NULL; F08/T079 seals the proposal.
CREATE TABLE IF NOT EXISTS platform.interactions (
    id                   uuid PRIMARY KEY,
    request_id           uuid NOT NULL REFERENCES platform.requests (id),
    kind                 text NOT NULL CHECK (kind IN ('input', 'selection')),
    state                text NOT NULL CHECK (state IN ('pending', 'answered', 'expired', 'invalidated')),
    proposal_artifact_id uuid NOT NULL REFERENCES platform.artifacts (id),
    response_artifact_id uuid REFERENCES platform.artifacts (id),
    correlation_id       uuid NOT NULL,
    expires_at           timestamptz NOT NULL,
    answered_at          timestamptz,
    created_at           timestamptz NOT NULL DEFAULT now(),
    CHECK ((state = 'answered') = (answered_at IS NOT NULL))
);

CREATE UNIQUE INDEX IF NOT EXISTS one_pending_interaction
    ON platform.interactions (request_id) WHERE state = 'pending';
CREATE INDEX IF NOT EXISTS interactions_correlation
    ON platform.interactions (correlation_id);
CREATE INDEX IF NOT EXISTS interactions_request
    ON platform.interactions (request_id, created_at DESC);

-- The confirmed handover (deviation 4). `source_run_ids` are the runs the user
-- explicitly chose to hand over; `summary_artifact_id` is the sealed summary bytes
-- the user confirmed. Neither the old conversation history nor the old
-- application's memory is referenced here, by design.
CREATE TABLE IF NOT EXISTS platform.request_handoffs (
    request_id          uuid PRIMARY KEY REFERENCES platform.requests (id),
    conversation_id     uuid NOT NULL REFERENCES platform.conversations (id),
    summary_artifact_id uuid NOT NULL REFERENCES platform.artifacts (id),
    source_run_ids      uuid[] NOT NULL CHECK (cardinality(source_run_ids) >= 1),
    artifact_ids        uuid[] NOT NULL DEFAULT '{}',
    confirmed_by        uuid NOT NULL REFERENCES platform.principals (id),
    confirmed_at        timestamptz NOT NULL DEFAULT now(),
    from_version_id     uuid REFERENCES platform.application_versions (id)
);

CREATE INDEX IF NOT EXISTS request_handoffs_conversation
    ON platform.request_handoffs (conversation_id);

-- Complete the deferred FKs. ADD CONSTRAINT has no IF NOT EXISTS, so the DO guard
-- keeps a repeated migrate a no-op.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'runs_request_id_fkey'
           AND conrelid = 'platform.runs'::regclass
    ) THEN
        ALTER TABLE platform.runs
            ADD CONSTRAINT runs_request_id_fkey
            FOREIGN KEY (request_id) REFERENCES platform.requests (id);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'runs_conversation_id_fkey'
           AND conrelid = 'platform.runs'::regclass
    ) THEN
        ALTER TABLE platform.runs
            ADD CONSTRAINT runs_conversation_id_fkey
            FOREIGN KEY (conversation_id) REFERENCES platform.conversations (id);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'platform_model_calls_request_fkey'
           AND conrelid = 'platform.platform_model_calls'::regclass
    ) THEN
        ALTER TABLE platform.platform_model_calls
            ADD CONSTRAINT platform_model_calls_request_fkey
            FOREIGN KEY (request_id) REFERENCES platform.requests (id);
    END IF;
END
$$;

REVOKE ALL ON SCHEMA platform FROM PUBLIC;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'orchara_worker') THEN
        EXECUTE 'REVOKE ALL ON ALL TABLES IN SCHEMA platform FROM orchara_worker';
        EXECUTE 'REVOKE ALL ON ALL SEQUENCES IN SCHEMA platform FROM orchara_worker';
        EXECUTE 'REVOKE USAGE ON SCHEMA platform FROM orchara_worker';
    END IF;
END
$$;

COMMENT ON TABLE platform.conversations IS
    'F08/T074: one user-visible conversation. deleted_at is the F11/T069 tombstone; every conversation read predicate filters it, and DELETE /conversations answers 202 through the shared deletion foundation.';
COMMENT ON TABLE platform.requests IS
    'F08/T074: one user request. The sealed message artifact is the authoritative input. state is the contract enum; revision is the CAS counter. At most one pre-dispatch request per conversation is enforced by one_active_request_per_conversation, and one_active_top_run_per_conversation (0014) bounds live top-level Runs; the conversation row lock serialises the pair.';
COMMENT ON COLUMN platform.requests.requested_version_id IS
    'F08/T074 (non-contract): the explicit application_version_id of the submit body. The fixed-version path bypasses model routing but keeps the Request, the queue, the ACL and the publication check (catalog.ResolveRunnableVersionTx).';
COMMENT ON COLUMN platform.requests.control_calls IS
    'F08/T079 (non-contract): control-model calls spent for this request, capped at 3 by requests_control_budget (IMPLEMENTATION-DETAILS.md §45). T074 creates the column and never increments it.';
COMMENT ON TABLE platform.interactions IS
    'F08/T074 creates the table; F08/T079 writes the pending rows. A request in awaiting_input/awaiting_selection holds the conversation slot, and cancelling it invalidates the pending interaction in the same transaction.';
COMMENT ON TABLE platform.request_handoffs IS
    'F08/T074 (non-contract): the user-confirmed minimal handover. Only the sealed summary artifact, the explicitly confirmed source runs/artifacts and the confirming principal are recorded; the old application history and personal memory are never read on the new version''s behalf.';
COMMENT ON COLUMN platform.request_handoffs.from_version_id IS
    'The application version of the most recent confirmed source Run, when one exists; the UI uses it to explain which application is handing over.';
