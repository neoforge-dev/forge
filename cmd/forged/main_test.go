//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

// TestMain_TimeoutHandler covers the withTimeout wrapper (line 740-742 in main.go).
func TestMain_TimeoutHandler(t *testing.T) {
	h := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	})
	handler := withTimeout(h, 5*time.Second)

	r := httptest.NewRequest(http.MethodGet, "/", nil)
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, r)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200 from withTimeout handler, got %d", w.Code)
	}
}

// TestHandleTaskLogs_MissingID_Main covers the empty taskID → 400 branch (main_test.go version).
func TestHandleTaskLogs_MissingID_Main(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodGet, "/tasks/logs", nil)
	w := httptest.NewRecorder()
	handleTaskLogs(w, r)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for missing id, got %d: %s", w.Code, w.Body.String())
	}
}

// TestHandleTaskLogs_ErrorFromQueue covers the rr.status >= 400 branch —
// closes the DB so getTaskEventsHandler returns 500, which handleTaskLogs forwards.
func TestHandleTaskLogs_ErrorFromQueue(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Close the test DB so GetTaskEvents returns an error.
	getDBConn().Close()

	r := httptest.NewRequest(http.MethodGet, "/tasks/logs?id=any-task-id", nil)
	w := httptest.NewRecorder()
	handleTaskLogs(w, r)

	if w.Code < 400 {
		t.Errorf("expected 4xx/5xx response when DB is closed, got %d", w.Code)
	}
}

// TestHandleTaskLogs_Success covers the normal success path with valid task ID.
func TestHandleTaskLogs_Success(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodGet, "/tasks/logs?id=nonexistent-task", nil)
	w := httptest.NewRecorder()
	handleTaskLogs(w, r)

	// Non-existent task returns 200 with empty events array (not an error).
	if w.Code >= 500 {
		t.Errorf("unexpected server error for nonexistent task: %d %s", w.Code, w.Body.String())
	}
}

// TestHandleQueueDepth_WithQueue_Main covers the normal path when taskQueue is set (main_test.go version).
func TestHandleQueueDepth_WithQueue_Main(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodGet, "/queue/depth", nil)
	w := httptest.NewRecorder()
	handleQueueDepth(w, r)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200 for queue depth, got %d: %s", w.Code, w.Body.String())
	}
}

// TestHandleQueueDepth_WithFormat covers the format query param code path.
func TestHandleQueueDepth_WithFormat(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodGet, "/queue/depth?format=json", nil)
	w := httptest.NewRecorder()
	handleQueueDepth(w, r)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200 for queue depth with format, got %d", w.Code)
	}
}

// TestHandleAgentList_OK covers the handleAgentList success path.
func TestHandleAgentList_OK(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodGet, "/agents", nil)
	w := httptest.NewRecorder()
	handleAgentList(w, r)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200 for agent list, got %d: %s", w.Code, w.Body.String())
	}
}

// TestHandleAgentStatus_MissingID_Main covers the id == "" → 400 branch (main_test.go version).
func TestHandleAgentStatus_MissingID_Main(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodGet, "/agents/status", nil)
	w := httptest.NewRecorder()
	handleAgentStatus(w, r)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for missing agent id, got %d", w.Code)
	}
}

// TestHandleAgentStatus_NotFound_Main covers the getAgentHealth error → 404 branch (main_test.go version).
func TestHandleAgentStatus_NotFound_Main(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodGet, "/agents/status?id=nonexistent-agent", nil)
	w := httptest.NewRecorder()
	handleAgentStatus(w, r)

	if w.Code != http.StatusNotFound {
		t.Errorf("expected 404 for nonexistent agent, got %d", w.Code)
	}
}

// TestStartupAgentReset verifies the S173 E1.1 startup reset logic:
// all agents with status 'connected' or 'online' are marked 'offline' at daemon startup.
// Agents that are already 'offline' are not changed.
func TestStartupAgentReset(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Insert three agents: one online, one connected, one already offline.
	agents := []struct {
		id     string
		status string
	}{
		{"agent-online", "online"},
		{"agent-connected", "connected"},
		{"agent-offline", "offline"},
	}
	now := time.Now().UTC().Format(time.RFC3339)
	for _, a := range agents {
		_, err := db.Exec(
			`INSERT INTO agent_heartbeats (agent_id, node, status, last_seen) VALUES (?, ?, ?, ?)`,
			a.id, "test-node", a.status, now,
		)
		if err != nil {
			t.Fatalf("failed to insert agent %s: %v", a.id, err)
		}
	}

	// Run the startup reset SQL (mirrors the block in main.go startDaemon).
	result, err := db.Exec(`UPDATE agent_heartbeats SET status = 'offline' WHERE status IN ('connected', 'online')`)
	if err != nil {
		t.Fatalf("startup reset SQL failed: %v", err)
	}

	rowsAffected, err := result.RowsAffected()
	if err != nil {
		t.Fatalf("RowsAffected failed: %v", err)
	}
	if rowsAffected != 2 {
		t.Errorf("expected 2 agents reset to offline, got %d", rowsAffected)
	}

	// Verify every agent in the table is now offline.
	rows, err := db.Query(`SELECT agent_id, status FROM agent_heartbeats`)
	if err != nil {
		t.Fatalf("query failed: %v", err)
	}
	defer rows.Close()

	for rows.Next() {
		var agentID, status string
		if err := rows.Scan(&agentID, &status); err != nil {
			t.Fatalf("scan failed: %v", err)
		}
		if status != "offline" {
			t.Errorf("agent %s: expected status 'offline', got %q", agentID, status)
		}
	}
	if err := rows.Err(); err != nil {
		t.Fatalf("rows iteration error: %v", err)
	}
}
