//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"bytes"
	"context"
	"database/sql"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
	"time"
)

// ---------------------------------------------------------------------------
// handler_helpers.go: writeJSON, requireMethod, withDB — 66.7%
// ---------------------------------------------------------------------------

func TestWave80c_WriteJSON_Success(t *testing.T) {
	rr := httptest.NewRecorder()
	writeJSON(rr, map[string]string{"status": "ok"})
	if rr.Code != http.StatusOK {
		t.Errorf("writeJSON status: got %d", rr.Code)
	}
	if ct := rr.Header().Get("Content-Type"); !strings.Contains(ct, "application/json") {
		t.Errorf("Content-Type: %q", ct)
	}
}

func TestWave80c_RequireMethod_Match(t *testing.T) {
	rr := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/", nil)
	if !requireMethod(rr, req, http.MethodGet) {
		t.Error("expected true for matching method")
	}
}

func TestWave80c_RequireMethod_NoMatch(t *testing.T) {
	rr := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodPost, "/", nil)
	if requireMethod(rr, req, http.MethodGet) {
		t.Error("expected false for non-matching method")
	}
	if rr.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", rr.Code)
	}
}

func TestWave80c_WithDB_Nil(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	oldDB := getDBConn()
	setDBConn(nil)
	defer setDBConn(oldDB)

	rr := httptest.NewRecorder()
	called := false
	withDB(rr, func(db *sql.DB) {
		called = true
	})
	if called {
		t.Error("callback should not be called with nil DB")
	}
	if rr.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503, got %d", rr.Code)
	}
}

func TestWave80c_WithDB_Active(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	rr := httptest.NewRecorder()
	called := false
	withDB(rr, func(db *sql.DB) {
		called = true
	})
	if !called {
		t.Error("callback should be called with active DB")
	}
}

// ---------------------------------------------------------------------------
// handlers_task.go: taskDeleteHandler — 75.0%
// ---------------------------------------------------------------------------

func TestWave80c_TaskDelete_MethodNotAllowed(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/some-id", nil)
	rr := httptest.NewRecorder()
	taskDeleteHandler(rr, req)
	if rr.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", rr.Code)
	}
}

func TestWave80c_TaskDelete_MissingID(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodDelete, "/api/tasks/", nil)
	rr := httptest.NewRecorder()
	taskDeleteHandler(rr, req)
	if rr.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", rr.Code)
	}
}

func TestWave80c_TaskDelete_Success(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	ctx := context.Background()
	_ = taskQueue.Enqueue(ctx, Task{
		ID:       "wave80c-del-task",
		Domain:   "test",
		Project:  "p",
		Type:     TaskTypeFeature,
		Priority: 5,
		Status:   TaskStatusQueued,
		State:    StateQueued,
	})

	req := httptest.NewRequest(http.MethodDelete, "/api/tasks/wave80c-del-task", nil)
	rr := httptest.NewRecorder()
	taskDeleteHandler(rr, req)
	if rr.Code >= 500 {
		t.Errorf("unexpected server error: %d %s", rr.Code, rr.Body.String())
	}
}

// ---------------------------------------------------------------------------
// handlers_task.go: pruneTasksHandler — 75.0%
// ---------------------------------------------------------------------------

func TestWave80c_PruneTasks_MethodNotAllowed(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/prune", nil)
	rr := httptest.NewRecorder()
	pruneTasksHandler(rr, req)
	if rr.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", rr.Code)
	}
}

func TestWave80c_PruneTasks_Success(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/tasks/prune", nil)
	rr := httptest.NewRecorder()
	pruneTasksHandler(rr, req)
	if rr.Code != http.StatusOK {
		t.Errorf("expected 200, got %d body=%s", rr.Code, rr.Body.String())
	}
}

// ---------------------------------------------------------------------------
// handlers_task.go: abandonTaskHandler — 73.9%
// ---------------------------------------------------------------------------

func TestWave80c_AbandonTask_MethodNotAllowed(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/some-id/abandon", nil)
	rr := httptest.NewRecorder()
	abandonTaskHandler(rr, req)
	if rr.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", rr.Code)
	}
}

func TestWave80c_AbandonTask_MissingID(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/tasks//abandon", nil)
	rr := httptest.NewRecorder()
	abandonTaskHandler(rr, req)
	if rr.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", rr.Code)
	}
}

func TestWave80c_AbandonTask_NotFound(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/tasks/nonexistent-wave80c/abandon", nil)
	rr := httptest.NewRecorder()
	abandonTaskHandler(rr, req)
	if rr.Code != http.StatusNotFound {
		t.Errorf("expected 404, got %d", rr.Code)
	}
}

