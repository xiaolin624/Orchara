package requests

// F08/T082: the minimal cross-application handover.
//
// A handover is not a free-text note. The server builds a bounded, metadata-only
// preview from the source Runs the user names and the artifacts the user selects;
// the submitted `RequestInput.handoff.summary` must be exactly that preview. This
// is the server-side meaning of "confirm the minimum before switching": the new
// application's sealed input closure is the new message, the explicitly selected
// artifacts and the confirmed summary — never the old request's text, the
// conversation transcript or the old application's personal memory.
//
// The preview is a non-contract HTTP surface (documented in ADR 0030). The
// contract's handoff object keeps its four members; nothing is widened.

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"strings"
	"time"

	"orchara/platform/internal/jcs"
)

// Minimal-scope bounds. The same numbers are enforced by the handoff migration's
// CHECK constraints and by the page helpers.
const (
	MaxHandoffSourceRuns   = 5
	MaxHandoffArtifacts    = 5
	MaxHandoffSummaryRunes = 2000
)

// CodeHandoffScopeExceeded is the located refusal for a handover that asks for
// more than the confirmed minimum (too many ids, an artifact outside the selected
// source scope, or a summary the server did not produce).
const CodeHandoffScopeExceeded = "handoff_scope_exceeded"

// CodeHandoffSourceUnknown is shared with T074: a source Run that is missing,
// foreign or in another conversation is refused without saying which.
const CodeHandoffSourceUnknown = "handoff_source_unknown"

var handoffPreviewFields = map[string]bool{
	"source_run_ids": true,
	"artifact_ids":   true,
}

// HandoffPreviewInput is the raw preview body.
type HandoffPreviewInput struct {
	SourceRunIDs []string
	ArtifactIDs  []string
}

// HandoffSourceRun is one source Run fact shown in the preview.
type HandoffSourceRun struct {
	RunID           string  `json:"run_id"`
	ApplicationID   string  `json:"application_id"`
	ApplicationName string  `json:"application_name"`
	VersionID       *string `json:"version_id"`
	VersionNo       int64   `json:"version_no"`
	CreatedAt       string  `json:"created_at"`
}

// HandoffArtifact is one selected attachment fact shown in the preview.
type HandoffArtifact struct {
	ArtifactID string `json:"artifact_id"`
	Purpose    string `json:"purpose"`
	MediaType  string `json:"media_type"`
	SizeBytes  int64  `json:"size_bytes"`
	SHA256     string `json:"sha256"`
}

// HandoffLimits documents the enforced minimal-scope bounds on the wire.
type HandoffLimits struct {
	MaxSourceRuns   int `json:"max_source_runs"`
	MaxArtifacts    int `json:"max_artifacts"`
	MaxSummaryRunes int `json:"max_summary_runes"`
}

// HandoffPreview is the server-produced handover scope. The page copies
// `summary`, `source_run_ids` and `artifact_ids` verbatim into the submit body.
type HandoffPreview struct {
	ConversationID string   `json:"conversation_id"`
	FromVersionID  *string  `json:"from_version_id"`
	SourceRunIDs   []string `json:"source_run_ids"`
	ArtifactIDs    []string `json:"artifact_ids"`
	Summary        string   `json:"summary"`
	SummarySHA256  string   `json:"summary_sha256"`

	SourceRuns []HandoffSourceRun `json:"source_runs"`
	Artifacts  []HandoffArtifact  `json:"artifacts"`
	Limits     HandoffLimits      `json:"limits"`
}

// HandoffLimitsOf returns the enforced bounds (used by the handler description).
func HandoffLimitsOf() HandoffLimits {
	return HandoffLimits{
		MaxSourceRuns:   MaxHandoffSourceRuns,
		MaxArtifacts:    MaxHandoffArtifacts,
		MaxSummaryRunes: MaxHandoffSummaryRunes,
	}
}

