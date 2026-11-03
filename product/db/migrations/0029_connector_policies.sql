-- F06/T048: the versioned connector policy registry and its controlled-egress
-- boundary.
--
-- IMPLEMENTATION-DETAILS §10 (Q30/Q31/Q34/Q41/Q43/Q44), SECURITY §3 and the T048
-- brief: a connector is not a marketplace object and not an arbitrary URL a
-- script may dial. An administrator registers one immutable *policy* — origin,
-- method, path prefix, request/response schemas, risk class and the deployer
-- credential reference — and the platform freezes the policy (id + sha256) into
-- the execution manifest closure. A run may only invoke a policy present in its
-- manifest, and the Go process performs the real HTTP call behind the SSRF/DNS/
-- redirect/private-IP guards.
--
-- Deliberate deviation from `contracts/database.sql`, and its owner:
--   1. The contract baseline has **no connector table** (`connector_versions` is
--      only a `tool_versions` jsonb list). T048 registers the queryable registry
--      in `platform.connector_policies`; `contracts/database.sql` is not edited
--      (docs/contracts are read-only here) and the generated contract is
--      unchanged. The table is additive and owns no contract column semantics.
--   2. The immutable policy body is sealed as an ordinary `platform.artifacts`
--      row (canonical RFC 8785 JCS bytes); `policy_artifact_id` points at it and
--      `policy_sha256` is the UNIQUE version identity. A duplicate body hash is a
--      409 (`connector_policy_exists`), never a second sealed copy.
--   3. Revocation is `revoked_at`, never a DELETE: an audit trail of what a run
--      could have called is the point of the registry. A revoked policy fails the
--      next invocation with `connector_revoked`.
--   4. `approved_at` is set by the registering administrator (registration *is*
--      the approval step in this batch); it stays separate from `created_at` so
--      a future two-person review can require both.
--   5. `version` is registry metadata (the Nth registration of the same
--      origin/method/path-prefix triple), not policy content: it is not part of
--      the sealed body/hash, so re-registering an identical policy is detected
--      as a duplicate instead of looking like a new version.
--
-- Boundaries (identical to 0007–0028):
--   * One writer: the trusted API process. No trigger, no second writer.
--   * Every table lives in `platform`; a worker never reads it directly. The
--     worker reaches a connector only through the mTLS capability endpoint.
--   * `orchara_worker`/PUBLIC get no `platform` privileges.

CREATE TABLE IF NOT EXISTS platform.connector_policies (
    id                  uuid PRIMARY KEY,
    -- Deviation 5: display/lineage metadata, not part of the sealed body.
    version             integer NOT NULL CHECK (version > 0),
    -- origin is exactly scheme://host[:port]; no path, query, fragment or
    -- userinfo. The CHECKs are defence in depth behind the Go validator.
    origin              text NOT NULL
                            CHECK (origin ~ '^https?://[^/?#@ ]+$'),
    method              text NOT NULL
                            CHECK (method IN ('GET', 'POST', 'PUT', 'PATCH', 'DELETE')),
    path_prefix         text NOT NULL
                            CHECK (length(btrim(path_prefix)) > 0),
    request_schema      jsonb NOT NULL DEFAULT '{}'::jsonb,
    response_schema     jsonb NOT NULL DEFAULT '{}'::jsonb,
    -- Deployment credential reference (`env:NAME` or `file:NAME`), never a
    -- credential value. The Go process resolves it and sends it only in
    -- secret_header; it is never returned, logged or sealed into a receipt.
    secret_ref          text NOT NULL,
    secret_header       text NOT NULL,
    risk_class          text NOT NULL CHECK (risk_class IN ('read', 'write')),
    allow_private       boolean NOT NULL DEFAULT false,
    policy_artifact_id  uuid NOT NULL REFERENCES platform.artifacts (id),
    -- Deviation 2: the version identity. UNIQUE so one immutable body has one
    -- registry row.
    policy_sha256       text NOT NULL
                            CHECK (policy_sha256 ~ '^[0-9a-f]{64}$'),
    created_by          uuid NOT NULL REFERENCES platform.principals (id),
    -- Deviation 4: registration is the approval step in this batch.
    approved_at         timestamptz,
    revoked_at          timestamptz,
    created_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT connector_policies_sha256 UNIQUE (policy_sha256),
    CONSTRAINT connector_policies_secret_header
        CHECK (secret_header ~ '^[A-Za-z0-9-]+$')
);

-- The queryable registry lists newest-first; the closure walk resolves one row
-- per policy id.
CREATE INDEX IF NOT EXISTS connector_policies_created
    ON platform.connector_policies (created_at DESC);
CREATE INDEX IF NOT EXISTS connector_policies_active
    ON platform.connector_policies (revoked_at);

-- Same privilege boundary as 0007–0028: the database never grants the worker a
-- direct path, and nothing is inherited from PUBLIC.
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

COMMENT ON TABLE platform.connector_policies IS
    'F06/T048 versioned connector policy registry (deviation 1: not in contracts/database.sql). One row per immutable admin-registered policy; the sealed JCS body lives in the referenced artifact and policy_sha256 is the version identity. Revocation is revoked_at, never a delete.';
COMMENT ON COLUMN platform.connector_policies.origin IS
    'Exactly scheme://host[:port]. The caller never supplies a host; the URL is built from this origin plus a policy-prefixed path, so userinfo/query/fragment cannot be smuggled through params.';
COMMENT ON COLUMN platform.connector_policies.path_prefix IS
    'The only path namespace this policy authorizes. Invocation paths must start with it after canonicalisation (no dot segments, no //, no backslash).';
COMMENT ON COLUMN platform.connector_policies.request_schema IS
    'Restricted JSON schema for the connector params body/query; validated in Go before any DNS lookup or dial.';
COMMENT ON COLUMN platform.connector_policies.secret_ref IS
    'Deployer credential reference (env:NAME or file:NAME). Never a value; resolved inside Go and sent only in secret_header.';
COMMENT ON COLUMN platform.connector_policies.allow_private IS
    'Explicit administrator opt-in for loopback/RFC1918 targets (local tests and on-prem gateways). Link-local/metadata/multicast/unspecified stay refused even when true.';
COMMENT ON COLUMN platform.connector_policies.revoked_at IS
    'Revocation timestamp. The next invokeCapability of this policy fails with connector_revoked; the row and its sealed artifact are retained for audit.';
COMMENT ON COLUMN platform.connector_policies.policy_sha256 IS
    'SHA-256 of the RFC 8785 JCS policy body. UNIQUE: re-registering an identical policy is a 409 connector_policy_exists, not a second version.';
