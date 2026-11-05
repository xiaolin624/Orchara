package catalog

// F02B/T068: the publication evidence reader.
//
// A submission may only cite a report that is *really* an application report for
// the same frozen manifest, produced by the trusted runner, over the same dataset,
// and accepted by the completion guard. The submission does not trust the
// document's own declarations: it re-reads the immutable `evaluation_reports` row,
// the evaluation binding, the sealed snapshot's canonical manifest bytes and the
// sealed report bytes, and re-applies the T062 case-run rules to every passed
// case. The same verification runs again at approval, because the review's
// evidence must still be trustworthy when a version is created.

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"strings"

	"orchara/platform/internal/compiler"
	"orchara/platform/internal/evaluation"
	"orchara/platform/internal/jcs"
	"orchara/platform/internal/objectstore"
)

const (
	// maxPublicationReportBytes bounds the sealed report document this gate will
	// read. The completion guard uses the same order of magnitude; a report is a
	// JSON document of per-case evidence, not a data dump.
	maxPublicationReportBytes int64 = 8 << 20
	// PublicationSnapshotVersion is the review snapshot document version.
	PublicationSnapshotVersion = 1
)

var (
	// ErrReportNotVisible is both "unknown report" and "a report that does not
	// belong to this application/caller". The API answers 404 for either.
	ErrReportNotVisible = errors.New("evaluation report is not visible")
	// ErrReviewNotVisible is both "unknown review" and "a review the caller may
	// not read". The API answers 404 for either.
	ErrReviewNotVisible = errors.New("publication review is not visible")
)

// ReportRefusedError is a specific, non-HTTP refusal of the cited evidence.
// Code is the stable machine code the client sees; Message says what was wrong.
type ReportRefusedError struct {
	Code    string
	Message string
	Details map[string]any
}

func (e *ReportRefusedError) Error() string { return e.Message }

// reportExpectation is what the caller demands of the report.
type reportExpectation struct {
	ReportID       string
	ApplicationID  string
	OwnerID        string
	ManifestSHA256 string // expected frozen hash; required
	// CheckDraft, when non-nil, additionally requires the frozen manifest's
	// source_revision to be the draft's current execution_revision (the
	// "same evaluated content" rule at submission). Approval passes nil: the
	// review is bound to the frozen snapshot and the draft may have moved on.
	CheckDraft *Draft
}

// verifiedApplicationReport is the evidence a submission/approval may cite.
type verifiedApplicationReport struct {
	ReportID           string
	Status             string
	ManifestSHA256     string
	DatasetSHA256      string
	ResultArtifactID   string
	ReportArtifactSHA  string
	EvaluationID       string
	OwnerID            string
	SnapshotID         string
	DatasetArtifactID  string
	ManifestArtifactID string
	Manifest           *compiler.Manifest
	ManifestBytes      []byte
	Document           *evaluation.Report
	Coverage           AuthorCaseCoverage
}

