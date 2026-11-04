// Package connectors is the F06/T048 fixed-HTTP-connector boundary: the
// administrator-registered, versioned egress policy and the guarded real HTTP
// client that is the only way a run may reach an external endpoint.
//
// Rules fixed by IMPLEMENTATION-DETAILS §10 and the T048 brief:
//
//   - A policy is registered by an administrator, never by a worker and never by
//     a script. It fixes exactly one origin (scheme://host[:port]), one method,
//     one path prefix, the request/response schemas, the risk class, whether
//     private addresses are allowed, and the deployer credential reference plus
//     the single header that credential may travel in.
//   - The immutable policy body is sealed as canonical RFC 8785 JCS bytes; its
//     SHA-256 is the version identity. The row is the queryable registry, never
//     the policy body: there is no update operation, only new versions and
//     revocation (`revoked_at`, never DELETE).
//   - The caller supplies a path under the registered prefix and (only when the
//     request schema declares it) a query. The URL is always built from the
//     registered origin; userinfo, absolute URLs, dot segments, fragments and
//     unapproved queries are refused before any DNS lookup.
//   - Every resolved address is classified. Loopback/private/link-local/
//     multicast/unspecified are refused unless allow_private; the metadata
//     address (169.254.169.254) and the other always-forbidden classes are
//     refused even then. The validated address is pinned for the connection and
//     Host/SNI are preserved, so a second DNS answer cannot rebind the dial.
//   - Redirects are refused, not followed. Request/response bytes and time are
//     capped, no environment proxy is used, and the secret is read inside Go,
//     sent only in its configured header, and never logged, sealed or returned.
package connectors

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"net/url"
	"regexp"
	"strings"
	"time"

	"orchara/platform/internal/config"
	"orchara/platform/internal/jcs"
	"orchara/platform/internal/jsonschema"
	"orchara/platform/internal/objectstore"
	"orchara/platform/internal/uuid"
)

// Stable error codes. The API maps them to statuses and the owner ledger shows
// them; they never carry a provider body, URL or credential.
const (
	CodePolicyExists      = "connector_policy_exists"
	CodePolicyNotFound    = "connector_policy_not_found"
	CodePolicyRevoked     = "connector_policy_revoked"
	CodePolicyUnavailable = "connector_policy_unavailable"
	CodePolicyMismatch    = "connector_policy_mismatch"
	CodeRequestInvalid    = "connector_request_invalid"
	CodeTargetForbidden   = "connector_target_forbidden"
	CodeRedirectRefused   = "connector_redirect_refused"
	CodeSecretUnavailable = "connector_secret_unavailable"
	CodeHTTPError         = "connector_http_error"
	CodeResponseInvalid   = "connector_response_invalid"
	CodeResponseTooLarge  = "connector_response_too_large"
	CodeInternal          = "internal_error"
)

// Always-forbidden header names: the secret header may not overwrite framing or
// routing headers.
var forbiddenSecretHeaders = map[string]bool{
	"host": true, "content-length": true, "transfer-encoding": true,
	"connection": true, "te": true, "trailer": true, "upgrade": true,
	"expect": true, "proxy-authorization": true, "proxy-authenticate": true,
}

var (
	headerNamePattern = regexp.MustCompile(`^[A-Za-z0-9-]+$`)
	hostLabelPattern  = regexp.MustCompile(`^[a-z0-9]([a-z0-9-]*[a-z0-9])?$`)
)

var allowedMethods = map[string]bool{
	"GET": true, "POST": true, "PUT": true, "PATCH": true, "DELETE": true,
}

// Problem is one located policy validation problem. Field is the administrator
// body field (or a nested path) and never echoes a credential value.
type Problem struct {
	Field   string `json:"field"`
	Message string `json:"message"`
}

