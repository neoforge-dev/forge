//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"database/sql"
	"fmt"
	"os"
	"path/filepath"
	"testing"
	"time"
)

// setupOrchestratorTestDB creates a temporary SQLite database with all
// migrations applied. Each test gets its own database to avoid cross-test
// contamination. The returned cleanup function removes both the DB file and
// any associated WAL/SHM files.
func setupOrchestratorTestDB(t *testing.T) (*sql.DB, func()) {
	t.Helper()

	dbPath := filepath.Join(os.TempDir(), fmt.Sprintf("test_orch_%d_%d.db", time.Now().UnixNano(), os.Getpid()))

	db, err := OpenDB(dbPath)
	if err != nil {
		t.Fatalf("setupOrchestratorTestDB: open: %v", err)
	}

	if err := MigrateUp(db); err != nil {
		db.Close()
		t.Fatalf("setupOrchestratorTestDB: migrate: %v", err)
	}

	cleanup := func() {
		db.Close()
		_ = os.Remove(dbPath)
		_ = os.Remove(dbPath + "-wal")
		_ = os.Remove(dbPath + "-shm")
	}
	return db, cleanup
}

// insertHeartbeat inserts a row into agent_heartbeats for testing.
// The node value defaults to "test-node" to satisfy the NOT NULL constraint.
func insertHeartbeat(t *testing.T, db *sql.DB, agentID, status string, lastSeen time.Time, currentTaskID string) {
	t.Helper()
	_, err := db.Exec(`
		INSERT INTO agent_heartbeats (agent_id, node, status, last_seen, current_task_id)
		VALUES (?, ?, ?, ?, ?)
	`, agentID, "test-node", status, lastSeen.UTC().Format(time.RFC3339), currentTaskID)
	if err != nil {
		t.Fatalf("insertHeartbeat(%s): %v", agentID, err)
	}
}

// insertQueuedTask inserts a task with status='queued' and no assignee.
func insertQueuedTask(t *testing.T, db *sql.DB, id, domain, taskType string) {
	t.Helper()
	now := time.Now().UTC().Format(time.RFC3339)
	_, err := db.Exec(`
		INSERT INTO tasks (id, domain, project, type, title, priority, status, state, created_at, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, 'queued', 'QUEUED', ?, ?)
	`, id, domain, "test-project", taskType, "Test task "+id, 50, now, now)
	if err != nil {
		t.Fatalf("insertQueuedTask(%s): %v", id, err)
	}
}

// insertAssignedTask inserts a task with status='assigned' to agentID.
func insertAssignedTask(t *testing.T, db *sql.DB, id, agentID string, updatedAt time.Time) {
	t.Helper()
	now := time.Now().UTC().Format(time.RFC3339)
	_, err := db.Exec(`
		INSERT INTO tasks (id, domain, project, type, title, priority, status, state, assigned_to, created_at, updated_at)
		VALUES (?, 'test-domain', 'test-project', 'feature', ?, 50, 'assigned', 'DISPATCHED', ?, ?, ?)
	`, id, "Assigned task "+id, agentID, now, updatedAt.UTC().Format(time.RFC3339))
	if err != nil {
		t.Fatalf("insertAssignedTask(%s): %v", id, err)
	}
}

// --- Tests for orchestratorHeartbeat ---

func TestOrchestratorPatrolHeartbeatUpdatesRow(t *testing.T) {
	db, cleanup := setupOrchestratorTestDB(t)
	defer cleanup()

	nodeID := "test-orchestrator-node"
	staleTime := time.Now().Add(-10 * time.Minute)
	insertHeartbeat(t, db, nodeID, "offline", staleTime, "")

	if err := orchestratorHeartbeat(context.Background(), db, nodeID); err != nil {
		t.Fatalf("orchestratorHeartbeat error: %v", err)
	}

	var status, lastSeen string
	if err := db.QueryRow(`SELECT status, last_seen FROM agent_heartbeats WHERE agent_id = ?`, nodeID).
		Scan(&status, &lastSeen); err != nil {
		t.Fatalf("query heartbeat: %v", err)
	}

	if status != "online" {
		t.Errorf("expected status='online', got %q", status)
	}

	// last_seen should be recent (within the last minute).
	ts, err := time.Parse(time.RFC3339, lastSeen)
	if err != nil {
		t.Fatalf("parse last_seen %q: %v", lastSeen, err)
	}
	if time.Since(ts) > time.Minute {
		t.Errorf("last_seen %v is not recent (expected within 1 minute)", ts)
	}
}