// verifyApplicationReport re-proves every binding of one cited report.
func verifyApplicationReport(
	ctx context.Context,
	q dependencyQueryer,
	objects *objectstore.Store,
	expectation reportExpectation,
) (*verifiedApplicationReport, error) {
	if objects == nil {
		return nil, objectstore.ErrNotConfigured
	}
	// 1. The immutable report row.
	var report verifiedApplicationReport
	err := q.QueryRowContext(ctx, `
		SELECT id::text, manifest_sha256, dataset_sha256, result_artifact_id::text, status
		  FROM platform.evaluation_reports
		 WHERE id = $1::uuid`, expectation.ReportID,
	).Scan(&report.ReportID, &report.ManifestSHA256, &report.DatasetSHA256,
		&report.ResultArtifactID, &report.Status)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrReportNotVisible
	}
	if err != nil {
		return nil, fmt.Errorf("reading the cited evaluation report: %w", err)
	}

	// 2. The evaluation binding: exactly one application evaluation, owned by the
	// submitting creator, whose report this is. A report no evaluation owns (or
	// another application's report) is indistinguishable from a missing one.
	var (
		evaluationID      string
		evaluationKind    string
		evaluationOwner   string
		evaluationState   string
		snapshotID        sql.NullString
		datasetArtifactID sql.NullString
		applicationID     sql.NullString
	)
	rows, err := q.QueryContext(ctx, `
		SELECT id::text, kind, owner_id::text, state, snapshot_id::text,
		       dataset_artifact_id::text, application_id::text
		  FROM platform.evaluation_jobs
		 WHERE report_id = $1::uuid`, expectation.ReportID)
	if err != nil {
		return nil, fmt.Errorf("reading the report's evaluation: %w", err)
	}
	defer rows.Close()
	bindings := 0
	for rows.Next() {
		bindings++
		if bindings > 1 {
			break
		}
		if err := rows.Scan(&evaluationID, &evaluationKind, &evaluationOwner,
			&evaluationState, &snapshotID, &datasetArtifactID, &applicationID); err != nil {
			return nil, fmt.Errorf("reading the report's evaluation: %w", err)
		}
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("reading the report's evaluation: %w", err)
	}
	if bindings != 1 {
		return nil, ErrReportNotVisible
	}
	if evaluationKind != "application" ||
		!applicationID.Valid || !strings.EqualFold(applicationID.String, expectation.ApplicationID) ||
		!strings.EqualFold(evaluationOwner, expectation.OwnerID) ||
		evaluationState != "succeeded" ||
		!snapshotID.Valid || !datasetArtifactID.Valid {
		return nil, ErrReportNotVisible
	}
	report.EvaluationID = evaluationID
	report.OwnerID = evaluationOwner
	report.SnapshotID = snapshotID.String
	report.DatasetArtifactID = datasetArtifactID.String

	// The report row itself and the sealed document must both be `passed`.
	if report.Status != evaluation.StatusPassed {
		return nil, &ReportRefusedError{
			Code:    "evaluation_not_passed",
			Message: "the cited evaluation report is not passed; a publication review can only cite a passed report",
			Details: map[string]any{"report_status": report.Status},
		}
	}
	if !strings.EqualFold(report.ManifestSHA256, expectation.ManifestSHA256) {
		return nil, &ReportRefusedError{
			Code:    "report_binding_mismatch",
			Message: "the cited report is for a different manifest than the content being submitted",
			Details: map[string]any{
				"report_manifest_sha256":   strings.ToLower(report.ManifestSHA256),
				"expected_manifest_sha256": strings.ToLower(expectation.ManifestSHA256),
			},
		}
	}

	// 3. The sealed report bytes: read, hash-check, strictly decode, re-apply the
	// completion guard's shape/verdict rules.
	content, artifactSHA, err := readSealedPublicationBytes(ctx, q, objects, report.ResultArtifactID, maxPublicationReportBytes)
	if err != nil {
		return nil, err
	}
	report.ReportArtifactSHA = artifactSHA
	document, err := evaluation.DecodeReport(content)
	if err != nil {
		return nil, &ReportRefusedError{
			Code:    "report_not_trusted",
			Message: "the cited report document is not a valid sealed report",
		}
	}
	if reason := evaluation.ValidateShape(document); reason != "" {
		return nil, &ReportRefusedError{
			Code:    "report_not_trusted",
			Message: "the cited report document fails the report shape rules: " + reason,
		}
	}
	report.Document = document
	if document.Status != evaluation.StatusPassed || document.Synthetic ||
		document.Evaluator != evaluation.RunnerEvaluatorID ||
		document.EvaluationKind != "application" {
		return nil, &ReportRefusedError{
			Code:    "report_not_trusted",
			Message: "the cited report was not produced by the trusted application evaluation runner",
			Details: map[string]any{
				"evaluator": document.Evaluator,
				"synthetic": document.Synthetic,
			},
		}
	}
	if document.EvaluationID != evaluationID ||
		!strings.EqualFold(document.ManifestSHA256, report.ManifestSHA256) ||
		!strings.EqualFold(document.DatasetSHA256, report.DatasetSHA256) ||
		document.DatasetArtifactID != report.DatasetArtifactID {
		return nil, &ReportRefusedError{
			Code:    "report_binding_mismatch",
			Message: "the cited report's own document does not match the report/evaluation/dataset rows it is cited by",
		}
	}
	if document.Provenance == nil || document.Provenance.SnapshotID != report.SnapshotID {
		return nil, &ReportRefusedError{
			Code:    "report_binding_mismatch",
			Message: "the cited report does not name the frozen execution snapshot it must have run under",
		}
	}

	// 4. The frozen snapshot and its canonical manifest bytes.
	var (
		snapshotKind        string
		snapshotApplication string
		snapshotOwner       string
		snapshotManifest    string
		manifestArtifactID  sql.NullString
	)
	err = q.QueryRowContext(ctx, `
		SELECT kind, application_id::text, owner_id::text, manifest_sha256,
		       manifest_artifact_id::text
		  FROM platform.execution_snapshots
		 WHERE id = $1::uuid`, report.SnapshotID,
	).Scan(&snapshotKind, &snapshotApplication, &snapshotOwner, &snapshotManifest,
		&manifestArtifactID)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, &ReportRefusedError{
			Code:    "report_binding_mismatch",
			Message: "the cited report's frozen execution snapshot no longer exists",
		}
	}
	if err != nil {
		return nil, fmt.Errorf("reading the report's execution snapshot: %w", err)
	}
	if snapshotKind != "debug" ||
		!strings.EqualFold(snapshotApplication, expectation.ApplicationID) ||
		!strings.EqualFold(snapshotOwner, expectation.OwnerID) ||
		!strings.EqualFold(snapshotManifest, report.ManifestSHA256) ||
		!manifestArtifactID.Valid {
		return nil, &ReportRefusedError{
			Code:    "report_binding_mismatch",
			Message: "the cited report's frozen snapshot does not belong to this application/manifest",
		}
	}
	report.ManifestArtifactID = manifestArtifactID.String

	var (
		objectKey string
		storedSHA sql.NullString
		sizeBytes sql.NullInt64
		purpose   string
		state     string
		deleted   sql.NullTime
	)
	err = q.QueryRowContext(ctx, `
		SELECT object_key, sha256, size_bytes, purpose, state, deleted_at
		  FROM platform.artifacts
		 WHERE id = $1::uuid`, report.ManifestArtifactID,
	).Scan(&objectKey, &storedSHA, &sizeBytes, &purpose, &state, &deleted)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, &ReportRefusedError{
			Code:    "report_binding_mismatch",
			Message: "the frozen manifest artifact no longer exists",
		}
	}
	if err != nil {
		return nil, fmt.Errorf("reading the frozen manifest artifact: %w", err)
	}
	if purpose != "execution_manifest" || state != "ready" || deleted.Valid ||
		!storedSHA.Valid || !strings.EqualFold(storedSHA.String, snapshotManifest) ||
		!sizeBytes.Valid {
		return nil, &ReportRefusedError{
			Code:    "report_not_trusted",
			Message: "the frozen manifest artifact is not a sealed ready copy of the hashed manifest",
		}
	}
	manifest, manifestBytes, err := compiler.VerifyManifestBytes(objects, compiler.ArtifactRef{
		ObjectKey: objectKey, SHA256: storedSHA.String, SizeBytes: sizeBytes.Int64,
	})
	if err != nil {
		return nil, &ReportRefusedError{
			Code:    "report_not_trusted",
			Message: "the frozen manifest bytes do not verify: " + err.Error(),
		}
	}
	if !strings.EqualFold(manifest.ApplicationID, expectation.ApplicationID) {
		return nil, &ReportRefusedError{
			Code:    "report_binding_mismatch",
			Message: "the frozen manifest names a different application than the report",
		}
	}
	report.Manifest = manifest
	report.ManifestBytes = manifestBytes
	if expectation.CheckDraft != nil {
		if manifest.SourceRevision != expectation.CheckDraft.ExecutionRevision {
			return nil, &ReportRefusedError{
				Code:    "report_binding_mismatch",
				Message: "the cited report was produced for a different draft execution revision than the current one",
				Details: map[string]any{
					"report_execution_revision":  manifest.SourceRevision,
					"current_execution_revision": expectation.CheckDraft.ExecutionRevision,
				},
			}
		}
	}

	// 5. T062's per-case run binding, re-applied at the publication gate.
	if err := verifyReportCaseRuns(ctx, q, report); err != nil {
		return nil, err
	}

	// 6. The author-case minimum over the sealed evidence.
	report.Coverage = EvaluateAuthorCaseCoverage(document)
	return &report, nil
}

