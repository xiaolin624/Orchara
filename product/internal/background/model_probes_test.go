package background

import (
	"testing"

	"orchara/platform/internal/uuid"
)

func TestRealModelCompatibilityRequiresDistinctEvidenceForEveryProbe(t *testing.T) {
	report := ModelCompatibilityReport{
		Check: ModelCompatibilityCheck{Name: "model_compatibility", Passed: true}, Compatible: true,
	}
	for _, name := range RealModelCompatibilityChecks() {
		report.Probes = append(report.Probes, ModelCompatibilityProbe{
			CallID: uuid.NewString(), ResultArtifactID: uuid.NewString(),
			Check: ModelCompatibilityCheck{Name: name, Passed: true},
		})
	}
	report.CallID = report.Probes[0].CallID
	report.ModelResultArtifactID = &report.Probes[0].ResultArtifactID
	if !realModelProbeSetValid(report) {
		t.Fatal("complete distinct probe evidence was refused")
	}
	original := append([]ModelCompatibilityProbe(nil), report.Probes...)
	for _, mutate := range []func(){
		func() { report.Probes = report.Probes[:1] },
		func() { report.Probes[1].CallID = report.Probes[0].CallID },
		func() { report.Probes[1].ResultArtifactID = report.Probes[0].ResultArtifactID },
		func() { report.Probes[1].Check.Name = "structured_output" },
		func() { report.Probes[1].Check.Passed = false },
	} {
		report.Probes = append([]ModelCompatibilityProbe(nil), original...)
		mutate()
		if realModelProbeSetValid(report) {
			t.Fatal("missing, duplicated or failed probe accepted as an overall pass")
		}
	}
}
