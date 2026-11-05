// Package routing is the F08/T079 control-plane router between a submitted
// natural-language request and exactly one Run.
//
// The router is deliberately narrow (ROUTING-MEMORY-KNOWLEDGE.md §5/§7):
//
//   - the candidate set is computed by an authorized SQL query that applies the
//     owner-or-grant predicate, `revoked_at` and `archived_at` *before* the LIMIT;
//     the full catalog is never materialized and hidden later;
//   - the model only suggests a member of that set (or asks a clarifying question,
//     or says there is no match). Every suggestion is validated server-side
//     against the persisted candidate snapshot, the current version/ACL state and
//     the required inputs; a suggestion outside the set is a typed refusal and
//     creates no Run;
//   - every control-model call is one governed `platform_model_calls` row (the
//     ledger's XOR owner is the `route_request` background job), so a retried
//     handler cannot reset the three-call budget and a call cannot be invisible;
//   - interactions carry a correlation id, are sealed as a proposal artifact and
//     expire; expiry releases the conversation slot through the same queue seam
//     T074 publishes, and an old reply can never resurrect a failed request.
//
// No score or probability ever leaves this package: candidates carry an ordinal
// rank, and the interaction proposal is built from the six capability-card fields
// only. ROUTING §7 says an uncalibrated score is not a probability.
package routing

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"strconv"
	"strings"
	"time"
	"unicode"

	"orchara/platform/internal/catalog"
	"orchara/platform/internal/jcs"
	"orchara/platform/internal/modelproxy"
	"orchara/platform/internal/objectstore"
	"orchara/platform/internal/policy"
	"orchara/platform/internal/requests"
	"orchara/platform/internal/uuid"
)

// InteractionTTL is the lifetime of a pending input/selection interaction. The
// spec fixes no number; this is the same 15-minute deployment default T074
// documents, reused so the two packages cannot disagree about when a pending
// request stops holding the conversation slot.
const InteractionTTL = requests.InteractionTTL

// ControlBudget is the maximum number of control-model attempts one request may
// spend (IMPLEMENTATION-DETAILS.md §45). A failed physical attempt is a ledger row
// and therefore counts; the report states that choice.
const ControlBudget = 3

// MaxCandidates bounds how many authorized candidates one routing round offers.
// A larger set is not "more choice": it is a search failure, and the model prompt
// and the selection interaction both stay bounded.
const MaxCandidates = 8

// Stable decision classes. `explicit_version` and `degraded` are the server's own
// rows, never model output.
const (
	DecisionSelect   = "select"
	DecisionClarify  = "clarify"
	DecisionNoMatch  = "no_match"
	DecisionExplicit = "explicit_version"
	DecisionDegraded = "degraded"
)

// Stable request error codes written by this package. They cross the report and
// the tests, so they are matched verbatim.
const (
	CodeNoMatchingApplication  = "no_matching_application"
	CodeRouteOutputInvalid     = "route_output_invalid"
	CodeRouteCandidateInvalid  = "route_candidate_invalid"
	CodeRouteBudgetExhausted   = "route_budget_exhausted"
	CodeRouteModelUnconfigured = "route_model_unconfigured"
	CodeRouteModelUnavailable  = "route_model_unavailable"
	CodeSelectedVersionInvalid = "selected_version_invalid"
)

// Artifact purpose of a sealed interaction proposal. Non-contract diagnostic
// label; the bytes are a projection of the interaction, never a second authority.
const PurposeInteractionProposal = "interaction_proposal"

// TransientError marks a control-model failure the background job may retry
// (bounded by the job retry table). A business refusal is committed on the request
// instead and never retried.
type TransientError struct{ Err error }

func (e *TransientError) Error() string { return e.Err.Error() }
func (e *TransientError) Unwrap() error { return e.Err }

// IsTransient reports whether err is a retryable routing failure.
func IsTransient(err error) bool {
	var transient *TransientError
	return errors.As(err, &transient)
}

// Outcome is the user-visible result of one routing round.
type Outcome struct {
	RequestID      string
	ConversationID string
	State          string
	Result         string // dispatched | awaiting_input | awaiting_selection | failed | noop
	ErrorCode      string
	InteractionID  string
	CorrelationID  string
	RunID          string
	ControlCalls   int
}

