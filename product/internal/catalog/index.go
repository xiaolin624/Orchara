package catalog

// F08/T077: the authorized purpose catalog read model.
//
// The catalog is a projection over `application_versions` (the authority) and the
// catalog outbox. It is deliberately separate from the knowledge index: it has its
// own tables, its own ACL-bearing SQL, its own rebuild ledger and its own failure
// mode. Three rules are enforced here, in this order:
//
//  1. **ACL and publication filters run inside the SQL, before ranking and LIMIT.**
//     Both the projection query and the authorized full-text fallback put the
//     caller's owner/grant predicate and the authoritative `revoked_at` /
//     `archived_at` conditions in the same WHERE as the match, so an invisible or
//     revoked row can neither consume a page slot nor hide a visible row.
//  2. **The projection is never the authority.** A page produced from
//     `application_catalog_entries` is re-checked one row at a time against the
//     authoritative version/application pair before it is returned. The fallback
//     path performs that second authorization check after its own authorized
//     full-text query, so a version revoked between the two statements is refused
//     and reported through `rejected_by_recheck`.
//  3. **A stale, absent or half-built index falls back instead of half-serving.**
//     The live eligible-version count and the live valid-entry count must agree and
//     the rebuild ledger must say `ready`; otherwise the authorized full-text path
//     answers the query and the response says `source="fallback"`, `stale=true`.
//
// The response surface is deliberately non-contract (`GET /api/v1/catalog/search`),
// because the contract's `Application` schema has no search member and
// `additionalProperties:false`. The contract's `listApplications` shape is not
// touched; ADR 0026 records the extension.

import (
	"context"
	"database/sql"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"orchara/platform/internal/policy"
	"orchara/platform/internal/uuid"
)

const (
	// MaxSearchQueryRunes bounds the non-contract `q` parameter. The contract
	// fixes neither a catalog search nor its length; this keeps one request from
	// turning into an unbounded pattern scan.
	MaxSearchQueryRunes = 200

	// CatalogIndexReady/Rebuilding/Failed are the ledger states. `absent` is the
	// read-side name for "no ledger row yet" and is never stored.
	CatalogIndexReady      = "ready"
	CatalogIndexRebuilding = "rebuilding"
	CatalogIndexFailed     = "failed"
	CatalogIndexAbsent     = "absent"

	// SourceIndex / SourceFallback are the retrieval modes reported to the caller.
	SourceIndex    = "index"
	SourceFallback = "fallback"

	// Stable rebuild failure codes. They cross the admin CLI JSON and the report
	// table, so they are matched verbatim by tests.
	CodeCatalogSourceInvalid     = "catalog_source_invalid"
	CodeCatalogSourceMissing     = "catalog_source_missing"
	CodeCatalogSourceChanged     = "catalog_source_changed"
	CodeCatalogRebuildSuperseded = "catalog_rebuild_superseded"
	CodeCatalogRebuildFailed     = "catalog_rebuild_failed"
	searchCursorPrefix           = "s1"
	searchRankPurposeMatch       = 0
	searchRankMetadataMatch      = 1
)

var (
	// ErrSearchQueryTooLong is a located 422 on the non-contract search surface.
	ErrSearchQueryTooLong = errors.New("catalog search query is too long")
	// ErrCatalogSourceInvalid marks a version whose capability card cannot be a
	// projection entry (corrupt/empty purpose). A rebuild records this code and
	// aborts; search's authoritative re-check refuses the row.
	ErrCatalogSourceInvalid = errors.New("catalog source capability card is invalid")
	// ErrCatalogSourceMissing marks a version id that no longer names a row.
	ErrCatalogSourceMissing = errors.New("catalog source version is missing")
	// ErrCatalogRebuildSuperseded means another rebuild bumped the epoch while
	// this one was running; its result must not claim the ledger.
	ErrCatalogRebuildSuperseded = errors.New("catalog rebuild was superseded by a newer epoch")
)

// ErrCatalogRebuild is the one error a failed rebuild returns, carrying the
// stable code the admin CLI prints and the failure recorded in the ledger.
type ErrCatalogRebuild struct {
	Code string
	Err  error
}

func (e *ErrCatalogRebuild) Error() string { return e.Code + ": " + e.Err.Error() }
func (e *ErrCatalogRebuild) Unwrap() error { return e.Err }

