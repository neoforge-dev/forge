package main

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"testing"
	"time"

	_ "github.com/mattn/go-sqlite3"
)

func setupLeaseTestDB(t *testing.T) *sql.DB {
	db, err := sql.Open("sqlite3", ":memory:")
	if err != nil {
		t.Fatalf("failed to open db: %v", err)
	}

	schema := `
	CREATE TABLE IF NOT EXISTS leases (
		id TEXT PRIMARY KEY,
		task_id TEXT NOT NULL UNIQUE,
		agent_id TEXT NOT NULL,
		expires_at TEXT NOT NULL
	);
	CREATE INDEX IF NOT EXISTS idx_leases_expires ON leases (expires_at);

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
		created_at TEXT,
		updated_at TEXT,
		origin TEXT,
		requester TEXT,
		source_channel TEXT,
		failure_context TEXT,
		dispatch_attempts INTEGER NOT NULL DEFAULT 0,
		last_dispatch_at TEXT
	);

	CREATE TABLE IF NOT EXISTS task_state_transitions (
		id TEXT PRIMARY KEY,
		task_id TEXT NOT NULL,
		from_state TEXT,
		to_state TEXT,
		reason TEXT,
		transitioned_at TEXT
	);
	`
	if _, err := db.Exec(schema); err != nil {
		t.Fatalf("failed to create schema: %v", err)
	}

	return db
}

func TestClaim(t *testing.T) {
	db := setupLeaseTestDB(t)
	defer db.Close()

	m := &sqliteLeaseManager{db: db}
	ctx := context.Background()

	lease, err := m.Claim(ctx, "task-1", "agent-1", 30*time.Minute)
	if err != nil {
		t.Fatalf("Claim failed: %v", err)
	}
	if lease.TaskID != "task-1" {
		t.Errorf("expected task-1, got %s", lease.TaskID)
	}
	if lease.AgentID != "agent-1" {
		t.Errorf("expected agent-1, got %s", lease.AgentID)
	}
	if lease.ID == "" {
		t.Error("lease ID should not be empty")
	}
}

func TestClaimConflict(t *testing.T) {
	db := setupLeaseTestDB(t)
	defer db.Close()

	m := &sqliteLeaseManager{db: db}
	ctx := context.Background()

	_, err := m.Claim(ctx, "task-1", "agent-1", 30*time.Minute)
	if err != nil {
		t.Fatalf("first Claim failed: %v", err)
	}

	_, err = m.Claim(ctx, "task-1", "agent-2", 30*time.Minute)
	if err != ErrLeaseConflict {
		t.Errorf("expected ErrLeaseConflict, got %v", err)
	}
}

func TestRenew(t *testing.T) {
	db := setupLeaseTestDB(t)
	defer db.Close()

	m := &sqliteLeaseManager{db: db}
	ctx := context.Background()

	lease, err := m.Claim(ctx, "task-1", "agent-1", 30*time.Minute)
	if err != nil {
		t.Fatalf("Claim failed: %v", err)
	}

	newTTL := 60 * time.Minute
	err = m.Renew(ctx, lease.ID, newTTL)
	if err != nil {
		t.Fatalf("Renew failed: %v", err)
	}

	var expiresAt string
	err = db.QueryRow("SELECT expires_at FROM leases WHERE id = ?", lease.ID).Scan(&expiresAt)
	if err != nil {
		t.Fatalf("failed to get expires_at: %v", err)
	}

	parsed, _ := time.Parse(time.RFC3339, expiresAt)
	if parsed.Sub(time.Now()) < 45*time.Minute {
		t.Error("lease should be renewed for ~60 minutes")
	}
}

func TestRelease(t *testing.T) {
	db := setupLeaseTestDB(t)
	defer db.Close()

	m := &sqliteLeaseManager{db: db}
	ctx := context.Background()

	lease, err := m.Claim(ctx, "task-1", "agent-1", 30*time.Minute)
	if err != nil {
		t.Fatalf("Claim failed: %v", err)
	}

	err = m.Release(ctx, lease.ID)
	if err != nil {
		t.Fatalf("Release failed: %v", err)
	}

	var count int
	err = db.QueryRow("SELECT COUNT(*) FROM leases WHERE id = ?", lease.ID).Scan(&count)
	if err != nil {
		t.Fatalf("failed to query: %v", err)
	}
	if count != 0 {
		t.Error("lease should be released")
	}
}

