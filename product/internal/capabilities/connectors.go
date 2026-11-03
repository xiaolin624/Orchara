package capabilities

// F06/T048: the connector capability branch.
//
// invokeCapability(kind=connector) names an administrator-registered policy
// version that the run's sealed manifest froze. The flow mirrors the model/tool
// branches:
//
//   - the policy must be present in manifest.connector_policies, approved and
//     unrevoked, and its sealed digest must match the manifest entry (a run may
//     not invoke a policy it does not carry);
//   - one operation row, one physical attempt row, one receipt artifact; a write
//     risk policy stops at awaiting_approval/approval_required (T050 writes
//     approvals) and never performs egress;
//   - the physical HTTP call is made by internal/connectors, whose guard refuses
//     private/link-local/metadata targets, DNS answers that are not permitted,
//     redirects, unapproved queries and oversized responses.
//
// The worker never sees the credential: the secret exists only inside the
// connectors package for the duration of the request, and the receipt carries
// the status, response digest and selected headers only.

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"net/url"
	"strings"

	"orchara/platform/internal/compiler"
	"orchara/platform/internal/connectors"
	"orchara/platform/internal/jsonschema"
	"orchara/platform/internal/uuid"
)

// ConnectorKind is the contract's capability_kind value for a connector call.
const ConnectorKind = "connector"

// resolveConnector applies the manifest allowlist, the sealed-digest match and
// the readiness/revocation rule, then validates the invocation params against
// the policy request schema. Nothing is dialed here.
func (s *Service) resolveConnector(ctx context.Context, run *runContext, input Input) (*capabilitySpec, *Error) {
	if s.Connectors == nil {
		return nil, capFailure(503, "connector_unavailable",
			"no connector registry is configured in this process")
	}
	manifestEntry := manifestConnectorPolicy(run.Manifest, input.CapabilityVersion)
	if manifestEntry == nil {
		return nil, capFailure(403, CodeCapabilityNotAllowed,
			"the connector policy version is not a connector policy of this run's manifest")
	}
	policy, capErr := s.Connectors.Load(ctx, input.CapabilityVersion)
	if capErr != nil {
		if capErr.Code == connectors.CodePolicyNotFound {
			return nil, capFailure(422, connectors.CodePolicyUnavailable,
				"the connector policy version does not exist")
		}
		return nil, capFailure(500, CodeInternal, "the connector policy could not be read")
	}
	if !strings.EqualFold(policy.PolicySHA256, manifestEntry.PolicySHA256) {
		return nil, capFailure(409, connectors.CodePolicyMismatch,
			"the connector policy digest does not match the manifest's frozen digest")
	}
	if policy.Revoked() {
		return nil, capFailure(403, connectors.CodePolicyRevoked,
			"the connector policy is revoked; no further call may be made")
	}
	if !policy.Approved() {
		return nil, capFailure(422, connectors.CodePolicyUnavailable,
			"the connector policy is not approved")
	}
	call, invalid := validateConnectorParams(policy, input.Params)
	if invalid != nil {
		return nil, invalid
	}
	return &capabilitySpec{
		Kind: ConnectorKind, RiskClass: policy.RiskClass, Connector: policy, ConnectorCall: call,
	}, nil
}

// manifestConnectorPolicy returns the manifest entry for a policy id, or nil.
func manifestConnectorPolicy(manifest *compiler.Manifest, versionID string) *compiler.ConnectorPolicy {
	if manifest == nil {
		return nil
	}
	for index := range manifest.ConnectorPolicies {
		if strings.EqualFold(manifest.ConnectorPolicies[index].VersionID, versionID) {
			return &manifest.ConnectorPolicies[index]
		}
	}
	return nil
}

