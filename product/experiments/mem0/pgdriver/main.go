// Command pgdriver is the T066 experiment's bridge to the real F09 memory store.
//
// It is experiment code (product/experiments/mem0), not a product binary: it is
// built only by the experiment and its tests. It exists so the PostgreSQL side of
// the comparison is produced by the real `internal/memory` service — the same
// Create/Update/Delete the HTTP API calls — instead of a Python re-implementation
// of the SQL. It also times the recall query over a persistent connection, which
// a one-shot `psql` subprocess could not measure honestly.
//
// Input is JSON lines on stdin, output is JSON lines on stdout; no body or
// credential is ever passed on the command line.
//
//	ORCHARA_DB_DSN      the isolated throwaway database
//	ORCHARA_OBJECT_DIR  the isolated object-store root
//
// Subcommands:
//
//	create  {"owner_id","application_id","text"}                  -> {"ok","id","revision"}
//	update  {"owner_id","memory_id","text","if_match"}            -> {"ok","id","revision"}
//	delete  {"owner_id","memory_id"}                              -> {"ok","id","state","resource_id"}
//	bench   {"owner_id","application_id","query_vectors":[[..]],"iterations":21,"top_k":3}
//	        -> {"ok","runs":[{"query_index","durations_ns","ids"}]}
package main

import (
	"bufio"
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"strings"
	"time"

	_ "github.com/jackc/pgx/v5/stdlib"

	"orchara/platform/internal/catalog"
	"orchara/platform/internal/memory"
	"orchara/platform/internal/objectstore"
	"orchara/platform/internal/policy"
)

type createInput struct {
	OwnerID       string `json:"owner_id"`
	ApplicationID string `json:"application_id"`
	Text          string `json:"text"`
}

type updateInput struct {
	OwnerID  string `json:"owner_id"`
	MemoryID string `json:"memory_id"`
	Text     string `json:"text"`
	IfMatch  string `json:"if_match"`
}

type deleteInput struct {
	OwnerID  string `json:"owner_id"`
	MemoryID string `json:"memory_id"`
}

type benchInput struct {
	OwnerID       string      `json:"owner_id"`
	ApplicationID string      `json:"application_id"`
	QueryVectors  [][]float64 `json:"query_vectors"`
	Iterations    int         `json:"iterations"`
	TopK          int         `json:"top_k"`
}

type benchRun struct {
	QueryIndex  int      `json:"query_index"`
	DurationsNS []int64  `json:"durations_ns"`
	IDs         []string `json:"ids"`
}

func main() {
	if len(os.Args) < 2 {
		fail("usage: pgdriver create|update|delete|bench")
	}
	mode := os.Args[1]
	dsn := strings.TrimSpace(os.Getenv("ORCHARA_DB_DSN"))
	if dsn == "" {
		fail("ORCHARA_DB_DSN is not set")
	}
	objectDir := strings.TrimSpace(os.Getenv("ORCHARA_OBJECT_DIR"))
	if objectDir == "" {
		fail("ORCHARA_OBJECT_DIR is not set")
	}
	db, err := sql.Open("pgx", dsn)
	if err != nil {
		fail("opening the database: " + err.Error())
	}
	defer func() { _ = db.Close() }()
	ctx := context.Background()
	if err := db.PingContext(ctx); err != nil {
		fail("pinging the database: " + err.Error())
	}
	objects, err := objectstore.Open(objectDir)
	if err != nil {
		fail("opening the object store: " + err.Error())
	}
	service := memory.New(db, objects, catalog.New(db, policy.New(db)), 0)

	switch mode {
	case "create":
		runCreate(ctx, service)
	case "update":
		runUpdate(ctx, service)
	case "delete":
		runDelete(ctx, service)
	case "bench":
		runBench(ctx, db)
	default:
		fail("unknown subcommand " + mode)
	}
}

func runCreate(ctx context.Context, service *memory.Service) {
	scanner := bufio.NewScanner(os.Stdin)
	scanner.Buffer(make([]byte, 0, 64*1024), 4*1024*1024)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" {
			continue
		}
		var input createInput
		if err := json.Unmarshal([]byte(line), &input); err != nil {
			fail("decoding a create input: " + err.Error())
		}
		created, err := service.Create(ctx, input.OwnerID, memory.CreateInput{
			ApplicationID: input.ApplicationID,
			Text:          input.Text,
			Confirmed:     true,
		})
		if err != nil {
			fail("creating a memory: " + err.Error())
		}
		emit(map[string]any{"ok": true, "id": created.ID, "revision": created.Revision})
	}
	if err := scanner.Err(); err != nil {
		fail("reading stdin: " + err.Error())
	}
}