// Service owns candidate retrieval, the control-model call and the one
// interaction-or-Run decision. Queue operations stay in internal/requests; this
// package calls the published seam instead of duplicating it.
type Service struct {
	DB      *sql.DB
	Catalog *catalog.Reader
	Objects *objectstore.Store
	Models  *modelproxy.Service
	// Queue is the T074 conversation/request aggregate. It supplies the candidate
	// reply's explicit-version dispatch, the interaction expiry and the queue
	// advance after a routing failure.
	Queue *requests.Service
	// Logger receives process diagnostics; nil discards.
	Logger *log.Logger
}

// New builds the router. Objects and Models may be nil: routing then fails closed
// with a typed request failure instead of inventing a decision or a Run.
func New(db *sql.DB, reader *catalog.Reader, objects *objectstore.Store,
	models *modelproxy.Service, queue *requests.Service) *Service {
	return &Service{DB: db, Catalog: reader, Objects: objects, Models: models, Queue: queue}
}

// Candidate is one authorized, published, unrevoked version the model may choose.
// Rank is an ordinal (1-based); there is no score field on purpose.
type Candidate struct {
	VersionID       string
	ApplicationID   string
	ApplicationName string
	VersionNo       int64
	ManifestSHA256  string
	Card            json.RawMessage
	Rank            int
}

// candidatesTx reads the authorized candidate set. Every filter runs inside the
// SQL before ORDER BY/LIMIT: the ACL predicate, archived, revoked, a non-empty card
// and a token match against the user message. The rank is the ordinal position in
// that deterministic order.
func candidatesTx(
	ctx context.Context, q queryer, principalID, message string, limit int,
) ([]Candidate, error) {
	tokens := messageTokens(message)
	if len(tokens) == 0 {
		return nil, nil
	}
	patterns := make([]string, 0, len(tokens))
	for _, token := range tokens {
		patterns = append(patterns, "%"+likeEscape(token)+"%")
	}
	rows, err := q.QueryContext(ctx, `
		SELECT v.id::text, v.application_id::text, a.name, v.version_no,
		       v.manifest_sha256, v.capability_card::text,
		       CASE WHEN (v.capability_card->>'purpose') ILIKE ANY($1::text[])
		            THEN 0 ELSE 1 END AS match_rank
		  FROM platform.application_versions AS v
		  JOIN platform.applications AS a ON a.id = v.application_id
		 WHERE a.archived_at IS NULL
		   AND v.revoked_at IS NULL
		   AND v.capability_card IS NOT NULL
		   AND v.capability_card <> '{}'::jsonb
		   AND (
		        v.capability_card::text ILIKE ANY($1::text[])
		        OR to_tsvector('simple', v.capability_card::text)
		           @@ plainto_tsquery('simple', $2)
		       )
		   AND `+catalog.ApplicationReadPredicate(3, 5, 6, 7)+`
		 ORDER BY match_rank ASC, v.version_no DESC, v.id DESC
		 LIMIT $4`,
		patterns, message, principalID, limit, timeNow(),
		policy.ResourceApplication, policy.ActionRead,
	)
	if err != nil {
		return nil, fmt.Errorf("searching the authorized candidate catalog: %w", err)
	}
	defer rows.Close()
	candidates := make([]Candidate, 0, limit)
	rank := 0
	for rows.Next() {
		var (
			candidate Candidate
			cardText  string
			matchRank int
		)
		if err := rows.Scan(&candidate.VersionID, &candidate.ApplicationID,
			&candidate.ApplicationName, &candidate.VersionNo, &candidate.ManifestSHA256,
			&cardText, &matchRank); err != nil {
			return nil, fmt.Errorf("scanning an authorized candidate: %w", err)
		}
		_ = matchRank
		rank++
		candidate.Rank = rank
		candidate.Card = json.RawMessage(cardText)
		candidates = append(candidates, candidate)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("reading the authorized candidate catalog: %w", err)
	}
	return candidates, nil
}

// queryer is the shared read seam (DB or transaction).
type queryer interface {
	QueryContext(ctx context.Context, query string, args ...any) (*sql.Rows, error)
	QueryRowContext(ctx context.Context, query string, args ...any) *sql.Row
}

