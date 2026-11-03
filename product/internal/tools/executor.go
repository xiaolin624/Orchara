// Package tools runs one trusted, preset platform tool as a real subprocess.
//
// The boundary is fixed by WORKFLOW-SPEC §5:
//
//   - the command is a fixed argv array ([python, script, --input, ...]); no
//     shell, no user-supplied argument, no environment from the caller;
//   - the platform writes <workspace>/input.json, copies the one authorized
//     input read-only into <workspace>/inputs, and creates an empty
//     <workspace>/artifacts;
//   - the tool writes only <workspace>/output.json and files inside
//     <workspace>/artifacts; stdout/stderr are capped logs, never parsed output;
//   - output.json is *strictly* {"data": object, "artifacts": [relative paths]};
//     every artifact is admitted again here (relative, inside the artifacts
//     directory, regular file, no symlink or "..", size/count/SHA-256 caps)
//     before the caller may upload it.
//
// This package executes *preset trusted code*. It is not the T033/T037 sandbox:
// there is no gVisor/pod boundary and no dependency build. The OS-level
// read-only input is the honest best effort available here (a DOS read-only
// attribute on Windows, mode 0444 on POSIX); a tool running as the same user can
// chmod it back. The tool source is fixed server-side, so that is a defense
// against accidents and a compromised tool writer, not against an arbitrary
// untrusted script. The real isolation boundary is T033 (sandbox runsc) and the
// real untrusted build boundary is T037.
package tools

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"time"
)

// The one preset tool this build ships. T033/F04A will bind a tool version to a
// run; until then the kind and the API both mean this fixed program.
const (
	ToolSalesStats = "sales_stats"
	// SalesStatsScript is the product-root-relative path of the tool source. The
	// maintenance process resolves it from the product root; no request text
	// ever contributes to it.
	SalesStatsScript = "worker/platform_worker/nodes/sales_stats.py"
	// ScriptVersion identifies the protocol shape for diagnostics/log headers.
	ScriptVersion = "1"
)

// Caps. They are deliberately constant here (not per-request values): a caller
// cannot raise them, and the tool re-checks the same numbers itself.
const (
	MaxInputBytes      int64 = 20 * 1024 * 1024
	MaxOutputBytes     int64 = 1024 * 1024
	MaxArtifactBytes   int64 = 20 * 1024 * 1024
	MaxArtifacts             = 16
	MaxLogBytes        int64 = 64 * 1024
	DefaultTimeout           = 30 * time.Second
	defaultArtifactDir       = "artifacts"
	defaultInputDir          = "inputs"
)

// Media types the preset tool accepts. The declared media type chooses the
// input file name; the user's original file name never reaches the tool.
const (
	MediaTypeCSV  = "text/csv"
	MediaTypeXLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)

// Options configures one executor instance. PythonPath and ScriptPath are the
// only deployment-dependent inputs; everything else is a cap.
type Options struct {
	PythonPath string
	ScriptPath string
	// WorkspaceRoot is the parent directory for per-run workspaces. Empty means
	// the OS temp directory.
	WorkspaceRoot string
	Timeout       time.Duration
	MaxInputBytes int64
	MaxArtifact   int64
	MaxArtifacts  int
	MaxLogBytes   int64
	MaxOutput     int64
}

func (o Options) normalized() Options {
	if strings.TrimSpace(o.PythonPath) == "" {
		if runtime.GOOS == "windows" {
			o.PythonPath = "python"
		} else {
			o.PythonPath = "python3"
		}
	}
	if o.Timeout <= 0 {
		o.Timeout = DefaultTimeout
	}
	if o.MaxInputBytes <= 0 || o.MaxInputBytes > MaxInputBytes {
		o.MaxInputBytes = MaxInputBytes
	}
	if o.MaxArtifact <= 0 || o.MaxArtifact > MaxArtifactBytes {
		o.MaxArtifact = MaxArtifactBytes
	}
	if o.MaxArtifacts <= 0 || o.MaxArtifacts > MaxArtifacts {
		o.MaxArtifacts = MaxArtifacts
	}
	if o.MaxLogBytes <= 0 || o.MaxLogBytes > MaxLogBytes {
		o.MaxLogBytes = MaxLogBytes
	}
	if o.MaxOutput <= 0 || o.MaxOutput > MaxOutputBytes {
		o.MaxOutput = MaxOutputBytes
	}
	o.ScriptPath = strings.TrimSpace(o.ScriptPath)
	return o
}

