package sandbox

// F05/T033: the tool output protocol, validated by the platform.
//
// The sandbox's output.json is untrusted input: the tool just ran with a mounted
// input, and the platform must not believe what it wrote. The shape is the fixed
// protocol from WORKFLOW-SPEC §5:
//
//	{"data": {...}, "artifacts": ["summary.json", "summary.csv"]}
//
// Every rule is closed: no unknown keys, data is an object, artifact names are
// plain basenames, no duplicates, at most 16 files, each non-empty and at most
// 10 MiB, and the driver's own byte report must match the listed names exactly.

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"regexp"
	"strings"
)

const (
	// MaxArtifacts is the fixed protocol cap (same as T030's executor).
	MaxArtifacts = 16
	// MaxArtifactBytes bounds one produced file.
	MaxArtifactBytes = 10 * 1024 * 1024
	// MaxOutputJSONBytes bounds output.json.
	MaxOutputJSONBytes = 1 * 1024 * 1024
)

// artifactNamePattern is the only shape a produced file name may have.
var artifactNamePattern = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._-]{0,126}$`)

// ErrOutputInvalid reports an output.json that does not follow the protocol.
var ErrOutputInvalid = errors.New("the sandbox output.json is not the fixed protocol shape")

// Artifact is one admitted output file.
type Artifact struct {
	Name    string
	Content []byte
	SHA256  string
}

// ParseOutputDocument strictly decodes output.json and returns the data object
// plus the artifact names.
func ParseOutputDocument(payload []byte) (json.RawMessage, []string, error) {
	if len(payload) == 0 || len(payload) > MaxOutputJSONBytes {
		return nil, nil, fmt.Errorf("%w: size outside the protocol cap", ErrOutputInvalid)
	}
	decoder := json.NewDecoder(bytes.NewReader(payload))
	decoder.DisallowUnknownFields()
	var document struct {
		Data      json.RawMessage `json:"data"`
		Artifacts []string        `json:"artifacts"`
	}
	if err := decoder.Decode(&document); err != nil {
		return nil, nil, fmt.Errorf("%w: %v", ErrOutputInvalid, err)
	}
	if err := decoder.Decode(&struct{}{}); !errors.Is(err, io.EOF) {
		return nil, nil, fmt.Errorf("%w: trailing data", ErrOutputInvalid)
	}
	trimmed := bytes.TrimSpace(document.Data)
	if len(trimmed) == 0 || trimmed[0] != '{' || !json.Valid(trimmed) {
		return nil, nil, fmt.Errorf("%w: data must be a JSON object", ErrOutputInvalid)
	}
	if len(document.Artifacts) > MaxArtifacts {
		return nil, nil, fmt.Errorf("%w: too many artifacts", ErrOutputInvalid)
	}
	seen := make(map[string]bool, len(document.Artifacts))
	for _, name := range document.Artifacts {
		if !artifactNamePattern.MatchString(name) || name == "." || name == ".." ||
			strings.ContainsAny(name, `/\`) {
			return nil, nil, fmt.Errorf("%w: invalid artifact name %q", ErrOutputInvalid, name)
		}
		if seen[name] {
			return nil, nil, fmt.Errorf("%w: duplicate artifact name %q", ErrOutputInvalid, name)
		}
		seen[name] = true
	}
	return json.RawMessage(trimmed), document.Artifacts, nil
}

// AdmitArtifacts matches the listed names against the bytes the driver collected.
// It never accepts a file the protocol did not list, a listed file the driver did
// not collect, an empty file, or an oversized one.
func AdmitArtifacts(names []string, collected map[string][]byte) ([]Artifact, error) {
	if len(names) > MaxArtifacts {
		return nil, fmt.Errorf("%w: too many artifacts", ErrOutputInvalid)
	}
	if len(collected) != len(names) {
		return nil, fmt.Errorf("%w: the driver collected %d files for %d listed artifacts",
			ErrOutputInvalid, len(collected), len(names))
	}
	admitted := make([]Artifact, 0, len(names))
	for _, name := range names {
		content, ok := collected[name]
		if !ok {
			return nil, fmt.Errorf("%w: artifact %q was not collected", ErrOutputInvalid, name)
		}
		if len(content) == 0 || len(content) > MaxArtifactBytes {
			return nil, fmt.Errorf("%w: artifact %q has an invalid size", ErrOutputInvalid, name)
		}
		sum := sha256.Sum256(content)
		admitted = append(admitted, Artifact{
			Name:    name,
			Content: content,
			SHA256:  hex.EncodeToString(sum[:]),
		})
	}
	return admitted, nil
}

// CanonicalEqual reports whether two JSON documents have the same RFC 8785
// canonical bytes. The supervisor uses it so the downloadable summary.json and
// output.json.data must be the same document (the same rule T030 applies).
func CanonicalEqual(left, right []byte) bool {
	leftCanonical, leftErr := canonicalize(left)
	rightCanonical, rightErr := canonicalize(right)
	if leftErr != nil || rightErr != nil {
		return false
	}
	return bytes.Equal(leftCanonical, rightCanonical)
}
