package catalog

// Execution validation for F02A: turn one stored draft into the fixed execution
// manifest and its canonical bytes.
//
// This file is the seam T025 (validate) and T027 (debug) share:
//
//   - ValidateExecution loads the draft owner-only, runs the P05 definition
//     validator and the manifest-schema capability-card check, resolves the fixed
//     dependency closure from the real tables, compiles the manifest, and seals
//     its canonical bytes as an artifact.
//   - It never creates a version, snapshot or report: validate is not an
//     execution identity. The manifest artifact is content-addressed by
//     (application_id, execution_revision, manifest_sha256), so re-validating an
//     unchanged draft (including after a layout-only save) returns the same
//     bytes and hash without another object.
//   - Dependency readiness is a Go decision over real rows. `platform.tool_versions`
//     is created by 0012 as a read model (F05 owns builds/approval); no code here
//     writes a tool, model or version row.
//
// Error codes added by this file (definition errors keep the P05 codes):
//
//	capability_card_incomplete  the card is {} or not the six-field manifest shape
//	dependency_invalid_id       a config value is not a UUID (defence in depth)
//	dependency_not_found        the referenced row does not exist
//	dependency_revoked          the referenced row is revoked
//	dependency_not_ready        model not passed / tool not ready, approved or image-pinned
//	dependency_not_visible      another principal's sub-app version without a read
//	                            grant, or another principal's unapproved tool in
//	                            author-private debug
//	dependency_cycle            the sub-application closure has a cycle (including
//	                            a draft that calls a version of its own application)
//
// Every error is located: `pointer` is either /capability_card[/field] or
// /nodes/<i>/config/<field>[/<j>], and `node_id` is filled when the pointer names
// one node. Messages contain ids and states, never another user's document body.

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"strconv"
	"strings"

	"orchara/platform/internal/compiler"
	"orchara/platform/internal/objectstore"
	"orchara/platform/internal/uuid"
)

// Execution dependency error codes. They are part of the documented manifest
// vocabulary (ADR 0004 §27) and are stable for T027/F02B clients.
const (
	CodeCapabilityCardIncomplete = "capability_card_incomplete"
	CodeDependencyInvalidID      = "dependency_invalid_id"
	CodeDependencyNotFound       = "dependency_not_found"
	CodeDependencyNotReady       = "dependency_not_ready"
	CodeDependencyRevoked        = "dependency_revoked"
	CodeDependencyNotVisible     = "dependency_not_visible"
	CodeDependencyCycle          = "dependency_cycle"
)

const (
	dependencyModel     = "model"
	dependencyTool      = "tool"
	dependencySubapp    = "subapp"
	dependencyConnector = "connector"
)

// ExecutionOptions selects who is compiling the draft.
//
// PrivateDebug is the author-private debug rule from DATA-MODEL §2: a tool must
// be ready and either approved or owned by the caller. Shared/submit execution
// (validate today, publication review later) requires approved_at. T027 passes
// the caller and PrivateDebug=true; ValidateExecution's caller id is used only
// for the sub-application ACL and the private-tool ownership rule.
type ExecutionOptions struct {
	PrivateDebug bool
}

// ValidatedExecution is a successfully compiled manifest plus the sealed
// artifact holding its exact canonical bytes. ArtifactID is what T027's
// execution_snapshots.manifest_artifact_id (and F02B's version row) references.
type ValidatedExecution struct {
	Manifest   *compiler.Manifest
	Bytes      []byte
	SHA256     string
	ArtifactID string
}

// dependencyRef is one located reference from a node config.
type dependencyRef struct {
	kind    string
	id      string
	pointer string
	nodeID  string
	// source is the primary profile version id when this ref is a declared
	// fallback of that profile (T045). It never changes the pointer: the
	// fallback list lives on the profile row, not in the draft document.
	source string
}

// ValidateExecution compiles one draft and seals the manifest bytes.
//
// Error precedence: definition errors, then capability-card errors, then
// dependency errors; a draft with any of those is not compiled at all, so a
// broken draft is reported even when the deployment has no worker digest. Only
// a fully valid draft needs the deployment inputs — a missing worker image
// digest/runtime lock is *compiler.UnconfiguredError (503), never a fake digest.
func (r *Reader) ValidateExecution(
	ctx context.Context,
	callerID, applicationID string,
	objects *objectstore.Store,
	inputs compiler.Inputs,
	options ExecutionOptions,
) (*ValidatedExecution, []ExecutableError, error) {
	draft, ownerID, err := r.loadDraft(ctx, applicationID)
	if err != nil {
		return nil, nil, err
	}
	// Validate is owner-only. A read grant is sufficient to read the draft and
	// insufficient to compile it; both "not the owner" and "invisible" answer
	// 404 so the endpoint cannot be used to enumerate ids.
	if !strings.EqualFold(callerID, ownerID) {
		return nil, nil, ErrNotVisible
	}

	document, definitionErrors, err := executableDocument(*draft)
	if err != nil {
		return nil, nil, err
	}
	card, cardErrors := capabilityCardDocument(draft.CapabilityCard)
	located := make([]ExecutableError, 0, len(definitionErrors)+len(cardErrors)+4)
	located = append(located, definitionErrors...)
	located = append(located, cardErrors...)
	if len(located) > 0 {
		return nil, located, nil
	}

	dependencies, dependencyErrors, err := r.checkDependencies(ctx, callerID, applicationID, collectDependencyRefs(document), options)
	if err != nil {
		return nil, nil, err
	}
	if len(dependencyErrors) > 0 {
		return nil, dependencyErrors, nil
	}

	resolved, err := inputs.Resolve()
	if err != nil {
		return nil, nil, err
	}
	built, err := compiler.Build(
		resolved,
		applicationID,
		draft.ExecutionRevision,
		document,
		card,
		dependencies.tools,
		dependencies.models,
		dependencies.subapps,
		dependencies.connectors...,
	)
	if err != nil {
		return nil, nil, fmt.Errorf("building the execution manifest: %w", err)
	}
	artifactID, err := r.persistManifestArtifact(ctx, applicationID, ownerID, draft.ExecutionRevision, built, objects)
	if err != nil {
		return nil, nil, err
	}
	return &ValidatedExecution{
		Manifest:   built.Manifest,
		Bytes:      built.Bytes,
		SHA256:     built.SHA256,
		ArtifactID: artifactID,
	}, nil, nil
}