func TestOrchestratorPatrolHeartbeatNilDB(t *testing.T) {
	// nil db must not panic — orchestratorAutoDispatch guards it.
	if err := orchestratorAutoDispatch(context.Background(), nil); err != nil {
		t.Fatalf("unexpected error with nil db: %v", err)
	}
}

// --- Tests for orchestratorDispatchQueued ---

func TestOrchestratorPatrolDispatchQueuedToIdleAgent(t *testing.T) {
	db, cleanup := setupOrchestratorTestDB(t)
	defer cleanup()

	// One queued task, one online idle agent.
	insertQueuedTask(t, db, "task-dispatch-1", "test-domain", "feature")
	insertHeartbeat(t, db, "agent-idle-1", "online", time.Now(), "")

	if err := orchestratorDispatchQueued(context.Background(), db); err != nil {
		t.Fatalf("orchestratorDispatchQueued error: %v", err)
	}

	var status, assignedTo string
	if err := db.QueryRow(`SELECT status, IFNULL(assigned_to,'') FROM tasks WHERE id = ?`, "task-dispatch-1").
		Scan(&status, &assignedTo); err != nil {
		t.Fatalf("query task: %v", err)
	}

	if status != "assigned" {
		t.Errorf("expected status='assigned', got %q", status)
	}
	if assignedTo == "" {
		t.Error("expected assigned_to to be set, got empty string")
	}
}

func TestOrchestratorPatrolDispatchQueuedNoIdleAgents(t *testing.T) {
	db, cleanup := setupOrchestratorTestDB(t)
	defer cleanup()

	// One queued task, but no online idle agents.
	insertQueuedTask(t, db, "task-no-agent-1", "test-domain", "feature")
	insertHeartbeat(t, db, "agent-busy-1", "online", time.Now(), "task-other-1")

	if err := orchestratorDispatchQueued(context.Background(), db); err != nil {
		t.Fatalf("orchestratorDispatchQueued error: %v", err)
	}

	var status string
	if err := db.QueryRow(`SELECT status FROM tasks WHERE id = ?`, "task-no-agent-1").Scan(&status); err != nil {
		t.Fatalf("query task: %v", err)
	}

	// Task must remain queued — no idle agent available.
	if status != "queued" {
		t.Errorf("expected task to remain 'queued', got %q", status)
	}
}

func TestOrchestratorPatrolDispatchQueuedNoTasks(t *testing.T) {
	db, cleanup := setupOrchestratorTestDB(t)
	defer cleanup()

	// Online idle agent but no queued tasks.
	insertHeartbeat(t, db, "agent-idle-2", "online", time.Now(), "")

	if err := orchestratorDispatchQueued(context.Background(), db); err != nil {
		t.Fatalf("orchestratorDispatchQueued error on empty queue: %v", err)
	}
	// No assertion needed; we just verify no error and no panic.
}

// --- Tests for orchestratorRequeueStale ---

func TestOrchestratorPatrolRequeueStaleOfflineAgent(t *testing.T) {
	db, cleanup := setupOrchestratorTestDB(t)
	defer cleanup()

	agentID := "agent-offline-stale"
	// Agent has been offline for 35 minutes.
	staleTime := time.Now().Add(-35 * time.Minute)
	insertHeartbeat(t, db, agentID, "offline", staleTime, "")
	insertAssignedTask(t, db, "task-stale-1", agentID, time.Now().Add(-35*time.Minute))

	if err := orchestratorRequeueStale(context.Background(), db); err != nil {
		t.Fatalf("orchestratorRequeueStale error: %v", err)
	}

	var status string
	var assignedTo sql.NullString
	if err := db.QueryRow(`SELECT status, assigned_to FROM tasks WHERE id = ?`, "task-stale-1").
		Scan(&status, &assignedTo); err != nil {
		t.Fatalf("query task: %v", err)
	}

	if status != "queued" {
		t.Errorf("expected status='queued' after requeue, got %q", status)
	}
	if assignedTo.Valid && assignedTo.String != "" {
		t.Errorf("expected assigned_to=NULL after requeue, got %q", assignedTo.String)
	}
}

