package compiler

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"regexp"
	"sort"
	"strings"

	"orchara/platform/internal/jcs"
	"orchara/platform/internal/objectstore"
	"orchara/platform/internal/uuid"
)

// Verification failures. They are sentinels so the future worker (F03/F04A) can
// classify them: a hash mismatch is a tampered/corrupted artifact (business
// failure, never retried as infrastructure), while a missing object or an
// unconfigured store belongs to the storage layer.
var (
	// ErrManifestHashMismatch means the bytes do not hash to the recorded
	// digest. Verification stops before parsing on purpose.
	ErrManifestHashMismatch = errors.New("manifest bytes do not match the recorded SHA-256")
	// ErrManifestSizeMismatch means the stored object is not the recorded size.
	ErrManifestSizeMismatch = errors.New("manifest bytes do not match the recorded size")
	// ErrManifestTooLarge means the object exceeds the platform's object limit.
	ErrManifestTooLarge = errors.New("manifest object exceeds the platform size limit")
	// ErrManifestInvalid means the bytes are not a schema-valid canonical
	// execution manifest.
	ErrManifestInvalid = errors.New("sealed bytes are not a valid canonical execution manifest")
)

var recordedDigestPattern = regexp.MustCompile(`^[0-9a-fA-F]{64}$`)

// ArtifactRef is the recorded identity of one sealed manifest object, taken
// from the artifact row (object_key/sha256/size_bytes). The worker receives it
// from the claim/snapshot record; it is never derived from the object itself.
type ArtifactRef struct {
	ObjectKey string
	SHA256    string
	SizeBytes int64 // -1 means "size not recorded"; any other value is enforced.
}

// manifestKeys is the schema's required-and-only property set.
var manifestKeys = []string{
	"manifest_version", "application_id", "source_revision", "compiler_version",
	"worker_image_digest", "runtime_lock_sha256", "state_schema_version",
	"definition", "capability_card", "tool_artifacts", "model_profile_versions",
	"subapp_versions", "connector_policies",
}

// VerifyManifestBytes is the worker-side re-verification mechanism:
//
//  1. read the sealed object (bounded by the platform's object limit);
//  2. hash the bytes and compare size + SHA-256 with the recorded artifact —
//     BEFORE parsing, so a tampered object never reaches the parser;
//  3. require the bytes to be exactly their RFC 8785 canonical form;
//  4. decode and re-check every schema rule, returning the manifest.
//
// The real supervisor/worker does not exist yet (F03/F04A). This function is the
// delivered mechanism; its caller is the future worker, and the tests prove it
// rejects tampered bytes, a wrong recorded hash, non-canonical JSON and
// schema-invalid manifests.
func VerifyManifestBytes(objects *objectstore.Store, artifact ArtifactRef) (*Manifest, []byte, error) {
	if objects == nil {
		return nil, nil, objectstore.ErrNotConfigured
	}
	if strings.TrimSpace(artifact.ObjectKey) == "" {
		return nil, nil, fmt.Errorf("%w: artifact object key is empty", ErrManifestHashMismatch)
	}
	if !recordedDigestPattern.MatchString(artifact.SHA256) {
		return nil, nil, fmt.Errorf("%w: recorded digest is not 64 hex characters", ErrManifestHashMismatch)
	}
	reader, _, err := objects.Open(artifact.ObjectKey)
	if err != nil {
		return nil, nil, fmt.Errorf("opening the sealed manifest object: %w", err)
	}
	defer func() { _ = reader.Close() }()

	payload, err := io.ReadAll(io.LimitReader(reader, objectstore.MaxObjectBytes+1))
	if err != nil {
		return nil, nil, fmt.Errorf("reading the sealed manifest object: %w", err)
	}
	if int64(len(payload)) > objectstore.MaxObjectBytes {
		return nil, nil, ErrManifestTooLarge
	}

	// Hash first, parse later. This ordering is the whole point of the
	// mechanism: the digest in the snapshot is the authority, not the JSON.
	sum := sha256.Sum256(payload)
	actual := hex.EncodeToString(sum[:])
	if !strings.EqualFold(actual, artifact.SHA256) {
		return nil, nil, fmt.Errorf("%w: recorded %s, bytes are %s",
			ErrManifestHashMismatch, strings.ToLower(artifact.SHA256), actual)
	}
	if artifact.SizeBytes >= 0 && int64(len(payload)) != artifact.SizeBytes {
		return nil, nil, fmt.Errorf("%w: recorded %d, bytes are %d",
			ErrManifestSizeMismatch, artifact.SizeBytes, len(payload))
	}

	canonical, err := jcs.CanonicalizeBytes(payload)
	if err != nil {
		return nil, nil, fmt.Errorf("%w: not canonical JSON: %v", ErrManifestInvalid, err)
	}
	if !bytes.Equal(canonical, payload) {
		return nil, nil, fmt.Errorf("%w: bytes are not the RFC 8785 canonical form", ErrManifestInvalid)
	}
	manifest, err := decodeManifest(payload)
	if err != nil {
		return nil, nil, err
	}
	return manifest, payload, nil
}

