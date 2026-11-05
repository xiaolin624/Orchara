-- F02B/T068 (#69): publication review → immutable version, and the durable
-- catalog outbox row.
--
-- Baseline: 0010 created `publication_reviews` and `application_versions` as the
-- F02A read/write tables; 0013 added the `publication_reviews.report_id` FK. No
-- code wrote either table, so this migration adds the frozen-decision evidence
-- columns the review flow needs and the per-version revocation state T070 (#71)
-- will update. All additions are additive and idempotent.
--
-- Deliberate deviations from `contracts/database.sql`, with their owners:
--   1. `publication_reviews` gains `reason` (the administrator's recorded
--      decision reason; the contract body requires it but the table had no
--      column), `snapshot_id` (the frozen `execution_snapshots` row the review is
--      bound to), `evaluation_id` (the evaluation whose report is cited),
--      `dataset_sha256` (the frozen dataset binding), `manifest_artifact_id` (the
--      sealed canonical manifest bytes approval binds to the version) and
--      `version_id` (the version an approval created, null for a rejection or a
--      pending review). The contract only has the free-form `snapshot jsonb`;
--      these columns make the binding a foreign-key-checkable fact rather than
--      something re-parsed out of JSON. T070 reads them, never writes them.
--   2. `application_versions` gains `revoked_by` and `revoke_reason` beside the
--      contract's `revoked_at`. Revocation is a state change (an UPDATE of these
--      three columns), never a rewrite of `manifest`/`manifest_sha256`/
--      `manifest_artifact_id`/`version_no`. T070 owns the transition; this
--      migration only creates the fields and a CHECK that refuses a half
--      revocation. There is no DELETE path: `applications.active_version_id` is
--      ON DELETE RESTRICT (0010) and the row is the historical reference.
--   3. A partial UNIQUE index allows at most one `submitted` review per
--      (application, frozen manifest hash). A decided review is outside the
--      index, so the history is preserved while two live submissions of the same
--      content cannot coexist (T068's submit path returns the existing review
--      instead of creating a second one).
--   4. `publication_reviews.version_id` is a second FK between the review and
--      the version (the version already references its review). Both tables
--      exist, so PostgreSQL accepts the mutual reference; the approval
--      transaction inserts the version first and then points the review at it.
--   5. `platform.outbox` is created with exactly the contract shape
--      (`contracts/database.sql`). T068 writes one row per publication in the
--      same transaction as the version/pointer update; there is no dispatcher or
--      consumer in this build (T077/T082 own delivery and the catalog index).
--      `attempts` stays 0 and `delivered_at` stays NULL until a consumer exists;
--      no code claims the catalog projection was updated through it.
--   6. `application_versions(review_id)` is UNIQUE: exactly one version per
--      approved review is a database fact, not only a Go check. Existing seeded
--      rows already satisfy it (one version per seeded review).
--
-- Deliberate boundaries:
--   * No `application_catalog_entries`/index here: T077 owns the catalog index.
--     The authoritative catalog is `applications.active_version_id` plus the
--     `application_versions` read model, both updated by the publication
--     transaction.
--   * Every statement is schema-qualified; nothing relies on search_path.
--   * `orchara_worker` keeps no USAGE on `platform` and no table/sequence
--     privileges; PUBLIC has nothing. Publication rows are reached only through
--     the authenticated API.

CREATE SCHEMA IF NOT EXISTS platform;

-- 1. Frozen decision evidence on the review row (deviation 1).
ALTER TABLE platform.publication_reviews
    ADD COLUMN IF NOT EXISTS reason text,
    ADD COLUMN IF NOT EXISTS snapshot_id uuid REFERENCES platform.execution_snapshots (id),
    ADD COLUMN IF NOT EXISTS evaluation_id uuid REFERENCES platform.evaluation_jobs (id),
    ADD COLUMN IF NOT EXISTS dataset_sha256 text,
    ADD COLUMN IF NOT EXISTS manifest_artifact_id uuid REFERENCES platform.artifacts (id),
    ADD COLUMN IF NOT EXISTS version_id uuid REFERENCES platform.application_versions (id);

-- At most one live submission per frozen content (deviation 3). The submit path
-- deliberately returns the existing review instead of creating a second row;
-- this index is the database backstop.
CREATE UNIQUE INDEX IF NOT EXISTS publication_reviews_one_submitted
    ON platform.publication_reviews (application_id, manifest_sha256)
    WHERE state = 'submitted';

-- The administrator queue is "submitted reviews, oldest first"; the creator
-- list is "my submissions, newest first".
CREATE INDEX IF NOT EXISTS publication_reviews_queue
    ON platform.publication_reviews (state, created_at)
    WHERE state = 'submitted';
CREATE INDEX IF NOT EXISTS publication_reviews_submitter
    ON platform.publication_reviews (submitted_by, created_at DESC);

-- 2. Revocation is a state change on the version row (deviation 2). The CHECK
-- refuses an actor/reason without the revocation timestamp; T070 always writes
-- all three, while a legacy/pre-T070 row that only sets `revoked_at` remains
-- schema-legal (the existing F02A test fixtures seed exactly that shape). Wrapped
-- in a DO guard because ADD CONSTRAINT has no IF NOT EXISTS.
ALTER TABLE platform.application_versions
    ADD COLUMN IF NOT EXISTS revoked_by uuid REFERENCES platform.principals (id),
    ADD COLUMN IF NOT EXISTS revoke_reason text;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'application_versions_revoke_shape'
           AND conrelid = 'platform.application_versions'::regclass
    ) THEN
        ALTER TABLE platform.application_versions
            ADD CONSTRAINT application_versions_revoke_shape CHECK (
                revoked_at IS NOT NULL
                OR (revoked_by IS NULL AND revoke_reason IS NULL)
            );
    END IF;