// timeNow is the process clock seam. Candidate authorization compares grant
// expiry; the API's policy clock is the process clock, so the router uses the same
// authority rather than PostgreSQL's now() for the ACL half.
var timeNow = func() time.Time { return time.Now().UTC() }

// messageTokens turns a natural-language request into bounded literal search
// tokens. Whitespace/punctuation split for space-delimited text; a CJK run also
// contributes its 2- and 3-grams so a Chinese sentence can still match a Chinese
// capability card without a segmenter. Everything is compared literally (LIKE
// wildcards are escaped), so a user token can never widen the match.
func messageTokens(message string) []string {
	raw := strings.ToLower(strings.TrimSpace(message))
	if raw == "" {
		return nil
	}
	var (
		tokens []string
		runes  []rune
	)
	flush := func() {
		if len(runes) == 0 {
			return
		}
		word := string(runes)
		if runeLen(word) >= 2 && !stopword(word) {
			tokens = append(tokens, word)
		}
		if isCJK(runes) {
			grams := []rune(word)
			for size := 2; size <= 3; size++ {
				for start := 0; start+size <= len(grams); start++ {
					tokens = append(tokens, string(grams[start:start+size]))
				}
			}
		}
		runes = runes[:0]
	}
	for _, char := range raw {
		if unicode.IsLetter(char) || unicode.IsDigit(char) {
			runes = append(runes, char)
			continue
		}
		flush()
	}
	flush()

	seen := make(map[string]bool, len(tokens))
	out := make([]string, 0, len(tokens))
	for _, token := range tokens {
		if seen[token] || runeLen(token) > 64 {
			continue
		}
		seen[token] = true
		out = append(out, token)
		if len(out) >= 40 {
			break
		}
	}
	return out
}

func isCJK(runes []rune) bool {
	for _, char := range runes {
		if unicode.Is(unicode.Han, char) {
			return true
		}
	}
	return false
}

func runeLen(value string) int { return len([]rune(value)) }

var stopwords = map[string]bool{
	"the": true, "and": true, "for": true, "with": true, "please": true,
	"this": true, "that": true, "from": true, "use": true, "using": true,
	"make": true, "create": true, "want": true, "need": true, "help": true,
	"you": true, "your": true, "can": true, "could": true, "would": true,
	"a": true, "an": true, "of": true, "to": true, "in": true, "on": true,
	"is": true, "are": true, "it": true, "me": true, "my": true, "we": true,
}

func stopword(value string) bool { return stopwords[value] }

// likeEscape makes one token literal inside a LIKE/ILIKE pattern.
func likeEscape(value string) string {
	return strings.NewReplacer("\\", "\\\\", "%", "\\%", "_", "\\_").Replace(value)
}

// promptPayload is the exact server-built routing prompt handed to the control
// model. It carries the six capability-card fields and no score: the model is
// asked to choose a member, not to rank by an uncalibrated number.
type promptPayload struct {
	Task       string            `json:"task"`
	Message    string            `json:"message"`
	Candidates []promptCandidate `json:"candidates"`
}

type promptCandidate struct {
	Rank                 int             `json:"rank"`
	ApplicationVersionID string          `json:"application_version_id"`
	ApplicationID        string          `json:"application_id"`
	ApplicationName      string          `json:"application_name"`
	VersionNo            int64           `json:"version_no"`
	CapabilityCard       json.RawMessage `json:"capability_card"`
}

// buildPrompt renders the deterministic control prompt bytes. The bytes are
// sealed nowhere: the model ledger keeps the result artifact, and the candidate
// snapshot keeps the allowed set. Nothing here is user-visible HTML.
func buildPrompt(message string, candidates []Candidate) ([]byte, error) {
	payload := promptPayload{Task: "route", Message: message,
		Candidates: make([]promptCandidate, 0, len(candidates))}
	for _, candidate := range candidates {
		payload.Candidates = append(payload.Candidates, promptCandidate{
			Rank:                 candidate.Rank,
			ApplicationVersionID: candidate.VersionID,
			ApplicationID:        candidate.ApplicationID,
			ApplicationName:      candidate.ApplicationName,
			VersionNo:            candidate.VersionNo,
			CapabilityCard:       candidate.Card,
		})
	}
	raw, err := json.Marshal(payload)
	if err != nil {
		return nil, fmt.Errorf("building the routing prompt: %w", err)
	}
	return raw, nil
}

