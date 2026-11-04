// Command localfs_probe runs the local-filesystem half of the F15A/#54 object
// store boundary evidence.
//
// The product's real object store is `internal/objectstore` (local filesystem +
// HMAC-signed links), not S3. It cannot prove IAM/anonymous/bucket-policy
// behaviour, and this probe does not pretend otherwise; those assertions are
// reported `not_run` and remain blocked on a candidate S3 endpoint.
//
// What it *does* prove, with real bytes in a real directory and the real package
// the platform imports:
//
//   - key typing: only `staging/<64 lowercase hex>` and `sealed/<64 hex>` resolve;
//     `../`, absolute paths, wrong length and wrong case are ErrInvalidKey;
//   - staging/sealed separation: overwriting a staging key cannot change a sealed
//     object, and a staging key is refused as a Move source;
//   - sealed write boundary: PutStaging refuses a sealed key and PutSealed refuses
//     a staging key; a PUT link can never target a sealed key;
//   - signed links: TTL expiry, operation binding (PUT↔staging, GET↔sealed),
//     max-bytes bound, short/long key rejection, fail-closed when unconfigured;
//   - delete: deleting twice is idempotent and a sibling sealed object survives;
//   - size cap: the store refuses bytes over MaxObjectBytes and leaves no partial
//     object behind.
//
// Usage: go run ./probes/f15a_objectstore/localfs_probe -root <dir> -out <json>
package main

import (
	"bytes"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"time"

	"orchara/platform/internal/objectstore"
	"orchara/platform/internal/uuid"
)

const probeVersion = "1.0.0"

type check struct {
	Name   string         `json:"name"`
	OK     bool           `json:"ok"`
	Detail map[string]any `json:"detail,omitempty"`
}

type assertion struct {
	ID       string         `json:"id"`
	Title    string         `json:"title"`
	Status   string         `json:"status"`
	Reason   string         `json:"reason"`
	Checks   []check        `json:"checks"`
	Evidence map[string]any `json:"evidence,omitempty"`
}

type report struct {
	Probe         string         `json:"probe"`
	ProbeVersion  string         `json:"probe_version"`
	Ticket        string         `json:"ticket"`
	Scope         string         `json:"scope"`
	NotS3Evidence bool           `json:"not_s3_evidence"`
	Status        string         `json:"status"`
	ExitCode      int            `json:"exit_code"`
	Environment   map[string]any `json:"environment"`
	Store         map[string]any `json:"store"`
	Assertions    []assertion    `json:"assertions"`
	Summary       map[string]int `json:"summary"`
	Limits        []string       `json:"limits"`
	StartedAt     string         `json:"started_at"`
	FinishedAt    string         `json:"finished_at"`
	DurationMS    int64          `json:"duration_ms"`
}

