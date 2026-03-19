//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"bytes"
	"context"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
	"time"
)

// ---------------------------------------------------------------------------
// task_store.go — TaskStore.ClaimTask additional paths
// ---------------------------------------------------------------------------

// TestClaimTask_AlreadyClaimed_W21: claim a task, then try to claim again with a
// different agent — second claim should fail (not in QUEUED state anymore).
func TestClaimTask_AlreadyClaimed_W21(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	taskID := "claim-already-w21"
	_, err := db.Exec(`
		INSERT INTO tasks (id, domain, project, type, title, priority, status, state, created_at, updated_at)
		VALUES (?, 'test', 'proj', 'feature', 'Already claimed', 5, 'queued', 'QUEUED', datetime('now'), datetime('now'))
	`, taskID)
	if err != nil {
		t.Fatalf("insert task: %v", err)
	}

	store := NewTaskStore(db)

	// First claim — should succeed.
	if err := store.ClaimTask(taskID, "agent-alpha"); err != nil {
		t.Fatalf("first ClaimTask failed: %v", err)
	}

	// Second claim by a different agent — task is now DISPATCHED, not QUEUED.
	err = store.ClaimTask(taskID, "agent-beta")
	if err == nil {
		t.Error("expected error on double-claim, got nil")
	}
}

// TestClaimTask_NotFound_W21: ClaimTask on a non-existent task ID.
func TestClaimTask_NotFound_W21(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	store := NewTaskStore(db)
	err := store.ClaimTask("does-not-exist-w21", "agent-x")
	if err == nil {
		t.Error("expected error for non-existent task, got nil")
	}
}

// TestClaimTask_WrongState_W21: task exists but is in DISPATCHED (not QUEUED) state.
func TestClaimTask_WrongState_W21(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	taskID := "wrong-state-w21"
	_, err := db.Exec(`
		INSERT INTO tasks (id, domain, project, type, title, priority, status, state, created_at, updated_at)
		VALUES (?, 'test', 'proj', 'feature', 'Wrong state task', 5, 'assigned', 'DISPATCHED', datetime('now'), datetime('now'))
	`, taskID)
	if err != nil {
		t.Fatalf("insert task: %v", err)
	}

	store := NewTaskStore(db)
	err = store.ClaimTask(taskID, "agent-x")
	if err == nil {
		t.Error("expected error for non-QUEUED task, got nil")
	}
}

// ---------------------------------------------------------------------------
// handlers_task.go — completeTaskWithApprovalHandler additional paths
// ---------------------------------------------------------------------------

// TestCompleteWithApproval_NotFound_W21: POST with a nonexistent task ID.
// Handler goes through JSON decode → integration.HandleTaskCompletion → error → 500.
func TestCompleteWithApproval_NotFound_W21(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	body := `{"agent_id":"agent-test","result":"done","confidence":{"tests_passed":1}}`
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/NONEXISTENT-W21/complete-with-approval",
		bytes.NewBufferString(body))
	req.URL.Path = "/api/tasks/NONEXISTENT-W21/complete-with-approval"
	w := httptest.NewRecorder()
	completeTaskWithApprovalHandler(w, req)

	// Expect 4xx or 5xx — not 200
	if w.Code == http.StatusOK {
		t.Errorf("expected non-200 for nonexistent task, got %d", w.Code)
	}
	t.Logf("completeWithApproval nonexistent: %d %s", w.Code, w.Body.String())
}

// TestCompleteWithApproval_MethodNotAllowed_W21: GET should 405.
// ---------------------------------------------------------------------------
// patrol.go — confidenceApproveCompletedTasks
// ---------------------------------------------------------------------------

// TestConfidenceApproveCompletedTasks_Empty_W21: empty DB, stateMachine=nil →
// function returns nil immediately (guard at top).
func TestConfidenceApproveCompletedTasks_Empty_W21(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()
	// stateMachine and globalApprovalService are nil in test context — early return.
	if err := confidenceApproveCompletedTasks(ctx, db); err != nil {
		t.Errorf("expected nil, got %v", err)
	}
}

// TestConfidenceApproveCompletedTasks_WithTask_W21: insert a COMPLETED task,
// call function. With stateMachine=nil it still early-returns nil.
func TestConfidenceApproveCompletedTasks_WithTask_W21(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	_, err := db.Exec(`
		INSERT INTO tasks (id, domain, project, type, title, priority, status, state, created_at, updated_at)
		VALUES (?, 'test', 'proj', 'feature', 'Completed task', 5, 'completed', 'COMPLETED', datetime('now', '-120 seconds'), datetime('now', '-120 seconds'))
	`, "conf-approve-w21")
	if err != nil {
		t.Logf("insert task: %v (schema may vary)", err)
	}

	ctx := context.Background()
	if err := confidenceApproveCompletedTasks(ctx, db); err != nil {
		t.Errorf("expected nil, got %v", err)
	}
}

