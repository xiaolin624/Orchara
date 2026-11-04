package jsonschema

import (
	"encoding/json"
	"testing"
)

func objectSchema(properties map[string]any) map[string]any {
	return map[string]any{"type": "object", "properties": properties}
}

func problemsFor(schema map[string]any, value any) []SchemaProblem {
	return ValidateValue(schema, value)
}

func mustReject(t *testing.T, schema map[string]any, value any, field string) {
	t.Helper()
	problems := problemsFor(schema, value)
	if len(problems) == 0 {
		t.Fatalf("value %#v was accepted by %#v", value, schema)
	}
	for _, problem := range problems {
		if problem.Field == field {
			return
		}
	}
	t.Fatalf("value %#v was refused, but not at %q (%v)", value, field, problems)
}

func mustAccept(t *testing.T, schema map[string]any, value any) {
	t.Helper()
	if problems := problemsFor(schema, value); len(problems) != 0 {
		t.Fatalf("value %#v was refused: %v", value, problems)
	}
}

// P1-2: the administrator-registered numeric bounds must be enforced at
// runtime, on both sides of the boundary, for number and integer shapes.
func TestValidateValueEnforcesNumericBounds(t *testing.T) {
	schema := objectSchema(map[string]any{
		"amount": map[string]any{"type": "number", "minimum": 1.5, "maximum": 10},
		"count":  map[string]any{"type": "integer", "minimum": 2, "maximum": 3},
	})
	mustAccept(t, schema, map[string]any{"amount": 1.5, "count": 2})
	mustAccept(t, schema, map[string]any{"amount": 10.0, "count": 3})
	mustAccept(t, schema, map[string]any{"amount": int64(4), "count": int64(2)})

	mustReject(t, schema, map[string]any{"amount": 1.49, "count": 2}, "/amount")
	mustReject(t, schema, map[string]any{"amount": 10.01, "count": 2}, "/amount")
	mustReject(t, schema, map[string]any{"amount": 2.0, "count": 1}, "/count")
	mustReject(t, schema, map[string]any{"amount": 2.0, "count": 4}, "/count")

	// The connector response decoder uses json.Number; the same bounds apply.
	mustReject(t, schema, map[string]any{
		"amount": json.Number("1000000"), "count": json.Number("2"),
	}, "/amount")
	mustAccept(t, schema, map[string]any{
		"amount": json.Number("10"), "count": json.Number("2"),
	})
	mustReject(t, schema, map[string]any{
		"amount": json.Number("2"), "count": json.Number("3.5"),
	}, "/count")

	// A nested object path stays located.
	nested := objectSchema(map[string]any{
		"body": objectSchema(map[string]any{
			"amount": map[string]any{"type": "number", "maximum": 10},
		}),
	})
	mustReject(t, nested, map[string]any{
		"body": map[string]any{"amount": 1000000},
	}, "/body/amount")
}

// P1-2: string lengths count runes, not bytes, and enforce both bounds.
func TestValidateValueEnforcesStringLengthBoundsByRunes(t *testing.T) {
	schema := objectSchema(map[string]any{
		"label": map[string]any{"type": "string", "minLength": 2, "maxLength": 4},
	})
	mustAccept(t, schema, map[string]any{"label": "ab"})
	mustAccept(t, schema, map[string]any{"label": "abcd"})
	mustAccept(t, schema, map[string]any{"label": "汉字"})   // 2 runes, 6 bytes
	mustAccept(t, schema, map[string]any{"label": "漢字漢字"}) // 4 runes, 12 bytes

	mustReject(t, schema, map[string]any{"label": "a"}, "/label")
	mustReject(t, schema, map[string]any{"label": "abcde"}, "/label")
	mustReject(t, schema, map[string]any{"label": "漢字漢字漢"}, "/label") // 5 runes
}

// A declared bound whose shape cannot be evaluated is a located schema problem,
// never silently skipped (the same fail-closed rule as an unknown type).
func TestValidateValueRefusesUnusableBoundShapes(t *testing.T) {
	schema := objectSchema(map[string]any{
		"amount": map[string]any{"type": "number", "minimum": "ten"},
		"label":  map[string]any{"type": "string", "maxLength": -1.5},
	})
	mustReject(t, schema, map[string]any{"amount": 5, "label": "x"}, "/amount")
	mustReject(t, schema, map[string]any{"amount": 5, "label": "x"}, "/label")
}

// Bounds are not applied to values of another type, matching JSON Schema.
func TestValidateValueIgnoresNumericBoundsForNonNumbers(t *testing.T) {
	mustAccept(t,
		objectSchema(map[string]any{
			"label": map[string]any{"type": "string", "minimum": 5},
		}),
		map[string]any{"label": "hi"},
	)
	mustAccept(t,
		objectSchema(map[string]any{
			"amount": map[string]any{"type": "number", "minLength": 5},
		}),
		map[string]any{"amount": 1},
	)
}
