package tools

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"
)

func pythonForTest(t *testing.T) string {
	t.Helper()
	candidate := "python3"
	if runtime.GOOS == "windows" {
		candidate = "python"
	}
	path, err := exec.LookPath(candidate)
	if err != nil {
		t.Skipf("%s is not available; the real subprocess executor cannot be tested", candidate)
	}
	return path
}

// fakeTool writes a real Python script and returns an executor that runs it
// with the real interpreter, the real workspace and the fixed argv protocol.
func fakeTool(t *testing.T, script string, options Options) *Executor {
	t.Helper()
	directory := t.TempDir()
	scriptPath := filepath.Join(directory, "tool.py")
	if err := os.WriteFile(scriptPath, []byte(script), 0o600); err != nil {
		t.Fatalf("writing the fake tool: %v", err)
	}
	options.PythonPath = pythonForTest(t)
	options.ScriptPath = scriptPath
	return New(options)
}

func sampleRequest() Request {
	input := []byte("order_id,date,region,amount\nA1,2026-01-01,x,1.00\n")
	sum := sha256.Sum256(input)
	return Request{
		Tool:       ToolSalesStats,
		ArtifactID: "11111111-1111-4111-8111-111111111111",
		MediaType:  MediaTypeCSV,
		Input:      input,
		SHA256:     hex.EncodeToString(sum[:]),
		SizeBytes:  int64(len(input)),
	}
}

const successScript = `import json, os, sys
args = dict(zip(sys.argv[1::2], sys.argv[2::2]))
artifacts = args["--artifacts-dir"]
with open(os.path.join(artifacts, "summary.json"), "wb") as handle:
    handle.write(b'{"total": "1.00"}')
with open(os.path.join(artifacts, "summary.csv"), "wb") as handle:
    handle.write(b"region,total\nx,1.00\n")
with open(os.path.join(artifacts, "unlisted.txt"), "wb") as handle:
    handle.write(b"a tool may write this, but it is not listed in output.json")
with open(args["--output"], "w", encoding="utf-8") as handle:
    json.dump({"data": {"total": "1.00", "regions": {"x": "1.00"}, "order_count": 1},
               "artifacts": ["summary.json", "summary.csv"]}, handle, ensure_ascii=False)
print("stdout noise that must never become the result")
print("stderr noise", file=sys.stderr)
`

func TestRunSucceedsThroughTheFixedArgvProtocol(t *testing.T) {
	executor := fakeTool(t, successScript, Options{})
	result, err := executor.Run(context.Background(), sampleRequest())
	if err != nil {
		t.Fatalf("Run: %v", err)
	}
	if len(result.Artifacts) != 2 {
		t.Fatalf("artifacts = %d, want 2", len(result.Artifacts))
	}
	if result.Artifacts[0].Name != "summary.json" || result.Artifacts[1].Name != "summary.csv" {
		t.Fatalf("artifact names = %v", []string{result.Artifacts[0].Name, result.Artifacts[1].Name})
	}
	if result.Artifacts[0].MediaType != "application/json" {
		t.Fatalf("summary.json media type = %q", result.Artifacts[0].MediaType)
	}
	if result.Artifacts[1].MediaType != "text/csv" {
		t.Fatalf("summary.csv media type = %q", result.Artifacts[1].MediaType)
	}
	// An unlisted file in the artifacts directory is never registered: only the
	// paths output.json declares are admitted.
	for _, artifact := range result.Artifacts {
		if artifact.Name == "unlisted.txt" {
			t.Fatal("an artifact that output.json did not list was registered")
		}
	}
	if !json.Valid(result.Data) || !strings.HasPrefix(string(result.Data), "{") {
		t.Fatalf("data is not a JSON object: %s", result.Data)
	}
	// stdout/stderr are log material only: they never replace output.json.
	if strings.Contains(string(result.Data), "stdout noise") {
		t.Fatal("stdout was treated as output")
	}
	log := string(result.Log)
	if !strings.Contains(log, "stdout noise") || !strings.Contains(log, "stderr noise") {
		t.Fatalf("the capped log should carry stdout/stderr: %s", log)
	}
	if !strings.Contains(log, "tool=sales_stats") {
		t.Fatalf("log header is missing: %s", log)
	}
}