func TestWave80c_AbandonTask_Success(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	ctx := context.Background()
	_ = taskQueue.Enqueue(ctx, Task{
		ID:       "wave80c-abandon-task",
		Domain:   "test",
		Project:  "p",
		Type:     TaskTypeFeature,
		Priority: 5,
		Status:   TaskStatusQueued,
		State:    StateQueued,
	})

	req := httptest.NewRequest(http.MethodPost, "/api/tasks/wave80c-abandon-task/abandon", nil)
	rr := httptest.NewRecorder()
	abandonTaskHandler(rr, req)
	if rr.Code != http.StatusOK {
		t.Errorf("expected 200, got %d body=%s", rr.Code, rr.Body.String())
	}
}

// ---------------------------------------------------------------------------
// handlers_task.go: taskHistoryHandler
// ---------------------------------------------------------------------------

func TestWave80c_TaskHistory_MethodNotAllowed(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/tasks/some-id/history", nil)
	rr := httptest.NewRecorder()
	taskHistoryHandler(rr, req)
	if rr.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", rr.Code)
	}
}

func TestWave80c_TaskHistory_MissingID(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/tasks//history", nil)
	rr := httptest.NewRecorder()
	taskHistoryHandler(rr, req)
	if rr.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", rr.Code)
	}
}

func TestWave80c_TaskHistory_ValidTask(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	ctx := context.Background()
	_ = taskQueue.Enqueue(ctx, Task{
		ID:       "wave80c-history-task",
		Domain:   "test",
		Project:  "p",
		Type:     TaskTypeFeature,
		Priority: 5,
		Status:   TaskStatusQueued,
		State:    StateQueued,
	})

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/wave80c-history-task/history", nil)
	rr := httptest.NewRecorder()
	taskHistoryHandler(rr, req)
	if rr.Code != http.StatusOK {
		t.Errorf("expected 200, got %d body=%s", rr.Code, rr.Body.String())
	}
}

// ---------------------------------------------------------------------------
// handlers_agent.go: agentMetricsHandler — 74.5%
// ---------------------------------------------------------------------------

func TestWave80c_AgentMetrics_MethodNotAllowed(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/agents/agent-1/metrics", nil)
	rr := httptest.NewRecorder()
	agentMetricsHandler(rr, req)
	if rr.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", rr.Code)
	}
}

func TestWave80c_AgentMetrics_MissingID(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/agents//metrics", nil)
	rr := httptest.NewRecorder()
	agentMetricsHandler(rr, req)
	if rr.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", rr.Code)
	}
}

func TestWave80c_AgentMetrics_WithData(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	db := getDBConn()
	now := time.Now().Format(time.RFC3339)
	_, _ = db.Exec(
		`INSERT INTO agent_telemetry (agent_id, node, context_pct, timestamp) VALUES (?,?,?,?)`,
		"wave80c-agent", "prya", 42.0, now,
	)

	req := httptest.NewRequest(http.MethodGet, "/api/agents/wave80c-agent/metrics", nil)
	rr := httptest.NewRecorder()
	agentMetricsHandler(rr, req)
	if rr.Code != http.StatusOK {
		t.Errorf("expected 200, got %d body=%s", rr.Code, rr.Body.String())
	}
}

func TestWave80c_AgentMetrics_WithSinceParam(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	since := time.Now().Add(-1 * time.Hour).Format(time.RFC3339)
	req := httptest.NewRequest(http.MethodGet, "/api/agents/wave80c-agent2/metrics?since="+since, nil)
	rr := httptest.NewRecorder()
	agentMetricsHandler(rr, req)
	if rr.Code != http.StatusOK {
		t.Errorf("expected 200, got %d body=%s", rr.Code, rr.Body.String())
	}
}

// ---------------------------------------------------------------------------
// handlers_task.go: getTaskEventsHandler
// ---------------------------------------------------------------------------

func TestWave80c_GetTaskEvents_MethodNotAllowed(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/tasks/some-id/events", nil)
	rr := httptest.NewRecorder()
	getTaskEventsHandler(rr, req)
	if rr.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", rr.Code)
	}
}

func TestWave80c_GetTaskEvents_MissingID(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/tasks//events", nil)
	rr := httptest.NewRecorder()
	getTaskEventsHandler(rr, req)
	if rr.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", rr.Code)
	}
}

