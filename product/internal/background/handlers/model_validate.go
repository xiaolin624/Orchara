// model_validate: the developer-triggered synthetic background job that proves a
// registered candidate can handle the platform protocol.
//
// The handler is deliberately dumb about scope: it can only read the job's own
// fixture, invoke the job's own candidate, and register a report artifact for the
// job it holds. Every one of those steps goes through the job-scoped internal API
// with the attempt token; the handler has no database identity, no candidate
// credential and no object path.
//
// A compatibility verdict is a *report* outcome: "the model failed the structured
// output check" is a successful job with a failed report. Only the platform being
// unable to do its work (storage, a stale lease, an unconfigured provider) fails
// the job, and only the codes in the fixed retry table are retried.
package handlers

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"time"

	"orchara/platform/internal/background"
	"orchara/platform/internal/jcs"
	"orchara/platform/internal/objectstore"
)

// reportReadyTimeout bounds how long a handler waits for its own report artifact
// to be finalized. The wait is real: another pool/process services
// artifact_finalize, and a handler that never sees "ready" must not claim
// success.
const reportReadyTimeout = 60 * time.Second

// ModelValidate performs the whole synthetic validation flow:
//
//	download the job's fixture through a signed link
//	→ invoke the job's candidate through the platform model proxy
//	→ upload the compatibility report through the job-scoped upload/complete flow
//	→ wait for the artifact to become ready
//	→ submit succeeded with the report artifact id.
//
// The fixture is the server-chosen `job.input_artifact_id`; the worker never
// supplies an artifact id, a fixture path or a provider URL.
func ModelValidate(ctx context.Context, work background.HandlerWork) background.Outcome {
	if work.Scope == nil || work.Job == nil {
		return background.Failed(background.CodeTransientInternal)
	}
	job := work.Job
	if job.InputArtifactID == nil || *job.InputArtifactID == "" {
		// The job was created without a fixture; retrying cannot invent one.
		return background.Failed(background.CodeFixtureMissing)
	}

	downloadLink, err := work.Scope.Download(ctx, job.ID, work.AttemptToken, *job.InputArtifactID)
	if err != nil {
		return outcomeFor(err)
	}
	fixture, err := work.Scope.FetchURL(ctx, downloadLink, objectstore.MaxObjectBytes)
	if err != nil {
		// The link may simply have expired, or the store may be momentarily
		// unreadable; a retry mints a fresh link.
		return background.Failed(background.CodeTransientStorageIO)
	}
	fixtureSHA := sha256Hex(fixture)

	// The logical key is derived from the fixture content, so a retry of the same
	// job replays the same logical call and a changed fixture is a different call
	// (the server would refuse it as a conflict under the same key).
	call, err := work.Scope.ModelCall(ctx, job.ID, work.AttemptToken, background.ModelCallRequest{
		LogicalKey:            "compatibility:" + fixtureSHA,
		ModelProfileVersionID: job.TargetID,
		Params: map[string]any{
			"task":           "model_compatibility",
			"fixture_sha256": fixtureSHA,
			"check":          "structured_output",
		},
	})
	if err != nil {
		return outcomeFor(err)
	}

	report := compatibilityReport(job, call, fixtureSHA)
	reportBytes, err := canonicalJSON(report)
	if err != nil {
		// The report is built from server-controlled values; a canonicalisation
		// failure is an internal defect.
		return background.Failed(background.CodeTransientInternal)
	}
	reportSHA := sha256Hex(reportBytes)

	ticket, err := work.Scope.Upload(ctx, job.ID, work.AttemptToken, background.UploadRequest{
		MediaType: "application/json",
		SizeBytes: int64(len(reportBytes)),
		Purpose:   "report",
	})
	if err != nil {
		return outcomeFor(err)
	}
	if err := work.Scope.PutURL(ctx, ticket.URL, reportBytes); err != nil {
		return background.Failed(background.CodeTransientStorageIO)
	}
	view, err := work.Scope.Complete(ctx, job.ID, work.AttemptToken, ticket.ArtifactID, reportSHA)
	if err != nil {
		return outcomeFor(err)
	}
	if view.State != "ready" {
		if outcome, ready := waitForReport(ctx, work, ticket.ArtifactID); !ready {
			return outcome
		}
	}
	return background.SucceededWith(ticket.ArtifactID)
}

