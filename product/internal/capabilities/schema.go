package capabilities

// F06/T048 moved the restricted JSON-schema subset validator into
// internal/jsonschema so the connector egress guard validates a response
// schema without importing this package (the connector service is a dependency
// of capabilities, so the reverse edge would be a cycle). These aliases keep
// the T041/T042/T043 call sites and tests unchanged.

import "orchara/platform/internal/jsonschema"

// SchemaProblem is one located JSON-schema violation.
type SchemaProblem = jsonschema.SchemaProblem

// ValidateValue checks value against the restricted schema subset F06 supports.
func ValidateValue(schema map[string]any, value any) []SchemaProblem {
	return jsonschema.ValidateValue(schema, value)
}
