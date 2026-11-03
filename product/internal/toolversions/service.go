// Package toolversions owns F05/T037's tool submission, build binding, review
// and revocation service.
//
// The contract's ToolVersion is small (seven fields); the platform needs more:
// the source hash that was admitted, the canonical dependency lock the build
// received, the job-scoped log/SBOM artifacts of the build, the content bundle
// digest the build guard derived, and the review/revocation facts. Those live in
// the guarded 0023 columns (a documented non-contract extension) and are read
// back through the non-contract build-facts endpoint.
//
// Boundaries held here:
//   - submit is one transaction: the tool row and exactly one `tool_build`
//     background job. The job is created with background.EnqueueTx through the
//     same (kind, dedup_key) mechanism every other producer uses;
//   - manifest_sha256 is globally UNIQUE and is the content identity: same
//     content by the same owner is an idempotent 202 (no second job), the same
//     content by another owner is 409 tool_conflict;
//   - the row is never ready here. Only the background completion guard may set
//     ready after it re-checks the revision/source hash and the job-linked build
//     artifacts;
//   - review is bound to the exact manifest_sha256 and requires state=ready;
//     a reject records the reason without pretending the build failed, and an
//     approved row is still the only thing shared execution accepts (T025);
//   - revoke keeps the row and history readable but makes the T025 dependency
//     closure report dependency_revoked.
package toolversions

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"strings"
	"time"

	"orchara/platform/internal/audit"
	"orchara/platform/internal/background"
	"orchara/platform/internal/catalog"
	"orchara/platform/internal/jcs"
	"orchara/platform/internal/uuid"
)

// ToolManifestVersion is the closed manifest document version submitTool hashes.
const ToolManifestVersion = "1.0"

// MaxJSONObjectBytes bounds each submitted schema/lock object. The contract does
// not state a limit; a tool schema is a small document, not a data payload.
const MaxJSONObjectBytes = 1 << 20

// MaxReasonBytes bounds an admin review/revocation reason.
const MaxReasonBytes = 2000

// Risk classes are the contract's closed enum.
var riskClasses = map[string]bool{"pure": true, "read": true, "write": true}

// Error sentinels the HTTP layer maps to stable contract codes.
var (
	// ErrNotVisible is both "no such tool" and "not yours"; the caller answers
	// with one 404 so ids cannot be enumerated.
	ErrNotVisible = errors.New("tool version not found")
)

// ValidationError names the field that failed validation (422 validation_failed).
type ValidationError struct {
	Field   string
	Message string
}

func (e *ValidationError) Error() string { return e.Field + ": " + e.Message }

// ConflictError carries one of the documented conflict codes:
//
//	tool_conflict        manifest_sha256 already names another owner's content
//	review_conflict      review decision/hash already recorded differently
//	tool_state_conflict  the tool is not in a state that allows the operation
type ConflictError struct {
	Code    string
	Message string
}

func (e *ConflictError) Error() string { return e.Code + ": " + e.Message }

// Version is the contract ToolVersion projection plus the owner used for the
// server-side visibility check (never serialised).
type Version struct {
	ID             string          `json:"id"`
	State          string          `json:"state"`
	Approved       bool            `json:"approved"`
	ImageDigest    *string         `json:"image_digest"`
	ManifestSHA256 string          `json:"manifest_sha256"`
	InputSchema    json.RawMessage `json:"input_schema"`
	OutputSchema   json.RawMessage `json:"output_schema"`
	OwnerID        string          `json:"-"`
	CreatedAt      time.Time       `json:"-"`
}

// SubmitInput is the exact contract ToolInput with the JSON objects kept as raw
// bytes so their canonical form is not re-serialised by this package.
type SubmitInput struct {
	SourceArtifactID    string          `json:"source_artifact_id"`
	InputSchema         json.RawMessage `json:"input_schema"`
	OutputSchema        json.RawMessage `json:"output_schema"`
	DependencyLock      json.RawMessage `json:"dependency_lock"`
	RiskClass           string          `json:"risk_class"`
	ConnectorVersionIDs []string        `json:"connector_version_ids"`
}

// BuildFacts is the non-contract diagnostics read model: what the build produced
// and how the review/revocation went. It never contains object bytes or keys.
type BuildFacts struct {
	ToolVersionID      string     `json:"tool_version_id"`
	State              string     `json:"state"`
	Revision           int64      `json:"revision"`
	SourceSHA256       *string    `json:"source_sha256"`
	DependencyLockSHA  *string    `json:"dependency_lock_sha256"`
	ManifestSHA256     string     `json:"manifest_sha256"`
	BuildLogArtifactID *string    `json:"build_log_artifact_id"`
	SBOMArtifactID     *string    `json:"sbom_artifact_id"`
	BuildBundleDigest  *string    `json:"build_bundle_digest"`
	BuildErrorCode     *string    `json:"build_error_code"`
	BuildStartedAt     *time.Time `json:"build_started_at"`
	BuildFinishedAt    *time.Time `json:"build_finished_at"`
	ReviewDecision     *string    `json:"review_decision"`
	ReviewReason       *string    `json:"review_reason"`
	ReviewedAt         *time.Time `json:"reviewed_at"`
	RevokedAt          *time.Time `json:"revoked_at"`
	RevokeReason       *string    `json:"revoke_reason"`
}

