// Command api is the Orchara platform HTTP entry point (F00 skeleton).
//
// It deliberately does very little: it exists so that liveness, readiness and
// contract drift can be exercised end to end before any business module is
// written. Readiness fails closed when the database is missing or unreachable;
// see internal/health.
package main

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"log"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"syscall"
	"time"

	_ "github.com/jackc/pgx/v5/stdlib"

	"orchara/platform/internal/api"
	"orchara/platform/internal/config"
	"orchara/platform/internal/health"
	"orchara/platform/internal/identity"
)

const defaultAddr = "127.0.0.1:8088"

func main() {
	logger := log.New(os.Stdout, "orchara-api ", log.LstdFlags|log.LUTC)
	if err := run(logger); err != nil {
		logger.Printf("fatal: %v", err)
		os.Exit(1)
	}
}

func run(logger *log.Logger) error {
	cfg, err := config.Load(config.FromEnv())
	if err != nil {
		// Refuse to start rather than serve as a healthy-looking corpse.
		return fmt.Errorf("configuration rejected: %w", err)
	}

	var db *sql.DB
	opened, openErr := sql.Open("pgx", cfg.DBDSN)
	if openErr != nil {
		// Deliberately not including the DSN: it carries credentials.
		return errors.New("cannot open database handle")
	}
	opened.SetMaxOpenConns(4)
	opened.SetConnMaxLifetime(5 * time.Minute)
	db = opened
	defer db.Close()
	dbCheck := func(ctx context.Context) error {
		var one int
		return db.QueryRowContext(ctx, "SELECT 1").Scan(&one)
	}

	reporter := health.New(map[string]health.Check{"database": dbCheck}, 2*time.Second).
		WithVersion(env("ORCHARA_VERSION", "0.0.1-skeleton")).
		WithProfile(string(cfg.Profile), cfg.Stub())

	mux := http.NewServeMux()
	// Contract paths (contracts/openapi.json: /health/live, /health/ready with
	// operationIds liveness/readiness) plus the conventional short aliases.
	mux.HandleFunc("/health/live", reporter.Live)
	mux.HandleFunc("/health/ready", reporter.Ready)
	mux.HandleFunc("/healthz", reporter.Live)
	mux.HandleFunc("/readyz", reporter.Ready)
	mux.HandleFunc("/api/status", reporter.Status)

	// F01 identity surface: login/invite/activate plus the pages that drive them.
	// Full login/logout/refresh/CSRF lifecycle lands with T017.
	identityService := identity.New(db)
	api.New(identityService, cfg.WebDir, cfg.Profile, logger).Register(mux)

	indexPath := filepath.Join(cfg.WebDir, "index.html")
	if _, err := os.Stat(indexPath); err != nil {
		return fmt.Errorf("web entry not found at %s (set ORCHARA_WEB_DIR)", indexPath)
	}
	mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/" {
			http.NotFound(w, r)
			return
		}
		http.ServeFile(w, r, indexPath)
	})

	server := &http.Server{
		Addr:              cfg.HTTPAddr,
		Handler:           api.WithRequestLogging(logger, api.WithRequestID(mux)),
		ReadHeaderTimeout: 5 * time.Second,
	}

	stop := make(chan os.Signal, 1)
	signal.Notify(stop, os.Interrupt, syscall.SIGTERM)
	go func() {
		<-stop
		logger.Print("shutting down")
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = server.Shutdown(ctx)
	}()

	logger.Printf("listening on http://%s/ (profile=%s stub=%t web=%s)",
		cfg.HTTPAddr, cfg.Profile, cfg.Stub(), indexPath)
	if err := server.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		return fmt.Errorf("http server failed: %w", err)
	}
	return nil
}

func env(key, fallback string) string {
	if value := os.Getenv(key); value != "" {
		return value
	}
	return fallback
}
