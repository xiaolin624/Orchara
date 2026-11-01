-- P02 artifact pinning: a published graph version is immutable.
--
-- A run must resume against the artifact it started with, even after a newer
-- version is published. Deleting that artifact must fail loudly
-- (`artifact_unavailable`) rather than silently substituting the newest one.

CREATE SCHEMA IF NOT EXISTS app;

CREATE TABLE IF NOT EXISTS app.artifacts (
    artifact_id  text PRIMARY KEY,
    graph_version integer NOT NULL,
    digest       text NOT NULL,
    manifest     jsonb NOT NULL,
    published_at timestamptz NOT NULL DEFAULT now(),
    deleted_at   timestamptz
);

CREATE UNIQUE INDEX IF NOT EXISTS artifacts_version
    ON app.artifacts (graph_version)
    WHERE deleted_at IS NULL;

COMMENT ON COLUMN app.artifacts.deleted_at IS
    'Soft delete keeps the row so a pinned run reports artifact_unavailable instead of silently upgrading.';