const probeScript = `import json, os, sys
args = dict(zip(sys.argv[1::2], sys.argv[2::2]))
with open(args["--input"], encoding="utf-8") as handle:
    document = json.load(handle)
input_path = os.path.join(args["--inputs-dir"], document["file"])
writable = False
try:
    with open(input_path, "ab") as handle:
        handle.write(b"X")
    writable = True
except OSError:
    writable = False
started = os.path.exists(os.path.join(args["--inputs-dir"], document["file"]))
environment = sorted(os.environ)
with open(os.path.join(args["--artifacts-dir"], "probe.json"), "w", encoding="utf-8") as handle:
    json.dump({"input_writable": writable, "input_exists": started,
               "has_orchara": any(key.startswith("ORCHARA_") for key in environment),
               "artifact_id": document["artifact"]["artifact_id"],
               "sha256": document["artifact"]["sha256"],
               "size_bytes": document["artifact"]["size_bytes"]}, handle)
with open(args["--output"], "w", encoding="utf-8") as handle:
    json.dump({"data": {"ok": True}, "artifacts": ["probe.json"]}, handle)
`

func TestTheInputIsReadOnlyAndTheEnvironmentIsStripped(t *testing.T) {
	t.Setenv("ORCHARA_TEST_SECRET", "must-not-reach-the-tool")
	t.Setenv("ORCHARA_OBJECT_SIGNING_KEY", "must-not-reach-the-tool")
	executor := fakeTool(t, probeScript, Options{})
	request := sampleRequest()
	result, err := executor.Run(context.Background(), request)
	if err != nil {
		t.Fatalf("Run: %v", err)
	}
	if len(result.Artifacts) != 1 {
		t.Fatalf("artifacts = %d, want 1", len(result.Artifacts))
	}
	var probe struct {
		InputWritable bool   `json:"input_writable"`
		InputExists   bool   `json:"input_exists"`
		HasOrchara    bool   `json:"has_orchara"`
		ArtifactID    string `json:"artifact_id"`
		SHA256        string `json:"sha256"`
		SizeBytes     int64  `json:"size_bytes"`
	}
	if err := json.Unmarshal(result.Artifacts[0].Content, &probe); err != nil {
		t.Fatalf("decoding the probe artifact: %v", err)
	}
	if probe.InputWritable {
		t.Fatal("the read-only input copy was writable by the tool")
	}
	if !probe.InputExists {
		t.Fatal("the tool could not see the copied input")
	}
	if probe.HasOrchara {
		t.Fatal("the tool inherited an ORCHARA_* environment variable")
	}
	if probe.ArtifactID != request.ArtifactID || probe.SHA256 != request.SHA256 ||
		probe.SizeBytes != request.SizeBytes {
		t.Fatalf("input.json facts = %+v", probe)
	}
}

const rejectionScript = `import json, sys
args = dict(zip(sys.argv[1::2], sys.argv[2::2]))
with open(args["--output"], "w", encoding="utf-8") as handle:
    json.dump({"error": {"code": "missing_column", "message": "缺少必需列: date",
                         "fix": "请补齐四列", "location": {"row": 1, "column": "date"}}},
              handle, ensure_ascii=False)
print("sales_stats: rejected missing_column", file=sys.stderr)
sys.exit(3)
`

func TestAStructuredRejectionIsReturnedWithItsLocation(t *testing.T) {
	executor := fakeTool(t, rejectionScript, Options{})
	result, err := executor.Run(context.Background(), sampleRequest())
	var rejection *RejectionError
	if !errors.As(err, &rejection) {
		t.Fatalf("expected a RejectionError, got %v", err)
	}
	if rejection.Rejection.Code != "missing_column" || rejection.Rejection.Message == "" ||
		rejection.Rejection.Fix == "" {
		t.Fatalf("rejection = %+v", rejection.Rejection)
	}
	if rejection.Rejection.Row == nil || *rejection.Rejection.Row != 1 ||
		rejection.Rejection.Column != "date" {
		t.Fatalf("rejection location = %+v", rejection.Rejection)
	}
	if len(result.Log) == 0 {
		t.Fatal("a rejection must still carry the capped log")
	}
}