// BuildBinding is what a leased tool_build job may learn about its tool: the
// exact source hash, the canonical dependency lock it must pass to the sandbox,
// and the manifest identity. The handler supplies only its lease proof.
type BuildBinding struct {
	ToolVersionID    string
	SourceArtifactID string
	SourceSHA256     string
	DependencyLock   json.RawMessage
	ManifestSHA256   string
	State            string
}

// Service owns the tool-version reads/writes over one database handle.
type Service struct {
	DB *sql.DB
}

// New builds the service.
func New(db *sql.DB) *Service { return &Service{DB: db} }

const selectVersionColumns = `
	id::text, state, approved_at IS NOT NULL, image_digest, manifest_sha256,
	input_schema, output_schema, owner_id::text, created_at`

func scanVersion(row interface{ Scan(dest ...any) error }) (*Version, error) {
	var (
		version   Version
		image     sql.NullString
		approved  bool
		createdAt time.Time
	)
	if err := row.Scan(
		&version.ID, &version.State, &approved, &image, &version.ManifestSHA256,
		&version.InputSchema, &version.OutputSchema, &version.OwnerID, &createdAt,
	); err != nil {
		return nil, err
	}
	version.Approved = approved
	version.ImageDigest = nullString(image)
	version.CreatedAt = createdAt
	return &version, nil
}

