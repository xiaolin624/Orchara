package catalog

// F02B/T068: publication submission, administrator decision and review reads.
//
// The flow the contract fixes (API.md §2, DATA-MODEL §2):
//
//	save draft -> validate -> submit (frozen snapshot + evidence) -> admin review
//	-> one transactional version + active pointer -> (T070) revocation
//
// submitApplication (POST /applications/{id}/submit):
//   - owner only; a browser session needs the creator role and an API key needs
//     application:write (the route enforces that before this service);
//   - the body binds the expected manifest SHA-256 and one evaluation report id;
//   - the current draft is compiled with the shared-execution dependency rule and
//     its hash must equal the expected one, so a draft edit after the evaluation
//     cannot be smuggled into the review;
//   - the cited report is re-verified by `verifyApplicationReport` (real runner,
//     frozen manifest bytes, T062 case-run binding, five author-case categories);
//   - one immutable snapshot (purpose card, model destinations, tool
//     read/write/network, pinned sub-versions/connectors, report evidence) is
//     stored on the review row, which is created inside the idempotency receipt's
//     transaction.
//
// reviewPublication (POST /reviews/{id}/decision):
//   - administrator only, hash-bound to the reviewed content, reason recorded;
//   - approval re-reads the sealed report and the frozen manifest bytes, re-runs
//     the dependency readiness gate (a revoked dependency fails the approval with
//     located evidence), then in one transaction creates the immutable version +
//     version_dependencies, points applications.active_version_id at it, writes
//     the durable outbox row and the audit trail, and marks the review approved.
//     The review row lock is what makes two concurrent approvals produce exactly
//     one version.
//   - rejection records state + reason and leaves the draft editable; a rejected
//     review can never be approved later (the state CAS refuses it).

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"strings"
	"time"
	"unicode/utf8"

	"orchara/platform/internal/audit"
	"orchara/platform/internal/compiler"
	"orchara/platform/internal/jcs"
	"orchara/platform/internal/objectstore"
	"orchara/platform/internal/uuid"
)

const (
	// PublicationStateSubmitted/Approved/Rejected are the contract's review
	// states (publication_reviews.state CHECK).
	PublicationStateSubmitted = "submitted"
	PublicationStateApproved  = "approved"
	PublicationStateRejected  = "rejected"

	// MaxDecisionReasonRunes bounds the recorded decision reason. The contract
	// only says `string`; a 2000-character cap keeps one review from storing an
	// essay without inventing a contract rule (a located 422 explains it).
	MaxDecisionReasonRunes = 2000

	// publicationAcceptedStatus / publicationDecisionStatus are the contract
	// success statuses whose bodies the idempotency receipts store.
	publicationAcceptedStatus = 202
	publicationDecisionStatus = 200

	// PublicationOutboxKind is the durable catalog signal T077/T082 consume.
	PublicationOutboxKind = "catalog_index"
)

// SubmitPublicationRequest keeps the two body members as raw JSON so a wrong
// type is a located 422 and unknown top-level keys are reported by the service.
type SubmitPublicationRequest struct {
	ExpectedManifestSHA256 json.RawMessage
	EvaluationReportID     json.RawMessage
	UnknownFields          []string
}

// PublicationPlan is a structurally valid submit body plus its canonical
// request identity.
type PublicationPlan struct {
	ExpectedManifestSHA256 string
	EvaluationReportID     string
	RequestSHA256          string
}

// PublicationReviewView is exactly one item of the contract's review list.
type PublicationReviewView struct {
	ID             string `json:"id"`
	ApplicationID  string `json:"application_id"`
	State          string `json:"state"`
	ManifestSHA256 string `json:"manifest_sha256"`
}

// PublicationReviewDetail is exactly the contract's getPublicationReview body:
// id, state, manifest_sha256, report_id and the frozen snapshot object.
type PublicationReviewDetail struct {
	ID             string          `json:"id"`
	State          string          `json:"state"`
	ManifestSHA256 string          `json:"manifest_sha256"`
	ReportID       string          `json:"report_id"`
	Snapshot       json.RawMessage `json:"snapshot"`
}

// PublicationSubmission is the result of one submit call.
type PublicationSubmission struct {
	Review   *PublicationReviewView
	Replayed bool
}

// ReviewDecisionRequest keeps the decision body raw for the same located-422
// reason as the submit body.
type ReviewDecisionRequest struct {
	Decision       json.RawMessage
	ManifestSHA256 json.RawMessage
	Reason         json.RawMessage
	UnknownFields  []string
}

// ReviewDecisionPlan is a structurally valid decision body plus its identity.
type ReviewDecisionPlan struct {
	Decision       string
	ManifestSHA256 string
	Reason         string
	RequestSHA256  string
}

// PublicationDecision is exactly the contract's PublicationDecisionResult.
type PublicationDecision struct {
	ID        string  `json:"id"`
	State     string  `json:"state"`
	VersionID *string `json:"version_id"`
}

// ---------------------------------------------------------------------------
// typed refusals (the API maps each to its contract status/code)
// ---------------------------------------------------------------------------

// ManifestChangedError is the 409 "the draft has moved on since the cited
// content" refusal. It carries both hashes so the client can resubmit exactly.
type ManifestChangedError struct {
	Expected string
	Current  string
	// CurrentExecutionRevision is set when the draft changed between the compile
	// and the locking transaction (the hash can no longer be attributed).
	CurrentExecutionRevision int64
}

func (e *ManifestChangedError) Error() string {
	if e.CurrentExecutionRevision > 0 {
		return "the draft changed while the publication submission was being frozen; fetch it and retry"
	}
	return "the current draft's manifest hash is not the expected one; fetch the draft and retry"
}

// ReviewStateError is the 409 "this review is no longer submitted" refusal.
type ReviewStateError struct{ State string }

func (e *ReviewStateError) Error() string {
	return "the publication review is already decided (state=" + e.State + ")"
}

// ReviewHashMismatchError is the 409 "the decision names a different content
// hash than the frozen review" refusal.
type ReviewHashMismatchError struct {
	Reviewed string
	Provided string
}

func (e *ReviewHashMismatchError) Error() string {
	return "manifest_sha256 does not equal the reviewed frozen hash; the draft changed under the review and must be re-submitted"
}

