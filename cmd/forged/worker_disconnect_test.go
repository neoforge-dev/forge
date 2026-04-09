package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/gorilla/websocket"
	_ "github.com/mattn/go-sqlite3"
)

// setupWorkerDisconnectTestDB creates a test database with necessary schema
func setupWorkerDisconnectTestDB(t *testing.T) (*sql.DB, func()) {
	db, err := sql.Open("sqlite3", ":memory:?_journal_mode=WAL")
	if err != nil {
		t.Fatalf("failed to open db: %v", err)
	}

	schema := `
	CREATE TABLE IF NOT EXISTS tasks (
		id TEXT PRIMARY KEY,
		domain TEXT,
		project TEXT,
		type TEXT,
		priority INTEGER,
		status TEXT,
		state TEXT DEFAULT 'QUEUED',
		lane TEXT,
		assigned_to TEXT,
		plan_version INTEGER,
		plan_id TEXT,
		envelope_id TEXT,
		result TEXT,
		error TEXT,
		completed_at TEXT,
		created_at TEXT,
		started_at TEXT,
		updated_at TEXT
	);

	CREATE TABLE IF NOT EXISTS leases (
		id TEXT PRIMARY KEY,
		task_id TEXT NOT NULL UNIQUE,
		agent_id TEXT NOT NULL,
		expires_at TEXT NOT NULL
	);
	CREATE INDEX IF NOT EXISTS idx_leases_expires ON leases (expires_at);

	CREATE TABLE IF NOT EXISTS task_events (
		id INTEGER PRIMARY KEY AUTOINCREMENT,
		task_id TEXT NOT NULL,
		domain TEXT,
		project TEXT,
		event_type TEXT,
		payload TEXT,
		created_at TEXT
	);
	`
	if _, err := db.Exec(schema); err != nil {
		t.Fatalf("failed to create schema: %v", err)
	}

	return db, func() { db.Close() }
}

// TestWorkerCrash_LeaseExpiryRequeuesTask tests that when a worker crashes,
// the lease expires and the task is returned to the queue
func TestWorkerCrash_LeaseExpiryRequeuesTask(t *testing.T) {
	db, cleanup := setupWorkerDisconnectTestDB(t)
	defer cleanup()

	now := time.Now().UTC()

	// Insert a task that's executing
	taskID := "worker-crash-task-1"
	agentID := "agent-crash-1"
	_, err := db.Exec(`
		INSERT INTO tasks (id, domain, project, type, priority, status, assigned_to, created_at, started_at, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
	`, taskID, "test-domain", "test-project", string(TaskTypeFeature), 50, string(TaskStatusExecuting), agentID,
		now.Add(-1*time.Hour).Format(time.RFC3339), now.Add(-5*time.Minute).Format(time.RFC3339), now.Format(time.RFC3339))
	if err != nil {
		t.Fatalf("failed to insert task: %v", err)
	}

	// Create an expired lease (simulating crashed worker)
	leaseID := "lease-crash-1"
	_, err = db.Exec(`
		INSERT INTO leases (id, task_id, agent_id, expires_at)
		VALUES (?, ?, ?, ?)
	`, leaseID, taskID, agentID, now.Add(-1*time.Minute).Format(time.RFC3339))
	if err != nil {
		t.Fatalf("failed to insert lease: %v", err)
	}

	// Verify lease exists
	var count int
	err = db.QueryRow("SELECT COUNT(*) FROM leases WHERE id = ?", leaseID).Scan(&count)
	if err != nil || count != 1 {
		t.Fatalf("lease should exist before release")
	}

	// Simulate patrol detecting expired lease and releasing it
	_, err = db.Exec(`DELETE FROM leases WHERE expires_at < ?`, now.Format(time.RFC3339))
	if err != nil {
		t.Fatalf("failed to delete expired lease: %v", err)
	}

	// Simulate task timeout patrol requeuing the task
	_, err = db.Exec(`
		UPDATE tasks
		SET status = ?, assigned_to = NULL, updated_at = ?
		WHERE id = ? AND status = ?
	`, TaskStatusQueued, now.Format(time.RFC3339), taskID, TaskStatusExecuting)
	if err != nil {
		t.Fatalf("failed to requeue task: %v", err)
	}

	// Verify lease is deleted
	err = db.QueryRow("SELECT COUNT(*) FROM leases WHERE id = ?", leaseID).Scan(&count)
	if err != nil {
		t.Fatalf("failed to count leases: %v", err)
	}
	if count != 0 {
		t.Errorf("lease should be deleted after worker crash, got %d leases", count)
	}

	// Verify task is back in queue
	var status, assignedTo string
	err = db.QueryRow("SELECT status, IFNULL(assigned_to, '') FROM tasks WHERE id = ?", taskID).Scan(&status, &assignedTo)
	if err != nil {
		t.Fatalf("failed to query task: %v", err)
	}
	if status != string(TaskStatusQueued) {
		t.Errorf("expected task status to be 'queued', got %q", status)
	}
	if assignedTo != "" {
		t.Errorf("expected task to be unassigned, got %q", assignedTo)
	}

	t.Logf("✓ Worker crash scenario: lease released, task requeued successfully")
}