// Submit validates one contract ToolInput and, in one transaction, inserts the
// tool row plus exactly one tool_build job. It is idempotent for the same owner
// and content and a conflict for another owner's identical content.
func (s *Service) Submit(
	ctx context.Context,
	callerID string,
	input SubmitInput,
) (*Version, error) {
	if !uuid.Valid(strings.TrimSpace(callerID)) {
		return nil, ErrNotVisible
	}
	if !uuid.Valid(strings.TrimSpace(input.SourceArtifactID)) {
		return nil, &ValidationError{Field: "source_artifact_id", Message: "must be a UUID"}
	}
	sourceID := strings.TrimSpace(input.SourceArtifactID)

	inputSchema, inputCanonical, err := canonicalObject("input_schema", input.InputSchema)
	if err != nil {
		return nil, err
	}
	outputSchema, outputCanonical, err := canonicalObject("output_schema", input.OutputSchema)
	if err != nil {
		return nil, err
	}
	lockCanonical, err := canonicalObjectBytes("dependency_lock", input.DependencyLock)
	if err != nil {
		return nil, err
	}
	riskClass := strings.ToLower(strings.TrimSpace(input.RiskClass))
	if !riskClasses[riskClass] {
		return nil, &ValidationError{
			Field:   "risk_class",
			Message: "risk_class must be pure, read or write",
		}
	}
	connectorIDs, err := normaliseConnectorVersionIDs(input.ConnectorVersionIDs)
	if err != nil {
		return nil, err
	}
	connectorsCanonical, err := canonicalConnectorIDs(connectorIDs)
	if err != nil {
		return nil, err
	}
	_ = inputCanonical
	_ = outputCanonical
	lockSHA := jcs.SHA256Hex(lockCanonical)

	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return nil, fmt.Errorf("starting the tool submission transaction: %w", err)
	}
	defer func() { _ = tx.Rollback() }()

	artifact, err := readSourceArtifact(ctx, tx, sourceID)
	if err != nil {
		return nil, err
	}
	if !strings.EqualFold(artifact.OwnerID, callerID) {
		// Another principal's artifact answers exactly like a missing one.
		return nil, ErrNotVisible
	}
	if artifact.DeletedAt.Valid || artifact.State == "deleted" || artifact.State == "quarantined" {
		return nil, ErrNotVisible
	}
	if artifact.State != "ready" {
		return nil, &ConflictError{
			Code:    "artifact_not_ready",
			Message: "the source artifact must be ready before a tool can be submitted",
		}
	}
	if artifact.Purpose != "tool_source" || strings.ToLower(strings.TrimSpace(artifact.MediaType)) != "application/zip" {
		return nil, &ValidationError{
			Field:   "source_artifact_id",
			Message: "the source artifact must be an admitted application/zip upload with purpose tool_source",
		}
	}
	sourceSHA := strings.ToLower(strings.TrimSpace(artifact.SHA256.String))
	if !artifact.SHA256.Valid || !isSHA256Hex(sourceSHA) {
		return nil, &ValidationError{
			Field:   "source_artifact_id",
			Message: "the source artifact has no recorded content hash",
		}
	}

	manifestSHA, manifestBytes, err := toolManifestSHA(
		sourceSHA, inputSchema, outputSchema, lockCanonical, lockSHA, riskClass, connectorIDs,
	)
	if err != nil {
		return nil, err
	}
	_ = manifestBytes

	toolID := uuid.NewString()
	if err := s.requireConnectorPolicies(ctx, tx, connectorIDs); err != nil {
		return nil, err
	}
	var insertedID string
	err = tx.QueryRowContext(ctx, `
		INSERT INTO platform.tool_versions
			(id, owner_id, source_artifact_id, input_schema, output_schema,
			 dependency_lock, connector_versions, risk_class, state, manifest_sha256,
			 revision, source_sha256, dependency_lock_sha256)
		VALUES ($1::uuid, $2::uuid, $3::uuid, $4::jsonb, $5::jsonb,
		        $6::jsonb, $7::jsonb, $8, 'submitted', $9, 1, $10, $11)
		ON CONFLICT (manifest_sha256) DO NOTHING
		RETURNING id::text`,
		toolID, callerID, sourceID, string(inputSchema), string(outputSchema),
		string(lockCanonical), string(connectorsCanonical), riskClass, manifestSHA, sourceSHA, lockSHA,
	).Scan(&insertedID)
	switch {
	case err == nil:
		if _, _, err := background.EnqueueTx(ctx, tx, background.EnqueueParams{
			Kind:             background.KindToolBuild,
			TargetID:         insertedID,
			OwnerID:          callerID,
			DedupKey:         buildDedupKey(insertedID),
			ExpectedRevision: int64Pointer(1),
			ExpectedSHA256:   sourceSHA,
			InputArtifactID:  sourceID,
		}); err != nil {
			return nil, fmt.Errorf("enqueuing the tool build: %w", err)
		}
		version := &Version{
			ID:             insertedID,
			State:          "submitted",
			Approved:       false,
			ImageDigest:    nil,
			ManifestSHA256: manifestSHA,
			InputSchema:    inputSchema,
			OutputSchema:   outputSchema,
			OwnerID:        callerID,
		}
		if err := tx.Commit(); err != nil {
			return nil, fmt.Errorf("committing the tool submission: %w", err)
		}
		return version, nil
	case !errors.Is(err, sql.ErrNoRows):
		return nil, fmt.Errorf("inserting the tool version: %w", err)
	}

	// The content already exists. Same owner: idempotent 202 with the recorded
	// row and no second job. Another owner: the globally unique manifest hash is
	// a conflict, never a silent cross-owner reuse.
	existing, err := scanVersion(tx.QueryRowContext(ctx, `
		SELECT `+selectVersionColumns+`
		  FROM platform.tool_versions
		 WHERE manifest_sha256 = $1
		 FOR UPDATE`, manifestSHA))
	if errors.Is(err, sql.ErrNoRows) {
		return nil, &ConflictError{Code: "tool_conflict", Message: "the content identity is already used"}
	}
	if err != nil {
		return nil, fmt.Errorf("reading the recorded tool version: %w", err)
	}
	if !strings.EqualFold(existing.OwnerID, callerID) {
		return nil, &ConflictError{
			Code:    "tool_conflict",
			Message: "identical tool content was submitted by another owner; manifest_sha256 is globally unique",
		}
	}
	if existing.State == "submitted" || existing.State == "building" {
		// Re-arm the *same* job idempotently: a dedup conflict here would mean the
		// recorded job names different source facts, which cannot be replayed.
		if _, _, err := background.EnqueueTx(ctx, tx, background.EnqueueParams{
			Kind:             background.KindToolBuild,
			TargetID:         existing.ID,
			OwnerID:          callerID,
			DedupKey:         buildDedupKey(existing.ID),
			ExpectedRevision: int64Pointer(1),
			ExpectedSHA256:   sourceSHA,
			InputArtifactID:  sourceID,
		}); err != nil && !errors.Is(err, background.ErrDedupConflict) {
			return nil, fmt.Errorf("re-arming the tool build: %w", err)
		}
	}
	if err := tx.Commit(); err != nil {
		return nil, fmt.Errorf("committing the idempotent tool submission: %w", err)
	}
	return existing, nil
}

func buildDedupKey(toolID string) string { return "tool-build:" + toolID }

func int64Pointer(value int64) *int64 { return &value }

// sourceArtifact is the admitted upload submitTool re-checks.
type sourceArtifact struct {
	OwnerID   string
	State     string
	Purpose   string
	MediaType string
	SHA256    sql.NullString
	DeletedAt sql.NullTime
}