func TestRecover(t *testing.T) {
	db := setupLeaseTestDB(t)
	defer db.Close()

	m := &sqliteLeaseManager{db: db}
	ctx := context.Background()

	_, err := m.Claim(ctx, "task-1", "agent-1", -1*time.Hour)
	if err != nil {
		t.Fatalf("Claim failed: %v", err)
	}

	recovered, err := m.Recover(ctx)
	if err != nil {
		t.Fatalf("Recover failed: %v", err)
	}
	if len(recovered) == 0 {
		t.Fatal("expected 1 recovered lease, got 0")
	}
	if recovered[0].TaskID != "task-1" {
		t.Errorf("expected task-1, got %s", recovered[0].TaskID)
	}

	var count int
	err = db.QueryRow("SELECT COUNT(*) FROM leases").Scan(&count)
	if err != nil {
		t.Fatalf("failed to query: %v", err)
	}
	if count != 0 {
		t.Error("stale leases should be deleted after recover")
	}
}

func TestClaimAfterRelease(t *testing.T) {
	db := setupLeaseTestDB(t)
	defer db.Close()

	m := &sqliteLeaseManager{db: db}
	ctx := context.Background()

	lease, err := m.Claim(ctx, "task-1", "agent-1", 30*time.Minute)
	if err != nil {
		t.Fatalf("Claim failed: %v", err)
	}

	err = m.Release(ctx, lease.ID)
	if err != nil {
		t.Fatalf("Release failed: %v", err)
	}

	lease2, err := m.Claim(ctx, "task-1", "agent-2", 30*time.Minute)
	if err != nil {
		t.Fatalf("Claim after release failed: %v", err)
	}
	if lease2.AgentID != "agent-2" {
		t.Errorf("expected agent-2, got %s", lease2.AgentID)
	}
}

func TestRenewNonExistent(t *testing.T) {
	db := setupLeaseTestDB(t)
	defer db.Close()

	m := &sqliteLeaseManager{db: db}
	ctx := context.Background()

	err := m.Renew(ctx, "non-existent", 30*time.Minute)
	if err != ErrLeaseNotFound {
		t.Errorf("expected ErrLeaseNotFound, got %v", err)
	}
}

func TestReleaseNonExistent(t *testing.T) {
	db := setupLeaseTestDB(t)
	defer db.Close()

	m := &sqliteLeaseManager{db: db}
	ctx := context.Background()

	err := m.Release(ctx, "non-existent")
	if err != ErrLeaseNotFound {
		t.Errorf("expected ErrLeaseNotFound, got %v", err)
	}
}

