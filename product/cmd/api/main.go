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

	"orchara/platform/internal/health"
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
	addr := env("ORCHARA_HTTP_ADDR", defaultAddr)
	dsn := os.Getenv("ORCHARA_DB_DSN")
	webDir := env("ORCHARA_WEB_DIR", filepath.Join("apps", "web", "src"))

	var db *sql.DB
	var dbCheck health.Check
	if dsn == "" {
		// Not configured is not the same as healthy: readiness stays failed.
		logger.Print("ORCHARA_DB_DSN is not set; readiness will report not_configured")
	} else {
		opened, err := sql.Open("pgx", dsn)
		if err != nil {
			// Deliberately not including the DSN: it carries credentials.
			return errors.New("cannot open database handle")
		}
		opened.SetMaxOpenConns(4)
		opened.SetConnMaxLifetime(5 * time.Minute)
		db = opened
		defer db.Close()
		dbCheck = func(ctx context.Context) error {
			var one int
			return db.QueryRowContext(ctx, "SELECT 1").Scan(&one)
		}
	}

	reporter := health.New(map[string]health.Check{"database": dbCheck}, 2*time.Second).
		WithVersion(env("ORCHARA_VERSION", "0.0.1-skeleton"))

	mux := http.NewServeMux()
	// Contract paths (contracts/openapi.json: /health/live, /health/ready with
	// operationIds liveness/readiness) plus the conventional short aliases.
	mux.HandleFunc("/health/live", reporter.Live)
	mux.HandleFunc("/health/ready", reporter.Ready)
	mux.HandleFunc("/healthz", reporter.Live)
	mux.HandleFunc("/readyz", reporter.Ready)
	mux.HandleFunc("/api/status", reporter.Status)

	indexPath := filepath.Join(webDir, "index.html")
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
		Addr:              addr,
		Handler:           mux,
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

	logger.Printf("listening on http://%s/ (web: %s)", addr, indexPath)
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