func readSourceArtifact(ctx context.Context, tx *sql.Tx, artifactID string) (sourceArtifact, error) {
	var artifact sourceArtifact
	err := tx.QueryRowContext(ctx, `
		SELECT owner_id::text, state, purpose, media_type, sha256, deleted_at
		  FROM platform.artifacts
		 WHERE id = $1::uuid`, artifactID,
	).Scan(
		&artifact.OwnerID, &artifact.State, &artifact.Purpose, &artifact.MediaType,
		&artifact.SHA256, &artifact.DeletedAt,
	)
	if errors.Is(err, sql.ErrNoRows) {
		return sourceArtifact{}, ErrNotVisible
	}
	if err != nil {
		return sourceArtifact{}, fmt.Errorf("reading the source artifact: %w", err)
	}
	return artifact, nil
}

// canonicalObject validates a contract schema field is a JSON object and returns
// its raw bytes plus the canonical bytes. The returned schemas are the raw
// submission (PostgreSQL stores them as jsonb); the canonical form is only used
// for hashing.
func canonicalObject(field string, raw json.RawMessage) (json.RawMessage, []byte, error) {
	canonical, err := canonicalObjectBytes(field, raw)
	if err != nil {
		return nil, nil, err
	}
	return json.RawMessage(bytes.TrimSpace(raw)), canonical, nil
}

func canonicalObjectBytes(field string, raw json.RawMessage) ([]byte, error) {
	trimmed := bytes.TrimSpace(raw)
	if len(trimmed) == 0 {
		return nil, &ValidationError{Field: field, Message: "is required and must be a JSON object"}
	}
	if len(trimmed) > MaxJSONObjectBytes {
		return nil, &ValidationError{Field: field, Message: "is too large"}
	}
	if trimmed[0] != '{' {
		return nil, &ValidationError{Field: field, Message: "must be a JSON object"}
	}
	canonical, err := jcs.CanonicalizeBytes(trimmed)
	if err != nil {
		return nil, &ValidationError{Field: field, Message: "is not valid canonical JSON: " + err.Error()}
	}
	return canonical, nil
}

// toolManifestSHA computes the canonical content identity:
//
//	{
//	  "connector_version_ids": [],
//	  "dependency_lock": <canonical lock>,
//	  "dependency_lock_sha256": "<sha256 of canonical lock>",
//	  "input_schema": <canonical input schema>,
//	  "output_schema": <canonical output schema>,
//	  "risk_class": "<pure|read|write>",
//	  "source_sha256": "<admitted source hash>",
//	  "tool_manifest_version": "1.0"
//	}
//
// The document excludes the tool's assigned id on purpose: the identity is the
// content, so a re-submission is recognised and another owner's identical
// content is a conflict. Every member is already canonical, and the keys are
// assembled in RFC 8785 (UTF-16) order, so the hash is stable without
// re-serialising user JSON through Go maps (which could change number text).
func toolManifestSHA(
	sourceSHA string,
	inputSchema, outputSchema, lockCanonical []byte,
	lockSHA, riskClass string,
	connectorIDs []string,
) (string, []byte, error) {
	inputCanonical, err := jcs.CanonicalizeBytes(inputSchema)
	if err != nil {
		return "", nil, &ValidationError{Field: "input_schema", Message: err.Error()}
	}
	outputCanonical, err := jcs.CanonicalizeBytes(outputSchema)
	if err != nil {
		return "", nil, &ValidationError{Field: "output_schema", Message: err.Error()}
	}
	riskCanonical, err := jcs.Canonicalize(riskClass)
	if err != nil {
		return "", nil, err
	}
	connectorCanonical, err := canonicalConnectorIDs(connectorIDs)
	if err != nil {
		return "", nil, err
	}
	sourceCanonical, err := jcs.Canonicalize(sourceSHA)
	if err != nil {
		return "", nil, err
	}
	lockSHACanonical, err := jcs.Canonicalize(lockSHA)
	if err != nil {
		return "", nil, err
	}
	// Keys in UTF-16 order: connector_version_ids, dependency_lock,
	// dependency_lock_sha256, input_schema, output_schema, risk_class,
	// source_sha256, tool_manifest_version.
	var builder bytes.Buffer
	builder.WriteString(`{"connector_version_ids":`)
	builder.Write(connectorCanonical)
	builder.WriteString(`,"dependency_lock":`)
	builder.Write(lockCanonical)
	builder.WriteString(`,"dependency_lock_sha256":`)
	builder.WriteString(lockSHACanonical)
	builder.WriteString(`,"input_schema":`)
	builder.Write(inputCanonical)
	builder.WriteString(`,"output_schema":`)
	builder.Write(outputCanonical)
	builder.WriteString(`,"risk_class":`)
	builder.WriteString(riskCanonical)
	builder.WriteString(`,"source_sha256":`)
	builder.WriteString(sourceCanonical)
	builder.WriteString(`,"tool_manifest_version":`)
	versionCanonical, _ := jcs.Canonicalize(ToolManifestVersion)
	builder.WriteString(versionCanonical)
	builder.WriteString(`}`)
	payload := builder.Bytes()
	return jcs.SHA256Hex(payload), payload, nil
}