// FrozenDependencyError is the 409 "a dependency is no longer valid" refusal at
// approval time. Errors is the same located vocabulary the submission uses.
type FrozenDependencyError struct{ Errors []ExecutableError }

func (e *FrozenDependencyError) Error() string {
	return "a frozen dependency is no longer valid; the publication review cannot be approved"
}

// ApplicationArchivedError is the 409 "the application was archived after the
// submission" refusal.
type ApplicationArchivedError struct{}

func (e *ApplicationArchivedError) Error() string {
	return "the application is archived and cannot be published"
}

// ---------------------------------------------------------------------------
// body validation
// ---------------------------------------------------------------------------

// ValidateSubmitPublicationRequest checks the contract body's shape and
// produces the canonical request identity. It touches no database.
func ValidateSubmitPublicationRequest(request SubmitPublicationRequest) (*PublicationPlan, error) {
	if len(request.UnknownFields) > 0 {
		unknown := append([]string(nil), request.UnknownFields...)
		sort.Strings(unknown)
		return nil, &ValidationError{
			Field:   unknown[0],
			Message: "submitApplication accepts exactly expected_manifest_sha256 and evaluation_report_id",
		}
	}
	plan := &PublicationPlan{}
	hash, ok := decodeSHA256(request.ExpectedManifestSHA256)
	if !ok {
		return nil, &ValidationError{
			Field:   "expected_manifest_sha256",
			Message: "expected_manifest_sha256 must be the 64-hex-character SHA-256 of the frozen manifest",
		}
	}
	plan.ExpectedManifestSHA256 = hash
	var reportID string
	if len(strings.TrimSpace(string(request.EvaluationReportID))) == 0 {
		return nil, &ValidationError{Field: "evaluation_report_id", Message: "evaluation_report_id is required"}
	}
	if err := json.Unmarshal(request.EvaluationReportID, &reportID); err != nil || !uuid.Valid(reportID) {
		return nil, &ValidationError{Field: "evaluation_report_id", Message: "evaluation_report_id must be a UUID"}
	}
	plan.EvaluationReportID = strings.ToLower(reportID)
	return plan, nil
}

// publicationSubmitDigest is the canonical request fingerprint stored in the
// idempotency receipt.
func publicationSubmitDigest(applicationID string, plan *PublicationPlan) (string, error) {
	return canonicalRequestDigest(struct {
		ApplicationID          string `json:"application_id"`
		EvaluationReportID     string `json:"evaluation_report_id"`
		ExpectedManifestSHA256 string `json:"expected_manifest_sha256"`
	}{
		ApplicationID:          applicationID,
		EvaluationReportID:     plan.EvaluationReportID,
		ExpectedManifestSHA256: plan.ExpectedManifestSHA256,
	})
}

// ValidateReviewDecisionRequest checks the decision body's shape.
func ValidateReviewDecisionRequest(request ReviewDecisionRequest) (*ReviewDecisionPlan, error) {
	if len(request.UnknownFields) > 0 {
		unknown := append([]string(nil), request.UnknownFields...)
		sort.Strings(unknown)
		return nil, &ValidationError{
			Field:   unknown[0],
			Message: "reviewPublication accepts exactly decision, manifest_sha256 and reason",
		}
	}
	plan := &ReviewDecisionPlan{}
	var decision string
	if err := json.Unmarshal(request.Decision, &decision); err != nil {
		return nil, &ValidationError{Field: "decision", Message: `decision must be "approve" or "reject"`}
	}
	if decision != "approve" && decision != "reject" {
		return nil, &ValidationError{Field: "decision", Message: `decision must be "approve" or "reject"`}
	}
	plan.Decision = decision
	hash, ok := decodeSHA256(request.ManifestSHA256)
	if !ok {
		return nil, &ValidationError{
			Field:   "manifest_sha256",
			Message: "manifest_sha256 must be the 64-hex-character SHA-256 the review froze",
		}
	}
	plan.ManifestSHA256 = hash
	var reason string
	if err := json.Unmarshal(request.Reason, &reason); err != nil {
		return nil, &ValidationError{Field: "reason", Message: "reason must be a string"}
	}
	reason = strings.TrimSpace(reason)
	if reason == "" {
		return nil, &ValidationError{Field: "reason", Message: "a decision reason is required"}
	}
	if utf8.RuneCountInString(reason) > MaxDecisionReasonRunes {
		return nil, &ValidationError{
			Field:   "reason",
			Message: fmt.Sprintf("reason must be at most %d characters", MaxDecisionReasonRunes),
		}
	}
	plan.Reason = reason
	return plan, nil
}

func reviewDecisionDigest(reviewID string, plan *ReviewDecisionPlan) (string, error) {
	return canonicalRequestDigest(struct {
		Decision       string `json:"decision"`
		ManifestSHA256 string `json:"manifest_sha256"`
		Reason         string `json:"reason"`
		ReviewID       string `json:"review_id"`
	}{
		Decision: plan.Decision, ManifestSHA256: plan.ManifestSHA256,
		Reason: plan.Reason, ReviewID: reviewID,
	})
}

// canonicalRequestDigest returns SHA-256 over the RFC 8785 canonical bytes of a
// request document, the same receipt identity debug/evaluation use.
func canonicalRequestDigest(document any) (string, error) {
	encoded, err := json.Marshal(document)
	if err != nil {
		return "", fmt.Errorf("encoding the idempotency request digest: %w", err)
	}
	canonical, err := jcs.CanonicalizeBytes(encoded)
	if err != nil {
		return "", fmt.Errorf("canonicalising the idempotency request digest: %w", err)
	}
	return jcs.SHA256Hex(canonical), nil
}

// decodeSHA256 accepts one JSON string and normalises it to lowercase 64-hex.
func decodeSHA256(raw json.RawMessage) (string, bool) {
	var value string
	if err := json.Unmarshal(raw, &value); err != nil {
		return "", false
	}
	value = strings.ToLower(strings.TrimSpace(value))
	return value, isSHA256Hex(value)
}

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

// ---------------------------------------------------------------------------
// submitApplication
// ---------------------------------------------------------------------------