// ParseHandoffPreview validates the preview body. Counts beyond the minimal scope
// are a located 422 before any row is read.
func ParseHandoffPreview(fields map[string]json.RawMessage) (*HandoffPreviewInput, *Error) {
	unknown := make([]string, 0)
	for key := range fields {
		if !handoffPreviewFields[key] {
			unknown = append(unknown, key)
		}
	}
	if len(unknown) > 0 {
		return nil, located("handoff", "the handoff preview body contains unknown members: "+
			strings.Join(sorted(unknown), ", "))
	}
	rawSources, present := fields["source_run_ids"]
	if !present {
		return nil, located("source_run_ids", "source_run_ids is required")
	}
	sources, parseErr := uuidList(rawSources, "source_run_ids", 50)
	if parseErr != nil {
		return nil, parseErr
	}
	if len(sources) == 0 {
		return nil, located("source_run_ids", "a handover preview needs at least one source Run")
	}
	if len(sources) > MaxHandoffSourceRuns {
		return nil, handoffTooBroad("source_run_ids", MaxHandoffSourceRuns)
	}
	rawArtifacts, present := fields["artifact_ids"]
	if !present {
		return nil, located("artifact_ids", "artifact_ids is required (send [] for none)")
	}
	artifacts, parseErr := uuidList(rawArtifacts, "artifact_ids", 50)
	if parseErr != nil {
		return nil, parseErr
	}
	if len(artifacts) > MaxHandoffArtifacts {
		return nil, handoffTooBroad("artifact_ids", MaxHandoffArtifacts)
	}
	return &HandoffPreviewInput{SourceRunIDs: sources, ArtifactIDs: artifacts}, nil
}

func handoffTooBroad(field string, limit int) *Error {
	return failureWithDetails(422, CodeHandoffScopeExceeded,
		fmt.Sprintf("%s exceeds the minimal handover scope (%d)", field, limit),
		map[string]any{"field": "handoff." + field, "limit": limit})
}

func handoffSummaryMismatch() *Error {
	return failureWithDetails(422, CodeHandoffScopeExceeded,
		"the handover summary is not the server preview for this exact scope; generate the preview and confirm it verbatim",
		map[string]any{"field": "handoff.summary", "reason": "summary_not_server_generated"})
}

// handoffRunFact is one immutable source-Run fact. Only immutable members are used
// so a preview stays reproducible while the Run's state advances.
type handoffRunFact struct {
	RunID           string
	ApplicationID   string
	ApplicationName string
	VersionID       sql.NullString
	VersionNo       int64
	CreatedAt       time.Time
}

// handoffRunFacts reads and authorises the named source Runs. Every Run must be
// the caller's own, live, part of this conversation and bound to a published
// version; otherwise the whole handover is the same located 422.
func (s *Service) handoffRunFacts(
	ctx context.Context, callerID, conversationID string, runIDs []string,
) ([]handoffRunFact, error) {
	facts := make([]handoffRunFact, 0, len(runIDs))
	for index, runID := range runIDs {
		var (
			fact      handoffRunFact
			ownerID   string
			convID    string
			version   sql.NullString
			appID     sql.NullString
			appName   sql.NullString
			versionNo sql.NullInt64
		)
		err := s.DB.QueryRowContext(ctx, `
			SELECT u.id::text, u.owner_id::text, u.conversation_id::text,
			       u.application_version_id::text, v.application_id::text,
			       a.name, v.version_no, u.created_at
			  FROM platform.runs AS u
			  LEFT JOIN platform.application_versions AS v
			         ON v.id = u.application_version_id
			  LEFT JOIN platform.applications AS a ON a.id = v.application_id
			 WHERE u.id = $1::uuid AND u.deleted_at IS NULL`,
			runID,
		).Scan(&fact.RunID, &ownerID, &convID, &version, &appID, &appName, &versionNo,
			&fact.CreatedAt)
		if errors.Is(err, sql.ErrNoRows) {
			return nil, handoffSourceUnknown(index, "the source Run does not exist")
		}
		if err != nil {
			return nil, fmt.Errorf("reading a handover source Run: %w", err)
		}
		if !strings.EqualFold(ownerID, callerID) {
			return nil, handoffSourceUnknown(index, "the source Run belongs to another principal")
		}
		if !strings.EqualFold(convID, conversationID) {
			return nil, handoffSourceUnknown(index, "the source Run is not part of this conversation")
		}
		if !version.Valid || !appID.Valid {
			return nil, handoffSourceUnknown(index,
				"the source Run has no published application version")
		}
		fact.VersionID = version
		fact.ApplicationID = appID.String
		if !appName.Valid {
			fact.ApplicationName = "未命名应用"
		} else {
			fact.ApplicationName = appName.String
		}
		if versionNo.Valid {
			fact.VersionNo = versionNo.Int64
		}
		facts = append(facts, fact)
	}
	// The preview order is a total order so two requests cannot disagree.
	sort.Slice(facts, func(i, j int) bool {
		if !facts[i].CreatedAt.Equal(facts[j].CreatedAt) {
			return facts[i].CreatedAt.Before(facts[j].CreatedAt)
		}
		return facts[i].RunID < facts[j].RunID
	})
	return facts, nil
}

