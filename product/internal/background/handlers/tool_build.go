// tool_build: the maintenance-side half of T037's sandboxed tool build.
//
// One job means: learn the admitted source hash and canonical dependency lock
// through the job-scoped binding, download the source zip through a signed link,
// run the checked-in deploy/sandbox/build_tool.py inside the T033 gVisor bundle,
// upload the capped build log and the SBOM through the job-scoped upload flow,
// and submit the SBOM as the result artifact.
//
// The handler never reads or writes the tool row, never names a command line,
// never sees an object key and never starts a process outside the sandbox. The
// build's own build.json is untrusted output: every fact it claims is checked
// against the bytes this process downloaded/received, and the completion guard
// re-reads the sealed source+SBOM and derives the authoritative bundle digest.
package handlers

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"strings"
	"time"

	"orchara/platform/internal/background"
	"orchara/platform/internal/jcs"
	"orchara/platform/internal/sandbox"
	"orchara/platform/internal/tools"
)

const (
	buildPurposeSBOM = "sbom"
	buildPurposeLog  = "build_log"
	buildPurposeDiag = "diagnostics"

	// DefaultBuildTimeout bounds one sandboxed build. It is longer than a preset
	// tool run because the untrusted setup.py hook may compile a source tree.
	DefaultBuildTimeout = 5 * time.Minute

	buildDocumentVersion = 1
	maxBuildSourceBytes  = 20 << 20
	maxBuildSBOMBytes    = 4 << 20
	maxBuildLogBytes     = 256 * 1024
)

// buildDocument is deploy/sandbox/build_tool.py's /workspace/build.json.
type buildDocument struct {
	BuildVersion         int             `json:"build_version"`
	Status               string          `json:"status"`
	ErrorCode            string          `json:"error_code"`
	Message              string          `json:"message"`
	SourceSHA256         string          `json:"source_sha256"`
	DependencyLockSHA256 string          `json:"dependency_lock_sha256"`
	FileCount            int             `json:"file_count"`
	Files                []buildFileFact `json:"files"`
	LogTail              string          `json:"log_tail"`
}

type buildFileFact struct {
	Path      string `json:"path"`
	SizeBytes int64  `json:"size_bytes"`
	SHA256    string `json:"sha256"`
}