// PreviewPublication is the creator's pre-submission read: it compiles the
// current draft with the shared-execution rule and returns the same
// shared-definition projection a submission would freeze (purpose card, model
// destinations, tool read/write/network, pinned sub-versions/connectors) plus
// the manifest hash the submit body must name.
//
// It is owner-only and creates no review, version or projection row; it does seal
// the canonical manifest bytes if this draft identity has not been validated yet
// (validate would do the same).
func (r *Reader) PreviewPublication(
	ctx context.Context, callerID, applicationID string,
	objects *objectstore.Store, inputs compiler.Inputs,
) (*publicationContent, error) {
	draft, ownerID, err := r.loadDraft(ctx, applicationID)
	if err != nil {
		return nil, err
	}
	if !strings.EqualFold(callerID, ownerID) {
		return nil, ErrNotVisible
	}
	execution, located, err := r.ValidateExecution(
		ctx, callerID, applicationID, objects, inputs, ExecutionOptions{},
	)
	if err != nil {
		return nil, err
	}
	if len(located) > 0 {
		return nil, &ExecutionRefusedError{Errors: located}
	}
	if execution == nil || execution.Manifest == nil {
		return nil, errors.New("publication preview produced no manifest")
	}
	name, live, err := applicationName(ctx, r.DB, applicationID)
	if err != nil {
		return nil, err
	}
	if !live {
		return nil, ErrNotVisible
	}
	return buildPublicationContent(ctx, r.DB, applicationID, name, draft, execution.Manifest,
		execution.SHA256, execution.ArtifactID)
}

// SubmitPublication validates one submitApplication call and creates the review.
func (r *Reader) SubmitPublication(
	ctx context.Context,
	callerID, applicationID string,
	scope IdempotencyScope,
	request SubmitPublicationRequest,
	objects *objectstore.Store,
	inputs compiler.Inputs,
) (*PublicationSubmission, error) {
	plan, err := ValidateSubmitPublicationRequest(request)
	if err != nil {
		return nil, err
	}
	if !uuid.Valid(applicationID) {
		return nil, ErrNotVisible
	}
	if strings.TrimSpace(scope.PrincipalID) == "" || !strings.EqualFold(scope.PrincipalID, callerID) ||
		strings.TrimSpace(scope.Key) == "" || scope.Method != debugIdempotencyMethod {
		return nil, &ValidationError{Field: "idempotency_key", Message: "a complete idempotency scope is required"}
	}
	digest, err := publicationSubmitDigest(applicationID, plan)
	if err != nil {
		return nil, err
	}
	plan.RequestSHA256 = digest

	// 1. A committed receipt answers immediately.
	existing, err := r.lookupIdempotency(ctx, scope)
	if err != nil {
		return nil, err
	}
	if existing != nil {
		return r.replayPublicationSubmission(ctx, existing, digest)
	}

	// 2. Owner only.
	draft, ownerID, err := r.loadDraft(ctx, applicationID)
	if err != nil {
		return nil, err
	}
	if !strings.EqualFold(callerID, ownerID) {
		return nil, ErrNotVisible
	}

	// 3. Compile the *current* draft with the shared execution rule and require
	// the expected hash. A draft edit after the evaluation changes this hash.
	execution, located, err := r.ValidateExecution(
		ctx, callerID, applicationID, objects, inputs, ExecutionOptions{},
	)
	if err != nil {
		return nil, err
	}
	if len(located) > 0 {
		return nil, &ExecutionRefusedError{Errors: located}
	}
	if execution == nil || execution.Manifest == nil {
		return nil, errors.New("publication validation produced no manifest")
	}
	if !strings.EqualFold(execution.SHA256, plan.ExpectedManifestSHA256) {
		return nil, &ManifestChangedError{
			Expected: plan.ExpectedManifestSHA256,
			Current:  execution.SHA256,
		}
	}

	// 4. The cited report: same application, same owner, same frozen manifest and
	// dataset, produced by the real runner, every passed case backed by a real
	// run, five author-case categories present.
	verified, err := verifyApplicationReport(ctx, r.DB, objects, reportExpectation{
		ReportID:       plan.EvaluationReportID,
		ApplicationID:  applicationID,
		OwnerID:        callerID,
		ManifestSHA256: plan.ExpectedManifestSHA256,
		CheckDraft:     draft,
	})
	if err != nil {
		return nil, err
	}
	if !verified.Coverage.Satisfied {
		return nil, &ReportRefusedError{
			Code: "author_cases_incomplete",
			Message: "the cited passed report does not satisfy the five author-case publication gate " +
				"(EVALUATION.md §6: normal, bad input, ambiguity/out-of-bounds, tool failure, output check; at least 5 cases)",
			Details: map[string]any{
				"missing_categories": verified.Coverage.Missing,
				"case_count":         verified.Coverage.CaseCount,
				"minimum_cases":      verified.Coverage.MinimumCases,
			},
		}
	}

	// 5. The shared-definition snapshot the administrator will review.
	name, live, err := applicationName(ctx, r.DB, applicationID)
	if err != nil {
		return nil, err
	}
	if !live {
		return nil, ErrNotVisible
	}
	snapshot, err := buildPublicationSnapshot(ctx, r.DB, applicationID, name, draft, verified)
	if err != nil {
		return nil, err
	}
	snapshotJSON, err := marshalSnapshot(snapshot)
	if err != nil {
		return nil, err
	}

	return r.createPublicationReview(ctx, createPublicationParams{
		CallerID:      callerID,
		ApplicationID: applicationID,
		Scope:         scope,
		Plan:          plan,
		Draft:         draft,
		Execution:     execution,
		Verified:      verified,
		Snapshot:      snapshotJSON,
	})
}

