// Command api is the Orchara platform HTTP entry point (F00 skeleton).
//
// It deliberately does very little: it exists so that liveness, readiness and
// contract drift can be exercised end to end before any business module is
// written. Readiness fails closed when the database is missing or unreachable;
// see internal/health.
//
// F00B/T022 adds an optional second, TLS-only listener for the internal
// maintenance/worker/supervisor protocol. It is separate from the public mux on
// purpose: the internal contract paths must not be reachable with a user cookie
// or API key, and the public pages must not be reachable with an internal
// certificate.
package main

import (
	"context"
	"crypto/tls"
	"crypto/x509"
	"database/sql"
	"errors"
	"fmt"
	"log"
	"net"
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
	"orchara/platform/internal/objectstore"
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

	objects, err := openObjectStore(cfg.ObjectDir)
	if err != nil {
		return fmt.Errorf("object store rejected: %w", err)
	}
	if objects == nil {
		logger.Print("object store is not configured (ORCHARA_OBJECT_DIR); artifact activation will fail closed")
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
	// F00B/T022 adds the background-job read and the internal controller routes.
	identityService := identity.New(db)
	apiServer := api.New(identityService, cfg.WebDir, cfg.Profile, logger, objects, cfg.RetryBackoff)
	apiServer.Register(mux)

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

	// The internal listener is built before either server starts, so a missing
	// certificate or an occupied port refuses startup instead of leaving a
	// half-running process.
	var (
		internalServer   *http.Server
		internalListener net.Listener
	)
	if cfg.Internal != nil {
		tlsConfig, err := internalTLSConfig(cfg.Internal)
		if err != nil {
			return fmt.Errorf("internal listener rejected: %w", err)
		}
		internalMux := http.NewServeMux()
		internalMux.HandleFunc("/health/live", reporter.Live)
		apiServer.RegisterInternal(internalMux)
		internalListener, err = net.Listen("tcp", cfg.Internal.Addr)
		if err != nil {
			return fmt.Errorf("internal listener rejected: cannot listen on %s", cfg.Internal.Addr)
		}
		internalServer = &http.Server{
			Handler: api.WithRequestLogging(logger,
				api.WithRequestID(api.RequireInternalMTLS(internalMux))),
			ReadHeaderTimeout: 5 * time.Second,
			TLSConfig:         tlsConfig,
		}
	}

	serverErr := make(chan error, 2)
	go func() {
		serverErr <- server.ListenAndServe()
	}()
	if internalServer != nil {
		tlsListener := tls.NewListener(internalListener, internalServer.TLSConfig)
		go func() {
			serverErr <- internalServer.Serve(tlsListener)
		}()
	}

	stop := make(chan os.Signal, 1)
	signal.Notify(stop, os.Interrupt, syscall.SIGTERM)
	select {
	case received := <-stop:
		logger.Printf("shutting down (%s)", received)
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = server.Shutdown(ctx)
		if internalServer != nil {
			_ = internalServer.Shutdown(ctx)
		}
		return nil
	case serveErr := <-serverErr:
		if internalServer != nil {
			_ = internalServer.Close()
		}
		if errors.Is(serveErr, http.ErrServerClosed) {
			return nil
		}
		return fmt.Errorf("http server failed: %w", serveErr)
	}
}

// openObjectStore configures the object store only when a root is set. An empty
// root is not an error at startup: the API is useful for identity work without
// objects, and every operation that needs bytes refuses later.
func openObjectStore(root string) (*objectstore.Store, error) {
	if root == "" {
		return nil, nil
	}
	return objectstore.Open(root)
}

// internalTLSConfig loads the server identity and the client CA. The listener
// requires and verifies a client certificate; TLS version is pinned to 1.2 or
// newer. Any missing or unreadable file refuses startup.
func internalTLSConfig(internal *config.InternalTLS) (*tls.Config, error) {
	certificate, err := tls.LoadX509KeyPair(internal.ServerCert, internal.ServerKey)
	if err != nil {
		return nil, fmt.Errorf("cannot load the internal server certificate: %w", err)
	}
	caPEM, err := os.ReadFile(internal.ClientCA)
	if err != nil {
		// The path is configuration, not a secret; the bytes are not logged.
		return nil, errors.New("cannot read the internal client CA file")
	}
	pool := x509.NewCertPool()
	if !pool.AppendCertsFromPEM(caPEM) {
		return nil, errors.New("the internal client CA file contains no certificate")
	}
	return &tls.Config{
		MinVersion:   tls.VersionTLS12,
		Certificates: []tls.Certificate{certificate},
		ClientAuth:   tls.RequireAndVerifyClientCert,
		ClientCAs:    pool,
	}, nil
}

func env(key, fallback string) string {
	if value := os.Getenv(key); value != "" {
		return value
	}
	return fallback
}