func main() {
	var (
		root = flag.String("root", "", "directory for the probe store (default: a fresh temp dir)")
		out  = flag.String("out", "", "write the JSON report to this path")
	)
	flag.Parse()

	started := time.Now()
	storeRoot := *root
	removeRoot := false
	if storeRoot == "" {
		dir, err := os.MkdirTemp("", "f15a-localfs-*")
		if err != nil {
			fmt.Fprintln(os.Stderr, "cannot create probe root:", err)
			os.Exit(2)
		}
		storeRoot = dir
		removeRoot = true
	}
	if err := os.MkdirAll(storeRoot, 0o700); err != nil {
		fmt.Fprintln(os.Stderr, "cannot create probe root:", err)
		os.Exit(2)
	}

	assertions, err := runAll(storeRoot)
	if err != nil {
		// A store failure inside an assertion is a real failure, not a pass.
		fmt.Fprintln(os.Stderr, "local store probe failed:", err)
	}
	finished := time.Now()
	failed := 0
	notRun := 0
	summary := map[string]int{"passed": 0, "failed": 0, "not_run": 0}
	for _, item := range assertions {
		switch item.Status {
		case "failed":
			failed++
		case "not_run":
			notRun++
		}
		summary[item.Status]++
	}
	status := "passed"
	exitCode := 0
	if failed > 0 || err != nil {
		status = "failed"
		exitCode = 1
	}
	result := report{
		Probe:         "f15a-local-fs-analogue",
		ProbeVersion:  probeVersion,
		Ticket:        "T053/F15A",
		Scope:         "local-filesystem",
		NotS3Evidence: true,
		Status:        status,
		ExitCode:      exitCode,
		Environment: map[string]any{
			"go":   runtime.Version(),
			"os":   runtime.GOOS,
			"arch": runtime.GOARCH,
		},
		Store: map[string]any{
			"root":             storeRoot,
			"max_object_bytes": objectstore.MaxObjectBytes,
			"implementation":   "internal/objectstore (local filesystem, HMAC links)",
		},
		Assertions: assertions,
		Summary:    summary,
		Limits: []string{
			"not acceptance evidence for a candidate S3 service",
			"no HTTP surface here: anonymous access, IAM/bucket policy, TLS and presigned S3 URLs are not exercised",
			"no object versioning, backup/restore, replication or server-side encryption in the local FS store",
			"the local journal/rename guarantees are filesystem-specific and say nothing about an S3 implementation",
		},
		StartedAt:  started.UTC().Format(time.RFC3339Nano),
		FinishedAt: finished.UTC().Format(time.RFC3339Nano),
		DurationMS: finished.Sub(started).Milliseconds(),
	}

	encoded, marshalErr := json.MarshalIndent(result, "", "  ")
	if marshalErr != nil {
		fmt.Fprintln(os.Stderr, "cannot encode report:", marshalErr)
		os.Exit(2)
	}
	encoded = append(encoded, '\n')
	if *out != "" {
		if mkErr := os.MkdirAll(filepath.Dir(*out), 0o755); mkErr != nil {
			fmt.Fprintln(os.Stderr, "cannot create report directory:", mkErr)
			os.Exit(2)
		}
		if writeErr := os.WriteFile(*out, encoded, 0o644); writeErr != nil {
			fmt.Fprintln(os.Stderr, "cannot write report:", writeErr)
			os.Exit(2)
		}
	}
	fmt.Printf("f15a local-fs analogue: %s (%d passed, %d failed, %d not_run)\n", status, summary["passed"], summary["failed"], notRun)
	for _, item := range assertions {
		fmt.Printf("  [%s] %s: %s\n", item.Status, item.ID, item.Reason)
	}
	if *out != "" {
		fmt.Println("report:", *out)
	}
	if removeRoot {
		_ = os.RemoveAll(storeRoot)
	}
	os.Exit(exitCode)
}

func runAll(root string) ([]assertion, error) {
	store, err := objectstore.Open(root)
	if err != nil {
		return nil, fmt.Errorf("open store: %w", err)
	}
	results := []assertion{
		keyTyping(store),
		stagingSealedSeparation(store),
		sealedWriteBoundary(store),
		signedLinkBoundaries(),
		deleteSemantics(store),
		sizeCap(store),
		{
			ID:     "anonymous_network_boundary",
			Title:  "anonymous HTTP access is denied (S3 deployment property)",
			Status: "not_run",
			Reason: "the local filesystem store has no HTTP surface; this stays blocked on a candidate S3 endpoint",
			Checks: []check{},
		},
		{
			ID:     "object_versioning",
			Title:  "object versioning keeps old sealed bytes addressable",
			Status: "not_run",
			Reason: "the local filesystem store has no object versioning",
			Checks: []check{},
		},
	}
	return results, nil
}

func newAssertion(id, title string, checks []check, evidence map[string]any) assertion {
	failed := []string{}
	for _, item := range checks {
		if !item.OK {
			failed = append(failed, item.Name)
		}
	}
	if len(failed) > 0 {
		return assertion{
			ID:       id,
			Title:    title,
			Status:   "failed",
			Reason:   "failed checks: " + strings.Join(failed, ", "),
			Checks:   checks,
			Evidence: evidence,
		}
	}
	return assertion{
		ID:       id,
		Title:    title,
		Status:   "passed",
		Reason:   "all checks held",
		Checks:   checks,
		Evidence: evidence,
	}
}

func sha(bytes []byte) string {
	sum := sha256.Sum256(bytes)
	return hex.EncodeToString(sum[:])
}

func keyTyping(store *objectstore.Store) assertion {
	checks := []check{
		{"dotdot_key_refused", errors.Is(openError(store, "../etc/passwd"), objectstore.ErrInvalidKey), nil},
		{"dotdot_inside_typed_key_refused", errors.Is(openError(store, "sealed/../x"), objectstore.ErrInvalidKey), nil},
		{"absolute_key_refused", errors.Is(openError(store, "/sealed/abc"), objectstore.ErrInvalidKey), nil},
		{"wrong_length_refused", errors.Is(openError(store, "staging/"+strings.Repeat("a", 63)), objectstore.ErrInvalidKey), nil},
		{"uppercase_refused", errors.Is(openError(store, "staging/"+strings.Repeat("A", 64)), objectstore.ErrInvalidKey), nil},
		{"delete_traversal_refused", errors.Is(store.Delete("../../etc/passwd"), objectstore.ErrInvalidKey), nil},
	}
	return newAssertion(
		"key_typing_and_traversal_refused",
		"only typed 64-hex staging/sealed keys resolve; traversal and absolute paths are invalid",
		checks,
		map[string]any{"valid_key_shape": "staging|<64 lowercase hex> or sealed|<64 lowercase hex>"},
	)
}