// Executor runs the fixed tool program. It is safe for concurrent use when
// every Run gets its own workspace (it does).
type Executor struct {
	options Options
}

// New builds an executor. A zero Options uses the platform defaults and the
// runtime's default python executable.
func New(options Options) *Executor {
	return &Executor{options: options.normalized()}
}

// Request is one tool invocation. Input is the already-downloaded artifact
// bytes; ArtifactID/SHA256/SizeBytes are the platform's own facts and are only
// written into input.json for traceability (the tool never trusts them).
type Request struct {
	Tool       string
	ArtifactID string
	MediaType  string
	Input      []byte
	SHA256     string
	SizeBytes  int64
}

// Artifact is one admitted output file, read into memory only after the path,
// type and size checks passed.
type Artifact struct {
	Name      string
	MediaType string
	Content   []byte
	Size      int64
	SHA256    string
}

// Result is a successful protocol run. Data is the raw JSON object from
// output.json (validated as an object here; the tool-specific schema is the
// handler's job). Log is already sanitised and capped.
type Result struct {
	Data      json.RawMessage
	Artifacts []Artifact
	Log       []byte
	ExitCode  int
	Duration  time.Duration
}

// Rejection is one structured, safe user-input refusal written by the tool.
// The code vocabulary is fixed (see rejectionCodes); an unknown code is treated
// as an invalid output document, never passed through.
type Rejection struct {
	Code    string
	Message string
	Fix     string
	Row     *int
	Column  string
	Cell    string
}

// RejectionError reports a tool rejection (exit code 3 with a valid document).
type RejectionError struct {
	Rejection Rejection
}

func (e *RejectionError) Error() string {
	if e == nil {
		return "tool rejected the input"
	}
	return "tool rejected the input: " + e.Rejection.Code
}

// Errors the handler maps onto background-job failure codes.
var (
	ErrTimeout          = errors.New("the tool exceeded its per-run timeout")
	ErrCrashed          = errors.New("the tool exited without a valid protocol result")
	ErrPythonMissing    = errors.New("the configured python executable is unavailable")
	ErrScriptMissing    = errors.New("the preset tool script is unavailable")
	ErrOutputMissing    = errors.New("the tool wrote no output.json")
	ErrOutputInvalid    = errors.New("the tool output.json is not the fixed protocol shape")
	ErrArtifactInvalid  = errors.New("the tool produced an invalid artifact")
	ErrArtifactEscape   = errors.New("the tool produced an artifact path outside the artifacts directory")
	ErrArtifactSymlink  = errors.New("the tool produced a symlink artifact")
	ErrArtifactNotFile  = errors.New("the tool produced a non-regular artifact")
	ErrArtifactTooLarge = errors.New("the tool produced an oversized artifact")
	ErrTooManyArtifacts = errors.New("the tool produced too many artifacts")
)