// publicationSnapshot assembles the frozen review document from the verified
// evidence and the shared-definition projection.
func buildPublicationSnapshot(
	ctx context.Context,
	q dependencyQueryer,
	applicationID, applicationName string,
	draft *Draft,
	verified *verifiedApplicationReport,
) (*PublicationSnapshot, error) {
	content, err := buildPublicationContent(ctx, q, applicationID, applicationName, draft,
		verified.Manifest, verified.ManifestSHA256, verified.ManifestArtifactID)
	if err != nil {
		return nil, err
	}
	return &PublicationSnapshot{
		ReviewVersion:      PublicationSnapshotVersion,
		ApplicationID:      content.ApplicationID,
		ApplicationName:    content.ApplicationName,
		DraftRevision:      content.DraftRevision,
		ExecutionRevision:  content.ExecutionRevision,
		ManifestSHA256:     content.ManifestSHA256,
		ManifestArtifactID: content.ManifestArtifactID,
		DatasetArtifactID:  verified.DatasetArtifactID,
		DatasetSHA256:      strings.ToLower(verified.DatasetSHA256),
		EvaluationID:       verified.EvaluationID,
		ReportID:           verified.ReportID,
		ReportArtifactID:   verified.ResultArtifactID,
		ReportSHA256:       verified.ReportArtifactSHA,
		ReportStatus:       verified.Status,
		PurposeCard:        content.PurposeCard,
		ModelDestinations:  content.ModelDestinations,
		ToolCapabilities:   content.ToolCapabilities,
		SubappVersions:     content.SubappVersions,
		ConnectorPolicies:  content.ConnectorPolicies,
		AuthorCases:        verified.Coverage,
	}, nil
}

type createPublicationParams struct {
	CallerID      string
	ApplicationID string
	Scope         IdempotencyScope
	Plan          *PublicationPlan
	Draft         *Draft
	Execution     *ValidatedExecution
	Verified      *verifiedApplicationReport
	Snapshot      []byte
}