// Input is the administrator registration body. Schemas are JSON objects using
// the restricted subset the platform understands; the secret reference is a
// deployer credential reference, never a value.
type Input struct {
	Origin         string         `json:"origin"`
	Method         string         `json:"method"`
	PathPrefix     string         `json:"path_prefix"`
	RequestSchema  map[string]any `json:"request_schema"`
	ResponseSchema map[string]any `json:"response_schema"`
	SecretRef      string         `json:"secret_ref"`
	SecretHeader   string         `json:"secret_header"`
	RiskClass      string         `json:"risk_class"`
	AllowPrivate   bool           `json:"allow_private"`
}

// Policy is one registry row plus its sealed identity. It never carries the
// credential value; SecretRef is the deployer reference only.
type Policy struct {
	ID               string         `json:"id"`
	Version          int            `json:"version"`
	Origin           string         `json:"origin"`
	Method           string         `json:"method"`
	PathPrefix       string         `json:"path_prefix"`
	RequestSchema    map[string]any `json:"request_schema"`
	ResponseSchema   map[string]any `json:"response_schema"`
	SecretRef        string         `json:"secret_ref"`
	SecretHeader     string         `json:"secret_header"`
	RiskClass        string         `json:"risk_class"`
	AllowPrivate     bool           `json:"allow_private"`
	PolicyArtifactID string         `json:"policy_artifact_id"`
	PolicySHA256     string         `json:"policy_sha256"`
	CreatedBy        string         `json:"created_by"`
	ApprovedAt       *time.Time     `json:"approved_at"`
	RevokedAt        *time.Time     `json:"revoked_at"`
	CreatedAt        time.Time      `json:"created_at"`
}

// Approved reports whether an administrator approved the policy (registration
// is the approval step in this batch).
func (p *Policy) Approved() bool { return p.ApprovedAt != nil }

// Revoked reports whether the policy was revoked; revocation is permanent.
func (p *Policy) Revoked() bool { return p.RevokedAt != nil }

// Error is the typed connector failure. Status is the HTTP mapping, Code the
// stable machine code and Field the located administrator/invocation field.
type Error struct {
	Status  int
	Code    string
	Message string
	Field   string
	Details map[string]any
}

func (e *Error) Error() string {
	if e.Field != "" {
		return e.Code + ": " + e.Field + ": " + e.Message
	}
	return e.Code + ": " + e.Message
}

// AsError extracts a *Error.
func AsError(err error) (*Error, bool) {
	var connectorError *Error
	if errors.As(err, &connectorError) && connectorError != nil {
		return connectorError, true
	}
	return nil, false
}

func code(err error) string {
	if typed, ok := AsError(err); ok {
		return typed.Code
	}
	return ""
}

func failure(status int, code, message string) *Error {
	return &Error{Status: status, Code: code, Message: message}
}

func validationProblem(problem Problem) *Error {
	return &Error{Status: 422, Code: CodeRequestInvalid, Message: problem.Message, Field: problem.Field}
}

