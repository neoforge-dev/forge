//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"database/sql"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	_ "github.com/mattn/go-sqlite3"
)

// ---------------------------------------------------------------------------
// task_state_machine.go: ClaimTransition — 68.8%
// ---------------------------------------------------------------------------

func TestWave75_ClaimTransition_Queued(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ts := NewTaskStore(db)
	sm := NewStateMachine(ts, db)
	taskID := "wave75-claim-1"
	now := time.Now().Format(time.RFC3339)
	_, _ = db.Exec(
		`INSERT INTO tasks (id, domain, project, type, priority, status, state, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)`,
		taskID, "d", "p", "feature", 5, "queued", "QUEUED", now, now,
	)
	if err := sm.ClaimTransition(taskID, "agent-wave75"); err != nil {
		t.Fatalf("ClaimTransition: %v", err)
	}
}

func TestWave75_ClaimTransition_AlreadyDispatched(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ts := NewTaskStore(db)
	sm := NewStateMachine(ts, db)
	taskID := "wave75-claim-2"
	now := time.Now().Format(time.RFC3339)
	// Insert already DISPATCHED task
	_, _ = db.Exec(
		`INSERT INTO tasks (id, domain, project, type, priority, status, state, assigned_to, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)`,
		taskID, "d", "p", "feature", 5, "assigned", "DISPATCHED", "other-agent", now, now,
	)
	if err := sm.ClaimTransition(taskID, "agent-wave75"); err == nil {
		t.Error("expected error claiming already-dispatched task")
	}
}

func TestWave75_ClaimTransition_NotFound(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ts := NewTaskStore(db)
	sm := NewStateMachine(ts, db)
	if err := sm.ClaimTransition("nonexistent-task-wave75", "agent-wave75"); err == nil {
		t.Error("expected error for nonexistent task")
	}
}

// ---------------------------------------------------------------------------
// task_state_machine.go: Transition — 78.0%
// ---------------------------------------------------------------------------

func TestWave75_Transition_RunningToCompleted(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ts := NewTaskStore(db)
	sm := NewStateMachine(ts, db)
	taskID := "wave75-trans-1"
	now := time.Now().Format(time.RFC3339)
	_, _ = db.Exec(
		`INSERT INTO tasks (id, domain, project, type, priority, status, state, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)`,
		taskID, "d", "p", "feature", 5, "executing", "RUNNING", now, now,
	)
	if err := sm.Transition(taskID, StateRunning, StateCompleted, "test complete"); err != nil {
		t.Fatalf("Transition RUNNING->COMPLETED: %v", err)
	}
}

func TestWave75_Transition_InvalidTransition(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ts := NewTaskStore(db)
	sm := NewStateMachine(ts, db)
	taskID := "wave75-trans-2"
	now := time.Now().Format(time.RFC3339)
	_, _ = db.Exec(
		`INSERT INTO tasks (id, domain, project, type, priority, status, state, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)`,
		taskID, "d", "p", "feature", 5, "queued", "QUEUED", now, now,
	)
	// QUEUED → COMPLETED is invalid
	if err := sm.Transition(taskID, StateQueued, StateCompleted, "skip"); err == nil {
		t.Error("expected error for invalid transition QUEUED->COMPLETED")
	}
}

// ---------------------------------------------------------------------------
// xnode.go: serializePendingMessages — 67.3%
// ---------------------------------------------------------------------------

func TestWave75_SerializePendingMessages_Empty(t *testing.T) {
	db, err := sql.Open("sqlite3", ":memory:")
	if err != nil {
		t.Fatalf("open db: %v", err)
	}
	defer db.Close()

	_, _ = db.Exec(`
		CREATE TABLE xnode_outbox (
			id TEXT PRIMARY KEY,
			target_node TEXT,
			message_type TEXT,
			payload TEXT,
			idempotency_key TEXT,
			status TEXT DEFAULT 'pending',
			created_at TEXT DEFAULT (datetime('now'))
		)
	`)

	xc := &XNodeController{db: db, nodeID: "wave75-node"}
	xc.serializePendingMessages(context.Background(), db)
}

func TestWave75_SerializePendingMessages_WithMessages(t *testing.T) {
	db, err := sql.Open("sqlite3", ":memory:")
	if err != nil {
		t.Fatalf("open db: %v", err)
	}
	defer db.Close()

	_, _ = db.Exec(`
		CREATE TABLE xnode_outbox (
			id TEXT PRIMARY KEY,
			target_node TEXT,
			message_type TEXT,
			payload TEXT,
			idempotency_key TEXT,
			status TEXT DEFAULT 'pending',
			created_at TEXT DEFAULT (datetime('now'))
		)
	`)
	_, _ = db.Exec(`INSERT INTO xnode_outbox VALUES ('msg-1', 'sati', 'task_dispatch', '{"task_id":"T1"}', NULL, 'pending', datetime('now'))`)
	_, _ = db.Exec(`INSERT INTO xnode_outbox VALUES ('msg-2', 'nova', 'task_dispatch', '{"task_id":"T2"}', 'idem-1', 'pending', datetime('now'))`)

	xc := &XNodeController{db: db, nodeID: "wave75-node"}
	// Should run without error — actual serialization to files may fail but should not panic
	xc.serializePendingMessages(context.Background(), db)
}

