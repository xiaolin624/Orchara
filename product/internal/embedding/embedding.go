// Package embedding is the F10/T055 embedding seam.
//
// Two paths exist and are recorded separately:
//
//   - **dev-stub**: a deterministic hash embedding, fixed at 384 dimensions and
//     stable across processes. It exists so the ingestion/retrieval pipeline can
//     be exercised end to end without a model credential. Its model revision is
//     `local-hash-v1`, and the deployment profile that can produce it is the
//     documented `dev-stub` profile only.
//   - **real**: any configured provider (`ORCHARA_EMBEDDING_PROVIDER` set to
//     anything other than empty/stub) takes the real path. This build has no
//     provider adapter and the development environment has no embedding
//     credential, so the real path fails closed with ErrNotConfigured
//     (`embedding_provider_not_configured`) instead of quietly falling back to
//     the stub. A document ingested with the stub is therefore never presented
//     as if it had real embeddings.
//
// The stub is deliberately simple and inspectable: lowercase ASCII/Unicode
// alphanumeric runs are tokens; each token's SHA-256 selects one dimension and a
// sign; the vector is the summed term frequency, L2-normalised. No map
// iteration, no process salt and no floating-point randomness is involved, so
// two processes produce byte-identical vectors for the same text.
package embedding

import (
	"context"
	"crypto/sha256"
	"encoding/binary"
	"errors"
	"math"
	"os"
	"strings"
	"unicode"
)

// Environment variables read by FromEnv. They are deployment configuration, not
// per-request values.
const (
	ProviderEnv = "ORCHARA_EMBEDDING_PROVIDER"
	ModelEnv    = "ORCHARA_EMBEDDING_MODEL"
)

const (
	// StubModelRevision is the recorded model revision of the deterministic local
	// embedder. A real provider gets its own revision; the two are never mixed.
	StubModelRevision = "local-hash-v1"
	// StubModelRevisionV2 is the second documented deterministic dev-stub
	// revision (T061). It exists so a *real deployment configuration change* —
	// start the API/maintenance with ORCHARA_EMBEDDING_MODEL=local-hash-v2 — can
	// produce a genuinely different vector space without a model provider. It is
	// still a labelled dev-stub, not a semantic model; a real provider keeps its
	// own revision and never falls back to either stub.
	StubModelRevisionV2 = "local-hash-v2"
	// Dimensions is the one fixed vector dimension of the local embedder. The
	// migration pins the same 384 in a CHECK, so the constant and the database
	// cannot disagree silently. Changing it is a new model revision and a
	// migration (T061/T064 own any real-provider dimension).
	Dimensions = 384
	// StubDimensions is kept as the documented alias used by tests.
	StubDimensions = Dimensions
	// stubProvider is the only provider name that selects the stub explicitly.
	stubProvider = "stub"
)

// ErrNotConfigured means the real embedding path was selected but this build has
// no provider adapter or credential. It is a business failure for ingestion: the
// same document cannot succeed on retry until an operator configures a provider.
var ErrNotConfigured = errors.New("embedding provider is not configured")

// Service is the process-wide embedding seam. The zero value is not usable;
// construct it with New or FromEnv.
type Service struct {
	provider      string
	modelRevision string
	dimensions    int
	stub          bool
}

// New builds the service from deployment configuration.
//
// Empty or `stub` selects a deterministic stub. The model may name a compiled
// stub revision (`local-hash-v1`, the default, or `local-hash-v2`); an unknown
// stub revision is refused (fail closed) rather than silently recording one
// revision while producing another model's vectors. Every other provider value
// is a real provider and fails closed: no adapter and no credential exist in
// this environment, and pretending otherwise would record fake vectors under a
// real model revision.
func New(provider, modelModel string) *Service {
	normalized := strings.ToLower(strings.TrimSpace(provider))
	if normalized == "" || normalized == stubProvider {
		revision := strings.TrimSpace(modelModel)
		if revision == "" {
			revision = StubModelRevision
		}
		if revision != StubModelRevision && revision != StubModelRevisionV2 {
			return &Service{
				provider:      stubProvider,
				modelRevision: revision,
				dimensions:    0,
				stub:          false,
			}
		}
		return &Service{
			provider:      stubProvider,
			modelRevision: revision,
			dimensions:    StubDimensions,
			stub:          true,
		}
	}
	revision := strings.TrimSpace(modelModel)
	if revision == "" {
		revision = normalized
	}
	return &Service{
		provider:      normalized,
		modelRevision: revision,
		dimensions:    0,
		stub:          false,
	}
}