func TestWave80c_GetTaskEvents_WithTask(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	ctx := context.Background()
	_ = taskQueue.Enqueue(ctx, Task{
		ID:       "wave80c-events-task",
		Domain:   "test",
		Project:  "p",
		Type:     TaskTypeFeature,
		Priority: 5,
		Status:   TaskStatusQueued,
		State:    StateQueued,
	})

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/wave80c-events-task/events", nil)
	rr := httptest.NewRecorder()
	getTaskEventsHandler(rr, req)
	if rr.Code != http.StatusOK {
		t.Errorf("expected 200, got %d body=%s", rr.Code, rr.Body.String())
	}
}

// ---------------------------------------------------------------------------
// handlers_agent.go: agentsHealthHandler specific agent path
// ---------------------------------------------------------------------------

func TestWave80c_AgentsHealthHandler_AllAgents(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/agents/", nil)
	rr := httptest.NewRecorder()
	agentsHealthHandler(rr, req)
	if rr.Code != http.StatusOK {
		t.Errorf("expected 200, got %d body=%s", rr.Code, rr.Body.String())
	}
}

// ---------------------------------------------------------------------------
// openclawEventsHandler — 36.7% (SSE — goroutine + context cancel)
// ---------------------------------------------------------------------------

func TestWave80c_OpenclawEvents_MethodNotAllowed(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/openclaw/events", nil)
	rr := httptest.NewRecorder()
	openclawEventsHandler(rr, req)
	if rr.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", rr.Code)
	}
}

func TestWave80c_OpenclawEvents_SSE_Connect(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	// httptest.NewRecorder implements http.Flusher in Go 1.26 — handler loops.
	// Use context with short timeout to exercise the initial SSE connection path.
	ctx, cancel := context.WithTimeout(context.Background(), 200*time.Millisecond)
	defer cancel()

	req := httptest.NewRequest(http.MethodGet, "/api/openclaw/events", nil)
	req = req.WithContext(ctx)
	rr := httptest.NewRecorder()

	done := make(chan struct{})
	go func() {
		openclawEventsHandler(rr, req)
		close(done)
	}()

	select {
	case <-done:
		// Handler returned cleanly
	case <-time.After(500 * time.Millisecond):
		// Still running after timeout — SSE loop is long-lived
	}
	t.Logf("openclawEventsHandler SSE path exercised: body=%d bytes", rr.Body.Len())
}

// ---------------------------------------------------------------------------
// gitguard.go: executeCommit — 44.8%
// ---------------------------------------------------------------------------

func TestWave80c_GitGuard_ExecuteCommit_NoBranchLock(t *testing.T) {
	db, err := sql.Open("sqlite3", ":memory:")
	if err != nil {
		t.Fatalf("open db: %v", err)
	}
	defer db.Close()

	_, _ = db.Exec(`CREATE TABLE git_branch_locks (
		task_id TEXT PRIMARY KEY,
		branch TEXT,
		agent_id TEXT,
		created_at TEXT
	)`)

	g := NewGitGuard(db, t.TempDir())
	action := GitAction{
		TaskID:  "wave80c-task-nolock",
		Type:    GitActionCommit,
		Message: "test commit",
	}

	result := g.executeCommit(action)
	if result.Success {
		t.Error("expected failure for commit without branch lock in non-git dir")
	}
	t.Logf("executeCommit no lock: error=%q", result.Error)
}

func TestWave80c_GitGuard_ExecuteCommit_WrongBranch(t *testing.T) {
	db, err := sql.Open("sqlite3", ":memory:")
	if err != nil {
		t.Fatalf("open db: %v", err)
	}
	defer db.Close()

	_, _ = db.Exec(`CREATE TABLE git_branch_locks (
		task_id TEXT PRIMARY KEY,
		branch TEXT,
		agent_id TEXT,
		created_at TEXT
	)`)
	_, _ = db.Exec(
		`INSERT INTO git_branch_locks VALUES ('wave80c-wrong-branch', 'feature/wave80c-test', 'agent', datetime('now'))`,
	)

	// Use real FORGE repo so getCurrentBranch succeeds and returns 'main'
	g := NewGitGuard(db, "/Users/bogdan/work/FORGE")
	action := GitAction{
		TaskID:  "wave80c-wrong-branch",
		Type:    GitActionCommit,
		Message: "test commit",
	}

	result := g.executeCommit(action)
	if result.Success {
		t.Error("expected failure for wrong branch")
	}
	t.Logf("executeCommit wrong branch: error=%q", result.Error)
}

// ---------------------------------------------------------------------------
// gitguard.go: CheckSafe — 75.0%
// ---------------------------------------------------------------------------

