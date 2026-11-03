// Package templates owns the F04A/T049 server-controlled application template
// registry and the non-contract "instantiate into my own application" bridge.
//
// A template is a private source draft: one application owned by the seeding
// administrator plus a revision-1 draft carrying the fixed sales definition and
// capability card. It is deliberately not a published application version:
// instantiating it creates an ordinary creator-private application, and the
// caller then uses the existing F02A debug path. Publication (a shared immutable
// version with a review) remains F02B's contract.
//
// What is real here:
//
//   - the template stores actual UUIDs for the fixed preset tool version and the
//     passed model profile version supplied by the seed command;
//   - the model profile must already have passed the real T039 register/validate
//     flow (this package refuses a pending, failed, revoked or non-stub profile;
//     it never marks one passed);
//   - the preset tool row carries the SHA-256 of the real sales_stats.py bytes as
//     its image digest;
//   - the copied definition is the same JSONB the source draft holds, so the
//     compile and debug checks are the normal product checks.
package templates

import (
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"orchara/platform/internal/uuid"
)

const (
	// SalesTemplateID is the fixed registry id of the seeded sales template.
	SalesTemplateID = "sales"
	// SalesTemplateName is the human-readable name stored on the source
	// application and copied to the instantiated application.
	SalesTemplateName = "销售统计模板"
	// PresetToolID is the fixed preset sales_stats tool version id every
	// executable definition in this build pins.
	PresetToolID = "11111111-1111-4111-8111-111111111111"
	// PresetToolName is the fixed tool name the capability ledger records.
	PresetToolName = "sales_stats"
	// presetToolRisk is the declared risk class of the read-only aggregation
	// tool. It reads the one authorized input file and writes only its summary
	// artifacts inside the sandbox.
	presetToolRisk = "read"
	// defaultToolScript is resolved against ORCHARA_PRODUCT_ROOT (or the current
	// directory) by the seed command; it is the same script the sandbox executes.
	defaultToolScript = "worker/platform_worker/nodes/sales_stats.py"
)

// ErrTemplateNotFound is returned for an unknown template id. It is also the
// answer for a malformed id, so the endpoint cannot enumerate templates.
var ErrTemplateNotFound = errors.New("application template not found")

// Summary is the seed command's JSON output. It contains identifiers only, never
// a secret or a credential reference.
type Summary struct {
	TemplateID     string `json:"template_id"`
	ApplicationID  string `json:"application_id"`
	ToolVersionID  string `json:"tool_version_id"`
	ModelVersionID string `json:"model_version_id"`
}

// Instance is the non-contract response of one instantiate call.
//
// Revision is the new application's draft revision (always 1: the copied draft
// is created, not saved). DefinitionRevision is the source template draft's
// revision, so a caller can tell which definition it received.
type Instance struct {
	ID                 string `json:"id"`
	Revision           int64  `json:"revision"`
	DefinitionRevision int64  `json:"definition_revision"`
}

// Service is the template registry. DB is the API/administrator role's database
// handle: the runtime worker role cannot read templates.
type Service struct {
	DB *sql.DB
	// ProductRoot resolves the preset tool script when ToolScriptPath is empty.
	// The CLI sets it from ORCHARA_PRODUCT_ROOT; tests may override it.
	ProductRoot string
	// ToolScriptPath overrides the sales_stats.py path (tests and deployments
	// that vendor the script elsewhere).
	ToolScriptPath string
}

// New returns a registry over db. The API only needs Instantiate; the seed
// command sets ProductRoot/ToolScriptPath on the returned service.
func New(db *sql.DB) *Service {
	return &Service{DB: db}
}

