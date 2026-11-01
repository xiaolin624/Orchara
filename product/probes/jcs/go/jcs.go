// Package main implements RFC 8785 JSON Canonicalization Scheme for Orchara.
//
// This is the Go half of P01/T003: the same canonical bytes and SHA-256 as the
// Python implementation, checked against shared golden vectors.
//
// Go's encoding/json silently replaces lone surrogates with U+FFFD and rejects
// NaN before we can name it, so the raw bytes are lexed first. That is also
// where invalid UTF-8 is caught.
package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"sort"
	"strconv"
	"strings"
	"unicode/utf16"
	"unicode/utf8"
)

// JCSError is a rejected input with a stable machine-readable code.
type JCSError struct {
	Code    string `json:"error"`
	Message string `json:"message"`
}

func (e *JCSError) Error() string { return e.Code + ": " + e.Message }

func jcsError(code, message string) *JCSError { return &JCSError{Code: code, Message: message} }

const (
	codeInvalidJSON    = "invalid_json"
	codeInvalidUTF8    = "invalid_utf8"
	codeInvalidUnicode = "invalid_unicode"
	codeDuplicateKey   = "duplicate_key"
	codeNonFinite      = "non_finite"
)

// ---------------------------------------------------------------------------
// lexical pre-scan: invalid UTF-8, lone surrogates, NaN/Infinity literals
// ---------------------------------------------------------------------------
func hex4(b []byte) (rune, bool) {
	if len(b) < 4 {
		return 0, false
	}
	var value rune
	for _, c := range b[:4] {
		value <<= 4
		switch {
		case c >= '0' && c <= '9':
			value |= rune(c - '0')
		case c >= 'a' && c <= 'f':
			value |= rune(c-'a') + 10
		case c >= 'A' && c <= 'F':
			value |= rune(c-'A') + 10
		default:
			return 0, false
		}
	}
	return value, true
}

func isIdentStart(c byte) bool {
	return c == '_' || (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z')
}

func isIdentChar(c byte) bool { return isIdentStart(c) || (c >= '0' && c <= '9') }

func preScan(raw []byte) error {
	if !utf8.Valid(raw) {
		return jcsError(codeInvalidUTF8, "input is not valid UTF-8")
	}
	for i := 0; i < len(raw); {
		switch c := raw[i]; {
		case c == '"':
			i++
			for i < len(raw) {
				if raw[i] == '\\' {
					i++
					if i >= len(raw) {
						return jcsError(codeInvalidJSON, "unterminated escape sequence")
					}
					if raw[i] != 'u' {
						if !strings.ContainsRune("\"\\/bfnrt", rune(raw[i])) {
							return jcsError(codeInvalidJSON, "invalid escape sequence")
						}
						i++
						continue
					}
					unit, ok := hex4(raw[i+1:])
					if !ok {
						return jcsError(codeInvalidJSON, "invalid \\u escape")
					}
					i += 5
					switch {
					case unit >= 0xD800 && unit <= 0xDBFF:
						// high surrogate: a matching low surrogate must follow
						if i+1 >= len(raw) || raw[i] != '\\' || raw[i+1] != 'u' {
							return jcsError(codeInvalidUnicode, "lone high surrogate")
						}
						low, ok := hex4(raw[i+2:])
						if !ok || low < 0xDC00 || low > 0xDFFF {
							return jcsError(codeInvalidUnicode, "lone high surrogate")
						}
						i += 6
					case unit >= 0xDC00 && unit <= 0xDFFF:
						return jcsError(codeInvalidUnicode, "lone low surrogate")
					}
					continue
				}
				if raw[i] == '"' {
					i++
					break
				}
				i++
			}
		case isIdentStart(c):
			start := i
			for i < len(raw) && isIdentChar(raw[i]) {
				i++
			}
			token := string(raw[start:i])
			if token == "NaN" || token == "Infinity" {
				return jcsError(codeNonFinite, token+" is not permitted in JSON")
			}
		default:
			i++
		}
	}
	return nil
}

// ---------------------------------------------------------------------------
// parsing with duplicate-key detection
// ---------------------------------------------------------------------------
func parse(raw []byte) (any, error) {
	if err := preScan(raw); err != nil {
		return nil, err
	}
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.UseNumber()
	value, err := parseValue(dec)
	if err != nil {
		return nil, err
	}
	if _, err := dec.Token(); err != io.EOF {
		return nil, jcsError(codeInvalidJSON, "trailing data after JSON value")
	}
	return value, nil
}

func parseValue(dec *json.Decoder) (any, error) {
	token, err := dec.Token()
	if err != nil {
		return nil, jcsError(codeInvalidJSON, err.Error())
	}
	switch typed := token.(type) {
	case json.Delim:
		switch typed {
		case '{':
			object := map[string]any{}
			for dec.More() {
				keyToken, err := dec.Token()
				if err != nil {
					return nil, jcsError(codeInvalidJSON, err.Error())
				}
				key, ok := keyToken.(string)
				if !ok {
					return nil, jcsError(codeInvalidJSON, "object property name is not a string")
				}
				if _, exists := object[key]; exists {
					return nil, jcsError(codeDuplicateKey, "duplicate property name")
				}
				value, err := parseValue(dec)
				if err != nil {
					return nil, err
				}
				object[key] = value
			}
			if _, err := dec.Token(); err != nil { // closing '}'
				return nil, jcsError(codeInvalidJSON, err.Error())
			}
			return object, nil
		case '[':
			array := []any{}
			for dec.More() {
				value, err := parseValue(dec)
				if err != nil {
					return nil, err
				}
				array = append(array, value)
			}
			if _, err := dec.Token(); err != nil { // closing ']'
				return nil, jcsError(codeInvalidJSON, err.Error())
			}
			return array, nil
		default:
			return nil, jcsError(codeInvalidJSON, "unexpected delimiter")
		}
	case json.Number:
		number, err := strconv.ParseFloat(string(typed), 64)
		if err != nil {
			if math.IsInf(number, 0) {
				return nil, jcsError(codeNonFinite, "number out of IEEE 754 double range")
			}
			return nil, jcsError(codeInvalidJSON, err.Error())
		}
		return number, nil
	case string, bool, nil:
		return typed, nil
	default:
		return nil, jcsError(codeInvalidJSON, "unsupported token")
	}
}

// ---------------------------------------------------------------------------
// serialisation
// ---------------------------------------------------------------------------
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

	// Shortest round-trip digits via the exponent form.
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
		return fmt.Sprintf("%se%s%d", body, sign, abs(n-1)), nil
	}
}

func abs(v int) int {
	if v < 0 {
		return -v
	}
	return v
}

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

// utf16SortKey renders a property name as UTF-16 code units so that sorting
// matches ECMAScript string comparison rather than Go's UTF-8 byte order.
func utf16SortKey(name string) []uint16 {
	return utf16.Encode([]rune(name))
}

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
		return "", jcsError(codeInvalidJSON, "unsupported value type")
	}
}

// CanonicalizeBytes parses and canonicalises raw JSON bytes.
func CanonicalizeBytes(raw []byte) ([]byte, error) {
	value, err := parse(raw)
	if err != nil {
		return nil, err
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