// ---------------------------------------------------------------------------
// reads
// ---------------------------------------------------------------------------

// List returns the caller's own tools, every approved tool (the shared catalog),
// and, for an administrator, every tool. The visibility predicate is in the same
// WHERE as the keyset cursor and LIMIT, so filtering happens before pagination.
func (s *Service) List(
	ctx context.Context,
	callerID string,
	admin bool,
	cursor string,
	limit int,
) ([]Version, *string, error) {
	if !uuid.Valid(strings.TrimSpace(callerID)) {
		return nil, nil, ErrNotVisible
	}
	if limit < 1 || limit > 100 {
		return nil, nil, &ValidationError{Field: "limit", Message: "must be between 1 and 100"}
	}
	var (
		cursorAt any
		cursorID any
	)
	if strings.TrimSpace(cursor) != "" {
		at, id, err := catalog.DecodeCursor(cursor)
		if err != nil {
			return nil, nil, catalog.ErrInvalidCursor
		}
		cursorAt, cursorID = at, id
	}
	rows, err := s.DB.QueryContext(ctx, `
		SELECT `+selectVersionColumns+`
		  FROM platform.tool_versions
		 WHERE ($1::boolean OR owner_id = $2::uuid OR approved_at IS NOT NULL)
		   AND ($3::timestamptz IS NULL
		        OR (created_at, id) < ($3::timestamptz, $4::uuid))
		 ORDER BY created_at DESC, id DESC
		 LIMIT $5`,
		admin, callerID, cursorAt, cursorID, limit+1,
	)
	if err != nil {
		return nil, nil, fmt.Errorf("listing tool versions: %w", err)
	}
	defer rows.Close()
	versions := make([]Version, 0, limit+1)
	for rows.Next() {
		version, err := scanVersion(rows)
		if err != nil {
			return nil, nil, fmt.Errorf("reading a tool version: %w", err)
		}
		versions = append(versions, *version)
	}
	if err := rows.Err(); err != nil {
		return nil, nil, fmt.Errorf("listing tool versions: %w", err)
	}
	var next *string
	if len(versions) > limit {
		last := versions[limit-1]
		token := catalog.EncodeCursor(last.CreatedAt, last.ID)
		next = &token
		versions = versions[:limit]
	}
	return versions, next, nil
}

// Get returns one visible tool: owner, administrator or any approved tool.
func (s *Service) Get(
	ctx context.Context,
	callerID, toolID string,
	admin bool,
) (*Version, error) {
	if !uuid.Valid(strings.TrimSpace(callerID)) || !uuid.Valid(strings.TrimSpace(toolID)) {
		return nil, ErrNotVisible
	}
	version, err := scanVersion(s.DB.QueryRowContext(ctx, `
		SELECT `+selectVersionColumns+`
		  FROM platform.tool_versions
		 WHERE id = $1::uuid
		   AND ($2::boolean OR owner_id = $3::uuid OR approved_at IS NOT NULL)`,
		toolID, admin, callerID))
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrNotVisible
	}
	if err != nil {
		return nil, fmt.Errorf("reading the tool version: %w", err)
	}
	return version, nil
}

// BuildFacts returns the non-contract build/review diagnostics for the owner or
// an administrator. An approved tool is readable by others, but its build
// artifacts stay owner-only, so this endpoint is deliberately narrower.
func (s *Service) BuildFacts(
	ctx context.Context,
	callerID, toolID string,
	admin bool,
) (*BuildFacts, error) {
	if !uuid.Valid(strings.TrimSpace(callerID)) || !uuid.Valid(strings.TrimSpace(toolID)) {
		return nil, ErrNotVisible
	}
	var (
		facts      BuildFacts
		sourceSHA  sql.NullString
		lockSHA    sql.NullString
		logID      sql.NullString
		sbomID     sql.NullString
		bundle     sql.NullString
		errorCode  sql.NullString
		startedAt  sql.NullTime
		finishedAt sql.NullTime
		decision   sql.NullString
		reason     sql.NullString
		reviewedAt sql.NullTime
		revokedAt  sql.NullTime
		revokeWhy  sql.NullString
	)
	err := s.DB.QueryRowContext(ctx, `
		SELECT id::text, state, revision, source_sha256, dependency_lock_sha256,
		       manifest_sha256, build_log_artifact_id::text, sbom_artifact_id::text,
		       build_bundle_digest, build_error_code, build_started_at, build_finished_at,
		       review_decision, review_reason, reviewed_at, revoked_at, revoke_reason
		  FROM platform.tool_versions
		 WHERE id = $1::uuid
		   AND (owner_id = $2::uuid OR $3::boolean)`,
		toolID, callerID, admin,
	).Scan(
		&facts.ToolVersionID, &facts.State, &facts.Revision, &sourceSHA, &lockSHA,
		&facts.ManifestSHA256, &logID, &sbomID, &bundle, &errorCode, &startedAt,
		&finishedAt, &decision, &reason, &reviewedAt, &revokedAt, &revokeWhy,
	)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrNotVisible
	}
	if err != nil {
		return nil, fmt.Errorf("reading the tool build facts: %w", err)
	}
	facts.SourceSHA256 = nullString(sourceSHA)
	facts.DependencyLockSHA = nullString(lockSHA)
	facts.BuildLogArtifactID = nullString(logID)
	facts.SBOMArtifactID = nullString(sbomID)
	facts.BuildBundleDigest = nullString(bundle)
	facts.BuildErrorCode = nullString(errorCode)
	facts.BuildStartedAt = nullTime(startedAt)
	facts.BuildFinishedAt = nullTime(finishedAt)
	facts.ReviewDecision = nullString(decision)
	facts.ReviewReason = nullString(reason)
	facts.ReviewedAt = nullTime(reviewedAt)
	facts.RevokedAt = nullTime(revokedAt)
	facts.RevokeReason = nullString(revokeWhy)
	return &facts, nil
}