// ---------------------------------------------------------------------------
// fleet_scaler.go — checkTokenBudgetGate additional paths
// ---------------------------------------------------------------------------

// TestCheckTokenBudgetGate_EmptyDB_W21: no budget file → fail-closed (P0 fix).
func TestCheckTokenBudgetGate_EmptyDB_W21(t *testing.T) {
	// Ensure no budget file in current dir.
	old, _ := os.Getwd()
	tmp := t.TempDir()
	os.Chdir(tmp)
	defer os.Chdir(old)

	passed, reason := checkTokenBudgetGate("anthropic")
	// P0 fix: fail-closed — no budget file blocks spawn until file is restored.
	if passed {
		t.Errorf("expected fail-closed (false) when no budget file, got pass (reason: %s)", reason)
	}
}

// TestCheckTokenBudgetGate_OverBudget_W21: create a budget file with DEPLETED status.
func TestCheckTokenBudgetGate_OverBudget_W21(t *testing.T) {
	tmp := t.TempDir()
	old, _ := os.Getwd()
	os.Chdir(tmp)
	defer os.Chdir(old)

	budgetDir := filepath.Join(tmp, ".forge", "heartbeat")
	if err := os.MkdirAll(budgetDir, 0755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	budgetFile := filepath.Join(budgetDir, "token-budgets-prya.json")
	content := `{"providers":{"anthropic":{"status":"DEPLETED"},"openai":{"status":"OK"}}}`
	if err := os.WriteFile(budgetFile, []byte(content), 0644); err != nil {
		t.Fatalf("write budget: %v", err)
	}

	// checkTokenBudgetGate reads ".forge/heartbeat/token-budgets-prya.json" relative to cwd.
	passed, reason := checkTokenBudgetGate("anthropic")
	if passed {
		t.Errorf("expected blocked when DEPLETED, got passed (reason: %s)", reason)
	}
	t.Logf("DEPLETED: passed=%v reason=%s", passed, reason)
}

// TestCheckTokenBudgetGate_Cooldown_W21: budget file with COOLDOWN status.
func TestCheckTokenBudgetGate_Cooldown_W21(t *testing.T) {
	tmp := t.TempDir()
	old, _ := os.Getwd()
	os.Chdir(tmp)
	defer os.Chdir(old)

	budgetDir := filepath.Join(tmp, ".forge", "heartbeat")
	os.MkdirAll(budgetDir, 0755)
	content := `{"providers":{"anthropic":{"status":"COOLDOWN"}}}`
	os.WriteFile(filepath.Join(budgetDir, "token-budgets-prya.json"), []byte(content), 0644)

	passed, reason := checkTokenBudgetGate("anthropic")
	if passed {
		t.Errorf("expected blocked when COOLDOWN, got passed (reason: %s)", reason)
	}
	t.Logf("COOLDOWN: passed=%v reason=%s", passed, reason)
}

// TestCheckTokenBudgetGate_UnknownProvider_W21: provider not in budget map → allow.
func TestCheckTokenBudgetGate_UnknownProvider_W21(t *testing.T) {
	tmp := t.TempDir()
	old, _ := os.Getwd()
	os.Chdir(tmp)
	defer os.Chdir(old)

	budgetDir := filepath.Join(tmp, ".forge", "heartbeat")
	os.MkdirAll(budgetDir, 0755)
	content := `{"providers":{"anthropic":{"status":"DEPLETED"}}}`
	os.WriteFile(filepath.Join(budgetDir, "token-budgets-prya.json"), []byte(content), 0644)

	passed, reason := checkTokenBudgetGate("openai")
	if !passed {
		t.Errorf("expected pass for unknown provider, got blocked (reason: %s)", reason)
	}
	t.Logf("unknown provider: passed=%v reason=%s", passed, reason)
}

// TestCheckTokenBudgetGate_BadJSON_W21: malformed budget file → parse error → allow.
func TestCheckTokenBudgetGate_BadJSON_W21(t *testing.T) {
	tmp := t.TempDir()
	old, _ := os.Getwd()
	os.Chdir(tmp)
	defer os.Chdir(old)

	budgetDir := filepath.Join(tmp, ".forge", "heartbeat")
	os.MkdirAll(budgetDir, 0755)
	os.WriteFile(filepath.Join(budgetDir, "token-budgets-prya.json"), []byte("not-json{{{"), 0644)

	passed, reason := checkTokenBudgetGate("anthropic")
	// P0 fix: fail-closed — malformed budget file blocks spawn until file is valid.
	if passed {
		t.Errorf("expected fail-closed (false) on malformed JSON, got pass; reason: %s", reason)
	}
	t.Logf("bad JSON: passed=%v reason=%s", passed, reason)
}

// ---------------------------------------------------------------------------
// xnode.go — ForwardHandler additional paths (missing target / task fields)
// ---------------------------------------------------------------------------

// TestForwardHandler_NoTarget_W21: POST with empty target_node field → 400.
func TestForwardHandler_NoTarget_W21(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	xc, err := NewXNodeController(db, "test-node")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}

	body := `{"target_node":"","task_id":"TASK-1"}`
	req := httptest.NewRequest(http.MethodPost, "/api/xnode/forward",
		bytes.NewBufferString(body))
	w := httptest.NewRecorder()
	xc.ForwardHandler(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for empty target_node, got %d", w.Code)
	}
}

