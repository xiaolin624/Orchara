// Signed object links.
//
// The platform never exposes a worker/user-facing path into the object store:
// bytes move through short-lived HMAC-signed URLs minted by the server for one
// operation and one object. The token binds
//
//	op (put|get) + object key + artifact id + expiry + max bytes
//
// and is signed with ORCHARA_OBJECT_SIGNING_KEY (>= 32 bytes). The rules that
// the rest of the platform relies on:
//
//   - **Fail closed when unconfigured.** No signing key means no link can be
//     minted (the API answers 503 object_signing_unavailable) and every
//     presented link is refused. The API still starts: only signed-link
//     traffic stops.
//   - **PUT is staging-only, GET is sealed-only.** A staging object is a
//     one-shot upload target; a sealed object is written by the server alone.
//     Sign refuses to mint a sealed PUT, and Verify refuses a token whose op
//     does not match the key prefix, so a leaked GET URL can never become a
//     write.
//   - **One refusal answer.** Expired, tampered, wrong-key, wrong-op and
//     sealed-PUT all surface as ErrLinkInvalid; the HTTP layer answers 404 and
//     does not reveal whether the object exists.
//   - **The size ceiling travels with the token.** The PUT handler enforces
//     MaxBytes from the claim, which is the artifact's declared size, so a
//     holder cannot store more bytes than the artifact row will admit.
package objectstore

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"errors"
	"fmt"
	"strconv"
	"strings"
	"time"

	"orchara/platform/internal/uuid"
)

// Op is the only operation a signed link may authorize.
type Op string

const (
	// OpPut writes bytes into a staging key (one-shot upload target).
	OpPut Op = "put"
	// OpGet reads bytes from a sealed key.
	OpGet Op = "get"
)

// MinSigningKeyBytes is the minimum HMAC key length accepted from the
// environment. A shorter key is a configuration error, not a degraded mode.
const MinSigningKeyBytes = 32

// DefaultLinkTTL is how long a minted link stays valid when configuration does
// not say otherwise. It is deliberately short: a link is a transfer, not a
// capability.
const DefaultLinkTTL = 10 * time.Minute

// MaxLinkTTL bounds a configured TTL so a misconfiguration cannot mint a
// day-long write capability.
const MaxLinkTTL = time.Hour

// Errors callers map to HTTP answers.
var (
	// ErrSignerNotConfigured means ORCHARA_OBJECT_SIGNING_KEY is empty.
	ErrSignerNotConfigured = errors.New("object link signing is not configured: ORCHARA_OBJECT_SIGNING_KEY is empty")
	// ErrLinkInvalid is the single refusal for every bad link: expired,
	// tampered, wrong key, wrong operation or an operation the key prefix does
	// not allow. Callers must not distinguish the cases.
	ErrLinkInvalid = errors.New("object link is not valid")
)

// Claims is the verified content of one signed link.
type Claims struct {
	Op         Op
	Key        string
	ArtifactID string
	ExpiresAt  time.Time
	MaxBytes   int64
}

// Signer mints and verifies object links for one process. It holds only the
// HMAC key (never logged) and the link TTL. It is safe for concurrent use.
type Signer struct {
	key []byte
	ttl time.Duration
	now func() time.Time // test seam; nil means time.Now
}

// NewSigner validates ORCHARA_OBJECT_SIGNING_KEY.
//
// An empty key returns (nil, nil): the process starts and refuses to mint or
// accept links. A non-empty but short key is a configuration error and refuses
// startup, because silently accepting a weak HMAC key would weaken every link.
func NewSigner(rawKey string, ttl time.Duration) (*Signer, error) {
	key := strings.TrimSpace(rawKey)
	if key == "" {
		return nil, nil
	}
	if len(key) < MinSigningKeyBytes {
		return nil, fmt.Errorf("ORCHARA_OBJECT_SIGNING_KEY must be at least %d bytes", MinSigningKeyBytes)
	}
	if ttl <= 0 {
		ttl = DefaultLinkTTL
	}
	if ttl > MaxLinkTTL {
		ttl = MaxLinkTTL
	}
	return &Signer{key: []byte(key), ttl: ttl}, nil
}

// TTL is the configured link lifetime.
func (s *Signer) TTL() time.Duration {
	if s == nil || s.ttl <= 0 {
		return DefaultLinkTTL
	}
	return s.ttl
}

func (s *Signer) clock() time.Time {
	if s != nil && s.now != nil {
		return s.now()
	}
	return time.Now()
}

