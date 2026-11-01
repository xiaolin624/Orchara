// Package identity implements local accounts, one-time invitations, sessions
// and role bindings.
//
// It is the only place that knows how passwords and tokens are represented:
//
//   - passwords are argon2id PHC strings (DESIGN.md §5 fixes the parameters);
//   - the value `!pending-activation` is a placeholder that can never verify;
//   - tokens are 32 random bytes, base64url encoded, and only their SHA-256 is
//     ever stored or compared.
package identity

import (
	"crypto/rand"
	"crypto/subtle"
	"encoding/base64"
	"errors"
	"fmt"
	"strconv"
	"strings"
	"sync"

	"golang.org/x/crypto/argon2"
)

// Password policy and argon2id parameters fixed by the F01 design.
const (
	// MinPasswordLength is the shortest password bootstrap and activation accept.
	MinPasswordLength = 12

	// PendingActivation is the users.password_hash sentinel for an invited user
	// who has not set a password yet. It never verifies: VerifyPassword rejects
	// anything that is not a well-formed argon2id PHC string.
	PendingActivation = "!pending-activation"

	argonMemory     uint32 = 64 * 1024 // 64 MiB
	argonIterations uint32 = 3
	argonThreads    uint8  = 2
	argonSaltLength        = 16
	argonKeyLength         = 32

	// Bounds for values read back from the database: a stored hash must not be
	// able to make verification allocate unbounded memory.
	maxArgonMemory     uint32 = 1 << 20
	maxArgonIterations uint32 = 10
	maxArgonThreads    uint8  = 16
	minSaltLength             = 8
	maxSaltLength             = 64
	minKeyLength              = 16
	maxKeyLength              = 64
)

// errMalformedHash is internal: callers only need "does not verify".
var errMalformedHash = errors.New("malformed password hash")

type argonParams struct {
	memory     uint32
	iterations uint32
	threads    uint8
	keyLength  uint32
}

var defaultParams = argonParams{
	memory:     argonMemory,
	iterations: argonIterations,
	threads:    argonThreads,
	keyLength:  argonKeyLength,
}

// HashPassword derives a new argon2id PHC string with a fresh random salt.
func HashPassword(password string) (string, error) {
	salt := make([]byte, argonSaltLength)
	if _, err := rand.Read(salt); err != nil {
		return "", fmt.Errorf("generating a password salt: %w", err)
	}
	key := argon2.IDKey([]byte(password), salt, argonIterations, argonMemory, argonThreads, argonKeyLength)
	return fmt.Sprintf(
		"$argon2id$v=%d$m=%d,t=%d,p=%d$%s$%s",
		argon2.Version,
		argonMemory,
		argonIterations,
		argonThreads,
		base64.RawStdEncoding.EncodeToString(salt),
		base64.RawStdEncoding.EncodeToString(key),
	), nil
}

// VerifyPassword reports whether password matches encoded.
//
// needsRehash is true when the stored hash used weaker/different parameters and
// should be replaced on a successful login. Any malformed value — including the
// pending-activation sentinel and an empty string — simply does not verify; it
// never falls back to string comparison.
func VerifyPassword(encoded, password string) (ok bool, needsRehash bool) {
	params, salt, expected, err := parsePHC(encoded)
	if err != nil {
		return false, false
	}
	computed := argon2.IDKey([]byte(password), salt, params.iterations, params.memory, params.threads, params.keyLength)
	if subtle.ConstantTimeCompare(computed, expected) != 1 {
		return false, false
	}
	return true, params != defaultParams
}

// dummyPasswordHash is verified when a login does not exist, so an unknown
// login costs roughly the same as a wrong password for an existing one.
var dummyPasswordHash = sync.OnceValue(func() string {
	hash, err := HashPassword(base64.RawURLEncoding.EncodeToString([]byte("dummy-verification-target")))
	if err != nil {
		// crypto/rand failing is unrecoverable; keeping an empty hash here would
		// silently remove the timing equalisation, so fail loudly instead.
		panic(fmt.Sprintf("identity: cannot build the dummy password hash: %v", err))
	}
	return hash
})

func verifyDummy(password string) {
	_, _ = VerifyPassword(dummyPasswordHash(), password)
}

func parsePHC(encoded string) (argonParams, []byte, []byte, error) {
	if !strings.HasPrefix(encoded, "$argon2id$") {
		// The sentinel, a plaintext value or another algorithm: never verify.
		return argonParams{}, nil, nil, errMalformedHash
	}
	parts := strings.Split(strings.TrimPrefix(encoded, "$"), "$")
	if len(parts) != 5 || parts[0] != "argon2id" {
		return argonParams{}, nil, nil, errMalformedHash
	}

	versionRaw, found := strings.CutPrefix(parts[1], "v=")
	if !found {
		return argonParams{}, nil, nil, errMalformedHash
	}
	version, err := strconv.Atoi(versionRaw)
	if err != nil || version != argon2.Version {
		return argonParams{}, nil, nil, errMalformedHash
	}

	params, err := parseParams(parts[2])
	if err != nil {
		return argonParams{}, nil, nil, err
	}
	salt, err := decodeBase64(parts[3], minSaltLength, maxSaltLength)
	if err != nil {
		return argonParams{}, nil, nil, err
	}
	key, err := decodeBase64(parts[4], minKeyLength, maxKeyLength)
	if err != nil {
		return argonParams{}, nil, nil, err
	}
	params.keyLength = uint32(len(key))
	return params, salt, key, nil
}

func parseParams(raw string) (argonParams, error) {
	values := make(map[string]string, 3)
	for _, field := range strings.Split(raw, ",") {
		key, value, found := strings.Cut(field, "=")
		if !found {
			return argonParams{}, errMalformedHash
		}
		values[key] = value
	}
	memory, err := parseUint32(values["m"], 8, maxArgonMemory)
	if err != nil {
		return argonParams{}, err
	}
	iterations, err := parseUint32(values["t"], 1, maxArgonIterations)
	if err != nil {
		return argonParams{}, err
	}
	threads, err := parseUint32(values["p"], 1, uint32(maxArgonThreads))
	if err != nil {
		return argonParams{}, err
	}
	if memory < 8*uint32(threads) {
		// argon2 requires memory >= 8*threads; reject instead of panicking.
		return argonParams{}, errMalformedHash
	}
	return argonParams{
		memory:     memory,
		iterations: iterations,
		threads:    uint8(threads),
	}, nil
}

func parseUint32(raw string, min, max uint32) (uint32, error) {
	if raw == "" {
		return 0, errMalformedHash
	}
	value, err := strconv.ParseUint(raw, 10, 32)
	if err != nil || uint32(value) < min || uint32(value) > max {
		return 0, errMalformedHash
	}
	return uint32(value), nil
}

func decodeBase64(raw string, minLen, maxLen int) ([]byte, error) {
	decoded, err := base64.RawStdEncoding.DecodeString(raw)
	if err != nil || len(decoded) < minLen || len(decoded) > maxLen {
		return nil, errMalformedHash
	}
	return decoded, nil
}