func TestOrchestratorPatrolRequeueStaleRecentlyOfflineNotRequeued(t *testing.T) {
	db, cleanup := setupOrchestratorTestDB(t)
	defer cleanup()

	agentID := "agent-offline-recent"
	// Agent went offline only 5 minutes ago — within the 30-minute grace period.
	recentTime := time.Now().Add(-5 * time.Minute)
	insertHeartbeat(t, db, agentID, "offline", recentTime, "")
	insertAssignedTask(t, db, "task-recent-offline", agentID, time.Now())

	if err := orchestratorRequeueStale(context.Background(), db); err != nil {
		t.Fatalf("orchestratorRequeueStale error: %v", err)
	}

	var status string
	if err := db.QueryRow(`SELECT status FROM tasks WHERE id = ?`, "task-recent-offline").Scan(&status); err != nil {
		t.Fatalf("query task: %v", err)
	}

	// Task must still be assigned — offline duration < 30 min.
	if status != "assigned" {
		t.Errorf("expected status='assigned' (agent went offline recently), got %q", status)
	}
}

// --- Tests for orchestratorRouteOpenClaw ---

func TestOrchestratorPatrolRouteOpenClawAssignsLane(t *testing.T) {
	db, cleanup := setupOrchestratorTestDB(t)
	defer cleanup()

	insertQueuedTask(t, db, "task-openclaw-1", "openclaw", "feature")

	if err := orchestratorRouteOpenClaw(context.Background(), db); err != nil {
		t.Fatalf("orchestratorRouteOpenClaw error: %v", err)
	}

	var lane sql.NullString
	if err := db.QueryRow(`SELECT lane FROM tasks WHERE id = ?`, "task-openclaw-1").Scan(&lane); err != nil {
		t.Fatalf("query task lane: %v", err)
	}

	// openclaw is an infrastructure domain; AutoAssignLane should assign a non-empty lane.
	if !lane.Valid || lane.String == "" {
		t.Errorf("expected non-empty lane for openclaw task, got %v", lane)
	}
}

func TestOrchestratorPatrolRouteOpenClawSkipsAlreadyLaned(t *testing.T) {
	db, cleanup := setupOrchestratorTestDB(t)
	defer cleanup()

	// Insert a task that already has a lane assigned.
	now := time.Now().UTC().Format(time.RFC3339)
	_, err := db.Exec(`
		INSERT INTO tasks (id, domain, project, type, title, priority, status, state, lane, created_at, updated_at)
		VALUES ('task-alreadylaned', 'openclaw', 'test-project', 'feature', 'Already laned', 50, 'queued', 'QUEUED', 'implementation', ?, ?)
	`, now, now)
	if err != nil {
		t.Fatalf("insert task: %v", err)
	}

	if err := orchestratorRouteOpenClaw(context.Background(), db); err != nil {
		t.Fatalf("orchestratorRouteOpenClaw error: %v", err)
	}

	var lane string
	if err := db.QueryRow(`SELECT lane FROM tasks WHERE id = ?`, "task-alreadylaned").Scan(&lane); err != nil {
		t.Fatalf("query task: %v", err)
	}

	// Lane must remain 'implementation' — not overwritten.
	if lane != "implementation" {
		t.Errorf("expected lane='implementation' (unchanged), got %q", lane)
	}
}

func TestOrchestratorPatrolRouteOpenClawNoTasks(t *testing.T) {
	db, cleanup := setupOrchestratorTestDB(t)
	defer cleanup()

	if err := orchestratorRouteOpenClaw(context.Background(), db); err != nil {
		t.Fatalf("orchestratorRouteOpenClaw error on empty table: %v", err)
	}
}

// --- Tests for role resolution in dispatch ---