END
$$;

-- 3. One immutable version per approved review (deviation 6).
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'application_versions_review_id_key'
           AND conrelid = 'platform.application_versions'::regclass
    ) THEN
        ALTER TABLE platform.application_versions
            ADD CONSTRAINT application_versions_review_id_key UNIQUE (review_id);
    END IF;
END
$$;

-- 4. The contract outbox shape (deviation 5). T068 writes a row with
-- kind='catalog_index'; T077/T082 own delivery and the catalog index.
CREATE TABLE IF NOT EXISTS platform.outbox (
    id           uuid PRIMARY KEY,
    kind         text NOT NULL,
    aggregate_id uuid NOT NULL,
    payload      jsonb NOT NULL,
    available_at timestamptz NOT NULL DEFAULT now(),
    delivered_at timestamptz,
    attempts     integer NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS outbox_ready
    ON platform.outbox (available_at, id)
    WHERE delivered_at IS NULL;

-- Privileges: identical boundary to 0006–0041.
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

COMMENT ON COLUMN platform.publication_reviews.reason IS
    'F02B/T068: the administrator''s recorded decision reason (a rejection always carries one). Kept on the review row so the decision history is durable after the draft moves on.';
COMMENT ON COLUMN platform.publication_reviews.snapshot_id IS
    'F02B/T068: the frozen execution_snapshots row the review was submitted against. Approval re-reads this row and never recompiles the current draft.';
COMMENT ON COLUMN platform.publication_reviews.evaluation_id IS
    'F02B/T068: the application evaluation whose sealed report is cited as the publication evidence.';
COMMENT ON COLUMN platform.publication_reviews.dataset_sha256 IS
    'F02B/T068: the dataset hash the cited report was produced over (copied from the immutable report row).';
COMMENT ON COLUMN platform.publication_reviews.manifest_artifact_id IS
    'F02B/T068: the sealed canonical manifest artifact the approval binds to the new version row (the version JSON artifact). The bytes are frozen at submission; the approval transaction re-verifies the artifact row and hash before referencing it.';
COMMENT ON COLUMN platform.publication_reviews.version_id IS
    'F02B/T068: the immutable application version an approval created; NULL while submitted and for a rejection.';
COMMENT ON TABLE platform.outbox IS
    'F02B/T068 contract outbox: one durable row per publication in the same transaction as the version/active-pointer update. No dispatcher/consumer exists in this build; T077/T082 own delivery and the catalog index, so delivered_at/attempts stay at their initial values until then.';
COMMENT ON COLUMN platform.application_versions.revoked_by IS
    'F02B/T070: the administrator who revoked this version. Revocation is a state change on this row (revoked_at/revoked_by/revoke_reason); manifest, hash, artifact and version_no are never rewritten.';
COMMENT ON COLUMN platform.application_versions.revoke_reason IS
    'F02B/T070: the recorded revocation reason; required together with revoked_at by application_versions_revoke_shape.';