// verifyReportCaseRuns re-applies the T062 rule: a passed case must be backed by
// this evaluation's own case run, owned by the evaluation's owner, executed
// against the bound snapshot and manifest, and finished.
func verifyReportCaseRuns(
	ctx context.Context, q dependencyQueryer, report verifiedApplicationReport,
) error {
	for _, item := range report.Document.Cases {
		if item.Status != evaluation.VerdictPassed || item.Facts == nil ||
			strings.TrimSpace(item.Facts.RunID) == "" {
			return &ReportRefusedError{
				Code:    "report_binding_mismatch",
				Message: "a passed case carries no run evidence",
				Details: map[string]any{"case_id": item.ID},
			}
		}
		var (
			runID      string
			runState   string
			runOwner   string
			snapshotID sql.NullString
			manifest   sql.NullString
		)
		err := q.QueryRowContext(ctx, `
			SELECT r.id::text, r.state, r.owner_id::text,
			       r.execution_snapshot_id::text, r.manifest_sha256
			  FROM platform.evaluation_case_runs AS cr
			  JOIN platform.runs AS r ON r.id = cr.run_id
			 WHERE cr.evaluation_id = $1::uuid AND cr.case_id = $2`,
			report.EvaluationID, item.ID,
		).Scan(&runID, &runState, &runOwner, &snapshotID, &manifest)
		if errors.Is(err, sql.ErrNoRows) {
			return &ReportRefusedError{
				Code:    "report_binding_mismatch",
				Message: "a passed case names a run that is not this evaluation's case run",
				Details: map[string]any{"case_id": item.ID},
			}
		}
		if err != nil {
			return fmt.Errorf("reading the report's case run: %w", err)
		}
		if runID != item.Facts.RunID ||
			!strings.EqualFold(runOwner, report.OwnerID) ||
			!snapshotID.Valid || snapshotID.String != report.SnapshotID ||
			!manifest.Valid || !strings.EqualFold(manifest.String, report.ManifestSHA256) ||
			!evaluation.CompletedRunState(runState) ||
			(item.Facts.RunState != "" && item.Facts.RunState != runState) {
			return &ReportRefusedError{
				Code:    "report_binding_mismatch",
				Message: "a passed case is not backed by a finished run of this evaluation and frozen snapshot",
				Details: map[string]any{"case_id": item.ID},
			}
		}
	}
	return nil
}

