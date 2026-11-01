// RFC 8785 JSON Canonicalization Scheme — the half P05 needs.
//
// This is a port of the serialisation part of probes/jcs/go/jcs.go
// (P01/T003). That package is package main and cannot be imported, so the
// code is reproduced here; to make sure the two copies cannot drift, the
// conformance test in validate_test.go re-runs the *RFC-derived* vectors
// from probes/jcs/vectors.json through this file on every go test.
//
// Only the serialisation half is needed: documents arrive already parsed by
// encoding/json. Lexer-level rejections (duplicate keys, lone surrogates,
// invalid UTF-8) stay in T003's implementation and are not re-tested here.
package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"math"
	"sort"
	"strconv"
	"strings"
	"unicode/utf16"
)

// JCSError is a rejected value with a stable machine-readable code.
type JCSError struct {
	Code    string `json:"error"`
	Message string `json:"message"`
}

func (e *JCSError) Error() string { return e.Code + ": " + e.Message }

func jcsError(code, message string) *JCSError { return &JCSError{Code: code, Message: message} }

const (
	codeInvalidJSON    = "invalid_json"
	codeInvalidUnicode = "invalid_unicode"
	codeNonFinite      = "non_finite"
)

// formatNumber serialises a finite IEEE-754 double as ECMAScript does
// (ECMA-262 7.1.12.1, "Note 2"): shortest round-trip digits, plain form for
// 1e-6 < |value| < 1e21, exponent form otherwise.
func formatNumber(value float64) (string, error) {
	if math.IsNaN(value) || math.IsInf(value, 0) {
		return "", jcsError(codeNonFinite, "NaN and Infinity are not permitted in JSON")
	}
	if value == 0 {
		return "0", nil // covers -0
	}
	if value < 0 {
		magnitude, err := formatNumber(-value)
		if err != nil {
			return "", err
		}
		return "-" + magnitude, nil
	}

	text := strconv.FormatFloat(value, 'e', -1, 64)
	mantissa, exponentText, _ := strings.Cut(text, "e")
	exponent, err := strconv.Atoi(exponentText)
	if err != nil {
		return "", jcsError(codeInvalidJSON, "cannot parse exponent")
	}
	digits := strings.Replace(mantissa, ".", "", 1)
	digits = strings.TrimRight(digits, "0")
	if digits == "" {
		digits = "0"
	}
	k := len(digits)
	n := exponent + 1

	switch {
	case n > 0 && n <= 21:
		if k <= n {
			return digits + strings.Repeat("0", n-k), nil
		}
		return digits[:n] + "." + digits[n:], nil
	case n > -6 && n <= 0:
		return "0." + strings.Repeat("0", -n) + digits, nil
	default:
		sign := "+"
		if n-1 < 0 {
			sign = "-"
		}
		body := digits[:1]
		if k > 1 {
			body += "." + digits[1:]
		}
		return fmt.Sprintf("%se%s%d", body, sign, absInt(n-1)), nil
	}
}

func absInt(v int) int {
	if v < 0 {
		return -v
	}
	return v
}

// formatString escapes exactly like JCS: short escapes for the usual controls,
// \u00xx for the rest, quotes and backslashes escaped, everything else raw.
func formatString(value string) (string, error) {
	var builder strings.Builder
	builder.WriteByte('"')
	for _, char := range value {
		switch {
		case char >= 0xD800 && char <= 0xDFFF:
			return "", jcsError(codeInvalidUnicode, "lone surrogate in string")
		case char == 0x08:
			builder.WriteString("\\b")
		case char == 0x09:
			builder.WriteString("\\t")
		case char == 0x0A:
			builder.WriteString("\\n")
		case char == 0x0C:
			builder.WriteString("\\f")
		case char == 0x0D:
			builder.WriteString("\\r")
		case char < 0x20:
			builder.WriteString(fmt.Sprintf("\\u%04x", char))
		case char == '"':
			builder.WriteString("\\\"")
		case char == '\\':
			builder.WriteString("\\\\")
		default:
			builder.WriteRune(char)
		}
	}
	builder.WriteByte('"')
	return builder.String(), nil
}

// utf16SortKey renders a property name as UTF-16 code units so sorting matches
// ECMAScript string comparison rather than Go's UTF-8 byte order.
func utf16SortKey(name string) []uint16 { return utf16.Encode([]rune(name)) }

func lessUTF16(a, b []uint16) bool {
	for i := 0; i < len(a) && i < len(b); i++ {
		if a[i] != b[i] {
			return a[i] < b[i]
		}
	}
	return len(a) < len(b)
}

// Canonicalize serialises already-parsed data into canonical JSON text.
func Canonicalize(value any) (string, error) {
	switch typed := value.(type) {
	case nil:
		return "null", nil
	case bool:
		if typed {
			return "true", nil
		}
		return "false", nil
	case string:
		return formatString(typed)
	case float64:
		return formatNumber(typed)
	case []any:
		parts := make([]string, 0, len(typed))
		for _, item := range typed {
			rendered, err := Canonicalize(item)
			if err != nil {
				return "", err
			}
			parts = append(parts, rendered)
		}
		return "[" + strings.Join(parts, ",") + "]", nil
	case map[string]any:
		keys := make([]string, 0, len(typed))
		for key := range typed {
			keys = append(keys, key)
		}
		sort.SliceStable(keys, func(i, j int) bool {
			return lessUTF16(utf16SortKey(keys[i]), utf16SortKey(keys[j]))
		})
		parts := make([]string, 0, len(keys))
		for _, key := range keys {
			renderedKey, err := formatString(key)
			if err != nil {
				return "", err
			}
			renderedValue, err := Canonicalize(typed[key])
			if err != nil {
				return "", err
			}
			parts = append(parts, renderedKey+":"+renderedValue)
		}
		return "{" + strings.Join(parts, ",") + "}", nil
	default:
		return "", jcsError(codeInvalidJSON, fmt.Sprintf("unsupported value type %T", value))
	}
}

// CanonicalizeDocument parses raw JSON bytes with encoding/json and canonicalises.
func CanonicalizeDocument(raw []byte) ([]byte, error) {
	var value any
	if err := json.Unmarshal(raw, &value); err != nil {
		return nil, jcsError(codeInvalidJSON, err.Error())
	}
	text, err := Canonicalize(value)
	if err != nil {
		return nil, err
	}
	return []byte(text), nil
}

// SHA256Hex returns the hex digest of the canonical bytes.
func SHA256Hex(payload []byte) string {
	sum := sha256.Sum256(payload)
	return hex.EncodeToString(sum[:])
}
