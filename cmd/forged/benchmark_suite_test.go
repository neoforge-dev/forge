package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"testing"
	"time"
)

// --- Helpers ----------------------------------------------------------------

func setupBenchmarkDB(b *testing.B) (*sql.DB, func()) {
	b.Helper()

	dbPath := filepath.Join(os.TempDir(), fmt.Sprintf("forge_bench_%d.db", time.Now().UnixNano()))

	db, err := OpenDB(dbPath)
	if err != nil {
		b.Fatalf("failed to open db: %v", err)
	}

	if err := MigrateUp(db); err != nil {
		db.Close()
		b.Fatalf("failed to run migrations: %v", err)
	}

	cleanup := func() {
		db.Close()
		_ = os.Remove(dbPath)
	}

	return db, cleanup
}

func setupBenchmarkQueue(b *testing.B) (TaskQueue, func()) {
	b.Helper()

	dbPath := filepath.Join(os.TempDir(), fmt.Sprintf("forge_queue_bench_%d.db", time.Now().UnixNano()))

	// Open DB and run migrations first, mirroring setupTestQueue.
	db, err := OpenDB(dbPath)
	if err != nil {
		b.Fatalf("failed to open db: %v", err)
	}
	if err := MigrateUp(db); err != nil {
		db.Close()
		b.Fatalf("failed to run migrations: %v", err)
	}
	db.Close()

	q, err := NewTaskQueue(dbPath)
	if err != nil {
		b.Fatalf("failed to create queue: %v", err)
	}

	cleanup := func() {
		_ = q.Close()
		_ = os.Remove(dbPath)
	}

	return q, cleanup
}

// --- 1. XNode Throughput ----------------------------------------------------

func BenchmarkXNodeForwardTask(b *testing.B) {
	db, cleanup := setupBenchmarkDB(b)
	defer cleanup()

	ctx := context.Background()

	// Seed a single online target node.
	_, err := db.ExecContext(ctx,
		`INSERT INTO nodes (id, hostname, address, status, last_heartbeat)
		 VALUES (?, ?, ?, ?, datetime('now'))`,
		"bench-target", "bench-host", "127.0.0.1", "online",
	)
	if err != nil {
		b.Fatalf("failed to seed node: %v", err)
	}

	controller, err := NewXNodeController(db, "bench-source")
	if err != nil {
		b.Fatalf("failed to create XNodeController: %v", err)
	}

	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		taskID := fmt.Sprintf("bench-task-%d", i)
		if err := controller.ForwardTask(ctx, "bench-target", taskID); err != nil {
			b.Fatalf("ForwardTask failed at #%d: %v", i, err)
		}
	}
}

// --- 2. Database Performance -----------------------------------------------

func BenchmarkDBInsertTaskEvent(b *testing.B) {
	db, cleanup := setupBenchmarkDB(b)
	defer cleanup()

	ctx := context.Background()

	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		_, err := db.ExecContext(ctx,
			`INSERT INTO task_events (task_id, domain, project, event_type, payload)
			 VALUES (?, ?, ?, ?, ?)`,
			fmt.Sprintf("bench-task-%d", i),
			"bench-domain",
			"bench-project",
			"bench.event",
			fmt.Sprintf(`{"i":%d}`, i),
		)
		if err != nil {
			b.Fatalf("failed to insert task_event at #%d: %v", i, err)
		}
	}
}

func BenchmarkDBQueryTasksByStatus(b *testing.B) {
	db, cleanup := setupBenchmarkDB(b)
	defer cleanup()

	ctx := context.Background()

	// Seed some tasks in "queued" status.
	for i := 0; i < 256; i++ {
		_, err := db.ExecContext(ctx,
			`INSERT INTO tasks (id, domain, project, type, priority, status, created_at, updated_at)
			 VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))`,
			fmt.Sprintf("bench-db-task-%d", i),
			"bench-domain",
			"bench-project",
			string(TaskTypeFeature),
			10,
			string(TaskStatusQueued),
		)
		if err != nil {
			b.Fatalf("failed to seed tasks: %v", err)
		}
	}

	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		rows, err := db.QueryContext(ctx,
			`SELECT id FROM tasks WHERE status = ? ORDER BY created_at DESC LIMIT 64`,
			string(TaskStatusQueued),
		)
		if err != nil {
			b.Fatalf("query failed: %v", err)
		}
		for rows.Next() {
			var id string
			if err := rows.Scan(&id); err != nil {
				rows.Close()
				b.Fatalf("scan failed: %v", err)
			}
		}
		rows.Close()
	}
}