func runUpdate(ctx context.Context, service *memory.Service) {
	scanner := bufio.NewScanner(os.Stdin)
	scanner.Buffer(make([]byte, 0, 64*1024), 4*1024*1024)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" {
			continue
		}
		var input updateInput
		if err := json.Unmarshal([]byte(line), &input); err != nil {
			fail("decoding an update input: " + err.Error())
		}
		updated, err := service.Update(ctx, input.OwnerID, memory.UpdateInput{
			MemoryID:  input.MemoryID,
			Text:      input.Text,
			Confirmed: true,
			IfMatch:   input.IfMatch,
		})
		if err != nil {
			fail("updating a memory: " + err.Error())
		}
		emit(map[string]any{"ok": true, "id": updated.ID, "revision": updated.Revision})
	}
	if err := scanner.Err(); err != nil {
		fail("reading stdin: " + err.Error())
	}
}

func runDelete(ctx context.Context, service *memory.Service) {
	scanner := bufio.NewScanner(os.Stdin)
	scanner.Buffer(make([]byte, 0, 64*1024), 4*1024*1024)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" {
			continue
		}
		var input deleteInput
		if err := json.Unmarshal([]byte(line), &input); err != nil {
			fail("decoding a delete input: " + err.Error())
		}
		job, err := service.Delete(ctx, input.OwnerID, input.MemoryID)
		if err != nil {
			fail("deleting a memory: " + err.Error())
		}
		emit(map[string]any{
			"ok": true, "id": job.ID, "state": job.State, "resource_id": job.ResourceID,
		})
	}
	if err := scanner.Err(); err != nil {
		fail("reading stdin: " + err.Error())
	}
}

// runBench times the recall query against the experiment's vector table.
//
// The query is the authoritative scope read (owner + application + not deleted)
// joined to the current revision's vector, which is exactly the guard T058 set up
// for T063/T067. The vector table is experiment-owned: the platform baseline has
// no embedding index (T063 does not build one; this experiment supplies the
// cheapest credible deterministic one so recall can be compared at all).
func runBench(ctx context.Context, db *sql.DB) {
	raw, err := io.ReadAll(io.LimitReader(os.Stdin, 8*1024*1024))
	if err != nil {
		fail("reading bench input: " + err.Error())
	}
	var input benchInput
	if err := json.Unmarshal(raw, &input); err != nil {
		fail("decoding bench input: " + err.Error())
	}
	if input.Iterations < 20 {
		fail("bench needs at least 20 iterations for p50/p95")
	}
	if input.TopK <= 0 {
		input.TopK = 3
	}
	const query = `
		SELECT m.id::text
		  FROM platform.memories AS m
		  JOIN t066.memory_vectors AS v
		    ON v.memory_id = m.id AND v.revision = m.revision
		 WHERE m.owner_id = $1::uuid
		   AND m.application_id = $2::uuid
		   AND m.deleted_at IS NULL
		 ORDER BY (
		      SELECT COALESCE(sum(a * b), 0)
		        FROM unnest(v.embedding, $3::float8[]) AS t(a, b)
		 ) DESC
		 LIMIT $4`
	runs := make([]benchRun, 0, len(input.QueryVectors))
	for index, vector := range input.QueryVectors {
		run := benchRun{QueryIndex: index, DurationsNS: make([]int64, 0, input.Iterations)}
		for iteration := 0; iteration < input.Iterations; iteration++ {
			started := time.Now()
			rows, err := db.QueryContext(ctx, query, input.OwnerID, input.ApplicationID, vector, input.TopK)
			if err != nil {
				fail("benchmark query: " + err.Error())
			}
			ids := make([]string, 0, input.TopK)
			for rows.Next() {
				var id string
				if err := rows.Scan(&id); err != nil {
					_ = rows.Close()
					fail("scanning the benchmark query: " + err.Error())
				}
				ids = append(ids, id)
			}
			if err := rows.Err(); err != nil {
				_ = rows.Close()
				fail("running the benchmark query: " + err.Error())
			}
			_ = rows.Close()
			run.DurationsNS = append(run.DurationsNS, time.Since(started).Nanoseconds())
			if iteration == 0 {
				run.IDs = ids
			}
		}
		runs = append(runs, run)
	}
	emit(map[string]any{"ok": true, "runs": runs})
}

func emit(payload any) {
	encoded, err := json.Marshal(payload)
	if err != nil {
		fail("encoding output: " + err.Error())
	}
	fmt.Println(string(encoded))
}

func fail(message string) {
	encoded, _ := json.Marshal(map[string]any{"ok": false, "error": message})
	fmt.Println(string(encoded))
	os.Exit(1)
}
