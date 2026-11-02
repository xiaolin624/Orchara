// Package compiler composes the frozen execution manifest defined by
// contracts/execution-manifest.schema.json, canonicalises it with RFC 8785 JCS
// (internal/jcs) and re-verifies sealed canonical bytes.
//
// Three rules shape this package:
//
//   - **The hash is external to the manifest.** manifest_sha256 is the SHA-256
//     of the UTF-8 JCS bytes; it is never a member of the object it covers
//     (IMPLEMENTATION-DETAILS §4). A client that re-serialises the JSON must not
//     assume the bytes (or the hash) are the same.
//   - **Deployment inputs fail closed.** The worker image digest comes from
//     ORCHARA_WORKER_IMAGE_DIGEST and the runtime lock digest from the locked
//     runtime file. This batch has not built a worker image (F04A/F15A own
//     that), so the digest is a deployer placeholder that must match
//     ^sha256:[0-9a-f]{64}$; a missing input is an *UnconfiguredError*, which
//     the HTTP layer maps to 503 compiler_unconfigured. The compiler never
//     invents a digest, an Image, or a state schema artifact.
//   - **Identity, layout and review state are not manifest content.** No run,
//     snapshot or version id, no layout, no review/validation state and no hash
//     enter the object: the same draft must keep its execution hash when it
//     moves from validate to debug to publication.
//
// The builder lives here (pure composition: no database, no object store);
// internal/catalog resolves the dependency closure, validates the draft and
// persists the bytes. VerifyManifestBytes is the mechanism the future
// supervisor/worker (F03/F04A) uses to re-read an artifact: hash the bytes first,
// compare the recorded digest, then parse. The real worker is not part of this
// ticket.
package compiler

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"

	"orchara/platform/internal/jcs"
	"orchara/platform/internal/uuid"
)

const (
	// ManifestVersion is the schema's const value.
	ManifestVersion = "1.0"
	// CompilerVersion names the compiler that produced the manifest. It is a
	// fixed constant in this batch: no compiler binary is shipped yet, so the
	// value identifies the composition rules (T025), not a build artifact. A
	// future compiler release may add its own version, but it must be a
	// deliberate, recorded change because it changes every manifest hash.
	CompilerVersion = "orchara-compiler/1.0.0"
	// StateSchemaVersion pins the LangGraph/worker state schema name. The real
	// state schema artifact belongs to F03; publishing the constant now keeps
	// the manifest honest ("this is what the platform expects") without
	// pretending the schema file exists.
	StateSchemaVersion = "orchara-state/1.0"
	// DefaultRuntimeLockPath is the P01 locked runtime (probes/agent_min) relative
	// to the product root. ORCHARA_RUNTIME_LOCK_PATH overrides it; the digest is
	// recomputed from the file so a changed lock changes every manifest hash.
	DefaultRuntimeLockPath = "probes/agent_min/requirements.lock.txt"

	// EnvWorkerImageDigest and EnvRuntimeLockPath are the deployment inputs the
	// compiler reads. They are defined here once; internal/config references
	// these constants so a rename cannot make the error message and the loader
	// disagree.
	EnvWorkerImageDigest = "ORCHARA_WORKER_IMAGE_DIGEST"
	EnvRuntimeLockPath   = "ORCHARA_RUNTIME_LOCK_PATH"
)

var (
	digestPattern = regexp.MustCompile(`^sha256:[0-9a-f]{64}$`)
	lockPattern   = regexp.MustCompile(`^[0-9a-f]{64}$`)
)

// ErrUnconfigured is the sentinel behind *UnconfiguredError: a deployment input
// needed to compile a manifest is absent or unusable. The API maps it to
// 503 compiler_unconfigured; it is never a 422 (the draft is not at fault).
var ErrUnconfigured = errors.New("compiler is not configured")

// ErrInvalidInput is the sentinel behind *InputError: the values handed to the
// builder do not describe a schema-valid manifest. Callers validate drafts
// before building, so this is a programming/validation defect rather than a
// user-facing condition.
var ErrInvalidInput = errors.New("manifest input is invalid")

// UnconfiguredError names the missing deployment input without echoing a
// secret. Variable is the environment variable the deployer must set.
type UnconfiguredError struct {
	Variable string
	Reason   string
}