// readSealedPublicationBytes reads one sealed artifact's bytes, re-verifying the
// recorded SHA-256 and size before returning them. Any mismatch is a refusal
// with the specific "not trustworthy" code, never a silent pass.
func readSealedPublicationBytes(
	ctx context.Context, q dependencyQueryer, objects *objectstore.Store,
	artifactID string, maxBytes int64,
) ([]byte, string, error) {
	var (
		objectKey string
		storedSHA sql.NullString
		sizeBytes sql.NullInt64
		state     string
		deleted   sql.NullTime
		purpose   string
	)
	err := q.QueryRowContext(ctx, `
		SELECT object_key, sha256, size_bytes, state, deleted_at, purpose
		  FROM platform.artifacts
		 WHERE id = $1::uuid`, artifactID,
	).Scan(&objectKey, &storedSHA, &sizeBytes, &state, &deleted, &purpose)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, "", &ReportRefusedError{
			Code:    "report_not_trusted",
			Message: "the cited report has no sealed artifact row",
		}
	}
	if err != nil {
		return nil, "", fmt.Errorf("reading the cited report artifact: %w", err)
	}
	if purpose != "report" || state != "ready" || deleted.Valid || !storedSHA.Valid ||
		!sizeBytes.Valid || sizeBytes.Int64 <= 0 || sizeBytes.Int64 > maxBytes {
		return nil, "", &ReportRefusedError{
			Code:    "report_not_trusted",
			Message: "the cited report artifact is not a ready sealed report",
		}
	}
	reader, _, err := objects.Open(objectKey)
	if err != nil {
		return nil, "", &ReportRefusedError{
			Code:    "report_not_trusted",
			Message: "the cited report artifact has no readable sealed bytes",
		}
	}
	defer func() { _ = reader.Close() }()
	content, err := io.ReadAll(io.LimitReader(reader, maxBytes+1))
	if err != nil {
		return nil, "", fmt.Errorf("reading the cited report bytes: %w", err)
	}
	if int64(len(content)) > maxBytes || int64(len(content)) != sizeBytes.Int64 {
		return nil, "", &ReportRefusedError{
			Code:    "report_not_trusted",
			Message: "the cited report bytes do not match the recorded size",
		}
	}
	if digest := jcs.SHA256Hex(content); !strings.EqualFold(digest, storedSHA.String) {
		return nil, "", &ReportRefusedError{
			Code:    "report_not_trusted",
			Message: "the cited report bytes do not match the recorded SHA-256",
		}
	}
	return content, strings.ToLower(storedSHA.String), nil
}