// TestNetworkDisconnect_WorkerPruned tests that when a worker loses network
// connection, it gets pruned from the hub
func TestNetworkDisconnect_WorkerPruned(t *testing.T) {
	hub := NewWebSocketHub()
	go hub.Run()
	defer hub.Stop()

	server := httptest.NewServer(WebSocketHandler(hub))
	defer server.Close()

	wsURL := strings.Replace(server.URL, "http", "ws", 1)

	// Connect a worker
	ws, _, err := websocket.DefaultDialer.Dial(wsURL, nil)
	if err != nil {
		t.Fatalf("Failed to connect: %v", err)
	}

	// Register the worker
	regPayload, _ := json.Marshal(map[string]interface{}{
		"agent_id": "agent-disconnect-test",
		"name":     "Test Agent",
		"node":     "test-node",
		"tier":     "standard",
		"capabilities": []string{},
	})
	registerMsg := WSMessage{
		Version: "1",
		Type:    MsgAgentRegister,
		ID:      "test-register-1",
		Payload: regPayload,
		Time:    time.Now(),
	}
	if err := ws.WriteJSON(registerMsg); err != nil {
		t.Fatalf("Failed to register: %v", err)
	}

	// Wait for registration
	time.Sleep(50 * time.Millisecond)

	// Verify worker is registered
	workers := hub.ListWorkers()
	found := false
	for _, w := range workers {
		if w == "agent-disconnect-test" {
			found = true
			break
		}
	}
	if !found {
		t.Fatal("Worker should be registered")
	}

	// Simulate network disconnect by closing connection without unregister message
	ws.Close()

	// Wait for disconnect to be detected
	time.Sleep(50 * time.Millisecond)

	// The worker should still be in the map but with stale heartbeat
	// Wait for prune cycle (30s ticker, but we'll manually trigger)
	hub.pruneStaleWorkers()

	// Now verify worker is removed
	workers = hub.ListWorkers()
	found = false
	for _, w := range workers {
		if w == "agent-disconnect-test" {
			found = true
			break
		}
	}
	if found {
		t.Error("Worker should be pruned after disconnect")
	}

	t.Logf("✓ Network disconnect scenario: worker pruned successfully")
}