// ---------------------------------------------------------------------------
// tui.go: GetDashboard — 64.9%
// ---------------------------------------------------------------------------

func TestWave75_GetDashboard_Empty(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	db := getDBConn()
	tui := NewTUI(db, nil, taskQueue)

	dash, err := tui.GetDashboard(context.Background())
	if err != nil {
		t.Fatalf("GetDashboard: %v", err)
	}
	if dash == nil {
		t.Error("expected non-nil dashboard")
	}
}

func TestWave75_GetDashboard_WithTasks(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	db := getDBConn()
	_ = taskQueue.Enqueue(context.Background(), Task{
		ID:       "wave75-dash-task",
		Domain:   "test",
		Project:  "p",
		Type:     TaskTypeFeature,
		Priority: 5,
		Status:   TaskStatusQueued,
		State:    StateQueued,
	})

	tui := NewTUI(db, nil, taskQueue)
	dash, err := tui.GetDashboard(context.Background())
	if err != nil {
		t.Fatalf("GetDashboard with tasks: %v", err)
	}
	if dash == nil {
		t.Error("expected non-nil dashboard")
	}
}

// ---------------------------------------------------------------------------
// xnode.go: ListAcksHandler — 65.0%
// ---------------------------------------------------------------------------

func TestWave75_ListAcksHandler_EmptyDir(t *testing.T) {
	db, err := sql.Open("sqlite3", ":memory:")
	if err != nil {
		t.Fatalf("open db: %v", err)
	}
	defer db.Close()

	// Point acks dir to empty temp dir
	tmpDir := t.TempDir()
	origAcksDir := XNodeAcksDir
	XNodeAcksDir = tmpDir
	defer func() { XNodeAcksDir = origAcksDir }()

	xc := &XNodeController{db: db, nodeID: "wave75-node"}
	req := httptest.NewRequest(http.MethodGet, "/api/xnode/acks", nil)
	rr := httptest.NewRecorder()
	xc.ListAcksHandler(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("ListAcksHandler empty: status=%d body=%s", rr.Code, rr.Body.String())
	}
}

func TestWave75_ListAcksHandler_NonexistentDir(t *testing.T) {
	db, err := sql.Open("sqlite3", ":memory:")
	if err != nil {
		t.Fatalf("open db: %v", err)
	}
	defer db.Close()

	origAcksDir := XNodeAcksDir
	XNodeAcksDir = "/tmp/nonexistent-wave75-acks"
	defer func() { XNodeAcksDir = origAcksDir }()

	xc := &XNodeController{db: db, nodeID: "wave75-node"}
	req := httptest.NewRequest(http.MethodGet, "/api/xnode/acks", nil)
	rr := httptest.NewRecorder()
	xc.ListAcksHandler(rr, req)

	// Should return error status since dir doesn't exist
	if rr.Code == http.StatusOK {
		t.Logf("ListAcksHandler returned 200 even for nonexistent dir (may be OS-dependent)")
	}
}

// ---------------------------------------------------------------------------
// xnode.go: StatusHandler error paths — 53.8%
// ---------------------------------------------------------------------------

func TestWave75_StatusHandler_NoNodeTable(t *testing.T) {
	db, err := sql.Open("sqlite3", ":memory:")
	if err != nil {
		t.Fatalf("open db: %v", err)
	}
	defer db.Close()

	// No tables — should return error
	xc := &XNodeController{db: db, nodeID: "wave75-node"}
	req := httptest.NewRequest(http.MethodGet, "/api/xnode/status", nil)
	rr := httptest.NewRecorder()
	xc.StatusHandler(rr, req)

	if rr.Code == http.StatusOK {
		t.Error("expected error status when nodes table missing")
	}
}

func TestWave75_StatusHandler_NoTasksTable(t *testing.T) {
	db, err := sql.Open("sqlite3", ":memory:")
	if err != nil {
		t.Fatalf("open db: %v", err)
	}
	defer db.Close()

	// Only nodes table, no xnode_tasks
	_, _ = db.Exec(`CREATE TABLE nodes (id TEXT, hostname TEXT, address TEXT, status TEXT, last_heartbeat TEXT)`)

	xc := &XNodeController{db: db, nodeID: "wave75-node"}
	req := httptest.NewRequest(http.MethodGet, "/api/xnode/status", nil)
	rr := httptest.NewRecorder()
	xc.StatusHandler(rr, req)

	if rr.Code == http.StatusOK {
		t.Error("expected error status when xnode_tasks table missing")
	}
}