// ResolveBuildBinding is the job-scoped read for a live tool_build attempt. It
// proves the tool row belongs to this exact job (target, owner and input
// artifact) and only then returns the canonical lock and source hash.
func (s *Service) ResolveBuildBinding(ctx context.Context, job *background.Job) (*BuildBinding, error) {
	if job == nil || job.Kind != background.KindToolBuild {
		return nil, &ConflictError{Code: "tool_state_conflict", Message: "this job is not a tool build"}
	}
	var (
		binding   BuildBinding
		sourceID  string
		ownerID   string
		sourceSHA sql.NullString
		lock      json.RawMessage
	)
	err := s.DB.QueryRowContext(ctx, `
		SELECT id::text, source_artifact_id::text, owner_id::text, source_sha256,
		       dependency_lock, manifest_sha256, state
		  FROM platform.tool_versions
		 WHERE id = $1::uuid`, job.TargetID,
	).Scan(
		&binding.ToolVersionID, &sourceID, &ownerID, &sourceSHA, &lock,
		&binding.ManifestSHA256, &binding.State,
	)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, &ConflictError{Code: "tool_state_conflict", Message: "the tool version no longer exists"}
	}
	if err != nil {
		return nil, fmt.Errorf("reading the tool build binding: %w", err)
	}
	if job.InputArtifactID == nil || *job.InputArtifactID != sourceID ||
		!strings.EqualFold(ownerID, job.OwnerID) ||
		!sourceSHA.Valid || !isSHA256Hex(strings.ToLower(sourceSHA.String)) {
		return nil, &ConflictError{
			Code:    "tool_state_conflict",
			Message: "the tool version does not match this job's source/owner",
		}
	}
	if binding.State != "submitted" && binding.State != "building" {
		return nil, &ConflictError{Code: "tool_state_conflict", Message: "the tool is not waiting for a build"}
	}
	binding.SourceArtifactID = sourceID
	binding.SourceSHA256 = strings.ToLower(sourceSHA.String)
	binding.DependencyLock = lock
	return &binding, nil
}

// ---------------------------------------------------------------------------
// review / revoke
// ---------------------------------------------------------------------------

// ReviewRequest is the exact contract reviewTool body.
type ReviewRequest struct {
	Decision       string `json:"decision"`
	ManifestSHA256 string `json:"manifest_sha256"`
	Reason         string `json:"reason"`
}