// normalizePolicyInput validates and canonicalises one registration body. It is
// pure (no database, no network) so the whole rejection matrix is unit-tested.
func normalizePolicyInput(input Input) (Input, []Problem) {
	problems := make([]Problem, 0)
	origin, err := normalizeOrigin(input.Origin)
	if err != nil {
		problems = append(problems, Problem{Field: "origin", Message: err.Error()})
	}
	input.Origin = origin

	method := strings.ToUpper(strings.TrimSpace(input.Method))
	if !allowedMethods[method] {
		problems = append(problems, Problem{
			Field:   "method",
			Message: "method must be one of GET, POST, PUT, PATCH or DELETE",
		})
	}
	input.Method = method

	prefix, err := normalizePathPrefix(input.PathPrefix)
	if err != nil {
		problems = append(problems, Problem{Field: "path_prefix", Message: err.Error()})
	}
	input.PathPrefix = prefix

	if _, err := config.ParseSecretRef(input.SecretRef); err != nil {
		problems = append(problems, Problem{
			Field:   "secret_ref",
			Message: "secret_ref must be a deployer credential reference (env:NAME or file:NAME), never a value",
		})
	}
	input.SecretRef = strings.TrimSpace(input.SecretRef)

	header := strings.TrimSpace(input.SecretHeader)
	switch {
	case header == "":
		problems = append(problems, Problem{Field: "secret_header", Message: "secret_header is required"})
	case !headerNamePattern.MatchString(header):
		problems = append(problems, Problem{
			Field:   "secret_header",
			Message: "secret_header must be an HTTP header token (letters, digits, hyphen)",
		})
	case forbiddenSecretHeaders[strings.ToLower(header)]:
		problems = append(problems, Problem{
			Field:   "secret_header",
			Message: "secret_header may not overwrite a framing or routing header",
		})
	}
	input.SecretHeader = header

	risk := strings.ToLower(strings.TrimSpace(input.RiskClass))
	if risk != "read" && risk != "write" {
		problems = append(problems, Problem{Field: "risk_class", Message: "risk_class must be read or write"})
	}
	input.RiskClass = risk

	input.RequestSchema = normalizeSchema(input.RequestSchema)
	input.ResponseSchema = normalizeSchema(input.ResponseSchema)
	problems = append(problems, validateSchema("request_schema", input.RequestSchema, true)...)
	problems = append(problems, validateSchema("response_schema", input.ResponseSchema, true)...)
	return input, problems
}

// normalizeOrigin accepts exactly scheme://host[:port]. Anything with a path,
// query, fragment, userinfo or an invalid port is refused: the caller may never
// choose where the request goes.
func normalizeOrigin(raw string) (string, error) {
	trimmed := strings.TrimSpace(raw)
	if trimmed == "" {
		return "", errors.New("origin is required (scheme://host[:port])")
	}
	parsed, err := url.Parse(trimmed)
	if err != nil {
		return "", errors.New("origin is not a valid URL")
	}
	scheme := strings.ToLower(parsed.Scheme)
	if scheme != "http" && scheme != "https" {
		return "", errors.New("origin scheme must be http or https")
	}
	if parsed.User != nil {
		return "", errors.New("origin may not contain userinfo")
	}
	if parsed.RawQuery != "" || parsed.ForceQuery || parsed.Fragment != "" || parsed.RawFragment != "" {
		return "", errors.New("origin may not contain a query or fragment")
	}
	if parsed.Path != "" && parsed.Path != "/" {
		return "", errors.New("origin may not contain a path; register a path_prefix instead")
	}
	host := strings.ToLower(parsed.Hostname())
	if host == "" {
		return "", errors.New("origin host is required")
	}
	if strings.ContainsAny(host, " /\\?#@\t\r\n") {
		return "", errors.New("origin host contains a forbidden character")
	}
	if !validHost(host) {
		return "", errors.New("origin host is not a DNS name or an IP address")
	}
	port := parsed.Port()
	if port != "" {
		number := 0
		if _, err := fmt.Sscanf(port, "%d", &number); err != nil || number < 1 || number > 65535 {
			return "", errors.New("origin port must be between 1 and 65535")
		}
	}
	result := scheme + "://" + host
	if port != "" {
		result += ":" + port
	}
	return result, nil
}

func validHost(host string) bool {
	if strings.HasPrefix(host, "[") && strings.HasSuffix(host, "]") {
		host = strings.TrimPrefix(strings.TrimSuffix(host, "]"), "[")
	}
	if strings.Contains(host, ":") {
		// IPv6 literal: require it to parse as an address.
		return strings.Count(host, ":") >= 2 && !strings.ContainsAny(host, " /?#@")
	}
	labels := strings.Split(host, ".")
	for _, label := range labels {
		if label == "" || len(label) > 63 || !hostLabelPattern.MatchString(label) {
			return false
		}
	}
	return true
}