// TestLeaseStateMachine tests the integration with task state machine
func TestLeaseStateMachine(t *testing.T) {
	db := setupLeaseTestDB(t)
	defer db.Close()

	// Insert a test task
	_, err := db.Exec(`INSERT INTO tasks (id, domain, project, type, priority, status, state, created_at, updated_at) 
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		"task-1", "test-domain", "test-project", "feature", 1, "queued", StateQueued,
		time.Now().Format(time.RFC3339), time.Now().Format(time.RFC3339))
	if err != nil {
		t.Fatalf("failed to insert test task: %v", err)
	}

	// Create lease manager with state machine
	sm := NewTaskStateMachine(db)
	m := &sqliteLeaseManager{db: db, stateMachine: sm}
	ctx := context.Background()

	t.Run("AcquireLease transitions to DISPATCHED", func(t *testing.T) {
		lease, err := m.Claim(ctx, "task-1", "agent-1", 30*time.Minute)
		if err != nil {
			t.Fatalf("Claim failed: %v", err)
		}

		// Verify state transition
		state, err := sm.GetTaskState("task-1")
		if err != nil {
			t.Fatalf("Failed to get task state: %v", err)
		}
		if state != StateDispatched {
			t.Errorf("expected state %s, got %s", StateDispatched, state)
		}

		// Clean up
		m.Release(ctx, lease.ID)
	})

	t.Run("Heartbeat transitions DISPATCHED to RUNNING", func(t *testing.T) {
		// Reset task state
		db.Exec("UPDATE tasks SET state = ? WHERE id = ?", StateQueued, "task-1")

		lease, err := m.Claim(ctx, "task-1", "agent-1", 30*time.Minute)
		if err != nil {
			t.Fatalf("Claim failed: %v", err)
		}

		// Record heartbeat
		err = m.RecordHeartbeat(ctx, lease.ID)
		if err != nil {
			t.Fatalf("RecordHeartbeat failed: %v", err)
		}

		// Verify state transition to RUNNING
		state, err := sm.GetTaskState("task-1")
		if err != nil {
			t.Fatalf("Failed to get task state: %v", err)
		}
		if state != StateRunning {
			t.Errorf("expected state %s, got %s", StateRunning, state)
		}

		// Clean up
		m.Release(ctx, lease.ID)
	})

	t.Run("Release transitions to COMPLETED", func(t *testing.T) {
		// Reset task state
		db.Exec("UPDATE tasks SET state = ? WHERE id = ?", StateQueued, "task-1")

		lease, err := m.Claim(ctx, "task-1", "agent-1", 30*time.Minute)
		if err != nil {
			t.Fatalf("Claim failed: %v", err)
		}

		// Release lease
		err = m.Release(ctx, lease.ID)
		if err != nil {
			t.Fatalf("Release failed: %v", err)
		}

		// Verify state transition to COMPLETED
		state, err := sm.GetTaskState("task-1")
		if err != nil {
			t.Fatalf("Failed to get task state: %v", err)
		}
		if state != StateCompleted {
			t.Errorf("expected state %s, got %s", StateCompleted, state)
		}
	})

	t.Run("Expired lease transitions to FAILED", func(t *testing.T) {
		// Reset task state
		db.Exec("UPDATE tasks SET state = ? WHERE id = ?", StateQueued, "task-1")

		// Create expired lease
		_, err := m.Claim(ctx, "task-1", "agent-1", -1*time.Hour)
		if err != nil {
			t.Fatalf("Claim failed: %v", err)
		}

		// Recover (this deletes expired leases and transitions tasks)
		_, err = m.Recover(ctx)
		if err != nil {
			t.Fatalf("Recover failed: %v", err)
		}

		// Verify state transition to FAILED
		state, err := sm.GetTaskState("task-1")
		if err != nil {
			t.Fatalf("Failed to get task state: %v", err)
		}
		if state != StateFailed {
			t.Errorf("expected state %s, got %s", StateFailed, state)
		}
	})
}

func TestGetTaskForLease(t *testing.T) {
	db := setupLeaseTestDB(t)
	defer db.Close()

	// Insert a test task
	_, err := db.Exec(`INSERT INTO tasks (id, domain, project, type, priority, status, state, created_at, updated_at) 
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		"task-1", "test-domain", "test-project", "feature", 1, "queued", StateQueued,
		time.Now().Format(time.RFC3339), time.Now().Format(time.RFC3339))
	if err != nil {
		t.Fatalf("failed to insert test task: %v", err)
	}

	m := &sqliteLeaseManager{db: db}
	ctx := context.Background()

	lease, err := m.Claim(ctx, "task-1", "agent-1", 30*time.Minute)
	if err != nil {
		t.Fatalf("Claim failed: %v", err)
	}

	task, err := m.GetTaskForLease(ctx, lease.ID)
	if err != nil {
		t.Fatalf("GetTaskForLease failed: %v", err)
	}

	if task.ID != "task-1" {
		t.Errorf("expected task-1, got %s", task.ID)
	}
	if task.Domain != "test-domain" {
		t.Errorf("expected test-domain, got %s", task.Domain)
	}
}

func TestMain(m *testing.M) {
	// Run tests
	code := m.Run()

	// Cleanup goroutines started during tests
	StopXNode()
	StopMetrics()

	os.Exit(code)
}

// ---------------------------------------------------------------------------
// Consolidated from coverage_wave259_test.go: lease handler & LeaseManager tests
// ---------------------------------------------------------------------------

