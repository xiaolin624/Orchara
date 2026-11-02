// tool_run: the maintenance-side half of T030's trusted preset tool path.
//
// One job means: download the caller-owned ready CSV/XLSX artifact through a
// job-scoped signed link, run the fixed sales-statistics tool in a bounded
// subprocess, verify output.json and its artifacts, upload the verified summary
// JSON/CSV plus a capped log, and submit the summary artifact id. A user-input
// rejection uploads a safe diagnostics document and fails the job with the
// tool's specific code; no summary artifact is registered for a failed run.
//
// The handler never parses stdout/stderr, never names a command line, never sees
// an object key and never writes a business row. Completion is still the
// server's decision: the tool_run guard re-checks that the named summary
// artifact is the job's own and ready.
package handlers

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math/big"
	"strings"
	"time"

	"orchara/platform/internal/background"
	"orchara/platform/internal/jcs"
	"orchara/platform/internal/tools"
)

const (
	toolPurposeSummary     = "tool_summary"
	toolPurposeArtifact    = "tool_artifact"
	toolPurposeDiagnostics = "diagnostics"
	toolPurposeLog         = "log"

	toolDiagnosticsVersion = 1
	defaultToolPoll        = 100 * time.Millisecond
	defaultToolWait        = 60 * time.Second
)

// ToolRun is the compile-time tool_run handler.
func ToolRun(ctx context.Context, work background.HandlerWork) background.Outcome {
	if work.Scope == nil || work.Job == nil || work.Tools == nil {
		return background.Failed(background.CodeTransientInternal)
	}
	job := work.Job
	if job.InputArtifactID == nil || strings.TrimSpace(*job.InputArtifactID) == "" {
		return background.Failed(background.CodeToolInputInvalid)
	}
	artifactID := strings.TrimSpace(*job.InputArtifactID)
	if !strings.EqualFold(artifactID, job.TargetID) {
		// A tool_run job may only read its own target artifact; a disagreement is
		// a binding defect, not something a retry can fix.
		return background.Failed(background.CodeToolInputInvalid)
	}
	view, err := work.Scope.Status(ctx, job.ID, work.AttemptToken, artifactID)
	if err != nil {
		return toolRunScopeOutcome(err)
	}
	if view.State == "quarantined" || view.State == "deleted" {
		return background.Failed(background.CodeSourceDeleted)
	}
	if view.State != "ready" {
		return background.Failed(background.CodeToolInputInvalid)
	}
	mediaType := strings.ToLower(strings.TrimSpace(view.MediaType))
	if mediaType != tools.MediaTypeCSV && mediaType != tools.MediaTypeXLSX {
		return background.Failed(background.CodeToolInputInvalid)
	}

	link, err := work.Scope.Download(ctx, job.ID, work.AttemptToken, artifactID)
	if err != nil {
		return toolRunScopeOutcome(err)
	}
	input, err := work.Scope.FetchURL(ctx, link, tools.MaxInputBytes)
	if err != nil {
		// The link or the store may be momentarily unavailable; a fresh attempt
		// mints a fresh link and re-reads the same sealed bytes.
		return background.Failed(background.CodeTransientStorageIO)
	}
	if len(input) == 0 {
		return background.Failed(background.CodeToolInputInvalid)
	}
	inputSHA := sha256Hex(input)

	result, runErr := work.Tools.Run(ctx, tools.Request{
		Tool:       tools.ToolSalesStats,
		ArtifactID: artifactID,
		MediaType:  mediaType,
		Input:      input,
		SHA256:     inputSHA,
		SizeBytes:  int64(len(input)),
	})
	if runErr != nil {
		return toolRunFailure(ctx, work, mediaType, inputSHA, int64(len(input)), result, runErr)
	}

	summary, err := validateSalesSummary(result.Data)
	if err != nil {
		return toolOutputFailure(ctx, work, mediaType, inputSHA, int64(len(input)), result,
			background.CodeToolOutputInvalid, "工具返回的 data 不符合销售统计 Schema。")
	}
	summaryArtifact := findToolArtifact(result.Artifacts, "summary.json")
	csvArtifact := findToolArtifact(result.Artifacts, "summary.csv")
	if summaryArtifact == nil || csvArtifact == nil {
		return toolOutputFailure(ctx, work, mediaType, inputSHA, int64(len(input)), result,
			background.CodeToolOutputInvalid, "工具没有同时产出 summary.json 与 summary.csv。")
	}
	// The downloadable summary.json and output.json.data must be the same
	// document, canonically. This is what lets the page treat the artifact as
	// the verified Decimal result instead of trusting a file the tool could not
	// reconcile with its own structured output.
	dataCanonical, err := jcs.CanonicalizeBytes(result.Data)
	if err != nil {
		return toolOutputFailure(ctx, work, mediaType, inputSHA, int64(len(input)), result,
			background.CodeToolOutputInvalid, "工具 data 不是合法规范化 JSON。")
	}
	artifactCanonical, err := jcs.CanonicalizeBytes(summaryArtifact.Content)
	if err != nil {
		return toolOutputFailure(ctx, work, mediaType, inputSHA, int64(len(input)), result,
			background.CodeToolOutputInvalid, "summary.json 不是合法规范化 JSON。")
	}
	if !bytes.Equal(dataCanonical, artifactCanonical) {
		return toolOutputFailure(ctx, work, mediaType, inputSHA, int64(len(input)), result,
			background.CodeToolOutputInvalid, "summary.json 与 output.json.data 不一致。")
	}
	_ = summary

	summaryID, outcome, ok := uploadToolArtifact(
		ctx, work, "summary.json", toolPurposeSummary, "application/json", summaryArtifact.Content,
	)
	if !ok {
		return outcome
	}
	if _, outcome, ok := uploadToolArtifact(
		ctx, work, "summary.csv", toolPurposeArtifact, "text/csv", csvArtifact.Content,
	); !ok {
		return outcome
	}
	logID, outcome, ok := uploadToolArtifact(
		ctx, work, "log", toolPurposeLog, "text/plain", result.Log,
	)
	if !ok {
		return outcome
	}
	_ = logID
	if outcome, ready := waitForToolArtifacts(ctx, work, []string{summaryID}); !ready {
		return outcome
	}
	return background.SucceededWith(summaryID)
}