// SalesDefinition is the fixed four-node definition:
//
//	start --> stats(python, preset sales_stats) --> explain(agent, stub) --> end
//
// The python node is the only place numbers are produced. The agent receives
// the validated summary and may only explain it; the end node maps the verified
// summary, the report body and the copied totals into the application output.
// summary.json/summary.csv stay separate python-node file artifacts: the report
// never replaces or rewrites them.
func SalesDefinition(toolVersionID, modelVersionID string) map[string]any {
	summarySchema := map[string]any{
		"type": "object",
		"properties": map[string]any{
			"total":       map[string]any{"type": "string"},
			"regions":     map[string]any{"type": "object", "additionalProperties": map[string]any{"type": "string"}},
			"order_count": map[string]any{"type": "integer"},
		},
		"required":             []any{"total", "regions", "order_count"},
		"additionalProperties": false,
	}
	totalsSchema := map[string]any{
		"type": "object",
		"properties": map[string]any{
			"total":       map[string]any{"type": "string"},
			"regions":     map[string]any{"type": "object", "additionalProperties": map[string]any{"type": "string"}},
			"order_count": map[string]any{"type": "integer"},
		},
		"required":             []any{"total", "regions", "order_count"},
		"additionalProperties": false,
	}
	return map[string]any{
		"dsl_version": "1.0",
		"input_schema": map[string]any{
			"type": "object",
			"properties": map[string]any{
				"file":       map[string]any{"type": "string"},
				"media_type": map[string]any{"type": "string"},
			},
			"additionalProperties": false,
		},
		"output_schema": map[string]any{
			"type": "object",
			"properties": map[string]any{
				"summary": summarySchema,
				"report":  map[string]any{"type": "string"},
				"totals":  totalsSchema,
			},
			"required":             []any{"summary", "report", "totals"},
			"additionalProperties": false,
		},
		"nodes": []any{
			map[string]any{
				"id": "start", "type": "start", "inputs": map[string]any{},
				"output_schema": map[string]any{"type": "object"}, "config": map[string]any{},
			},
			map[string]any{
				"id": "stats", "type": "python",
				"inputs":        map[string]any{"file": map[string]any{"ref": "node:start#/artifact"}},
				"output_schema": summarySchema,
				"config":        map[string]any{"tool_version": toolVersionID},
			},
			map[string]any{
				"id": "explain", "type": "agent",
				"inputs": map[string]any{"summary": map[string]any{"ref": "node:stats#"}},
				"output_schema": map[string]any{
					"type": "object",
					"properties": map[string]any{
						"report": map[string]any{"type": "string"},
						"totals": totalsSchema,
					},
					"required":             []any{"report", "totals"},
					"additionalProperties": false,
				},
				"config": map[string]any{
					"model_profile_version": modelVersionID,
					"instructions": "你是销售统计报告作者。只能使用输入中已验证的统计数字（total、regions、" +
						"order_count）来撰写报告；不得计算、推断或编造任何金额。report 是一段中文正文，" +
						"totals 必须原样引用输入中的统计对象。",
					"max_turns":       4,
					"tool_versions":   []any{},
					"subapp_versions": []any{},
				},
			},
			map[string]any{
				"id": "end", "type": "end",
				"inputs": map[string]any{
					"summary": map[string]any{"ref": "node:stats#"},
					"report":  map[string]any{"ref": "node:explain#/report"},
					"totals":  map[string]any{"ref": "node:explain#/totals"},
				},
				"output_schema": map[string]any{
					"type": "object",
					"properties": map[string]any{
						"summary": summarySchema,
						"report":  map[string]any{"type": "string"},
						"totals":  totalsSchema,
					},
					"required":             []any{"summary", "report", "totals"},
					"additionalProperties": false,
				},
				"config": map[string]any{},
			},
		},
		"edges": []any{
			map[string]any{"source": "start", "target": "stats"},
			map[string]any{"source": "stats", "target": "explain"},
			map[string]any{"source": "explain", "target": "end"},
		},
		"policy": map[string]any{
			"max_active_seconds": 900,
			"max_child_depth":    3,
			"max_child_calls":    12,
			"budget_limit":       "1.00",
			"currency":           "USD",
		},
	}
}

// CapabilityCard is the fixed card of the sales template.
func CapabilityCard() map[string]any {
	return map[string]any{
		"purpose":          "把 CSV/XLSX 销售明细汇总为已验证统计，并生成只引用这些数字的报告正文。",
		"includes":         []any{"CSV/XLSX 销售汇总", "summary.json 与 summary.csv 附件", "独立报告正文"},
		"excludes":         []any{"外部写入", "未经验证的金额", "发布为共享版本"},
		"required_inputs":  []any{"order_id,date,region,amount 四列的 CSV 或 XLSX"},
		"expected_outputs": []any{"summary", "report", "totals"},
		"examples": []any{
			"东区 120.50 与 79.50、西区 50.00 → 总额 250.00，3 单",
			"上传等价 XLSX → 同样的 250.00 / 东区 200.00 / 西区 50.00 / 3 单",
		},
	}
}