// cardRequiredInputs extracts `required_inputs` from a validated capability card.
func cardRequiredInputs(card json.RawMessage) []string {
	var value struct {
		RequiredInputs []string `json:"required_inputs"`
	}
	if err := json.Unmarshal(card, &value); err != nil {
		return nil
	}
	out := make([]string, 0, len(value.RequiredInputs))
	for _, input := range value.RequiredInputs {
		if strings.TrimSpace(input) != "" {
			out = append(out, input)
		}
	}
	return out
}

// missingInputs is the conservative deterministic input check the spec leaves to
// the server: each declared required input needs one explicitly attached artifact;
// the first N required inputs are covered by N attachments. A message-only reply is
// additional context, not a typed input, so it never silently satisfies a required
// field. The T079 report/ADR state this limitation.
func missingInputs(required []string, artifactIDs []string) []string {
	if len(required) == 0 {
		return nil
	}
	unique := make(map[string]bool, len(artifactIDs))
	count := 0
	for _, id := range artifactIDs {
		if !unique[id] {
			unique[id] = true
			count++
		}
	}
	if count >= len(required) {
		return nil
	}
	return append([]string(nil), required[count:]...)
}

// sealJSON writes one sealed server-side proposal object and returns its identity.
func (s *Service) sealJSON(data []byte) (key, sha string, size int64, err error) {
	if s.Objects == nil {
		return "", "", 0, errors.New("the object store is not configured")
	}
	key, err = objectstore.SealedKey()
	if err != nil {
		return "", "", 0, err
	}
	digest := jcs.SHA256Hex(data)
	object, err := s.Objects.PutSealed(key, strings.NewReader(string(data)), int64(len(data)))
	if err != nil {
		return "", "", 0, fmt.Errorf("sealing the interaction proposal: %w", err)
	}
	if object.SHA256 != digest || object.Size != int64(len(data)) {
		_ = s.Objects.Delete(key)
		return "", "", 0, errors.New("the sealed interaction proposal does not match its bytes")
	}
	return key, digest, int64(len(data)), nil
}

// discard deletes one sealed object after a failed commit.
func (s *Service) discard(key string) {
	if s.Objects != nil && key != "" {
		_ = s.Objects.Delete(key)
	}
}

// insertArtifactTx registers one ready sealed artifact row.
func insertArtifactTx(
	ctx context.Context, tx *sql.Tx, artifactID, ownerID, objectKey, purpose,
	mediaType, sha string, size int64,
) error {
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO platform.artifacts
		       (id, owner_id, object_key, purpose, sha256, media_type, size_bytes, state, revision)
		VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7, 'ready', 1)`,
		artifactID, ownerID, objectKey, purpose, sha, mediaType, size,
	); err != nil {
		return fmt.Errorf("registering the sealed interaction proposal: %w", err)
	}
	return nil
}

// insertInteractionTx writes the pending interaction, its correlation id and the
// proposal artifact row, then moves the request to the matching awaiting state.
func insertInteractionTx(
	ctx context.Context, tx *sql.Tx, requestID, kind, state string,
	proposalArtifactID, interactionID, correlationID string, ttl time.Duration,
) error {
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO platform.interactions
		       (id, request_id, kind, state, proposal_artifact_id, correlation_id,
		        expires_at, created_at)
		VALUES ($1::uuid, $2::uuid, $3, 'pending', $4::uuid, $5::uuid,
		        now() + make_interval(secs => $6), now())`,
		interactionID, requestID, kind, proposalArtifactID, correlationID,
		ttl.Seconds(),
	); err != nil {
		return fmt.Errorf("creating the pending interaction: %w", err)
	}
	if _, err := tx.ExecContext(ctx, `
		UPDATE platform.requests
		   SET state = $2, error_code = NULL, revision = revision + 1, updated_at = now()
		 WHERE id = $1::uuid AND state = 'routing'`,
		requestID, state,
	); err != nil {
		return fmt.Errorf("moving the request into the interaction state: %w", err)
	}
	return nil
}

