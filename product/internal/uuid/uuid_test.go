package uuid

import "testing"

func TestValidAcceptsTheCanonicalForm(t *testing.T) {
	for _, value := range []string{
		"11111111-1111-4111-8111-111111111111",
		"AAAAAAAA-BBBB-4CCC-8DDD-EEEEEEEEEEEE",
		"00000000-0000-0000-0000-000000000000",
	} {
		if !Valid(value) {
			t.Fatalf("%q was rejected", value)
		}
	}
}

func TestValidRejectsEverythingElse(t *testing.T) {
	for _, value := range []string{
		"",
		"11111111111141118111111111111111",
		"{11111111-1111-4111-8111-111111111111}",
		"11111111-1111-4111-8111-11111111111g",
		"11111111-1111-4111-8111-1111111111111",
		"1111-1111-4111-8111-111111111111",
		" 11111111-1111-4111-8111-111111111111",
		"not-a-uuid-at-all-not-a-uuid-at-allx",
	} {
		if Valid(value) {
			t.Fatalf("%q was accepted", value)
		}
	}
}