// SeedSalesTemplate creates or updates the fixed sales template owned by
// login ADMIN and ensures the preset tool row exists. It is idempotent: the same
// inputs produce the same template/tool/application ids and no duplicate rows.
//
// It refuses to invent a passed model: modelVersionID must already be a passed,
// unrevoked stub profile created through the real T039 flow.
func (s *Service) SeedSalesTemplate(
	ctx context.Context, ownerLogin, modelVersionID string,
) (*Summary, error) {
	if s == nil || s.DB == nil {
		return nil, errors.New("the template registry has no database")
	}
	login := strings.TrimSpace(ownerLogin)
	if login == "" {
		return nil, errors.New("--owner-login is required")
	}
	modelID := strings.ToLower(strings.TrimSpace(modelVersionID))
	if !uuid.Valid(modelID) {
		return nil, errors.New("--model-version-id must be a UUID")
	}
	script, err := s.toolScript()
	if err != nil {
		return nil, err
	}
	digest := sha256.Sum256(script)
	scriptDigest := hex.EncodeToString(digest[:])
	imageDigest := "sha256:" + scriptDigest
	toolManifest := sha256Hex([]byte("preset-sales-stats:v1:" + scriptDigest))

	definition := SalesDefinition(PresetToolID, modelID)
	definitionJSON, err := json.Marshal(definition)
	if err != nil {
		return nil, fmt.Errorf("encoding the sales definition: %w", err)
	}
	cardJSON, err := json.Marshal(CapabilityCard())
	if err != nil {
		return nil, fmt.Errorf("encoding the sales capability card: %w", err)
	}

	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return nil, fmt.Errorf("starting the template seed transaction: %w", err)
	}
	defer func() { _ = tx.Rollback() }()

	var adminID string
	err = tx.QueryRowContext(ctx, `
		SELECT p.id::text
		  FROM platform.principals p
		  JOIN platform.users u ON u.principal_id = p.id
		 WHERE lower(u.login) = lower($1)
		   AND p.disabled_at IS NULL`, login).Scan(&adminID)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, fmt.Errorf("administrator login %q does not exist or is disabled", login)
	}
	if err != nil {
		return nil, fmt.Errorf("reading the seeding administrator: %w", err)
	}

	// The model must have passed the real validation flow already. The seed is
	// not allowed to write validation_state: that would be a fake validation.
	var (
		provider   string
		validation string
		revoked    bool
	)
	err = tx.QueryRowContext(ctx, `
		SELECT provider, validation_state, revoked_at IS NOT NULL
		  FROM platform.model_profile_versions
		 WHERE id = $1::uuid`, modelID).Scan(&provider, &validation, &revoked)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, errors.New("the model profile version does not exist")
	}
	if err != nil {
		return nil, fmt.Errorf("reading the model profile version: %w", err)
	}
	if !strings.EqualFold(provider, "stub") {
		return nil, fmt.Errorf("the sales template requires the documented stub provider; %q is not a dev-stub profile", provider)
	}
	if validation != "passed" {
		return nil, fmt.Errorf("the model profile version is %q, not passed; run the real register/validate flow first", validation)
	}
	if revoked {
		return nil, errors.New("the model profile version is revoked")
	}

	// The fixed preset tool row. An existing ready row is reused (a deployment
	// may have built it); a pre-existing row in any other state is refused rather
	// than silently overwritten.
	var toolState string
	err = tx.QueryRowContext(ctx, `
		SELECT state FROM platform.tool_versions WHERE id = $1::uuid`, PresetToolID).Scan(&toolState)
	switch {
	case errors.Is(err, sql.ErrNoRows):
		artifactID, artifactErr := s.insertToolSourceArtifact(ctx, tx, adminID, scriptDigest, len(script))
		if artifactErr != nil {
			return nil, artifactErr
		}
		if _, err := tx.ExecContext(ctx, `
			INSERT INTO platform.tool_versions
			       (id, owner_id, source_artifact_id, input_schema, output_schema,
			        dependency_lock, connector_versions, image_digest, risk_class, state,
			        manifest_sha256, approved_by, approved_at)
			VALUES ($1::uuid, $2::uuid, $3::uuid, $4::jsonb, $5::jsonb,
			        '{}'::jsonb, '[]'::jsonb, $6, $7, 'ready', $8, $2::uuid, now())`,
			PresetToolID, adminID, artifactID,
			`{"type":"object","properties":{"file":{"type":"string"}},"required":["file"],"additionalProperties":false}`,
			`{"type":"object","properties":{"total":{"type":"string"},"regions":{"type":"object"},"order_count":{"type":"integer"}}}`,
			imageDigest, presetToolRisk, toolManifest,
		); err != nil {
			return nil, fmt.Errorf("creating the preset tool version: %w", err)
		}
	case err != nil:
		return nil, fmt.Errorf("reading the preset tool version: %w", err)
	case toolState != "ready":
		return nil, fmt.Errorf("the fixed preset tool version exists with state %q; it must be ready", toolState)
	}

	// Reuse the source application when the template already exists, so a second
	// seed is an update, not a second source draft.
	var applicationID string
	err = tx.QueryRowContext(ctx, `
		SELECT application_id::text
		  FROM platform.application_templates WHERE id = $1`,
		SalesTemplateID).Scan(&applicationID)
	if errors.Is(err, sql.ErrNoRows) {
		applicationID = uuid.NewString()
		if _, err := tx.ExecContext(ctx, `
			INSERT INTO platform.applications (id, owner_id, name) VALUES ($1::uuid, $2::uuid, $3)`,
			applicationID, adminID, SalesTemplateName,
		); err != nil {
			return nil, fmt.Errorf("creating the template application: %w", err)
		}
		if _, err := tx.ExecContext(ctx, `
			INSERT INTO platform.application_drafts
			       (application_id, revision, execution_revision, definition, layout, capability_card)
			VALUES ($1::uuid, 1, 1, $2::jsonb, '{}'::jsonb, $3::jsonb)`,
			applicationID, string(definitionJSON), string(cardJSON),
		); err != nil {
			return nil, fmt.Errorf("creating the template source draft: %w", err)
		}
		if _, err := tx.ExecContext(ctx, `
			INSERT INTO platform.application_templates
			       (id, name, application_id, definition, capability_card)
			VALUES ($1, $2, $3::uuid, $4::jsonb, $5::jsonb)`,
			SalesTemplateID, SalesTemplateName, applicationID,
			string(definitionJSON), string(cardJSON),
		); err != nil {
			return nil, fmt.Errorf("registering the template: %w", err)
		}
	} else if err != nil {
		return nil, fmt.Errorf("reading the template: %w", err)
	} else {
		// The source draft stays at revision 1: it is the reviewed source, and an
		// update rewrites it in place rather than pretending an editor saved it.
		if _, err := tx.ExecContext(ctx, `
			UPDATE platform.application_drafts
			   SET definition = $2::jsonb, capability_card = $3::jsonb,
			       revision = 1, execution_revision = 1, updated_at = now()
			 WHERE application_id = $1::uuid`,
			applicationID, string(definitionJSON), string(cardJSON),
		); err != nil {
			return nil, fmt.Errorf("updating the template source draft: %w", err)
		}
		if _, err := tx.ExecContext(ctx, `
			UPDATE platform.application_templates
			   SET name = $2, definition = $3::jsonb, capability_card = $4::jsonb, updated_at = now()
			 WHERE id = $1`,
			SalesTemplateID, SalesTemplateName, string(definitionJSON), string(cardJSON),
		); err != nil {
			return nil, fmt.Errorf("updating the template: %w", err)
		}
	}

	if err := tx.Commit(); err != nil {
		return nil, fmt.Errorf("committing the template seed: %w", err)
	}
	return &Summary{
		TemplateID:     SalesTemplateID,
		ApplicationID:  applicationID,
		ToolVersionID:  PresetToolID,
		ModelVersionID: modelID,
	}, nil
}