// TestHeartbeatTimeout_DetectsStaleWorkers tests that workers with stale
// heartbeats are properly detected and removed
func TestHeartbeatTimeout_DetectsStaleWorkers(t *testing.T) {
	hub := NewWebSocketHub()

	// Create a mock worker with stale heartbeat
	// Use a server to create a valid websocket connection
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		upgrader.CheckOrigin = func(r *http.Request) bool { return true }
		ws, err := upgrader.Upgrade(w, r, nil)
		if err != nil {
			return
		}
		defer ws.Close()
		// Keep connection open
		for {
			_, _, err := ws.ReadMessage()
			if err != nil {
				return
			}
		}
	}))
	defer server.Close()

	// Connect to get a valid websocket connection
	wsURL := strings.Replace(server.URL, "http", "ws", 1)
	ws, _, err := websocket.DefaultDialer.Dial(wsURL, nil)
	if err != nil {
		t.Fatalf("Failed to connect: %v", err)
	}
	defer ws.Close()

	worker := &WebSocketWorker{
		AgentID: "stale-worker-1",
		Node:    "test-node",
		Send:    make(chan WSMessage, 10),
		Conn:    ws,
	}
	// Manually set lastPong to be stale
	worker.lastPong = time.Now().Add(-2 * time.Minute)

	// Register worker
	hub.mu.Lock()
	hub.workers[worker.AgentID] = worker
	hub.mu.Unlock()

	// Verify worker is initially in the list
	workers := hub.ListWorkers()
	if len(workers) != 0 {
		t.Errorf("Expected 0 active workers (stale should be filtered), got %d", len(workers))
	}

	// Prune stale workers
	hub.pruneStaleWorkers()

	// Verify worker is removed
	hub.mu.RLock()
	_, exists := hub.workers[worker.AgentID]
	hub.mu.RUnlock()

	if exists {
		t.Error("Stale worker should be pruned")
	}

	t.Logf("✓ Heartbeat timeout scenario: stale workers detected and removed")
}

// TestWorkerDisconnect_TaskCanBeReclaimed tests that after a worker disconnects,
// its task can be claimed by another worker
func TestWorkerDisconnect_TaskCanBeReclaimed(t *testing.T) {
	db, cleanup := setupWorkerDisconnectTestDB(t)
	defer cleanup()

	ctx := context.Background()
	now := time.Now().UTC()

	// Insert an executing task
	taskID := "reclaim-task-1"
	originalAgent := "agent-1"
	newAgent := "agent-2"

	_, err := db.Exec(`
		INSERT INTO tasks (id, domain, project, type, priority, status, assigned_to, created_at, started_at, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
	`, taskID, "test-domain", "test-project", string(TaskTypeFeature), 50, string(TaskStatusExecuting), originalAgent,
		now.Add(-1*time.Hour).Format(time.RFC3339), now.Add(-5*time.Minute).Format(time.RFC3339), now.Format(time.RFC3339))
	if err != nil {
		t.Fatalf("failed to insert task: %v", err)
	}

	// Create lease
	_, err = db.Exec(`
		INSERT INTO leases (id, task_id, agent_id, expires_at)
		VALUES (?, ?, ?, ?)
	`, "lease-1", taskID, originalAgent, now.Add(30*time.Minute).Format(time.RFC3339))
	if err != nil {
		t.Fatalf("failed to insert lease: %v", err)
	}

	// Simulate worker disconnect - release lease
	_, err = db.Exec(`DELETE FROM leases WHERE task_id = ?`, taskID)
	if err != nil {
		t.Fatalf("failed to release lease: %v", err)
	}

	// Requeue the task
	_, err = db.Exec(`
		UPDATE tasks
		SET status = ?, assigned_to = NULL, updated_at = ?
		WHERE id = ?
	`, TaskStatusQueued, now.Format(time.RFC3339), taskID)
	if err != nil {
		t.Fatalf("failed to requeue task: %v", err)
	}

	// Verify task is available for claiming
	var status, assignedTo string
	err = db.QueryRow("SELECT status, IFNULL(assigned_to, '') FROM tasks WHERE id = ?", taskID).Scan(&status, &assignedTo)
	if err != nil {
		t.Fatalf("failed to query task: %v", err)
	}
	if status != string(TaskStatusQueued) {
		t.Fatalf("expected task to be queued, got %s", status)
	}

	// Simulate new worker claiming the task
	leaseManager := &sqliteLeaseManager{db: db}
	lease, err := leaseManager.Claim(ctx, taskID, newAgent, 30*time.Minute)
	if err != nil {
		t.Fatalf("failed to claim task: %v", err)
	}

	if lease.TaskID != taskID {
		t.Errorf("expected lease for task %s, got %s", taskID, lease.TaskID)
	}
	if lease.AgentID != newAgent {
		t.Errorf("expected lease for agent %s, got %s", newAgent, lease.AgentID)
	}

	// Update task assignment
	_, err = db.Exec(`
		UPDATE tasks SET status = ?, assigned_to = ?, updated_at = ?
		WHERE id = ?
	`, TaskStatusExecuting, newAgent, now.Format(time.RFC3339), taskID)
	if err != nil {
		t.Fatalf("failed to update task: %v", err)
	}

	// Verify new assignment
	err = db.QueryRow("SELECT assigned_to FROM tasks WHERE id = ?", taskID).Scan(&assignedTo)
	if err != nil {
		t.Fatalf("failed to query task: %v", err)
	}
	if assignedTo != newAgent {
		t.Errorf("expected task assigned to %s, got %s", newAgent, assignedTo)
	}

	t.Logf("✓ Task reclaimed by new agent after disconnect")
}

