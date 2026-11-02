package background

import (
	"context"
	"database/sql"
	"errors"
	"fmt"

	"orchara/platform/internal/uuid"
)

// Get returns one job for an owner-or-admin read.
//
// The owner check and the admin override are one SQL predicate; a caller that is
// neither gets ErrNotFound, so the endpoint cannot be used to enumerate ids.
// admin comes from the caller's admin role, which F01 explicitly does not treat
// as a content-reader for arbitrary resources; it is accepted here because
// BACKGROUND-JOBS.md §3 gives "有权的管理员/owner" the redacted diagnostic.
func (c *Controller) Get(
	ctx context.Context,
	jobID, principalID string,
	admin bool,
) (*Job, error) {
	if !uuid.Valid(jobID) || !uuid.Valid(principalID) {
		return nil, ErrNotFound
	}
	job, err := scanJob(c.DB.QueryRowContext(ctx, `
		SELECT `+selectJobColumns+`
		  FROM platform.background_jobs
		 WHERE id = $1::uuid
		   AND (owner_id = $2::uuid OR $3::boolean)`, jobID, principalID, admin))
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrNotFound
	}
	if err != nil {
		return nil, fmt.Errorf("reading the background job: %w", err)
	}
	return job, nil
}