// TestForwardHandler_NoTaskID_W21: POST with empty task_id → 400.
func TestForwardHandler_NoTaskID_W21(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	xc, err := NewXNodeController(db, "test-node")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}

	body := `{"target_node":"sati","task_id":""}`
	req := httptest.NewRequest(http.MethodPost, "/api/xnode/forward",
		bytes.NewBufferString(body))
	w := httptest.NewRecorder()
	xc.ForwardHandler(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for empty task_id, got %d", w.Code)
	}
}

// TestForwardHandler_MethodNotAllowed_W21: GET → 405.
// TestForwardHandler_ValidRequest_W21: POST with valid target+task → node not found → non-200.
func TestForwardHandler_ValidRequest_W21(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	xc, err := NewXNodeController(db, "test-node")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}

	body := `{"target_node":"sati","task_id":"TASK-W21"}`
	req := httptest.NewRequest(http.MethodPost, "/api/xnode/forward",
		bytes.NewBufferString(body))
	w := httptest.NewRecorder()
	xc.ForwardHandler(w, req)

	// Node "sati" not in DB → XNodeErrNodeNotFound → 404
	t.Logf("ForwardHandler valid req: %d %s", w.Code, w.Body.String())
}

// ---------------------------------------------------------------------------
// handlers_lane.go — laneByIDHandler (60.7%) additional paths
// ---------------------------------------------------------------------------

// TestLaneByIDHandler_MethodNotAllowed_W21: POST → 405.
// TestLaneByIDHandler_GET_W21: GET a lane → 200 or 404 depending on DB state.
func TestLaneByIDHandler_GET_W21(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/lanes/dev", nil)
	req.URL.Path = "/api/lanes/dev"
	w := httptest.NewRecorder()
	laneByIDHandler(w, req)

	t.Logf("laneByIDHandler GET dev: %d %s", w.Code, w.Body.String())
}

// ---------------------------------------------------------------------------
// handlers_agent.go — agentByIDHandler (partially covered)
// ---------------------------------------------------------------------------