func handoffSourceUnknown(index int, message string) *Error {
	return failureWithDetails(422, CodeHandoffSourceUnknown, message, map[string]any{
		"field": fmt.Sprintf("handoff.source_run_ids/%d", index),
	})
}

// handoffArtifactFacts reads the metadata of the selected artifacts. Ownership and
// readiness were already checked by checkArtifacts (the shared T074 vocabulary), so
// a missing row here is an internal inconsistency, never a silent omission.
func (s *Service) handoffArtifactFacts(
	ctx context.Context, artifactIDs []string,
) ([]HandoffArtifact, error) {
	if len(artifactIDs) == 0 {
		return []HandoffArtifact{}, nil
	}
	rows, err := s.DB.QueryContext(ctx, `
		SELECT id::text, purpose, media_type, coalesce(sha256, ''), coalesce(size_bytes, 0)
		  FROM platform.artifacts
		 WHERE id = ANY($1::uuid[])`, artifactIDs)
	if err != nil {
		return nil, fmt.Errorf("reading the handover artifacts: %w", err)
	}
	defer rows.Close()
	found := make(map[string]HandoffArtifact, len(artifactIDs))
	for rows.Next() {
		var artifact HandoffArtifact
		if err := rows.Scan(&artifact.ArtifactID, &artifact.Purpose, &artifact.MediaType,
			&artifact.SHA256, &artifact.SizeBytes); err != nil {
			return nil, fmt.Errorf("reading the handover artifacts: %w", err)
		}
		found[artifact.ArtifactID] = artifact
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("reading the handover artifacts: %w", err)
	}
	out := make([]HandoffArtifact, 0, len(artifactIDs))
	for _, id := range artifactIDs {
		artifact, present := found[id]
		if !present {
			return nil, errors.New("a confirmed handover artifact disappeared during the read")
		}
		out = append(out, artifact)
	}
	return out, nil
}

// handoffNewestVersion is the version of the newest source Run: the application
// that is handing over, for the handover record and the page.
func handoffNewestVersion(facts []handoffRunFact) *string {
	var newest time.Time
	versionID := ""
	for _, fact := range facts {
		if !fact.VersionID.Valid {
			continue
		}
		if versionID == "" || fact.CreatedAt.After(newest) {
			newest = fact.CreatedAt
			versionID = fact.VersionID.String
		}
	}
	if versionID == "" {
		return nil
	}
	return &versionID
}