// CatalogHit is one search result: a published, currently readable version and
// the six-field capability card that was read back from the authoritative row.
type CatalogHit struct {
	ApplicationID   string
	ApplicationName string
	VersionID       string
	VersionNo       int64
	ManifestSHA256  string
	CapabilityCard  json.RawMessage
}

// CatalogRetrieval describes how one page was produced. It is non-contract data
// and is rendered by the catalog page as the "index vs fallback" badge.
type CatalogRetrieval struct {
	Source            string `json:"source"`
	Stale             bool   `json:"stale"`
	IndexState        string `json:"index_state"`
	ProjectionEpoch   int64  `json:"projection_epoch"`
	Rechecked         int    `json:"rechecked"`
	RejectedByRecheck int    `json:"rejected_by_recheck"`
}

// CatalogSearchPage is one page of the non-contract catalog search.
type CatalogSearchPage struct {
	Items      []CatalogHit
	NextCursor *string
	Retrieval  CatalogRetrieval
}

// EncodeSearchCursor serialises one keyset position (rank, version_no, id). The
// rank is the purpose-match tier, so the continuation stays valid whether the
// next page is answered by the index or by the fallback.
func EncodeSearchCursor(rank int, versionNo int64, id string) string {
	payload := fmt.Sprintf("%s|%d|%d|%s", searchCursorPrefix, rank, versionNo, id)
	return base64.RawURLEncoding.EncodeToString([]byte(payload))
}

// DecodeSearchCursor parses a cursor produced by EncodeSearchCursor; anything
// else is ErrInvalidCursor.
func DecodeSearchCursor(raw string) (int, int64, string, error) {
	decoded, err := base64.RawURLEncoding.DecodeString(strings.TrimSpace(raw))
	if err != nil {
		return 0, 0, "", ErrInvalidCursor
	}
	parts := strings.Split(string(decoded), "|")
	if len(parts) != 4 || parts[0] != searchCursorPrefix {
		return 0, 0, "", ErrInvalidCursor
	}
	rank, err := strconv.Atoi(parts[1])
	if err != nil || rank < searchRankPurposeMatch || rank > searchRankMetadataMatch {
		return 0, 0, "", ErrInvalidCursor
	}
	versionNo, err := strconv.ParseInt(parts[2], 10, 64)
	if err != nil || versionNo < 1 {
		return 0, 0, "", ErrInvalidCursor
	}
	if !uuid.Valid(parts[3]) {
		return 0, 0, "", ErrInvalidCursor
	}
	return rank, versionNo, parts[3], nil
}

// SearchAuthorized answers one catalog search page for principalID. The query is
// the non-contract `q`; an empty query browses the authorized catalog in the same
// deterministic order.
func (r *Reader) SearchAuthorized(
	ctx context.Context,
	principalID, rawQuery, cursor string,
	limit int,
	now time.Time,
) (*CatalogSearchPage, error) {
	if limit == 0 {
		limit = DefaultLimit
	}
	if limit < MinLimit || limit > MaxLimit {
		return nil, ErrInvalidLimit
	}
	query := strings.TrimSpace(rawQuery)
	if utf8.RuneCountInString(query) > MaxSearchQueryRunes {
		return nil, ErrSearchQueryTooLong
	}
	var (
		cursorRank any
		cursorNo   any
		cursorID   any
	)
	if strings.TrimSpace(cursor) != "" {
		rank, versionNo, id, err := DecodeSearchCursor(cursor)
		if err != nil {
			return nil, err
		}
		cursorRank, cursorNo, cursorID = rank, versionNo, id
	}

	status, err := r.catalogIndexStatus(ctx, principalID, now)
	if err != nil {
		return nil, err
	}

	pattern := likePattern(query)
	var candidates []searchCandidate
	if status.stale {
		candidates, err = r.searchAuthorizedFallback(ctx, principalID, query, pattern,
			cursorRank, cursorNo, cursorID, limit, now)
	} else {
		candidates, err = r.searchProjection(ctx, principalID, query, pattern,
			cursorRank, cursorNo, cursorID, limit, now)
	}
	if err != nil {
		return nil, err
	}

	page := &CatalogSearchPage{
		Items: make([]CatalogHit, 0, limit),
		Retrieval: CatalogRetrieval{
			Source:          SourceIndex,
			Stale:           status.stale,
			IndexState:      status.state,
			ProjectionEpoch: status.epoch,
		},
	}
	if status.stale {
		page.Retrieval.Source = SourceFallback
	}

	window := candidates
	var next *string
	if len(candidates) > limit {
		window = candidates[:limit]
		token := EncodeSearchCursor(window[limit-1].rank, window[limit-1].versionNo, window[limit-1].versionID)
		next = &token
	}
	for _, candidate := range window {
		// The authoritative re-check is the second authorization/publication check.
		// It is a separate statement from the page query, so a revocation or grant
		// removal that committed in between is observed; a rejected row never
		// reaches the caller and is counted in the retrieval metadata.
		hit, err := r.authoritativeCatalogHit(ctx, principalID, candidate.versionID, now)
		page.Retrieval.Rechecked++
		if err != nil {
			return nil, err
		}
		if hit == nil {
			page.Retrieval.RejectedByRecheck++
			continue
		}
		page.Items = append(page.Items, *hit)
	}
	page.NextCursor = next
	return page, nil
}