// Sign mints one link claim. The op/key pairing is checked here so a sealed PUT
// can never be minted; every value is also range-checked before it is signed.
func (s *Signer) Sign(op Op, key, artifactID string, maxBytes int64) (string, time.Time, error) {
	if s == nil {
		return "", time.Time{}, ErrSignerNotConfigured
	}
	switch op {
	case OpPut:
		if !strings.HasPrefix(key, stagingPrefix) {
			return "", time.Time{}, fmt.Errorf("%w: a PUT link must target a staging key", ErrLinkInvalid)
		}
	case OpGet:
		if !strings.HasPrefix(key, sealedPrefix) {
			return "", time.Time{}, fmt.Errorf("%w: a GET link must target a sealed key", ErrLinkInvalid)
		}
	default:
		return "", time.Time{}, fmt.Errorf("%w: unknown operation %q", ErrLinkInvalid, op)
	}
	if !validKey(key) {
		return "", time.Time{}, ErrInvalidKey
	}
	if !uuid.Valid(strings.TrimSpace(artifactID)) {
		return "", time.Time{}, fmt.Errorf("%w: artifact id is not a UUID", ErrLinkInvalid)
	}
	if maxBytes < 0 || maxBytes > MaxObjectBytes {
		return "", time.Time{}, fmt.Errorf("%w: max bytes is outside 0..%d", ErrLinkInvalid, MaxObjectBytes)
	}
	expires := s.clock().Add(s.TTL()).UTC()
	payload := strings.Join([]string{
		"v1",
		string(op),
		key,
		artifactID,
		strconv.FormatInt(expires.Unix(), 10),
		strconv.FormatInt(maxBytes, 10),
	}, "|")
	encoded := base64.RawURLEncoding.EncodeToString([]byte(payload))
	signature := s.mac(encoded)
	return encoded + "." + signature, expires, nil
}

// Verify checks a presented token against the operation the route serves.
// Every failure returns ErrLinkInvalid: the caller must answer 404 and must not
// reveal whether the object exists.
func (s *Signer) Verify(raw string, want Op) (Claims, error) {
	if s == nil {
		return Claims{}, ErrSignerNotConfigured
	}
	parts := strings.Split(raw, ".")
	if len(parts) != 2 || parts[0] == "" || parts[1] == "" {
		return Claims{}, ErrLinkInvalid
	}
	if !hmac.Equal([]byte(parts[1]), []byte(s.mac(parts[0]))) {
		return Claims{}, ErrLinkInvalid
	}
	payloadBytes, err := base64.RawURLEncoding.DecodeString(parts[0])
	if err != nil {
		return Claims{}, ErrLinkInvalid
	}
	fields := strings.Split(string(payloadBytes), "|")
	if len(fields) != 6 || fields[0] != "v1" {
		return Claims{}, ErrLinkInvalid
	}
	op := Op(fields[1])
	if op != want {
		return Claims{}, ErrLinkInvalid
	}
	key := fields[2]
	switch op {
	case OpPut:
		if !strings.HasPrefix(key, stagingPrefix) {
			return Claims{}, ErrLinkInvalid
		}
	case OpGet:
		if !strings.HasPrefix(key, sealedPrefix) {
			return Claims{}, ErrLinkInvalid
		}
	default:
		return Claims{}, ErrLinkInvalid
	}
	if !validKey(key) {
		return Claims{}, ErrLinkInvalid
	}
	if !uuid.Valid(fields[3]) {
		return Claims{}, ErrLinkInvalid
	}
	expiresUnix, err := strconv.ParseInt(fields[4], 10, 64)
	if err != nil {
		return Claims{}, ErrLinkInvalid
	}
	if !s.clock().Before(time.Unix(expiresUnix, 0).UTC()) {
		return Claims{}, ErrLinkInvalid
	}
	maxBytes, err := strconv.ParseInt(fields[5], 10, 64)
	if err != nil || maxBytes < 0 || maxBytes > MaxObjectBytes {
		return Claims{}, ErrLinkInvalid
	}
	return Claims{
		Op:         op,
		Key:        key,
		ArtifactID: fields[3],
		ExpiresAt:  time.Unix(expiresUnix, 0).UTC(),
		MaxBytes:   maxBytes,
	}, nil
}

func (s *Signer) mac(encodedPayload string) string {
	mac := hmac.New(sha256.New, s.key)
	_, _ = mac.Write([]byte(encodedPayload))
	return base64.RawURLEncoding.EncodeToString(mac.Sum(nil))
}