const crashScript = `import sys
print("unexpected failure", file=sys.stderr)
sys.exit(9)
`

func TestACrashIsNotARejectionAndCarriesTheLog(t *testing.T) {
	executor := fakeTool(t, crashScript, Options{})
	result, err := executor.Run(context.Background(), sampleRequest())
	if !errors.Is(err, ErrCrashed) {
		t.Fatalf("expected ErrCrashed, got %v", err)
	}
	if result.ExitCode != 9 {
		t.Fatalf("exit code = %d, want 9", result.ExitCode)
	}
	if !strings.Contains(string(result.Log), "unexpected failure") {
		t.Fatalf("log = %s", result.Log)
	}
	var rejection *RejectionError
	if errors.As(err, &rejection) {
		t.Fatal("a crash must never be parsed as a rejection")
	}
}

const timeoutScript = `import time
time.sleep(10)
`

func TestATimeoutIsBoundedAndKillsTheProcess(t *testing.T) {
	executor := fakeTool(t, timeoutScript, Options{Timeout: 100 * time.Millisecond})
	started := time.Now()
	result, err := executor.Run(context.Background(), sampleRequest())
	if !errors.Is(err, ErrTimeout) {
		t.Fatalf("expected ErrTimeout, got %v", err)
	}
	if elapsed := time.Since(started); elapsed > 5*time.Second {
		t.Fatalf("the timeout did not bound the run: %s", elapsed)
	}
	if !strings.Contains(string(result.Log), "timed_out=true") {
		t.Fatalf("log = %s", result.Log)
	}
}

func TestTheLogIsCappedAndSanitised(t *testing.T) {
	script := `import json, sys
args = dict(zip(sys.argv[1::2], sys.argv[2::2]))
for _ in range(2000):
    print("A" * 200)
with open(args["--output"], "w", encoding="utf-8") as handle:
    json.dump({"data": {"ok": True}, "artifacts": []}, handle)
`
	executor := fakeTool(t, script, Options{MaxLogBytes: 1024})
	result, err := executor.Run(context.Background(), sampleRequest())
	if err != nil {
		t.Fatalf("Run: %v", err)
	}
	if int64(len(result.Log)) > 1024 {
		t.Fatalf("log = %d bytes, cap is 1024", len(result.Log))
	}
	if !strings.Contains(string(result.Log), "stdout_truncated=true") {
		t.Fatalf("log did not report truncation: %s", result.Log)
	}
}

func writeOutputTool(t *testing.T, payload string) *Executor {
	t.Helper()
	script := `import json, sys
args = dict(zip(sys.argv[1::2], sys.argv[2::2]))
with open(args["--output"], "w", encoding="utf-8") as handle:
    handle.write(` + pythonRawString(payload) + `)
`
	return fakeTool(t, script, Options{})
}

func pythonRawString(value string) string {
	encoded, _ := json.Marshal(value)
	return string(encoded)
}

func TestInvalidOutputDocumentsAreRefused(t *testing.T) {
	cases := map[string]string{
		"not json":          `not-json`,
		"array data":        `{"data": [], "artifacts": []}`,
		"missing artifacts": `{"data": {"a": 1}}`,
		"unknown top key":   `{"data": {"a": 1}, "artifacts": [], "extra": true}`,
		"trailing data":     `{"data": {"a": 1}, "artifacts": []} trailing`,
		"string data":       `{"data": "x", "artifacts": []}`,
	}
	for label, payload := range cases {
		t.Run(label, func(t *testing.T) {
			executor := writeOutputTool(t, payload)
			_, err := executor.Run(context.Background(), sampleRequest())
			if !errors.Is(err, ErrOutputInvalid) {
				t.Fatalf("expected ErrOutputInvalid, got %v", err)
			}
		})
	}
}