// createPublicationReview writes the receipt, the review row, the audit event
// and (on a duplicate content submission) the existing review's 202 body in one
// transaction.
func (r *Reader) createPublicationReview(
	ctx context.Context, params createPublicationParams,
) (*PublicationSubmission, error) {
	tx, err := r.DB.BeginTx(ctx, nil)
	if err != nil {
		return nil, fmt.Errorf("starting the publication transaction: %w", err)
	}
	defer func() { _ = tx.Rollback() }()

	result, err := tx.ExecContext(ctx, `
		INSERT INTO platform.idempotency_keys
		       (principal_id, method, path, key, request_sha256, expires_at)
		VALUES ($1::uuid, $2, $3, $4, $5, now() + $6::interval)
		ON CONFLICT (principal_id, method, path, key) DO NOTHING`,
		params.Scope.PrincipalID, params.Scope.Method, params.Scope.Path, params.Scope.Key,
		params.Plan.RequestSHA256, intervalOf(IdempotencyTTL),
	)
	if err != nil {
		return nil, fmt.Errorf("claiming the publication idempotency receipt: %w", err)
	}
	claimed, err := result.RowsAffected()
	if err != nil {
		return nil, fmt.Errorf("reading the publication idempotency claim: %w", err)
	}
	if claimed == 0 {
		_ = tx.Rollback()
		winner, lookupErr := r.lookupIdempotency(ctx, params.Scope)
		if lookupErr != nil {
			return nil, lookupErr
		}
		if winner == nil {
			return nil, errors.New("the publication idempotency claim conflicted but no receipt is visible")
		}
		return r.replayPublicationSubmission(ctx, winner, params.Plan.RequestSHA256)
	}

	// Serialise the same frozen content across keys. The partial unique index is
	// the database backstop; this advisory lock keeps the normal path from
	// reaching it and lets a duplicate return the existing review instead of an
	// error.
	if _, err := tx.ExecContext(ctx, `
		SELECT pg_advisory_xact_lock(hashtextextended($1, 0))`,
		"publication-submit|"+params.ApplicationID+"|"+params.Plan.ExpectedManifestSHA256,
	); err != nil {
		return nil, fmt.Errorf("serialising the publication submission: %w", err)
	}

	// Re-lock the draft and prove the executable content did not move while the
	// manifest was being compiled. Layout-only saves keep execution_revision and
	// are allowed; an executable edit is a manifest change and must resubmit.
	var executionRevision int64
	err = tx.QueryRowContext(ctx, `
		SELECT execution_revision FROM platform.application_drafts
		 WHERE application_id = $1::uuid
		 FOR UPDATE`, params.ApplicationID).Scan(&executionRevision)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrNotVisible
	}
	if err != nil {
		return nil, fmt.Errorf("locking the publication draft: %w", err)
	}
	if executionRevision != params.Draft.ExecutionRevision ||
		executionRevision != params.Execution.Manifest.SourceRevision {
		return nil, &ManifestChangedError{
			Expected:                 params.Plan.ExpectedManifestSHA256,
			CurrentExecutionRevision: executionRevision,
		}
	}

	// Identical content must not create a second review, whatever the state of
	// the earlier one. The frozen hash is the content identity; a new report for
	// the same content is the same review.
	var existingID, existingState string
	err = tx.QueryRowContext(ctx, `
		SELECT id::text, state FROM platform.publication_reviews
		 WHERE application_id = $1::uuid AND manifest_sha256 = $2
		 ORDER BY created_at DESC, id DESC
		 LIMIT 1`, params.ApplicationID, params.Plan.ExpectedManifestSHA256,
	).Scan(&existingID, &existingState)
	switch {
	case err == nil:
		view := &PublicationReviewView{
			ID: existingID, ApplicationID: params.ApplicationID,
			State: existingState, ManifestSHA256: params.Plan.ExpectedManifestSHA256,
		}
		if err := storeIdempotentResponse(ctx, tx, params.Scope,
			publicationAcceptedStatus, view); err != nil {
			return nil, err
		}
		if err := tx.Commit(); err != nil {
			return nil, fmt.Errorf("committing the replayed publication submission: %w", err)
		}
		return &PublicationSubmission{Review: view, Replayed: true}, nil
	case !errors.Is(err, sql.ErrNoRows):
		return nil, fmt.Errorf("reading an existing publication review: %w", err)
	}

	reviewID := uuid.NewString()
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO platform.publication_reviews
		       (id, application_id, submitted_by, snapshot, manifest_sha256, report_id,
		        state, dataset_sha256, snapshot_id, evaluation_id, manifest_artifact_id,
		        created_at)
		VALUES ($1::uuid, $2::uuid, $3::uuid, $4::jsonb, $5, $6::uuid,
		        'submitted', $7, $8::uuid, $9::uuid, $10::uuid, now())`,
		reviewID, params.ApplicationID, params.CallerID, string(params.Snapshot),
		params.Plan.ExpectedManifestSHA256, params.Verified.ReportID,
		strings.ToLower(params.Verified.DatasetSHA256), params.Verified.SnapshotID,
		params.Verified.EvaluationID, params.Verified.ManifestArtifactID,
	); err != nil {
		return nil, fmt.Errorf("creating the publication review: %w", err)
	}

	actor := params.CallerID
	if err := audit.Record(ctx, tx, &actor, "publication.submitted", "publication_review",
		&reviewID, map[string]any{
			"application_id":     params.ApplicationID,
			"manifest_sha256":    params.Plan.ExpectedManifestSHA256,
			"report_id":          params.Verified.ReportID,
			"evaluation_id":      params.Verified.EvaluationID,
			"execution_revision": params.Draft.ExecutionRevision,
		}); err != nil {
		return nil, err
	}

	view := &PublicationReviewView{
		ID: reviewID, ApplicationID: params.ApplicationID,
		State: PublicationStateSubmitted, ManifestSHA256: params.Plan.ExpectedManifestSHA256,
	}
	if err := storeIdempotentResponse(ctx, tx, params.Scope, publicationAcceptedStatus, view); err != nil {
		return nil, err
	}
	if err := tx.Commit(); err != nil {
		return nil, fmt.Errorf("committing the publication submission: %w", err)
	}
	return &PublicationSubmission{Review: view, Replayed: false}, nil
}

// storeIdempotentResponse persists the successful body on the claimed receipt.
func storeIdempotentResponse(
	ctx context.Context, tx *sql.Tx, scope IdempotencyScope, status int, payload any,
) error {
	encoded, err := json.Marshal(payload)
	if err != nil {
		return fmt.Errorf("encoding the idempotent publication response: %w", err)
	}
	if _, err := tx.ExecContext(ctx, `
		UPDATE platform.idempotency_keys
		   SET response_code = $5, response_json = $6::jsonb
		 WHERE principal_id = $1::uuid AND method = $2 AND path = $3 AND key = $4`,
		scope.PrincipalID, scope.Method, scope.Path, scope.Key, status, string(encoded),
	); err != nil {
		return fmt.Errorf("storing the idempotent publication response: %w", err)
	}
	return nil
}

// replayPublicationSubmission turns one receipt into the stored 202 body.
func (r *Reader) replayPublicationSubmission(
	ctx context.Context, record *idempotencyRecord, digest string,
) (*PublicationSubmission, error) {
	if record.RequestSHA256 != digest {
		return nil, &IdempotencyConflictError{}
	}
	if !record.ResponseCode.Valid || record.ResponseCode.Int64 != publicationAcceptedStatus ||
		!record.ResponseJSON.Valid {
		return nil, errors.New("the publication idempotency receipt has no stored 202 response")
	}
	var view PublicationReviewView
	if err := json.Unmarshal([]byte(record.ResponseJSON.String), &view); err != nil {
		return nil, fmt.Errorf("decoding the stored publication response: %w", err)
	}
	return &PublicationSubmission{Review: &view, Replayed: true}, nil
}

// ---------------------------------------------------------------------------
// reviewPublication
// ---------------------------------------------------------------------------

// ReviewPublication validates one decision and either creates the immutable
// version (approve) or records the rejection.
func (r *Reader) ReviewPublication(
	ctx context.Context,
	adminID, reviewID string,
	scope IdempotencyScope,
	request ReviewDecisionRequest,
	objects *objectstore.Store,
) (*PublicationDecision, error) {
	plan, err := ValidateReviewDecisionRequest(request)
	if err != nil {
		return nil, err
	}
	if !uuid.Valid(reviewID) {
		return nil, ErrReviewNotVisible
	}
	if strings.TrimSpace(scope.PrincipalID) == "" || !strings.EqualFold(scope.PrincipalID, adminID) ||
		strings.TrimSpace(scope.Key) == "" || scope.Method != debugIdempotencyMethod {
		return nil, &ValidationError{Field: "idempotency_key", Message: "a complete idempotency scope is required"}
	}
	digest, err := reviewDecisionDigest(reviewID, plan)
	if err != nil {
		return nil, err
	}
	plan.RequestSHA256 = digest

	existing, err := r.lookupIdempotency(ctx, scope)
	if err != nil {
		return nil, err
	}
	if existing != nil {
		return r.replayPublicationDecision(existing, digest)
	}

	review, err := r.loadPublicationReview(ctx, reviewID)
	if err != nil {
		return nil, err
	}
	if review.state != PublicationStateSubmitted {
		return nil, &ReviewStateError{State: review.state}
	}
	if !strings.EqualFold(plan.ManifestSHA256, review.manifestSHA256) {
		return nil, &ReviewHashMismatchError{
			Reviewed: review.manifestSHA256, Provided: plan.ManifestSHA256,
		}
	}

	if plan.Decision == "reject" {
		return r.rejectPublicationReview(ctx, adminID, review, scope, plan)
	}
	return r.approvePublicationReview(ctx, adminID, review, scope, plan, objects)
}

// storedReview is the internal review row a decision needs.
type storedReview struct {
	id                 string
	applicationID      string
	submittedBy        string
	state              string
	manifestSHA256     string
	reportID           string
	snapshotID         string
	evaluationID       string
	datasetSHA256      string
	manifestArtifactID string
}

func (r *Reader) loadPublicationReview(ctx context.Context, reviewID string) (*storedReview, error) {
	review := &storedReview{}
	err := r.DB.QueryRowContext(ctx, `
		SELECT id::text, application_id::text, submitted_by::text, state,
		       manifest_sha256, report_id::text,
		       coalesce(snapshot_id::text, ''), coalesce(evaluation_id::text, ''),
		       coalesce(dataset_sha256, ''), coalesce(manifest_artifact_id::text, '')
		  FROM platform.publication_reviews
		 WHERE id = $1::uuid`, reviewID,
	).Scan(&review.id, &review.applicationID, &review.submittedBy, &review.state,
		&review.manifestSHA256, &review.reportID, &review.snapshotID,
		&review.evaluationID, &review.datasetSHA256, &review.manifestArtifactID)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrReviewNotVisible
	}
	if err != nil {
		return nil, fmt.Errorf("reading the publication review: %w", err)
	}
	return review, nil
}

func (r *Reader) rejectPublicationReview(
	ctx context.Context, adminID string, review *storedReview,
	scope IdempotencyScope, plan *ReviewDecisionPlan,
) (*PublicationDecision, error) {
	tx, err := r.DB.BeginTx(ctx, nil)
	if err != nil {
		return nil, fmt.Errorf("starting the review rejection transaction: %w", err)
	}
	defer func() { _ = tx.Rollback() }()

	locked, err := lockSubmittedReview(ctx, tx, review.id)
	if err != nil {
		return nil, err
	}
	result, err := tx.ExecContext(ctx, `
		UPDATE platform.publication_reviews
		   SET state = 'rejected', reviewer_id = $2::uuid, decided_at = now(), reason = $3
		 WHERE id = $1::uuid AND state = 'submitted'`,
		locked, adminID, plan.Reason,
	)
	if err != nil {
		return nil, fmt.Errorf("rejecting the publication review: %w", err)
	}
	affected, err := result.RowsAffected()
	if err != nil {
		return nil, fmt.Errorf("rejecting the publication review: %w", err)
	}
	if affected != 1 {
		return nil, &ReviewStateError{State: PublicationStateRejected}
	}
	actor := adminID
	if err := audit.Record(ctx, tx, &actor, "publication.rejected", "publication_review",
		&review.id, map[string]any{
			"application_id":  review.applicationID,
			"manifest_sha256": review.manifestSHA256,
			"reason":          plan.Reason,
		}); err != nil {
		return nil, err
	}
	decision := &PublicationDecision{ID: review.id, State: PublicationStateRejected, VersionID: nil}
	if err := storeIdempotentResponse(ctx, tx, scope, publicationDecisionStatus, decision); err != nil {
		return nil, err
	}
	if err := tx.Commit(); err != nil {
		return nil, fmt.Errorf("committing the review rejection: %w", err)
	}
	return decision, nil
}

// lockSubmittedReview locks one review row and proves it is still submitted.
func lockSubmittedReview(ctx context.Context, tx *sql.Tx, reviewID string) (string, error) {
	var state string
	err := tx.QueryRowContext(ctx, `
		SELECT state FROM platform.publication_reviews
		 WHERE id = $1::uuid FOR UPDATE`, reviewID).Scan(&state)
	if errors.Is(err, sql.ErrNoRows) {
		return "", ErrReviewNotVisible
	}
	if err != nil {
		return "", fmt.Errorf("locking the publication review: %w", err)
	}
	if state != PublicationStateSubmitted {
		return "", &ReviewStateError{State: state}
	}
	return reviewID, nil
}

func (r *Reader) approvePublicationReview(
	ctx context.Context, adminID string, review *storedReview,
	scope IdempotencyScope, plan *ReviewDecisionPlan, objects *objectstore.Store,
) (*PublicationDecision, error) {
	// Re-prove the evidence before creating anything. The report must still be a
	// real runner report over the frozen manifest, the frozen manifest bytes must
	// still verify, and the five author-case gate must still hold.
	verified, err := verifyApplicationReport(ctx, r.DB, objects, reportExpectation{
		ReportID:       review.reportID,
		ApplicationID:  review.applicationID,
		OwnerID:        review.submittedBy,
		ManifestSHA256: review.manifestSHA256,
	})
	if err != nil {
		return nil, err
	}
	if !verified.Coverage.Satisfied {
		return nil, &ReportRefusedError{
			Code:    "author_cases_incomplete",
			Message: "the cited report no longer satisfies the five author-case publication gate",
			Details: map[string]any{
				"missing_categories": verified.Coverage.Missing,
				"case_count":         verified.Coverage.CaseCount,
				"minimum_cases":      verified.Coverage.MinimumCases,
			},
		}
	}
	if review.manifestArtifactID != "" &&
		!strings.EqualFold(review.manifestArtifactID, verified.ManifestArtifactID) {
		return nil, &ReportRefusedError{
			Code:    "report_binding_mismatch",
			Message: "the frozen manifest artifact named by the review does not match the cited report's snapshot",
		}
	}
	if review.snapshotID != "" && review.snapshotID != verified.SnapshotID {
		return nil, &ReportRefusedError{
			Code:    "report_binding_mismatch",
			Message: "the frozen snapshot named by the review does not match the cited report's snapshot",
		}
	}

	tx, err := r.DB.BeginTx(ctx, nil)
	if err != nil {
		return nil, fmt.Errorf("starting the publication approval transaction: %w", err)
	}
	defer func() { _ = tx.Rollback() }()

	if _, err := lockSubmittedReview(ctx, tx, review.id); err != nil {
		return nil, err
	}

	// Lock the application while allocating the next version number and moving
	// the active pointer, so two approvals for one application cannot interleave.
	var (
		name     string
		archived sql.NullTime
		active   sql.NullString
	)
	err = tx.QueryRowContext(ctx, `
		SELECT name, archived_at, active_version_id::text
		  FROM platform.applications WHERE id = $1::uuid FOR UPDATE`,
		review.applicationID).Scan(&name, &archived, &active)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrReviewNotVisible
	}
	if err != nil {
		return nil, fmt.Errorf("locking the publication application: %w", err)
	}
	if archived.Valid {
		return nil, &ApplicationArchivedError{}
	}

	// The dependency closure is re-read inside this transaction: a revocation
	// committed before this point is visible to the recheck and fails the
	// approval; a revocation attempted concurrently blocks on the row locks held
	// by the publication writes and sees the committed version afterwards.
	located, err := recheckFrozenDependencies(ctx, tx, verified.Manifest)
	if err != nil {
		return nil, err
	}
	if len(located) > 0 {
		return nil, &FrozenDependencyError{Errors: located}
	}

	// The version JSON artifact is the canonical manifest artifact the snapshot
	// froze at submission. Re-prove its row inside the transaction before the
	// version references it; the object bytes themselves were re-hashed by the
	// verification above and are never rewritten.
	var (
		manifestState   string
		manifestDeleted sql.NullTime
		manifestSHA     sql.NullString
	)
	err = tx.QueryRowContext(ctx, `
		SELECT state, deleted_at, sha256 FROM platform.artifacts
		 WHERE id = $1::uuid FOR SHARE`, verified.ManifestArtifactID,
	).Scan(&manifestState, &manifestDeleted, &manifestSHA)
	if err != nil || manifestState != "ready" || manifestDeleted.Valid ||
		!manifestSHA.Valid || !strings.EqualFold(manifestSHA.String, review.manifestSHA256) {
		return nil, &ReportRefusedError{
			Code:    "report_not_trusted",
			Message: "the frozen version manifest artifact is not a ready copy of the reviewed hash",
		}
	}

	var nextVersionNo int64
	if err := tx.QueryRowContext(ctx, `
		SELECT COALESCE(MAX(version_no), 0) + 1
		  FROM platform.application_versions WHERE application_id = $1::uuid`,
		review.applicationID).Scan(&nextVersionNo); err != nil {
		return nil, fmt.Errorf("allocating the publication version number: %w", err)
	}

	versionID := uuid.NewString()
	capabilityCard, err := json.Marshal(verified.Manifest.CapabilityCard)
	if err != nil {
		return nil, fmt.Errorf("encoding the version capability card: %w", err)
	}
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO platform.application_versions
		       (id, application_id, version_no, review_id, manifest, manifest_sha256,
		        manifest_artifact_id, capability_card, compiler_version,
		        worker_image_digest, state_schema_version, created_at)
		VALUES ($1::uuid, $2::uuid, $3, $4::uuid, $5::jsonb, $6, $7::uuid, $8::jsonb,
		        $9, $10, $11, now())`,
		versionID, review.applicationID, nextVersionNo, review.id,
		string(verified.ManifestBytes), review.manifestSHA256, verified.ManifestArtifactID,
		string(capabilityCard), verified.Manifest.CompilerVersion,
		verified.Manifest.WorkerImageDigest, verified.Manifest.StateSchemaVersion,
	); err != nil {
		return nil, fmt.Errorf("creating the publication version: %w", err)
	}

	// Fixed sub-application edges the published version closes over. T075/T076
	// read this table for child-version resolution and cycle checks.
	for _, childID := range normalizedDependencyIDs(verified.Manifest.SubappVersions) {
		if _, err := tx.ExecContext(ctx, `
			INSERT INTO platform.version_dependencies (parent_version_id, child_version_id)
			VALUES ($1::uuid, $2::uuid)
			ON CONFLICT (parent_version_id, child_version_id) DO NOTHING`,
			versionID, childID,
		); err != nil {
			return nil, fmt.Errorf("recording the version dependency: %w", err)
		}
	}

	result, err := tx.ExecContext(ctx, `
		UPDATE platform.publication_reviews
		   SET state = 'approved', reviewer_id = $2::uuid, decided_at = now(),
		       reason = $3, version_id = $4::uuid
		 WHERE id = $1::uuid AND state = 'submitted'`,
		review.id, adminID, plan.Reason, versionID,
	)
	if err != nil {
		return nil, fmt.Errorf("approving the publication review: %w", err)
	}
	affected, err := result.RowsAffected()
	if err != nil || affected != 1 {
		return nil, &ReviewStateError{State: PublicationStateApproved}
	}

	result, err = tx.ExecContext(ctx, `
		UPDATE platform.applications
		   SET active_version_id = $2::uuid
		 WHERE id = $1::uuid AND archived_at IS NULL`,
		review.applicationID, versionID,
	)
	if err != nil {
		return nil, fmt.Errorf("moving the active version pointer: %w", err)
	}
	affected, err = result.RowsAffected()
	if err != nil || affected != 1 {
		return nil, &ApplicationArchivedError{}
	}

	// Durable catalog signal. There is no dispatcher in this build; T077/T082
	// own delivery and the catalog index, so delivered_at/attempts stay untouched
	// and no report claims the projection was consumed.
	if _, err := insertOutboxTx(ctx, tx, PublicationOutboxKind, versionID, struct {
		Op                      string  `json:"op"`
		ApplicationID           string  `json:"application_id"`
		VersionID               string  `json:"version_id"`
		VersionNo               int64   `json:"version_no"`
		ManifestSHA256          string  `json:"manifest_sha256"`
		PreviousActiveVersionID *string `json:"previous_active_version_id"`
		ActiveVersionID         string  `json:"active_version_id"`
	}{
		Op: "publish", ApplicationID: review.applicationID, VersionID: versionID,
		VersionNo: nextVersionNo, ManifestSHA256: review.manifestSHA256,
		PreviousActiveVersionID: stringPtrOrNil(active), ActiveVersionID: versionID,
	}); err != nil {
		return nil, err
	}

	actor := adminID
	if err := audit.Record(ctx, tx, &actor, "publication.approved", "application_version",
		&versionID, map[string]any{
			"application_id":  review.applicationID,
			"review_id":       review.id,
			"version_no":      nextVersionNo,
			"manifest_sha256": review.manifestSHA256,
			"report_id":       review.reportID,
			"reason":          plan.Reason,
		}); err != nil {
		return nil, err
	}

	decision := &PublicationDecision{
		ID: review.id, State: PublicationStateApproved, VersionID: &versionID,
	}
	if err := storeIdempotentResponse(ctx, tx, scope, publicationDecisionStatus, decision); err != nil {
		return nil, err
	}
	if err := tx.Commit(); err != nil {
		return nil, fmt.Errorf("committing the publication approval: %w", err)
	}
	return decision, nil
}

