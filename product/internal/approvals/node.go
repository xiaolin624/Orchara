package approvals

// F04B/T087: the human approval node's server-built record.
//
// A node approval is a *business decision*, not a capability authorization:
//
//   - the row is `platform.approvals` with `operation_id NULL` and
//     `node_key = "node:<node_id>"`; no `platform.operations` row is created and
//     no capability grant is written;
//   - the proposal is sealed by the server from server facts (the run's sealed
//     definition, the frozen manifest/snapshot identities, the node config) plus
//     the node's bound `proposal` object; the worker never supplies the title or
//     the decision;
//   - `params_sha256` is SHA-256 over the RFC 8785 JCS canonical proposal object,
//     so the decision path's exact-hash rule applies unchanged;
//   - the row is idempotent for `UNIQUE(run_id, node_key, params_sha256)`: a replay
//     of the same proposal returns the original row in whatever state it has
//     (pending/approved/rejected/expired/invalidated). A *changed* proposal is a
//     different approval and gets its own row: the schema's uniqueness key is the
//     canonical proposal, and the old decision must never apply to new content.
//
// A later write tool still needs its own operation-level approval at the
// capability proxy; approving this node never grants one.

import (
	"bytes"
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"strings"
	"time"
	"unicode/utf8"

	"orchara/platform/internal/execution"
	"orchara/platform/internal/jcs"
	"orchara/platform/internal/objectstore"
	"orchara/platform/internal/uuid"
)

const (
	// MaxNodeProposalBytes bounds the bound `proposal` object of one node
	// approval (it is a human-facing business payload, not a data channel).
	MaxNodeProposalBytes = 64 << 10
	// MaxNodeTitleRunes bounds the display title fixed by the node config.
	MaxNodeTitleRunes = 200
	// nodeApprovalKind is the sealed proposal's document discriminator. The
	// approval page renders a node approval differently from a capability one
	// without any contract change.
	nodeApprovalKind = "node_approval"
)

// NodeProposal is the sealed server document the approval page renders for a
// node approval. Every member is a server fact; the only worker-influenced
// member is the bound `proposal` object, which is exactly what the human sees
// and what `params_sha256` covers.
type NodeProposal struct {
	ApprovalVersion int             `json:"approval_version"`
	Kind            string          `json:"kind"`
	RunID           string          `json:"run_id"`
	NodeID          string          `json:"node_id"`
	NodeKey         string          `json:"node_key"`
	Title           string          `json:"title"`
	Proposal        json.RawMessage `json:"proposal"`
	ParamsSHA256    string          `json:"params_sha256"`
	ManifestSHA256  string          `json:"manifest_sha256"`
	SnapshotID      string          `json:"snapshot_id"`
	SnapshotSHA256  string          `json:"snapshot_sha256"`
	DefinitionNodes []string        `json:"definition_nodes"`
	ExpiresAt       string          `json:"expires_at"`
}

// PrepareNodeRecord is the internal worker route's body after validation. Every
// identity member is re-proved against the run's sealed snapshot; nothing here is
// trusted as an authority.
type PrepareNodeRecord struct {
	RunID          string
	AttemptToken   string
	NodeID         string
	Title          string
	ExpiresSeconds int
	Proposal       json.RawMessage
}

// nodeRunFacts is the locked Run/snapshot prefix a node approval is built from.
type nodeRunFacts struct {
	OwnerID            string
	State              string
	Cancel             sql.NullTime
	ManifestSHA256     string
	SnapshotID         string
	SnapshotSHA256     string
	ManifestArtifactID string
}

// nodeDefinitionNode is the subset of one sealed definition node this service
// needs: the identity, type and config. The config is the authority for the
// human-facing title and the approval window; the worker request cannot change
// either.
type nodeDefinitionNode struct {
	ID     string          `json:"id"`
	Type   string          `json:"type"`
	Config json.RawMessage `json:"config"`
}