// toolRunFailure uploads the safe diagnostics/log for a failed tool execution
// and reports the mapped background failure code. Artifact upload failures never
// replace the real failure code: the job must fail with what actually happened.
func toolRunFailure(
	ctx context.Context,
	work background.HandlerWork,
	mediaType, inputSHA string,
	inputSize int64,
	result tools.Result,
	runErr error,
) background.Outcome {
	code := toolRunCode(runErr)
	message, fix := toolFailureText(code)
	location := map[string]any{}
	var rejection *tools.RejectionError
	if errors.As(runErr, &rejection) {
		message = rejection.Rejection.Message
		fix = rejection.Rejection.Fix
		if rejection.Rejection.Row != nil {
			location["row"] = *rejection.Rejection.Row
		}
		if rejection.Rejection.Column != "" {
			location["column"] = rejection.Rejection.Column
		}
		if rejection.Rejection.Cell != "" {
			location["cell"] = rejection.Rejection.Cell
		}
	}
	publishToolDiagnostics(ctx, work, diagnosticsInput{
		mediaType: mediaType,
		inputSHA:  inputSHA,
		sizeBytes: inputSize,
	}, code, message, fix, location, result.Log)
	return background.Failed(code)
}

// toolOutputFailure is a tool defect (invalid/missing/divergent output) reported
// as a business failure with a safe diagnostics document.
func toolOutputFailure(
	ctx context.Context,
	work background.HandlerWork,
	mediaType, inputSHA string,
	inputSize int64,
	result tools.Result,
	code, message string,
) background.Outcome {
	_, fix := toolFailureText(code)
	publishToolDiagnostics(ctx, work, diagnosticsInput{
		mediaType: mediaType,
		inputSHA:  inputSHA,
		sizeBytes: inputSize,
	}, code, message, fix, map[string]any{}, result.Log)
	return background.Failed(code)
}

// diagnosticsInput carries only safe platform facts: no object key, no path, no
// input bytes.
type diagnosticsInput struct {
	mediaType string
	inputSHA  string
	sizeBytes int64
}