// ---------------------------------------------------------------------------
// the frozen review snapshot
// ---------------------------------------------------------------------------

type modelDestinationView struct {
	ModelProfileVersionID string `json:"model_profile_version_id"`
	Provider              string `json:"provider"`
	ModelID               string `json:"model_id"`
	DataDestination       string `json:"data_destination"`
}

type toolCapabilityView struct {
	ToolVersionID string `json:"tool_version_id"`
	ImageDigest   string `json:"image_digest,omitempty"`
	RiskClass     string `json:"risk_class"`
	Read          bool   `json:"read"`
	Write         bool   `json:"write"`
	Network       bool   `json:"network"`
}

type subappVersionView struct {
	ApplicationID   string `json:"application_id"`
	ApplicationName string `json:"application_name"`
	VersionID       string `json:"version_id"`
	VersionNo       int64  `json:"version_no"`
	ManifestSHA256  string `json:"manifest_sha256"`
}

type connectorPolicyView struct {
	VersionID    string `json:"version_id"`
	PolicySHA256 string `json:"policy_sha256"`
}

// publicationContent is the shared-definition projection the creator previews
// and the admin reviews. It deliberately carries only the shared definition:
// purpose card, model data destination, tool read/write/network, pinned
// sub-application versions and connector policies. No user content, object key,
// credential reference or another user's data appears here.
type publicationContent struct {
	ApplicationID      string                 `json:"application_id"`
	ApplicationName    string                 `json:"application_name"`
	DraftRevision      int64                  `json:"draft_revision"`
	ExecutionRevision  int64                  `json:"execution_revision"`
	ManifestSHA256     string                 `json:"manifest_sha256"`
	ManifestArtifactID string                 `json:"manifest_artifact_id"`
	PurposeCard        map[string]any         `json:"purpose_card"`
	ModelDestinations  []modelDestinationView `json:"model_destinations"`
	ToolCapabilities   []toolCapabilityView   `json:"tool_capabilities"`
	SubappVersions     []subappVersionView    `json:"subapp_versions"`
	ConnectorPolicies  []connectorPolicyView  `json:"connector_policies"`
}

// PublicationSnapshot is the frozen JSON an administrator reviews and a creator
// can read back. It is sealed in `publication_reviews.snapshot` at submission and
// never re-derived afterwards; approval reads it, it does not rewrite it.
type PublicationSnapshot struct {
	ReviewVersion      int                    `json:"review_version"`
	ApplicationID      string                 `json:"application_id"`
	ApplicationName    string                 `json:"application_name"`
	DraftRevision      int64                  `json:"draft_revision"`
	ExecutionRevision  int64                  `json:"execution_revision"`
	ManifestSHA256     string                 `json:"manifest_sha256"`
	ManifestArtifactID string                 `json:"manifest_artifact_id"`
	DatasetArtifactID  string                 `json:"dataset_artifact_id"`
	DatasetSHA256      string                 `json:"dataset_sha256"`
	EvaluationID       string                 `json:"evaluation_id"`
	ReportID           string                 `json:"report_id"`
	ReportArtifactID   string                 `json:"report_artifact_id"`
	ReportSHA256       string                 `json:"report_sha256"`
	ReportStatus       string                 `json:"report_status"`
	PurposeCard        map[string]any         `json:"purpose_card"`
	ModelDestinations  []modelDestinationView `json:"model_destinations"`
	ToolCapabilities   []toolCapabilityView   `json:"tool_capabilities"`
	SubappVersions     []subappVersionView    `json:"subapp_versions"`
	ConnectorPolicies  []connectorPolicyView  `json:"connector_policies"`
	AuthorCases        AuthorCaseCoverage     `json:"author_cases"`
}

