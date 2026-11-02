// Package objectstore is the platform's private object store.
//
// This batch ships the local filesystem implementation fixed by the F00B plan
// (D2): a configured root (ORCHARA_OBJECT_DIR) contains two subdirectories,
//
//	staging/  one-shot upload targets; a staging key is never sealed as-is
//	sealed/   immutable platform objects; the server writes, the server reads
//
// Design rules that the rest of the platform relies on:
//
//   - **Fail closed when unconfigured.** Open rejects an empty root and every
//     method on a nil *Store returns ErrNotConfigured. No code path falls back
//     to a temporary directory or to "no object".
//   - **Keys are unguessable and typed.** A key is `staging/<64 lowercase hex>`
//     or `sealed/<64 lowercase hex>`; the key alone names the directory, so a
//     caller never chooses a path. Anything else is ErrInvalidKey, which is what
//     stops `../` traversal through an artifact row.
//   - **Writes are atomic.** Bytes go to a temp file inside the target directory
//     and are renamed into place only after a full copy and fsync. A failed or
//     over-limit copy leaves no object behind, so a retry never sees a torn file.
//   - **The size cap is enforced by the store.** MaxObjectBytes matches the
//     contract's `size_bytes.maximum` (20 MiB); a caller may pass a smaller
//     limit (the artifact's declared size) but never a larger one.
//   - **Hashing is streaming.** Every write returns the SHA-256 of the bytes it
//     stored, and Hash re-reads an object to recompute it server-side; nothing
//     trusts a client-declared hash.
//
// Signed object links (T024) live in sign.go: an HMAC token binds one operation
// and one key, PUT can only target a staging key, GET can only target a sealed
// key, and an unconfigured signing key fails closed without stopping the
// process. S3/MinIO and virus scanning remain out of scope: file admission is
// the hash/size/tombstone/media-type checks this package and the finalize guard
// implement.
package objectstore