// Run executes one tool invocation.
func (e *Executor) Run(ctx context.Context, request Request) (Result, error) {
	if e == nil {
		return Result{}, ErrScriptMissing
	}
	options := e.options
	if request.Tool != ToolSalesStats {
		return Result{}, fmt.Errorf("%w: unsupported tool", ErrScriptMissing)
	}
	fileName, ok := inputFileName(request.MediaType)
	if !ok {
		return Result{}, fmt.Errorf("%w: unsupported media type", ErrOutputInvalid)
	}
	if int64(len(request.Input)) == 0 || int64(len(request.Input)) > options.MaxInputBytes {
		return Result{}, fmt.Errorf("%w: input size outside the protocol cap", ErrArtifactInvalid)
	}
	if info, err := os.Stat(options.ScriptPath); err != nil || !info.Mode().IsRegular() {
		return Result{}, ErrScriptMissing
	}
	python, err := exec.LookPath(options.PythonPath)
	if err != nil {
		return Result{}, ErrPythonMissing
	}

	workspace, err := os.MkdirTemp(options.WorkspaceRoot, "orchara-tool-")
	if err != nil {
		return Result{}, fmt.Errorf("creating the tool workspace: %w", err)
	}
	defer cleanupWorkspace(workspace)
	if err := os.Chmod(workspace, 0o700); err != nil {
		return Result{}, fmt.Errorf("restricting the tool workspace: %w", err)
	}
	inputsDir := filepath.Join(workspace, defaultInputDir)
	artifactsDir := filepath.Join(workspace, defaultArtifactDir)
	if err := os.MkdirAll(inputsDir, 0o700); err != nil {
		return Result{}, fmt.Errorf("creating the inputs directory: %w", err)
	}
	if err := os.MkdirAll(artifactsDir, 0o700); err != nil {
		return Result{}, fmt.Errorf("creating the artifacts directory: %w", err)
	}

	inputPath := filepath.Join(inputsDir, fileName)
	if err := writeReadOnlyInput(inputPath, request.Input); err != nil {
		return Result{}, fmt.Errorf("writing the read-only input: %w", err)
	}
	inputJSONPath := filepath.Join(workspace, "input.json")
	outputJSONPath := filepath.Join(workspace, "output.json")
	if err := writeInputJSON(inputJSONPath, request, fileName); err != nil {
		return Result{}, fmt.Errorf("writing the tool input document: %w", err)
	}

	runCtx, cancel := context.WithTimeout(ctx, options.Timeout)
	defer cancel()

	stdout := newCappedBuffer(options.MaxLogBytes)
	stderr := newCappedBuffer(options.MaxLogBytes)
	command := exec.CommandContext(
		runCtx, python, options.ScriptPath,
		"--input", inputJSONPath,
		"--output", outputJSONPath,
		"--inputs-dir", inputsDir,
		"--artifacts-dir", artifactsDir,
	)
	command.Dir = workspace
	command.Env = toolEnvironment()
	command.Stdin = nil
	command.Stdout = stdout
	command.Stderr = stderr

	started := time.Now()
	runErr := command.Run()
	duration := time.Since(started)
	exitCode := 0
	if runErr != nil {
		var exitError *exec.ExitError
		if errors.As(runErr, &exitError) {
			exitCode = exitError.ExitCode()
		} else {
			exitCode = -1
		}
	}
	timedOut := errors.Is(runCtx.Err(), context.DeadlineExceeded)
	logBytes := buildLog(options.ScriptPath, workspace, exitCode, timedOut, duration, stdout, stderr, options.MaxLogBytes)

	result := Result{Log: logBytes, ExitCode: exitCode, Duration: duration}
	switch {
	case ctx.Err() != nil:
		return result, ctx.Err()
	case timedOut:
		return result, ErrTimeout
	case exitCode == 3:
		rejection, err := parseRejection(outputJSONPath, options.MaxOutput)
		if err != nil {
			return result, err
		}
		return result, &RejectionError{Rejection: rejection}
	case exitCode != 0:
		return result, fmt.Errorf("%w: exit code %d", ErrCrashed, exitCode)
	}

	document, err := parseSuccess(outputJSONPath, options.MaxOutput)
	if err != nil {
		return result, err
	}
	if len(document.Artifacts) > options.MaxArtifacts {
		return result, ErrTooManyArtifacts
	}
	artifacts, err := collectArtifacts(artifactsDir, document.Artifacts, options.MaxArtifact)
	if err != nil {
		return result, err
	}
	result.Data = document.Data
	result.Artifacts = artifacts
	return result, nil
}

type successDocument struct {
	Data      json.RawMessage `json:"data"`
	Artifacts []string        `json:"artifacts"`
}

func inputFileName(mediaType string) (string, bool) {
	switch strings.ToLower(strings.TrimSpace(mediaType)) {
	case MediaTypeCSV:
		return "input.csv", true
	case MediaTypeXLSX:
		return "input.xlsx", true
	default:
		return "", false
	}
}

// writeReadOnlyInput copies the artifact bytes and makes the copy read-only.
// On Windows os.Chmod only toggles the DOS read-only attribute; that and the
// fixed trusted tool source are the honest boundary (documented in the package
// comment).
func writeReadOnlyInput(path string, payload []byte) error {
	if err := os.WriteFile(path, payload, 0o600); err != nil {
		return err
	}
	if err := os.Chmod(path, 0o444); err != nil {
		return err
	}
	info, err := os.Lstat(path)
	if err != nil {
		return err
	}
	if !info.Mode().IsRegular() {
		return errors.New("the copied input is not a regular file")
	}
	return nil
}