// registerInteraction seals the proposal, inserts the interaction and cleans the
// sealed object if the transaction rolls back.
func (s *Service) registerInteraction(
	ctx context.Context, tx *sql.Tx, requestID, ownerID, kind, state string,
	proposal []byte, ttl time.Duration,
) (interactionID, correlationID string, err error) {
	key, sha, size, err := s.sealJSON(proposal)
	if err != nil {
		return "", "", err
	}
	interactionID = uuid.NewString()
	correlationID = uuid.NewString()
	artifactID := uuid.NewString()
	if err := insertArtifactTx(ctx, tx, artifactID, ownerID, key,
		PurposeInteractionProposal, "application/json", sha, size); err != nil {
		s.discard(key)
		return "", "", err
	}
	if err := insertInteractionTx(ctx, tx, requestID, kind, state,
		artifactID, interactionID, correlationID, ttl); err != nil {
		s.discard(key)
		return "", "", err
	}
	return interactionID, correlationID, nil
}

// interactionRef is the pending interaction lookup used for the retry guard.
type interactionRef struct {
	ID            string
	CorrelationID string
	Kind          string
	State         string
}

// pendingInteractionTx is the "a retry first looks up the original interaction"
// guard: a handler that resumes after a crash must not create a second one.
func pendingInteractionTx(
	ctx context.Context, tx *sql.Tx, requestID string,
) (*interactionRef, error) {
	var ref interactionRef
	err := tx.QueryRowContext(ctx, `
		SELECT id::text, correlation_id::text, kind
		  FROM platform.interactions
		 WHERE request_id = $1::uuid AND state = 'pending'
		 ORDER BY created_at DESC, id DESC
		 LIMIT 1`, requestID,
	).Scan(&ref.ID, &ref.CorrelationID, &ref.Kind)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("reading the pending interaction: %w", err)
	}
	ref.State = requests.InteractionPending
	return &ref, nil
}

// runForRequestTx is the other half of the retry guard: a Run already created for
// this request means the routing round is done.
func runForRequestTx(ctx context.Context, tx *sql.Tx, requestID string) (string, error) {
	var runID string
	err := tx.QueryRowContext(ctx, `
		SELECT id::text FROM platform.runs
		 WHERE request_id = $1::uuid AND deleted_at IS NULL
		 ORDER BY created_at, id LIMIT 1`, requestID).Scan(&runID)
	if errors.Is(err, sql.ErrNoRows) {
		return "", nil
	}
	if err != nil {
		return "", fmt.Errorf("reading the routed run: %w", err)
	}
	return runID, nil
}

// controlCallsTx counts the real control-model attempts attributed to this
// request: every `route_request` background job whose target is the request, plus
// its `platform_model_calls` rows. The ledger is the authority because a rolled
// back transaction cannot un-bill a physical attempt; a failed attempt is a row
// and counts (report §deviation).
func controlCallsTx(ctx context.Context, tx *sql.Tx, requestID string) (int, error) {
	var calls int
	err := tx.QueryRowContext(ctx, `
		SELECT count(*)
		  FROM platform.platform_model_calls AS c
		  JOIN platform.background_jobs AS j ON j.id = c.background_job_id
		 WHERE j.kind = 'route_request'
		   AND j.target_id = $1::uuid`, requestID).Scan(&calls)
	if err != nil {
		return 0, fmt.Errorf("counting the control-model calls: %w", err)
	}
	return calls, nil
}

// decisionAttemptTx is the next routing round number (1-based).
func decisionAttemptTx(ctx context.Context, tx *sql.Tx, requestID string) (int, error) {
	var attempt int
	err := tx.QueryRowContext(ctx, `
		SELECT coalesce(max(attempt), 0) + 1
		  FROM platform.request_routing_decisions
		 WHERE request_id = $1::uuid`, requestID).Scan(&attempt)
	if err != nil {
		return 0, fmt.Errorf("reading the routing round: %w", err)
	}
	return attempt, nil
}

// roundLabel is the stable per-round logical key of the control call. The same
// round replays its recorded call instead of billing a second one.
func roundLabel(requestID string, attempt int) string {
	return "route:" + requestID + ":" + strconv.Itoa(attempt)
}