// validateConnectorParams enforces the fixed params vocabulary {path, query,
// body}, the request schema and the body/method rule. The path prefix and query
// allowance are enforced again in the egress guard; both layers are deliberate.
func validateConnectorParams(policy *connectors.Policy, params map[string]any) (connectors.Call, *Error) {
	for key := range params {
		switch key {
		case "path", "query", "body":
		default:
			return connectors.Call{}, validationFailure("params",
				"connector params may only contain path, query and body")
		}
	}
	path, ok := params["path"].(string)
	if !ok || strings.TrimSpace(path) == "" {
		return connectors.Call{}, validationFailure("path", "path must be a non-empty string")
	}
	call := connectors.Call{Path: path}
	if rawQuery, present := params["query"]; present && rawQuery != nil {
		queryObject, ok := rawQuery.(map[string]any)
		if !ok {
			return connectors.Call{}, validationFailure("query", "query must be an object of string values")
		}
		values := url.Values{}
		for name, raw := range queryObject {
			value, ok := raw.(string)
			if !ok {
				return connectors.Call{}, validationFailure("query", "query values must be strings")
			}
			values.Set(name, value)
		}
		if !connectorQueryAllowed(policy.RequestSchema) {
			return connectors.Call{}, validationFailure("query",
				"the connector policy does not declare a query shape; a query may not be sent")
		}
		call.Query = values
	}
	if body, present := params["body"]; present && body != nil {
		switch policy.Method {
		case "POST", "PUT", "PATCH":
			call.Body = body
		default:
			return connectors.Call{}, validationFailure("body",
				"the connector policy method does not accept a request body")
		}
	}
	if len(policy.RequestSchema) > 0 {
		if problems := jsonschema.ValidateValue(policy.RequestSchema, params); len(problems) > 0 {
			return connectors.Call{}, &Error{
				Status:  422,
				Code:    CodeParamsInvalid,
				Message: "the connector params failed the policy request schema: " + problems[0].Message,
				Field:   strings.TrimPrefix(problems[0].Field, "/"),
			}
		}
	}
	return call, nil
}

// connectorQueryAllowed mirrors the policy rule the egress guard applies: a
// query member must be declared in the request schema's properties.
func connectorQueryAllowed(schema map[string]any) bool {
	properties, ok := schema["properties"].(map[string]any)
	if !ok {
		return false
	}
	_, present := properties["query"]
	return present
}

// connectorFailure maps a typed connectors error onto the capability vocabulary.
// The connector package's codes are already stable owner-facing codes.
func connectorFailure(err error) *Error {
	var typed *connectors.Error
	if errors.As(err, &typed) && typed != nil {
		return &Error{
			Status: typed.Status, Code: typed.Code, Message: typed.Message,
			Field: typed.Field, Details: typed.Details,
		}
	}
	return capFailure(500, CodeInternal, "the connector call could not be completed")
}

