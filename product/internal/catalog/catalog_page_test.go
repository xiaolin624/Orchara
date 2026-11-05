package catalog

import (
	"strings"
	"testing"
)

// TestCatalogSearchPageSQLHasOnePaginationShape keeps the projection search and
// the authoritative fallback paginating identically: the keyset cursor and the
// total order live in one helper, and only the rank expression's text-query
// parameter differs between the two paths ($2 vs $3).
func TestCatalogSearchPageSQLHasOnePaginationShape(t *testing.T) {
	projectionRank, projectionCursor, projectionOrder := catalogSearchPageSQL("$2")
	fallbackRank, fallbackCursor, fallbackOrder := catalogSearchPageSQL("$3")

	if projectionOrder != fallbackOrder {
		t.Fatalf("the two search paths order differently:\n%s\n%s", projectionOrder, fallbackOrder)
	}
	if strings.ReplaceAll(projectionRank, "$2", "$3") != fallbackRank {
		t.Fatalf("the rank expressions differ beyond the query parameter:\n%s\n%s",
			projectionRank, fallbackRank)
	}
	if strings.ReplaceAll(projectionCursor, "$2", "$3") != fallbackCursor {
		t.Fatalf("the cursor predicates differ beyond the query parameter:\n%s\n%s",
			projectionCursor, fallbackCursor)
	}

	for _, fragment := range []string{
		"$4::integer IS NULL",
		"$5::bigint",
		"$6::uuid",
		"ORDER BY rank ASC, v.version_no DESC, v.id DESC",
		"LIMIT $7",
	} {
		if !strings.Contains(projectionCursor+projectionOrder, fragment) {
			t.Fatalf("the shared pagination tail lost the %q fragment:\n%s\n%s",
				fragment, projectionCursor, projectionOrder)
		}
	}
}