// normalizePathPrefix makes the prefix a single canonical path namespace: it
// starts with "/", ends without "/" (except root) and contains no dot segments,
// escapes, backslashes, query or fragment.
func normalizePathPrefix(raw string) (string, error) {
	prefix := strings.TrimSpace(raw)
	if prefix == "" {
		return "", errors.New("path_prefix is required")
	}
	if !strings.HasPrefix(prefix, "/") {
		return "", errors.New("path_prefix must start with /")
	}
	if strings.HasPrefix(prefix, "//") {
		return "", errors.New("path_prefix may not start with //")
	}
	if strings.ContainsAny(prefix, "\\?#\t\r\n") {
		return "", errors.New("path_prefix may not contain a backslash, query, fragment or control character")
	}
	if strings.Contains(prefix, "%") {
		return "", errors.New("path_prefix may not contain percent escapes")
	}
	if containsDotSegment(prefix) {
		return "", errors.New("path_prefix may not contain dot segments")
	}
	for len(prefix) > 1 && strings.HasSuffix(prefix, "/") {
		prefix = strings.TrimSuffix(prefix, "/")
	}
	if len(prefix) > 512 {
		return "", errors.New("path_prefix is too long")
	}
	return prefix, nil
}

func containsDotSegment(path string) bool {
	for _, segment := range strings.Split(path, "/") {
		if segment == ".." || segment == "." {
			return true
		}
	}
	return false
}

// normalizeSchema turns nil into the empty (unconstrained) schema and keeps a
// JSON-decoded object as-is.
func normalizeSchema(schema map[string]any) map[string]any {
	if schema == nil {
		return map[string]any{}
	}
	return schema
}

// validateSchema restricts the supported schema subset: object payloads only,
// a known top-level vocabulary, recursively valid properties/items. An unknown
// keyword is refused rather than silently ignored, exactly like the capability
// response validator.
func validateSchema(field string, schema map[string]any, root bool) []Problem {
	problems := make([]Problem, 0)
	if len(schema) == 0 {
		return problems
	}
	if encoded, err := json.Marshal(schema); err != nil {
		return []Problem{{Field: field, Message: "schema is not valid JSON"}}
	} else if len(encoded) > 64*1024 {
		return []Problem{{Field: field, Message: "schema is too large"}}
	}
	allowed := map[string]bool{
		"type": true, "properties": true, "required": true,
		"additionalProperties": true, "items": true, "enum": true,
		"description": true, "title": true, "minimum": true, "maximum": true,
		"minLength": true, "maxLength": true,
	}
	for key := range schema {
		if !allowed[key] {
			problems = append(problems, Problem{
				Field:   field + "." + key,
				Message: "schema keyword is not supported",
			})
		}
	}
	if wanted, present := schema["type"]; present {
		types, ok := wanted.([]any)
		if !ok {
			if single, isString := wanted.(string); isString {
				types = []any{single}
			}
		}
		if len(types) == 0 {
			problems = append(problems, Problem{Field: field + ".type", Message: "schema type must be a string or string array"})
		}
		for _, raw := range types {
			value, ok := raw.(string)
			if !ok {
				problems = append(problems, Problem{Field: field + ".type", Message: "schema type must be a string"})
				break
			}
			if root && value != "object" && value != "null" {
				problems = append(problems, Problem{
					Field:   field + ".type",
					Message: "a connector request/response payload must be a JSON object",
				})
				break
			}
			if !root && !supportedJSONType(value) {
				problems = append(problems, Problem{
					Field:   field + ".type",
					Message: "schema type is not one of the supported JSON types",
				})
				break
			}
		}
	}
	if properties, present := schema["properties"]; present {
		object, ok := properties.(map[string]any)
		if !ok {
			problems = append(problems, Problem{Field: field + ".properties", Message: "schema properties must be an object"})
		} else {
			for name, raw := range object {
				nested, ok := raw.(map[string]any)
				if !ok {
					problems = append(problems, Problem{
						Field:   field + ".properties." + name,
						Message: "each property schema must be an object",
					})
					continue
				}
				problems = append(problems, validateSchema(field+".properties."+name, nested, false)...)
			}
		}
	}
	if required, present := schema["required"]; present {
		list, ok := required.([]any)
		if !ok {
			problems = append(problems, Problem{Field: field + ".required", Message: "schema required must be an array"})
		} else {
			for _, item := range list {
				if _, ok := item.(string); !ok {
					problems = append(problems, Problem{Field: field + ".required", Message: "schema required entries must be strings"})
					break
				}
			}
		}
	}
	if additional, present := schema["additionalProperties"]; present {
		switch typed := additional.(type) {
		case bool:
		case map[string]any:
			problems = append(problems, validateSchema(field+".additionalProperties", typed, false)...)
		default:
			problems = append(problems, Problem{
				Field:   field + ".additionalProperties",
				Message: "schema additionalProperties must be a boolean or an object",
			})
		}
	}
	if items, present := schema["items"]; present {
		nested, ok := items.(map[string]any)
		if !ok {
			problems = append(problems, Problem{Field: field + ".items", Message: "schema items must be an object"})
		} else {
			problems = append(problems, validateSchema(field+".items", nested, false)...)
		}
	}
	if enum, present := schema["enum"]; present {
		if _, ok := enum.([]any); !ok {
			problems = append(problems, Problem{Field: field + ".enum", Message: "schema enum must be an array"})
		}
	}
	problems = append(problems, validateSchemaBounds(field, schema)...)
	return problems
}

