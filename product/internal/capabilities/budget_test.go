package capabilities

import "testing"

// P1-5: a model attempt that fails *after* the provider answered must settle the
// real usage instead of releasing the reservation, while an answer without a
// usable usage object keeps the conservative reservation for reconciliation.
// settleFailureUsage is the pure decision the DB-writing helper makes; this
// table pins every shape the capability validator can produce.
func TestSettleFailureUsageRequiresAParsedProviderUsage(t *testing.T) {
	cases := []struct {
		name       string
		projection *providerProjection
		settle     bool
		tokensIn   int64
		tokensOut  int64
	}{
		{"no projection (provider returned no object)", nil, false, 0, 0},
		{"projection without a usage object", &providerProjection{Text: "answer"}, false, 0, 0},
		{
			"parsed usage",
			&providerProjection{
				Usage:    &projectedUsage{InputTokens: 12, OutputTokens: 34},
				TokensIn: 12, TokensOut: 34,
			},
			true, 12, 34,
		},
		{
			"zero usage is still a real usage object",
			&providerProjection{Usage: &projectedUsage{}},
			true, 0, 0,
		},
	}
	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			tokensIn, tokensOut, settle := settleFailureUsage(testCase.projection)
			if settle != testCase.settle {
				t.Fatalf("settle=%v, want %v", settle, testCase.settle)
			}
			if tokensIn != testCase.tokensIn || tokensOut != testCase.tokensOut {
				t.Fatalf("tokens=(%d,%d), want (%d,%d)",
					tokensIn, tokensOut, testCase.tokensIn, testCase.tokensOut)
			}
		})
	}
}

// A completed streamed answer that a later assembly check refuses still carries
// the provider's usage object. Recovering it makes that failure settle exactly
// like every other post-provider failure.
func TestUsageProjectionRecoversTheReportedUsage(t *testing.T) {
	if projection := usageProjection(nil); projection != nil {
		t.Fatalf("nil data produced %+v", projection)
	}
	if projection := usageProjection(map[string]any{"text": "answer"}); projection != nil {
		t.Fatalf("missing usage produced %+v", projection)
	}
	if projection := usageProjection(map[string]any{
		"usage": map[string]any{"input_tokens": "twelve", "output_tokens": 1},
	}); projection != nil {
		t.Fatalf("invalid usage produced %+v", projection)
	}

	projection := usageProjection(map[string]any{
		"usage": map[string]any{"input_tokens": 12, "output_tokens": 34},
	})
	if projection == nil || projection.Usage == nil {
		t.Fatal("a valid usage object was not recovered")
	}
	if projection.TokensIn != 12 || projection.TokensOut != 34 {
		t.Fatalf("tokens=(%d,%d), want (12,34)", projection.TokensIn, projection.TokensOut)
	}
	if tokensIn, tokensOut, settle := settleFailureUsage(projection); !settle ||
		tokensIn != 12 || tokensOut != 34 {
		t.Fatalf("recovered usage did not settle: settle=%v tokens=(%d,%d)",
			settle, tokensIn, tokensOut)
	}
}
