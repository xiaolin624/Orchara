// Command worker is the platform runtime entry point (F00 skeleton).
//
// It deliberately does nothing yet beyond proving the runtime path exists: it
// connects with the restricted role, verifies the schema it is allowed to see,
// and keeps a heartbeat until it is asked to stop. Business nodes, leases and
// the supervisor protocol arrive in later tickets; nothing here should be read
// as a working executor.
package main

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"log"
	"os"
	"os/signal"
	"syscall"
	"time"

	_ "github.com/jackc/pgx/v5/stdlib"
)

func main() {
	logger := log.New(os.Stdout, "orchara-worker ", log.LstdFlags|log.LUTC)
	if err := run(logger); err != nil {
		logger.Printf("fatal: %v", err)
		os.Exit(1)
	}
}

func run(logger *log.Logger) error {
	dsn := os.Getenv("ORCHARA_WORKER_DSN")
	if dsn == "" {
		// Fail closed: a runtime without a database is not a runtime.
		return errors.New("ORCHARA_WORKER_DSN is not set; refusing to start")
	}

	db, err := sql.Open("pgx", dsn)
	if err != nil {
		// Never include the DSN: it carries credentials.
		return errors.New("cannot open the worker database handle")
	}
	defer db.Close()
	db.SetMaxOpenConns(2)

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	var role string
	if err := db.QueryRowContext(ctx, "SELECT current_user").Scan(&role); err != nil {
		return fmt.Errorf("worker cannot query the database as configured: %w", err)
	}
	var searchPath string
	if err := db.QueryRowContext(ctx, "SHOW search_path").Scan(&searchPath); err != nil {
		return fmt.Errorf("worker cannot read search_path: %w", err)
	}
	logger.Printf("runtime ready: role=%s search_path=%s (skeleton, no work claimed)", role, searchPath)

	stop := make(chan os.Signal, 1)
	signal.Notify(stop, os.Interrupt, syscall.SIGTERM)
	ticker := time.NewTicker(5 * time.Second)
	defer ticker.Stop()

	for {
		select {
		case <-stop:
			logger.Print("shutting down")
			return nil
		case <-ticker.C:
			var one int
			if err := db.QueryRowContext(ctx, "SELECT 1").Scan(&one); err != nil {
				logger.Printf("heartbeat failed: %v", err)
				continue
			}
			logger.Print("heartbeat ok")
		}
	}
}