// publishToolDiagnostics uploads the capped log first (so diagnostics can name
// it), then the diagnostics document. It is best effort: a storage failure here
// must not mask the tool outcome. Artifacts are linked to the job but the job is
// already failing, so none of them can become the result artifact.
func publishToolDiagnostics(
	ctx context.Context,
	work background.HandlerWork,
	input diagnosticsInput,
	code, message, fix string,
	location map[string]any,
	logBytes []byte,
) {
	logID := ""
	if len(logBytes) > 0 {
		if artifactID, _, err := uploadToolArtifactNoWait(
			ctx, work, "log", toolPurposeLog, "text/plain", logBytes,
		); err == nil {
			logID = artifactID
		}
	}
	document := map[string]any{
		"diagnostics_version": toolDiagnosticsVersion,
		"tool":                tools.ToolSalesStats,
		"status":              "rejected",
		"error_code":          code,
		"message":             message,
		"fix":                 fix,
		"retryable":           background.ClassifyFailure(background.KindToolRun, code) == background.FailureInfrastructure,
		"input": map[string]any{
			"artifact_id": jobInputArtifactID(work),
			"media_type":  input.mediaType,
			"size_bytes":  input.sizeBytes,
			"sha256":      input.inputSHA,
		},
	}
	if len(location) > 0 {
		document["location"] = location
	}
	if logID != "" {
		document["log_artifact_id"] = logID
	}
	payload, err := json.Marshal(document)
	if err != nil {
		return
	}
	payload = append(payload, '\n')
	_, _, _ = uploadToolArtifactNoWait(
		ctx, work, "diagnostics", toolPurposeDiagnostics, "application/json", payload,
	)
}

// uploadToolArtifact uploads one artifact and waits for the finalize guard to
// make it ready. A wait failure returns the outcome the handler must submit.
func uploadToolArtifact(
	ctx context.Context,
	work background.HandlerWork,
	name, purpose, mediaType string,
	content []byte,
) (string, background.Outcome, bool) {
	artifactID, idempotentReady, err := uploadToolArtifactNoWait(ctx, work, name, purpose, mediaType, content)
	if err != nil {
		return "", toolArtifactUploadOutcome(err), false
	}
	if idempotentReady {
		return artifactID, background.Succeeded(), true
	}
	if outcome, ready := waitForToolArtifacts(ctx, work, []string{artifactID}); !ready {
		return "", outcome, false
	}
	return artifactID, background.Succeeded(), true
}

// errToolByteIO is a PutURL failure: the staging bytes did not move.
var errToolByteIO = errors.New("the tool artifact bytes could not be transferred")

// toolArtifactUploadOutcome maps an upload/complete refusal. A byte-transfer
// failure is retryable IO; a scope refusal keeps its own meaning.
func toolArtifactUploadOutcome(err error) background.Outcome {
	if errors.Is(err, errToolByteIO) {
		return background.Failed(background.CodeTransientStorageIO)
	}
	if errors.Is(err, tools.ErrArtifactTooLarge) {
		return background.Failed(background.CodeToolArtifactTooLarge)
	}
	return toolRunScopeOutcome(err)
}

// uploadToolArtifactNoWait reserves/puts/completes one job-scoped artifact.
func uploadToolArtifactNoWait(
	ctx context.Context,
	work background.HandlerWork,
	name, purpose, mediaType string,
	content []byte,
) (string, bool, error) {
	if len(content) == 0 || int64(len(content)) > tools.MaxArtifactBytes {
		return "", false, tools.ErrArtifactTooLarge
	}
	ticket, err := work.Scope.Upload(ctx, work.Job.ID, work.AttemptToken, background.UploadRequest{
		MediaType: mediaType,
		SizeBytes: int64(len(content)),
		Purpose:   purpose,
	})
	if err != nil {
		return "", false, err
	}
	if err := work.Scope.PutURL(ctx, ticket.URL, content); err != nil {
		return "", false, errToolByteIO
	}
	sum := sha256.Sum256(content)
	view, err := work.Scope.Complete(
		ctx, work.Job.ID, work.AttemptToken, ticket.ArtifactID, hex.EncodeToString(sum[:]),
	)
	if err != nil {
		return "", false, err
	}
	_ = name
	if view.State == "ready" {
		return ticket.ArtifactID, true, nil
	}
	return ticket.ArtifactID, false, nil
}