func TestWave80c_GitGuard_CheckSafe_WithIndexLock(t *testing.T) {
	tmpDir := t.TempDir()
	gitDir := tmpDir + "/.git"
	os.MkdirAll(gitDir, 0755)
	os.WriteFile(gitDir+"/index.lock", []byte("locked"), 0644)

	db, err := sql.Open("sqlite3", ":memory:")
	if err != nil {
		t.Fatalf("open db: %v", err)
	}
	defer db.Close()

	g := NewGitGuard(db, tmpDir)
	safe, reason := g.CheckSafe()
	if safe {
		t.Error("expected unsafe with index.lock present")
	}
	if !strings.Contains(reason, "index.lock") {
		t.Errorf("expected index.lock in reason, got %q", reason)
	}
}

func TestWave80c_GitGuard_CheckSafe_RebaseInProgress(t *testing.T) {
	tmpDir := t.TempDir()
	gitDir := tmpDir + "/.git"
	os.MkdirAll(gitDir+"/rebase-merge", 0755)

	db, err := sql.Open("sqlite3", ":memory:")
	if err != nil {
		t.Fatalf("open db: %v", err)
	}
	defer db.Close()

	g := NewGitGuard(db, tmpDir)
	safe, reason := g.CheckSafe()
	if safe {
		t.Error("expected unsafe with rebase in progress")
	}
	if !strings.Contains(reason, "rebase") {
		t.Errorf("expected rebase in reason, got %q", reason)
	}
}

// ---------------------------------------------------------------------------
// coordination.go: sendBlockerAlert — 71.4%
// ---------------------------------------------------------------------------

func TestWave80c_SendBlockerAlert_NoWebhookURL(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	old := os.Getenv("FORGE_COORDINATION_BLOCKER_WEBHOOK_URL")
	os.Unsetenv("FORGE_COORDINATION_BLOCKER_WEBHOOK_URL")
	defer os.Setenv("FORGE_COORDINATION_BLOCKER_WEBHOOK_URL", old)

	cd := &CoordinationDashboard{db: db}
	cd.sendBlockerAlert([]Blocker{
		{Agent: "agent-1", Description: "blocked", Severity: "high", Since: time.Now()},
	})
}

func TestWave80c_SendBlockerAlert_WithWebhookURL(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))
	defer server.Close()

	old := os.Getenv("FORGE_COORDINATION_BLOCKER_WEBHOOK_URL")
	os.Setenv("FORGE_COORDINATION_BLOCKER_WEBHOOK_URL", server.URL)
	defer os.Setenv("FORGE_COORDINATION_BLOCKER_WEBHOOK_URL", old)

	cd := &CoordinationDashboard{db: db}
	cd.sendBlockerAlert([]Blocker{
		{Agent: "agent-2", Description: "critical blocker", Severity: "critical", Since: time.Now()},
	})
}

// ---------------------------------------------------------------------------
// coordination.go: GetCoordinationHTML
// ---------------------------------------------------------------------------

func TestWave80c_GetCoordinationHTML_OK(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	cd := &CoordinationDashboard{db: db}
	html := cd.GetCoordinationHTML()
	if html == "" {
		t.Error("expected non-empty coordination HTML")
	}
}

// ---------------------------------------------------------------------------
// gitguard_idempotency.go: GitGuardIdempotency.ExecuteCommit — 72.7%
// ---------------------------------------------------------------------------

func TestWave80c_GitGuardIdempotency_ExecuteCommit_NewAction(t *testing.T) {
	db, err := sql.Open("sqlite3", ":memory:")
	if err != nil {
		t.Fatalf("open db: %v", err)
	}
	defer db.Close()

	_, err = db.Exec(`CREATE TABLE IF NOT EXISTS idempotent_actions (
		id TEXT PRIMARY KEY,
		type TEXT NOT NULL,
		payload_hash TEXT NOT NULL UNIQUE,
		executed_at TEXT DEFAULT (datetime('now')),
		result TEXT
	)`)
	if err != nil {
		t.Fatalf("create table: %v", err)
	}

	store := NewIdempotencyStore(db)
	ggi := NewGitGuardIdempotency(store)

	payload := CommitPayload{
		RepoPath: t.TempDir(),
		Message:  "wave80c test commit",
	}

	called := false
	commitHash, err := ggi.ExecuteCommit(payload, func() (string, error) {
		called = true
		return "abc123def456", nil
	})
	if err != nil {
		t.Fatalf("ExecuteCommit: %v", err)
	}
	if !called {
		t.Error("expected fn to be called for new action")
	}
	if commitHash == "" {
		t.Error("expected non-empty commit hash")
	}
}

