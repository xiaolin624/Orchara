-- F10/T060: admit the `pdf` detected format in the artifact admission evidence.
--
-- `platform.artifact_admissions` is the non-contract table T028 introduced
-- (migration 0015) to record the *sniffed* fact for one uploaded artifact. Its
-- `detected_format` CHECK was a closed vocabulary for the formats T028 knew:
-- text, csv, json, xlsx, zip, binary, unknown. T060 admits `application/pdf` for
-- purpose=knowledge, so the sniff produces `detected_format = 'pdf'`, and the
-- finalize handler cannot persist an accepted admission without this vocabulary
-- extension (the write fails, the artifact stays unsealed and the job is retried
-- forever).
--
-- Deliberate scope:
--   1. This is an additive vocabulary extension on a documented non-contract
--      table (T028/ADR 0006). `contracts/database.sql` does not define
--      `artifact_admissions`, so no contract file changes and
--      `python tools/sync_spec.py --check` stays clean.
--   2. Only `pdf` is added. The declared media type is still narrowed by purpose
--      in Go (`internal/artifacts.SupportedUploadMediaTypeForPurpose`): a PDF is
--      admitted for purpose=knowledge and refused for task_input/tool_source/
--      evaluation_dataset. The database vocabulary is broader than any single
--      purpose by design, exactly like `zip` (tool_source only) already is.
--   3. The constraint is recreated with its existing name so 0015's comments and
--      any operator query remain valid. Re-running `admin migrate` is a no-op:
--      the migration only runs once per database.
--
-- `orchara_worker`/PUBLIC keep zero `platform` privileges: this migration adds no
-- grant and re-asserts the revoke exactly like 0015/0034.

ALTER TABLE platform.artifact_admissions
    DROP CONSTRAINT IF EXISTS artifact_admissions_detected_format_check;

ALTER TABLE platform.artifact_admissions
    ADD CONSTRAINT artifact_admissions_detected_format_check CHECK (
        detected_format IS NULL OR detected_format IN (
            'text', 'csv', 'json', 'xlsx', 'zip', 'pdf', 'binary', 'unknown'
        )
    );

COMMENT ON COLUMN platform.artifact_admissions.detected_format IS
    'Fixed sniffed format: text, csv, json, xlsx, zip, pdf, binary or unknown. The handler decides admission for user-upload purposes from this and the declared media type.';

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