// ToolBuild is the compile-time tool_build handler.
func ToolBuild(ctx context.Context, work background.HandlerWork) background.Outcome {
	if work.Scope == nil || work.Job == nil || work.Sandbox == nil {
		return background.Failed(background.CodeTransientInternal)
	}
	job := work.Job
	if job.Kind != background.KindToolBuild ||
		job.InputArtifactID == nil || strings.TrimSpace(*job.InputArtifactID) == "" {
		return background.Failed(background.CodeBuildSourceInvalid)
	}
	binding, err := work.Scope.ToolBuild(ctx, job.ID, work.AttemptToken)
	if err != nil {
		return toolBuildScopeOutcome(err)
	}
	if binding.SourceArtifactID != *job.InputArtifactID {
		return background.Failed(background.CodeBuildSourceChanged)
	}
	link, err := work.Scope.Download(ctx, job.ID, work.AttemptToken, binding.SourceArtifactID)
	if err != nil {
		return toolBuildScopeOutcome(err)
	}
	source, err := work.Scope.FetchURL(ctx, link, maxBuildSourceBytes)
	if err != nil {
		return background.Failed(background.CodeTransientStorageIO)
	}
	if len(source) == 0 || int64(len(source)) > maxBuildSourceBytes {
		return background.Failed(background.CodeBuildSourceInvalid)
	}
	sourceSHA := sha256HexBytes(source)
	if !strings.EqualFold(sourceSHA, strings.TrimSpace(binding.SourceSHA256)) {
		return background.Failed(background.CodeBuildSourceChanged)
	}
	lockCanonical, err := jcs.CanonicalizeBytes(bytes.TrimSpace(binding.DependencyLock))
	if err != nil || len(lockCanonical) == 0 {
		return background.Failed(background.CodeBuildSourceInvalid)
	}
	lockSHA := sha256HexBytes(lockCanonical)

	outcome, sandboxErr := work.Sandbox.Run(ctx, sandbox.Request{
		Program:         sandbox.ProgramBuildTool,
		Input:           source,
		InputJSON:       lockCanonical,
		ExecutionUnitID: "tool-build-" + job.ID,
		Timeout:         buildTimeout(work.Settings.ToolBuildTimeout),
	}, sandbox.Control{
		HeartbeatInterval: 5 * time.Second,
		Heartbeat: func(heartbeatCtx context.Context) (sandbox.HeartbeatState, error) {
			if work.Heartbeat == nil {
				return sandbox.HeartbeatState{}, nil
			}
			err := work.Heartbeat(heartbeatCtx)
			if err != nil {
				return sandbox.HeartbeatState{}, err
			}
			return sandbox.HeartbeatState{CancelRequested: false}, nil
		},
	})

	document, documentErr := parseBuildDocument(outcome.OutputJSON)
	if sandboxErr != nil {
		return buildSandboxFailure(ctx, work, outcome, sandboxErr, document)
	}
	if outcome.ExitCode != 0 {
		// build_tool.py exits 3 with a machine-readable failed document.
		code := background.CodeBuildFailed
		message := "the sandboxed build failed"
		if documentErr == nil && document != nil && document.Status == "failed" {
			code = normalizeBuildCode(document.ErrorCode)
			if strings.TrimSpace(document.Message) != "" {
				message = document.Message
			}
		}
		if documentErr == nil && document != nil && document.Status == "ok" {
			code = background.CodeBuildOutputInvalid
			message = "the build script reported success with a non-zero exit"
		}
		return buildFailure(ctx, work, outcome, code, message, sourceSHA, lockSHA, document)
	}
	if documentErr != nil || document == nil {
		return buildFailure(ctx, work, outcome, background.CodeBuildOutputInvalid,
			"the build produced no valid build.json", sourceSHA, lockSHA, document)
	}
	if document.BuildVersion != buildDocumentVersion || document.Status != "ok" {
		return buildFailure(ctx, work, outcome, background.CodeBuildOutputInvalid,
			"the build document is not a successful result", sourceSHA, lockSHA, document)
	}
	if !strings.EqualFold(document.SourceSHA256, sourceSHA) ||
		!strings.EqualFold(document.DependencyLockSHA256, lockSHA) {
		return buildFailure(ctx, work, outcome, background.CodeBuildOutputInvalid,
			"the build document does not match the submitted source/lock hashes", sourceSHA, lockSHA, document)
	}

	collected := sandbox.CollectedArtifacts(outcome)
	sbom := collected["sbom.json"]
	logBytes := collected["build_log"]
	if len(sbom) == 0 || int64(len(sbom)) > maxBuildSBOMBytes || len(logBytes) == 0 {
		return buildFailure(ctx, work, outcome, background.CodeBuildOutputInvalid,
			"the build produced no SBOM or log", sourceSHA, lockSHA, document)
	}
	if len(logBytes) > maxBuildLogBytes {
		logBytes = logBytes[:maxBuildLogBytes]
	}

	sbomID, uploadOutcome, ok := uploadBuildArtifact(
		ctx, work, "sbom.json", buildPurposeSBOM, "application/json", sbom,
	)
	if !ok {
		return uploadOutcome
	}
	if _, uploadOutcome, ok := uploadBuildArtifact(
		ctx, work, "build_log", buildPurposeLog, "text/plain", logBytes,
	); !ok {
		return uploadOutcome
	}
	return background.SucceededWith(sbomID)
}

// buildTimeout applies the documented default and the sandbox ceiling.
func buildTimeout(configured time.Duration) time.Duration {
	if configured <= 0 {
		return DefaultBuildTimeout
	}
	if configured > sandbox.MaxTimeout {
		return sandbox.MaxTimeout
	}
	return configured
}