// searchCandidate is one row of a page query before the authoritative re-check.
type searchCandidate struct {
	versionID string
	versionNo int64
	rank      int
}

// catalogIndexStatus answers "may the projection be served for this caller".
// The ledger must say ready AND the live authorized eligible-version count must
// equal the live valid-entry count. Counters on the ledger are observability, not
// the authority: a hand-edited counter cannot make a half-built projection look
// complete.
type catalogIndexStatus struct {
	state string
	epoch int64
	stale bool
}

func (r *Reader) catalogIndexStatus(
	ctx context.Context, principalID string, now time.Time,
) (catalogIndexStatus, error) {
	var status catalogIndexStatus
	var state string
	var epoch int64
	err := r.DB.QueryRowContext(ctx,
		`SELECT state, projection_epoch FROM platform.catalog_index_state WHERE id = 'catalog'`,
	).Scan(&state, &epoch)
	switch {
	case errors.Is(err, sql.ErrNoRows):
		status.state = CatalogIndexAbsent
		status.stale = true
		return status, nil
	case err != nil:
		return status, fmt.Errorf("reading the catalog index state: %w", err)
	}
	status.state = state
	status.epoch = epoch
	if state != CatalogIndexReady {
		status.stale = true
		return status, nil
	}

	eligible, err := r.countEligibleVersions(ctx, principalID, now)
	if err != nil {
		return status, err
	}
	indexed, err := r.countValidEntries(ctx, principalID, now)
	if err != nil {
		return status, err
	}
	status.stale = eligible != indexed
	return status, nil
}

// countEligibleVersions counts the authoritative searchable versions the caller
// may read: unarchived, unrevoked, with a non-empty capability card.
func (r *Reader) countEligibleVersions(
	ctx context.Context, principalID string, now time.Time,
) (int64, error) {
	var count int64
	err := r.DB.QueryRowContext(ctx, `
		SELECT count(*)
		  FROM platform.application_versions AS v
		  JOIN platform.applications AS a ON a.id = v.application_id
		 WHERE a.archived_at IS NULL
		   AND v.revoked_at IS NULL
		   AND v.capability_card IS NOT NULL
		   AND v.capability_card <> '{}'::jsonb
		   AND `+applicationReadPredicate(1, 2, 3, 4),
		principalID, now.UTC(), policy.ResourceApplication, policy.ActionRead,
	).Scan(&count)
	if err != nil {
		return 0, fmt.Errorf("counting the eligible catalog versions: %w", err)
	}
	return count, nil
}

// countValidEntries counts the projection entries that still match their
// authoritative version (same manifest hash, same owning application, unrevoked,
// unarchived) inside the caller's ACL.
func (r *Reader) countValidEntries(
	ctx context.Context, principalID string, now time.Time,
) (int64, error) {
	var count int64
	err := r.DB.QueryRowContext(ctx, `
		SELECT count(*)
		  FROM platform.application_catalog_entries AS e
		  JOIN platform.application_versions AS v ON v.id = e.application_version_id
		  JOIN platform.applications AS a ON a.id = v.application_id
		 WHERE a.archived_at IS NULL
		   AND v.revoked_at IS NULL
		   AND e.application_id = v.application_id
		   AND e.source_manifest_sha256 = v.manifest_sha256
		   AND v.capability_card IS NOT NULL
		   AND v.capability_card <> '{}'::jsonb
		   AND `+applicationReadPredicate(1, 2, 3, 4),
		principalID, now.UTC(), policy.ResourceApplication, policy.ActionRead,
	).Scan(&count)
	if err != nil {
		return 0, fmt.Errorf("counting the valid catalog entries: %w", err)
	}
	return count, nil
}