// validateSchemaBounds refuses a declared bound the runtime validator cannot
// evaluate. minimum/maximum must be JSON numbers with minimum<=maximum;
// minLength/maxLength must be non-negative integers with minLength<=maxLength.
// Without this an unusable policy could be registered and then either silently
// widen the call or refuse every invocation at runtime.
func validateSchemaBounds(field string, schema map[string]any) []Problem {
	problems := make([]Problem, 0)
	for _, keyword := range []string{"minimum", "maximum"} {
		raw, present := schema[keyword]
		if !present {
			continue
		}
		if !jsonschema.IsJSONNumber(raw) {
			problems = append(problems, Problem{
				Field:   field + "." + keyword,
				Message: "schema " + keyword + " must be a number",
			})
		}
	}
	if minimum, hasMinimum := schema["minimum"]; hasMinimum {
		if maximum, hasMaximum := schema["maximum"]; hasMaximum {
			if comparison, ok := jsonschema.CompareNumbers(minimum, maximum); ok && comparison > 0 {
				problems = append(problems, Problem{
					Field:   field + ".minimum",
					Message: "schema minimum must not exceed maximum",
				})
			}
		}
	}
	for _, keyword := range []string{"minLength", "maxLength"} {
		raw, present := schema[keyword]
		if !present {
			continue
		}
		if _, ok := jsonschema.NonNegativeInteger(raw); !ok {
			problems = append(problems, Problem{
				Field:   field + "." + keyword,
				Message: "schema " + keyword + " must be a non-negative integer",
			})
		}
	}
	if minimum, hasMinimum := schema["minLength"]; hasMinimum {
		if maximum, hasMaximum := schema["maxLength"]; hasMaximum {
			if minimumValue, ok := jsonschema.NonNegativeInteger(minimum); ok {
				if maximumValue, ok := jsonschema.NonNegativeInteger(maximum); ok &&
					minimumValue > maximumValue {
					problems = append(problems, Problem{
						Field:   field + ".minLength",
						Message: "schema minLength must not exceed maxLength",
					})
				}
			}
		}
	}
	return problems
}

