// Command worker is the platform runtime entry point (F00 skeleton).
//
// It deliberately executes no business work yet. What it does do is take part in
// the liveness protocol the supervisor depends on: register itself, heartbeat,
// and record a clean stop. Without that, the supervisor cannot tell a dead
// worker from a busy one, and "park the run" would be guesswork.
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

const heartbeatInterval = 5 * time.Second

func main() {
	logger := log.New(os.Stdout, "orchara-worker ", log.LstdFlags|log.LUTC)
	if err := run(logger); err != nil {
		logger.Printf("fatal: %v", err)
		os.Exit(1)
	}
}

func workerID() string {
	if id := os.Getenv("ORCHARA_WORKER_ID"); id != "" {
		return id
	}
	host, err := os.Hostname()
	if err != nil {
		host = "unknown-host"
	}
	return fmt.Sprintf("%s:%d", host, os.Getpid())
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

	id := workerID()
	var role string
	if err := db.QueryRowContext(ctx, "SELECT current_user").Scan(&role); err != nil {
		return fmt.Errorf("worker cannot query the database as configured: %w", err)
	}
	if _, err := db.ExecContext(ctx, `
		INSERT INTO app.workers (worker_id) VALUES ($1)
		ON CONFLICT (worker_id) DO UPDATE
		SET heartbeat_at = now(), started_at = now(), stopped_at = NULL`, id); err != nil {
		return fmt.Errorf("worker cannot register itself: %w", err)
	}
	logger.Printf("runtime ready: id=%s role=%s (skeleton, no work claimed)", id, role)

	stop := make(chan os.Signal, 1)
	signal.Notify(stop, os.Interrupt, syscall.SIGTERM)
	ticker := time.NewTicker(heartbeatInterval)
	defer ticker.Stop()

	for {
		select {
		case <-stop:
			if _, err := db.ExecContext(ctx,
				"UPDATE app.workers SET stopped_at = now() WHERE worker_id = $1", id); err != nil {
				logger.Printf("could not record clean stop: %v", err)
				return err
			}
			logger.Print("shutting down (clean stop recorded)")
			return nil
		case <-ticker.C:
			if _, err := db.ExecContext(ctx,
				"UPDATE app.workers SET heartbeat_at = now() WHERE worker_id = $1", id); err != nil {
				logger.Printf("heartbeat failed: %v", err)
				continue
			}
			logger.Print("heartbeat ok")
		}
	}
}