// buildPublicationContent reads the shared-definition projection for one frozen
// manifest. It is used both by the creator preview and by the sealed submission
// snapshot so the two cannot drift.
func buildPublicationContent(
	ctx context.Context,
	q dependencyQueryer,
	applicationID, applicationName string,
	draft *Draft,
	manifest *compiler.Manifest,
	manifestSHA, manifestArtifactID string,
) (*publicationContent, error) {
	content := &publicationContent{
		ApplicationID:      applicationID,
		ApplicationName:    applicationName,
		DraftRevision:      draft.Revision,
		ExecutionRevision:  draft.ExecutionRevision,
		ManifestSHA256:     strings.ToLower(manifestSHA),
		ManifestArtifactID: manifestArtifactID,
		PurposeCard:        manifest.CapabilityCard,
		ModelDestinations:  []modelDestinationView{},
		ToolCapabilities:   []toolCapabilityView{},
		SubappVersions:     []subappVersionView{},
		ConnectorPolicies:  []connectorPolicyView{},
	}

	modelIDs := normalizedDependencyIDs(manifest.ModelProfileVersions)
	if len(modelIDs) > 0 {
		rows, err := q.QueryContext(ctx, `
			SELECT id::text, provider, model_id, data_destination
			  FROM platform.model_profile_versions
			 WHERE id::text = ANY($1::text[])`, modelIDs)
		if err != nil {
			return nil, fmt.Errorf("reading the frozen model destinations: %w", err)
		}
		defer rows.Close()
		byID := map[string]modelDestinationView{}
		for rows.Next() {
			var view modelDestinationView
			if err := rows.Scan(&view.ModelProfileVersionID, &view.Provider,
				&view.ModelID, &view.DataDestination); err != nil {
				return nil, fmt.Errorf("reading the frozen model destinations: %w", err)
			}
			byID[view.ModelProfileVersionID] = view
		}
		if err := rows.Err(); err != nil {
			return nil, fmt.Errorf("reading the frozen model destinations: %w", err)
		}
		for _, id := range modelIDs {
			if view, present := byID[id]; present {
				content.ModelDestinations = append(content.ModelDestinations, view)
			}
		}
	}

	toolPins := make([]compiler.ToolArtifact, 0, len(manifest.ToolArtifacts))
	toolIDs := make([]string, 0, len(manifest.ToolArtifacts))
	for _, artifact := range manifest.ToolArtifacts {
		toolPins = append(toolPins, artifact)
		toolIDs = append(toolIDs, normalizeDependencyID(artifact.ID))
	}
	if len(toolIDs) > 0 {
		rows, err := q.QueryContext(ctx, `
			SELECT id::text, risk_class, coalesce(image_digest, ''), connector_versions
			  FROM platform.tool_versions
			 WHERE id::text = ANY($1::text[])`, uniqueStrings(toolIDs))
		if err != nil {
			return nil, fmt.Errorf("reading the frozen tool capabilities: %w", err)
		}
		defer rows.Close()
		type toolFact struct {
			riskClass string
			digest    string
			network   bool
		}
		byID := map[string]toolFact{}
		for rows.Next() {
			var (
				id        string
				riskClass string
				digest    string
				raw       []byte
			)
			if err := rows.Scan(&id, &riskClass, &digest, &raw); err != nil {
				return nil, fmt.Errorf("reading the frozen tool capabilities: %w", err)
			}
			byID[id] = toolFact{
				riskClass: riskClass,
				digest:    digest,
				network:   len(connectorVersionIDs(raw)) > 0,
			}
		}
		if err := rows.Err(); err != nil {
			return nil, fmt.Errorf("reading the frozen tool capabilities: %w", err)
		}
		for _, pin := range toolPins {
			id := normalizeDependencyID(pin.ID)
			fact, present := byID[id]
			if !present {
				continue
			}
			content.ToolCapabilities = append(content.ToolCapabilities, toolCapabilityView{
				ToolVersionID: id,
				ImageDigest:   pin.ImageDigest,
				RiskClass:     fact.riskClass,
				Read:          fact.riskClass == "read" || fact.riskClass == "write",
				Write:         fact.riskClass == "write",
				Network:       fact.network,
			})
		}
	}

	subappIDs := normalizedDependencyIDs(manifest.SubappVersions)
	if len(subappIDs) > 0 {
		rows, err := q.QueryContext(ctx, `
			SELECT v.id::text, v.application_id::text, a.name, v.version_no, v.manifest_sha256
			  FROM platform.application_versions AS v
			  JOIN platform.applications AS a ON a.id = v.application_id
			 WHERE v.id::text = ANY($1::text[])`, subappIDs)
		if err != nil {
			return nil, fmt.Errorf("reading the pinned sub-application versions: %w", err)
		}
		defer rows.Close()
		byID := map[string]subappVersionView{}
		for rows.Next() {
			var view subappVersionView
			if err := rows.Scan(&view.VersionID, &view.ApplicationID, &view.ApplicationName,
				&view.VersionNo, &view.ManifestSHA256); err != nil {
				return nil, fmt.Errorf("reading the pinned sub-application versions: %w", err)
			}
			byID[view.VersionID] = view
		}
		if err := rows.Err(); err != nil {
			return nil, fmt.Errorf("reading the pinned sub-application versions: %w", err)
		}
		for _, id := range subappIDs {
			if view, present := byID[id]; present {
				content.SubappVersions = append(content.SubappVersions, view)
			}
		}
	}

	connectorIDs := make([]string, 0, len(manifest.ConnectorPolicies))
	for _, policy := range manifest.ConnectorPolicies {
		connectorIDs = append(connectorIDs, normalizeDependencyID(policy.VersionID))
	}
	if len(connectorIDs) > 0 {
		rows, err := q.QueryContext(ctx, `
			SELECT id::text, policy_sha256
			  FROM platform.connector_policies
			 WHERE id::text = ANY($1::text[])`, uniqueStrings(connectorIDs))
		if err != nil {
			return nil, fmt.Errorf("reading the pinned connector policies: %w", err)
		}
		defer rows.Close()
		byID := map[string]string{}
		for rows.Next() {
			var id, policySHA string
			if err := rows.Scan(&id, &policySHA); err != nil {
				return nil, fmt.Errorf("reading the pinned connector policies: %w", err)
			}
			byID[id] = policySHA
		}
		if err := rows.Err(); err != nil {
			return nil, fmt.Errorf("reading the pinned connector policies: %w", err)
		}
		for _, id := range connectorIDs {
			if policySHA, present := byID[id]; present {
				content.ConnectorPolicies = append(content.ConnectorPolicies, connectorPolicyView{
					VersionID: id, PolicySHA256: policySHA,
				})
			}
		}
	}
	return content, nil
}

// applicationName reads the shared display name and refuses an archived
// application (a submission and an approval both require a live application).
func applicationName(ctx context.Context, q dependencyQueryer, applicationID string) (string, bool, error) {
	var (
		name     string
		archived sql.NullTime
	)
	err := q.QueryRowContext(ctx, `
		SELECT name, archived_at FROM platform.applications WHERE id = $1::uuid`,
		applicationID,
	).Scan(&name, &archived)
	if errors.Is(err, sql.ErrNoRows) {
		return "", false, ErrNotVisible
	}
	if err != nil {
		return "", false, fmt.Errorf("reading the application: %w", err)
	}
	return name, !archived.Valid, nil
}

// marshalSnapshot encodes the sealed snapshot deterministically (JCS is not
// required for a jsonb column, but a stable encoding makes tests and support
// reads simple).
func marshalSnapshot(snapshot *PublicationSnapshot) ([]byte, error) {
	encoded, err := json.Marshal(snapshot)
	if err != nil {
		return nil, fmt.Errorf("encoding the publication snapshot: %w", err)
	}
	return encoded, nil
}