func TestOrchestratorPatrolRoleMatchingNoConfigFile(t *testing.T) {
	db, cleanup := setupOrchestratorTestDB(t)
	defer cleanup()

	// Insert a 'build'-stage-domain task (no YAML file loaded → "builder" default role)
	insertQueuedTask(t, db, "task-role-1", "test-domain", "feature")
	insertHeartbeat(t, db, "agent-role-test", "online", time.Now(), "")

	// Should not error even when agent-roles.yaml is absent (roles == nil path).
	if err := orchestratorDispatchQueued(context.Background(), db); err != nil {
		t.Fatalf("orchestratorDispatchQueued error: %v", err)
	}

	var status string
	if err := db.QueryRow(`SELECT status FROM tasks WHERE id = ?`, "task-role-1").Scan(&status); err != nil {
		t.Fatalf("query task: %v", err)
	}

	// Task should have been dispatched to the only available idle agent.
	if status != "assigned" {
		t.Errorf("expected status='assigned', got %q", status)
	}
}

// --- Integration: orchestratorAutoDispatch end-to-end ---

func TestOrchestratorPatrolAutoDispatchEndToEnd(t *testing.T) {
	db, cleanup := setupOrchestratorTestDB(t)
	defer cleanup()

	nodeID := "orchestrator-e2e-node"
	insertHeartbeat(t, db, nodeID, "offline", time.Now().Add(-10*time.Minute), "")

	// Stale task (assigned to an agent that has been offline 40 minutes)
	staleAgent := "stale-agent-e2e"
	insertHeartbeat(t, db, staleAgent, "offline", time.Now().Add(-40*time.Minute), "")
	insertAssignedTask(t, db, "task-stale-e2e", staleAgent, time.Now().Add(-40*time.Minute))

	// Queued task ready for dispatch
	insertQueuedTask(t, db, "task-queue-e2e", "test-domain", "feature")

	// Idle agent available
	insertHeartbeat(t, db, "idle-agent-e2e", "online", time.Now(), "")

	// Set NODE_ID so orchestratorHeartbeat targets the right row.
	t.Setenv("NODE_ID", nodeID)

	if err := orchestratorAutoDispatch(context.Background(), db); err != nil {
		t.Fatalf("orchestratorAutoDispatch error: %v", err)
	}

	// Verify: orchestrator node heartbeat updated to 'online'.
	var nodeStatus string
	if err := db.QueryRow(`SELECT status FROM agent_heartbeats WHERE agent_id = ?`, nodeID).Scan(&nodeStatus); err != nil {
		t.Fatalf("query node heartbeat: %v", err)
	}
	if nodeStatus != "online" {
		t.Errorf("expected orchestrator node status='online', got %q", nodeStatus)
	}

	// Verify: stale task was requeued.
	var staleStatus string
	if err := db.QueryRow(`SELECT status FROM tasks WHERE id = ?`, "task-stale-e2e").Scan(&staleStatus); err != nil {
		t.Fatalf("query stale task: %v", err)
	}
	if staleStatus != "queued" {
		t.Errorf("expected stale task status='queued', got %q", staleStatus)
	}

	// Verify: queued task was dispatched to the idle agent.
	var queuedStatus string
	if err := db.QueryRow(`SELECT status FROM tasks WHERE id = ?`, "task-queue-e2e").Scan(&queuedStatus); err != nil {
		t.Fatalf("query queued task: %v", err)
	}
	if queuedStatus != "assigned" {
		t.Errorf("expected queued task status='assigned', got %q", queuedStatus)
	}
}

// --- Test: standard patrol registration includes orchestrator-auto ---

func TestOrchestratorPatrolRegistered(t *testing.T) {
	patrols := StandardPatrols()
	for _, p := range patrols {
		if p.ID == "orchestrator-auto" {
			if p.Schedule != 60*time.Second {
				t.Errorf("expected 60s schedule, got %v", p.Schedule)
			}
			if p.Action == nil {
				t.Error("expected non-nil Action for orchestrator-auto patrol")
			}
			return
		}
	}
	t.Error("orchestrator-auto patrol not found in StandardPatrols()")
}

// ── orchestratorAutoDispatch ──────────────────────────────────────────────────

func TestOrchestratorAutoDispatch_NilDB(t *testing.T) {
	// With nil DB the function returns nil immediately.
	if err := orchestratorAutoDispatch(context.Background(), nil); err != nil {
		t.Errorf("expected nil error for nil db, got %v", err)
	}
}