func (e *UnconfiguredError) Error() string {
	if e.Variable == "" {
		return e.Reason
	}
	return e.Variable + ": " + e.Reason
}

// Unwrap lets callers use errors.Is(err, ErrUnconfigured).
func (e *UnconfiguredError) Unwrap() error { return ErrUnconfigured }

// InputError names the manifest field that could not be built.
type InputError struct {
	Field   string
	Message string
}

func (e *InputError) Error() string { return e.Field + ": " + e.Message }

// Unwrap lets callers use errors.Is(err, ErrInvalidInput).
func (e *InputError) Unwrap() error { return ErrInvalidInput }

func invalid(field, message string) error { return &InputError{Field: field, Message: message} }

// Inputs is the raw deployment configuration for manifest generation, read from
// the environment by internal/config. Resolve turns it into the fixed values the
// builder consumes; an empty worker digest is the documented fail-closed state
// until F04A/F15A build a real image.
type Inputs struct {
	WorkerImageDigest string
	RuntimeLockPath   string
}

// Resolved is a manifest build's deployment identity. It is computed per
// validate request (the runtime lock is hashed from the file, so a changed lock
// is reflected), not cached at process start.
type Resolved struct {
	WorkerImageDigest string
	RuntimeLockSHA256 string
}

// ValidWorkerImageDigest reports whether raw is the contract image digest form.
// It is used by config at startup so a malformed digest refuses to start instead
// of surfacing as a 503 per request.
func ValidWorkerImageDigest(raw string) bool {
	return digestPattern.MatchString(strings.TrimSpace(raw))
}

// Resolve validates the deployment inputs and hashes the runtime lock file.
func (i Inputs) Resolve() (Resolved, error) {
	if strings.TrimSpace(i.WorkerImageDigest) == "" {
		return Resolved{}, &UnconfiguredError{
			Variable: EnvWorkerImageDigest,
			Reason:   "is required to compile an execution manifest; no worker image has been built in this batch",
		}
	}
	if !digestPattern.MatchString(strings.TrimSpace(i.WorkerImageDigest)) {
		return Resolved{}, &UnconfiguredError{
			Variable: EnvWorkerImageDigest,
			Reason:   "must be a sha256:<64 lowercase hex> image digest",
		}
	}
	path := strings.TrimSpace(i.RuntimeLockPath)
	if path == "" {
		path = DefaultRuntimeLockPath
	}
	lockSHA, err := RuntimeLockSHA256(path)
	if err != nil {
		return Resolved{}, &UnconfiguredError{
			Variable: EnvRuntimeLockPath,
			Reason:   "cannot read the locked runtime file: " + err.Error(),
		}
	}
	return Resolved{
		WorkerImageDigest: strings.TrimSpace(i.WorkerImageDigest),
		RuntimeLockSHA256: lockSHA,
	}, nil
}

// RuntimeLockSHA256 hashes one locked runtime file. It returns a plain error
// (wrapped by Resolve into an UnconfiguredError) so tests can assert the raw
// failure without a deployment-input message.
func RuntimeLockSHA256(path string) (string, error) {
	payload, err := os.ReadFile(filepath.Clean(path))
	if err != nil {
		return "", err
	}
	return jcs.SHA256Hex(payload), nil
}

// ToolArtifact is one entry of manifest.tool_artifacts. ID is the tool version
// id (the same id a python/agent config references); ImageDigest is the fixed
// image the tool version row pins.
type ToolArtifact struct {
	ID          string `json:"id"`
	ImageDigest string `json:"image_digest"`
}

// ConnectorPolicy is one entry of manifest.connector_policies. T025 always emits
// an empty array; the type exists so the schema field is explicit and a later
// connector ticket can populate it without reshaping the manifest.
type ConnectorPolicy struct {
	VersionID    string `json:"version_id"`
	PolicySHA256 string `json:"policy_sha256"`
}