// catalogSearchPageSQL builds the page tail shared by the projection search and
// the authoritative fallback: the ordinal-priority rank expression, the keyset
// cursor predicate and the total order. The two paths deliberately differ in
// their FROM/WHERE authority predicates — that distinction stays visible in each
// caller — but a pagination or ranking rule must have exactly one home. Only the
// rank expression's text-query parameter differs (the projection binds the query
// at $2, the fallback at $3); the cursor ($4,$5,$6) and LIMIT ($7) positions are
// the same in both statements.
func catalogSearchPageSQL(patternParam string) (rank, cursor, order string) {
	rank = fmt.Sprintf(
		`(CASE WHEN v.capability_card->>'purpose' ILIKE %s ESCAPE '\'
		                  THEN 0 ELSE 1 END)`, patternParam)
	cursor = fmt.Sprintf(`(
		        $4::integer IS NULL
		        OR %s > $4
		        OR (%s = $4
		            AND (v.version_no, v.id) < ($5::bigint, $6::uuid))
		       )`, rank, rank)
	order = `ORDER BY rank ASC, v.version_no DESC, v.id DESC
		 LIMIT $7`
	return rank, cursor, order
}

// searchProjection answers a page from the projection. The query already joins
// the authority so ACL, revocation, archive, owning-application and manifest-hash
// checks all run before ORDER BY/LIMIT.
func (r *Reader) searchProjection(
	ctx context.Context,
	principalID, query, pattern string,
	cursorRank, cursorNo, cursorID any,
	limit int,
	now time.Time,
) ([]searchCandidate, error) {
	rank, cursor, order := catalogSearchPageSQL("$2")
	rows, err := r.DB.QueryContext(ctx, `
		SELECT v.id::text, v.version_no,
		       `+rank+` AS rank
		  FROM platform.application_catalog_entries AS e
		  JOIN platform.application_versions AS v ON v.id = e.application_version_id
		  JOIN platform.applications AS a ON a.id = v.application_id
		 WHERE a.archived_at IS NULL
		   AND v.revoked_at IS NULL
		   AND e.application_id = v.application_id
		   AND e.source_manifest_sha256 = v.manifest_sha256
		   AND v.capability_card IS NOT NULL
		   AND v.capability_card <> '{}'::jsonb
		   AND (e.search_text ILIKE $2 ESCAPE '\'
		        OR e.ts @@ plainto_tsquery('simple', $1))
		   AND `+applicationReadPredicate(3, 8, 9, 10)+`
		   AND `+cursor+`
		 `+order,
		query, pattern, principalID, cursorRank, cursorNo, cursorID, limit+1,
		now.UTC(), policy.ResourceApplication, policy.ActionRead,
	)
	if err != nil {
		return nil, fmt.Errorf("searching the catalog projection: %w", err)
	}
	defer rows.Close()
	return scanSearchCandidates(rows)
}

// searchAuthorizedFallback answers a page from the authoritative capability
// cards. ACL and archive are applied before the page is cut. Revocation is
// deliberately re-checked per row by SearchAuthorized rather than trusted here:
// this is the documented "检索返回后再次核对版本/ACL" second check, and the
// `rejected_by_recheck` counter makes it observable.
func (r *Reader) searchAuthorizedFallback(
	ctx context.Context,
	principalID, query, pattern string,
	cursorRank, cursorNo, cursorID any,
	limit int,
	now time.Time,
) ([]searchCandidate, error) {
	rank, cursor, order := catalogSearchPageSQL("$3")
	rows, err := r.DB.QueryContext(ctx, `
		SELECT v.id::text, v.version_no,
		       `+rank+` AS rank
		  FROM platform.application_versions AS v
		  JOIN platform.applications AS a ON a.id = v.application_id
		 WHERE a.archived_at IS NULL
		   AND v.capability_card IS NOT NULL
		   AND v.capability_card <> '{}'::jsonb
		   AND (v.capability_card::text ILIKE $3 ESCAPE '\'
		        OR to_tsvector('simple', v.capability_card::text)
		           @@ plainto_tsquery('simple', $2))
		   AND `+applicationReadPredicate(1, 8, 9, 10)+`
		   AND `+cursor+`
		 `+order,
		principalID, query, pattern, cursorRank, cursorNo, cursorID, limit+1,
		now.UTC(), policy.ResourceApplication, policy.ActionRead,
	)
	if err != nil {
		return nil, fmt.Errorf("searching the authorized catalog: %w", err)
	}
	defer rows.Close()
	return scanSearchCandidates(rows)
}