// beginConnectorAttempt opens the single physical attempt row. A connector call
// is never retried: an invocation is exactly one HTTP request, so attempt_no is
// always 1 and a replay of a dispatching operation is operation_in_progress.
func (s *Service) beginConnectorAttempt(ctx context.Context, run *runContext, operationID, attemptToken string) *Error {
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return capFailure(500, CodeInternal, "the connector attempt transaction could not start")
	}
	defer func() { _ = tx.Rollback() }()
	var state string
	if err := tx.QueryRowContext(ctx, `
		SELECT state FROM platform.operations WHERE id = $1::uuid FOR UPDATE`,
		operationID,
	).Scan(&state); err != nil {
		return capFailure(500, CodeInternal, "the connector operation could not be locked")
	}
	if state != "authorized" {
		return capFailure(409, CodeOperationInProgress, "the connector operation is not authorized")
	}
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO platform.operation_attempts
			(id, operation_id, attempt_no, state, attempt_token)
		VALUES ($1::uuid, $2::uuid, 1, 'dispatching', $3::uuid)`,
		uuid.NewString(), operationID, attemptToken,
	); err != nil {
		if isUniqueViolation(err) {
			return capFailure(409, CodeOperationInProgress, "the connector operation already has its attempt")
		}
		return capFailure(500, CodeInternal, "the connector attempt could not be recorded")
	}
	if _, err := tx.ExecContext(ctx, `
		UPDATE platform.operations
		   SET state = 'dispatching', dispatched_at = now(), updated_at = now()
		 WHERE id = $1::uuid AND state = 'authorized'`, operationID,
	); err != nil {
		return capFailure(500, CodeInternal, "the connector operation could not be dispatched")
	}
	if err := tx.Commit(); err != nil {
		return capFailure(500, CodeInternal, "the connector attempt could not be committed")
	}
	return nil
}

// dispatchConnector performs the one physical call and records the receipt. The
// operation is failed with the typed connector code when the guard or the
// endpoint refuses; a success seals the validated response.
func (s *Service) dispatchConnector(
	ctx context.Context,
	run *runContext,
	input Input,
	spec *capabilitySpec,
	operationID string,
) (*Result, error) {
	if s.Connectors == nil {
		_ = s.failOperation(ctx, operationID, "connector_unavailable")
		return nil, capFailure(503, "connector_unavailable", "no connector registry is configured")
	}
	if capErr := s.beginConnectorAttempt(ctx, run, operationID, input.AttemptToken); capErr != nil {
		return nil, capErr
	}
	response, err := s.Connectors.Execute(ctx, spec.Connector, spec.ConnectorCall)
	if err != nil {
		mapped := connectorFailure(err)
		if failErr := s.failAttemptAndOperation(ctx, operationID, 1, mapped.Code); failErr != nil {
			return nil, capFailure(500, CodeInternal, "the failed connector attempt could not be recorded")
		}
		return nil, mapped
	}
	payload, receipt, err := connectorReceipt(spec, response)
	if err != nil {
		_ = s.failAttemptAndOperation(ctx, operationID, 1, CodeInternal)
		return nil, capFailure(500, CodeInternal, "the connector response could not be projected")
	}
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return nil, capFailure(500, CodeInternal, "the connector result transaction could not start")
	}
	defer func() { _ = tx.Rollback() }()
	receiptArtifactID, capErr := s.sealTx(ctx, tx, run, input.AttemptToken,
		"receipt", "capability_receipt", receipt)
	if capErr != nil {
		_ = tx.Rollback()
		_ = s.failAttemptAndOperation(ctx, operationID, 1, CodeInternal)
		return nil, capErr
	}
	if _, err := tx.ExecContext(ctx, `
		UPDATE platform.operation_attempts
		   SET state = 'succeeded', result_artifact_id = $3::uuid, finished_at = now()
		 WHERE operation_id = $1::uuid AND attempt_no = $2 AND state = 'dispatching'`,
		operationID, 1, receiptArtifactID,
	); err != nil {
		return nil, capFailure(500, CodeInternal, "the connector attempt could not be closed")
	}
	if _, err := tx.ExecContext(ctx, `
		UPDATE platform.operations
		   SET state = 'succeeded', receipt_artifact_id = $2::uuid,
		       last_error_code = NULL, updated_at = now()
		 WHERE id = $1::uuid AND state = 'dispatching'`, operationID, receiptArtifactID,
	); err != nil {
		return nil, capFailure(500, CodeInternal, "the connector operation could not be succeeded")
	}
	if err := tx.Commit(); err != nil {
		return nil, capFailure(500, CodeInternal, "the connector result could not be committed")
	}
	artifact := receiptArtifactID
	return &Result{
		OperationID:       operationID,
		State:             "succeeded",
		ReceiptArtifactID: &artifact,
		Data:              payload,
	}, nil
}

// connectorReceipt builds (worker-visible data, sealed receipt). The receipt
// never contains the request body, the query values or the credential: status,
// response digest, byte count and the allow-listed headers are the audit facts.
func connectorReceipt(spec *capabilitySpec, response *connectors.Response) (map[string]any, []byte, error) {
	payload := map[string]any{
		"status":                      response.Status,
		"headers":                     response.Header,
		"connector_policy_version_id": spec.Connector.ID,
		"policy_sha256":               spec.Connector.PolicySHA256,
		"stub":                        false,
	}
	receipt := map[string]any{
		"capability_kind":             ConnectorKind,
		"connector_policy_version_id": spec.Connector.ID,
		"policy_sha256":               spec.Connector.PolicySHA256,
		"method":                      spec.Connector.Method,
		"origin":                      spec.Connector.Origin,
		"path_prefix":                 spec.Connector.PathPrefix,
		"status":                      response.Status,
		"response_sha256":             sha256Hex(response.Body),
		"response_bytes":              len(response.Body),
		"headers":                     response.Header,
		"stub":                        false,
	}
	var decoded any
	decoder := json.NewDecoder(strings.NewReader(string(response.Body)))
	decoder.UseNumber()
	if err := decoder.Decode(&decoded); err == nil {
		payload["body"] = decoded
		receipt["body"] = decoded
	}
	content, err := json.Marshal(receipt)
	if err != nil {
		return nil, nil, err
	}
	return payload, content, nil
}

// sha256Hex is a tiny helper so the receipt has no dependency on the artifact
// writer's internals.
func sha256Hex(content []byte) string {
	sum := sha256.Sum256(content)
	return hex.EncodeToString(sum[:])
}

// isConnectorAwaitingApproval reports whether a prepared operation must stop for
// approval. T050 owns the approval row and the later resume; here the write risk
// policy is recorded and answered with awaiting_approval/approval_required.
func (s *Service) connectorAwaitingApproval(spec *capabilitySpec) bool {
	return spec != nil && spec.Kind == ConnectorKind && spec.RiskClass == "write"
}

// connectorApprovalResult is the contract-shaped answer for a write policy.
func connectorApprovalResult(operationID string, spec *capabilitySpec) *Result {
	return &Result{
		OperationID: operationID,
		State:       "awaiting_approval",
		Data: map[string]any{
			"error_code":                  "approval_required",
			"connector_policy_version_id": spec.Connector.ID,
			"risk_class":                  spec.RiskClass,
			"status":                      "awaiting_approval",
		},
	}
}