// executionDependencySet is the normalised closure the builder consumes.
type executionDependencySet struct {
	tools      []compiler.ToolArtifact
	models     []string
	subapps    []string
	connectors []compiler.ConnectorPolicy
}

// normalizeDependencyID is the identity form of a dependency reference.
//
// UUIDs are case-insensitive identifiers, and PostgreSQL renders uuid text in
// lowercase, so readiness lookups and the manifest's normalized sets use this
// form: the same model/tool/sub-app id in uppercase must resolve to the same row
// as its lowercase twin. The author's original spelling remains in the definition
// document (and therefore in the sealed bytes) and in located error messages; only
// the identity comparison and the normalized manifest values are canonicalized.
func normalizeDependencyID(id string) string {
	return strings.ToLower(strings.TrimSpace(id))
}

// normalizedDependencyIDs canonicalizes a list of ids before a lookup, keeping
// the caller's order and dropping nothing: the loaders return one map keyed by
// the database's own lowercase text.
func normalizedDependencyIDs(ids []string) []string {
	normalized := make([]string, 0, len(ids))
	for _, id := range ids {
		normalized = append(normalized, normalizeDependencyID(id))
	}
	return uniqueStrings(normalized)
}

// checkDependencies resolves every reference and returns both the normalised set
// and every located problem. Unknown/revoked/unready/not-visible rows are
// errors; the returned set only contains usable values. T027 reuses this through
// ValidateExecution with PrivateDebug=true.
func (r *Reader) checkDependencies(
	ctx context.Context,
	callerID, applicationID string,
	refs []dependencyRef,
	options ExecutionOptions,
) (*executionDependencySet, []ExecutableError, error) {
	modelIDs, toolIDs, subappIDs := make([]string, 0), make([]string, 0), make([]string, 0)
	for _, ref := range refs {
		if !uuid.Valid(ref.id) {
			continue
		}
		// Lookup ids are compared case-insensitively: PostgreSQL stores uuid text
		// in lowercase, so an uppercase reference is the same dependency.
		id := normalizeDependencyID(ref.id)
		switch ref.kind {
		case dependencyModel:
			modelIDs = append(modelIDs, id)
		case dependencyTool:
			toolIDs = append(toolIDs, id)
		case dependencySubapp:
			subappIDs = append(subappIDs, id)
		}
	}
	models, err := r.loadModelDependencies(ctx, modelIDs)
	if err != nil {
		return nil, nil, err
	}
	// T045: every primary profile's declared fallback profiles join the same
	// closure and are frozen into the manifest beside the primary. The list is
	// stored on the profile row (ModelConfig.fallback_profile_version_ids), not
	// in the draft document, so the expansion needs the loaded rows; each
	// expanded ref keeps the primary's document pointer and names its source.
	fallbackRefs := fallbackModelRefs(refs, models)
	if len(fallbackRefs) > 0 {
		fallbackIDs := make([]string, 0, len(fallbackRefs))
		for _, ref := range fallbackRefs {
			fallbackIDs = append(fallbackIDs, ref.id)
		}
		fallbacks, err := r.loadModelDependencies(ctx, fallbackIDs)
		if err != nil {
			return nil, nil, err
		}
		for id, row := range fallbacks {
			models[id] = row
		}
		refs = append(refs, fallbackRefs...)
	}
	tools, err := r.loadToolDependencies(ctx, toolIDs)
	if err != nil {
		return nil, nil, err
	}
	// F06/T048 §3: a tool version's declared connector ids expand the closure.
	// The reference is located at the tool's own document pointer (that is the
	// field that selected the tool) and names the tool as its source, so a
	// missing/revoked/unapproved connector is reported where the author can fix
	// it. Connector ids are not a node config field in this batch.
	connectorRefs := connectorRefsForTools(refs, tools)
	var connectors map[string]connectorDependency
	if len(connectorRefs) > 0 {
		connectorIDs := make([]string, 0, len(connectorRefs))
		for _, ref := range connectorRefs {
			connectorIDs = append(connectorIDs, ref.id)
		}
		connectors, err = r.loadConnectorDependencies(ctx, connectorIDs)
		if err != nil {
			return nil, nil, err
		}
		refs = append(refs, connectorRefs...)
	}
	subapps, err := r.loadSubappDependencies(ctx, subappIDs)
	if err != nil {
		return nil, nil, err
	}

	set := &executionDependencySet{
		tools:      make([]compiler.ToolArtifact, 0, len(tools)),
		models:     make([]string, 0, len(models)),
		subapps:    make([]string, 0, len(subapps)),
		connectors: make([]compiler.ConnectorPolicy, 0, len(connectors)),
	}
	located := make([]ExecutableError, 0)
	emitted := make(map[string]bool)

	for _, ref := range refs {
		// The same dependency referenced from two nodes (even with different UUID
		// letter case) is one readiness fact; report it once at its first
		// location, then include it once.
		id := normalizeDependencyID(ref.id)
		key := ref.kind + ":" + id
		if emitted[key] {
			continue
		}
		emitted[key] = true
		if !uuid.Valid(ref.id) {
			message := "the referenced id is not a UUID; fixed versions, never latest or a name, are required"
			if ref.source != "" {
				message = "the declared fallback profile version " + ref.id + " of " + ref.source +
					" is not a UUID; fixed versions are required"
			}
			located = append(located, ExecutableError{
				NodeID:  ref.nodeID,
				Code:    CodeDependencyInvalidID,
				Pointer: ref.pointer,
				Message: message,
			})
			continue
		}
		switch ref.kind {
		case dependencyModel:
			row, present := models[id]
			if code, message := modelReadinessProblem(row, present); code != "" {
				if ref.source != "" {
					message = "fallback profile version " + id + " declared by " + ref.source + ": " + message
				}
				located = append(located, ExecutableError{NodeID: ref.nodeID, Code: code, Pointer: ref.pointer, Message: message})
				continue
			}
			set.models = append(set.models, id)
		case dependencyTool:
			row, present := tools[id]
			if code, message := toolReadinessProblem(row, present, callerID, options.PrivateDebug); code != "" {
				located = append(located, ExecutableError{NodeID: ref.nodeID, Code: code, Pointer: ref.pointer, Message: message})
				continue
			}
			set.tools = append(set.tools, compiler.ToolArtifact{ID: id, ImageDigest: row.imageDigest.String})
		case dependencySubapp:
			row, present := subapps[id]
			if code, message := subappReadinessProblem(row, present); code != "" {
				located = append(located, ExecutableError{NodeID: ref.nodeID, Code: code, Pointer: ref.pointer, Message: message})
				continue
			}
			readable, err := r.subappReadable(ctx, callerID, row.ownerID, row.applicationID)
			if err != nil {
				return nil, nil, err
			}
			if !readable {
				located = append(located, ExecutableError{
					NodeID: ref.nodeID, Code: CodeDependencyNotVisible, Pointer: ref.pointer,
					Message: "the sub-application version belongs to an application the caller cannot read; another principal's dependency is not included silently",
				})
				continue
			}
			set.subapps = append(set.subapps, id)
		case dependencyConnector:
			row, present := connectors[id]
			if code, message := connectorReadinessProblem(row, present); code != "" {
				located = append(located, ExecutableError{NodeID: ref.nodeID, Code: code, Pointer: ref.pointer, Message: message})
				continue
			}
			set.connectors = append(set.connectors, compiler.ConnectorPolicy{
				VersionID: id, PolicySHA256: row.policySHA256,
			})
		}
	}
	if len(located) > 0 {
		return nil, located, nil
	}

	cycleErrors, err := r.checkSubappCycles(ctx, applicationID, refs, subapps)
	if err != nil {
		return nil, nil, err
	}
	if len(cycleErrors) > 0 {
		return nil, cycleErrors, nil
	}
	return set, nil, nil
}