// decodeManifest enforces the execution-manifest schema semantics without a
// JSON-Schema runtime: exact key set, required fields, types, formats, and the
// builder's canonical ordering (sorted unique sets). Only manifests produced by
// Build are expected to pass.
func decodeManifest(raw []byte) (*Manifest, error) {
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(raw, &fields); err != nil {
		return nil, fmt.Errorf("%w: top level is not a JSON object: %v", ErrManifestInvalid, err)
	}
	if fields == nil {
		return nil, fmt.Errorf("%w: top level must be an object", ErrManifestInvalid)
	}
	for _, key := range manifestKeys {
		if _, present := fields[key]; !present {
			return nil, fmt.Errorf("%w: required field %q is missing", ErrManifestInvalid, key)
		}
	}
	for key := range fields {
		if !containsString(manifestKeys, key) {
			return nil, fmt.Errorf("%w: field %q is not in the manifest schema", ErrManifestInvalid, key)
		}
	}

	manifest := &Manifest{}
	var err error
	if manifest.ManifestVersion, err = decodeString(fields, "manifest_version"); err != nil {
		return nil, err
	}
	if manifest.ManifestVersion != ManifestVersion {
		return nil, fmt.Errorf("%w: manifest_version must be %q", ErrManifestInvalid, ManifestVersion)
	}
	if manifest.ApplicationID, err = decodeString(fields, "application_id"); err != nil {
		return nil, err
	}
	if !uuid.Valid(manifest.ApplicationID) {
		return nil, fmt.Errorf("%w: application_id must be a UUID", ErrManifestInvalid)
	}
	if manifest.SourceRevision, err = decodeInt64(fields, "source_revision"); err != nil {
		return nil, err
	}
	if manifest.SourceRevision < 1 {
		return nil, fmt.Errorf("%w: source_revision must be at least 1", ErrManifestInvalid)
	}
	if manifest.CompilerVersion, err = decodeString(fields, "compiler_version"); err != nil {
		return nil, err
	}
	if manifest.CompilerVersion == "" {
		return nil, fmt.Errorf("%w: compiler_version must not be empty", ErrManifestInvalid)
	}
	if manifest.WorkerImageDigest, err = decodeString(fields, "worker_image_digest"); err != nil {
		return nil, err
	}
	if !digestPattern.MatchString(manifest.WorkerImageDigest) {
		return nil, fmt.Errorf("%w: worker_image_digest must be sha256:<64 lowercase hex>", ErrManifestInvalid)
	}
	if manifest.RuntimeLockSHA256, err = decodeString(fields, "runtime_lock_sha256"); err != nil {
		return nil, err
	}
	if !lockPattern.MatchString(manifest.RuntimeLockSHA256) {
		return nil, fmt.Errorf("%w: runtime_lock_sha256 must be 64 lowercase hex characters", ErrManifestInvalid)
	}
	if manifest.StateSchemaVersion, err = decodeString(fields, "state_schema_version"); err != nil {
		return nil, err
	}
	if manifest.StateSchemaVersion == "" {
		return nil, fmt.Errorf("%w: state_schema_version must not be empty", ErrManifestInvalid)
	}
	if manifest.Definition, err = decodeObject(fields, "definition"); err != nil {
		return nil, err
	}
	if manifest.CapabilityCard, err = decodeObject(fields, "capability_card"); err != nil {
		return nil, err
	}
	if problems := ValidateCapabilityCard(manifest.CapabilityCard); len(problems) > 0 {
		return nil, fmt.Errorf("%w: capability_card: %s", ErrManifestInvalid, problems[0].Message)
	}
	if manifest.ToolArtifacts, err = decodeToolArtifacts(fields["tool_artifacts"]); err != nil {
		return nil, err
	}
	if manifest.ModelProfileVersions, err = decodeUUIDSet(fields, "model_profile_versions"); err != nil {
		return nil, err
	}
	if manifest.SubappVersions, err = decodeUUIDSet(fields, "subapp_versions"); err != nil {
		return nil, err
	}
	if manifest.ConnectorPolicies, err = decodeConnectorPolicies(fields["connector_policies"]); err != nil {
		return nil, err
	}
	return manifest, nil
}

func decodeString(fields map[string]json.RawMessage, name string) (string, error) {
	var value string
	if err := json.Unmarshal(fields[name], &value); err != nil {
		return "", fmt.Errorf("%w: %s must be a string", ErrManifestInvalid, name)
	}
	return value, nil
}