// policyBodyBytes is the sealed immutable body. It excludes the row identity,
// artifact id, timestamps and the registry version: the hash is the policy
// identity, so re-registering an identical policy is detected as a duplicate.
func policyBodyBytes(input Input) ([]byte, error) {
	body := map[string]any{
		"origin":          input.Origin,
		"method":          input.Method,
		"path_prefix":     input.PathPrefix,
		"request_schema":  input.RequestSchema,
		"response_schema": input.ResponseSchema,
		"secret_ref":      input.SecretRef,
		"secret_header":   input.SecretHeader,
		"risk_class":      input.RiskClass,
		"allow_private":   input.AllowPrivate,
	}
	encoded, err := json.Marshal(body)
	if err != nil {
		return nil, err
	}
	return jcs.CanonicalizeBytes(encoded)
}

func policyFromInput(input Input) *Policy {
	return &Policy{
		Origin: input.Origin, Method: input.Method, PathPrefix: input.PathPrefix,
		RequestSchema: input.RequestSchema, ResponseSchema: input.ResponseSchema,
		SecretRef: input.SecretRef, SecretHeader: input.SecretHeader,
		RiskClass: input.RiskClass, AllowPrivate: input.AllowPrivate,
	}
}

// queryAllowed reports whether the request schema explicitly declares a query
// member. Without it a query is refused: an administrator opts in, the worker
// never does.
func queryAllowed(schema map[string]any) bool {
	properties, ok := schema["properties"].(map[string]any)
	if !ok {
		return false
	}
	_, present := properties["query"]
	return present
}

// Service owns the connector policy registry and the guarded HTTP client.
type Service struct {
	DB     *sql.DB
	Store  *objectstore.Store
	Lookup config.Lookup
	// SecretDir is the deployer file-reference root (`file:NAME`). Empty means
	// file references are unavailable and fail closed.
	SecretDir string
	// Egress is the HTTP seamer; nil uses the production guard.
	Egress *Egress
}

// New builds the connector service.
func New(db *sql.DB, store *objectstore.Store, lookup config.Lookup, secretDir string) *Service {
	return &Service{DB: db, Store: store, Lookup: lookup, SecretDir: secretDir}
}

func (s *Service) egress() *Egress {
	if s.Egress != nil {
		return s.Egress
	}
	return &Egress{Lookup: s.Lookup, SecretDir: s.SecretDir}
}

func (s *Service) connectorError(err error) *Error {
	var typed *Error
	if errors.As(err, &typed) && typed != nil {
		return typed
	}
	return failure(500, CodeInternal, "the connector call could not be completed")
}