// subappReadable applies the read ACL to a dependency application. An empty
// applicationID means "no ACL resource" (tools in this batch); it returns true
// so the ownership rule alone decides.
func (r *Reader) subappReadable(ctx context.Context, callerID, ownerID, applicationID string) (bool, error) {
	if applicationID == "" {
		return true, nil
	}
	return r.readAllowed(ctx, callerID, applicationID, ownerID)
}

// ---------------------------------------------------------------------------
// node config extraction
// ---------------------------------------------------------------------------

// collectDependencyRefs walks the validated nodes in document order and records
// every fixed dependency with its exact config pointer. The P05 validator has
// already checked that config values are UUID strings and arrays, so this is a
// structural walk, not a second validator.
func collectDependencyRefs(document map[string]any) []dependencyRef {
	nodes, ok := document["nodes"].([]any)
	if !ok {
		return nil
	}
	refs := make([]dependencyRef, 0, len(nodes))
	for position, rawNode := range nodes {
		node, ok := rawNode.(map[string]any)
		if !ok {
			continue
		}
		nodeType, _ := node["type"].(string)
		nodeID, _ := node["id"].(string)
		config, ok := node["config"].(map[string]any)
		if !ok {
			continue
		}
		add := func(kind, field string) {
			pointer := fmt.Sprintf("/nodes/%d/config/%s", position, field)
			switch typed := config[field].(type) {
			case string:
				refs = append(refs, dependencyRef{kind: kind, id: typed, pointer: pointer, nodeID: nodeID})
			case []any:
				for itemIndex, item := range typed {
					value, ok := item.(string)
					if !ok {
						continue
					}
					arrayPointer := fmt.Sprintf("/nodes/%d/config/%s/%d", position, field, itemIndex)
					refs = append(refs, dependencyRef{kind: kind, id: value, pointer: arrayPointer, nodeID: nodeID})
				}
			}
		}
		switch nodeType {
		case "model":
			add(dependencyModel, "model_profile_version")
		case "agent":
			add(dependencyModel, "model_profile_version")
			add(dependencyTool, "tool_versions")
			add(dependencySubapp, "subapp_versions")
		case "python":
			add(dependencyTool, "tool_version")
		case "call_app":
			add(dependencySubapp, "application_version")
		}
	}
	return refs
}