func decodeInt64(fields map[string]json.RawMessage, name string) (int64, error) {
	var number json.Number
	decoder := json.NewDecoder(bytes.NewReader(fields[name]))
	decoder.UseNumber()
	if err := decoder.Decode(&number); err != nil {
		return 0, fmt.Errorf("%w: %s must be an integer", ErrManifestInvalid, name)
	}
	value, err := number.Int64()
	if err != nil {
		return 0, fmt.Errorf("%w: %s must be an integer", ErrManifestInvalid, name)
	}
	return value, nil
}

func decodeObject(fields map[string]json.RawMessage, name string) (map[string]any, error) {
	var object map[string]any
	if err := json.Unmarshal(fields[name], &object); err != nil || object == nil {
		return nil, fmt.Errorf("%w: %s must be a JSON object", ErrManifestInvalid, name)
	}
	return object, nil
}

func decodeToolArtifacts(raw json.RawMessage) ([]ToolArtifact, error) {
	if raw == nil {
		return nil, fmt.Errorf("%w: tool_artifacts is missing", ErrManifestInvalid)
	}
	var entries []map[string]json.RawMessage
	if err := json.Unmarshal(raw, &entries); err != nil || entries == nil {
		return nil, fmt.Errorf("%w: tool_artifacts must be an array of objects", ErrManifestInvalid)
	}
	tools := make([]ToolArtifact, 0, len(entries))
	seen := map[string]bool{}
	for _, entry := range entries {
		if len(entry) != 2 {
			return nil, fmt.Errorf("%w: each tool artifact must have exactly id and image_digest", ErrManifestInvalid)
		}
		id, err := decodeString(entry, "id")
		if err != nil {
			return nil, err
		}
		digest, err := decodeString(entry, "image_digest")
		if err != nil {
			return nil, err
		}
		if !uuid.Valid(id) {
			return nil, fmt.Errorf("%w: tool artifact id must be a UUID", ErrManifestInvalid)
		}
		if !digestPattern.MatchString(digest) {
			return nil, fmt.Errorf("%w: tool artifact image_digest must be sha256:<64 lowercase hex>", ErrManifestInvalid)
		}
		if seen[id] {
			return nil, fmt.Errorf("%w: tool artifact ids must be unique", ErrManifestInvalid)
		}
		seen[id] = true
		tools = append(tools, ToolArtifact{ID: id, ImageDigest: digest})
	}
	if !sort.SliceIsSorted(tools, func(i, j int) bool { return tools[i].ID < tools[j].ID }) {
		return nil, fmt.Errorf("%w: tool_artifacts must be sorted by id", ErrManifestInvalid)
	}
	return tools, nil
}

func decodeUUIDSet(fields map[string]json.RawMessage, name string) ([]string, error) {
	var values []string
	if err := json.Unmarshal(fields[name], &values); err != nil || values == nil {
		return nil, fmt.Errorf("%w: %s must be an array of UUID strings", ErrManifestInvalid, name)
	}
	for index, value := range values {
		if !uuid.Valid(value) {
			return nil, fmt.Errorf("%w: %s[%d] must be a UUID", ErrManifestInvalid, name, index)
		}
		if index > 0 && values[index-1] >= value {
			return nil, fmt.Errorf("%w: %s must be sorted and unique", ErrManifestInvalid, name)
		}
	}
	return values, nil
}

func decodeConnectorPolicies(raw json.RawMessage) ([]ConnectorPolicy, error) {
	if raw == nil {
		return nil, fmt.Errorf("%w: connector_policies is missing", ErrManifestInvalid)
	}
	var entries []map[string]json.RawMessage
	if err := json.Unmarshal(raw, &entries); err != nil || entries == nil {
		return nil, fmt.Errorf("%w: connector_policies must be an array of objects", ErrManifestInvalid)
	}
	policies := make([]ConnectorPolicy, 0, len(entries))
	for _, entry := range entries {
		if len(entry) != 2 {
			return nil, fmt.Errorf("%w: each connector policy must have exactly version_id and policy_sha256", ErrManifestInvalid)
		}
		versionID, err := decodeString(entry, "version_id")
		if err != nil {
			return nil, err
		}
		policySHA, err := decodeString(entry, "policy_sha256")
		if err != nil {
			return nil, err
		}
		if !uuid.Valid(versionID) || !lockPattern.MatchString(policySHA) {
			return nil, fmt.Errorf("%w: connector policy version_id/policy_sha256 are malformed", ErrManifestInvalid)
		}
		policies = append(policies, ConnectorPolicy{VersionID: versionID, PolicySHA256: policySHA})
	}
	if !sort.SliceIsSorted(policies, func(i, j int) bool { return policies[i].VersionID < policies[j].VersionID }) {
		return nil, fmt.Errorf("%w: connector_policies must be sorted by version_id", ErrManifestInvalid)
	}
	return policies, nil
}