func openError(store *objectstore.Store, key string) error {
	_, _, err := store.Open(key)
	return err
}

func stagingSealedSeparation(store *objectstore.Store) assertion {
	staging, err := objectstore.StagingKey()
	if err != nil {
		return failedAssertion("staging_sealed_separation", "generate staging key", err)
	}
	sealedBefore, err := objectstore.SealedKey()
	if err != nil {
		return failedAssertion("staging_sealed_separation", "generate sealed key", err)
	}
	sealedBody := []byte("sealed object the staging writes must never touch")
	first := []byte("staging first bytes")
	second := []byte("staging second bytes, longer than the first")
	sealed, err := store.CreateSealed(bytes.NewReader(sealedBody), 0)
	if err != nil {
		return failedAssertion("staging_sealed_separation", "create sealed", err)
	}
	if _, err := store.PutStaging(staging, bytes.NewReader(first), objectstore.MaxObjectBytes); err != nil {
		return failedAssertion("staging_sealed_separation", "first staging put", err)
	}
	if _, err := store.PutStaging(staging, bytes.NewReader(second), objectstore.MaxObjectBytes); err != nil {
		return failedAssertion("staging_sealed_separation", "second staging put", err)
	}
	sealedAfter, err := store.Hash(sealed.Key)
	if err != nil {
		return failedAssertion("staging_sealed_separation", "hash sealed", err)
	}
	stagingAfter, err := store.Hash(staging)
	if err != nil {
		return failedAssertion("staging_sealed_separation", "hash staging", err)
	}
	checks := []check{
		{"sealed_bytes_unchanged", sealedAfter.SHA256 == sha(sealedBody), map[string]any{
			"before": sealed.SHA256, "after": sealedAfter.SHA256, "expected": sha(sealedBody),
		}},
		{"staging_holds_latest", stagingAfter.SHA256 == sha(second), map[string]any{
			"staging_sha256": stagingAfter.SHA256,
		}},
		{"staging_cannot_move_into_sealed", errors.Is(store.Move(staging, sealedBefore), objectstore.ErrInvalidKey), nil},
		{"sealed_move_stays_sealed", func() bool {
			destination, keyErr := objectstore.SealedKey()
			if keyErr != nil {
				return false
			}
			moveErr := store.Move(sealed.Key, destination)
			if moveErr != nil {
				return false
			}
			moved, hashErr := store.Hash(destination)
			return hashErr == nil && moved.SHA256 == sha(sealedBody)
		}(), nil},
	}
	return newAssertion(
		"staging_sealed_separation",
		"overwriting staging cannot change sealed bytes; staging cannot be promoted by key",
		checks,
		map[string]any{"staging_key": staging, "sealed_key": sealed.Key},
	)
}

func sealedWriteBoundary(store *objectstore.Store) assertion {
	sealed, err := objectstore.SealedKey()
	if err != nil {
		return failedAssertion("sealed_write_boundary", "generate sealed key", err)
	}
	staging, err := objectstore.StagingKey()
	if err != nil {
		return failedAssertion("sealed_write_boundary", "generate staging key", err)
	}
	_, putStagingErr := store.PutStaging(sealed, bytes.NewReader([]byte("x")), 0)
	_, putSealedErr := store.PutSealed(staging, bytes.NewReader([]byte("x")), 0)
	checks := []check{
		{"put_staging_refuses_sealed_key", errors.Is(putStagingErr, objectstore.ErrInvalidKey), nil},
		{"put_sealed_refuses_staging_key", errors.Is(putSealedErr, objectstore.ErrInvalidKey), nil},
	}
	return newAssertion(
		"sealed_write_boundary",
		"the local store refuses cross-namespace writes; sealed bytes are server-only",
		checks,
		map[string]any{"sealed_key": sealed, "staging_key": staging},
	)
}