func TestAnUnknownRejectionCodeIsNotPassedThrough(t *testing.T) {
	payload := `{"error": {"code": "made_up", "message": "x", "fix": "y"}}`
	executor := writeOutputTool(t, payload)
	// exit code 0 with a rejection shape is an invalid success document.
	_, err := executor.Run(context.Background(), sampleRequest())
	if !errors.Is(err, ErrOutputInvalid) {
		t.Fatalf("expected ErrOutputInvalid, got %v", err)
	}
}

func TestUnsupportedMediaTypeAndToolAreRefusedBeforeExecuting(t *testing.T) {
	executor := fakeTool(t, successScript, Options{})
	request := sampleRequest()
	request.MediaType = "application/json"
	if _, err := executor.Run(context.Background(), request); err == nil {
		t.Fatal("an unsupported media type must be refused")
	}
	request = sampleRequest()
	request.Tool = "other"
	if _, err := executor.Run(context.Background(), request); err == nil {
		t.Fatal("an unsupported tool must be refused")
	}
}

func TestAMissingScriptFailsClosed(t *testing.T) {
	executor := New(Options{PythonPath: pythonForTest(t), ScriptPath: filepath.Join(t.TempDir(), "nope.py")})
	if _, err := executor.Run(context.Background(), sampleRequest()); !errors.Is(err, ErrScriptMissing) {
		t.Fatalf("expected ErrScriptMissing, got %v", err)
	}
}

// ---------------------------------------------------------------------------
// artifact admission
// ---------------------------------------------------------------------------
func writeArtifact(t *testing.T, directory, name string, content []byte) {
	t.Helper()
	path := filepath.Join(directory, filepath.FromSlash(name))
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	if err := os.WriteFile(path, content, 0o600); err != nil {
		t.Fatalf("write artifact: %v", err)
	}
}

func TestArtifactAdmissionAcceptsCleanRelativeFiles(t *testing.T) {
	directory := t.TempDir()
	writeArtifact(t, directory, "summary.json", []byte(`{"total":"1.00"}`))
	writeArtifact(t, directory, "nested/summary.csv", []byte("region,total\nx,1.00\n"))
	artifacts, err := collectArtifacts(directory, []string{"summary.json", `nested\summary.csv`}, MaxArtifactBytes)
	if err != nil {
		t.Fatalf("collectArtifacts: %v", err)
	}
	if len(artifacts) != 2 {
		t.Fatalf("artifacts = %d", len(artifacts))
	}
	if artifacts[0].MediaType != "application/json" || artifacts[1].MediaType != "text/csv" {
		t.Fatalf("media types = %q %q", artifacts[0].MediaType, artifacts[1].MediaType)
	}
	sum := sha256.Sum256([]byte(`{"total":"1.00"}`))
	if artifacts[0].SHA256 != hex.EncodeToString(sum[:]) {
		t.Fatalf("sha256 = %q", artifacts[0].SHA256)
	}
}

func TestArtifactAdmissionRefusesTraversalAndAbsolutePaths(t *testing.T) {
	directory := t.TempDir()
	writeArtifact(t, directory, "ok.json", []byte("{}"))
	writeArtifact(t, filepath.Dir(directory), "outside.json", []byte("{}"))
	cases := map[string]error{
		"../outside.json": ErrArtifactEscape,
		"/absolute.json":  ErrArtifactEscape,
		"a/../ok.json":    ErrArtifactEscape,
		"a//b.json":       ErrArtifactEscape,
		"./ok.json":       ErrArtifactEscape,
		`..\outside.json`: ErrArtifactEscape,
		"":                ErrArtifactInvalid,
	}
	for name, expected := range cases {
		_, err := collectArtifacts(directory, []string{name}, MaxArtifactBytes)
		if !errors.Is(err, expected) {
			t.Fatalf("%q: expected %v, got %v", name, expected, err)
		}
	}
	if runtime.GOOS == "windows" {
		if _, err := collectArtifacts(directory, []string{`C:\windows\win.ini`}, MaxArtifactBytes); !errors.Is(err, ErrArtifactEscape) {
			t.Fatalf("a drive-absolute path must be refused, got %v", err)
		}
	}
}