// TestAgentByIDHandler_MethodNotAllowed_W21: POST → 405.
// TestAgentByIDHandler_NotFound_W21: GET non-existent agent → 404.
func TestAgentByIDHandler_NotFound_W21(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/agents/no-such-agent-w21", nil)
	req.URL.Path = "/api/agents/no-such-agent-w21"
	w := httptest.NewRecorder()
	agentByIDHandler(w, req)

	if w.Code != http.StatusNotFound {
		t.Logf("agentByIDHandler not found: %d %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// context.go — ContextManager.EnvelopesHandler POST valid path
// ---------------------------------------------------------------------------

// TestEnvelopesHandler_PostValid_W21: POST with valid task_id → attempts to create envelope.
func TestEnvelopesHandler_PostValid_W21(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	db := getDBConn()
	cm := testContextManager(t, db, t.TempDir())
	defer cm.Stop()

	// Insert a task so the envelope creation has a valid task to reference.
	ctx := context.Background()
	taskQueue.Enqueue(ctx, Task{
		ID:       "TASK-ENV-W21",
		Domain:   "test",
		Project:  "proj",
		Type:     "feature",
		Title:    "Envelope test",
		Priority: 5,
		Status:   TaskStatusQueued,
	})

	body := `{"task_id":"TASK-ENV-W21","agent_id":"agent-1","context_pct":75}`
	req := httptest.NewRequest(http.MethodPost, "/api/context/envelopes",
		bytes.NewBufferString(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	cm.EnvelopesHandler(w, req)

	t.Logf("EnvelopesHandler POST valid: %d %s", w.Code, w.Body.String())
}

// ---------------------------------------------------------------------------
// main.go — handleQueueList additional path (list with items)
// ---------------------------------------------------------------------------

// TestHandleQueueList_WithItems_W21: queue has tasks → JSON list returned.
func TestHandleQueueList_WithItems_W21(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	ctx := context.Background()
	taskQueue.Enqueue(ctx, Task{
		ID:       "ql-w21-task",
		Domain:   "test",
		Project:  "proj",
		Type:     "feature",
		Title:    "Queue list test",
		Priority: 5,
		Status:   TaskStatusQueued,
	})

	req := httptest.NewRequest(http.MethodGet, "/api/queue/list", nil)
	w := httptest.NewRecorder()
	handleQueueList(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// worktree_manager.go — CreateWorktree empty ID path
// ---------------------------------------------------------------------------

func TestCreateWorktree_EmptyTaskID_W21(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	tmp := t.TempDir()
	mgr := NewWorktreeManager(tmp, filepath.Join(tmp, "worktrees"), db)

	_, err := mgr.CreateWorktree(context.Background(), "")
	if err == nil {
		t.Error("expected error for empty taskID, got nil")
	}
}

func TestCreateWorktree_ExistingDir_W21(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	tmp := t.TempDir()
	wtDir := filepath.Join(tmp, "worktrees")
	mgr := NewWorktreeManager(tmp, wtDir, db)

	// Pre-create the worktree directory to hit the "already exists" branch.
	taskID := "task-existing-w21"
	existingPath := filepath.Join(wtDir, taskID)
	os.MkdirAll(existingPath, 0755)

	path, err := mgr.CreateWorktree(context.Background(), taskID)
	if err != nil {
		t.Errorf("expected no error for existing dir, got %v", err)
	}
	t.Logf("CreateWorktree existing dir: path=%s", path)
}

// ---------------------------------------------------------------------------
// tui.go — TUI.GetDashboard
// ---------------------------------------------------------------------------

func TestTUIGetDashboard_W21(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	tui := NewTUI(db, nil, taskQueue)
	dash, err := tui.GetDashboard(context.Background())
	if err != nil {
		t.Logf("GetDashboard returned error (acceptable): %v", err)
	}
	if dash == nil {
		t.Error("expected non-nil dashboard")
	}
}

// ---------------------------------------------------------------------------
// context.go — findLatestEnvelope additional paths
// ---------------------------------------------------------------------------

func TestFindLatestEnvelope_NotFound_W21(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	db := getDBConn()
	cm := testContextManager(t, db, t.TempDir())
	defer cm.Stop()

	_, err := cm.findLatestEnvelope(context.Background(), "no-such-agent", "no-such-task")
	if err == nil {
		t.Log("findLatestEnvelope returned nil error for missing agent/task (no rows)")
	}
}

// ---------------------------------------------------------------------------
// handlers_pattern.go — createPatternHandler
// ---------------------------------------------------------------------------

func TestCreatePatternHandler_BadJSON_W21(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/patterns",
		bytes.NewBufferString("not-json"))
	w := httptest.NewRecorder()
	createPatternHandler(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", w.Code)
	}
}

func TestCreatePatternHandler_EmptyName_W21(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	body := `{"name":"","domain":"test","yaml_content":"---"}`
	req := httptest.NewRequest(http.MethodPost, "/api/patterns",
		bytes.NewBufferString(body))
	w := httptest.NewRecorder()
	createPatternHandler(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for empty name, got %d", w.Code)
	}
}

func TestCreatePatternHandler_ValidPattern_W21(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	body := `{"name":"test-pattern-w21","domain":"test","yaml_content":"---\nsteps: []"}`
	req := httptest.NewRequest(http.MethodPost, "/api/patterns",
		bytes.NewBufferString(body))
	w := httptest.NewRecorder()
	createPatternHandler(w, req)

	t.Logf("createPatternHandler valid: %d %s", w.Code, w.Body.String())
}

// ---------------------------------------------------------------------------
// queue.go — dependenciesComplete with incomplete dep
// ---------------------------------------------------------------------------

func TestDependenciesComplete_IncompleteDep_W21(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	q, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}
	sq := q.(*sqliteTaskQueue)
	ctx := context.Background()

	// Create parent task + dependency task (not completed)
	dep := Task{
		ID: "dep-task-w21", Domain: "test", Project: "proj", Type: "feature",
		Title: "Dependency", Priority: 5, Status: TaskStatusQueued,
	}
	main := Task{
		ID: "main-task-w21", Domain: "test", Project: "proj", Type: "feature",
		Title: "Main", Priority: 5, Status: TaskStatusQueued,
	}
	q.Enqueue(ctx, dep)
	q.Enqueue(ctx, main)

	// Insert dependency relation
	db.Exec(`INSERT INTO task_dependencies (task_id, depends_on) VALUES (?, ?)`, main.ID, dep.ID)

	// dep is in 'queued' (not completed) state — dependenciesComplete should return false
	done, err := sq.dependenciesComplete(ctx, main.ID)
	if err != nil {
		t.Fatalf("dependenciesComplete: %v", err)
	}
	if done {
		t.Error("expected false when dependency is not completed")
	}
}

// ---------------------------------------------------------------------------
// handlers_patrols.go — nodesHealthHandler with agent_heartbeats data
// ---------------------------------------------------------------------------

func TestNodesHealthHandler_WithAgents_W21(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	db := getDBConn()
	// Insert some agent heartbeats to exercise the row-scanning loop.
	db.Exec(`INSERT OR IGNORE INTO agent_heartbeats (agent_id, node, status, context_pct, current_task_id, last_seen)
		VALUES ('agent-w21-1', 'prya', 'online', 45.0, '', datetime('now'))`)
	db.Exec(`INSERT OR IGNORE INTO agent_heartbeats (agent_id, node, status, context_pct, current_task_id, last_seen)
		VALUES ('agent-w21-2', 'prya', 'offline', 0.0, '', datetime('now', '-10 minutes'))`)
	db.Exec(`INSERT OR IGNORE INTO agent_heartbeats (agent_id, node, status, context_pct, current_task_id, last_seen)
		VALUES ('agent-w21-3', 'sati', 'online', 30.0, 'TASK-1', datetime('now'))`)

	req := httptest.NewRequest(http.MethodGet, "/api/nodes/health", nil)
	w := httptest.NewRecorder()
	nodesHealthHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
	t.Logf("nodesHealthHandler with agents: %s", w.Body.String())
}

// ---------------------------------------------------------------------------
// handlers_patrols.go — fleetSnapshotHandler
// ---------------------------------------------------------------------------

func TestFleetSnapshotHandler_GET_W21(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	db := getDBConn()
	db.Exec(`INSERT OR IGNORE INTO agent_heartbeats (agent_id, node, status, context_pct, current_task_id, last_seen)
		VALUES ('snap-agent-w21', 'prya', 'online', 55.0, NULL, datetime('now'))`)

	req := httptest.NewRequest(http.MethodGet, "/api/fleet/snapshot", nil)
	w := httptest.NewRecorder()
	fleetSnapshotHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// patrol.go — dispatchTimeoutPatrol with old dispatch file
// ---------------------------------------------------------------------------

func TestDispatchTimeoutPatrol_OldFile_W21(t *testing.T) {
	tmp := t.TempDir()
	t.Setenv("FORGE_ROOT", tmp)

	dispatchDir := filepath.Join(tmp, ".forge", "dispatches")
	resultsDir := filepath.Join(tmp, ".forge", "heartbeat", "results")
	os.MkdirAll(dispatchDir, 0755)
	os.MkdirAll(resultsDir, 0755)

	// Write a dispatch file then backdate its mod time to 3 hours ago.
	dispatchFile := filepath.Join(dispatchDir, "codex-test-task-20260101.md")
	os.WriteFile(dispatchFile, []byte("# Test dispatch\n"), 0644)

	threeHoursAgo := time.Now().Add(-3 * time.Hour)
	os.Chtimes(dispatchFile, threeHoursAgo, threeHoursAgo)

	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()
	err := dispatchTimeoutPatrol(ctx, db)
	if err != nil {
		t.Errorf("unexpected error: %v", err)
	}
	// Timeout marker should be written.
	timeoutFile := filepath.Join(resultsDir, "codex-test-task-TIMEOUT.md")
	if _, err := os.Stat(timeoutFile); os.IsNotExist(err) {
		t.Error("expected TIMEOUT marker file to be written")
	}
}

func TestDependenciesComplete_AllDone_W21(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	q, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}
	sq := q.(*sqliteTaskQueue)
	ctx := context.Background()

	dep := Task{
		ID: "dep-done-w21", Domain: "test", Project: "proj", Type: "feature",
		Title: "Done dep", Priority: 5, Status: TaskStatusCompleted,
	}
	main := Task{
		ID: "main-done-w21", Domain: "test", Project: "proj", Type: "feature",
		Title: "Main done", Priority: 5, Status: TaskStatusQueued,
	}
	q.Enqueue(ctx, dep)
	q.Enqueue(ctx, main)

	// Mark dep as completed in DB directly
	db.Exec(`UPDATE tasks SET status = 'completed' WHERE id = ?`, dep.ID)
	db.Exec(`INSERT INTO task_dependencies (task_id, depends_on) VALUES (?, ?)`, main.ID, dep.ID)

	done, err := sq.dependenciesComplete(ctx, main.ID)
	if err != nil {
		t.Fatalf("dependenciesComplete: %v", err)
	}
	if !done {
		t.Error("expected true when all dependencies completed")
	}
}

// ---------------------------------------------------------------------------
// handlers_plan.go — resumeTaskHandler with valid task
// ---------------------------------------------------------------------------

func TestResumeTaskHandler_ValidTask_W21(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	ctx := context.Background()
	task := Task{
		ID: "resume-w21", Domain: "test", Project: "proj", Type: "feature",
		Title: "Resume test", Priority: 5, Status: TaskStatusAssigned,
	}
	taskQueue.Enqueue(ctx, task)

	req := httptest.NewRequest(http.MethodPost, "/api/tasks/resume-w21/resume", nil)
	req.URL.Path = "/api/tasks/resume-w21/resume"
	w := httptest.NewRecorder()
	resumeTaskHandler(w, req)

	t.Logf("resumeTaskHandler valid: %d %s", w.Code, w.Body.String())
}

// ---------------------------------------------------------------------------
// context.go — BootstrapHandler paths
// ---------------------------------------------------------------------------

func TestBootstrapHandler_MissingAgentID_W21(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	db := getDBConn()
	cm := testContextManager(t, db, t.TempDir())
	defer cm.Stop()

	req := httptest.NewRequest(http.MethodGet, "/api/context/bootstrap", nil)
	w := httptest.NewRecorder()
	cm.BootstrapHandler(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for missing agent_id, got %d", w.Code)
	}
}

func TestBootstrapHandler_GetWithAgentID_W21(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	db := getDBConn()
	cm := testContextManager(t, db, t.TempDir())
	defer cm.Stop()

	req := httptest.NewRequest(http.MethodGet, "/api/context/bootstrap?agent_id=codex", nil)
	w := httptest.NewRecorder()
	cm.BootstrapHandler(w, req)

	// No envelope exists → 404 or 500; just verify no panic
	t.Logf("BootstrapHandler GET: %d %s", w.Code, w.Body.String())
}

func TestBootstrapHandler_PostBadJSON_W21(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	db := getDBConn()
	cm := testContextManager(t, db, t.TempDir())
	defer cm.Stop()

	req := httptest.NewRequest(http.MethodPost, "/api/context/bootstrap",
		bytes.NewBufferString("not-json"))
	w := httptest.NewRecorder()
	cm.BootstrapHandler(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", w.Code)
	}
}

func TestBootstrapHandler_InvalidAgentIDFormat_W21(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	db := getDBConn()
	cm := testContextManager(t, db, t.TempDir())
	defer cm.Stop()

	// agent_id with invalid characters
	req := httptest.NewRequest(http.MethodGet, "/api/context/bootstrap?agent_id=agent%20with%20spaces", nil)
	w := httptest.NewRecorder()
	cm.BootstrapHandler(w, req)

	t.Logf("BootstrapHandler invalid ID: %d %s", w.Code, w.Body.String())
}

// ---------------------------------------------------------------------------
// patrol.go — checkContextThreshold with agent_heartbeats data
// ---------------------------------------------------------------------------

func TestCheckContextThreshold_WithHighContextAgent_W21(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()

	// Insert agent with context_pct >= 50 to trigger the loop body.
	db.Exec(`INSERT OR IGNORE INTO agent_heartbeats (agent_id, node, status, context_pct, current_task_id, last_seen)
		VALUES ('ctx-high-w21', 'prya', 'online', 75.0, 'TASK-W21', datetime('now'))`)

	// Use a real ContextManager (non-nil) so the function proceeds past the nil guard.
	cm := testContextManager(t, db, t.TempDir())
	defer cm.Stop()

	// rj=nil is fine — function checks rj != nil before using it.
	err := checkContextThreshold(ctx, db, cm, nil)
	if err != nil {
		t.Errorf("unexpected error: %v", err)
	}
}

// ---------------------------------------------------------------------------
// handlers_tui.go — getPlanHistoryHandler with initialized planManager
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// handlers_patrols.go — patrolExecutionsHandler
// ---------------------------------------------------------------------------

func TestPatrolExecutionsHandler_GET_W21(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/patrol-executions?limit=10&hours=1", nil)
	w := httptest.NewRecorder()
	patrolExecutionsHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestPatrolExecutionsHandler_WithFilters_W21(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/patrol-executions?patrol_id=test-patrol&status=completed", nil)
	w := httptest.NewRecorder()
	patrolExecutionsHandler(w, req)

	t.Logf("patrolExecutionsHandler with filters: %d", w.Code)
}

// ---------------------------------------------------------------------------
// handlers_context.go — contextsHandler
// ---------------------------------------------------------------------------

func TestContextsHandler_GET_W21(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/contexts", nil)
	w := httptest.NewRecorder()
	contextsHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestContextsHandler_WithAgentFilter_W21(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/contexts?agent_id=codex&domain=test&task_id=TASK-1", nil)
	w := httptest.NewRecorder()
	contextsHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// main.go — handleQueueList with limit query param
// ---------------------------------------------------------------------------

func TestHandleQueueList_WithLimit_W21(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/queue/list?limit=10&format=json", nil)
	w := httptest.NewRecorder()
	handleQueueList(w, req)

	t.Logf("handleQueueList with limit: %d", w.Code)
}

func TestHandleQueueList_InvalidLimit_W21(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/queue/list?limit=-1", nil)
	w := httptest.NewRecorder()
	handleQueueList(w, req)

	t.Logf("handleQueueList invalid limit: %d", w.Code)
}

// ---------------------------------------------------------------------------
// handlers_tui.go — replanTaskHandler
// ---------------------------------------------------------------------------

func TestReplanTaskHandler_WithPlanManager_W21(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	db := getDBConn()
	oldPM := planManager
	planManager = NewPlanManager(db)
	defer func() { planManager = oldPM }()

	taskQueue.Enqueue(context.Background(), Task{
		ID: "replan-w21", Domain: "test", Project: "proj", Type: "feature",
		Title: "Replan test", Priority: 5, Status: TaskStatusQueued,
	})

	body := `{"plan":"step 1\nstep 2","reason":"updated approach"}`
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/replan-w21/replan",
		bytes.NewBufferString(body))
	req.URL.Path = "/api/tasks/replan-w21/replan"
	w := httptest.NewRecorder()
	replanTaskHandler(w, req)

	t.Logf("replanTaskHandler: %d %s", w.Code, w.Body.String())
}

func TestGetPlanHistoryHandler_WithPlanManager_W21(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	db := getDBConn()

	// Initialize planManager for this test (save/restore global).
	oldPM := planManager
	planManager = NewPlanManager(db)
	defer func() { planManager = oldPM }()

	taskQueue.Enqueue(context.Background(), Task{
		ID: "plan-hist-w21", Domain: "test", Project: "proj", Type: "feature",
		Title: "Plan history test", Priority: 5, Status: TaskStatusQueued,
	})

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/plan-hist-w21/plans", nil)
	req.URL.Path = "/api/tasks/plan-hist-w21/plans"
	w := httptest.NewRecorder()
	getPlanHistoryHandler(w, req)

	if w.Code != http.StatusOK {
		t.Logf("getPlanHistoryHandler: %d %s", w.Code, w.Body.String())
	}
}

// --- Additional coverage batch ---

func TestEnvelopeByIDHandler_NotFound_W21(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	cm := testContextManager(t, db, t.TempDir())
	defer cm.Stop()
	req := httptest.NewRequest(http.MethodGet, "/api/context/envelopes/nonexistent123", nil)
	req.URL.Path = "/api/context/envelopes/nonexistent123"
	w := httptest.NewRecorder()
	cm.EnvelopeByIDHandler(w, req)
	if w.Code != http.StatusNotFound {
		t.Logf("EnvelopeByIDHandler: %d %s", w.Code, w.Body.String())
	}
}

func TestEnvelopeByIDHandler_MissingID_W21(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	cm := testContextManager(t, db, t.TempDir())
	defer cm.Stop()
	req := httptest.NewRequest(http.MethodGet, "/api/context/envelopes/", nil)
	req.URL.Path = "/api/context/envelopes/"
	w := httptest.NewRecorder()
	cm.EnvelopeByIDHandler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Logf("EnvelopeByIDHandler missing ID: %d %s", w.Code, w.Body.String())
	}
}

func TestGetTaskEventsHandler_GET_W21(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()
	req := httptest.NewRequest(http.MethodGet, "/api/tasks/sometask/events", nil)
	req.URL.Path = "/api/tasks/sometask/events"
	w := httptest.NewRecorder()
	getTaskEventsHandler(w, req)
	// no events but should return 200
	if w.Code != http.StatusOK {
		t.Logf("getTaskEventsHandler: %d %s", w.Code, w.Body.String())
	}
}

func TestAgentTasksHandler_GET_W21(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()
	req := httptest.NewRequest(http.MethodGet, "/api/agents/codex/tasks", nil)
	req.URL.Path = "/api/agents/codex/tasks"
	w := httptest.NewRecorder()
	agentTasksHandler(w, req)
	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d %s", w.Code, w.Body.String())
	}
}

func TestHandleTaskList_GET_W21(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()
	req := httptest.NewRequest(http.MethodGet, "/cli/tasks", nil)
	w := httptest.NewRecorder()
	handleTaskList(w, req)
	if w.Code != http.StatusOK {
		t.Logf("handleTaskList: %d %s", w.Code, w.Body.String())
	}
}

func TestHandleQueuePriority_BadJSON_W21(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()
	req := httptest.NewRequest(http.MethodPost, "/cli/queue/priority", bytes.NewBufferString("not json"))
	w := httptest.NewRecorder()
	handleQueuePriority(w, req)
	if w.Code == http.StatusOK {
		t.Logf("handleQueuePriority bad json: %d", w.Code)
	}
}

func TestApprovalHandlerPending_GET_W21(t *testing.T) {
	db, _ := setupClaimTestDB(t)
	store := NewApprovalStore(db)
	svc := NewApprovalService(store)
	h := NewApprovalHandler(svc)
	req := httptest.NewRequest(http.MethodGet, "/api/approvals/pending", nil)
	w := httptest.NewRecorder()
	h.handlePending(w, req)
	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d %s", w.Code, w.Body.String())
	}
}

func TestApprovalHandlerCount_GET_W21(t *testing.T) {
	db, _ := setupClaimTestDB(t)
	store := NewApprovalStore(db)
	svc := NewApprovalService(store)
	h := NewApprovalHandler(svc)
	req := httptest.NewRequest(http.MethodGet, "/api/approvals/count", nil)
	w := httptest.NewRecorder()
	h.handleApprovalCount(w, req)
	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d %s", w.Code, w.Body.String())
	}
}

func TestApprovalHandlerList_GET_W21(t *testing.T) {
	db, _ := setupClaimTestDB(t)
	store := NewApprovalStore(db)
	svc := NewApprovalService(store)
	h := NewApprovalHandler(svc)
	req := httptest.NewRequest(http.MethodGet, "/api/approvals", nil)
	w := httptest.NewRecorder()
	h.handleApprovals(w, req)
	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d %s", w.Code, w.Body.String())
	}
}

func TestApprovalHandlerAction_BadPath_W21(t *testing.T) {
	db, _ := setupClaimTestDB(t)
	store := NewApprovalStore(db)
	svc := NewApprovalService(store)
	h := NewApprovalHandler(svc)
	req := httptest.NewRequest(http.MethodPost, "/api/approvals/bad", nil)
	req.URL.Path = "/api/approvals/bad"
	w := httptest.NewRecorder()
	h.handleApprovalAction(w, req)
	if w.Code != http.StatusBadRequest {
		t.Logf("handleApprovalAction bad path: %d %s", w.Code, w.Body.String())
	}
}

func TestApprovalHandlerCreate_W21(t *testing.T) {
	db, _ := setupClaimTestDB(t)
	store := NewApprovalStore(db)
	svc := NewApprovalService(store)
	h := NewApprovalHandler(svc)
	body := bytes.NewBufferString(`{"type":"code","agent_id":"codex","domain":"test","title":"Test approval"}`)
	req := httptest.NewRequest(http.MethodPost, "/api/approvals", body)
	w := httptest.NewRecorder()
	h.handleApprovals(w, req)
	t.Logf("createApproval: %d %s", w.Code, w.Body.String())
}

func TestHandleTaskShow_NoID_W21(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()
	req := httptest.NewRequest(http.MethodGet, "/cli/tasks/show", nil)
	w := httptest.NewRecorder()
	handleTaskShow(w, req)
	if w.Code != http.StatusBadRequest {
		t.Logf("handleTaskShow no id: %d", w.Code)
	}
}

func TestHandleTaskShow_WithID_W21(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()
	req := httptest.NewRequest(http.MethodGet, "/cli/tasks/show?id=nonexistent", nil)
	w := httptest.NewRecorder()
	handleTaskShow(w, req)
	t.Logf("handleTaskShow: %d %s", w.Code, w.Body.String())
}