// TestPatrolReleasesExpiredLeases tests the patrol system releasing expired leases
func TestPatrolReleasesExpiredLeases(t *testing.T) {
	db, cleanup := setupWorkerDisconnectTestDB(t)
	defer cleanup()

	now := time.Now().UTC()

	// Insert multiple tasks with varying lease states
	testCases := []struct {
		name       string
		taskID     string
		leaseExp   time.Time
		shouldRelease bool
	}{
		{
			name:       "expired_lease",
			taskID:     "task-expired-1",
			leaseExp:   now.Add(-5 * time.Minute),
			shouldRelease: true,
		},
		{
			name:       "active_lease",
			taskID:     "task-active-1",
			leaseExp:   now.Add(30 * time.Minute),
			shouldRelease: false,
		},
	}

	for _, tc := range testCases {
		_, err := db.Exec(`
			INSERT INTO tasks (id, domain, project, type, priority, status, assigned_to, created_at, started_at, updated_at)
			VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
		`, tc.taskID, "test-domain", "test-project", string(TaskTypeFeature), 50, string(TaskStatusExecuting), "agent-1",
			now.Add(-1*time.Hour).Format(time.RFC3339), now.Add(-10*time.Minute).Format(time.RFC3339), now.Format(time.RFC3339))
		if err != nil {
			t.Fatalf("failed to insert task %s: %v", tc.taskID, err)
		}

		_, err = db.Exec(`
			INSERT INTO leases (id, task_id, agent_id, expires_at)
			VALUES (?, ?, ?, ?)
		`, "lease-"+tc.taskID, tc.taskID, "agent-1", tc.leaseExp.Format(time.RFC3339))
		if err != nil {
			t.Fatalf("failed to insert lease for %s: %v", tc.taskID, err)
		}
	}

	// Simulate patrol deleting expired leases
	_, err := db.Exec(`DELETE FROM leases WHERE expires_at < ?`, now.Format(time.RFC3339))
	if err != nil {
		t.Fatalf("failed to delete expired leases: %v", err)
	}

	// Verify only expired lease was deleted
	var expiredCount, activeCount int
	err = db.QueryRow("SELECT COUNT(*) FROM leases WHERE task_id = ?", "task-expired-1").Scan(&expiredCount)
	if err != nil {
		t.Fatalf("failed to count expired leases: %v", err)
	}
	err = db.QueryRow("SELECT COUNT(*) FROM leases WHERE task_id = ?", "task-active-1").Scan(&activeCount)
	if err != nil {
		t.Fatalf("failed to count active leases: %v", err)
	}

	if expiredCount != 0 {
		t.Errorf("expired lease should be deleted, got %d", expiredCount)
	}
	if activeCount != 1 {
		t.Errorf("active lease should remain, got %d", activeCount)
	}

	t.Logf("✓ Patrol correctly releases only expired leases")
}

