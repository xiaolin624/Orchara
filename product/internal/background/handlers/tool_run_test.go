package handlers

import (
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"testing"

	"orchara/platform/internal/background"
	"orchara/platform/internal/tools"
)

func TestValidateSalesSummaryAcceptsThePublishedShape(t *testing.T) {
	raw := []byte(`{"total":"250.00","regions":{"东区":"200.00","西区":"50.00"},"order_count":3}`)
	summary, err := validateSalesSummary(raw)
	if err != nil {
		t.Fatalf("validateSalesSummary: %v", err)
	}
	if summary.Total == nil || *summary.Total != "250.00" || summary.OrderCount == nil ||
		*summary.OrderCount != 3 {
		t.Fatalf("summary = %+v", summary)
	}
	if summary.Regions["东区"] != "200.00" || summary.Regions["西区"] != "50.00" {
		t.Fatalf("regions = %v", summary.Regions)
	}
}

func TestValidateSalesSummaryKeepsDecimalPrecision(t *testing.T) {
	raw := []byte(`{"total":"0.30","regions":{"北区":"0.30"},"order_count":2}`)
	if _, err := validateSalesSummary(raw); err != nil {
		t.Fatalf("0.1+0.2-style summary must validate: %v", err)
	}
}

func TestValidateSalesSummaryRefusesNonProtocolData(t *testing.T) {
	cases := map[string]string{
		"total mismatch":   `{"total":"250.01","regions":{"东区":"200.00","西区":"50.00"},"order_count":3}`,
		"one decimal":      `{"total":"250.0","regions":{"东区":"200.00"},"order_count":1}`,
		"negative total":   `{"total":"-1.00","regions":{"东区":"-1.00"},"order_count":1}`,
		"float total":      `{"total":250.00,"regions":{"东区":"250.00"},"order_count":1}`,
		"float count":      `{"total":"1.00","regions":{"东区":"1.00"},"order_count":1.5}`,
		"empty regions":    `{"total":"0.00","regions":{},"order_count":0}`,
		"zero count":       `{"total":"1.00","regions":{"东区":"1.00"},"order_count":0}`,
		"extra key":        `{"total":"1.00","regions":{"东区":"1.00"},"order_count":1,"currency":"USD"}`,
		"missing total":    `{"regions":{"东区":"1.00"},"order_count":1}`,
		"trailing content": `{"total":"1.00","regions":{"东区":"1.00"},"order_count":1} {}`,
		"not an object":    `[]`,
	}
	for label, raw := range cases {
		if _, err := validateSalesSummary([]byte(raw)); err == nil {
			t.Fatalf("%s: invalid data must be refused", label)
		}
	}
}

func TestParseMoneyCentsIsExact(t *testing.T) {
	cases := map[string]string{
		"0.00":   "0",
		"250.00": "25000",
		"0.30":   "30",
		"120.50": "12050",
	}
	for value, want := range cases {
		cents, ok := parseMoneyCents(value)
		if !ok || cents.String() != want {
			t.Fatalf("parseMoneyCents(%q) = %v, %t (want %s)", value, cents, ok, want)
		}
	}
	for _, bad := range []string{"", ".50", "1.", "1.0", "1.000", "+1.00", "-1.00", "1,00", " 1.00"} {
		if _, ok := parseMoneyCents(bad); ok {
			t.Fatalf("parseMoneyCents(%q) must fail", bad)
		}
	}
}

func TestToolRunCodeKeepsTheToolVocabularyAndMapsPlatformEdges(t *testing.T) {
	rejection := &tools.RejectionError{Rejection: tools.Rejection{Code: "missing_column"}}
	if code := toolRunCode(rejection); code != "missing_column" {
		t.Fatalf("rejection code = %q", code)
	}
	for err, want := range map[error]string{
		tools.ErrTimeout:                         background.CodeToolTimeout,
		fmt.Errorf("%w: exit", tools.ErrCrashed): background.CodeToolCrash,
		tools.ErrOutputInvalid:                   background.CodeToolOutputInvalid,
		tools.ErrOutputMissing:                   background.CodeToolOutputInvalid,
		tools.ErrArtifactEscape:                  background.CodeToolArtifactEscape,
		tools.ErrArtifactSymlink:                 background.CodeToolArtifactSymlink,
		tools.ErrArtifactTooLarge:                background.CodeToolArtifactTooLarge,
		tools.ErrTooManyArtifacts:                background.CodeToolArtifactCount,
		tools.ErrArtifactInvalid:                 background.CodeToolArtifactInvalid,
		tools.ErrScriptMissing:                   background.CodeTransientInternal,
		tools.ErrPythonMissing:                   background.CodeTransientInternal,
		errors.New("anything else"):              background.CodeTransientInternal,
	} {
		if code := toolRunCode(err); code != want {
			t.Fatalf("toolRunCode(%v) = %q, want %q", err, code, want)
		}
	}
}

func TestToolFailureTextIsSafeAndActionable(t *testing.T) {
	for _, code := range []string{
		background.CodeToolTimeout, background.CodeToolCrash,
		background.CodeToolOutputInvalid, background.CodeToolArtifactInvalid,
		background.CodeToolArtifactEscape, background.CodeToolArtifactSymlink,
		background.CodeToolArtifactTooLarge, background.CodeToolArtifactCount,
	} {
		message, fix := toolFailureText(code)
		if message == "" || fix == "" {
			t.Fatalf("%s: empty message/fix", code)
		}
		combined := message + fix
		if strings.Contains(combined, "C:\\") || strings.Contains(combined, "/workspace") ||
			strings.Contains(combined, "ORCHARA_") {
			t.Fatalf("%s: unsafe diagnostics text: %s", code, combined)
		}
		if !json.Valid([]byte(fmt.Sprintf("%q", combined))) {
			t.Fatalf("%s: not JSON-stringifiable", code)
		}
	}
}

func TestToolRunRetryTableIsBoundedAndBusinessRejectionsAreNotRetried(t *testing.T) {
	for _, code := range []string{
		background.CodeTransientStorageIO, background.CodeTransientInternal,
		background.CodeLeaseExpired, background.CodeToolTimeout, background.CodeToolCrash,
	} {
		if !background.Retryable(background.KindToolRun, code) {
			t.Fatalf("%q must be retryable for tool_run", code)
		}
	}
	for _, code := range []string{
		"missing_column", "duplicate_order_id", "negative_amount", "macro_present",
		background.CodeToolOutputInvalid, background.CodeToolArtifactEscape,
		background.CodeToolArtifactSymlink, background.CodeToolArtifactTooLarge,
		background.CodeToolArtifactInvalid, "made_up_code",
	} {
		if background.Retryable(background.KindToolRun, code) {
			t.Fatalf("%q must not be retried", code)
		}
		if background.ClassifyFailure(background.KindToolRun, code) != background.FailureBusiness {
			t.Fatalf("%q must classify as business", code)
		}
	}
}