import (
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

// MaxObjectBytes is the contract's single-object ceiling (contracts/openapi.json
// size_bytes.maximum = 20971520).
const MaxObjectBytes int64 = 20 << 20

const (
	stagingPrefix = "staging/"
	sealedPrefix  = "sealed/"
	keyNameLength = 64 // 32 random bytes, lowercase hex
)

// Errors callers map to HTTP codes or to a stable job error_code.
var (
	// ErrNotConfigured means ORCHARA_OBJECT_DIR was empty (or the store is nil).
	// Every caller must treat it as "this operation cannot happen here" rather
	// than degrade to another location.
	ErrNotConfigured = errors.New("object store is not configured: ORCHARA_OBJECT_DIR is empty")
	// ErrTooLarge means the bytes exceeded the store's cap or the caller's limit.
	ErrTooLarge = errors.New("object exceeds the allowed size")
	// ErrInvalidKey means the key is not a platform staging/sealed key.
	ErrInvalidKey = errors.New("object key is not a platform object key")
	// ErrNotExist means there is no object at that key.
	ErrNotExist = errors.New("object does not exist")
)

// Store is one configured object-store root. It is safe for concurrent use: it
// holds no mutable state and every write uses a unique temp file.
type Store struct {
	stagingDir string
	sealedDir  string
}

// Object is the result of writing or hashing one object.
type Object struct {
	Key    string
	Size   int64
	SHA256 string // lowercase hex
}

// Open configures a store rooted at root and creates staging/ and sealed/ when
// they are missing. An empty root is ErrNotConfigured: a process that needs the
// object store must refuse the operation instead of inventing a location.
func Open(root string) (*Store, error) {
	trimmed := strings.TrimSpace(root)
	if trimmed == "" {
		return nil, ErrNotConfigured
	}
	store := &Store{
		stagingDir: filepath.Join(trimmed, strings.TrimSuffix(stagingPrefix, "/")),
		sealedDir:  filepath.Join(trimmed, strings.TrimSuffix(sealedPrefix, "/")),
	}
	for _, dir := range []string{store.stagingDir, store.sealedDir} {
		if err := os.MkdirAll(dir, 0o700); err != nil {
			return nil, fmt.Errorf("creating object store directory: %w", err)
		}
	}
	return store, nil
}

// StagingKey returns a fresh, unguessable staging key. It does not touch the
// filesystem, so a caller can allocate the key and hand it out before writing.
func StagingKey() (string, error) { return newKey(stagingPrefix) }

// SealedKey returns a fresh, unguessable sealed key. A sealed key is never
// reused for another object and is never handed to a client for writing.
func SealedKey() (string, error) { return newKey(sealedPrefix) }

func newKey(prefix string) (string, error) {
	buf := make([]byte, keyNameLength/2)
	if _, err := rand.Read(buf); err != nil {
		return "", fmt.Errorf("generating an object key: %w", err)
	}
	return prefix + hex.EncodeToString(buf), nil
}

// CreateStaging writes r to a new staging object. limit narrows the size cap
// (for example to the artifact's declared size_bytes); a value <= 0 means
// MaxObjectBytes. Returns ErrTooLarge without leaving a partial object.
func (s *Store) CreateStaging(r io.Reader, limit int64) (Object, error) {
	return s.create(stagingPrefix, r, limit)
}

// CreateSealed writes r to a new sealed object. See CreateStaging for limit.
func (s *Store) CreateSealed(r io.Reader, limit int64) (Object, error) {
	return s.create(sealedPrefix, r, limit)
}

// PutSealed writes r to an existing sealed key atomically.
//
// The finalize handler needs this: completeUpload (T024) preallocates the sealed
// key on the artifact row, and the handler must fill exactly that key rather than
// invent another one. The write is whole-or-nothing (temp file + rename) and a
// retry of the same job rewrites the same key; a key is never reused for a
// different artifact, and no client ever receives a PUT URL for it. Staging keys
// are refused here: a staging object must stay a one-shot upload.
func (s *Store) PutSealed(key string, r io.Reader, limit int64) (Object, error) {
	return s.put(key, sealedPrefix, r, effectiveLimit(limit))
}

// PutStaging writes r to an existing staging key atomically.
//
// This is the signed-PUT path from T024: artifact-uploads preallocates the key
// on the artifact row and mints a PUT link for exactly it, so the uploader can
// neither choose a path nor overwrite a sealed object. Like every write it is
// whole-or-nothing and streams the SHA-256 of the bytes it accepted; the server
// recomputes the hash at finalize time and never trusts this one.
//
// limit is exact here (0 accepts only an empty body): a reserved upload's
// declared size is a ceiling, not a default.
func (s *Store) PutStaging(key string, r io.Reader, limit int64) (Object, error) {
	if limit < 0 {
		limit = 0
	}
	if limit > MaxObjectBytes {
		limit = MaxObjectBytes
	}
	return s.put(key, stagingPrefix, r, limit)
}

func (s *Store) put(key, requiredPrefix string, r io.Reader, limit int64) (Object, error) {
	if s == nil {
		return Object{}, ErrNotConfigured
	}
	if r == nil {
		return Object{}, errors.New("object reader is nil")
	}
	if !validKey(key) || !strings.HasPrefix(key, requiredPrefix) {
		return Object{}, ErrInvalidKey
	}
	path, err := s.pathFor(key)
	if err != nil {
		return Object{}, err
	}
	temp, err := os.CreateTemp(s.dirFor(requiredPrefix), ".tmp-*")
	if err != nil {
		return Object{}, fmt.Errorf("creating a temporary object file: %w", err)
	}
	removeTemp := func() {
		_ = temp.Close()
		_ = os.Remove(temp.Name())
	}
	hasher := sha256.New()
	written, copyErr := io.Copy(io.MultiWriter(temp, hasher), io.LimitReader(r, limit+1))
	if copyErr != nil {
		removeTemp()
		return Object{}, fmt.Errorf("writing the object: %w", copyErr)
	}
	if written > limit {
		removeTemp()
		return Object{}, ErrTooLarge
	}
	if err := temp.Sync(); err != nil {
		removeTemp()
		return Object{}, fmt.Errorf("flushing the object: %w", err)
	}
	if err := temp.Close(); err != nil {
		_ = os.Remove(temp.Name())
		return Object{}, fmt.Errorf("closing the object: %w", err)
	}
	if err := os.Rename(temp.Name(), path); err != nil {
		_ = os.Remove(temp.Name())
		return Object{}, fmt.Errorf("sealing the object: %w", err)
	}
	return Object{Key: key, Size: written, SHA256: hex.EncodeToString(hasher.Sum(nil))}, nil
}

func (s *Store) create(prefix string, r io.Reader, limit int64) (Object, error) {
	if s == nil {
		return Object{}, ErrNotConfigured
	}
	if r == nil {
		return Object{}, errors.New("object reader is nil")
	}
	limit = effectiveLimit(limit)
	key, err := newKey(prefix)
	if err != nil {
		return Object{}, err
	}
	dir := s.dirFor(prefix)
	name := strings.TrimPrefix(key, prefix)

	temp, err := os.CreateTemp(dir, ".tmp-*")
	if err != nil {
		return Object{}, fmt.Errorf("creating a staging temp file: %w", err)
	}
	tempName := temp.Name()
	// Any failure below removes the temp file: an object either exists whole or
	// not at all, and a retry never reads a torn file.
	removeTemp := func() {
		_ = temp.Close()
		_ = os.Remove(tempName)
	}

	hasher := sha256.New()
	written, copyErr := io.Copy(io.MultiWriter(temp, hasher), io.LimitReader(r, limit+1))
	if copyErr != nil {
		removeTemp()
		return Object{}, fmt.Errorf("writing the object: %w", copyErr)
	}
	if written > limit {
		removeTemp()
		return Object{}, ErrTooLarge
	}
	if err := temp.Sync(); err != nil {
		removeTemp()
		return Object{}, fmt.Errorf("flushing the object: %w", err)
	}
	if err := temp.Close(); err != nil {
		_ = os.Remove(tempName)
		return Object{}, fmt.Errorf("closing the object: %w", err)
	}
	if err := os.Rename(tempName, filepath.Join(dir, name)); err != nil {
		_ = os.Remove(tempName)
		return Object{}, fmt.Errorf("sealing the object: %w", err)
	}
	return Object{Key: key, Size: written, SHA256: hex.EncodeToString(hasher.Sum(nil))}, nil
}

// Open returns a streaming reader for key and the stored size. The caller owns
// the returned ReadCloser.
func (s *Store) Open(key string) (io.ReadCloser, int64, error) {
	if s == nil {
		return nil, 0, ErrNotConfigured
	}
	path, err := s.pathFor(key)
	if err != nil {
		return nil, 0, err
	}
	file, err := os.Open(path)
	if errors.Is(err, os.ErrNotExist) {
		return nil, 0, ErrNotExist
	}
	if err != nil {
		return nil, 0, fmt.Errorf("opening the object: %w", err)
	}
	info, err := file.Stat()
	if err != nil {
		_ = file.Close()
		return nil, 0, fmt.Errorf("inspecting the object: %w", err)
	}
	if info.IsDir() {
		_ = file.Close()
		return nil, 0, ErrInvalidKey
	}
	return file, info.Size(), nil
}

// Hash re-reads key and returns its size and SHA-256. It caps the read at
// MaxObjectBytes: an object larger than the platform ceiling is refused rather
// than hashed forever, and the caller must not activate it.
func (s *Store) Hash(key string) (Object, error) {
	if s == nil {
		return Object{}, ErrNotConfigured
	}
	reader, _, err := s.Open(key)
	if err != nil {
		return Object{}, err
	}
	defer func() { _ = reader.Close() }()

	hasher := sha256.New()
	written, err := io.Copy(hasher, io.LimitReader(reader, MaxObjectBytes+1))
	if err != nil {
		return Object{}, fmt.Errorf("reading the object: %w", err)
	}
	if written > MaxObjectBytes {
		return Object{}, ErrTooLarge
	}
	return Object{Key: key, Size: written, SHA256: hex.EncodeToString(hasher.Sum(nil))}, nil
}

// Stat returns the size of key without reading its bytes.
func (s *Store) Stat(key string) (int64, error) {
	if s == nil {
		return 0, ErrNotConfigured
	}
	path, err := s.pathFor(key)
	if err != nil {
		return 0, err
	}
	info, err := os.Stat(path)
	if errors.Is(err, os.ErrNotExist) {
		return 0, ErrNotExist
	}
	if err != nil {
		return 0, fmt.Errorf("inspecting the object: %w", err)
	}
	if info.IsDir() {
		return 0, ErrInvalidKey
	}
	return info.Size(), nil
}

// Delete removes key. A missing object is not an error: deleting twice is
// idempotent, which the orphan sweep will rely on.
func (s *Store) Delete(key string) error {
	if s == nil {
		return ErrNotConfigured
	}
	path, err := s.pathFor(key)
	if err != nil {
		return err
	}
	if err := os.Remove(path); err != nil && !errors.Is(err, os.ErrNotExist) {
		return fmt.Errorf("deleting the object: %w", err)
	}
	return nil
}

// ListedObject is one existing object returned by List: key, size and mtime.
// The mtime is what the orphan sweep uses as its age boundary, so the caller
// never guesses an object's age from the database row.
type ListedObject struct {
	Key     string
	Size    int64
	ModTime time.Time
}

// List returns every valid platform object under one typed prefix
// (`staging/` or `sealed/`), sorted by key.
//
// Only names that parse as a platform key are returned: a stray temporary file
// or a manually dropped name is not an object the platform created, so a
// caller cannot mistake it for one. The listing is a point-in-time view; the
// caller re-checks the database references before deleting anything.
func (s *Store) List(prefix string) ([]ListedObject, error) {
	if s == nil {
		return nil, ErrNotConfigured
	}
	var dir string
	switch prefix {
	case stagingPrefix:
		dir = s.stagingDir
	case sealedPrefix:
		dir = s.sealedDir
	default:
		return nil, ErrInvalidKey
	}
	entries, err := os.ReadDir(dir)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return nil, nil
		}
		return nil, fmt.Errorf("listing the object store: %w", err)
	}
	objects := make([]ListedObject, 0, len(entries))
	for _, entry := range entries {
		if entry.IsDir() {
			continue
		}
		key := prefix + entry.Name()
		if !validKey(key) {
			continue
		}
		info, err := entry.Info()
		if err != nil {
			return nil, fmt.Errorf("inspecting the object: %w", err)
		}
		objects = append(objects, ListedObject{
			Key:     key,
			Size:    info.Size(),
			ModTime: info.ModTime(),
		})
	}
	sort.Slice(objects, func(i, j int) bool { return objects[i].Key < objects[j].Key })
	return objects, nil
}

