package background

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"strings"

	"orchara/platform/internal/objectstore"
	"orchara/platform/internal/uuid"
)

// A handler cannot promote a model by inventing probe verdicts. Each required
// check is bound to a distinct succeeded call and its immutable server-sealed
// result; both scope and bytes are rechecked in the completion transaction.
func (c *Controller) verifyRealModelProbes(ctx context.Context, tx *sql.Tx, job *Job, report ModelCompatibilityReport) (string, error) {
	if !realModelProbeSetValid(report) {
		return CodeReportInvalid, nil
	}
	for _, probe := range report.Probes {
		var key, digest string
		err := tx.QueryRowContext(ctx, `
			SELECT a.object_key, a.sha256
			  FROM platform.platform_model_calls AS mc
			  JOIN platform.artifacts AS a ON a.id = mc.result_artifact_id
			 WHERE mc.id = $1::uuid AND mc.background_job_id = $2::uuid
			   AND mc.model_profile_version_id = $3::uuid AND mc.state = 'succeeded'
			   AND a.id = $4::uuid AND a.owner_id = $5::uuid
			   AND a.state = 'ready' AND a.deleted_at IS NULL`,
			probe.CallID, job.ID, job.TargetID, probe.ResultArtifactID, job.OwnerID,
		).Scan(&key, &digest)
		if errors.Is(err, sql.ErrNoRows) {
			return CodeReportBindingMismatch, nil
		}
		if err != nil {
			return "", fmt.Errorf("reading compatibility probe binding: %w", err)
		}
		content, actual, err := readCappedObject(c.Store, key, MaxEvaluationReportBytes)
		switch {
		case errors.Is(err, objectstore.ErrNotExist), errors.Is(err, objectstore.ErrInvalidKey), errors.Is(err, objectstore.ErrTooLarge):
			return CodeReportInvalid, nil
		case err != nil:
			return "", fmt.Errorf("%w: reading the compatibility probe", errGuardTransient)
		}
		var sealed struct {
			Check    ModelCompatibilityCheck `json:"check"`
			Provider string                  `json:"provider"`
			Stub     bool                    `json:"stub"`
		}
		if !strings.EqualFold(actual, digest) || json.Unmarshal(content, &sealed) != nil ||
			sealed.Stub || sealed.Provider != report.Provider || sealed.Check != probe.Check {
			return CodeReportBindingMismatch, nil
		}
	}
	return "", nil
}

func realModelProbeSetValid(report ModelCompatibilityReport) bool {
	names := RealModelCompatibilityChecks()
	if len(report.Probes) != len(names) || report.Check.Name != "model_compatibility" {
		return false
	}
	seenCalls, seenArtifacts := map[string]bool{}, map[string]bool{}
	passed := true
	for index, probe := range report.Probes {
		if probe.Check.Name != names[index] || !uuid.Valid(probe.CallID) || !uuid.Valid(probe.ResultArtifactID) ||
			seenCalls[probe.CallID] || seenArtifacts[probe.ResultArtifactID] {
			return false
		}
		seenCalls[probe.CallID], seenArtifacts[probe.ResultArtifactID] = true, true
		passed = passed && probe.Check.Passed
	}
	return report.CallID == report.Probes[0].CallID && report.ModelResultArtifactID != nil &&
		*report.ModelResultArtifactID == report.Probes[0].ResultArtifactID &&
		report.Compatible == passed && report.Check.Passed == passed
}