// ---------------------------------------------------------------------------
// readiness rules (pure functions so the whole matrix is unit-testable)
// ---------------------------------------------------------------------------

type modelDependency struct {
	validationState string
	revoked         bool
	// fallbackIDs is the profile's declared ModelConfig.fallback_profile_version_ids.
	// They join the frozen closure exactly like the primary (T045), so a run can
	// never dispatch to a profile its manifest did not freeze.
	fallbackIDs []string
}

type toolDependency struct {
	ownerID     string
	state       string
	approved    bool
	imageDigest sql.NullString
	// connectorIDs is the tool version's declared connector_versions list
	// (migration 0012). T048 expands each into the manifest connector closure.
	connectorIDs []string
}

// connectorDependency is one registry row reduced to the readiness facts the
// manifest closure needs.
type connectorDependency struct {
	policySHA256 string
	approved     bool
	revoked      bool
}

type subappDependency struct {
	applicationID string
	ownerID       string
	revoked       bool
	archived      bool
}

func modelReadinessProblem(row modelDependency, present bool) (string, string) {
	if !present {
		return CodeDependencyNotFound, "the referenced model profile version does not exist; fixed model registrations are required"
	}
	if row.revoked {
		return CodeDependencyRevoked, "the referenced model profile version is revoked"
	}
	if row.validationState != "passed" {
		return CodeDependencyNotReady, "the referenced model profile version has validation_state=" +
			row.validationState + "; only a passed model can be compiled into a manifest"
	}
	return "", ""
}

func toolReadinessProblem(row toolDependency, present bool, callerID string, privateDebug bool) (string, string) {
	if !present {
		return CodeDependencyNotFound, "the referenced tool version does not exist; fixed tool versions are required"
	}
	if row.state == "revoked" {
		return CodeDependencyRevoked, "the referenced tool version is revoked"
	}
	if row.state != "ready" {
		return CodeDependencyNotReady, "the referenced tool version has state=" + row.state +
			"; only a ready tool (built, image-pinned) can be compiled into a manifest"
	}
	if !row.imageDigest.Valid || row.imageDigest.String == "" {
		return CodeDependencyNotReady, "the referenced ready tool version has no image digest"
	}
	if privateDebug {
		if row.approved || strings.EqualFold(row.ownerID, callerID) {
			return "", ""
		}
		return CodeDependencyNotVisible, "the referenced tool version is owned by another principal and is not approved; author-private debug may only use the author's own ready tools"
	}
	if !row.approved {
		return CodeDependencyNotReady, "the referenced tool version is not approved (approved_at is null); shared execution requires an organization-approved tool"
	}
	return "", ""
}

func subappReadinessProblem(row subappDependency, present bool) (string, string) {
	if !present {
		return CodeDependencyNotFound, "the referenced application version does not exist; fixed published versions are required"
	}
	if row.revoked {
		return CodeDependencyRevoked, "the referenced application version is revoked"
	}
	if row.archived {
		return CodeDependencyNotVisible, "the referenced application version belongs to an archived application"
	}
	return "", ""
}

// connectorReadinessProblem applies the T048 rule: the policy must exist, be
// approved and not revoked. A revoked policy is dependency_revoked; a missing or
// unapproved one is dependency_not_ready (or dependency_not_found).
func connectorReadinessProblem(row connectorDependency, present bool) (string, string) {
	if !present {
		return CodeDependencyNotFound, "the connector policy referenced by this tool does not exist; only administrator-registered fixed connectors are executable"
	}
	if row.revoked {
		return CodeDependencyRevoked, "the connector policy referenced by this tool is revoked"
	}
	if !row.approved || row.policySHA256 == "" {
		return CodeDependencyNotReady, "the connector policy referenced by this tool is not approved or has no sealed policy digest; shared execution requires an administrator-approved connector"
	}
	return "", ""
}

// connectorRefsForTools expands each referenced tool version's declared
// connector_versions into a located dependency reference. The pointer stays the
// tool's document pointer and source names the tool, so the author sees where
// the connector was selected. Invalid ids are kept verbatim (readiness reports
// dependency_invalid_id), never silently dropped.
func connectorRefsForTools(refs []dependencyRef, tools map[string]toolDependency) []dependencyRef {
	expanded := make([]dependencyRef, 0)
	seen := make(map[string]bool)
	for _, ref := range refs {
		if ref.kind != dependencyTool {
			continue
		}
		row, present := tools[normalizeDependencyID(ref.id)]
		if !present {
			continue
		}
		for _, connectorID := range row.connectorIDs {
			id := normalizeDependencyID(connectorID)
			if id == "" || seen[id] {
				continue
			}
			seen[id] = true
			expanded = append(expanded, dependencyRef{
				kind:    dependencyConnector,
				id:      connectorID,
				pointer: ref.pointer,
				nodeID:  ref.nodeID,
				source:  normalizeDependencyID(ref.id),
			})
		}
	}
	return expanded
}

// ---------------------------------------------------------------------------
// loading
// ---------------------------------------------------------------------------