// buildHandoffSummary is the one summary generator. It uses metadata only —
// application name, version number, Run id, created time and artifact purpose/
// type/size/hash — never the sealed message bytes or a memory body. Every field
// is clamped so the result can never exceed MaxHandoffSummaryRunes.
func buildHandoffSummary(runs []handoffRunFact, artifacts []HandoffArtifact) string {
	var builder strings.Builder
	builder.WriteString("本次交接摘要（服务端生成，仅含元数据）：\n")
	fmt.Fprintf(&builder, "来源 Run（%d 个）：\n", len(runs))
	for _, run := range runs {
		version := "未发布版本"
		if run.VersionID.Valid {
			version = fmt.Sprintf("v%d", run.VersionNo)
		}
		fmt.Fprintf(&builder, "- 应用「%s」%s（Run %s，%s）\n",
			clampRunes(run.ApplicationName, 80), version, shortID(run.RunID, 8),
			run.CreatedAt.UTC().Format(time.RFC3339))
	}
	fmt.Fprintf(&builder, "附件（%d 个）：\n", len(artifacts))
	if len(artifacts) == 0 {
		builder.WriteString("- 无\n")
	}
	for _, artifact := range artifacts {
		fmt.Fprintf(&builder, "- 用途 %s，类型 %s，大小 %d 字节，sha256 %s\n",
			clampRunes(artifact.Purpose, 60), clampRunes(artifact.MediaType, 80),
			artifact.SizeBytes, shortID(artifact.SHA256, 16))
	}
	builder.WriteString("交接边界：仅上述摘要与附件进入新应用；旧应用的内部历史、对话正文与个人记忆不在其中。\n")
	summary := builder.String()
	if runes := []rune(summary); len(runes) > MaxHandoffSummaryRunes {
		return string(runes[:MaxHandoffSummaryRunes])
	}
	return summary
}

func clampRunes(value string, limit int) string {
	runes := []rune(value)
	if len(runes) <= limit {
		return value
	}
	return string(runes[:limit]) + "…"
}

func shortID(value string, length int) string {
	if len(value) <= length {
		return value
	}
	return value[:length]
}

// HandoffPreview builds the minimal handover scope for one conversation.
func (s *Service) HandoffPreview(
	ctx context.Context, callerID, conversationID string, input *HandoffPreviewInput,
) (*HandoffPreview, error) {
	caller, ok := requireUUID(callerID)
	if !ok {
		return nil, failure(401, "unauthenticated", "an authenticated human is required")
	}
	conversation, ok := requireUUID(conversationID)
	if !ok {
		return nil, conversationNotFound()
	}
	if input == nil {
		return nil, failure(422, "validation_failed", "a parsed handover preview body is required")
	}
	if len(input.SourceRunIDs) == 0 {
		return nil, located("source_run_ids", "a handover preview needs at least one source Run")
	}
	if len(input.SourceRunIDs) > MaxHandoffSourceRuns {
		return nil, handoffTooBroad("source_run_ids", MaxHandoffSourceRuns)
	}
	if len(input.ArtifactIDs) > MaxHandoffArtifacts {
		return nil, handoffTooBroad("artifact_ids", MaxHandoffArtifacts)
	}
	alive, err := s.conversationAlive(ctx, caller, conversation)
	if err != nil {
		return nil, err
	}
	if !alive {
		return nil, conversationNotFound()
	}
	if err := s.checkArtifacts(ctx, caller, input.ArtifactIDs); err != nil {
		return nil, err
	}
	runs, err := s.handoffRunFacts(ctx, caller, conversation, input.SourceRunIDs)
	if err != nil {
		return nil, err
	}
	artifacts, err := s.handoffArtifactFacts(ctx, input.ArtifactIDs)
	if err != nil {
		return nil, err
	}
	summary := buildHandoffSummary(runs, artifacts)
	preview := &HandoffPreview{
		ConversationID: conversation,
		FromVersionID:  handoffNewestVersion(runs),
		SourceRunIDs:   append([]string(nil), input.SourceRunIDs...),
		ArtifactIDs:    append([]string(nil), input.ArtifactIDs...),
		Summary:        summary,
		SummarySHA256:  jcs.SHA256Hex([]byte(summary)),
		SourceRuns:     make([]HandoffSourceRun, 0, len(runs)),
		Artifacts:      artifacts,
		Limits:         HandoffLimitsOf(),
	}
	for _, run := range runs {
		var versionID *string
		if run.VersionID.Valid {
			value := run.VersionID.String
			versionID = &value
		}
		preview.SourceRuns = append(preview.SourceRuns, HandoffSourceRun{
			RunID:           run.RunID,
			ApplicationID:   run.ApplicationID,
			ApplicationName: run.ApplicationName,
			VersionID:       versionID,
			VersionNo:       run.VersionNo,
			CreatedAt:       run.CreatedAt.UTC().Format(time.RFC3339Nano),
		})
	}
	return preview, nil
}