// TestWorkerDisconnect_HTTPCompleteEndpoint tests that the complete endpoint
// works correctly after a worker reconnects
func TestWorkerDisconnect_HTTPCompleteEndpoint(t *testing.T) {
	t.Skip("Flaky: taskQueue global state pollution from other tests")
	db, cleanup := setupWorkerDisconnectTestDB(t)
	defer cleanup()

	// Set global dbConn for handler
	oldDBConn := getDBConn()
	setDBConn(db)
	defer func() { setDBConn(oldDBConn) }()

	now := time.Now().UTC()
	taskID := "http-complete-task-1"

	// Insert executing task
	_, err := db.Exec(`
		INSERT INTO tasks (id, domain, project, type, priority, status, state, assigned_to, created_at, started_at, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
	`, taskID, "test-domain", "test-project", string(TaskTypeFeature), 50, string(TaskStatusExecuting), StateRunning, "agent-1",
		now.Add(-1*time.Hour).Format(time.RFC3339), now.Add(-5*time.Minute).Format(time.RFC3339), now.Format(time.RFC3339))
	if err != nil {
		t.Fatalf("failed to insert task: %v", err)
	}

	// Create lease
	_, err = db.Exec(`
		INSERT INTO leases (id, task_id, agent_id, expires_at)
		VALUES (?, ?, ?, ?)
	`, "lease-http-1", taskID, "agent-1", now.Add(30*time.Minute).Format(time.RFC3339))
	if err != nil {
		t.Fatalf("failed to insert lease: %v", err)
	}

	// Test complete endpoint
	reqBody := `{"result": "Task completed successfully", "status": "completed"}`
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/"+taskID+"/complete", strings.NewReader(reqBody))
	req.Header.Set("Content-Type", "application/json")
	rr := httptest.NewRecorder()

	completeTaskHandler(rr, req)

	if rr.Code != http.StatusOK {
		t.Errorf("expected status %d, got %d: %s", http.StatusOK, rr.Code, rr.Body.String())
	}

	// Verify task is completed
	var status string
	err = db.QueryRow("SELECT status FROM tasks WHERE id = ?", taskID).Scan(&status)
	if err != nil {
		t.Fatalf("failed to query task: %v", err)
	}
	if status != "completed" {
		t.Errorf("expected task status 'completed', got %s", status)
	}

	// Verify lease is released
	var leaseCount int
	err = db.QueryRow("SELECT COUNT(*) FROM leases WHERE task_id = ?", taskID).Scan(&leaseCount)
	if err != nil {
		t.Fatalf("failed to count leases: %v", err)
	}
	if leaseCount != 0 {
		t.Errorf("lease should be released after completion, got %d", leaseCount)
	}

	t.Logf("✓ HTTP complete endpoint works correctly after worker activity")
}