func (r *Reader) loadModelDependencies(ctx context.Context, ids []string) (map[string]modelDependency, error) {
	rows := map[string]modelDependency{}
	if len(ids) == 0 {
		return rows, nil
	}
	result, err := r.DB.QueryContext(ctx, `
		SELECT id::text, validation_state, revoked_at IS NOT NULL, config
		  FROM platform.model_profile_versions
		 WHERE id::text = ANY($1::text[])`, normalizedDependencyIDs(ids))
	if err != nil {
		return nil, fmt.Errorf("reading model profile versions: %w", err)
	}
	defer result.Close()
	for result.Next() {
		var (
			id, state string
			config    []byte
			row       modelDependency
		)
		if err := result.Scan(&id, &state, &row.revoked, &config); err != nil {
			return nil, fmt.Errorf("reading model profile versions: %w", err)
		}
		row.validationState = state
		row.fallbackIDs = modelFallbackIDs(config)
		rows[id] = row
	}
	if err := result.Err(); err != nil {
		return nil, fmt.Errorf("reading model profile versions: %w", err)
	}
	return rows, nil
}

// modelFallbackIDs reads the declared fallback list of one stored profile
// config. Invalid entries are kept verbatim so readiness can report
// dependency_invalid_id at the primary's pointer instead of silently dropping
// a misconfigured fallback.
func modelFallbackIDs(config []byte) []string {
	if len(config) == 0 {
		return nil
	}
	var decoded map[string]any
	if err := json.Unmarshal(config, &decoded); err != nil {
		return nil
	}
	raw, present := decoded["fallback_profile_version_ids"]
	if !present {
		return nil
	}
	items, ok := raw.([]any)
	if !ok {
		return nil
	}
	ids := make([]string, 0, len(items))
	for _, item := range items {
		value, ok := item.(string)
		if !ok {
			continue
		}
		ids = append(ids, strings.TrimSpace(value))
	}
	return ids
}

// fallbackModelRefs expands the closure with the fallback profile versions a
// primary profile declares. The pointer stays the primary's declaration (that
// is the document field that selected the profile) and source names the
// primary, so a located error is honest about where the reference came from.
func fallbackModelRefs(refs []dependencyRef, models map[string]modelDependency) []dependencyRef {
	expanded := make([]dependencyRef, 0, len(refs))
	seen := make(map[string]bool)
	for _, ref := range refs {
		if ref.kind != dependencyModel || !uuid.Valid(ref.id) {
			continue
		}
		row, present := models[normalizeDependencyID(ref.id)]
		if !present {
			continue
		}
		for _, fallbackID := range row.fallbackIDs {
			key := normalizeDependencyID(fallbackID)
			if key == "" || seen[key] {
				continue
			}
			seen[key] = true
			expanded = append(expanded, dependencyRef{
				kind:    dependencyModel,
				id:      fallbackID,
				pointer: ref.pointer,
				nodeID:  ref.nodeID,
				source:  normalizeDependencyID(ref.id),
			})
		}
	}
	return expanded
}

func (r *Reader) loadToolDependencies(ctx context.Context, ids []string) (map[string]toolDependency, error) {
	rows := map[string]toolDependency{}
	if len(ids) == 0 {
		return rows, nil
	}
	result, err := r.DB.QueryContext(ctx, `
		SELECT id::text, owner_id::text, state, approved_at IS NOT NULL, image_digest,
		       connector_versions
		  FROM platform.tool_versions
		 WHERE id::text = ANY($1::text[])`, normalizedDependencyIDs(ids))
	if err != nil {
		return nil, fmt.Errorf("reading tool versions: %w", err)
	}
	defer result.Close()
	for result.Next() {
		var (
			id            string
			row           toolDependency
			connectorsRaw []byte
		)
		if err := result.Scan(&id, &row.ownerID, &row.state, &row.approved,
			&row.imageDigest, &connectorsRaw); err != nil {
			return nil, fmt.Errorf("reading tool versions: %w", err)
		}
		row.connectorIDs = connectorVersionIDs(connectorsRaw)
		rows[id] = row
	}
	if err := result.Err(); err != nil {
		return nil, fmt.Errorf("reading tool versions: %w", err)
	}
	return rows, nil
}

// connectorVersionIDs reads a tool version's connector_versions jsonb list.
// Invalid entries stay verbatim so readiness can locate dependency_invalid_id.
func connectorVersionIDs(raw []byte) []string {
	if len(raw) == 0 {
		return nil
	}
	var items []any
	if err := json.Unmarshal(raw, &items); err != nil {
		return nil
	}
	ids := make([]string, 0, len(items))
	for _, item := range items {
		if value, ok := item.(string); ok {
			ids = append(ids, strings.TrimSpace(value))
		}
	}
	return ids
}

// loadConnectorDependencies reads the registry rows the tool closure named.
func (r *Reader) loadConnectorDependencies(ctx context.Context, ids []string) (map[string]connectorDependency, error) {
	rows := map[string]connectorDependency{}
	if len(ids) == 0 {
		return rows, nil
	}
	result, err := r.DB.QueryContext(ctx, `
		SELECT id::text, policy_sha256, approved_at IS NOT NULL, revoked_at IS NOT NULL
		  FROM platform.connector_policies
		 WHERE id::text = ANY($1::text[])`, normalizedDependencyIDs(ids))
	if err != nil {
		return nil, fmt.Errorf("reading connector policies: %w", err)
	}
	defer result.Close()
	for result.Next() {
		var (
			id  string
			row connectorDependency
		)
		if err := result.Scan(&id, &row.policySHA256, &row.approved, &row.revoked); err != nil {
			return nil, fmt.Errorf("reading connector policies: %w", err)
		}
		rows[id] = row
	}
	if err := result.Err(); err != nil {
		return nil, fmt.Errorf("reading connector policies: %w", err)
	}
	return rows, nil
}