// Register seals one immutable policy and writes the queryable registry row.
// A duplicate body hash is 409 connector_policy_exists (idempotent: the caller
// learns the existing policy id instead of creating a second version).
func (s *Service) Register(ctx context.Context, adminID string, input Input) (*Policy, *Error) {
	if !uuid.Valid(strings.TrimSpace(adminID)) {
		return nil, failure(403, "forbidden", "an authenticated administrator is required")
	}
	normalized, problems := normalizePolicyInput(input)
	if len(problems) > 0 {
		return nil, &Error{
			Status: 422, Code: "validation_failed",
			Message: problems[0].Message, Field: problems[0].Field,
		}
	}
	body, err := policyBodyBytes(normalized)
	if err != nil {
		return nil, failure(500, CodeInternal, "the policy body could not be canonicalised")
	}
	digest := jcs.SHA256Hex(body)

	if existing, err := s.byHash(ctx, digest); err != nil {
		return nil, failure(500, CodeInternal, "the connector policy registry could not be read")
	} else if existing != nil {
		return nil, &Error{
			Status: 409, Code: CodePolicyExists,
			Message: "an identical connector policy is already registered",
			Details: map[string]any{"policy_id": existing.ID, "policy_sha256": existing.PolicySHA256},
		}
	}
	if s.Store == nil {
		return nil, failure(503, "object_store_unavailable", "the object store is not configured")
	}
	object, err := s.Store.CreateSealed(strings.NewReader(string(body)), objectstore.MaxObjectBytes)
	if err != nil {
		return nil, failure(500, CodeInternal, "the connector policy artifact could not be sealed")
	}
	policyID := uuid.NewString()
	artifactID := uuid.NewString()
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return nil, failure(500, CodeInternal, "the connector policy transaction could not start")
	}
	defer func() { _ = tx.Rollback() }()
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO platform.artifacts
			(id, owner_id, object_key, purpose, sha256, media_type, size_bytes, state, revision)
		VALUES ($1::uuid, $2::uuid, $3, 'connector_policy', $4, 'application/json', $5, 'ready', 1)`,
		artifactID, adminID, object.Key, object.SHA256, object.Size,
	); err != nil {
		return nil, failure(500, CodeInternal, "the connector policy artifact row could not be written")
	}
	var version int
	if err := tx.QueryRowContext(ctx, `
		SELECT COALESCE(MAX(version), 0) + 1
		  FROM platform.connector_policies
		 WHERE origin = $1 AND method = $2 AND path_prefix = $3`,
		normalized.Origin, normalized.Method, normalized.PathPrefix,
	).Scan(&version); err != nil {
		return nil, failure(500, CodeInternal, "the connector policy version could not be computed")
	}
	requestSchema, err := json.Marshal(normalized.RequestSchema)
	if err != nil {
		return nil, failure(500, CodeInternal, "the request schema could not be stored")
	}
	responseSchema, err := json.Marshal(normalized.ResponseSchema)
	if err != nil {
		return nil, failure(500, CodeInternal, "the response schema could not be stored")
	}
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO platform.connector_policies
			(id, version, origin, method, path_prefix, request_schema, response_schema,
			 secret_ref, secret_header, risk_class, allow_private,
			 policy_artifact_id, policy_sha256, created_by, approved_at)
		VALUES ($1::uuid, $2, $3, $4, $5, $6::jsonb, $7::jsonb, $8, $9, $10, $11,
		        $12::uuid, $13, $14::uuid, now())`,
		policyID, version, normalized.Origin, normalized.Method, normalized.PathPrefix,
		string(requestSchema), string(responseSchema), normalized.SecretRef,
		normalized.SecretHeader, normalized.RiskClass, normalized.AllowPrivate,
		artifactID, digest, adminID,
	); err != nil {
		if isUniqueViolation(err) {
			return nil, &Error{
				Status: 409, Code: CodePolicyExists,
				Message: "an identical connector policy is already registered",
				Details: map[string]any{"policy_sha256": digest},
			}
		}
		return nil, failure(500, CodeInternal, "the connector policy could not be recorded")
	}
	if err := tx.Commit(); err != nil {
		return nil, failure(500, CodeInternal, "the connector policy could not be committed")
	}
	return s.Load(ctx, policyID)
}

// byHash returns the row with this sealed body hash, or nil.
func (s *Service) byHash(ctx context.Context, digest string) (*Policy, error) {
	return s.scanOne(ctx, `
		SELECT id::text, version, origin, method, path_prefix, request_schema,
		       response_schema, secret_ref, secret_header, risk_class, allow_private,
		       policy_artifact_id::text, policy_sha256, created_by::text,
		       approved_at, revoked_at, created_at
		  FROM platform.connector_policies WHERE policy_sha256 = $1`, digest)
}

// Load returns one policy row. A missing row is connector_policy_not_found.
func (s *Service) Load(ctx context.Context, policyID string) (*Policy, *Error) {
	if !uuid.Valid(strings.TrimSpace(policyID)) {
		return nil, failure(404, CodePolicyNotFound, "that connector policy does not exist")
	}
	policy, err := s.scanOne(ctx, `
		SELECT id::text, version, origin, method, path_prefix, request_schema,
		       response_schema, secret_ref, secret_header, risk_class, allow_private,
		       policy_artifact_id::text, policy_sha256, created_by::text,
		       approved_at, revoked_at, created_at
		  FROM platform.connector_policies WHERE id = $1::uuid`, strings.TrimSpace(policyID))
	if err != nil {
		return nil, failure(500, CodeInternal, "the connector policy could not be read")
	}
	if policy == nil {
		return nil, failure(404, CodePolicyNotFound, "that connector policy does not exist")
	}
	return policy, nil
}