// --- 3. Agent Spawning (simulated) -----------------------------------------

// BenchmarkAgentCommandFormatting measures the overhead of constructing agent
// spawn command strings (proxy for agent spawn CPU cost without tmux I/O).
func BenchmarkAgentCommandFormatting(b *testing.B) {
	agents := []string{"kimi", "gemini", "minimax", "pi", "opencode", "cursor", "claude"}

	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		for _, name := range agents {
			_ = fmt.Sprintf("forge:%s-%d", name, i)
		}
	}
}

// --- 4. Dashboard Rendering -------------------------------------------------

var sampleDashboardJSON = []byte(`{
  "status": {"uptime":"1m","status":"ok","version":"3.0.0","total_agents":3},
  "agents": [
    {"name":"forge:kimi","status":"online","current_task":"TASK-ALPHA-001","last_heartbeat":"2026-03-04T21:00:00Z"},
    {"name":"forge:gemini","status":"online","current_task":"TASK-BETA-002","last_heartbeat":"2026-03-04T21:00:01Z"}
  ],
  "tasks": [
    {"id":"TASK-ALPHA-001","status":"executing","priority":"high","assignee":"forge:kimi","progress":50},
    {"id":"TASK-BETA-002","status":"queued","priority":"medium","assignee":"","progress":0}
  ],
  "logs": [
    {"timestamp":"2026-03-04T21:00:00Z","level":"info","message":"system ok","source":"server"}
  ]
}`)

func BenchmarkDashboardJSONDecode(b *testing.B) {
	var state DashboardState

	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		if err := json.Unmarshal(sampleDashboardJSON, &state); err != nil {
			b.Fatalf("unmarshal failed: %v", err)
		}
	}
}

// --- 5. Task Queue Processing ----------------------------------------------

func BenchmarkTaskQueueEnqueue(b *testing.B) {
	q, cleanup := setupBenchmarkQueue(b)
	defer cleanup()

	ctx := context.Background()

	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		task := Task{
			ID:       fmt.Sprintf("bench-enqueue-%d", i),
			Domain:   "bench-domain",
			Project:  "bench-project",
			Type:     TaskTypeFeature,
			Priority: 10,
			Status:   TaskStatusQueued,
		}
		if err := q.Enqueue(ctx, task); err != nil {
			b.Fatalf("Enqueue failed at #%d: %v", i, err)
		}
	}
}

func BenchmarkTaskQueueDequeueParallel(b *testing.B) {
	q, cleanup := setupBenchmarkQueue(b)
	defer cleanup()

	ctx := context.Background()

	// Seed tasks proportional to N, but with a floor so we get some work
	// even for small b.N.
	taskCount := b.N
	if taskCount < 1024 {
		taskCount = 1024
	}

	for i := 0; i < taskCount; i++ {
		task := Task{
			ID:       fmt.Sprintf("bench-dequeue-%d", i),
			Domain:   "bench-domain",
			Project:  "bench-project",
			Type:     TaskTypeFeature,
			Priority: 10,
			Status:   TaskStatusQueued,
		}
		if err := q.Enqueue(ctx, task); err != nil {
			b.Fatalf("seed Enqueue failed at #%d: %v", i, err)
		}
	}

	b.ResetTimer()
	b.RunParallel(func(pb *testing.PB) {
		for pb.Next() {
			_, err := q.Dequeue(ctx, "bench-agent")
			if err != nil {
				// When the queue is empty, Dequeue will eventually fail; treat
				// that as completion and stop this goroutine.
				return
			}
		}
	})
}

// BenchmarkTaskQueueGetClaimableAtDepth measures GetClaimableTasks at queue depth 500.
func BenchmarkTaskQueueGetClaimableAtDepth(b *testing.B) {
	q, cleanup := setupBenchmarkQueue(b)
	defer cleanup()

	ctx := context.Background()
	const depth = 500

	for i := 0; i < depth; i++ {
		task := Task{
			ID:       fmt.Sprintf("bench-claimable-%d", i),
			Domain:   "bench-domain",
			Project:  "bench-project",
			Type:     TaskTypeFeature,
			Priority: 10,
			Status:   TaskStatusQueued,
		}
		if err := q.Enqueue(ctx, task); err != nil {
			b.Fatalf("seed Enqueue failed: %v", err)
		}
	}

	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		_, err := q.GetClaimableTasks(ctx, 64)
		if err != nil {
			b.Fatalf("GetClaimableTasks failed: %v", err)
		}
	}
}