func (r *Reader) loadSubappDependencies(ctx context.Context, ids []string) (map[string]subappDependency, error) {
	rows := map[string]subappDependency{}
	if len(ids) == 0 {
		return rows, nil
	}
	result, err := r.DB.QueryContext(ctx, `
		SELECT v.id::text, v.application_id::text, a.owner_id::text,
		       v.revoked_at IS NOT NULL, a.archived_at IS NOT NULL
		  FROM platform.application_versions AS v
		  JOIN platform.applications AS a ON a.id = v.application_id
		 WHERE v.id::text = ANY($1::text[])`, normalizedDependencyIDs(ids))
	if err != nil {
		return nil, fmt.Errorf("reading application versions: %w", err)
	}
	defer result.Close()
	for result.Next() {
		var (
			id  string
			row subappDependency
		)
		if err := result.Scan(&id, &row.applicationID, &row.ownerID, &row.revoked, &row.archived); err != nil {
			return nil, fmt.Errorf("reading application versions: %w", err)
		}
		rows[id] = row
	}
	if err := result.Err(); err != nil {
		return nil, fmt.Errorf("reading application versions: %w", err)
	}
	return rows, nil
}

// ---------------------------------------------------------------------------
// sub-application cycle detection
// ---------------------------------------------------------------------------

// checkSubappCycles rejects a sub-application closure that circles back:
//
//   - a published version this draft calls, or one of its version_dependencies
//     descendants, belongs to the application being validated (a draft calling
//     its own application would recurse);
//   - the reachable version_dependencies edges themselves contain a cycle.
//
// The in-document call graph cannot cycle by itself (one draft has no nested
// drafts); its only possible cycle is the self-application case above. Edges
// come from platform.version_dependencies, which F02B populates when versions
// are published; tests seed real rows.
func (r *Reader) checkSubappCycles(
	ctx context.Context,
	applicationID string,
	refs []dependencyRef,
	subapps map[string]subappDependency,
) ([]ExecutableError, error) {
	roots := make([]string, 0)
	for _, ref := range refs {
		if ref.kind != dependencySubapp || !uuid.Valid(ref.id) {
			continue
		}
		id := normalizeDependencyID(ref.id)
		if _, present := subapps[id]; !present {
			continue
		}
		roots = append(roots, id)
	}
	roots = uniqueStrings(roots)
	if len(roots) == 0 {
		return nil, nil
	}

	edges, err := r.loadVersionEdges(ctx, roots)
	if err != nil {
		return nil, err
	}
	versionIDs := make([]string, 0, len(edges)+len(roots))
	versionIDs = append(versionIDs, roots...)
	for parent, children := range edges {
		versionIDs = append(versionIDs, parent)
		versionIDs = append(versionIDs, children...)
	}
	versionIDs = uniqueStrings(versionIDs)
	applications, err := r.loadVersionApplications(ctx, versionIDs)
	if err != nil {
		return nil, err
	}
	selfVersions := make(map[string]bool)
	for id, appID := range applications {
		if strings.EqualFold(appID, applicationID) {
			selfVersions[id] = true
		}
	}
	cycle := findCycle(edges)
	cycleSet := make(map[string]bool, len(cycle))
	for _, id := range cycle {
		cycleSet[id] = true
	}

	// Locate the problem at the first reference (in node order) whose reachable
	// closure participates in the cycle/self-call.
	for _, ref := range refs {
		if ref.kind != dependencySubapp || !uuid.Valid(ref.id) {
			continue
		}
		id := normalizeDependencyID(ref.id)
		if _, present := subapps[id]; !present {
			continue
		}
		reachable := reachableVersions(id, edges)
		selfHit := ""
		for _, id := range reachable {
			if selfVersions[id] {
				selfHit = id
				break
			}
		}
		if selfHit != "" {
			return []ExecutableError{{
				NodeID:  ref.nodeID,
				Code:    CodeDependencyCycle,
				Pointer: ref.pointer,
				Message: "the sub-application closure calls version " + selfHit + " of the application being validated; a draft cannot depend on its own application",
			}}, nil
		}
		cycleHit := false
		for _, id := range reachable {
			if cycleSet[id] {
				cycleHit = true
				break
			}
		}
		if cycleHit {
			return []ExecutableError{{
				NodeID:  ref.nodeID,
				Code:    CodeDependencyCycle,
				Pointer: ref.pointer,
				Message: "the sub-application dependency graph contains a cycle through version " + strings.Join(cycle, " -> "),
			}}, nil
		}
	}
	return nil, nil
}

