package jsonschema

import (
	"encoding/json"
	"fmt"
	"math"
	"math/big"
	"sort"
	"strconv"
	"strings"
	"unicode/utf8"
)

// SchemaProblem is one located JSON-schema violation. Field is a JSON-pointer
// style path ("/messages/0/content") so the HTTP 422 details can name it without
// echoing the value.
type SchemaProblem struct {
	Field   string `json:"field"`
	Message string `json:"message"`
}

// ValidateValue checks value against the restricted schema subset F06 supports:
// type, object/properties/required/additionalProperties, array/items, enum,
// minimum/maximum (numbers), minLength/maxLength (string runes),
// string/number/integer/boolean and null. Anything outside that subset is
// ignored (description/title) or, for an unknown *type* or an unusable bound
// keyword shape, a located problem: the platform never silently skips a
// constraint it does not understand when the schema itself declares one.
func ValidateValue(schema map[string]any, value any) []SchemaProblem {
	problems := make([]SchemaProblem, 0)
	validateValue(schema, value, "", 0, &problems)
	return problems
}

func validateValue(schema map[string]any, value any, path string, depth int, problems *[]SchemaProblem) {
	if len(schema) == 0 {
		return
	}
	if depth > 64 {
		addProblem(problems, path, "schema nesting exceeds the supported depth")
		return
	}
	if wanted, present := schema["type"]; present {
		types := schemaTypes(wanted)
		if len(types) == 0 {
			addProblem(problems, path, "schema type is not a supported string or string array")
			return
		}
		if !valueMatchesAnyType(value, types) {
			addProblem(problems, path, "must be of type "+strings.Join(types, "|"))
			return
		}
	}
	if enum, present := schema["enum"]; present {
		values, ok := enum.([]any)
		if !ok {
			addProblem(problems, path, "schema enum must be an array")
			return
		}
		matched := false
		for _, candidate := range values {
			if jsonEqual(candidate, value) {
				matched = true
				break
			}
		}
		if !matched {
			addProblem(problems, path, "must be one of the declared enum values")
		}
	}
	validateBounds(schema, value, path, problems)
	if object, ok := value.(map[string]any); ok {
		validateObject(schema, object, path, depth, problems)
	}
	if items, ok := value.([]any); ok {
		itemSchema, present := schema["items"].(map[string]any)
		if present {
			for index, item := range items {
				validateValue(itemSchema, item, fmt.Sprintf("%s/%d", path, index), depth+1, problems)
			}
		}
	}
}