// parseBuildDocument strictly decodes build.json. Unknown fields, trailing data
// or an oversized body are an invalid result, never accepted.
func parseBuildDocument(payload []byte) (*buildDocument, error) {
	if len(payload) == 0 || len(payload) > sandbox.MaxOutputJSONBytes {
		return nil, errors.New("the build document is missing or oversized")
	}
	decoder := json.NewDecoder(bytes.NewReader(payload))
	decoder.DisallowUnknownFields()
	var document buildDocument
	if err := decoder.Decode(&document); err != nil {
		return nil, err
	}
	if err := decoder.Decode(&struct{}{}); !errors.Is(err, io.EOF) {
		return nil, errors.New("the build document has trailing data")
	}
	return &document, nil
}

// normalizeBuildCode closes the build failure vocabulary. An unknown code is
// build_failed: the job error code and the tool row must never store arbitrary
// sandbox text.
func normalizeBuildCode(code string) string {
	switch strings.TrimSpace(code) {
	case background.CodeBuildDependenciesMissing:
		return background.CodeBuildDependenciesMissing
	case background.CodeBuildSourceInvalid:
		return background.CodeBuildSourceInvalid
	case background.CodeBuildOutputInvalid:
		return background.CodeBuildOutputInvalid
	case background.CodeBuildTimeout:
		return background.CodeBuildTimeout
	case background.CodeBuildArtifactInvalid:
		return background.CodeBuildArtifactInvalid
	default:
		return background.CodeBuildFailed
	}
}

// buildSandboxFailure maps an untrusted sandbox outcome to a safe build code.
func buildSandboxFailure(
	ctx context.Context,
	work background.HandlerWork,
	outcome sandbox.Outcome,
	sandboxErr error,
	document *buildDocument,
) background.Outcome {
	code := background.CodeBuildCrashed
	message := "the build sandbox failed"
	switch {
	case errors.Is(sandboxErr, sandbox.ErrTimeout):
		code = background.CodeBuildTimeout
		message = "the build exceeded its sandbox timeout"
	case errors.Is(sandboxErr, sandbox.ErrRootfsUnavailable),
		errors.Is(sandboxErr, sandbox.ErrToolUnavailable),
		errors.Is(sandboxErr, sandbox.ErrRunnerUnavailable),
		errors.Is(sandboxErr, sandbox.ErrDriverReport),
		errors.Is(sandboxErr, sandbox.ErrDigestMismatch),
		errors.Is(sandboxErr, sandbox.ErrOrphan):
		code = background.CodeBuildSandboxUnavailable
		message = "the build sandbox could not be started or trusted"
	}
	return buildFailure(ctx, work, outcome, code, message, "", "", document)
}

// buildFailure uploads the safe log/diagnostics for one failed attempt and
// returns the job failure. Upload errors never replace the real failure code.
func buildFailure(
	ctx context.Context,
	work background.HandlerWork,
	outcome sandbox.Outcome,
	code, message, sourceSHA, lockSHA string,
	document *buildDocument,
) background.Outcome {
	collected := sandbox.CollectedArtifacts(outcome)
	logBytes := collected["build_log"]
	if len(logBytes) == 0 {
		logBytes = buildSandboxLog(outcome)
	}
	if len(logBytes) > 0 {
		if len(logBytes) > maxBuildLogBytes {
			logBytes = logBytes[:maxBuildLogBytes]
		}
		// Wait for the log to be ready: the failure guard links the latest ready
		// build_log artifact to the failed tool row for diagnostics.
		_, _, _ = uploadBuildArtifact(
			ctx, work, "build_log", buildPurposeLog, "text/plain", logBytes,
		)
	}
	publishBuildDiagnostics(ctx, work, code, message, sourceSHA, lockSHA, document)
	return background.Failed(code)
}