// PrepareNode prepares (or replays) the one approval of this run's approval node.
//
// Validation order:
//  1. shape/UUID/expiry/title/proposal size (422/413) before any row is touched;
//  2. the locked Run + live attempt token (404/403/409);
//  3. the sealed definition proves the node exists and is an `approval` node;
//  4. `(run_id, node_key, params_sha256)` replay returns the original row;
//  5. otherwise one transaction seals the proposal artifact, inserts the approval
//     row and appends the durable `approval.required` event.
func (s *Service) PrepareNode(
	ctx context.Context, request PrepareNodeRecord,
) (*Approval, error) {
	record, canonical, paramsSHA, err := validatePrepareNode(request)
	if err != nil {
		return nil, err
	}

	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return nil, fmt.Errorf("starting the node approval transaction: %w", err)
	}
	defer func() { _ = tx.Rollback() }()

	facts, err := loadNodeRunFactsTx(ctx, tx, record.RunID, record.AttemptToken)
	if err != nil {
		return nil, err
	}
	definitionNodes, err := s.readSealedDefinitionTx(ctx, tx, facts)
	if err != nil {
		return nil, err
	}
	nodes, err := requireApprovalNode(definitionNodes, record.NodeID)
	if err != nil {
		return nil, err
	}
	// The worker's request only carries the run/attempt/node identity. The
	// display title and the approval window are read from the sealed definition,
	// so a live attempt token cannot rewrite the human-facing proposal or extend
	// its own deadline.
	sealedTitle, sealedExpires, err := approvalNodeSettings(definitionNodes, record.NodeID)
	if err != nil {
		return nil, err
	}
	record.Title = sealedTitle
	record.ExpiresSeconds = sealedExpires
	nodeKey := "node:" + record.NodeID
	existing, err := nodeApprovalByKeyTx(ctx, tx, record.RunID, nodeKey, paramsSHA)
	if err != nil {
		return nil, err
	}
	if existing != nil {
		return existing, nil
	}

	approverID, err := approverForOwnerTx(ctx, tx, facts.OwnerID)
	if err != nil {
		return nil, err
	}
	expires := time.Now().UTC().Add(time.Duration(record.ExpiresSeconds) * time.Second)
	artifactID, err := s.sealNodeProposalTx(
		ctx, tx, approverID, facts, record, nodes, canonical, paramsSHA, expires,
	)
	if err != nil {
		return nil, err
	}
	approvalID := uuid.NewString()
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO platform.approvals
			(id, run_id, operation_id, node_key, proposal_artifact_id, params_sha256,
			 approver_id, state, expires_at)
		VALUES ($1::uuid, $2::uuid, NULL, $3, $4::uuid, $5, $6::uuid, 'pending', $7)`,
		approvalID, record.RunID, nodeKey, artifactID, paramsSHA, approverID, expires,
	); err != nil {
		if isUniqueViolation(err) {
			// A concurrent prepare won the same canonical proposal. The whole
			// transaction (including its artifact) rolls back; answer with the
			// winner instead of a second approval or a 409.
			return s.nodeApprovalByKey(ctx, record.RunID, nodeKey, paramsSHA)
		}
		return nil, fmt.Errorf("writing the node approval row: %w", err)
	}
	if err := execution.AppendApprovalRequiredTx(
		ctx, tx, record.RunID, approvalID, expires,
	); err != nil {
		return nil, fmt.Errorf("appending the approval.required event: %w", err)
	}
	if err := tx.Commit(); err != nil {
		return nil, fmt.Errorf("committing the node approval transaction: %w", err)
	}
	return &Approval{
		ID: approvalID, RunID: record.RunID, State: "pending",
		ProposalArtifactID: artifactID, ParamsSHA256: paramsSHA,
		ExpiresAt: expires, ApproverID: approverID, NodeKey: nodeKey,
	}, nil
}

// validatePrepareNode normalises the request and proves the parts that need no
// database: identities, the display title, the expiry window and the proposal
// object/size/canonical hash.
func validatePrepareNode(request PrepareNodeRecord) (PrepareNodeRecord, []byte, string, error) {
	record := PrepareNodeRecord{
		RunID:          strings.TrimSpace(request.RunID),
		AttemptToken:   strings.ToLower(strings.TrimSpace(request.AttemptToken)),
		NodeID:         strings.TrimSpace(request.NodeID),
		Title:          strings.TrimSpace(request.Title),
		ExpiresSeconds: request.ExpiresSeconds,
		Proposal:       request.Proposal,
	}
	if !uuid.Valid(record.RunID) {
		return record, nil, "", failure(404, CodeNotFound, "that run does not exist")
	}
	if !uuid.Valid(record.AttemptToken) {
		return record, nil, "", failure(403, "stale_attempt",
			"the attempt token is not a live lease of this run")
	}
	if record.NodeID == "" || len(record.NodeID) > 256 || !printableNodeID(record.NodeID) {
		return record, nil, "", &Error{Status: 422, Code: "validation_failed",
			Message: "node_id must be a printable node identity of at most 256 characters",
			Field:   "node_id"}
	}
	if record.Title == "" || utf8.RuneCountInString(record.Title) > MaxNodeTitleRunes {
		return record, nil, "", &Error{Status: 422, Code: "validation_failed",
			Message: fmt.Sprintf("title must be 1..%d characters", MaxNodeTitleRunes),
			Field:   "title"}
	}
	if record.ExpiresSeconds < 60 || record.ExpiresSeconds > 86400 {
		return record, nil, "", &Error{Status: 422, Code: "validation_failed",
			Message: "expires_seconds must be between 60 and 86400", Field: "expires_seconds"}
	}
	if len(record.Proposal) == 0 || !json.Valid(record.Proposal) {
		return record, nil, "", &Error{Status: 422, Code: "validation_failed",
			Message: "the proposal must be a JSON object", Field: "proposal"}
	}
	if len(record.Proposal) > MaxNodeProposalBytes {
		return record, nil, "", &Error{Status: 413, Code: "payload_too_large",
			Message: fmt.Sprintf("the proposal must be at most %d bytes", MaxNodeProposalBytes),
			Field:   "proposal"}
	}
	var object map[string]any
	if err := json.Unmarshal(record.Proposal, &object); err != nil {
		return record, nil, "", &Error{Status: 422, Code: "validation_failed",
			Message: "the proposal must be a JSON object", Field: "proposal"}
	}
	canonical, err := jcs.CanonicalizeBytes(record.Proposal)
	if err != nil {
		return record, nil, "", &Error{Status: 422, Code: "validation_failed",
			Message: "the proposal cannot be canonicalised", Field: "proposal"}
	}
	sum := sha256.Sum256(canonical)
	return record, canonical, hex.EncodeToString(sum[:]), nil
}

// printableNodeID keeps control characters out of the node identity that lands in
// `node_key` and in the sealed document.
func printableNodeID(value string) bool {
	for _, char := range value {
		if char < 0x20 || char == 0x7f {
			return false
		}
	}
	return true
}

// loadNodeRunFactsTx locks the Run and proves the attempt token is a live lease of
// it; it also reads the frozen manifest/snapshot identities.
func loadNodeRunFactsTx(
	ctx context.Context, tx *sql.Tx, runID, attemptToken string,
) (*nodeRunFacts, error) {
	var (
		facts       nodeRunFacts
		liveAttempt bool
	)
	err := tx.QueryRowContext(ctx, `
		SELECT r.owner_id::text, r.state, r.cancel_requested_at, r.manifest_sha256,
		       r.execution_snapshot_id::text, s.manifest_sha256,
		       s.manifest_artifact_id::text,
		       EXISTS (
		           SELECT 1 FROM platform.run_attempts AS at
		             JOIN platform.jobs AS j ON j.id = at.job_id AND j.run_id = at.run_id
		            WHERE at.token = $2::uuid AND at.run_id = r.id
		              AND at.exited_at IS NULL AND at.revoked_at IS NULL
		              AND at.lease_deadline > now()
		              AND j.current_attempt_token = at.token)
		  FROM platform.runs AS r
		  JOIN platform.execution_snapshots AS s ON s.id = r.execution_snapshot_id
		 WHERE r.id = $1::uuid
		   AND r.deleted_at IS NULL
		 FOR UPDATE OF r`, runID, attemptToken,
	).Scan(
		&facts.OwnerID, &facts.State, &facts.Cancel, &facts.ManifestSHA256,
		&facts.SnapshotID, &facts.SnapshotSHA256, &facts.ManifestArtifactID, &liveAttempt,
	)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, failure(404, CodeNotFound, "that run does not exist")
	}
	if err != nil {
		return nil, fmt.Errorf("reading the run: %w", err)
	}
	if !liveAttempt {
		return nil, failure(403, "stale_attempt",
			"the attempt token is not a live lease of this run")
	}
	if facts.Cancel.Valid {
		return nil, failure(409, "run_cancelled",
			"the run was cancelled; no approval may be prepared")
	}
	if facts.State != "running" && facts.State != "waiting_approval" {
		return nil, failure(409, "run_state_conflict", "the run is not executing this node")
	}
	if !strings.EqualFold(facts.ManifestSHA256, facts.SnapshotSHA256) {
		return nil, failure(500, "internal_error",
			"the run and execution snapshot manifest digests disagree")
	}
	return &facts, nil
}

// readSealedDefinitionTx reads the frozen manifest artifact and proves its bytes
// hash to the recorded manifest digest before trusting the definition. The JSONB
// projection on the snapshot is not the authority.
func (s *Service) readSealedDefinitionTx(
	ctx context.Context, tx *sql.Tx, facts *nodeRunFacts,
) ([]nodeDefinitionNode, error) {
	var objectKey, digest string
	err := tx.QueryRowContext(ctx, `
		SELECT object_key, COALESCE(sha256, '') FROM platform.artifacts
		 WHERE id = $1::uuid AND state = 'ready' AND deleted_at IS NULL`,
		facts.ManifestArtifactID,
	).Scan(&objectKey, &digest)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, failure(503, "manifest_unavailable",
			"the sealed execution manifest is not readable")
	}
	if err != nil {
		return nil, fmt.Errorf("reading the manifest artifact: %w", err)
	}
	if !strings.EqualFold(digest, facts.ManifestSHA256) {
		return nil, failure(503, "manifest_unavailable",
			"the manifest artifact does not match the run's manifest digest")
	}
	if s.Objects == nil {
		return nil, failure(503, "object_store_unavailable",
			"the object store is not configured")
	}
	reader, _, err := s.Objects.Open(objectKey)
	if err != nil {
		return nil, failure(503, "manifest_unavailable",
			"the sealed execution manifest could not be read")
	}
	defer func() { _ = reader.Close() }()
	payload, err := io.ReadAll(io.LimitReader(reader, objectstore.MaxObjectBytes+1))
	if err != nil || int64(len(payload)) > objectstore.MaxObjectBytes {
		return nil, failure(503, "manifest_unavailable",
			"the sealed execution manifest is too large or unreadable")
	}
	sum := sha256.Sum256(payload)
	if !strings.EqualFold(hex.EncodeToString(sum[:]), facts.ManifestSHA256) {
		return nil, failure(503, "manifest_unavailable",
			"the sealed manifest bytes do not match the run digest")
	}
	var manifest struct {
		Definition struct {
			Nodes []nodeDefinitionNode `json:"nodes"`
		} `json:"definition"`
	}
	if err := json.Unmarshal(payload, &manifest); err != nil {
		return nil, failure(503, "manifest_unavailable",
			"the sealed execution manifest is not valid JSON")
	}
	return manifest.Definition.Nodes, nil
}

// requireApprovalNode proves the node exists in the sealed definition and is an
// approval node, returning the definition's node ids for the sealed document.
func requireApprovalNode(
	nodes []nodeDefinitionNode, nodeID string,
) ([]string, error) {
	ids := make([]string, 0, len(nodes))
	found := false
	nodeType := ""
	for _, node := range nodes {
		ids = append(ids, node.ID)
		if node.ID == nodeID {
			found = true
			nodeType = node.Type
		}
	}
	if !found {
		return nil, &Error{Status: 422, Code: "validation_failed",
			Message: "the node is not part of this run's sealed definition",
			Field:   "node_id"}
	}
	if nodeType != "approval" {
		return nil, &Error{Status: 422, Code: "validation_failed",
			Message: "the node is not an approval node in this run's sealed definition",
			Field:   "node_id"}
	}
	return ids, nil
}

// nodeApprovalSettings is the sealed config of one approval node: the only
// authority for the human-facing title and the approval window.
type nodeApprovalSettings struct {
	Title          string `json:"title"`
	ExpiresSeconds int    `json:"expires_seconds"`
}

// approvalNodeSettings reads the title and expiry from the sealed definition.
// A sealed config that fails the contract bounds is a manifest problem, never a
// reason to fall back to the worker request.
func approvalNodeSettings(nodes []nodeDefinitionNode, nodeID string) (string, int, error) {
	for _, node := range nodes {
		if node.ID != nodeID {
			continue
		}
		var settings nodeApprovalSettings
		if len(node.Config) == 0 || json.Unmarshal(node.Config, &settings) != nil {
			return "", 0, failure(503, "manifest_unavailable",
				"the sealed approval node config is not the documented object")
		}
		title := strings.TrimSpace(settings.Title)
		if title == "" || utf8.RuneCountInString(title) > MaxNodeTitleRunes {
			return "", 0, failure(503, "manifest_unavailable",
				"the sealed approval node title is empty or too long")
		}
		if settings.ExpiresSeconds < 60 || settings.ExpiresSeconds > 86400 {
			return "", 0, failure(503, "manifest_unavailable",
				"the sealed approval node expiry is outside the contract window")
		}
		return title, settings.ExpiresSeconds, nil
	}
	return "", 0, &Error{Status: 422, Code: "validation_failed",
		Message: "the node is not part of this run's sealed definition",
		Field:   "node_id"}
}

// sealNodeProposalTx writes the sealed node proposal object and its run link. The
// artifact owner is the designated approver, so the existing artifact-content ACL
// gives exactly the right human access.
func (s *Service) sealNodeProposalTx(
	ctx context.Context, tx *sql.Tx, approverID string, facts *nodeRunFacts,
	record PrepareNodeRecord, definitionNodes []string, canonicalProposal []byte,
	paramsSHA string, expires time.Time,
) (string, error) {
	proposal := NodeProposal{
		ApprovalVersion: 1,
		Kind:            nodeApprovalKind,
		RunID:           record.RunID,
		NodeID:          record.NodeID,
		NodeKey:         "node:" + record.NodeID,
		Title:           record.Title,
		Proposal:        json.RawMessage(canonicalProposal),
		ParamsSHA256:    paramsSHA,
		ManifestSHA256:  facts.ManifestSHA256,
		SnapshotID:      facts.SnapshotID,
		SnapshotSHA256:  facts.SnapshotSHA256,
		DefinitionNodes: definitionNodes,
		ExpiresAt:       expires.UTC().Format(time.RFC3339),
	}
	raw, err := json.Marshal(proposal)
	if err != nil {
		return "", fmt.Errorf("encoding the node approval proposal: %w", err)
	}
	canonical, err := jcs.CanonicalizeBytes(raw)
	if err != nil {
		return "", fmt.Errorf("canonicalising the node approval proposal: %w", err)
	}
	if s.Objects == nil {
		return "", failure(503, "object_store_unavailable", "the object store is not configured")
	}
	object, err := s.Objects.CreateSealed(bytes.NewReader(canonical), objectstore.MaxObjectBytes)
	if err != nil {
		return "", failure(503, "object_store_unavailable",
			"the node approval proposal could not be sealed")
	}
	artifactID := uuid.NewString()
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO platform.artifacts
			(id, owner_id, object_key, purpose, sha256, media_type, size_bytes, state, revision)
		VALUES ($1::uuid, $2::uuid, $3, 'approval_proposal', $4, 'application/json', $5, 'ready', 1)`,
		artifactID, approverID, object.Key, object.SHA256, object.Size,
	); err != nil {
		return "", fmt.Errorf("writing the node approval proposal artifact: %w", err)
	}
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO platform.run_artifacts (run_id, artifact_id, attempt_token, relation)
		VALUES ($1::uuid, $2::uuid, $3::uuid, 'approval_proposal')
		ON CONFLICT (run_id, artifact_id) DO NOTHING`,
		record.RunID, artifactID, record.AttemptToken,
	); err != nil {
		return "", fmt.Errorf("linking the node approval proposal to the run: %w", err)
	}
	return artifactID, nil
}

// nodeApprovalByKeyTx is the replay lookup: the original row in whatever state it
// has. The unique key is the canonical proposal, so a changed proposal never
// matches a decided row.
func nodeApprovalByKeyTx(
	ctx context.Context, tx *sql.Tx, runID, nodeKey, paramsSHA string,
) (*Approval, error) {
	return approvalFromRow(tx.QueryRowContext(ctx, `
		SELECT `+approvalColumns+` FROM platform.approvals
		 WHERE run_id = $1::uuid AND node_key = $2 AND params_sha256 = $3`,
		runID, nodeKey, paramsSHA))
}

// nodeApprovalByKey reads the same row outside the (rolled back) transaction.
func (s *Service) nodeApprovalByKey(
	ctx context.Context, runID, nodeKey, paramsSHA string,
) (*Approval, error) {
	approval, err := approvalFromRow(s.DB.QueryRowContext(ctx, `
		SELECT `+approvalColumns+` FROM platform.approvals
		 WHERE run_id = $1::uuid AND node_key = $2 AND params_sha256 = $3`,
		runID, nodeKey, paramsSHA))
	if err != nil {
		return nil, fmt.Errorf("reading the concurrent node approval: %w", err)
	}
	if approval == nil {
		return nil, failure(500, "internal_error",
			"a concurrent node approval could not be read back")
	}
	return approval, nil
}