func stringPtrOrNil(value sql.NullString) *string {
	if !value.Valid {
		return nil
	}
	copy := value.String
	return &copy
}

// replayPublicationDecision turns one receipt into the stored 200 body.
func (r *Reader) replayPublicationDecision(
	record *idempotencyRecord, digest string,
) (*PublicationDecision, error) {
	if record.RequestSHA256 != digest {
		return nil, &IdempotencyConflictError{}
	}
	if !record.ResponseCode.Valid || record.ResponseCode.Int64 != publicationDecisionStatus ||
		!record.ResponseJSON.Valid {
		return nil, errors.New("the publication decision receipt has no stored 200 response")
	}
	var decision PublicationDecision
	if err := json.Unmarshal([]byte(record.ResponseJSON.String), &decision); err != nil {
		return nil, fmt.Errorf("decoding the stored publication decision: %w", err)
	}
	return &decision, nil
}

// ---------------------------------------------------------------------------
// reads
// ---------------------------------------------------------------------------

// ListReviews returns one ACL-filtered page of publication reviews, newest
// first with a stable (created_at, id) keyset order. An administrator sees every
// review; everyone else sees only reviews they submitted or reviews of an
// application they own. The ACL predicate is inside the same SQL WHERE as the
// cursor and LIMIT, so filtering happens before the page is cut.
func (r *Reader) ListReviews(
	ctx context.Context,
	callerID string,
	admin bool,
	cursor string,
	limit int,
) ([]PublicationReviewView, *string, error) {
	if limit == 0 {
		limit = DefaultLimit
	}
	if limit < MinLimit || limit > MaxLimit {
		return nil, nil, ErrInvalidLimit
	}
	var (
		cursorAt any
		cursorID any
	)
	if strings.TrimSpace(cursor) != "" {
		at, id, err := DecodeCursor(cursor)
		if err != nil {
			return nil, nil, err
		}
		cursorAt, cursorID = at, id
	}
	rows, err := r.DB.QueryContext(ctx, `
		SELECT r.id::text, r.application_id::text, r.state, r.manifest_sha256, r.created_at
		  FROM platform.publication_reviews AS r
		  JOIN platform.applications AS a ON a.id = r.application_id
		 WHERE (
		        $1::boolean
		        OR r.submitted_by = $2::uuid
		        OR a.owner_id = $2::uuid
		       )
		   AND (
		        $3::timestamptz IS NULL
		        OR (r.created_at, r.id) < ($3::timestamptz, $4::uuid)
		       )
		 ORDER BY r.created_at DESC, r.id DESC
		 LIMIT $5`,
		admin, callerID, cursorAt, cursorID, limit+1,
	)
	if err != nil {
		return nil, nil, fmt.Errorf("listing publication reviews: %w", err)
	}
	defer rows.Close()

	type scored struct {
		view      PublicationReviewView
		createdAt time.Time
	}
	all := make([]scored, 0, limit+1)
	for rows.Next() {
		var entry scored
		if err := rows.Scan(&entry.view.ID, &entry.view.ApplicationID, &entry.view.State,
			&entry.view.ManifestSHA256, &entry.createdAt); err != nil {
			return nil, nil, fmt.Errorf("listing publication reviews: %w", err)
		}
		all = append(all, entry)
	}
	if err := rows.Err(); err != nil {
		return nil, nil, fmt.Errorf("listing publication reviews: %w", err)
	}
	var next *string
	if len(all) > limit {
		last := all[limit-1]
		token := EncodeCursor(last.createdAt, last.view.ID)
		next = &token
		all = all[:limit]
	}
	items := make([]PublicationReviewView, 0, len(all))
	for _, entry := range all {
		items = append(items, entry.view)
	}
	return items, next, nil
}