// insertToolSourceArtifact records the real source bytes facts for the preset
// tool. The script itself stays in the product tree; the row records its digest
// and size, which is what the manifest and the approval refer to.
func (s *Service) insertToolSourceArtifact(
	ctx context.Context, tx *sql.Tx, ownerID, scriptDigest string, size int,
) (string, error) {
	artifactID := uuid.NewString()
	objectKey := "sealed/" + strings.ReplaceAll(uuid.NewString(), "-", "")
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO platform.artifacts
		       (id, owner_id, object_key, purpose, sha256, media_type, size_bytes, state, revision)
		VALUES ($1::uuid, $2::uuid, $3, 'tool_source', $4, 'text/x-python', $5, 'ready', 1)`,
		artifactID, ownerID, objectKey, scriptDigest, size,
	); err != nil {
		return "", fmt.Errorf("recording the preset tool source artifact: %w", err)
	}
	return artifactID, nil
}

// toolScript reads the real sales_stats.py bytes whose digest becomes the tool
// image digest.
func (s *Service) toolScript() ([]byte, error) {
	path := s.ToolScriptPath
	if strings.TrimSpace(path) == "" {
		root := strings.TrimSpace(s.ProductRoot)
		if root == "" {
			if env := strings.TrimSpace(os.Getenv("ORCHARA_PRODUCT_ROOT")); env != "" {
				root = env
			} else {
				root = "."
			}
		}
		path = filepath.Join(root, filepath.FromSlash(defaultToolScript))
	}
	payload, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("reading the preset tool script: %w", err)
	}
	if len(payload) == 0 {
		return nil, errors.New("the preset tool script is empty")
	}
	return payload, nil
}

// Instantiate copies the stored template definition and capability card into a
// new application owned by callerID and returns the draft revision the caller
// must freeze in its debug submission.
//
// The template lookup and both inserts are one transaction: a caller never gets
// an application whose draft is half the template. An unknown or malformed
// template id is ErrTemplateNotFound, the same answer as "does not exist".
func (s *Service) Instantiate(
	ctx context.Context, callerID, templateID string,
) (*Instance, error) {
	if s == nil || s.DB == nil {
		return nil, errors.New("the template registry has no database")
	}
	if !uuid.Valid(callerID) {
		return nil, errors.New("the caller is not a valid principal")
	}
	key := strings.TrimSpace(templateID)
	if key == "" {
		return nil, ErrTemplateNotFound
	}

	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return nil, fmt.Errorf("starting the instantiate transaction: %w", err)
	}
	defer func() { _ = tx.Rollback() }()

	var (
		name               string
		definitionJSON     string
		cardJSON           string
		definitionRevision int64
	)
	err = tx.QueryRowContext(ctx, `
		SELECT t.name, t.definition::text, t.capability_card::text, d.revision
		  FROM platform.application_templates t
		  JOIN platform.application_drafts d ON d.application_id = t.application_id
		 WHERE t.id = $1`, key).Scan(
		&name, &definitionJSON, &cardJSON, &definitionRevision,
	)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrTemplateNotFound
	}
	if err != nil {
		return nil, fmt.Errorf("reading the template: %w", err)
	}

	applicationID := uuid.NewString()
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO platform.applications (id, owner_id, name) VALUES ($1::uuid, $2::uuid, $3)`,
		applicationID, callerID, name,
	); err != nil {
		return nil, fmt.Errorf("creating the application from the template: %w", err)
	}
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO platform.application_drafts
		       (application_id, revision, execution_revision, definition, layout, capability_card)
		VALUES ($1::uuid, 1, 1, $2::jsonb, '{}'::jsonb, $3::jsonb)`,
		applicationID, definitionJSON, cardJSON,
	); err != nil {
		return nil, fmt.Errorf("copying the template draft: %w", err)
	}
	if err := tx.Commit(); err != nil {
		return nil, fmt.Errorf("committing the instantiate: %w", err)
	}
	return &Instance{ID: applicationID, Revision: 1, DefinitionRevision: definitionRevision}, nil
}

func sha256Hex(payload []byte) string {
	sum := sha256.Sum256(payload)
	return hex.EncodeToString(sum[:])
}