// waitForToolArtifacts polls the job-scoped status until every artifact is
// ready, heartbeatting between checks. It never reads the artifact table: the
// status endpoint is the handler's only view of the finalize decision.
func waitForToolArtifacts(
	ctx context.Context,
	work background.HandlerWork,
	artifacts []string,
) (background.Outcome, bool) {
	timeout := work.Settings.ReportWaitTimeout
	if timeout <= 0 {
		timeout = defaultToolWait
	}
	poll := work.Settings.EvaluationPollInterval
	if poll <= 0 {
		poll = defaultToolPoll
	}
	deadline := time.Now().Add(timeout)
	for {
		if ctx.Err() != nil {
			return background.Failed(background.CodeTransientInternal), false
		}
		allReady := true
		for _, artifactID := range artifacts {
			view, err := work.Scope.Status(ctx, work.Job.ID, work.AttemptToken, artifactID)
			if err != nil {
				return toolRunScopeOutcome(err), false
			}
			switch view.State {
			case "ready":
			case "quarantined", "deleted":
				return background.Failed(background.CodeToolArtifactInvalid), false
			default:
				allReady = false
			}
		}
		if allReady {
			return background.Succeeded(), true
		}
		if time.Now().After(deadline) {
			// The finalize pool did not activate the output in time. Retrying is
			// honest: the next attempt waits again instead of claiming a success
			// the completion guard would reject.
			return background.Failed(background.CodeTransientStorageIO), false
		}
		if work.Heartbeat != nil {
			_ = work.Heartbeat(ctx)
		}
		select {
		case <-ctx.Done():
			return background.Failed(background.CodeTransientInternal), false
		case <-time.After(poll):
		}
	}
}

// toolRunCode maps an executor error to the fixed background failure vocabulary.
func toolRunCode(err error) string {
	var rejection *tools.RejectionError
	switch {
	case errors.As(err, &rejection):
		return rejection.Rejection.Code
	case errors.Is(err, tools.ErrTimeout):
		return background.CodeToolTimeout
	case errors.Is(err, tools.ErrCrashed):
		return background.CodeToolCrash
	case errors.Is(err, tools.ErrOutputMissing), errors.Is(err, tools.ErrOutputInvalid):
		return background.CodeToolOutputInvalid
	case errors.Is(err, tools.ErrArtifactEscape):
		return background.CodeToolArtifactEscape
	case errors.Is(err, tools.ErrArtifactSymlink):
		return background.CodeToolArtifactSymlink
	case errors.Is(err, tools.ErrArtifactTooLarge):
		return background.CodeToolArtifactTooLarge
	case errors.Is(err, tools.ErrTooManyArtifacts):
		return background.CodeToolArtifactCount
	case errors.Is(err, tools.ErrArtifactInvalid), errors.Is(err, tools.ErrArtifactNotFile):
		return background.CodeToolArtifactInvalid
	case errors.Is(err, tools.ErrPythonMissing), errors.Is(err, tools.ErrScriptMissing):
		// A deployment problem, retried until the attempt ceiling; the
		// maintenance startup line and report name the configuration knob.
		return background.CodeTransientInternal
	default:
		return background.CodeTransientInternal
	}
}

func toolFailureText(code string) (message, fix string) {
	switch code {
	case background.CodeToolTimeout:
		return "工具执行超过单次运行时间上限，未产生统计结果。",
			"请稍后重试；若同一文件持续超时，请缩小文件或联系平台维护者调大工具超时。"
	case background.CodeToolCrash:
		return "工具异常退出，未产生可信的统计结果。",
			"请稍后重试；若同一文件持续失败，请联系平台维护者查看受限日志。"
	case background.CodeToolOutputInvalid:
		return "工具输出不符合平台协议，结果未被接受。",
			"请重试；若持续出现，说明预置工具版本有缺陷，请联系平台维护者。"
	case background.CodeToolArtifactInvalid:
		return "工具产出的文件未通过平台准入（缺失/非普通文件/为空）。",
			"请重试；若持续出现，请联系平台维护者检查预置工具。"
	case background.CodeToolArtifactEscape:
		return "工具试图写出 artifacts 目录之外的路径，已被拒绝。",
			"请重试；若持续出现，说明预置工具版本有缺陷，请联系平台维护者。"
	case background.CodeToolArtifactSymlink:
		return "工具产出的文件是符号链接，已被拒绝。",
			"请重试；若持续出现，说明预置工具版本有缺陷，请联系平台维护者。"
	case background.CodeToolArtifactTooLarge:
		return "工具产出的文件超过平台单文件大小上限。",
			"请缩小输入文件后重试。"
	case background.CodeToolArtifactCount:
		return "工具产出的文件数量超过平台上限。",
			"请重试；若持续出现，说明预置工具版本有缺陷，请联系平台维护者。"
	default:
		return "预置工具未能完成本次运行。", "请稍后重试。"
	}
}