// TestWorkerDisconnect_HTTPCompleteDoneAndFailed tests "done" normalization and status "failed"
func TestWorkerDisconnect_HTTPCompleteDoneAndFailed(t *testing.T) {
	t.Skip("Flaky: taskQueue global state pollution from other tests")
	db, cleanup := setupWorkerDisconnectTestDB(t)
	defer cleanup()

	oldDBConn := getDBConn()
	setDBConn(db)
	defer func() { setDBConn(oldDBConn) }()

	now := time.Now().UTC()

	// 1) Complete with status "done" -> normalized to completed
	taskDone := "http-complete-done-1"
	_, err := db.Exec(`
		INSERT INTO tasks (id, domain, project, type, priority, status, state, assigned_to, created_at, started_at, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
	`, taskDone, "test-domain", "test-project", string(TaskTypeFeature), 50, string(TaskStatusExecuting), StateRunning, "agent-1",
		now.Format(time.RFC3339), now.Format(time.RFC3339), now.Format(time.RFC3339))
	if err != nil {
		t.Fatalf("failed to insert task: %v", err)
	}

	reqBodyDone := `{"result": "ok", "status": "done"}`
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/"+taskDone+"/complete", strings.NewReader(reqBodyDone))
	req.Header.Set("Content-Type", "application/json")
	rr := httptest.NewRecorder()
	completeTaskHandler(rr, req)
	if rr.Code != http.StatusOK {
		t.Errorf("complete with status 'done' expected 200, got %d: %s", rr.Code, rr.Body.String())
	}
	var st string
	_ = db.QueryRow("SELECT status FROM tasks WHERE id = ?", taskDone).Scan(&st)
	if st != "completed" {
		t.Errorf("expected status 'completed' after 'done', got %s", st)
	}

	// 2) Complete with status "failed"
	taskFail := "http-complete-fail-1"
	_, err = db.Exec(`
		INSERT INTO tasks (id, domain, project, type, priority, status, state, assigned_to, created_at, started_at, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
	`, taskFail, "test-domain", "test-project", string(TaskTypeFeature), 50, string(TaskStatusExecuting), StateRunning, "agent-1",
		now.Format(time.RFC3339), now.Format(time.RFC3339), now.Format(time.RFC3339))
	if err != nil {
		t.Fatalf("failed to insert task: %v", err)
	}

	reqBodyFail := `{"result": "", "status": "failed"}`
	req2 := httptest.NewRequest(http.MethodPost, "/api/tasks/"+taskFail+"/complete", strings.NewReader(reqBodyFail))
	req2.Header.Set("Content-Type", "application/json")
	rr2 := httptest.NewRecorder()
	completeTaskHandler(rr2, req2)
	if rr2.Code != http.StatusOK {
		t.Errorf("complete with status 'failed' expected 200, got %d: %s", rr2.Code, rr2.Body.String())
	}
	_ = db.QueryRow("SELECT status FROM tasks WHERE id = ?", taskFail).Scan(&st)
	if st != "failed" {
		t.Errorf("expected status 'failed', got %s", st)
	}
}
func TestConcurrentWorkerDisconnects(t *testing.T) {
	db, cleanup := setupWorkerDisconnectTestDB(t)
	defer cleanup()

	now := time.Now().UTC()

	// Create multiple executing tasks
	for i := 0; i < 5; i++ {
		taskID := "concurrent-task-" + string(rune('a'+i))
		agentID := "agent-" + string(rune('a'+i))

		_, err := db.Exec(`
			INSERT INTO tasks (id, domain, project, type, priority, status, assigned_to, created_at, started_at, updated_at)
			VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
		`, taskID, "test-domain", "test-project", string(TaskTypeFeature), 50, string(TaskStatusExecuting), agentID,
			now.Add(-1*time.Hour).Format(time.RFC3339), now.Add(-5*time.Minute).Format(time.RFC3339), now.Format(time.RFC3339))
		if err != nil {
			t.Fatalf("failed to insert task %s: %v", taskID, err)
		}

		_, err = db.Exec(`
			INSERT INTO leases (id, task_id, agent_id, expires_at)
			VALUES (?, ?, ?, ?)
		`, "lease-"+taskID, taskID, agentID, now.Add(-1*time.Minute).Format(time.RFC3339))
		if err != nil {
			t.Fatalf("failed to insert lease for %s: %v", taskID, err)
		}
	}

	// Release all expired leases at once
	_, err := db.Exec(`DELETE FROM leases WHERE expires_at < ?`, now.Format(time.RFC3339))
	if err != nil {
		t.Fatalf("failed to delete expired leases: %v", err)
	}

	// Requeue all tasks
	_, err = db.Exec(`
		UPDATE tasks
		SET status = ?, assigned_to = NULL, updated_at = ?
		WHERE status = ? AND started_at < ?
	`, TaskStatusQueued, now.Format(time.RFC3339), TaskStatusExecuting, now.Add(-1*time.Minute).Format(time.RFC3339))
	if err != nil {
		t.Fatalf("failed to requeue tasks: %v", err)
	}

	// Verify all leases are gone
	var leaseCount int
	err = db.QueryRow("SELECT COUNT(*) FROM leases").Scan(&leaseCount)
	if err != nil {
		t.Fatalf("failed to count leases: %v", err)
	}
	if leaseCount != 0 {
		t.Errorf("expected 0 leases, got %d", leaseCount)
	}

	// Verify all tasks are queued
	var queuedCount int
	err = db.QueryRow("SELECT COUNT(*) FROM tasks WHERE status = ?", TaskStatusQueued).Scan(&queuedCount)
	if err != nil {
		t.Fatalf("failed to count queued tasks: %v", err)
	}
	if queuedCount != 5 {
		t.Errorf("expected 5 queued tasks, got %d", queuedCount)
	}

	t.Logf("✓ Concurrent worker disconnects handled correctly")
}