// loadVersionEdges reads the version_dependencies closure reachable from roots.
// UNION (not UNION ALL) makes a cyclic edge set terminate; the cycle is then
// reported by findCycle instead of hanging the query.
func (r *Reader) loadVersionEdges(ctx context.Context, roots []string) (map[string][]string, error) {
	rows, err := r.DB.QueryContext(ctx, `
		WITH RECURSIVE closure(parent_version_id, child_version_id) AS (
		    SELECT parent_version_id, child_version_id
		      FROM platform.version_dependencies
		     WHERE parent_version_id::text = ANY($1::text[])
		    UNION
		    SELECT d.parent_version_id, d.child_version_id
		      FROM platform.version_dependencies AS d
		      JOIN closure AS c ON d.parent_version_id = c.child_version_id
		)
		SELECT parent_version_id::text, child_version_id::text FROM closure`, normalizedDependencyIDs(roots))
	if err != nil {
		return nil, fmt.Errorf("reading version dependencies: %w", err)
	}
	defer rows.Close()
	edges := make(map[string][]string)
	for rows.Next() {
		var parent, child string
		if err := rows.Scan(&parent, &child); err != nil {
			return nil, fmt.Errorf("reading version dependencies: %w", err)
		}
		edges[parent] = append(edges[parent], child)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("reading version dependencies: %w", err)
	}
	return edges, nil
}

func (r *Reader) loadVersionApplications(ctx context.Context, ids []string) (map[string]string, error) {
	rows := map[string]string{}
	if len(ids) == 0 {
		return rows, nil
	}
	result, err := r.DB.QueryContext(ctx, `
		SELECT id::text, application_id::text
		  FROM platform.application_versions
		 WHERE id::text = ANY($1::text[])`, normalizedDependencyIDs(ids))
	if err != nil {
		return nil, fmt.Errorf("reading application versions: %w", err)
	}
	defer result.Close()
	for result.Next() {
		var id, applicationID string
		if err := result.Scan(&id, &applicationID); err != nil {
			return nil, fmt.Errorf("reading application versions: %w", err)
		}
		rows[id] = applicationID
	}
	if err := result.Err(); err != nil {
		return nil, fmt.Errorf("reading application versions: %w", err)
	}
	return rows, nil
}

// reachableVersions returns root plus every version reachable through the edges.
func reachableVersions(root string, edges map[string][]string) []string {
	seen := map[string]bool{root: true}
	queue := []string{root}
	visited := make([]string, 0, 8)
	for len(queue) > 0 {
		current := queue[0]
		queue = queue[1:]
		visited = append(visited, current)
		for _, child := range edges[current] {
			if seen[child] {
				continue
			}
			seen[child] = true
			queue = append(queue, child)
		}
	}
	sort.Strings(visited)
	return visited
}

// findCycle returns one cycle path in the directed graph, or nil. The graph may
// contain nodes with no outgoing edges; only real back edges count.
func findCycle(edges map[string][]string) []string {
	const (
		white = 0
		grey  = 1
		black = 2
	)
	color := make(map[string]int, len(edges))
	stack := make([]string, 0, len(edges))
	var found []string
	var visit func(node string) bool
	visit = func(node string) bool {
		color[node] = grey
		stack = append(stack, node)
		for _, child := range edges[node] {
			switch color[child] {
			case white:
				if visit(child) {
					return true
				}
			case grey:
				// Found a back edge: slice the current stack from the child.
				start := 0
				for index, entry := range stack {
					if entry == child {
						start = index
						break
					}
				}
				found = append(found, stack[start:]...)
				found = append(found, child)
				return true
			}
		}
		stack = stack[:len(stack)-1]
		color[node] = black
		return false
	}
	roots := make([]string, 0, len(edges))
	for parent := range edges {
		roots = append(roots, parent)
	}
	sort.Strings(roots)
	for _, root := range roots {
		if color[root] == white && visit(root) {
			return found
		}
	}
	return nil
}

// ---------------------------------------------------------------------------
// capability card
// ---------------------------------------------------------------------------

// capabilityCardDocument decodes the stored card and maps manifest-schema
// problems to located errors. {} is the documented not-yet-filled state and
// fails here (it is not executable), never padded with invented examples.
func capabilityCardDocument(raw json.RawMessage) (map[string]any, []ExecutableError) {
	trimmed := bytes.TrimSpace(raw)
	if len(trimmed) == 0 || trimmed[0] != '{' {
		return nil, []ExecutableError{{
			Code:    CodeCapabilityCardIncomplete,
			Pointer: "/capability_card",
			Message: "capability_card must be a JSON object",
		}}
	}
	var card map[string]any
	if err := json.Unmarshal(trimmed, &card); err != nil || card == nil {
		return nil, []ExecutableError{{
			Code:    CodeCapabilityCardIncomplete,
			Pointer: "/capability_card",
			Message: "capability_card must be a JSON object",
		}}
	}
	problems := compiler.ValidateCapabilityCard(card)
	if len(problems) == 0 {
		return card, nil
	}
	located := make([]ExecutableError, 0, len(problems))
	for _, problem := range problems {
		pointer := "/capability_card"
		if problem.Field != "" {
			pointer += "/" + problem.Field
		}
		located = append(located, ExecutableError{
			Code:    CodeCapabilityCardIncomplete,
			Pointer: pointer,
			Message: problem.Message,
		})
	}
	return card, located
}

// ---------------------------------------------------------------------------
// sealed manifest bytes
// ---------------------------------------------------------------------------