// Review approves or rejects one ready tool, bound to its manifest hash.
func (s *Service) Review(
	ctx context.Context,
	reviewerID, toolID string,
	request ReviewRequest,
) (*Version, error) {
	if !uuid.Valid(strings.TrimSpace(reviewerID)) || !uuid.Valid(strings.TrimSpace(toolID)) {
		return nil, ErrNotVisible
	}
	decision := strings.ToLower(strings.TrimSpace(request.Decision))
	if decision != "approve" && decision != "reject" {
		return nil, &ValidationError{Field: "decision", Message: "must be approve or reject"}
	}
	manifestSHA := strings.ToLower(strings.TrimSpace(request.ManifestSHA256))
	if !isSHA256Hex(manifestSHA) {
		return nil, &ValidationError{Field: "manifest_sha256", Message: "must be 64 hexadecimal characters"}
	}
	reason := strings.TrimSpace(request.Reason)
	if reason == "" || len(reason) > MaxReasonBytes {
		return nil, &ValidationError{
			Field:   "reason",
			Message: fmt.Sprintf("is required and must be at most %d characters", MaxReasonBytes),
		}
	}
	recordedDecision := "approved"
	if decision == "reject" {
		recordedDecision = "rejected"
	}

	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return nil, fmt.Errorf("starting the review transaction: %w", err)
	}
	defer func() { _ = tx.Rollback() }()

	var (
		state          string
		recordedSHA    string
		revision       int64
		existingDeci   sql.NullString
		existingReason sql.NullString
	)
	err = tx.QueryRowContext(ctx, `
		SELECT state, manifest_sha256, revision, review_decision, review_reason
		  FROM platform.tool_versions
		 WHERE id = $1::uuid
		 FOR UPDATE`, toolID,
	).Scan(&state, &recordedSHA, &revision, &existingDeci, &existingReason)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrNotVisible
	}
	if err != nil {
		return nil, fmt.Errorf("reading the tool version: %w", err)
	}
	if recordedSHA != manifestSHA {
		return nil, &ConflictError{
			Code:    "review_conflict",
			Message: "the submitted manifest_sha256 does not match the tool version; review cannot swap the hash",
		}
	}
	if state != "ready" {
		return nil, &ConflictError{
			Code:    "tool_state_conflict",
			Message: "only a ready (built) tool can be reviewed; the current state is " + state,
		}
	}
	if existingDeci.Valid {
		if existingDeci.String == recordedDecision &&
			strings.TrimSpace(existingReason.String) == reason {
			// Idempotent replay of the same decision: no second audit row.
			version, err := scanVersion(tx.QueryRowContext(ctx, `
				SELECT `+selectVersionColumns+` FROM platform.tool_versions
				 WHERE id = $1::uuid`, toolID))
			if err != nil {
				return nil, fmt.Errorf("reading the replayed tool version: %w", err)
			}
			if err := tx.Commit(); err != nil {
				return nil, fmt.Errorf("committing the idempotent review: %w", err)
			}
			return version, nil
		}
		return nil, &ConflictError{
			Code:    "review_conflict",
			Message: "a different review decision is already recorded for this tool",
		}
	}

	result, err := tx.ExecContext(ctx, `
		UPDATE platform.tool_versions
		   SET review_decision = $2,
		       review_reason = $3,
		       reviewed_by = $4::uuid,
		       reviewed_at = now(),
		       approved_by = CASE WHEN $2 = 'approved' THEN $4::uuid ELSE approved_by END,
		       approved_at = CASE WHEN $2 = 'approved' THEN now() ELSE approved_at END,
		       revision = revision + 1
		 WHERE id = $1::uuid AND state = 'ready' AND revision = $5
		   AND manifest_sha256 = $6`,
		toolID, recordedDecision, reason, reviewerID, revision, manifestSHA,
	)
	if err != nil {
		return nil, fmt.Errorf("recording the review decision: %w", err)
	}
	if affected, err := result.RowsAffected(); err != nil || affected != 1 {
		return nil, &ConflictError{Code: "review_conflict", Message: "the tool changed while reviewing"}
	}
	action := "tool.approved"
	if recordedDecision == "rejected" {
		action = "tool.rejected"
	}
	if err := audit.Record(ctx, tx, &reviewerID, action, "tool_version", &toolID, map[string]any{
		"decision":        recordedDecision,
		"manifest_sha256": manifestSHA,
		"reason":          reason,
	}); err != nil {
		return nil, err
	}
	version, err := scanVersion(tx.QueryRowContext(ctx, `
		SELECT `+selectVersionColumns+` FROM platform.tool_versions
		 WHERE id = $1::uuid`, toolID))
	if err != nil {
		return nil, fmt.Errorf("reading the reviewed tool version: %w", err)
	}
	if err := tx.Commit(); err != nil {
		return nil, fmt.Errorf("committing the review decision: %w", err)
	}
	return version, nil
}