func TestWave259_ReleaseTaskHandler_TaskNotAssigned(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	task := enqueueTestTask(t, "wave259-release-mismatch")
	if err := taskQueue.UpdateTaskStatus(task.ID, "assigned", "agent-a"); err != nil {
		t.Fatalf("UpdateTaskStatus: %v", err)
	}

	body, _ := json.Marshal(map[string]string{
		"agent_id": "agent-b",
		"reason":   "mismatched agent trying to release",
	})
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/"+task.ID+"/release", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()

	releaseTaskHandler(w, req)

	if w.Code != http.StatusForbidden {
		t.Errorf("expected 403 when releasing from wrong agent, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave259_ExtendLeaseHandler_Success(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	task := enqueueTestTask(t, "wave259-extend-success")
	if err := taskQueue.UpdateTaskStatus(task.ID, "assigned", "agent-lease"); err != nil {
		t.Fatalf("UpdateTaskStatus: %v", err)
	}

	body := `{"agent_id":"agent-lease"}`
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/"+task.ID+"/extend-lease", bytes.NewReader([]byte(body)))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()

	extendLeaseHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200 from extendLeaseHandler, got %d: %s", w.Code, w.Body.String())
	}
	var resp map[string]string
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatalf("invalid JSON response: %v", err)
	}
	if resp["status"] != "lease_extended" {
		t.Errorf("expected status=lease_extended, got %q", resp["status"])
	}
	if resp["task_id"] != task.ID {
		t.Errorf("expected task_id=%s, got %q", task.ID, resp["task_id"])
	}
}

// mockStateMachine implements TaskStateMachine for lease tests.
type mockStateMachine struct {
	states      map[string]TaskState
	transitions []struct {
		taskID string
		from   TaskState
		to     TaskState
		reason string
	}
}

func newMockStateMachine(initial map[string]TaskState) *mockStateMachine {
	states := make(map[string]TaskState, len(initial))
	for id, st := range initial {
		states[id] = st
	}
	return &mockStateMachine{states: states}
}

func (m *mockStateMachine) Transition(taskID string, from, to TaskState, reason string) error {
	if m.states == nil {
		m.states = map[string]TaskState{}
	}
	m.transitions = append(m.transitions, struct {
		taskID string
		from   TaskState
		to     TaskState
		reason string
	}{taskID, from, to, reason})
	m.states[taskID] = to
	return nil
}

func (m *mockStateMachine) GetTaskState(taskID string) (TaskState, error) {
	if st, ok := m.states[taskID]; ok {
		return st, nil
	}
	return "", nil
}

func TestWave259_LeaseManager_RecordHeartbeat_DispatchedToRunning(t *testing.T) {
	db := setupLeaseTestDB(t)
	defer db.Close()

	taskID := "wave259-hb-task"
	now := time.Now().UTC().Format(time.RFC3339)
	if _, err := db.Exec(`INSERT INTO tasks (id, state, created_at, updated_at) VALUES (?, ?, ?, ?)`,
		taskID, StateDispatched, now, now); err != nil {
		t.Fatalf("insert task: %v", err)
	}
	leaseID := "wave259-hb-lease"
	if _, err := db.Exec(`INSERT INTO leases (id, task_id, agent_id, expires_at) VALUES (?, ?, ?, ?)`,
		leaseID, taskID, "agent-hb", now); err != nil {
		t.Fatalf("insert lease: %v", err)
	}

	sm := newMockStateMachine(map[string]TaskState{taskID: StateDispatched})
	lm := &sqliteLeaseManager{db: db, stateMachine: sm}

	if err := lm.RecordHeartbeat(context.Background(), leaseID); err != nil {
		t.Fatalf("RecordHeartbeat: %v", err)
	}
	if got, _ := sm.GetTaskState(taskID); got != StateRunning {
		t.Errorf("expected state RUNNING after heartbeat, got %s", got)
	}
}

func TestWave259_LeaseManager_GetTaskForLease_NotFound(t *testing.T) {
	db := setupLeaseTestDB(t)
	defer db.Close()

	lm := &sqliteLeaseManager{db: db}
	_, err := lm.GetTaskForLease(context.Background(), "wave259-missing-lease")
	if err != ErrLeaseNotFound {
		t.Errorf("expected ErrLeaseNotFound, got %v", err)
	}
}

// ── Closed-DB error path coverage ─────────────────────────────────────────────

// setupClosedLeaseDB creates a sqliteLeaseManager whose underlying DB is
// already closed, so all SQL operations return "sql: database is closed".
func setupClosedLeaseDB(t *testing.T) *sqliteLeaseManager {
	t.Helper()
	db := setupLeaseTestDB(t)
	db.Close() // close immediately — methods will now fail with DB errors
	sm := newMockStateMachine(map[string]TaskState{})
	return &sqliteLeaseManager{db: db, stateMachine: sm}
}

func TestLease_ClosedDB_Claim(t *testing.T) {
	lm := setupClosedLeaseDB(t)
	_, err := lm.Claim(context.Background(), "task-1", "agent-1", time.Minute)
	if err == nil {
		t.Error("expected error on Claim with closed DB")
	}
}