// FromEnv reads ORCHARA_EMBEDDING_PROVIDER / ORCHARA_EMBEDDING_MODEL.
func FromEnv() *Service {
	return New(os.Getenv(ProviderEnv), os.Getenv(ModelEnv))
}

// IsStub reports whether this service produces the deterministic stub vector.
func (s *Service) IsStub() bool { return s != nil && s.stub }

// Provider returns the configured provider name (never empty).
func (s *Service) Provider() string {
	if s == nil {
		return stubProvider
	}
	return s.provider
}

// ModelRevision is the revision recorded on every chunk. A stub revision is
// never mixed with a real one: retrieval ranks only candidates whose
// embedding_model_revision equals the current service revision.
func (s *Service) ModelRevision() string {
	if s == nil {
		return StubModelRevision
	}
	return s.modelRevision
}

// Dimensions is the fixed vector length for this model revision. Zero means the
// real provider cannot be resolved here.
func (s *Service) Dimensions() int {
	if s == nil {
		return StubDimensions
	}
	return s.dimensions
}

// Embed returns one vector per text. The stub path always succeeds; the real
// path fails closed before producing any vector, because a partial result would
// let an ingestion record a mixed revision.
func (s *Service) Embed(ctx context.Context, texts []string) ([][]float64, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	if s == nil || !s.stub {
		return nil, ErrNotConfigured
	}
	vectors := make([][]float64, len(texts))
	for index, text := range texts {
		vectors[index] = stubVector(text, s.dimensions, s.modelRevision)
	}
	return vectors, nil
}

// EmbedOne is Embed for a single text.
func (s *Service) EmbedOne(ctx context.Context, text string) ([]float64, error) {
	vectors, err := s.Embed(ctx, []string{text})
	if err != nil {
		return nil, err
	}
	return vectors[0], nil
}

// StubVector is the deterministic vector function of the default stub revision
// (`local-hash-v1`). It is exported for the cross-process stability test and for
// the T059/T060 parser tests that need to compare chunk bodies without a
// provider.
func StubVector(text string, dimensions int) []float64 {
	return stubVector(text, dimensions, StubModelRevision)
}

// stubVector is the deterministic vector function of one compiled stub
// revision. The two revisions read different SHA-256 bytes for the bucket and
// sign, so they are distinct vector spaces over the same fixed dimension; this is
// how T061 exercises a real embedding-model switch without a provider. Changing
// either algorithm is a new stub revision, never a silent edit of this one.
func stubVector(text string, dimensions int, revision string) []float64 {
	indexOffset, signOffset := 0, 4
	if revision == StubModelRevisionV2 {
		indexOffset, signOffset = 8, 12
	}
	vector := make([]float64, dimensions)
	for _, token := range tokens(text) {
		sum := sha256.Sum256([]byte(token))
		index := int(binary.BigEndian.Uint32(sum[indexOffset:indexOffset+4]) % uint32(dimensions))
		weight := 1.0
		if sum[signOffset]&1 == 1 {
			weight = -1
		}
		vector[index] += weight
	}
	normalize(vector)
	return vector
}

func tokens(text string) []string {
	var (
		tokens  []string
		current strings.Builder
	)
	flush := func() {
		if current.Len() == 0 {
			return
		}
		tokens = append(tokens, strings.ToLower(current.String()))
		current.Reset()
	}
	for _, char := range text {
		if unicode.IsLetter(char) || unicode.IsDigit(char) {
			current.WriteRune(char)
			continue
		}
		flush()
	}
	flush()
	return tokens
}

func normalize(vector []float64) {
	var sum float64
	for _, value := range vector {
		sum += value * value
	}
	if sum == 0 {
		return
	}
	norm := math.Sqrt(sum)
	for index := range vector {
		vector[index] /= norm
	}
}

// Cosine is the ranking function used after the SQL ACL/current-revision filter.
// A zero-length or all-zero vector scores 0; identical unit vectors score 1.
func Cosine(left, right []float64) float64 {
	if len(left) == 0 || len(left) != len(right) {
		return 0
	}
	var dot, leftNorm, rightNorm float64
	for index := range left {
		dot += left[index] * right[index]
		leftNorm += left[index] * left[index]
		rightNorm += right[index] * right[index]
	}
	if leftNorm == 0 || rightNorm == 0 {
		return 0
	}
	return dot / (math.Sqrt(leftNorm) * math.Sqrt(rightNorm))
}