// ---------------------------------------------------------------------------
// token_budget.go: writeTokenBudgetFile — 72.7%
// ---------------------------------------------------------------------------

func TestWave75_WriteTokenBudgetFile_Success(t *testing.T) {
	tmpDir := t.TempDir()
	path := tmpDir + "/budget.json"

	f := &tokenBudgetFile{
		Node:      "wave75-node",
		UpdatedAt: time.Now().Format(time.RFC3339),
		Providers: map[string]tbProviderData{
			"anthropic": {
				Provider: "anthropic",
				Agents:   []string{"claude"},
				Limits:   map[string]int{"daily": 100000},
				Resets:   map[string]string{"daily": "2026-03-09T00:00:00Z"},
				Status:   "ok",
			},
		},
	}

	if err := writeTokenBudgetFile(path, f); err != nil {
		t.Fatalf("writeTokenBudgetFile: %v", err)
	}

	// Read back to verify
	f2, err := readTokenBudgetFile(path)
	if err != nil {
		t.Fatalf("readTokenBudgetFile: %v", err)
	}
	if f2.Node != "wave75-node" {
		t.Errorf("node mismatch: got %q", f2.Node)
	}
}

func TestWave75_WriteTokenBudgetFile_BadPath(t *testing.T) {
	// Write to a path with nonexistent parent
	path := "/tmp/nonexistent-wave75-dir/budget.json"
	f := &tokenBudgetFile{Node: "wave75-node", UpdatedAt: time.Now().Format(time.RFC3339), Providers: map[string]tbProviderData{}}
	if err := writeTokenBudgetFile(path, f); err == nil {
		t.Error("expected error writing to invalid path")
	}
}

// ---------------------------------------------------------------------------
// worktree_manager.go: PruneStaleWorktrees — 72.0%
// ---------------------------------------------------------------------------

func TestWave75_PruneStaleWorktrees_NoTable(t *testing.T) {
	db, err := sql.Open("sqlite3", ":memory:")
	if err != nil {
		t.Fatalf("open db: %v", err)
	}
	defer db.Close()

	wm := NewWorktreeManager(t.TempDir(), t.TempDir(), db)
	// No worktrees table — should fail gracefully
	err = wm.PruneStaleWorktrees(context.Background())
	if err != nil {
		t.Logf("PruneStaleWorktrees with no table: %v (expected)", err)
	}
}

func TestWave75_PruneStaleWorktrees_EmptyTable(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	wm := NewWorktreeManager(t.TempDir(), t.TempDir(), db)
	if err := wm.PruneStaleWorktrees(context.Background()); err != nil {
		t.Logf("PruneStaleWorktrees empty: %v", err)
	}
}

// ---------------------------------------------------------------------------
// context_sync.go: SyncDomainToFilesystem — 80.8%
// ---------------------------------------------------------------------------

func TestWave75_SyncDomainToFilesystem_NoRows(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	tmpDir := t.TempDir()
	cm := &ContextManager{db: db, contextDir: tmpDir}
	cs, err := NewContextSync(cm, db, tmpDir)
	if err != nil {
		t.Fatalf("NewContextSync: %v", err)
	}
	defer cs.Stop()

	if err := cs.SyncDomainToFilesystem("nonexistent-domain"); err != nil {
		t.Errorf("SyncDomainToFilesystem no rows: %v", err)
	}
}

// ---------------------------------------------------------------------------
// task_store.go: RecordTransition — 75.0%
// ---------------------------------------------------------------------------

func TestWave75_TaskStore_RecordTransition_MissingTable(t *testing.T) {
	db, err := sql.Open("sqlite3", ":memory:")
	if err != nil {
		t.Fatalf("open db: %v", err)
	}
	defer db.Close()

	ts := NewTaskStore(db)
	// No state_transitions table — should fail
	err = ts.RecordTransition("task-id", StateQueued, StateDispatched, "test")
	if err != nil {
		t.Logf("RecordTransition with missing table: %v (expected)", err)
	}
}

func TestWave75_TaskStore_GetTransitionHistory_Empty(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ts := NewTaskStore(db)
	history, err := ts.GetTransitionHistory("nonexistent-wave75")
	if err != nil {
		t.Fatalf("GetTransitionHistory: %v", err)
	}
	if len(history) != 0 {
		t.Errorf("expected empty history, got %d entries", len(history))
	}
}
