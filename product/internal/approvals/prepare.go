package approvals

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"strings"
	"time"

	"orchara/platform/internal/jcs"
	"orchara/platform/internal/objectstore"
	"orchara/platform/internal/uuid"
)

// PrepareRecord is everything the capability proxy already proved about one
// parked operation. Every field is a server fact: the run, attempt, manifest,
// operation and policy rows plus the canonical params bytes.
type PrepareRecord struct {
	RunID             string
	OperationID       string
	LogicalKey        string
	AttemptToken      string
	OwnerID           string
	ParamsSHA256      string
	Params            json.RawMessage
	CapabilityKind    string
	CapabilityVersion string
	RiskClass         string
	Target            Target
	DataDestination   string
	PolicySHA256      string
	ManifestSHA256    string
	SnapshotID        string
	SnapshotSHA256    string
	ExpiresAt         time.Time
}

// PrepareRecordTx creates (or returns) the one pending approval for a parked
// operation inside the caller's transaction, so the operation row and its
// approval cannot disagree: either both exist or neither does.
//
// The proposal bytes are built here from the record, never from worker text, and
// sealed with the designated approver as the artifact owner (the F04A run owner
// equals the approver, so the existing artifact-content ACL already gives exactly
// the right human access).
func (s *Service) PrepareRecordTx(
	ctx context.Context, tx *sql.Tx, record PrepareRecord,
) (*Approval, error) {
	record.RunID = strings.TrimSpace(record.RunID)
	record.OperationID = strings.TrimSpace(record.OperationID)
	record.OwnerID = strings.TrimSpace(record.OwnerID)
	record.LogicalKey = strings.TrimSpace(record.LogicalKey)
	if !uuid.Valid(record.RunID) || !uuid.Valid(record.OperationID) {
		return nil, failure(422, "validation_failed", "run_id and operation_id must be UUIDs")
	}
	if record.CapabilityKind != "connector" || record.RiskClass != "write" {
		return nil, failure(422, "approval_not_applicable",
			"only a write-risk connector operation requires a run approval")
	}
	if record.ParamsSHA256 == "" || len(record.Params) == 0 {
		return nil, failure(500, "internal_error", "the parked operation has no canonical params")
	}
	existing, err := approvalByOperationTx(ctx, tx, record.OperationID)
	if err != nil {
		return nil, err
	}
	if existing != nil {
		if existing.ParamsSHA256 != record.ParamsSHA256 {
			return nil, failure(409, "operation_conflict",
				"that operation already has an approval for different parameters")
		}
		return existing, nil
	}

	approverID, err := approverForOwnerTx(ctx, tx, record.OwnerID)
	if err != nil {
		return nil, err
	}
	expires := expiresAt(record.ExpiresAt)
	proposal := Proposal{
		ApprovalVersion:   1,
		RunID:             record.RunID,
		OperationID:       record.OperationID,
		LogicalKey:        record.LogicalKey,
		NodeKey:           nodeKeyForLogicalKey(record.LogicalKey),
		CapabilityKind:    record.CapabilityKind,
		CapabilityVersion: record.CapabilityVersion,
		RiskClass:         record.RiskClass,
		Target:            record.Target,
		Params:            record.Params,
		ParamsSHA256:      record.ParamsSHA256,
		DataDestination:   record.DataDestination,
		PolicySHA256:      record.PolicySHA256,
		ManifestSHA256:    record.ManifestSHA256,
		SnapshotID:        record.SnapshotID,
		SnapshotSHA256:    record.SnapshotSHA256,
		ExpiresAt:         expires.Format(time.RFC3339),
	}
	artifactID, err := s.sealProposalTx(ctx, tx, approverID, record, expires)
	if err != nil {
		return nil, err
	}
	approvalID := uuid.NewString()
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO platform.approvals
			(id, run_id, operation_id, node_key, proposal_artifact_id, params_sha256,
			 approver_id, state, expires_at)
		VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $5::uuid, $6, $7::uuid, 'pending', $8)`,
		approvalID, record.RunID, record.OperationID, proposal.NodeKey, artifactID,
		record.ParamsSHA256, approverID, expires,
	); err != nil {
		if isUniqueViolation(err) {
			return nil, failure(409, "operation_conflict",
				"an approval already exists for this run, node and parameters")
		}
		return nil, fmt.Errorf("writing the approval row: %w", err)
	}
	return &Approval{
		ID: approvalID, RunID: record.RunID, State: "pending",
		ProposalArtifactID: artifactID, ParamsSHA256: record.ParamsSHA256,
		ExpiresAt: expires, ApproverID: approverID, OperationID: record.OperationID,
	}, nil
}

// canonicalProposal renders the closed proposal document as RFC 8785 JCS bytes.
// The bytes are what the page reads and what the hash chain covers.
func canonicalProposal(proposal Proposal) ([]byte, error) {
	if len(proposal.Params) == 0 {
		proposal.Params = json.RawMessage(`{}`)
	}
	var object map[string]any
	if err := json.Unmarshal(proposal.Params, &object); err != nil {
		return nil, failure(500, "internal_error", "the canonical params are not a JSON object")
	}
	raw, err := json.Marshal(proposal)
	if err != nil {
		return nil, fmt.Errorf("encoding the approval proposal: %w", err)
	}
	canonical, err := jcs.CanonicalizeBytes(raw)
	if err != nil {
		return nil, fmt.Errorf("canonicalising the approval proposal: %w", err)
	}
	return canonical, nil
}

// sealProposalTx writes the sealed proposal object and its artifact/run links.
func (s *Service) sealProposalTx(
	ctx context.Context, tx *sql.Tx, ownerID string, record PrepareRecord, expires time.Time,
) (string, error) {
	proposal := Proposal{
		ApprovalVersion:   1,
		RunID:             record.RunID,
		OperationID:       record.OperationID,
		LogicalKey:        record.LogicalKey,
		NodeKey:           nodeKeyForLogicalKey(record.LogicalKey),
		CapabilityKind:    record.CapabilityKind,
		CapabilityVersion: record.CapabilityVersion,
		RiskClass:         record.RiskClass,
		Target:            record.Target,
		Params:            record.Params,
		ParamsSHA256:      record.ParamsSHA256,
		DataDestination:   record.DataDestination,
		PolicySHA256:      record.PolicySHA256,
		ManifestSHA256:    record.ManifestSHA256,
		SnapshotID:        record.SnapshotID,
		SnapshotSHA256:    record.SnapshotSHA256,
		ExpiresAt:         expires.Format(time.RFC3339),
	}
	canonical, err := canonicalProposal(proposal)
	if err != nil {
		return "", err
	}
	if s.Objects == nil {
		return "", failure(503, "object_store_unavailable", "the object store is not configured")
	}
	object, err := s.Objects.CreateSealed(bytes.NewReader(canonical), objectstore.MaxObjectBytes)
	if err != nil {
		return "", failure(503, "object_store_unavailable", "the approval proposal could not be sealed")
	}
	artifactID := uuid.NewString()
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO platform.artifacts
			(id, owner_id, object_key, purpose, sha256, media_type, size_bytes, state, revision)
		VALUES ($1::uuid, $2::uuid, $3, 'approval_proposal', $4, 'application/json', $5, 'ready', 1)`,
		artifactID, ownerID, object.Key, object.SHA256, object.Size,
	); err != nil {
		return "", fmt.Errorf("writing the proposal artifact: %w", err)
	}
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO platform.run_artifacts (run_id, artifact_id, attempt_token, relation)
		VALUES ($1::uuid, $2::uuid, $3::uuid, 'approval_proposal')
		ON CONFLICT (run_id, artifact_id) DO NOTHING`,
		record.RunID, artifactID, record.AttemptToken,
	); err != nil {
		return "", fmt.Errorf("linking the proposal artifact to the run: %w", err)
	}
	return artifactID, nil
}

func approvalByOperationTx(ctx context.Context, tx *sql.Tx, operationID string) (*Approval, error) {
	return approvalFromRow(tx.QueryRowContext(ctx, `
		SELECT `+approvalColumns+` FROM platform.approvals
		 WHERE operation_id = $1::uuid FOR UPDATE`, operationID))
}

// PrepareRequest is the contract's `prepareApproval` body. `proposal` is accepted
// for schema compatibility but never trusted or copied: the server rebuilds the
// proposal from the operation and policy rows.
type PrepareRequest struct {
	RunID        string
	AttemptToken string
	LogicalKey   string
	OperationID  string
}

// Prepare is the internal worker endpoint (contract operationId prepareApproval).
// It proves the live attempt, that the operation belongs to this run and that a
// pending approval for the same params already exists (the capability proxy is
// the normal creator); otherwise it builds the same server proposal from the
// database facts.
func (s *Service) Prepare(ctx context.Context, request PrepareRequest) (*Approval, error) {
	request.RunID = strings.TrimSpace(request.RunID)
	request.AttemptToken = strings.ToLower(strings.TrimSpace(request.AttemptToken))
	request.OperationID = strings.TrimSpace(request.OperationID)
	request.LogicalKey = strings.TrimSpace(request.LogicalKey)
	if !uuid.Valid(request.RunID) {
		return nil, failure(404, CodeNotFound, "that run does not exist")
	}
	if !uuid.Valid(request.AttemptToken) {
		return nil, failure(403, "stale_attempt", "the attempt token is not a live lease of this run")
	}
	if !uuid.Valid(request.OperationID) || request.LogicalKey == "" {
		return nil, &Error{Status: 422, Code: "validation_failed",
			Message: "operation_id must be a UUID and logical_key must be non-empty"}
	}

	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return nil, fmt.Errorf("starting the approval transaction: %w", err)
	}
	defer func() { _ = tx.Rollback() }()

	record, err := s.loadPrepareRecordTx(ctx, tx, request)
	if err != nil {
		return nil, err
	}
	approval, err := s.PrepareRecordTx(ctx, tx, record)
	if err != nil {
		return nil, err
	}
	if _, err := tx.ExecContext(ctx, `
		UPDATE platform.operations
		   SET state = 'awaiting_approval', updated_at = now()
		 WHERE id = $1::uuid AND state = 'authorized'`, record.OperationID,
	); err != nil {
		return nil, fmt.Errorf("parking the operation: %w", err)
	}
	if err := tx.Commit(); err != nil {
		return nil, fmt.Errorf("committing the approval transaction: %w", err)
	}
	return approval, nil
}

// loadPrepareRecordTx rebuilds the server proposal facts from the durable rows.
func (s *Service) loadPrepareRecordTx(
	ctx context.Context, tx *sql.Tx, request PrepareRequest,
) (PrepareRecord, error) {
	var (
		record      PrepareRecord
		runState    string
		cancel      sql.NullTime
		snapshotID  string
		snapshotSHA string
		liveAttempt bool
	)
	err := tx.QueryRowContext(ctx, `
		SELECT r.owner_id::text, r.state, r.cancel_requested_at, r.manifest_sha256,
		       r.execution_snapshot_id::text,
		       EXISTS (
		           SELECT 1 FROM platform.run_attempts AS at
		             JOIN platform.jobs AS j ON j.id = at.job_id AND j.run_id = at.run_id
		            WHERE at.token = $2::uuid AND at.run_id = r.id
		              AND at.exited_at IS NULL AND at.revoked_at IS NULL
		              AND at.lease_deadline > now()
		              AND j.current_attempt_token = at.token)
		  FROM platform.runs AS r
		 WHERE r.id = $1::uuid`, request.RunID, request.AttemptToken,
	).Scan(&record.OwnerID, &runState, &cancel, &record.ManifestSHA256, &snapshotID, &liveAttempt)
	if errors.Is(err, sql.ErrNoRows) {
		return record, failure(404, CodeNotFound, "that run does not exist")
	}
	if err != nil {
		return record, fmt.Errorf("reading the run: %w", err)
	}
	if !liveAttempt {
		return record, failure(403, "stale_attempt", "the attempt token is not a live lease of this run")
	}
	if cancel.Valid {
		return record, failure(409, "run_cancelled", "the run was cancelled; no approval may be prepared")
	}
	if runState != "running" && runState != "waiting_approval" {
		return record, failure(409, "run_state_conflict", "the run is not executing this operation")
	}
	if err := tx.QueryRowContext(ctx, `
		SELECT manifest_sha256 FROM platform.execution_snapshots WHERE id = $1::uuid`,
		snapshotID).Scan(&snapshotSHA); err != nil {
		return record, fmt.Errorf("reading the execution snapshot: %w", err)
	}
	record.RunID = request.RunID
	record.SnapshotID = snapshotID
	record.SnapshotSHA256 = snapshotSHA

	var (
		runID, logicalKey, kind, version, risk, paramsSHA, paramsArtifactID, opState string
	)
	err = tx.QueryRowContext(ctx, `
		SELECT run_id::text, logical_key, capability_kind, capability_version,
		       risk_class, params_sha256, params_artifact_id::text, state
		  FROM platform.operations WHERE id = $1::uuid FOR UPDATE`,
		request.OperationID,
	).Scan(&runID, &logicalKey, &kind, &version, &risk, &paramsSHA, &paramsArtifactID, &opState)
	if errors.Is(err, sql.ErrNoRows) {
		return record, failure(404, CodeNotFound, "that operation does not exist")
	}
	if err != nil {
		return record, fmt.Errorf("reading the operation: %w", err)
	}
	if runID != request.RunID || logicalKey != request.LogicalKey {
		return record, failure(404, CodeNotFound, "that operation does not belong to this run")
	}
	if kind != "connector" || risk != "write" {
		return record, failure(422, "approval_not_applicable",
			"only a write-risk connector operation requires a run approval")
	}
	if opState != "awaiting_approval" && opState != "authorized" && opState != "prepared" {
		return record, failure(409, CodeNotPending, "the operation is not awaiting approval")
	}
	record.OperationID = request.OperationID
	record.LogicalKey = logicalKey
	record.AttemptToken = request.AttemptToken
	record.CapabilityKind = kind
	record.CapabilityVersion = version
	record.RiskClass = risk
	record.ParamsSHA256 = paramsSHA

	params, err := s.readParamsArtifactTx(ctx, tx, paramsArtifactID, paramsSHA)
	if err != nil {
		return record, err
	}
	record.Params = params

	var (
		origin, method, pathPrefix, policySHA string
	)
	err = tx.QueryRowContext(ctx, `
		SELECT origin, method, path_prefix, policy_sha256
		  FROM platform.connector_policies WHERE id = $1::uuid`, version,
	).Scan(&origin, &method, &pathPrefix, &policySHA)
	if errors.Is(err, sql.ErrNoRows) {
		return record, failure(422, "policy_unavailable", "the connector policy does not exist")
	}
	if err != nil {
		return record, fmt.Errorf("reading the connector policy: %w", err)
	}
	var paramsObject struct {
		Path  string         `json:"path"`
		Query map[string]any `json:"query"`
	}
	if err := json.Unmarshal(params, &paramsObject); err != nil {
		return record, failure(500, "internal_error", "the canonical params are not a JSON object")
	}
	query := map[string]string{}
	for name, raw := range paramsObject.Query {
		value, ok := raw.(string)
		if !ok {
			return record, failure(500, "internal_error", "the canonical params query is not strings")
		}
		query[name] = value
	}
	if len(query) == 0 {
		query = nil
	}
	record.Target = Target{
		Origin: origin, Method: method, PathPrefix: pathPrefix, Path: paramsObject.Path, Query: query,
	}
	record.DataDestination = origin
	record.PolicySHA256 = policySHA
	return record, nil
}

// readParamsArtifactTx reads the sealed canonical params bytes and proves the
// recorded hash instead of trusting a caller-supplied projection.
func (s *Service) readParamsArtifactTx(
	ctx context.Context, tx *sql.Tx, artifactID, expectedSHA string,
) (json.RawMessage, error) {
	var objectKey, digest string
	if err := tx.QueryRowContext(ctx, `
		SELECT object_key, COALESCE(sha256, '') FROM platform.artifacts
		 WHERE id = $1::uuid AND state = 'ready' AND deleted_at IS NULL`, artifactID,
	).Scan(&objectKey, &digest); err != nil {
		return nil, fmt.Errorf("reading the params artifact: %w", err)
	}
	if s.Objects == nil {
		return nil, failure(503, "object_store_unavailable", "the object store is not configured")
	}
	reader, _, err := s.Objects.Open(objectKey)
	if err != nil {
		return nil, fmt.Errorf("opening the params artifact: %w", err)
	}
	defer func() { _ = reader.Close() }()
	content, err := io.ReadAll(io.LimitReader(reader, objectstore.MaxObjectBytes+1))
	if err != nil {
		return nil, fmt.Errorf("reading the params artifact: %w", err)
	}
	if int64(len(content)) > objectstore.MaxObjectBytes {
		return nil, failure(500, "internal_error", "the params artifact is too large")
	}
	if !strings.EqualFold(digest, expectedSHA) || !json.Valid(content) {
		return nil, failure(500, "internal_error", "the params artifact does not match the operation")
	}
	return json.RawMessage(content), nil
}

// isUniqueViolation keeps the approval insert mapping local (the capability
// package's helper is unexported and a second SQLSTATE check is cheap).
func isUniqueViolation(err error) bool {
	type sqlState interface{ SQLState() string }
	var state sqlState
	if errors.As(err, &state) {
		return state.SQLState() == "23505"
	}
	return false
}