// verifyHandoffScope is the submit-time confirmation gate. It returns the version
// of the newest source Run for the durable handover record.
//
// Order matters: the source Runs are authorised first (T074's located
// handoff_source_unknown), then the artifact scope is checked against exactly what
// the user selected for the new request plus the source Runs' produced artifacts,
// and only an allowed set is read back as metadata. The summary is compared last.
// A hand-written transcript can therefore never be submitted as a "confirmed"
// minimal handover.
func (s *Service) verifyHandoffScope(
	ctx context.Context, callerID, conversationID string, handoff *HandoffInput,
	requestArtifactIDs []string,
) (string, error) {
	if handoff == nil {
		return "", nil
	}
	if len(handoff.SourceRunIDs) == 0 {
		return "", located("handoff.source_run_ids", "handoff must name at least one source Run")
	}
	if len(handoff.SourceRunIDs) > MaxHandoffSourceRuns {
		return "", handoffTooBroad("source_run_ids", MaxHandoffSourceRuns)
	}
	if len(handoff.ArtifactIDs) > MaxHandoffArtifacts {
		return "", handoffTooBroad("artifact_ids", MaxHandoffArtifacts)
	}
	runs, err := s.handoffRunFacts(ctx, callerID, conversationID, handoff.SourceRunIDs)
	if err != nil {
		return "", err
	}
	// The allowed scope needs only ids. Check it before any artifact metadata is
	// read, so a foreign/unknown id is always the located 422 and never an
	// existence oracle (a disappeared id used to reach a 500 first).
	allowed, err := s.allowedHandoffArtifacts(ctx, handoff.SourceRunIDs, requestArtifactIDs)
	if err != nil {
		return "", err
	}
	outside := make([]string, 0)
	for _, id := range handoff.ArtifactIDs {
		if !allowed[id] {
			outside = append(outside, id)
		}
	}
	if len(outside) > 0 {
		return "", failureWithDetails(422, CodeHandoffScopeExceeded,
			"the handover includes artifacts that are neither produced by the source Runs nor explicitly attached to this request",
			map[string]any{"field": "handoff.artifact_ids", "artifact_ids": outside})
	}
	artifacts, err := s.handoffArtifactFacts(ctx, handoff.ArtifactIDs)
	if err != nil {
		return "", err
	}
	if buildHandoffSummary(runs, artifacts) != handoff.Summary {
		return "", handoffSummaryMismatch()
	}
	if version := handoffNewestVersion(runs); version != nil {
		return *version, nil
	}
	return "", nil
}

// allowedHandoffArtifacts is the exact artifact scope a handover may name: the
// artifacts the selected source Runs produced (never their `input` links, which
// are the old request's sealed text) plus the attachments the user explicitly
// attached to the new request.
func (s *Service) allowedHandoffArtifacts(
	ctx context.Context, sourceRunIDs, requestArtifactIDs []string,
) (map[string]bool, error) {
	allowed := make(map[string]bool, len(requestArtifactIDs))
	for _, id := range requestArtifactIDs {
		allowed[id] = true
	}
	rows, err := s.DB.QueryContext(ctx, `
		SELECT DISTINCT artifact_id::text
		  FROM platform.run_artifacts
		 WHERE run_id = ANY($1::uuid[])
		   AND relation <> 'input'`, sourceRunIDs)
	if err != nil {
		return nil, fmt.Errorf("reading the produced handover artifacts: %w", err)
	}
	defer rows.Close()
	for rows.Next() {
		var id string
		if err := rows.Scan(&id); err != nil {
			return nil, fmt.Errorf("reading the produced handover artifacts: %w", err)
		}
		allowed[id] = true
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("reading the produced handover artifacts: %w", err)
	}
	return allowed, nil
}