func TestOrchestratorAutoDispatch_DisabledByEnv(t *testing.T) {
	t.Setenv("FORGE_AUTO_DISPATCH", "0")
	db, cleanup := setupOrchestratorTestDB(t)
	defer cleanup()

	// Should return nil immediately when disabled.
	if err := orchestratorAutoDispatch(context.Background(), db); err != nil {
		t.Errorf("expected nil when FORGE_AUTO_DISPATCH=0, got %v", err)
	}
}

func TestOrchestratorAutoDispatch_EmptyQueue(t *testing.T) {
	t.Setenv("FORGE_AUTO_DISPATCH", "1")
	db, cleanup := setupOrchestratorTestDB(t)
	defer cleanup()

	// Should complete without error when the task queue is empty.
	if err := orchestratorAutoDispatch(context.Background(), db); err != nil {
		t.Errorf("unexpected error with empty queue: %v", err)
	}
}

// insertOpenclawTask inserts an openclaw queued task with lane=NULL so that
// orchestratorRouteOpenClaw can route it.  The lane column has DEFAULT 'dev'
// so a plain insertQueuedTask would never be routed.
func insertOpenclawTask(t *testing.T, db *sql.DB, id, taskType string) {
	t.Helper()
	now := time.Now().UTC().Format(time.RFC3339)
	_, err := db.Exec(`
		INSERT INTO tasks (id, domain, project, type, title, priority, status, state, lane, created_at, updated_at)
		VALUES (?, 'openclaw', 'bridge', ?, ?, 5, 'queued', 'QUEUED', NULL, ?, ?)
	`, id, taskType, "Test openclaw "+id, now, now)
	if err != nil {
		t.Fatalf("insertOpenclawTask(%s): %v", id, err)
	}
}

// ── orchestratorRouteOpenClaw ─────────────────────────────────────────────────

func TestOrchestratorRouteOpenClaw_Empty(t *testing.T) {
	db, cleanup := setupOrchestratorTestDB(t)
	defer cleanup()

	// No openclaw tasks → returns nil.
	if err := orchestratorRouteOpenClaw(context.Background(), db); err != nil {
		t.Errorf("unexpected error with no tasks: %v", err)
	}
}

func TestOrchestratorRouteOpenClaw_AssignsLane(t *testing.T) {
	db, cleanup := setupOrchestratorTestDB(t)
	defer cleanup()

	// Insert a queued openclaw task with explicit NULL lane.
	insertOpenclawTask(t, db, "oc-task-001", "feature")

	if err := orchestratorRouteOpenClaw(context.Background(), db); err != nil {
		t.Fatalf("orchestratorRouteOpenClaw: %v", err)
	}

	// Lane should have been assigned.
	var lane sql.NullString
	if err := db.QueryRow(`SELECT lane FROM tasks WHERE id = 'oc-task-001'`).Scan(&lane); err != nil {
		t.Fatalf("scan lane: %v", err)
	}
	if !lane.Valid || lane.String == "" {
		t.Errorf("expected lane to be assigned, got %v", lane)
	}
}

func TestOrchestratorRouteOpenClaw_MultipleTypes(t *testing.T) {
	db, cleanup := setupOrchestratorTestDB(t)
	defer cleanup()

	// Insert openclaw tasks of different types with NULL lanes.
	for i, taskType := range []string{"feature", "bug", "research"} {
		insertOpenclawTask(t, db, fmt.Sprintf("oc-multi-%d", i), taskType)
	}

	if err := orchestratorRouteOpenClaw(context.Background(), db); err != nil {
		t.Fatalf("orchestratorRouteOpenClaw: %v", err)
	}

	// All tasks should have lanes assigned.
	rows, err := db.Query(`SELECT id, lane FROM tasks WHERE domain = 'openclaw'`)
	if err != nil {
		t.Fatalf("query tasks: %v", err)
	}
	defer rows.Close()

	count := 0
	for rows.Next() {
		var id string
		var lane sql.NullString
		if err := rows.Scan(&id, &lane); err != nil {
			t.Fatalf("scan: %v", err)
		}
		if !lane.Valid || lane.String == "" {
			t.Errorf("task %s has no lane after routing", id)
		}
		count++
	}
	if count != 3 {
		t.Errorf("expected 3 tasks, got %d", count)
	}
}

// ── resolveNodeID ─────────────────────────────────────────────────────────────

func TestResolveNodeID_EnvSet(t *testing.T) {
	t.Setenv("NODE_ID", "test-prya")
	id := resolveNodeID()
	if id != "test-prya" {
		t.Errorf("expected 'test-prya', got %q", id)
	}
}