func TestArtifactAdmissionRefusesSymlinksDirectoriesAndMissingFiles(t *testing.T) {
	directory := t.TempDir()
	writeArtifact(t, directory, "target.json", []byte("{}"))
	if err := os.Mkdir(filepath.Join(directory, "folder"), 0o700); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	if _, err := collectArtifacts(directory, []string{"folder"}, MaxArtifactBytes); !errors.Is(err, ErrArtifactNotFile) {
		t.Fatalf("a directory must be refused, got %v", err)
	}
	if _, err := collectArtifacts(directory, []string{"missing.json"}, MaxArtifactBytes); !errors.Is(err, ErrArtifactInvalid) {
		t.Fatalf("a missing artifact must be refused, got %v", err)
	}
	if err := os.Symlink(filepath.Join(directory, "target.json"), filepath.Join(directory, "link.json")); err != nil {
		t.Skipf("symlinks are unavailable on this host: %v", err)
	}
	if _, err := collectArtifacts(directory, []string{"link.json"}, MaxArtifactBytes); !errors.Is(err, ErrArtifactSymlink) {
		t.Fatalf("a symlink must be refused, got %v", err)
	}
}

func TestArtifactAdmissionRefusesEmptyOversizedAndDuplicateArtifacts(t *testing.T) {
	directory := t.TempDir()
	writeArtifact(t, directory, "empty.json", []byte{})
	writeArtifact(t, directory, "big.csv", []byte("12345"))
	if _, err := collectArtifacts(directory, []string{"empty.json"}, MaxArtifactBytes); !errors.Is(err, ErrArtifactInvalid) {
		t.Fatalf("an empty artifact must be refused, got %v", err)
	}
	if _, err := collectArtifacts(directory, []string{"big.csv"}, 4); !errors.Is(err, ErrArtifactTooLarge) {
		t.Fatalf("an oversized artifact must be refused, got %v", err)
	}
	if _, err := collectArtifacts(directory, []string{"big.csv", "big.csv"}, MaxArtifactBytes); !errors.Is(err, ErrArtifactInvalid) {
		t.Fatalf("a duplicate artifact must be refused, got %v", err)
	}
}

func TestParseRejectionRefusesUnknownKeysAndBadLocations(t *testing.T) {
	dir := t.TempDir()
	write := func(payload string) string {
		path := filepath.Join(dir, "output.json")
		if err := os.WriteFile(path, []byte(payload), 0o600); err != nil {
			t.Fatalf("write: %v", err)
		}
		return path
	}
	valid := write(`{"error":{"code":"invalid_date","message":"bad date","fix":"use ISO","location":{"row":2,"column":"date"}}}`)
	rejection, err := parseRejection(valid, MaxOutputBytes)
	if err != nil || rejection.Code != "invalid_date" || rejection.Row == nil || *rejection.Row != 2 {
		t.Fatalf("parseRejection = %+v, %v", rejection, err)
	}
	for label, payload := range map[string]string{
		"unknown top key":   `{"error":{"code":"invalid_date","message":"x","fix":"y"},"extra":1}`,
		"unknown inner key": `{"error":{"code":"invalid_date","message":"x","fix":"y","detail":"z"}}`,
		"unknown code":      `{"error":{"code":"made_up","message":"x","fix":"y"}}`,
		"missing message":   `{"error":{"code":"invalid_date","fix":"y"}}`,
		"bad row":           `{"error":{"code":"invalid_date","message":"x","fix":"y","location":{"row":0}}}`,
	} {
		if _, err := parseRejection(write(payload), MaxOutputBytes); !errors.Is(err, ErrOutputInvalid) {
			t.Fatalf("%s: expected ErrOutputInvalid, got %v", label, err)
		}
	}
}
