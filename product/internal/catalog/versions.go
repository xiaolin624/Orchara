package catalog

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

	"orchara/platform/internal/policy"
	"orchara/platform/internal/uuid"
)

// Version is one immutable published row of the contract's Version schema. It
// never carries a draft definition/layout: only the published manifest and its
// hash are readable through this model.
type Version struct {
	ID             string
	ApplicationID  string
	VersionNo      int64
	ManifestSHA256 string
	Manifest       json.RawMessage
}

// EncodeVersionCursor serialises the keyset position (version_no, id) as an
// opaque continuation token. version_no is unique per application and versions
// are immutable, so the order is stable without a timestamp tiebreak.
func EncodeVersionCursor(versionNo int64, id string) string {
	payload := "v1|" + strconv.FormatInt(versionNo, 10) + "|" + id
	return base64.RawURLEncoding.EncodeToString([]byte(payload))
}

// DecodeVersionCursor parses a cursor produced by EncodeVersionCursor;
// anything else is ErrInvalidCursor.
func DecodeVersionCursor(raw string) (int64, string, error) {
	decoded, err := base64.RawURLEncoding.DecodeString(strings.TrimSpace(raw))
	if err != nil {
		return 0, "", ErrInvalidCursor
	}
	parts := strings.Split(string(decoded), "|")
	if len(parts) != 3 || parts[0] != "v1" {
		return 0, "", ErrInvalidCursor
	}
	versionNo, err := strconv.ParseInt(parts[1], 10, 64)
	if err != nil || versionNo < 1 {
		return 0, "", ErrInvalidCursor
	}
	if !uuid.Valid(parts[2]) {
		return 0, "", ErrInvalidCursor
	}
	return versionNo, parts[2], nil
}

// applicationVisible resolves the application and applies the read ACL. It is
// the single place that answers "may this caller list/read this application's
// versions", so the list and the detail cannot disagree. A missing, archived or
// unauthorized application is one ErrNotVisible.
func (r *Reader) applicationVisible(
	ctx context.Context, callerID, applicationID string,
) error {
	if !uuid.Valid(applicationID) {
		return ErrNotVisible
	}
	var ownerID string
	err := r.DB.QueryRowContext(ctx, `
		SELECT owner_id::text
		  FROM platform.applications
		 WHERE id = $1
		   AND archived_at IS NULL`,
		applicationID,
	).Scan(&ownerID)
	if errors.Is(err, sql.ErrNoRows) {
		return ErrNotVisible
	}
	if err != nil {
		return fmt.Errorf("reading the application: %w", err)
	}
	allow, err := r.readAllowed(ctx, callerID, applicationID, ownerID)
	if err != nil {
		return err
	}
	if !allow {
		return ErrNotVisible
	}
	return nil
}

// ListVersions returns one page of published versions, newest first, with the
// ACL predicate in the same SQL WHERE as the cursor and LIMIT. Filtering before
// the page is cut means a visible row can never hide behind an invisible one
// (the list endpoint is per application, but the invariant is kept identical to
// the applications list so neither can drift).
//
// The application-level visibility check runs first and answers 404 for an
// invisible application; the list query repeats the predicate so a grant revoked
// between the two statements cannot leak a page. A revoked version (F02B/T070)
// is omitted: the contract's Version schema has no state/availability field, so
// omission is the only honest representation of "not runnable".
func (r *Reader) ListVersions(
	ctx context.Context,
	callerID, applicationID, cursor string,
	limit int,
	now time.Time,
) ([]Version, *string, error) {
	if limit == 0 {
		limit = DefaultLimit
	}
	if limit < MinLimit || limit > MaxLimit {
		return nil, nil, ErrInvalidLimit
	}
	if err := r.applicationVisible(ctx, callerID, applicationID); err != nil {
		return nil, nil, err
	}
	var (
		cursorNo any
		cursorID any
	)
	if strings.TrimSpace(cursor) != "" {
		versionNo, id, err := DecodeVersionCursor(cursor)
		if err != nil {
			return nil, nil, err
		}
		cursorNo, cursorID = versionNo, id
	}
	rows, err := r.DB.QueryContext(ctx, `
		SELECT v.id::text, v.application_id::text, v.version_no,
		       v.manifest_sha256, v.manifest::text
		  FROM platform.application_versions AS v
		  JOIN platform.applications AS a ON a.id = v.application_id
		 WHERE v.application_id = $1
		   AND a.archived_at IS NULL
		   AND v.revoked_at IS NULL
		   AND `+applicationReadPredicate(2, 6, 7, 8)+`
		   AND (
		        $3::bigint IS NULL
		        OR (v.version_no, v.id) < ($3::bigint, $4::uuid)
		       )
		 ORDER BY v.version_no DESC, v.id DESC
		 LIMIT $5`,
		applicationID, callerID, cursorNo, cursorID, limit+1,
		now.UTC(), policy.ResourceApplication, policy.ActionRead,
	)
	if err != nil {
		return nil, nil, fmt.Errorf("listing application versions: %w", err)
	}
	defer rows.Close()

	items := make([]Version, 0, limit+1)
	for rows.Next() {
		var (
			version  Version
			manifest string
		)
		if err := rows.Scan(
			&version.ID, &version.ApplicationID, &version.VersionNo,
			&version.ManifestSHA256, &manifest,
		); err != nil {
			return nil, nil, fmt.Errorf("listing application versions: %w", err)
		}
		version.Manifest = json.RawMessage(manifest)
		items = append(items, version)
	}
	if err := rows.Err(); err != nil {
		return nil, nil, fmt.Errorf("listing application versions: %w", err)
	}

	var next *string
	if len(items) > limit {
		last := items[limit-1]
		token := EncodeVersionCursor(last.VersionNo, last.ID)
		next = &token
		items = items[:limit]
	}
	return items, next, nil
}

// GetVersion returns one published version of an application the caller may
// read. Unknown, archived-application, revoked (F02B/T070) and unauthorized are
// all ErrNotVisible/404: the contract's Version schema has no state field, so an
// emptied detail read is the only availability signal it can express.
func (r *Reader) GetVersion(
	ctx context.Context, callerID, versionID string,
) (*Version, error) {
	if !uuid.Valid(versionID) {
		return nil, ErrNotVisible
	}
	var (
		version  Version
		ownerID  string
		manifest string
	)
	err := r.DB.QueryRowContext(ctx, `
		SELECT v.id::text, v.application_id::text, v.version_no,
		       v.manifest_sha256, v.manifest::text, a.owner_id::text
		  FROM platform.application_versions AS v
		  JOIN platform.applications AS a ON a.id = v.application_id
		 WHERE v.id = $1
		   AND a.archived_at IS NULL
		   AND v.revoked_at IS NULL`,
		versionID,
	).Scan(
		&version.ID, &version.ApplicationID, &version.VersionNo,
		&version.ManifestSHA256, &manifest, &ownerID,
	)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrNotVisible
	}
	if err != nil {
		return nil, fmt.Errorf("reading an application version: %w", err)
	}
	version.Manifest = json.RawMessage(manifest)
	allow, err := r.readAllowed(ctx, callerID, version.ApplicationID, ownerID)
	if err != nil {
		return nil, err
	}
	if !allow {
		return nil, ErrNotVisible
	}
	return &version, nil
}