func writeInputJSON(path string, request Request, fileName string) error {
	document := map[string]any{
		"tool": ToolSalesStats,
		"file": fileName,
		"artifact": map[string]any{
			"artifact_id": request.ArtifactID,
			"media_type":  request.MediaType,
			"size_bytes":  request.SizeBytes,
			"sha256":      request.SHA256,
		},
	}
	payload, err := json.Marshal(document)
	if err != nil {
		return err
	}
	return os.WriteFile(path, payload, 0o600)
}

// toolEnvironment is deliberately tiny and does not inherit the maintenance
// process's environment: no ORCHARA_* values, no credentials, no signing key,
// no database DSN.
func toolEnvironment() []string {
	environment := []string{
		"PYTHONHASHSEED=0",
		"PYTHONIOENCODING=utf-8",
		"PYTHONUTF8=1",
		"PYTHONDONTWRITEBYTECODE=1",
	}
	if runtime.GOOS == "windows" {
		// Python itself needs these for basic runtime services; they carry no
		// platform secret.
		for _, key := range []string{"SystemRoot", "TEMP", "TMP"} {
			if value, ok := os.LookupEnv(key); ok {
				environment = append(environment, key+"="+value)
			}
		}
	}
	return environment
}

// cappedBuffer keeps the first limit bytes and discards the rest. It always
// reports a full write so the child process never sees a short-write/EPIPE.
type cappedBuffer struct {
	limit     int64
	buffer    bytes.Buffer
	truncated bool
}

func newCappedBuffer(limit int64) *cappedBuffer {
	return &cappedBuffer{limit: limit}
}

func (c *cappedBuffer) Write(payload []byte) (int, error) {
	remaining := c.limit - int64(c.buffer.Len())
	if remaining > 0 {
		take := int64(len(payload))
		if take > remaining {
			take = remaining
		}
		_, _ = c.buffer.Write(payload[:take])
		if take < int64(len(payload)) {
			c.truncated = true
		}
	} else if len(payload) > 0 {
		c.truncated = true
	}
	return len(payload), nil
}

// buildLog renders the capped stdout/stderr log artifact. It never includes
// the input bytes, the environment or a raw host workspace path.
func buildLog(
	scriptPath, workspace string,
	exitCode int,
	timedOut bool,
	duration time.Duration,
	stdout, stderr *cappedBuffer,
	limit int64,
) []byte {
	sanitize := func(value string) string {
		value = strings.ReplaceAll(value, workspace, "<workspace>")
		value = strings.ReplaceAll(value, filepath.ToSlash(workspace), "<workspace>")
		if scriptPath != "" {
			value = strings.ReplaceAll(value, scriptPath, "<tool>")
			value = strings.ReplaceAll(value, filepath.ToSlash(scriptPath), "<tool>")
		}
		return value
	}
	header := fmt.Sprintf(
		"tool=%s version=%s\nexit_code=%d timed_out=%t duration_ms=%d\nstdout_truncated=%t stderr_truncated=%t\n",
		ToolSalesStats, ScriptVersion, exitCode, timedOut, duration.Milliseconds(),
		stdout.truncated, stderr.truncated,
	)
	body := header +
		"--- stdout ---\n" + sanitize(stdout.buffer.String()) +
		"\n--- stderr ---\n" + sanitize(stderr.buffer.String()) + "\n"
	if int64(len(body)) > limit {
		body = body[:limit]
	}
	// A cap can split a multi-byte rune; the log artifact must stay valid UTF-8.
	return []byte(strings.ToValidUTF8(body, "\uFFFD"))
}

// parseSuccess strictly decodes the fixed success document. Unknown top-level
// keys, a non-object data value, missing fields and trailing bytes are refused.
func parseSuccess(path string, limit int64) (successDocument, error) {
	payload, err := readLimited(path, limit)
	if errors.Is(err, os.ErrNotExist) {
		return successDocument{}, ErrOutputMissing
	}
	if err != nil {
		return successDocument{}, ErrOutputInvalid
	}
	var document successDocument
	if err := strictDecode(payload, &document); err != nil {
		return successDocument{}, ErrOutputInvalid
	}
	if document.Data == nil || len(bytes.TrimSpace(document.Data)) == 0 {
		return successDocument{}, ErrOutputInvalid
	}
	trimmed := bytes.TrimSpace(document.Data)
	if trimmed[0] != '{' {
		return successDocument{}, ErrOutputInvalid
	}
	if !json.Valid(trimmed) {
		return successDocument{}, ErrOutputInvalid
	}
	if document.Artifacts == nil {
		return successDocument{}, ErrOutputInvalid
	}
	return document, nil
}