func signedLinkBoundaries() assertion {
	const (
		linkTTL      = 5 * time.Second
		expiryMargin = 1 * time.Second // strictly past the TTL: no boundary race
	)
	// NewSigner applies strings.TrimSpace. A key built from raw random bytes can
	// begin/end with a whitespace byte (~4-6% of runs) and then trim below
	// MinSigningKeyBytes, failing the probe for a probe-input reason. Encode the
	// randomness as hex so trimming is a no-op and every run takes the same
	// configured-and-valid path; the trim behaviour itself is asserted below.
	rawKey := make([]byte, 32)
	if _, err := rand.Read(rawKey); err != nil {
		return failedAssertion("signed_link_boundaries", "generate signing key", err)
	}
	signingKey := hex.EncodeToString(rawKey)
	if strings.TrimSpace(signingKey) != signingKey || len(signingKey) < objectstore.MinSigningKeyBytes {
		return failedAssertion(
			"signed_link_boundaries",
			"generated signing key is not trim-stable",
			fmt.Errorf("key has %d bytes after TrimSpace", len(strings.TrimSpace(signingKey))),
		)
	}
	signer, err := objectstore.NewSigner(signingKey, linkTTL)
	if err != nil {
		return failedAssertion("signed_link_boundaries", "new signer", err)
	}
	// Deterministic trim regression: a padded but long-enough key is accepted
	// after TrimSpace, while a key whose effective length falls below the
	// minimum is a clean configuration refusal (not a crash, not a spurious
	// pass). This is the property the random-key bug violated.
	paddedSigner, paddedErr := objectstore.NewSigner(" \n\t"+signingKey+" \t\n", linkTTL)
	var paddedSignErr error
	if paddedErr == nil && paddedSigner != nil {
		_, _, paddedSignErr = paddedSigner.Sign(
			objectstore.OpPut, "staging/"+strings.Repeat("a", 64), uuid.NewString(), 1,
		)
	}
	trimmedShortKey := " " + strings.Repeat("k", objectstore.MinSigningKeyBytes-1)
	_, trimmedShortErr := objectstore.NewSigner(trimmedShortKey, linkTTL)
	artifactID := uuid.NewString()
	staging, _ := objectstore.StagingKey()
	sealed, _ := objectstore.SealedKey()

	signedAt := time.Now()
	putToken, _, putErr := signer.Sign(objectstore.OpPut, staging, artifactID, 100)
	_, putClaimsErr := signer.Verify(putToken, objectstore.OpPut)
	_, wrongOpErr := signer.Verify(putToken, objectstore.OpGet)
	_, _, sealedPutErr := signer.Sign(objectstore.OpPut, sealed, artifactID, 100)
	_, _, stagingGetErr := signer.Sign(objectstore.OpGet, staging, artifactID, 100)
	getToken, _, getErr := signer.Sign(objectstore.OpGet, sealed, artifactID, 100)
	_, getClaimsErr := signer.Verify(getToken, objectstore.OpGet)
	_, _, oversizedErr := signer.Sign(objectstore.OpGet, sealed, artifactID, objectstore.MaxObjectBytes+1)
	acceptedWithin := time.Since(signedAt)
	time.Sleep(linkTTL + expiryMargin)
	_, expiredErr := signer.Verify(putToken, objectstore.OpPut)

	_, shortKeyErr := objectstore.NewSigner("too-short", time.Second)
	unconfigured, nilErr := objectstore.NewSigner("", time.Second)
	_, _, unconfiguredSignErr := unconfigured.Sign(objectstore.OpPut, staging, artifactID, 1)
	_, nilVerifyErr := (*objectstore.Signer)(nil).Verify("anything", objectstore.OpPut)

	checks := []check{
		{"generated_signing_key_is_trim_stable", strings.TrimSpace(signingKey) == signingKey, map[string]any{
			"key_chars": len(signingKey),
		}},
		{"whitespace_padded_key_is_accepted_after_trim", paddedErr == nil && paddedSigner != nil && paddedSignErr == nil, map[string]any{
			"padded_key_error": fmt.Sprint(paddedErr),
		}},
		{"whitespace_trimmed_below_minimum_refused", trimmedShortErr != nil, map[string]any{
			"effective_bytes": objectstore.MinSigningKeyBytes - 1,
		}},
		{"put_link_minted_for_staging", putErr == nil && putToken != "", nil},
		{"put_link_verifies_inside_ttl", putClaimsErr == nil && acceptedWithin < linkTTL, map[string]any{
			"verified_after_ms": acceptedWithin.Milliseconds(), "ttl_ms": linkTTL.Milliseconds(),
		}},
		{"put_link_cannot_be_used_as_get", errors.Is(wrongOpErr, objectstore.ErrLinkInvalid), nil},
		{"sealed_put_link_cannot_be_minted", errors.Is(sealedPutErr, objectstore.ErrLinkInvalid), nil},
		{"staging_get_link_cannot_be_minted", errors.Is(stagingGetErr, objectstore.ErrLinkInvalid), nil},
		{"get_link_minted_and_verified", getErr == nil && getClaimsErr == nil, nil},
		{"max_bytes_cannot_exceed_cap", errors.Is(oversizedErr, objectstore.ErrLinkInvalid), nil},
		{"link_expires_past_ttl_plus_margin", errors.Is(expiredErr, objectstore.ErrLinkInvalid), nil},
		{"short_signing_key_refused", shortKeyErr != nil && unconfigured == nil && nilErr == nil, nil},
		{"unconfigured_signer_fails_closed", errors.Is(unconfiguredSignErr, objectstore.ErrSignerNotConfigured), nil},
		{"nil_signer_fails_closed", errors.Is(nilVerifyErr, objectstore.ErrSignerNotConfigured), nil},
	}
	return newAssertion(
		"signed_link_boundaries",
		"HMAC links expire, bind one operation and one key, and fail closed when unconfigured",
		checks,
		map[string]any{
			"ttl_seconds":           linkTTL.Seconds(),
			"expiry_margin_seconds": expiryMargin.Seconds(),
			"verified_after_ms":     acceptedWithin.Milliseconds(),
			"signing_key_bytes":     len(signingKey),
			"signing_key_encoding":  "hex",
		},
	)
}