func TestLease_ClosedDB_Renew(t *testing.T) {
	lm := setupClosedLeaseDB(t)
	err := lm.Renew(context.Background(), "lease-1", time.Minute)
	if err == nil {
		t.Error("expected error on Renew with closed DB")
	}
}

func TestLease_ClosedDB_Release(t *testing.T) {
	lm := setupClosedLeaseDB(t)
	err := lm.Release(context.Background(), "lease-1")
	if err == nil {
		t.Error("expected error on Release with closed DB")
	}
}

func TestLease_ClosedDB_Recover(t *testing.T) {
	lm := setupClosedLeaseDB(t)
	_, err := lm.Recover(context.Background())
	if err == nil {
		t.Error("expected error on Recover with closed DB")
	}
}

func TestLease_ClosedDB_RecordHeartbeat(t *testing.T) {
	lm := setupClosedLeaseDB(t)
	err := lm.RecordHeartbeat(context.Background(), "lease-1")
	if err == nil {
		t.Error("expected error on RecordHeartbeat with closed DB")
	}
}

func TestLease_ClosedDB_GetTaskForLease(t *testing.T) {
	lm := setupClosedLeaseDB(t)
	_, err := lm.GetTaskForLease(context.Background(), "lease-1")
	if err == nil {
		t.Error("expected error on GetTaskForLease with closed DB")
	}
}

// TestRelease_WithStateMachine_Running covers the state machine transition paths
// inside Release (L229-243 in lease.go): stateMachine != nil, GetTaskState returns
// StateRunning, then Transition is called.
func TestRelease_WithStateMachine_Running(t *testing.T) {
	db := setupLeaseTestDB(t)
	defer db.Close()

	taskID := "release-sm-running"
	now := time.Now().UTC().Format(time.RFC3339)
	if _, err := db.Exec(`INSERT INTO tasks (id, state, created_at, updated_at) VALUES (?, ?, ?, ?)`,
		taskID, StateRunning, now, now); err != nil {
		t.Fatalf("insert task: %v", err)
	}

	sm := newMockStateMachine(map[string]TaskState{taskID: StateRunning})
	m := &sqliteLeaseManager{db: db, stateMachine: sm}
	ctx := context.Background()

	lease, err := m.Claim(ctx, taskID, "agent-sm-rel", 30*time.Minute)
	if err != nil {
		t.Fatalf("Claim: %v", err)
	}

	if err := m.Release(ctx, lease.ID); err != nil {
		t.Fatalf("Release: %v", err)
	}

	// Verify the state machine received the COMPLETED transition.
	found := false
	for _, tr := range sm.transitions {
		if tr.taskID == taskID && tr.to == StateCompleted {
			found = true
			break
		}
	}
	if !found {
		t.Error("expected COMPLETED state machine transition for released running task")
	}
}

// TestRecover_WithStateMachine covers the state machine transition block inside
// Recover (L279-314 in lease.go): stateMachine != nil with recovered leases and
// task in StateRunning — triggers FAILED transition + auto-requeue logic.
func TestRecover_WithStateMachine(t *testing.T) {
	db := setupLeaseTestDB(t)
	defer db.Close()

	taskID := "recover-sm-task"
	now := time.Now().UTC().Format(time.RFC3339)
	if _, err := db.Exec(`INSERT INTO tasks (id, state, created_at, updated_at) VALUES (?, ?, ?, ?)`,
		taskID, StateRunning, now, now); err != nil {
		t.Fatalf("insert task: %v", err)
	}

	sm := newMockStateMachine(map[string]TaskState{taskID: StateRunning})
	m := &sqliteLeaseManager{db: db, stateMachine: sm}
	ctx := context.Background()

	// Claim with negative TTL so the lease is already expired.
	if _, err := m.Claim(ctx, taskID, "agent-sm-recover", -1*time.Hour); err != nil {
		t.Fatalf("Claim: %v", err)
	}

	recovered, err := m.Recover(ctx)
	if err != nil {
		t.Fatalf("Recover: %v", err)
	}
	if len(recovered) == 0 {
		t.Fatal("expected at least 1 recovered lease")
	}

	// Verify the FAILED transition was recorded.
	foundFailed := false
	for _, tr := range sm.transitions {
		if tr.taskID == taskID && tr.to == StateFailed {
			foundFailed = true
			break
		}
	}
	if !foundFailed {
		t.Error("expected FAILED state machine transition for expired task")
	}
}