type rejectionPayload struct {
	Code     string `json:"code"`
	Message  string `json:"message"`
	Fix      string `json:"fix"`
	Location *struct {
		Row    *int   `json:"row"`
		Column string `json:"column"`
		Cell   string `json:"cell"`
	} `json:"location"`
}

type rejectionDocument struct {
	Error *rejectionPayload `json:"error"`
}

func parseRejection(path string, limit int64) (Rejection, error) {
	payload, err := readLimited(path, limit)
	if errors.Is(err, os.ErrNotExist) {
		return Rejection{}, ErrOutputMissing
	}
	if err != nil {
		return Rejection{}, ErrOutputInvalid
	}
	return ParseRejectionPayload(payload)
}

// ParseRejectionPayload validates an in-memory rejection document (exit code 3).
// It is the same closed-vocabulary check the subprocess executor applies, exposed
// so the F05/T033 sandbox supervisor (which collects output.json over the
// sandbox boundary instead of reading a local path) cannot drift.
func ParseRejectionPayload(payload []byte) (Rejection, error) {
	var document rejectionDocument
	if err := strictDecode(payload, &document); err != nil || document.Error == nil {
		return Rejection{}, ErrOutputInvalid
	}
	code := strings.TrimSpace(document.Error.Code)
	if !rejectionCodes[code] {
		return Rejection{}, ErrOutputInvalid
	}
	message := strings.TrimSpace(document.Error.Message)
	if message == "" || len(message) > 500 || strings.ContainsAny(message, "\r\n\x00") {
		return Rejection{}, ErrOutputInvalid
	}
	fix := strings.TrimSpace(document.Error.Fix)
	if fix == "" || len(fix) > 500 || strings.ContainsAny(fix, "\r\n\x00") {
		return Rejection{}, ErrOutputInvalid
	}
	rejection := Rejection{Code: code, Message: message, Fix: fix}
	if location := document.Error.Location; location != nil {
		rejection.Row = location.Row
		rejection.Column = strings.TrimSpace(location.Column)
		rejection.Cell = strings.TrimSpace(location.Cell)
		if len(rejection.Column) > 100 || len(rejection.Cell) > 20 ||
			strings.ContainsAny(rejection.Column+rejection.Cell, "\r\n\x00") {
			return Rejection{}, ErrOutputInvalid
		}
		if rejection.Row != nil && (*rejection.Row < 1 || *rejection.Row > 1_000_000) {
			return Rejection{}, ErrOutputInvalid
		}
	}
	return rejection, nil
}

func strictDecode(payload []byte, target any) error {
	decoder := json.NewDecoder(bytes.NewReader(payload))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(target); err != nil {
		return err
	}
	if err := decoder.Decode(&struct{}{}); !errors.Is(err, io.EOF) {
		return errors.New("trailing data")
	}
	return nil
}

func readLimited(path string, limit int64) ([]byte, error) {
	info, err := os.Stat(path)
	if err != nil {
		return nil, err
	}
	if !info.Mode().IsRegular() {
		return nil, errors.New("not a regular file")
	}
	if info.Size() > limit {
		return nil, errors.New("too large")
	}
	return os.ReadFile(path)
}