// GetReview returns one review to an administrator, its submitter, or the owner
// of its application. Everyone else gets ErrReviewNotVisible (404).
func (r *Reader) GetReview(
	ctx context.Context, callerID, reviewID string, admin bool,
) (*PublicationReviewDetail, error) {
	if !uuid.Valid(reviewID) {
		return nil, ErrReviewNotVisible
	}
	var (
		detail   PublicationReviewDetail
		snapshot string
	)
	err := r.DB.QueryRowContext(ctx, `
		SELECT r.id::text, r.state, r.manifest_sha256, r.report_id::text, r.snapshot::text
		  FROM platform.publication_reviews AS r
		  JOIN platform.applications AS a ON a.id = r.application_id
		 WHERE r.id = $1::uuid
		   AND (
		        $2::boolean
		        OR r.submitted_by = $3::uuid
		        OR a.owner_id = $3::uuid
		       )`,
		reviewID, admin, callerID,
	).Scan(&detail.ID, &detail.State, &detail.ManifestSHA256, &detail.ReportID, &snapshot)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrReviewNotVisible
	}
	if err != nil {
		return nil, fmt.Errorf("reading the publication review: %w", err)
	}
	detail.Snapshot = json.RawMessage(snapshot)
	return &detail, nil
}

// ---------------------------------------------------------------------------
// the run-creation seam later tickets build on
// ---------------------------------------------------------------------------

