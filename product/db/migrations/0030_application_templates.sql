-- F04A/T049: the server-controlled application template registry.
--
-- A template is a *source draft*: one application (and its revision-1 draft)
-- owned by the seeding administrator, plus the definition and capability card a
-- second creator can copy into a new private application. It is deliberately
-- NOT an application_version and NOT a publication:
--
--   * there is no row in `application_versions`, `publication_reviews` or
--     `version_dependencies`;
--   * instantiating a template creates an ordinary owner-private application
--     with a draft, and the caller then uses the existing F02A debug path;
--   * creating a shared published version is F02B's job. This table must never
--     grow a `published`/`version_id` column here: if a later ticket needs the
--     template to publish, it adds the publication facts through F02B's own
--     contract and review gate.
--
-- `definition` and `capability_card` are stored exactly as the source draft
-- holds them, so a seed can be re-run without recompiling anything and an
-- instantiate is a byte-for-byte copy of the reviewed source.
--
-- Deliberate deviation from `contracts/database.sql`, and its owner:
--   1. The contract baseline has **no template table**. T049 adds
--      `platform.application_templates` as the server-controlled source-draft
--      registry; docs/contracts are not edited and the generated contract is
--      unchanged. A template is not an application, version or publication:
--      the F02B publication boundary is untouched.
CREATE TABLE IF NOT EXISTS platform.application_templates (
    id              text PRIMARY KEY CHECK (length(btrim(id)) > 0),
    name            text NOT NULL CHECK (length(btrim(name)) > 0),
    application_id  uuid NOT NULL REFERENCES platform.applications (id) ON DELETE RESTRICT,
    definition      jsonb NOT NULL,
    capability_card jsonb NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    -- Templates are objects, exactly like a draft: a scalar or array could not
    -- be compiled and would make the instantiate copy ambiguous.
    CHECK (jsonb_typeof(definition) = 'object'),
    CHECK (jsonb_typeof(capability_card) = 'object')
);

-- One template per source application. This is what makes the seed idempotent:
-- re-running it updates the same row instead of creating a second source draft.
CREATE UNIQUE INDEX IF NOT EXISTS application_templates_application
    ON platform.application_templates (application_id);

-- Privileges: the same boundary as every other platform table. The runtime
-- worker role never reads templates; the API reaches them with the API role.
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

COMMENT ON TABLE platform.application_templates IS
    'F04A/T049 server-controlled sales template: a private source draft copied into the caller''s own application. Not a published application version; publication is F02B.';