// Manifest is exactly contracts/execution-manifest.schema.json: no field outside
// the schema, every schema field present. Definition and CapabilityCard are the
// parsed documents (JSON objects); the canonical bytes are produced by JCS, so
// map ordering never leaks into the hash.
type Manifest struct {
	ManifestVersion      string            `json:"manifest_version"`
	ApplicationID        string            `json:"application_id"`
	SourceRevision       int64             `json:"source_revision"`
	CompilerVersion      string            `json:"compiler_version"`
	WorkerImageDigest    string            `json:"worker_image_digest"`
	RuntimeLockSHA256    string            `json:"runtime_lock_sha256"`
	StateSchemaVersion   string            `json:"state_schema_version"`
	Definition           map[string]any    `json:"definition"`
	CapabilityCard       map[string]any    `json:"capability_card"`
	ToolArtifacts        []ToolArtifact    `json:"tool_artifacts"`
	ModelProfileVersions []string          `json:"model_profile_versions"`
	SubappVersions       []string          `json:"subapp_versions"`
	ConnectorPolicies    []ConnectorPolicy `json:"connector_policies"`
}

// Built is one composed manifest plus its canonical identity.
type Built struct {
	Manifest *Manifest
	// Bytes are the exact UTF-8 RFC 8785 JCS bytes; this is what gets sealed as
	// an artifact and what a worker must hash before parsing.
	Bytes  []byte
	SHA256 string
}

// Build composes and canonicalises the manifest.
//
// The dependency closure is resolved by the caller (internal/catalog) because
// it needs the database and the ACL; this function only normalises the fixed
// sets (sorted, deduplicated) and refuses anything the schema would reject.
func Build(
	inputs Resolved,
	applicationID string,
	sourceRevision int64,
	definition, capabilityCard map[string]any,
	tools []ToolArtifact,
	modelProfileVersions, subappVersions []string,
) (*Built, error) {
	if !digestPattern.MatchString(inputs.WorkerImageDigest) {
		return nil, invalid("worker_image_digest", "must be sha256:<64 lowercase hex>")
	}
	if !lockPattern.MatchString(inputs.RuntimeLockSHA256) {
		return nil, invalid("runtime_lock_sha256", "must be 64 lowercase hex characters")
	}
	if !uuid.Valid(applicationID) {
		return nil, invalid("application_id", "must be a UUID")
	}
	if sourceRevision < 1 {
		return nil, invalid("source_revision", "must be at least 1")
	}
	if definition == nil {
		return nil, invalid("definition", "must be a JSON object")
	}
	if problems := ValidateCapabilityCard(capabilityCard); len(problems) > 0 {
		return nil, invalid("capability_card", problems[0].Message)
	}
	normalTools, err := normaliseTools(tools)
	if err != nil {
		return nil, err
	}
	normalModels, err := normaliseUUIDSet("model_profile_versions", modelProfileVersions)
	if err != nil {
		return nil, err
	}
	normalSubapps, err := normaliseUUIDSet("subapp_versions", subappVersions)
	if err != nil {
		return nil, err
	}

	manifest := &Manifest{
		ManifestVersion:      ManifestVersion,
		ApplicationID:        applicationID,
		SourceRevision:       sourceRevision,
		CompilerVersion:      CompilerVersion,
		WorkerImageDigest:    inputs.WorkerImageDigest,
		RuntimeLockSHA256:    inputs.RuntimeLockSHA256,
		StateSchemaVersion:   StateSchemaVersion,
		Definition:           definition,
		CapabilityCard:       capabilityCard,
		ToolArtifacts:        normalTools,
		ModelProfileVersions: normalModels,
		SubappVersions:       normalSubapps,
		ConnectorPolicies:    []ConnectorPolicy{},
	}
	encoded, err := json.Marshal(manifest)
	if err != nil {
		return nil, fmt.Errorf("encoding the execution manifest: %w", err)
	}
	canonical, err := jcs.CanonicalizeBytes(encoded)
	if err != nil {
		return nil, fmt.Errorf("canonicalising the execution manifest: %w", err)
	}
	// Defence in depth: the builder must never emit something the verifier
	// rejects. decodeManifest also re-checks the schema semantics.
	if _, err := decodeManifest(canonical); err != nil {
		return nil, fmt.Errorf("the built manifest does not satisfy its schema: %w", err)
	}
	return &Built{Manifest: manifest, Bytes: canonical, SHA256: jcs.SHA256Hex(canonical)}, nil
}