func scanSearchCandidates(rows *sql.Rows) ([]searchCandidate, error) {
	candidates := make([]searchCandidate, 0, 8)
	for rows.Next() {
		var candidate searchCandidate
		if err := rows.Scan(&candidate.versionID, &candidate.versionNo, &candidate.rank); err != nil {
			return nil, fmt.Errorf("scanning a catalog candidate: %w", err)
		}
		candidates = append(candidates, candidate)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("reading catalog candidates: %w", err)
	}
	return candidates, nil
}

// authoritativeCatalogHit is the second check: one separate statement that
// re-reads the version and its application with the ACL, revocation, archive and
// card conditions. It returns nil,nil when the row is not currently returnable.
func (r *Reader) authoritativeCatalogHit(
	ctx context.Context, principalID, versionID string, now time.Time,
) (*CatalogHit, error) {
	if !uuid.Valid(versionID) {
		return nil, nil
	}
	var (
		hit      CatalogHit
		cardText string
	)
	err := r.DB.QueryRowContext(ctx, `
		SELECT v.id::text, v.application_id::text, a.name, v.version_no,
		       v.manifest_sha256, v.capability_card::text
		  FROM platform.application_versions AS v
		  JOIN platform.applications AS a ON a.id = v.application_id
		 WHERE v.id = $1::uuid
		   AND a.archived_at IS NULL
		   AND v.revoked_at IS NULL
		   AND v.capability_card IS NOT NULL
		   AND v.capability_card <> '{}'::jsonb
		   AND `+applicationReadPredicate(2, 3, 4, 5),
		versionID, principalID, now.UTC(), policy.ResourceApplication, policy.ActionRead,
	).Scan(
		&hit.VersionID, &hit.ApplicationID, &hit.ApplicationName, &hit.VersionNo,
		&hit.ManifestSHA256, &cardText,
	)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("re-checking a catalog candidate: %w", err)
	}
	if _, _, err := parseCatalogCard(cardText); err != nil {
		return nil, nil
	}
	hit.CapabilityCard = json.RawMessage(cardText)
	return &hit, nil
}

// parseCatalogCard extracts the searchable text and the purpose from a stored
// capability card. A card that is not an object with a non-empty purpose string
// is not a catalog entry: the rebuild records catalog_source_invalid and search
// refuses the row instead of leaking malformed metadata.
func parseCatalogCard(raw string) (purpose string, searchText string, err error) {
	var card map[string]any
	if err := json.Unmarshal([]byte(raw), &card); err != nil || card == nil {
		return "", "", ErrCatalogSourceInvalid
	}
	value, present := card["purpose"]
	if !present {
		return "", "", ErrCatalogSourceInvalid
	}
	purpose, ok := value.(string)
	if !ok || strings.TrimSpace(purpose) == "" {
		return "", "", ErrCatalogSourceInvalid
	}
	lines := make([]string, 0, 8)
	lines = append(lines, strings.TrimSpace(purpose))
	for _, key := range []string{"includes", "excludes", "required_inputs", "expected_outputs", "examples"} {
		values, ok := card[key].([]any)
		if !ok {
			// The compiler always writes the six members; a partial card is
			// corrupt. Keeping only the purpose would silently change what the
			// projection claims to match.
			if _, present := card[key]; present {
				return "", "", ErrCatalogSourceInvalid
			}
			continue
		}
		for _, item := range values {
			text, ok := item.(string)
			if !ok {
				return "", "", ErrCatalogSourceInvalid
			}
			if strings.TrimSpace(text) != "" {
				lines = append(lines, text)
			}
		}
	}
	return purpose, strings.Join(lines, "\n"), nil
}

// likePattern makes a substring query literal: `%` and `_` are escaped so a user
// query can never turn into an unintended wildcard.
func likePattern(query string) string {
	escaped := strings.NewReplacer("\\", "\\\\", "%", "\\%", "_", "\\_").Replace(query)
	return "%" + escaped + "%"
}