// List returns the queryable registry, newest first. It is an administrator
// read; the HTTP layer enforces the role.
func (s *Service) List(ctx context.Context) ([]Policy, error) {
	rows, err := s.DB.QueryContext(ctx, `
		SELECT id::text, version, origin, method, path_prefix, request_schema,
		       response_schema, secret_ref, secret_header, risk_class, allow_private,
		       policy_artifact_id::text, policy_sha256, created_by::text,
		       approved_at, revoked_at, created_at
		  FROM platform.connector_policies
		 ORDER BY created_at DESC, id`)
	if err != nil {
		return nil, err
	}
	defer func() { _ = rows.Close() }()
	policies := make([]Policy, 0)
	for rows.Next() {
		policy, err := scanPolicy(rows)
		if err != nil {
			return nil, err
		}
		policies = append(policies, *policy)
	}
	return policies, rows.Err()
}

// Revoke sets revoked_at once. Revoking an already-revoked policy is
// connector_policy_revoked, not a silent second write.
func (s *Service) Revoke(ctx context.Context, policyID string) (*Policy, *Error) {
	if !uuid.Valid(strings.TrimSpace(policyID)) {
		return nil, failure(404, CodePolicyNotFound, "that connector policy does not exist")
	}
	result, err := s.DB.ExecContext(ctx, `
		UPDATE platform.connector_policies
		   SET revoked_at = now()
		 WHERE id = $1::uuid AND revoked_at IS NULL`, strings.TrimSpace(policyID))
	if err != nil {
		return nil, failure(500, CodeInternal, "the connector policy could not be revoked")
	}
	affected, _ := result.RowsAffected()
	if affected == 0 {
		policy, loadErr := s.Load(ctx, policyID)
		if loadErr != nil {
			return nil, loadErr
		}
		if policy.RevokedAt != nil {
			return nil, failure(409, CodePolicyRevoked, "the connector policy is already revoked")
		}
		return nil, failure(404, CodePolicyNotFound, "that connector policy does not exist")
	}
	return s.Load(ctx, policyID)
}

type rowScanner interface {
	Scan(dest ...any) error
}

func scanPolicy(row rowScanner) (*Policy, error) {
	policy := &Policy{}
	var requestRaw, responseRaw []byte
	var approvedAt, revokedAt sql.NullTime
	if err := row.Scan(
		&policy.ID, &policy.Version, &policy.Origin, &policy.Method, &policy.PathPrefix,
		&requestRaw, &responseRaw, &policy.SecretRef, &policy.SecretHeader,
		&policy.RiskClass, &policy.AllowPrivate, &policy.PolicyArtifactID,
		&policy.PolicySHA256, &policy.CreatedBy, &approvedAt, &revokedAt, &policy.CreatedAt,
	); err != nil {
		return nil, err
	}
	if err := json.Unmarshal(requestRaw, &policy.RequestSchema); err != nil {
		return nil, err
	}
	if err := json.Unmarshal(responseRaw, &policy.ResponseSchema); err != nil {
		return nil, err
	}
	if approvedAt.Valid {
		value := approvedAt.Time
		policy.ApprovedAt = &value
	}
	if revokedAt.Valid {
		value := revokedAt.Time
		policy.RevokedAt = &value
	}
	return policy, nil
}

func (s *Service) scanOne(ctx context.Context, query string, args ...any) (*Policy, error) {
	row := s.DB.QueryRowContext(ctx, query, args...)
	policy, err := scanPolicy(row)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return policy, nil
}

func isUniqueViolation(err error) bool {
	return err != nil && strings.Contains(err.Error(), "SQLSTATE 23505")
}

func supportedJSONType(value string) bool {
	switch value {
	case "object", "array", "string", "number", "integer", "boolean", "null":
		return true
	}
	return false
}