// persistManifestArtifact seals the canonical manifest bytes and registers the
// content-addressed artifact. The advisory transaction lock serialises two
// concurrent validates of the same (application, execution revision, hash), so
// exactly one object is written; a repeated validate returns the existing
// artifact and writes nothing. A crash between the object write and the commit
// can leave an unreferenced sealed object; T026's orphan sweep owns that cleanup,
// and no second object is ever created for the same identity.
func (r *Reader) persistManifestArtifact(
	ctx context.Context,
	applicationID, ownerID string,
	executionRevision int64,
	built *compiler.Built,
	objects *objectstore.Store,
) (string, error) {
	if objects == nil {
		return "", objectstore.ErrNotConfigured
	}
	tx, err := r.DB.BeginTx(ctx, nil)
	if err != nil {
		return "", fmt.Errorf("starting the manifest artifact transaction: %w", err)
	}
	defer func() { _ = tx.Rollback() }()

	lockKey := applicationID + "|" + strconv.FormatInt(executionRevision, 10) + "|" + built.SHA256
	if _, err := tx.ExecContext(ctx, `SELECT pg_advisory_xact_lock(hashtextextended($1, 0))`, lockKey); err != nil {
		return "", fmt.Errorf("serialising the manifest artifact write: %w", err)
	}

	var (
		existingID        string
		existingState     string
		existingDeleted   bool
		existingSHA256    sql.NullString
		existingSizeBytes sql.NullInt64
	)
	err = tx.QueryRowContext(ctx, `
		SELECT a.id::text, a.state, a.deleted_at IS NOT NULL, a.sha256, a.size_bytes
		  FROM platform.manifest_artifacts AS m
		  JOIN platform.artifacts AS a ON a.id = m.artifact_id
		 WHERE m.application_id = $1
		   AND m.execution_revision = $2
		   AND m.manifest_sha256 = $3`,
		applicationID, executionRevision, built.SHA256,
	).Scan(&existingID, &existingState, &existingDeleted, &existingSHA256, &existingSizeBytes)
	switch {
	case err == nil:
		if existingState != "ready" || existingDeleted ||
			!existingSHA256.Valid || existingSHA256.String != built.SHA256 ||
			!existingSizeBytes.Valid || existingSizeBytes.Int64 != int64(len(built.Bytes)) {
			return "", fmt.Errorf("the recorded manifest artifact %s is not a usable copy of the same bytes", existingID)
		}
		if err := tx.Commit(); err != nil {
			return "", fmt.Errorf("committing the manifest artifact transaction: %w", err)
		}
		return existingID, nil
	case !errors.Is(err, sql.ErrNoRows):
		return "", fmt.Errorf("reading the recorded manifest artifact: %w", err)
	}

	sealedKey, err := objectstore.SealedKey()
	if err != nil {
		return "", err
	}
	object, err := objects.PutSealed(sealedKey, bytes.NewReader(built.Bytes), int64(len(built.Bytes)))
	if err != nil {
		return "", fmt.Errorf("sealing the manifest bytes: %w", err)
	}
	if object.SHA256 != built.SHA256 || object.Size != int64(len(built.Bytes)) {
		_ = objects.Delete(sealedKey)
		return "", fmt.Errorf("the sealed manifest object does not match the canonical bytes")
	}
	discardObject := func() { _ = objects.Delete(sealedKey) }

	artifactID := uuid.NewString()
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO platform.artifacts
		       (id, owner_id, object_key, purpose, sha256, media_type, size_bytes, state, revision)
		VALUES ($1::uuid, $2::uuid, $3, 'execution_manifest', $4, 'application/json', $5, 'ready', 1)`,
		artifactID, ownerID, sealedKey, built.SHA256, int64(len(built.Bytes)),
	); err != nil {
		discardObject()
		return "", fmt.Errorf("registering the manifest artifact: %w", err)
	}
	result, err := tx.ExecContext(ctx, `
		INSERT INTO platform.manifest_artifacts
		       (application_id, execution_revision, manifest_sha256, artifact_id)
		VALUES ($1::uuid, $2, $3, $4::uuid)
		ON CONFLICT (application_id, execution_revision, manifest_sha256) DO NOTHING`,
		applicationID, executionRevision, built.SHA256, artifactID,
	)
	if err != nil {
		discardObject()
		return "", fmt.Errorf("registering the manifest identity: %w", err)
	}
	if affected, err := result.RowsAffected(); err != nil || affected != 1 {
		// A writer that bypassed the advisory lock won. Keep its object and drop
		// ours, then return the recorded artifact.
		discardObject()
		var winner string
		if scanErr := tx.QueryRowContext(ctx, `
			SELECT artifact_id::text FROM platform.manifest_artifacts
			 WHERE application_id = $1 AND execution_revision = $2 AND manifest_sha256 = $3`,
			applicationID, executionRevision, built.SHA256,
		).Scan(&winner); scanErr != nil {
			return "", fmt.Errorf("reading the concurrently recorded manifest artifact: %w", scanErr)
		}
		if commitErr := tx.Commit(); commitErr != nil {
			return "", fmt.Errorf("committing the manifest artifact transaction: %w", commitErr)
		}
		return winner, nil
	}
	if err := tx.Commit(); err != nil {
		discardObject()
		return "", fmt.Errorf("committing the manifest artifact transaction: %w", err)
	}
	return artifactID, nil
}

func uniqueStrings(values []string) []string {
	seen := make(map[string]bool, len(values))
	unique := make([]string, 0, len(values))
	for _, value := range values {
		if seen[value] {
			continue
		}
		seen[value] = true
		unique = append(unique, value)
	}
	return unique
}