func deleteSemantics(store *objectstore.Store) assertion {
	first, err := store.CreateSealed(bytes.NewReader([]byte("first sealed object")), 0)
	if err != nil {
		return failedAssertion("delete_semantics", "create first sealed", err)
	}
	second, err := store.CreateSealed(bytes.NewReader([]byte("sibling sealed object")), 0)
	if err != nil {
		return failedAssertion("delete_semantics", "create sibling sealed", err)
	}
	deleteErr := store.Delete(first.Key)
	secondDeleteErr := store.Delete(first.Key)
	_, _, openErr := store.Open(first.Key)
	siblingReader, _, siblingErr := store.Open(second.Key)
	siblingOK := false
	if siblingErr == nil {
		body, readErr := io.ReadAll(siblingReader)
		siblingOK = readErr == nil && string(body) == "sibling sealed object"
		_ = siblingReader.Close()
	}
	checks := []check{
		{"delete_succeeds", deleteErr == nil, nil},
		{"repeated_delete_is_idempotent", secondDeleteErr == nil, nil},
		{"deleted_key_is_not_found", errors.Is(openErr, objectstore.ErrNotExist), nil},
		{"sibling_object_untouched", siblingOK, nil},
	}
	return newAssertion(
		"delete_idempotent_and_sibling_safe",
		"delete is idempotent and never removes a sibling object",
		checks,
		map[string]any{"deleted_key": first.Key, "sibling_key": second.Key},
	)
}

func sizeCap(store *objectstore.Store) assertion {
	before, _ := store.List("staging/")
	oversized := io.LimitReader(zeroReader{}, objectstore.MaxObjectBytes+1)
	_, err := store.CreateStaging(oversized, 0)
	after, _ := store.List("staging/")
	_, narrowedErr := store.CreateStaging(bytes.NewReader(make([]byte, 32)), 16)
	checks := []check{
		{"oversized_write_refused", errors.Is(err, objectstore.ErrTooLarge), nil},
		{"no_partial_object_left", len(before) == len(after), map[string]any{
			"objects_before": len(before), "objects_after": len(after),
		}},
		{"narrowed_limit_refused", errors.Is(narrowedErr, objectstore.ErrTooLarge), nil},
	}
	return newAssertion(
		"size_cap_enforced",
		"bytes over MaxObjectBytes are refused with no partial object",
		checks,
		map[string]any{"max_object_bytes": objectstore.MaxObjectBytes},
	)
}

type zeroReader struct{}

func (zeroReader) Read(p []byte) (int, error) {
	for i := range p {
		p[i] = 0
	}
	return len(p), nil
}

func failedAssertion(id, stage string, err error) assertion {
	return assertion{
		ID:     id,
		Title:  id,
		Status: "failed",
		Reason: fmt.Sprintf("%s: %v", stage, err),
		Checks: []check{{Name: stage, OK: false, Detail: map[string]any{"error": err.Error()}}},
	}
}