func jobInputArtifactID(work background.HandlerWork) string {
	if work.Job == nil || work.Job.InputArtifactID == nil {
		return ""
	}
	return *work.Job.InputArtifactID
}

func findToolArtifact(artifacts []tools.Artifact, name string) *tools.Artifact {
	for index := range artifacts {
		if artifacts[index].Name == name {
			return &artifacts[index]
		}
	}
	return nil
}

// toolRunScopeOutcome maps a job-scoped API refusal onto the tool_run failure
// vocabulary. Only codes in the fixed retry table are retried.
func toolRunScopeOutcome(err error) background.Outcome {
	var coded interface{ ErrorCode() string }
	if errors.As(err, &coded) {
		switch coded.ErrorCode() {
		case "stale_attempt":
			return background.Failed(background.CodeStaleAttempt)
		case "artifact_not_found":
			return background.Failed(background.CodeSourceDeleted)
		case "artifact_not_ready", "artifact_conflict":
			return background.Failed(background.CodeToolInputInvalid)
		case "object_store_unavailable", "object_signing_unavailable":
			return background.Failed(background.CodeObjectStoreUnavailable)
		}
	}
	return background.Failed(background.CodeTransientInternal)
}

// ---------------------------------------------------------------------------
// sales summary schema
// ---------------------------------------------------------------------------

// salesSummary is the only data shape this preset tool may return. Every amount
// is a two-decimal string: no float, no exponent, no locale formatting.
type salesSummary struct {
	Total      *string           `json:"total"`
	Regions    map[string]string `json:"regions"`
	OrderCount *int              `json:"order_count"`
}

func validateSalesSummary(raw []byte) (*salesSummary, error) {
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	var summary salesSummary
	if err := decoder.Decode(&summary); err != nil {
		return nil, err
	}
	if err := decoder.Decode(&struct{}{}); !errors.Is(err, io.EOF) {
		return nil, errors.New("trailing data")
	}
	if summary.Total == nil || !isMoneyString(*summary.Total) {
		return nil, errors.New("total must be a non-negative two-decimal string")
	}
	if len(summary.Regions) == 0 || len(summary.Regions) > 10_000 {
		return nil, errors.New("regions must contain at least one region")
	}
	regionSum := new(big.Int)
	for region, total := range summary.Regions {
		if strings.TrimSpace(region) == "" {
			return nil, errors.New("a region key is empty")
		}
		cents, ok := parseMoneyCents(total)
		if !ok {
			return nil, fmt.Errorf("region %q is not a two-decimal string", region)
		}
		regionSum.Add(regionSum, cents)
	}
	if summary.OrderCount == nil || *summary.OrderCount < 1 {
		return nil, errors.New("order_count must be at least 1")
	}
	totalCents, ok := parseMoneyCents(*summary.Total)
	if !ok || totalCents.Cmp(regionSum) != 0 {
		return nil, errors.New("total does not equal the sum of region totals")
	}
	return &summary, nil
}

// isMoneyString accepts "0.00", "250.00" and other non-negative amounts with
// exactly two decimal places. Negative totals are impossible because the tool
// rejects negative inputs; refusing them here keeps the boundary closed.
func isMoneyString(value string) bool {
	_, ok := parseMoneyCents(value)
	return ok
}

// parseMoneyCents parses a non-negative decimal string with exactly two decimal
// places into an integer number of cents. No float is involved.
func parseMoneyCents(value string) (*big.Int, bool) {
	if value == "" || len(value) > 40 {
		return nil, false
	}
	dot := strings.IndexByte(value, '.')
	if dot <= 0 || dot != len(value)-3 {
		return nil, false
	}
	if value[0] == '+' || value[0] == '-' {
		return nil, false
	}
	whole := value[:dot]
	fraction := value[dot+1:]
	if len(whole) > 1 && whole[0] == '0' {
		return nil, false
	}
	for _, char := range whole {
		if char < '0' || char > '9' {
			return nil, false
		}
	}
	for _, char := range fraction {
		if char < '0' || char > '9' {
			return nil, false
		}
	}
	cents := new(big.Int)
	cents.SetString(whole+fraction, 10)
	return cents, true
}