func validateObject(schema map[string]any, object map[string]any, path string, depth int, problems *[]SchemaProblem) {
	properties, _ := schema["properties"].(map[string]any)
	if required, present := schema["required"]; present {
		names, ok := required.([]any)
		if !ok {
			addProblem(problems, path, "schema required must be an array of strings")
			return
		}
		for _, raw := range names {
			name, ok := raw.(string)
			if !ok {
				addProblem(problems, path, "schema required must be an array of strings")
				return
			}
			if _, exists := object[name]; !exists {
				addProblem(problems, path+"/"+name, "is required")
			}
		}
	}
	keys := make([]string, 0, len(object))
	for key := range object {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	additional, hasAdditional := schema["additionalProperties"]
	for _, key := range keys {
		childSchema, declared := properties[key].(map[string]any)
		if declared {
			validateValue(childSchema, object[key], path+"/"+key, depth+1, problems)
			continue
		}
		switch value := additional.(type) {
		case bool:
			if hasAdditional && !value {
				addProblem(problems, path+"/"+key, "is not an allowed property")
			}
		case map[string]any:
			validateValue(value, object[key], path+"/"+key, depth+1, problems)
		default:
			// additionalProperties absent (or not a supported form) leaves extra
			// properties allowed, matching JSON Schema's default.
		}
	}
}

func schemaTypes(raw any) []string {
	switch value := raw.(type) {
	case string:
		return []string{value}
	case []any:
		types := make([]string, 0, len(value))
		for _, item := range value {
			name, ok := item.(string)
			if !ok {
				return nil
			}
			types = append(types, name)
		}
		return types
	default:
		return nil
	}
}

func valueMatchesAnyType(value any, types []string) bool {
	for _, wanted := range types {
		switch wanted {
		case "object":
			if _, ok := value.(map[string]any); ok {
				return true
			}
		case "array":
			if _, ok := value.([]any); ok {
				return true
			}
		case "string":
			if _, ok := value.(string); ok {
				return true
			}
		case "boolean":
			if _, ok := value.(bool); ok {
				return true
			}
		case "null":
			if value == nil {
				return true
			}
		case "number":
			if isJSONNumber(value) {
				return true
			}
		case "integer":
			if isJSONInteger(value) {
				return true
			}
		default:
			// Unknown type name: never accept silently.
			return false
		}
	}
	return false
}

func isJSONNumber(value any) bool {
	switch value.(type) {
	case float64, float32, int, int32, int64, json.Number:
		return true
	default:
		return false
	}
}

func isJSONInteger(value any) bool {
	switch number := value.(type) {
	case float64:
		return !math.IsNaN(number) && !math.IsInf(number, 0) && math.Trunc(number) == number
	case float32:
		return math.Trunc(float64(number)) == float64(number)
	case int, int32, int64:
		return true
	case json.Number:
		_, err := number.Int64()
		return err == nil
	default:
		return false
	}
}

func addProblem(problems *[]SchemaProblem, path, message string) {
	if path == "" {
		path = "/"
	}
	*problems = append(*problems, SchemaProblem{Field: path, Message: message})
}

// validateBounds enforces the registered numeric and string-length keywords.
// Bounds apply only to values of the matching JSON type, following JSON Schema:
// a `minimum` on a string property is not a value error. A declared keyword
// whose own shape is unusable is a located problem, because an
// administrator-registered constraint must never be silently skipped.
func validateBounds(schema map[string]any, value any, path string, problems *[]SchemaProblem) {
	if number, ok := jsonNumberRat(value); ok {
		if raw, present := schema["minimum"]; present {
			if bound, ok := jsonNumberRat(raw); !ok {
				addProblem(problems, path, "schema minimum must be a number")
			} else if number.Cmp(bound) < 0 {
				addProblem(problems, path, "is less than the declared minimum")
			}
		}
		if raw, present := schema["maximum"]; present {
			if bound, ok := jsonNumberRat(raw); !ok {
				addProblem(problems, path, "schema maximum must be a number")
			} else if number.Cmp(bound) > 0 {
				addProblem(problems, path, "is greater than the declared maximum")
			}
		}
	}
	if text, ok := value.(string); ok {
		length := int64(utf8.RuneCountInString(text))
		if raw, present := schema["minLength"]; present {
			if bound, ok := jsonNonNegativeInteger(raw); !ok {
				addProblem(problems, path, "schema minLength must be a non-negative integer")
			} else if length < bound {
				addProblem(problems, path, "is shorter than the declared minLength")
			}
		}
		if raw, present := schema["maxLength"]; present {
			if bound, ok := jsonNonNegativeInteger(raw); !ok {
				addProblem(problems, path, "schema maxLength must be a non-negative integer")
			} else if length > bound {
				addProblem(problems, path, "is longer than the declared maxLength")
			}
		}
	}
}

// IsJSONNumber reports whether value is one of the JSON number shapes the
// platform accepts (decoded float64/int and decoder json.Number). It is the
// shared keyword-shape check for schema registration and validation.
func IsJSONNumber(value any) bool {
	_, ok := jsonNumberRat(value)
	return ok
}

// CompareNumbers compares two JSON numbers by exact value (-1, 0, 1). ok is
// false when either side is not a number shape.
func CompareNumbers(left, right any) (int, bool) {
	leftNumber, ok := jsonNumberRat(left)
	if !ok {
		return 0, false
	}
	rightNumber, ok := jsonNumberRat(right)
	if !ok {
		return 0, false
	}
	return leftNumber.Cmp(rightNumber), true
}

// NonNegativeInteger reads a JSON non-negative integer keyword (a declared
// length bound). Fractional, negative and non-number shapes are refused.
func NonNegativeInteger(value any) (int64, bool) {
	return jsonNonNegativeInteger(value)
}

// jsonNumberRat turns a JSON-shaped number into an exact rational so decimal
// bounds compare without float rounding. A float64 uses its shortest
// round-tripping decimal form, which is what a JSON document means by it.
func jsonNumberRat(value any) (*big.Rat, bool) {
	switch number := value.(type) {
	case json.Number:
		parsed, ok := new(big.Rat).SetString(string(number))
		return parsed, ok
	case float64:
		if math.IsNaN(number) || math.IsInf(number, 0) {
			return nil, false
		}
		parsed, ok := new(big.Rat).SetString(strconv.FormatFloat(number, 'g', -1, 64))
		return parsed, ok
	case float32:
		wide := float64(number)
		if math.IsNaN(wide) || math.IsInf(wide, 0) {
			return nil, false
		}
		parsed, ok := new(big.Rat).SetString(strconv.FormatFloat(wide, 'g', -1, 32))
		return parsed, ok
	case int:
		return new(big.Rat).SetInt64(int64(number)), true
	case int32:
		return new(big.Rat).SetInt64(int64(number)), true
	case int64:
		return new(big.Rat).SetInt64(number), true
	default:
		return nil, false
	}
}

func jsonNonNegativeInteger(value any) (int64, bool) {
	switch number := value.(type) {
	case float64:
		if math.IsNaN(number) || math.IsInf(number, 0) ||
			math.Trunc(number) != number || number < 0 || number > math.MaxInt64 {
			return 0, false
		}
		return int64(number), true
	case float32:
		wide := float64(number)
		if math.IsNaN(wide) || math.IsInf(wide, 0) ||
			math.Trunc(wide) != wide || wide < 0 || wide > math.MaxInt64 {
			return 0, false
		}
		return int64(wide), true
	case int:
		if number < 0 {
			return 0, false
		}
		return int64(number), true
	case int32:
		if number < 0 {
			return 0, false
		}
		return int64(number), true
	case int64:
		if number < 0 {
			return 0, false
		}
		return number, true
	case json.Number:
		parsed, err := number.Int64()
		if err != nil || parsed < 0 {
			return 0, false
		}
		return parsed, true
	default:
		return 0, false
	}
}

// jsonEqual compares two JSON-shaped values structurally. Numbers compare by
// value (1 == 1.0), which is what an enum declaration means.
func jsonEqual(left, right any) bool {
	if leftNumber, ok := jsonNumberValue(left); ok {
		rightNumber, ok := jsonNumberValue(right)
		return ok && leftNumber == rightNumber
	}
	switch leftValue := left.(type) {
	case nil:
		return right == nil
	case string:
		rightValue, ok := right.(string)
		return ok && leftValue == rightValue
	case bool:
		rightValue, ok := right.(bool)
		return ok && leftValue == rightValue
	case []any:
		rightValue, ok := right.([]any)
		if !ok || len(leftValue) != len(rightValue) {
			return false
		}
		for index := range leftValue {
			if !jsonEqual(leftValue[index], rightValue[index]) {
				return false
			}
		}
		return true
	case map[string]any:
		rightValue, ok := right.(map[string]any)
		if !ok || len(leftValue) != len(rightValue) {
			return false
		}
		for key, value := range leftValue {
			other, present := rightValue[key]
			if !present || !jsonEqual(value, other) {
				return false
			}
		}
		return true
	default:
		return false
	}
}

func jsonNumberValue(value any) (float64, bool) {
	switch number := value.(type) {
	case float64:
		return number, true
	case float32:
		return float64(number), true
	case int:
		return float64(number), true
	case int32:
		return float64(number), true
	case int64:
		return float64(number), true
	case json.Number:
		parsed, err := number.Float64()
		return parsed, err == nil
	default:
		return 0, false
	}
}