func (s *Store) dirFor(prefix string) string {
	if prefix == sealedPrefix {
		return s.sealedDir
	}
	return s.stagingDir
}

// pathFor validates the typed key and resolves it inside the configured root.
// The key carries its directory, so no caller-supplied path component is used.
func (s *Store) pathFor(key string) (string, error) {
	if !validKey(key) {
		return "", ErrInvalidKey
	}
	if strings.HasPrefix(key, sealedPrefix) {
		return filepath.Join(s.sealedDir, strings.TrimPrefix(key, sealedPrefix)), nil
	}
	return filepath.Join(s.stagingDir, strings.TrimPrefix(key, stagingPrefix)), nil
}

// validKey reports whether key is exactly `staging/<64 hex>` or
// `sealed/<64 hex>`. It is shared with the link signer so a token can never name
// a key the store would resolve differently.
func validKey(key string) bool {
	var name string
	switch {
	case strings.HasPrefix(key, stagingPrefix):
		name = strings.TrimPrefix(key, stagingPrefix)
	case strings.HasPrefix(key, sealedPrefix):
		name = strings.TrimPrefix(key, sealedPrefix)
	default:
		return false
	}
	if len(name) != keyNameLength {
		return false
	}
	for index := 0; index < len(name); index++ {
		char := name[index]
		if (char < '0' || char > '9') && (char < 'a' || char > 'f') {
			return false
		}
	}
	return true
}

func effectiveLimit(limit int64) int64 {
	if limit <= 0 || limit > MaxObjectBytes {
		return MaxObjectBytes
	}
	return limit
}