func TestWave80c_GitGuardIdempotency_ExecuteCommit_Cached(t *testing.T) {
	db, err := sql.Open("sqlite3", ":memory:")
	if err != nil {
		t.Fatalf("open db: %v", err)
	}
	defer db.Close()

	_, err = db.Exec(`CREATE TABLE IF NOT EXISTS idempotent_actions (
		id TEXT PRIMARY KEY,
		type TEXT NOT NULL,
		payload_hash TEXT NOT NULL UNIQUE,
		executed_at TEXT DEFAULT (datetime('now')),
		result TEXT
	)`)
	if err != nil {
		t.Fatalf("create table: %v", err)
	}

	store := NewIdempotencyStore(db)
	ggi := NewGitGuardIdempotency(store)

	payload := CommitPayload{
		RepoPath: "/tmp/wave80c-cached",
		Message:  "cached commit wave80c",
	}

	callCount := 0
	hash1, err := ggi.ExecuteCommit(payload, func() (string, error) {
		callCount++
		return "firsthash123", nil
	})
	if err != nil {
		t.Fatalf("first ExecuteCommit: %v", err)
	}

	hash2, err := ggi.ExecuteCommit(payload, func() (string, error) {
		callCount++
		return "secondhash456", nil
	})
	if err != nil {
		t.Fatalf("second ExecuteCommit: %v", err)
	}

	if callCount != 1 {
		t.Errorf("expected fn called once, got %d times", callCount)
	}
	if hash1 != hash2 {
		t.Errorf("expected same hash: %s != %s", hash1, hash2)
	}
}

// ---------------------------------------------------------------------------
// handlers_task.go: claimTaskHandler context gate — 68.4%
// ---------------------------------------------------------------------------

func TestWave80c_ClaimTask_ContextFull(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	db := getDBConn()
	ctx := context.Background()

	_ = taskQueue.Enqueue(ctx, Task{
		ID:       "wave80c-claim-ctx",
		Domain:   "test",
		Project:  "p",
		Type:     TaskTypeFeature,
		Priority: 5,
		Status:   TaskStatusQueued,
		State:    StateQueued,
	})

	now := time.Now().Format(time.RFC3339)
	_, _ = db.Exec(
		`INSERT OR REPLACE INTO agent_heartbeats (agent_id, node, status, context_pct, last_seen, connected_at) VALUES (?,?,?,?,?,?)`,
		"wave80c-ctx-agent", "prya", "idle", 95.0, now, now,
	)

	body := `{"agent_id":"wave80c-ctx-agent"}`
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/wave80c-claim-ctx/claim", bytes.NewBufferString(body))
	req.Header.Set("Content-Type", "application/json")
	rr := httptest.NewRecorder()
	claimTaskHandler(rr, req)

	t.Logf("claimTask context gate: got %d body=%s", rr.Code, rr.Body.String())
}

// ---------------------------------------------------------------------------
// main.go: handleQueueStatus — 70.6%
// ---------------------------------------------------------------------------

func TestWave80c_HandleQueueStatus_OK(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	ctx := context.Background()
	for _, status := range []TaskStatus{TaskStatusQueued, TaskStatusAssigned, TaskStatusCompleted} {
		id := fmt.Sprintf("wave80c-qs-%s", status)
		_ = taskQueue.Enqueue(ctx, Task{
			ID:       id,
			Domain:   "test",
			Project:  "p",
			Type:     TaskTypeFeature,
			Priority: 5,
			Status:   TaskStatusQueued,
			State:    StateQueued,
		})
		db := getDBConn()
		now := time.Now().Format(time.RFC3339)
		_, _ = db.Exec(`UPDATE tasks SET status=?, updated_at=? WHERE id=?`, status, now, id)
	}

	req := httptest.NewRequest(http.MethodGet, "/api/queue/status", nil)
	rr := httptest.NewRecorder()
	handleQueueStatus(rr, req)
	if rr.Code != http.StatusOK {
		t.Errorf("expected 200, got %d body=%s", rr.Code, rr.Body.String())
	}
}

func TestWave80c_HandleQueueStatus_FormatTable(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/queue/status?format=table", nil)
	rr := httptest.NewRecorder()
	handleQueueStatus(rr, req)
	if rr.Code != http.StatusOK {
		t.Errorf("expected 200, got %d body=%s", rr.Code, rr.Body.String())
	}
}