// waitForReport polls the job-scoped status endpoint while renewing the lease
// through the regular heartbeat. It never reads the artifact table directly: the
// handler cannot see the finalize guard's decision except through the API.
func waitForReport(
	ctx context.Context,
	work background.HandlerWork,
	artifactID string,
) (background.Outcome, bool) {
	deadline := time.Now().Add(reportReadyTimeout)
	for {
		if err := ctx.Err(); err != nil {
			return background.Failed(background.CodeTransientInternal), false
		}
		view, err := work.Scope.Status(ctx, work.Job.ID, work.AttemptToken, artifactID)
		if err != nil {
			return outcomeFor(err), false
		}
		switch view.State {
		case "ready":
			return background.Succeeded(), true
		case "quarantined", "deleted":
			// The finalize guard refused the report; the job cannot produce a
			// usable result and retrying the same bytes would be refused again.
			return background.Failed(background.CodeReportNotReady), false
		}
		if time.Now().After(deadline) {
			return background.Failed(background.CodeTransientStorageIO), false
		}
		if work.Heartbeat != nil {
			_ = work.Heartbeat(ctx)
		}
		select {
		case <-ctx.Done():
			return background.Failed(background.CodeTransientInternal), false
		case <-time.After(100 * time.Millisecond):
		}
	}
}

// compatibilityReport is the artifact a model_validate job registers. Its
// fixture hash and call id make the report traceable to the exact bytes and
// ledger attempt that produced it.
func compatibilityReport(
	job *background.Job,
	call background.ModelCallResult,
	fixtureSHA string,
) map[string]any {
	check, _ := call.Data["check"].(map[string]any)
	compatible := false
	if value, ok := check["passed"].(bool); ok {
		compatible = value
	}
	report := map[string]any{
		"report_version":           1,
		"kind":                     "model_compatibility",
		"model_profile_version_id": job.TargetID,
		"call_id":                  call.CallID,
		"fixture_artifact_id":      derefArtifact(job.InputArtifactID),
		"fixture_sha256":           fixtureSHA,
		"check":                    check,
		"compatible":               compatible,
		"provider":                 call.Data["provider"],
		"stub":                     call.Data["stub"],
	}
	if call.ResultArtifactID != nil {
		report["model_result_artifact_id"] = *call.ResultArtifactID
	}
	return report
}

func derefArtifact(value *string) string {
	if value == nil {
		return ""
	}
	return *value
}

// sha256Hex is the handler's hash of the bytes it just moved. The server
// recomputes it, so this value is a declaration, never a trusted fact.
func sha256Hex(content []byte) string {
	sum := sha256.Sum256(content)
	return hex.EncodeToString(sum[:])
}

// canonicalJSON renders the report as RFC 8785 bytes; those exact bytes are what
// the artifact stores and what the complete call declares.
func canonicalJSON(value any) ([]byte, error) {
	raw, err := json.Marshal(value)
	if err != nil {
		return nil, err
	}
	return jcs.CanonicalizeBytes(raw)
}

// outcomeFor maps an internal-API refusal to a job outcome.
//
// The vocabulary is fixed here, not inferred from strings: infrastructure
// refusals (a 5xx/transport problem) are retryable, while a missing provider, a
// refused candidate or a stale lease is a business outcome that must not loop.
func outcomeFor(err error) background.Outcome {
	var coded interface{ ErrorCode() string }
	if errors.As(err, &coded) {
		switch coded.ErrorCode() {
		case "provider_not_configured":
			return background.Failed(background.CodeProviderNotConfigured)
		case "provider_adapter_unavailable":
			return background.Failed(background.CodeProviderAdapterUnavailable)
		case "candidate_mismatch", "candidate_unavailable", "model_not_allowed",
			"model_call_conflict", "fixture_missing":
			return background.Failed(background.CodeModelCallRefused)
		case "stale_attempt":
			return background.Failed(background.CodeStaleAttempt)
		case "object_store_unavailable", "object_signing_unavailable":
			return background.Failed(background.CodeObjectStoreUnavailable)
		case "artifact_not_ready", "artifact_conflict", "artifact_not_found":
			return background.Failed(background.CodeReportNotReady)
		}
	}
	return background.Failed(background.CodeTransientInternal)
}

// DescribeModelValidate is a small diagnostic for the maintenance startup line.
func DescribeModelValidate() string {
	return fmt.Sprintf("model_validate requires the job-scoped artifact API and a registered provider profile")
}