// publishBuildDiagnostics uploads the safe diagnostics document. It never
// contains the sandbox log body, an object key, a host path or a credential.
func publishBuildDiagnostics(
	ctx context.Context,
	work background.HandlerWork,
	code, message, sourceSHA, lockSHA string,
	document *buildDocument,
) {
	payload := map[string]any{
		"diagnostics_version": 1,
		"tool_build":          true,
		"status":              "failed",
		"error_code":          code,
		"message":             message,
		"retryable":           background.Retryable(background.KindToolBuild, code),
	}
	if sourceSHA != "" {
		payload["source_sha256"] = sourceSHA
	}
	if lockSHA != "" {
		payload["dependency_lock_sha256"] = lockSHA
	}
	if document != nil {
		payload["build_version"] = document.BuildVersion
		if document.FileCount > 0 {
			payload["file_count"] = document.FileCount
		}
	}
	encoded, err := json.Marshal(payload)
	if err != nil {
		return
	}
	encoded = append(encoded, '\n')
	_, _, _ = uploadBuildArtifactNoWait(
		ctx, work, "diagnostics", buildPurposeDiag, "application/json", encoded,
	)
}

// buildSandboxLog is the capped driver stdout/stderr of a sandbox failure. It is
// a log, never protocol output, and never host content.
func buildSandboxLog(outcome sandbox.Outcome) []byte {
	var builder bytes.Buffer
	builder.WriteString("build sandbox stdout:\n")
	builder.Write(outcome.Stdout)
	builder.WriteString("\nbuild sandbox stderr:\n")
	builder.Write(outcome.Stderr)
	builder.WriteByte('\n')
	content := builder.Bytes()
	if len(content) > maxBuildLogBytes {
		content = content[:maxBuildLogBytes]
	}
	return content
}

// uploadBuildArtifact reserves/puts/completes one artifact and waits for the
// finalize guard, mapping refusals to build codes.
func uploadBuildArtifact(
	ctx context.Context,
	work background.HandlerWork,
	name, purpose, mediaType string,
	content []byte,
) (string, background.Outcome, bool) {
	artifactID, ready, err := uploadBuildArtifactNoWait(ctx, work, name, purpose, mediaType, content)
	if err != nil {
		return "", buildUploadOutcome(err), false
	}
	if ready {
		return artifactID, background.Succeeded(), true
	}
	outcome, ok := waitForToolArtifacts(ctx, work, []string{artifactID})
	if !ok {
		return "", translateBuildWaitOutcome(outcome), false
	}
	return artifactID, background.Succeeded(), true
}

func uploadBuildArtifactNoWait(
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
	return ticket.ArtifactID, view.State == "ready", nil
}

// buildUploadOutcome maps an upload/scope refusal to the build vocabulary.
func buildUploadOutcome(err error) background.Outcome {
	switch {
	case errors.Is(err, errToolByteIO):
		return background.Failed(background.CodeTransientStorageIO)
	case errors.Is(err, tools.ErrArtifactTooLarge):
		return background.Failed(background.CodeBuildArtifactInvalid)
	default:
		return toolBuildScopeOutcome(err)
	}
}

// translateBuildWaitOutcome keeps the wait loop shared with tool_run but reports
// build-specific codes.
func translateBuildWaitOutcome(outcome background.Outcome) background.Outcome {
	if outcome.ErrorCode == background.CodeToolArtifactInvalid {
		return background.Failed(background.CodeBuildArtifactInvalid)
	}
	return outcome
}

// toolBuildScopeOutcome maps a job-scoped API refusal to the build vocabulary.
func toolBuildScopeOutcome(err error) background.Outcome {
	var coded interface{ ErrorCode() string }
	if errors.As(err, &coded) {
		switch coded.ErrorCode() {
		case "stale_attempt":
			return background.Failed(background.CodeStaleAttempt)
		case "artifact_not_found", "artifact_not_ready", "artifact_conflict":
			return background.Failed(background.CodeBuildSourceChanged)
		case "object_store_unavailable", "object_signing_unavailable":
			return background.Failed(background.CodeObjectStoreUnavailable)
		}
	}
	return background.Failed(background.CodeTransientInternal)
}

// sha256HexBytes is the local hash of bytes this handler just read.
func sha256HexBytes(content []byte) string {
	sum := sha256.Sum256(content)
	return hex.EncodeToString(sum[:])
}