func TestResolveNodeID_Hostname(t *testing.T) {
	t.Setenv("NODE_ID", "")
	id := resolveNodeID()
	if id == "" || id == "unknown" {
		t.Logf("resolveNodeID returned %q (acceptable if hostname call fails)", id)
	}
}

// ── orchestratorAutoDispatch — error aggregation path ─────────────────────────

// TestOrchestratorAutoDispatch_ClosedDB exercises the sub-error aggregation
// block inside orchestratorAutoDispatch. When the DB is closed every sub-call
// (heartbeat, dispatchQueued, requeueStale, routeOpenClaw) returns an error,
// so all four error-append branches AND the final aggregation block fire.
func TestOrchestratorAutoDispatch_ClosedDB(t *testing.T) {
	db, cleanup := setupOrchestratorTestDB(t)
	cleanup() // Close the DB immediately — sub-function calls will fail.

	t.Setenv("NODE_ID", "error-agg-node")
	t.Setenv("FORGE_AUTO_DISPATCH", "1") // Ensure auto-dispatch is enabled.

	err := orchestratorAutoDispatch(context.Background(), db)
	// With a closed DB all sub-functions return errors → orchestratorAutoDispatch
	// must return a combined error (not nil).
	if err == nil {
		t.Error("expected non-nil error when DB is closed, got nil")
	}
}

// TestOrchestratorDispatchQueued_MultipleTasksOneAgent ensures that when all
// queued tasks exceed the number of available idle agents, the second task's
// dispatch loop exhausts onlineIdle and hits the "assigned=="" continue" path.
func TestOrchestratorDispatchQueued_MultipleTasksOneAgent(t *testing.T) {
	db, cleanup := setupOrchestratorTestDB(t)
	defer cleanup()

	// Two queued tasks but only one idle agent.
	insertQueuedTask(t, db, "multi-task-1", "test-domain", "feature")
	insertQueuedTask(t, db, "multi-task-2", "test-domain", "bug")
	insertHeartbeat(t, db, "sole-agent", "online", time.Now(), "")

	if err := orchestratorDispatchQueued(context.Background(), db); err != nil {
		t.Fatalf("orchestratorDispatchQueued: %v", err)
	}

	// Exactly one task must be assigned; the other remains queued.
	var assigned, queued int
	db.QueryRow(`SELECT COUNT(*) FROM tasks WHERE status = 'assigned'`).Scan(&assigned)
	db.QueryRow(`SELECT COUNT(*) FROM tasks WHERE status = 'queued'`).Scan(&queued)

	if assigned != 1 {
		t.Errorf("expected 1 assigned task, got %d", assigned)
	}
	if queued != 1 {
		t.Errorf("expected 1 queued task remaining, got %d", queued)
	}
}

// TestOrchestratorRouteOpenClaw_ClosedDB covers the query-error return path
// inside orchestratorRouteOpenClaw when the DB is closed.
func TestOrchestratorRouteOpenClaw_ClosedDB(t *testing.T) {
	db, cleanup := setupOrchestratorTestDB(t)
	cleanup() // Close DB immediately.

	err := orchestratorRouteOpenClaw(context.Background(), db)
	if err == nil {
		t.Error("expected error when DB is closed, got nil")
	}
}

// TestOrchestratorDispatchQueued_ClosedDB covers the query-error return path
// inside orchestratorDispatchQueued.
func TestOrchestratorDispatchQueued_ClosedDB(t *testing.T) {
	db, cleanup := setupOrchestratorTestDB(t)
	cleanup() // Close DB immediately.

	err := orchestratorDispatchQueued(context.Background(), db)
	if err == nil {
		t.Error("expected error when DB is closed, got nil")
	}
}

// TestOrchestratorRequeueStale_ClosedDB covers the exec-error return path
// inside orchestratorRequeueStale.
func TestOrchestratorRequeueStale_ClosedDB(t *testing.T) {
	db, cleanup := setupOrchestratorTestDB(t)
	cleanup() // Close DB immediately.

	err := orchestratorRequeueStale(context.Background(), db)
	if err == nil {
		t.Error("expected error when DB is closed, got nil")
	}
}
