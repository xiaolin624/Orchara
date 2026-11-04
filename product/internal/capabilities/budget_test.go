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
