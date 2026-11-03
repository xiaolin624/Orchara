// Command admin: seed the F04A/T049 sales template.
//
// The command is a server-controlled bootstrap, not a publication:
//
//	orchara-admin seed-sales-template --owner-login ADMIN --model-version-id UUID
//
// It requires a model profile version that already passed the real T039
// register/validate flow (a stub profile in this build), records the fixed
// preset sales_stats tool version with the content digest of the real script,
// and creates/updates the template source draft (revision 1). Re-running it is
// idempotent and prints the same identifiers.
package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"strings"

	"orchara/platform/internal/config"
	"orchara/platform/internal/templates"
	"orchara/platform/internal/uuid"
)

func runSeedSalesTemplate(args []string) error {
	flags := flag.NewFlagSet("seed-sales-template", flag.ContinueOnError)
	flags.SetOutput(io.Discard)
	ownerLogin := flags.String("owner-login", "", "login of the administrator that owns the template source draft")
	modelVersionID := flags.String("model-version-id", "", "passed stub model profile version id (from the real T039 flow)")
	toolScript := flags.String("tool-script", "", "path to sales_stats.py (default: ORCHARA_PRODUCT_ROOT/worker/platform_worker/nodes/sales_stats.py)")
	if err := flags.Parse(args); err != nil {
		return usageErrorf("%v", err)
	}
	login := strings.TrimSpace(*ownerLogin)
	if login == "" {
		return usageErrorf("--owner-login is required")
	}
	model := strings.ToLower(strings.TrimSpace(*modelVersionID))
	if !uuid.Valid(model) {
		return usageErrorf("--model-version-id must be a UUID")
	}

	cfg, err := config.LoadDatabase(config.FromEnv())
	if err != nil {
		return err
	}
	db, err := openDatabase(cfg.DBDSN)
	if err != nil {
		return err
	}
	defer db.Close()

	ctx, cancel := context.WithTimeout(context.Background(), commandTimeout)
	defer cancel()
	if err := db.PingContext(ctx); err != nil {
		return errors.New("cannot reach the database")
	}

	service := templates.New(db)
	service.ProductRoot = strings.TrimSpace(os.Getenv("ORCHARA_PRODUCT_ROOT"))
	service.ToolScriptPath = strings.TrimSpace(*toolScript)
	summary, err := service.SeedSalesTemplate(ctx, login, model)
	if err != nil {
		return fmt.Errorf("seeding the sales template: %w", err)
	}
	return printJSONLine(summary)
}
