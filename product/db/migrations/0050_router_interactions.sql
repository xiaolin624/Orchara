-- F08/T079 (#80): the model only suggests a member of the server's authorized
-- candidate set; the server validates the suggestion, the required inputs and the
-- current publication/ACL, and commits exactly one interaction or one Run.
--
-- Baseline: 0044 created `requests`, `interactions` and the conversation queue and
-- left `requests.control_calls` as the (never incremented) control budget column;
-- 0045 created the authorized catalog projection. `request_routing_candidates`
-- and `request_routing_decisions` do not exist. This migration creates them and
-- adds the two request columns the reply/selection path needs.
--
-- Deliberate design decisions (recorded in the T079 report and ADR 0025):
--   1. `request_routing_candidates` is the **candidate snapshot** for one routing
--      round: the exact authorized, published, unrevoked versions the model was
--      allowed to choose from, with `rank` as an ordinal (1-based). It is written
--      in the routing transaction *before* the control model is called, so an
--      audit can prove what the model saw. `rank` is deliberately an integer
--      ordinal and there is no score/probability column: ROUTING §7 says an
--      uncalibrated model score is not a probability and must not be presented as
--      one. The snapshot is replaced on every round (one set per request) because
--      an interaction is answered before the next round starts.
--   2. `request_routing_decisions` keeps one row per round (`attempt`), including
--      a `degraded` row when the control budget is exhausted without a fourth
--      model call. `model_call_id` links the round to the governed
--      `platform_model_calls` row (XOR owner: the ledger row carries
--      `background_job_id`, the decision row carries the call id, so the ledger's
--      `(request_id IS NULL) <> (background_job_id IS NULL)` CHECK is untouched).
--   3. `requests.selected_version_id` is a non-contract column: it records the
--      user's explicit `candidate_version_id` reply so the resumed routing round
--      dispatches **that** version without a second model call and without
--      silently re-selecting. It is distinct from `requested_version_id` (the
--      explicit fixed version of the original submit, which bypasses routing and
--      is consumed by the queue promotion). `selected_version_id` never bypasses
--      the candidate-snapshot/ACL/publication re-check.
--   4. `requests.control_calls` (0044) becomes a mirror of the ledger count for
--      observability; the ledger count for the request's `route_request` jobs is
--      the budget authority (a crashed transaction cannot reset it), and the
--      database CHECK still bounds the mirror at 3.
--
-- Zero grants: every statement is schema-qualified and `orchara_worker`/PUBLIC
-- keep no `platform` privileges, exactly like 0007-0045.

CREATE SCHEMA IF NOT EXISTS platform;

-- The per-round candidate snapshot. Authorization is computed in the same SQL that
-- produces these rows; persisting the result makes the model's allowed set
-- auditable and lets the reply path reject a forged candidate deterministically.
CREATE TABLE IF NOT EXISTS platform.request_routing_candidates (
    request_id             uuid NOT NULL REFERENCES platform.requests (id),
    application_version_id uuid NOT NULL REFERENCES platform.application_versions (id),
    rank                   integer NOT NULL CHECK (rank > 0),
    application_name       text NOT NULL CHECK (length(btrim(application_name)) > 0),
    version_no             bigint NOT NULL CHECK (version_no > 0),
    capability_card        jsonb NOT NULL,
    card_sha256            text NOT NULL CHECK (length(card_sha256) = 64),
    visible_at             timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (request_id, application_version_id)
);

CREATE INDEX IF NOT EXISTS routing_candidates_rank
    ON platform.request_routing_candidates (request_id, rank);

-- One decision per routing round. `decision` is the model's output class
-- (select/clarify/no_match) or the server's own `explicit_version` (a
-- user-selected candidate resumed without a model call) and `degraded` (the
-- control budget was exhausted, so the request moved to selection/failure without
-- another model call). `raw_decision` keeps the model's sanitized JSON for audit;
-- it carries no score.
CREATE TABLE IF NOT EXISTS platform.request_routing_decisions (
    request_id          uuid NOT NULL REFERENCES platform.requests (id),
    attempt             integer NOT NULL CHECK (attempt > 0),
    decision            text NOT NULL CHECK (decision IN (
                            'select', 'clarify', 'no_match', 'explicit_version', 'degraded'
                        )),
    selected_version_id uuid REFERENCES platform.application_versions (id),
    model_call_id       uuid REFERENCES platform.platform_model_calls (id),
    raw_decision        jsonb,
    decided_at          timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (request_id, attempt)
);

-- The user's explicit candidate reply, consumed by the next routing round. It is
-- never a Run input on its own: the round re-reads the candidate snapshot and the
-- authoritative version/ACL/publication state before dispatching.
ALTER TABLE platform.requests
    ADD COLUMN IF NOT EXISTS selected_version_id uuid
        REFERENCES platform.application_versions (id);

COMMENT ON TABLE platform.request_routing_candidates IS
    'F08/T079 (non-contract): the authorized candidate snapshot of one routing round, written before the control model call. rank is an ordinal only; there is no score or probability column because ROUTING-MEMORY-KNOWLEDGE §7 forbids presenting an uncalibrated model score as one.';
COMMENT ON TABLE platform.request_routing_decisions IS
    'F08/T079 (non-contract): one row per routing round (attempt). decision is the validated model class (select/clarify/no_match), the server decision explicit_version (user-selected candidate resumed without a model call) or degraded (control budget exhausted without another call). model_call_id links the governed platform_model_calls row.';
COMMENT ON COLUMN platform.requests.selected_version_id IS
    'F08/T079 (non-contract): the user''s explicit candidate_version_id reply. The resumed routing round dispatches exactly this version after re-checking the candidate snapshot, the current ACL/publication state and the required inputs; it never silently re-selects and it never skips the checks.';
COMMENT ON COLUMN platform.requests.control_calls IS
    'F08/T079 (non-contract): an observability mirror of the governed control-model attempts counted through the request''s route_request background jobs; the ledger count is the budget authority (a rollback cannot reset it) and requests_control_budget keeps the mirror at most 3.';

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