// CardProblem is one located capability-card problem. Field is the card member
// ("" for the whole card) so catalog can turn it into /capability_card/<field>.
type CardProblem struct {
	Field   string
	Message string
}

var cardStringArrayKeys = []string{"includes", "excludes", "required_inputs", "expected_outputs", "examples"}

var cardKeys = []string{
	"purpose", "includes", "excludes", "required_inputs", "expected_outputs", "examples",
}

// ValidateCapabilityCard enforces the manifest schema's card semantics: the six
// required fields, exact types, no additional properties and at least two
// examples. The empty card {} is the honest "not filled in yet" state that the
// save path accepts (contract gap, ADR 0004 §13); it is NOT executable, so it
// fails here with a whole-card problem instead of being padded with fabricated
// examples.
func ValidateCapabilityCard(card map[string]any) []CardProblem {
	if len(card) == 0 {
		return []CardProblem{{
			Field:   "",
			Message: "capability_card is empty; the execution manifest requires purpose, includes, excludes, required_inputs, expected_outputs and at least two examples",
		}}
	}
	problems := make([]CardProblem, 0)
	unknown := make([]string, 0)
	for key := range card {
		if !containsString(cardKeys, key) {
			unknown = append(unknown, key)
		}
	}
	sort.Strings(unknown)
	for _, key := range unknown {
		problems = append(problems, CardProblem{
			Field:   key,
			Message: "capability_card has an unknown field; the manifest schema allows exactly the six DraftInput fields",
		})
	}
	if purpose, present := card["purpose"]; !present {
		problems = append(problems, CardProblem{Field: "purpose", Message: "capability_card.purpose is required"})
	} else if _, ok := purpose.(string); !ok {
		problems = append(problems, CardProblem{Field: "purpose", Message: "capability_card.purpose must be a string"})
	}
	for _, key := range cardStringArrayKeys {
		value, present := card[key]
		if !present {
			problems = append(problems, CardProblem{
				Field: key, Message: "capability_card." + key + " is required",
			})
			continue
		}
		items, ok := value.([]any)
		if !ok {
			problems = append(problems, CardProblem{
				Field: key, Message: "capability_card." + key + " must be an array of strings",
			})
			continue
		}
		valid := true
		for _, item := range items {
			if _, isString := item.(string); !isString {
				valid = false
				break
			}
		}
		if !valid {
			problems = append(problems, CardProblem{
				Field: key, Message: "capability_card." + key + " must be an array of strings",
			})
			continue
		}
		if key == "examples" && len(items) < 2 {
			problems = append(problems, CardProblem{
				Field: key, Message: "capability_card.examples must contain at least 2 examples",
			})
		}
	}
	return problems
}

func normaliseTools(tools []ToolArtifact) ([]ToolArtifact, error) {
	byID := make(map[string]string, len(tools))
	for _, tool := range tools {
		if !uuid.Valid(tool.ID) {
			return nil, invalid("tool_artifacts", "tool id "+tool.ID+" must be a UUID")
		}
		if !digestPattern.MatchString(tool.ImageDigest) {
			return nil, invalid("tool_artifacts", "tool "+tool.ID+" must have a sha256 image digest")
		}
		if existing, present := byID[tool.ID]; present && existing != tool.ImageDigest {
			return nil, invalid("tool_artifacts", "tool "+tool.ID+" is referenced with two different image digests")
		}
		byID[tool.ID] = tool.ImageDigest
	}
	ids := make([]string, 0, len(byID))
	for id := range byID {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	normalised := make([]ToolArtifact, 0, len(ids))
	for _, id := range ids {
		normalised = append(normalised, ToolArtifact{ID: id, ImageDigest: byID[id]})
	}
	return normalised, nil
}

func normaliseUUIDSet(field string, values []string) ([]string, error) {
	seen := make(map[string]bool, len(values))
	for _, value := range values {
		if !uuid.Valid(value) {
			return nil, invalid(field, "value "+value+" must be a UUID")
		}
		seen[value] = true
	}
	sorted := make([]string, 0, len(seen))
	for value := range seen {
		sorted = append(sorted, value)
	}
	sort.Strings(sorted)
	return sorted, nil
}

func containsString(values []string, wanted string) bool {
	for _, value := range values {
		if value == wanted {
			return true
		}
	}
	return false
}