// Revoke is the documented non-contract revocation. A revoked tool can never be
// used by new executions/dependency validation again (T025 reports
// dependency_revoked); its row and artifacts stay readable.
func (s *Service) Revoke(ctx context.Context, adminID, toolID, reason string) (*Version, error) {
	if !uuid.Valid(strings.TrimSpace(adminID)) || !uuid.Valid(strings.TrimSpace(toolID)) {
		return nil, ErrNotVisible
	}
	reason = strings.TrimSpace(reason)
	if reason == "" || len(reason) > MaxReasonBytes {
		return nil, &ValidationError{
			Field:   "reason",
			Message: fmt.Sprintf("is required and must be at most %d characters", MaxReasonBytes),
		}
	}
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return nil, fmt.Errorf("starting the revocation transaction: %w", err)
	}
	defer func() { _ = tx.Rollback() }()

	var (
		state    string
		revision int64
	)
	err = tx.QueryRowContext(ctx, `
		SELECT state, revision FROM platform.tool_versions
		 WHERE id = $1::uuid FOR UPDATE`, toolID,
	).Scan(&state, &revision)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrNotVisible
	}
	if err != nil {
		return nil, fmt.Errorf("reading the tool version: %w", err)
	}
	if state == "revoked" {
		version, err := scanVersion(tx.QueryRowContext(ctx, `
			SELECT `+selectVersionColumns+` FROM platform.tool_versions
			 WHERE id = $1::uuid`, toolID))
		if err != nil {
			return nil, fmt.Errorf("reading the revoked tool version: %w", err)
		}
		if err := tx.Commit(); err != nil {
			return nil, fmt.Errorf("committing the idempotent revocation: %w", err)
		}
		return version, nil
	}
	if state != "ready" {
		return nil, &ConflictError{
			Code:    "tool_state_conflict",
			Message: "only a ready (built) tool can be revoked; the current state is " + state,
		}
	}
	result, err := tx.ExecContext(ctx, `
		UPDATE platform.tool_versions
		   SET state = 'revoked', revoked_at = now(), revoked_by = $2::uuid,
		       revoke_reason = $3, revision = revision + 1
		 WHERE id = $1::uuid AND state = 'ready' AND revision = $4`,
		toolID, adminID, reason, revision,
	)
	if err != nil {
		return nil, fmt.Errorf("revoking the tool version: %w", err)
	}
	if affected, err := result.RowsAffected(); err != nil || affected != 1 {
		return nil, &ConflictError{Code: "tool_state_conflict", Message: "the tool changed while revoking"}
	}
	if err := audit.Record(ctx, tx, &adminID, "tool.revoked", "tool_version", &toolID, map[string]any{
		"reason": reason,
	}); err != nil {
		return nil, err
	}
	version, err := scanVersion(tx.QueryRowContext(ctx, `
		SELECT `+selectVersionColumns+` FROM platform.tool_versions
		 WHERE id = $1::uuid`, toolID))
	if err != nil {
		return nil, fmt.Errorf("reading the revoked tool version: %w", err)
	}
	if err := tx.Commit(); err != nil {
		return nil, fmt.Errorf("committing the revocation: %w", err)
	}
	return version, nil
}

// isSHA256Hex reports whether value is 64 lowercase hex characters.
func isSHA256Hex(value string) bool {
	if len(value) != 64 {
		return false
	}
	for index := 0; index < len(value); index++ {
		char := value[index]
		if (char < '0' || char > '9') && (char < 'a' || char > 'f') {
			return false
		}
	}
	return true
}

func nullString(value sql.NullString) *string {
	if !value.Valid {
		return nil
	}
	copied := value.String
	return &copied
}

func nullTime(value sql.NullTime) *time.Time {
	if !value.Valid {
		return nil
	}
	copied := value.Time
	return &copied
}

// normaliseConnectorVersionIDs canonicalises the declared connector list: UUIDs
// only, deduplicated and sorted, so the tool manifest identity is stable.
func normaliseConnectorVersionIDs(ids []string) ([]string, error) {
	seen := make(map[string]bool, len(ids))
	out := make([]string, 0, len(ids))
	for _, raw := range ids {
		id := strings.ToLower(strings.TrimSpace(raw))
		if !uuid.Valid(id) {
			return nil, &ValidationError{
				Field:   "connector_version_ids",
				Message: "each connector_version_id must be a UUID of an approved policy",
			}
		}
		if seen[id] {
			continue
		}
		seen[id] = true
		out = append(out, id)
	}
	sort.Strings(out)
	return out, nil
}

// requireConnectorPolicies refuses a submission that names a connector policy
// which is not approved or is revoked: a tool must never be built with a dead
// connector binding that the manifest would later reject.
func (s *Service) requireConnectorPolicies(ctx context.Context, tx *sql.Tx, ids []string) error {
	if len(ids) == 0 {
		return nil
	}
	var count int
	if err := tx.QueryRowContext(ctx, `
		SELECT count(*) FROM platform.connector_policies
		 WHERE id::text = ANY($1::text[])
		   AND approved_at IS NOT NULL AND revoked_at IS NULL`, ids).Scan(&count); err != nil {
		return fmt.Errorf("reading connector policies: %w", err)
	}
	if count != len(ids) {
		return &ValidationError{
			Field:   "connector_version_ids",
			Message: "every connector_version_id must be an approved, unrevoked connector policy",
		}
	}
	return nil
}

// canonicalConnectorIDs serialises the declared connector list as canonical JCS
// bytes (a JSON string array). It is part of the tool content identity: binding
// a tool to a different connector set is a different tool version.
func canonicalConnectorIDs(ids []string) ([]byte, error) {
	if ids == nil {
		ids = []string{}
	}
	encoded, err := json.Marshal(ids)
	if err != nil {
		return nil, &ValidationError{Field: "connector_version_ids", Message: "must be a list of UUIDs"}
	}
	canonical, err := jcs.CanonicalizeBytes(encoded)
	if err != nil {
		return nil, &ValidationError{Field: "connector_version_ids", Message: "must be a list of UUIDs"}
	}
	return canonical, nil
}