var (
	// ErrVersionRevoked is returned by ResolveRunnableVersion for a version whose
	// revoked_at is set (T070 owns the transition).
	ErrVersionRevoked = errors.New("application version is revoked")
	// ErrVersionArchived is returned for a version whose application is archived.
	ErrVersionArchived = errors.New("application version belongs to an archived application")
)

// ResolveRunnableVersion is the authoritative "may a new Run bind this published
// version" read. It returns the immutable Version row only when the caller may
// read the application, the version is not revoked and the application is not
// archived; revoked/archived are distinct errors so the caller can explain the
// refusal.
//
// T074 (#75) must call it before creating a Request/Run and copy the returned
// manifest hash/artifact into the published execution snapshot. T070 (#71) makes
// revocation a state change (`revoked_at`/`revoked_by`/`revoke_reason`), which
// this read observes immediately; a stale catalog index can only supply a
// candidate id, never bypass this check.
func (r *Reader) ResolveRunnableVersion(
	ctx context.Context, callerID, versionID string,
) (*Version, error) {
	if !uuid.Valid(versionID) {
		return nil, ErrNotVisible
	}
	var (
		version  Version
		manifest string
		ownerID  string
		revoked  sql.NullTime
		archived sql.NullTime
	)
	err := r.DB.QueryRowContext(ctx, `
		SELECT v.id::text, v.application_id::text, v.version_no, v.manifest_sha256,
		       v.manifest::text, a.owner_id::text, v.revoked_at, a.archived_at
		  FROM platform.application_versions AS v
		  JOIN platform.applications AS a ON a.id = v.application_id
		 WHERE v.id = $1::uuid`, versionID,
	).Scan(&version.ID, &version.ApplicationID, &version.VersionNo, &version.ManifestSHA256,
		&manifest, &ownerID, &revoked, &archived)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrNotVisible
	}
	if err != nil {
		return nil, fmt.Errorf("reading a runnable application version: %w", err)
	}
	allow, err := r.readAllowed(ctx, callerID, version.ApplicationID, ownerID)
	if err != nil {
		return nil, err
	}
	if !allow {
		return nil, ErrNotVisible
	}
	if archived.Valid {
		return nil, ErrVersionArchived
	}
	if revoked.Valid {
		return nil, ErrVersionRevoked
	}
	version.Manifest = json.RawMessage(manifest)
	return &version, nil
}