// collectArtifacts admits every artifact the tool listed. Each path must be a
// clean relative path inside the artifacts directory, a regular (not symlinked)
// file, non-empty and within the size cap. The returned bytes are hash-verified
// by the same read.
func collectArtifacts(artifactsDir string, names []string, maxBytes int64) ([]Artifact, error) {
	seen := make(map[string]bool, len(names))
	artifacts := make([]Artifact, 0, len(names))
	for _, rawName := range names {
		name, err := normalizeRelativeArtifact(rawName)
		if err != nil {
			return nil, err
		}
		if seen[name] {
			return nil, fmt.Errorf("%w: duplicate artifact %q", ErrArtifactInvalid, name)
		}
		seen[name] = true

		root, err := filepath.Abs(artifactsDir)
		if err != nil {
			return nil, fmt.Errorf("resolving the artifacts directory: %w", err)
		}
		target := filepath.Join(root, filepath.FromSlash(name))
		relative, err := filepath.Rel(root, target)
		if err != nil || relative == ".." || strings.HasPrefix(relative, ".."+string(filepath.Separator)) {
			return nil, ErrArtifactEscape
		}
		info, err := os.Lstat(target)
		if err != nil {
			return nil, fmt.Errorf("%w: %q is missing", ErrArtifactInvalid, name)
		}
		if info.Mode()&os.ModeSymlink != 0 {
			return nil, ErrArtifactSymlink
		}
		if !info.Mode().IsRegular() {
			return nil, ErrArtifactNotFile
		}
		if info.Size() == 0 {
			return nil, fmt.Errorf("%w: %q is empty", ErrArtifactInvalid, name)
		}
		if info.Size() > maxBytes {
			return nil, ErrArtifactTooLarge
		}
		payload, err := readLimited(target, maxBytes)
		if err != nil {
			return nil, fmt.Errorf("%w: %q could not be read", ErrArtifactInvalid, name)
		}
		sum := sha256.Sum256(payload)
		artifacts = append(artifacts, Artifact{
			Name:      name,
			MediaType: artifactMediaType(name),
			Content:   payload,
			Size:      int64(len(payload)),
			SHA256:    hex.EncodeToString(sum[:]),
		})
	}
	return artifacts, nil
}

// normalizeRelativeArtifact accepts both '/' and '\' separators (the tool may
// run on either OS) but refuses absolute paths, drive letters, empty/"."/".."
// segments and NUL. A path that has to be cleaned is refused: "a/../b" is a
// traversal attempt, not a path to normalize.
func normalizeRelativeArtifact(rawName string) (string, error) {
	name := strings.TrimSpace(rawName)
	if name == "" || strings.ContainsRune(name, '\x00') {
		return "", ErrArtifactInvalid
	}
	name = strings.ReplaceAll(name, "\\", "/")
	if strings.HasPrefix(name, "/") || filepath.IsAbs(name) || filepath.VolumeName(name) != "" {
		return "", ErrArtifactEscape
	}
	for _, segment := range strings.Split(name, "/") {
		if segment == "" || segment == "." || segment == ".." {
			return "", ErrArtifactEscape
		}
	}
	return name, nil
}

func artifactMediaType(name string) string {
	switch strings.ToLower(filepath.Ext(name)) {
	case ".json":
		return "application/json"
	case ".csv":
		return "text/csv"
	default:
		return "application/octet-stream"
	}
}

// cleanupWorkspace removes the per-run directory even though the copied input
// is read-only: Windows cannot delete a read-only file, so every entry is made
// writable first. Cleanup failures are ignored by design (the OS temp cleaner
// is the fallback); run artifacts have already been copied into memory.
func cleanupWorkspace(root string) {
	_ = filepath.WalkDir(root, func(path string, entry os.DirEntry, err error) error {
		if err != nil {
			return nil
		}
		_ = os.Chmod(path, 0o700)
		return nil
	})
	_ = os.RemoveAll(root)
}

// rejectionCodes is the fixed tool rejection vocabulary. It mirrors
// sales_stats.py's safe codes; an unknown code means the output document is not
// the protocol and is refused as tool_output_invalid.
var rejectionCodes = map[string]bool{
	"missing_column":           true,
	"extra_column":             true,
	"column_order_mismatch":    true,
	"duplicate_order_id":       true,
	"negative_amount":          true,
	"invalid_amount":           true,
	"empty_region":             true,
	"invalid_date":             true,
	"formula_not_supported":    true,
	"invalid_row":              true,
	"empty_input":              true,
	"invalid_encoding":         true,
	"unsupported_media_type":   true,
	"input_file_missing":       true,
	"input_path_invalid":       true,
	"input_too_large":          true,
	"invalid_input":            true,
	"invalid_csv":              true,
	"field_too_large":          true,
	"empty_order_id":           true,
	"invalid_xlsx":             true,
	"macro_present":            true,
	"dtd_not_supported":        true,
	"archive_limit_exceeded":   true,
	"archive_path_traversal":   true,
	"worksheet_limit_exceeded": true,
	"row_limit_exceeded":       true,
	"cell_limit_exceeded":      true,
	"invalid_cell_type":        true,
	"artifact_path_invalid":    true,
}
