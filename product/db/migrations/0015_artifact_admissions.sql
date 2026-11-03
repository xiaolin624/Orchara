-- F05/T028: the format-admission record for a user-uploaded artifact.
--
-- Baseline: `contracts/database.sql` has no admission table. T028 adds exactly
-- one table because the user-upload path owes the platform a durable answer to
-- "what did the server actually see in the bytes?" (IMPLEMENTATION-DETAILS §9,
-- SECURITY §4). The artifact row itself still carries only the contract's
-- state/media_type/size/hash; the evidence lives next to it, keyed by the one
-- artifact it describes.
--
-- Deliberate deviations from contracts/database.sql, and their owners:
--   1. `platform.artifact_admissions` is a new table (deviation). One row per
--      artifact: the media type the uploader declared, the media type/format the
--      server sniffed, the verdict and a safe reason code. It is one current
--      fact per artifact, never an activation decision: only the finalize
--      completion guard can set an artifact ready or quarantined. The
--      artifact_finalize handler upserts the same row, so a retried attempt
--      replaces the stored verdict instead of accumulating per-attempt history.
--   2. `artifact_id` is the primary key and a real foreign key to
--      `platform.artifacts(id)`. A physical artifact row is never deleted in this
--      batch (tombstones are F07's model), so the ON DELETE CASCADE is only a
--      safety net for a future purge path; it is not a deletion model.
--   3. `sniffed_media_type` and `detected_format` are nullable because a
--      rejection can happen before any byte is inspectable (a missing staging
--      object, a hash/size mismatch, an over-limit object). An accepted verdict
--      always carries both, enforced by a CHECK; a rejected verdict always
--      carries a reason, enforced by another CHECK. Reasons are fixed safe codes
--      from `internal/artifacts` (no file names, no content, no URLs).
--
-- Deliberate boundaries:
--   * No deletion model (F07), no requests/conversations (F05/F07), no tool or
--     tool-version tables (T037): those tickets own their migrations.
--   * No sealed/staging key is copied here: keys stay on the artifact row, so
--     this table cannot become a second object namespace.
--   * Every statement is schema-qualified; nothing relies on search_path.
--   * `orchara_worker` gets no USAGE on `platform` and no table/sequence
--     privileges, exactly like 0007–0014. The admission row is written by the
--     trusted maintenance handler through its own DSN and read through the API.

CREATE SCHEMA IF NOT EXISTS platform;

CREATE TABLE IF NOT EXISTS platform.artifact_admissions (
    artifact_id         uuid PRIMARY KEY REFERENCES platform.artifacts (id) ON DELETE CASCADE,
    declared_media_type text NOT NULL CHECK (length(btrim(declared_media_type)) > 0),
    sniffed_media_type  text CHECK (sniffed_media_type IS NULL OR length(btrim(sniffed_media_type)) > 0),
    detected_format     text CHECK (detected_format IS NULL OR detected_format IN (
                            'text', 'csv', 'json', 'xlsx', 'zip', 'binary', 'unknown'
                        )),
    verdict             text NOT NULL CHECK (verdict IN ('accepted', 'rejected')),
    reason              text CHECK (reason IS NULL OR (length(btrim(reason)) > 0 AND length(reason) <= 200)),
    checked_at          timestamptz NOT NULL DEFAULT now(),
    -- An accepted upload has a sniffed fact; a rejection always has a safe
    -- reason. The two rules together make every row interpretable without
    -- reading any content.
    CONSTRAINT artifact_admissions_accepted_has_sniff CHECK (
        verdict = 'rejected'
        OR (sniffed_media_type IS NOT NULL AND detected_format IS NOT NULL)
    ),
    CONSTRAINT artifact_admissions_reason_matches_verdict CHECK (
        (verdict = 'accepted' AND reason IS NULL)
        OR (verdict = 'rejected' AND reason IS NOT NULL)
    )
);

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

COMMENT ON TABLE platform.artifact_admissions IS
    'T028 format admission evidence for one uploaded artifact: declared vs sniffed media type, detected format, verdict and a safe reason. One current row per artifact, upserted by a retried finalize attempt; only the completion guard may activate or quarantine the artifact.';
COMMENT ON COLUMN platform.artifact_admissions.sniffed_media_type IS
    'The media type implied by the real bytes (or NULL when the rejection happened before inspection). Never copied from the client declaration.';
COMMENT ON COLUMN platform.artifact_admissions.detected_format IS
    'Fixed sniffed format: text, csv, json, xlsx, zip, binary or unknown. The handler decides admission for user-upload purposes from this and the declared media type.';
COMMENT ON COLUMN platform.artifact_admissions.verdict IS
    'accepted = the bytes match the declared supported media type; rejected = the artifact must be quarantined by the completion guard.';
COMMENT ON COLUMN platform.artifact_admissions.reason IS
    'Safe stable reason code (no content, paths or URLs) for a rejected admission; NULL for an accepted one.';
