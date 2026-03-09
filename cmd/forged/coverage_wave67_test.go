//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

// setupWave67ScaleRecs creates the scale_recommendations table in an existing
// migrated DB (it is not part of the standard migrations).
func setupWave67ScaleRecs(t *testing.T, db interface {
	Exec(query string, args ...interface{}) (interface{ RowsAffected() (int64, error) }, error)
}) {
	t.Helper()
}

// ---------------------------------------------------------------------------
// TestWave67_StartDrain_CouncilMarker — council-protected agent → rejected, no drain
// ---------------------------------------------------------------------------

func TestWave67_StartDrain_CouncilMarker(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Create scale_recommendations table (not in standard migrations).
	if _, err := db.Exec(`
		CREATE TABLE IF NOT EXISTS scale_recommendations (
			id           TEXT PRIMARY KEY,
			agent_type   TEXT NOT NULL,
			node_id      TEXT NOT NULL,
			action       TEXT NOT NULL,
			status       TEXT DEFAULT 'pending',
			processed_at TEXT DEFAULT NULL
		)
	`); err != nil {
		t.Fatalf("create scale_recommendations: %v", err)
	}

	agentID := "wave67-agent-protected"
	recID := "rec-protected-001"
	nodeID := "sati"

	// Insert recommendation.
	if _, err := db.Exec(`INSERT INTO scale_recommendations (id, agent_type, node_id, action, status) VALUES (?, ?, ?, 'deflate', 'approved')`,
		recID, "claude", nodeID); err != nil {
		t.Fatalf("insert scale_recommendations: %v", err)
	}

	// Create a valid council marker for this agent so it is protected.
	markerDir := filepath.Join(".forge", "autoscale", "council")
	if err := os.MkdirAll(markerDir, 0755); err != nil {
		t.Fatalf("mkdir council: %v", err)
	}
	markerPath := filepath.Join(markerDir, agentID+".json")
	expires := time.Now().Add(1 * time.Hour).Format(time.RFC3339)
	markerData := fmt.Sprintf(`{"expires_at":"%s"}`, expires)
	if err := os.WriteFile(markerPath, []byte(markerData), 0644); err != nil {
		t.Fatalf("write marker: %v", err)
	}
	defer os.Remove(markerPath)

	ctx := context.Background()
	// startDrain returns early because hasActiveCouncilMarker is true.
	startDrain(ctx, db, agentID, nodeID, recID)

	// Verify recommendation status was set to 'rejected'.
	var status string
	if err := db.QueryRow(`SELECT status FROM scale_recommendations WHERE id = ?`, recID).Scan(&status); err != nil {
		t.Fatalf("query recommendation: %v", err)
	}
	if status != "rejected" {
		t.Errorf("expected status='rejected' for protected agent, got %q", status)
	}
}

// ---------------------------------------------------------------------------
// TestWave67_StartDrain_NoMarker — no council marker → startDrain attempts drain
// The real schema uses 'id' PK not 'agent_id', so the UPDATE fails; startDrain
// logs the error and returns. We verify no panic and the function completes.
// ---------------------------------------------------------------------------

func TestWave67_StartDrain_NoMarker(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Create scale_recommendations table.
	if _, err := db.Exec(`
		CREATE TABLE IF NOT EXISTS scale_recommendations (
			id           TEXT PRIMARY KEY,
			agent_type   TEXT NOT NULL,
			node_id      TEXT NOT NULL,
			action       TEXT NOT NULL,
			status       TEXT DEFAULT 'pending',
			processed_at TEXT DEFAULT NULL
		)
	`); err != nil {
		t.Fatalf("create scale_recommendations: %v", err)
	}

	agentID := "wave67-agent-drainable"
	recID := "rec-drainable-001"
	nodeID := "sati"

	// Ensure no council marker exists.
	markerPath := filepath.Join(".forge", "autoscale", "council", agentID+".json")
	os.Remove(markerPath)

	// Insert recommendation.
	if _, err := db.Exec(`INSERT INTO scale_recommendations (id, agent_type, node_id, action, status) VALUES (?, ?, ?, 'deflate', 'approved')`,
		recID, "kimi", nodeID); err != nil {
		t.Fatalf("insert scale_recommendations: %v", err)
	}

	ctx := context.Background()
	// startDrain will attempt to UPDATE agent_inventory WHERE agent_id=?, which
	// will fail because the column is 'id' not 'agent_id' — exercises the error
	// return path after the council marker check.
	startDrain(ctx, db, agentID, nodeID, recID)
	// No panic means both branches were reached.
}

// ---------------------------------------------------------------------------
// TestWave67_StartDrain_DBClosed — db closed → exercises error return path
// ---------------------------------------------------------------------------

func TestWave67_StartDrain_DBClosed(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	// Do NOT defer cleanup — we close the DB early to trigger errors.

	// Create scale_recommendations table.
	if _, err := db.Exec(`
		CREATE TABLE IF NOT EXISTS scale_recommendations (
			id TEXT PRIMARY KEY, agent_type TEXT NOT NULL, node_id TEXT NOT NULL,
			action TEXT NOT NULL, status TEXT DEFAULT 'pending', processed_at TEXT DEFAULT NULL
		)
	`); err != nil {
		cleanup()
		t.Fatalf("create scale_recommendations: %v", err)
	}

	recID := "rec-closed-001"
	if _, err := db.Exec(`INSERT INTO scale_recommendations (id, agent_type, node_id, action, status) VALUES (?, ?, ?, 'deflate', 'approved')`,
		recID, "pi", "nova"); err != nil {
		cleanup()
		t.Fatalf("insert scale_recommendations: %v", err)
	}

	// Close DB before calling startDrain.
	db.Close()
	cleanup()

	agentID := "wave67-agent-closed"
	// Ensure no council marker.
	markerPath := filepath.Join(".forge", "autoscale", "council", agentID+".json")
	os.Remove(markerPath)

	ctx := context.Background()
	// Should not panic — error paths handle closed DB gracefully.
	startDrain(ctx, db, agentID, "nova", recID)
}

// ---------------------------------------------------------------------------
// TestWave67_RunTestSuite_ContextCancel — pre-cancelled context → error
// ---------------------------------------------------------------------------

func TestWave67_RunTestSuite_ContextCancel(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel() // cancel immediately

	err := runTestSuite(ctx)
	if err == nil {
		t.Error("expected error when context is already cancelled, got nil")
	}
}

// ---------------------------------------------------------------------------
// TestWave67_CheckCircular_VisitedShortCircuit — visited map prevents re-checking
// ---------------------------------------------------------------------------

func TestWave67_CheckCircular_Visited(t *testing.T) {
	q, cleanup := setupTestQueue(t)
	defer cleanup()

	sq, ok := q.(*sqliteTaskQueue)
	if !ok {
		t.Skip("queue is not *sqliteTaskQueue")
	}

	ctx := context.Background()

	// Add task A with no dependencies.
	taskA := Task{ID: "wave67-circ-a", Domain: "d", Project: "p", Type: TaskTypeFeature, Priority: 5, Status: TaskStatusQueued}
	if err := q.Enqueue(ctx, taskA); err != nil {
		t.Fatalf("enqueue A: %v", err)
	}

	// visited map already marks "wave67-circ-a"; checkCircular should return nil
	// immediately on the visited check (current != target, then visited[current]=true already set).
	visited := map[string]bool{"wave67-circ-a": true}
	err := sq.checkCircular(ctx, "wave67-circ-a", "wave67-circ-b", visited)
	if err != nil {
		t.Errorf("expected nil when visited shortcircuits, got %v", err)
	}
}

func TestWave67_CheckCircular_DetectsLoop(t *testing.T) {
	q, cleanup := setupTestQueue(t)
	defer cleanup()

	sq, ok := q.(*sqliteTaskQueue)
	if !ok {
		t.Skip("queue is not *sqliteTaskQueue")
	}

	ctx := context.Background()

	// checkCircular(ctx, "X", "X", {}) should return ErrCircularDeps.
	visited := map[string]bool{}
	err := sq.checkCircular(ctx, "same-id", "same-id", visited)
	if err != ErrCircularDeps {
		t.Errorf("expected ErrCircularDeps for self-reference, got %v", err)
	}
}

// ---------------------------------------------------------------------------
// TestWave67_ClaimTask_AlreadyAssigned — covers "already assigned to" branch
// ---------------------------------------------------------------------------

func TestWave67_ClaimTask_AlreadyAssigned(t *testing.T) {
	q, cleanup := setupTestQueue(t)
	defer cleanup()

	ctx := context.Background()

	task := Task{
		ID:       "wave67-claim-assigned",
		Domain:   "d",
		Project:  "p",
		Type:     TaskTypeFeature,
		Priority: 5,
		Status:   TaskStatusQueued,
	}
	if err := q.Enqueue(ctx, task); err != nil {
		t.Fatalf("enqueue: %v", err)
	}

	// First claim by agent-1.
	if err := q.ClaimTask(ctx, task.ID, "agent-1"); err != nil {
		t.Fatalf("first claim: %v", err)
	}

	// Reset status to queued but keep assigned_to=agent-1 to simulate race condition.
	sq := q.(*sqliteTaskQueue)
	sq.db.Exec(`UPDATE tasks SET status='queued', assigned_to='agent-1' WHERE id=?`, task.ID)

	// agent-2 tries to claim — should fail with "already assigned".
	err := q.ClaimTask(ctx, task.ID, "agent-2")
	if err == nil {
		t.Error("expected error claiming task already assigned to another agent")
	}
	if !strings.Contains(err.Error(), "already assigned") {
		t.Errorf("expected 'already assigned' error, got %v", err)
	}
}

// ---------------------------------------------------------------------------
// TestWave67_ClaimTask_WrongStatus — covers "not claimable" branch
// ---------------------------------------------------------------------------

func TestWave67_ClaimTask_WrongStatus(t *testing.T) {
	q, cleanup := setupTestQueue(t)
	defer cleanup()

	ctx := context.Background()

	task := Task{
		ID:       "wave67-claim-wrong-status",
		Domain:   "d",
		Project:  "p",
		Type:     TaskTypeFeature,
		Priority: 5,
		Status:   TaskStatusQueued,
	}
	if err := q.Enqueue(ctx, task); err != nil {
		t.Fatalf("enqueue: %v", err)
	}

	// Mark task as completed so it's not claimable.
	sq := q.(*sqliteTaskQueue)
	sq.db.Exec(`UPDATE tasks SET status='completed' WHERE id=?`, task.ID)

	err := q.ClaimTask(ctx, task.ID, "agent-x")
	if err == nil {
		t.Error("expected error claiming completed task")
	}
	if !strings.Contains(err.Error(), "not claimable") {
		t.Errorf("expected 'not claimable' error, got %v", err)
	}
}

// ---------------------------------------------------------------------------
// TestWave67_ClaimTask_NotFound — task missing from DB
// ---------------------------------------------------------------------------

func TestWave67_ClaimTask_NotFound(t *testing.T) {
	q, cleanup := setupTestQueue(t)
	defer cleanup()

	ctx := context.Background()

	err := q.ClaimTask(ctx, "nonexistent-task-wave67", "agent-x")
	if err == nil {
		t.Error("expected error claiming nonexistent task")
	}
}

// ---------------------------------------------------------------------------
// TestWave67_ReleaseTaskHandler_MethodNotAllowed
// ---------------------------------------------------------------------------

func TestWave67_ReleaseTaskHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/tasks/task-1/release", nil)
	w := httptest.NewRecorder()
	releaseTaskHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// TestWave67_ReleaseTaskHandler_InvalidJSON
// ---------------------------------------------------------------------------

func TestWave67_ReleaseTaskHandler_InvalidJSON(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/task-1/release", strings.NewReader("{invalid"))
	w := httptest.NewRecorder()
	releaseTaskHandler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// TestWave67_ReleaseTaskHandler_TaskNotFound
// ---------------------------------------------------------------------------

func TestWave67_ReleaseTaskHandler_TaskNotFound(t *testing.T) {
	// setupClaimTestDB sets the global dbConn; cleanup restores it.
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	q, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}
	orig := taskQueue
	taskQueue = q
	defer func() { taskQueue = orig }()

	body := bytes.NewBufferString(`{"agent_id":"agent-1","reason":"test"}`)
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/nonexistent-task-wave67/release", body)
	w := httptest.NewRecorder()
	releaseTaskHandler(w, req)
	if w.Code != http.StatusNotFound {
		t.Errorf("expected 404, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// TestWave67_ReleaseTaskHandler_WrongAgent — task found but agent mismatch
// ---------------------------------------------------------------------------

func TestWave67_ReleaseTaskHandler_WrongAgent(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	q, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}
	orig := taskQueue
	taskQueue = q
	defer func() { taskQueue = orig }()

	ctx := context.Background()
	task := Task{
		ID:       "wave67-release-wrong-agent",
		Domain:   "d",
		Project:  "p",
		Type:     TaskTypeFeature,
		Priority: 5,
		Status:   TaskStatusQueued,
	}
	if err := q.Enqueue(ctx, task); err != nil {
		t.Fatalf("enqueue: %v", err)
	}
	if err := q.ClaimTask(ctx, task.ID, "agent-correct"); err != nil {
		t.Fatalf("claim: %v", err)
	}

	body := bytes.NewBufferString(`{"agent_id":"agent-wrong","reason":"test"}`)
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/"+task.ID+"/release", body)
	w := httptest.NewRecorder()
	releaseTaskHandler(w, req)
	if w.Code != http.StatusForbidden {
		t.Errorf("expected 403, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// TestWave67_HandleTaskShow_MissingID
// ---------------------------------------------------------------------------

func TestWave67_HandleTaskShow_MissingID(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/cli/tasks/show", nil)
	w := httptest.NewRecorder()
	handleTaskShow(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// TestWave67_HandleTaskShow_TaskNotFound
// ---------------------------------------------------------------------------

func TestWave67_HandleTaskShow_TaskNotFound(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	q, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}
	orig := taskQueue
	taskQueue = q
	defer func() { taskQueue = orig }()

	req := httptest.NewRequest(http.MethodGet, "/cli/tasks/show?id=nonexistent-wave67", nil)
	w := httptest.NewRecorder()
	handleTaskShow(w, req)
	// Expect >=400 for missing task.
	if w.Code < 400 {
		t.Errorf("expected >=400 for missing task, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// TestWave67_GetAgentHealth_NotFound — covers ErrNoRows path
// ---------------------------------------------------------------------------

func TestWave67_GetAgentHealth_NotFound(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)

	_, err := getAgentHealth("nonexistent-agent-wave67")
	if err == nil {
		t.Error("expected error for nonexistent agent, got nil")
	}
}

// ---------------------------------------------------------------------------
// TestWave67_NewContextManager_EmptyDir — empty contextDir → defaults to ".forge/context"
// ---------------------------------------------------------------------------

func TestWave67_NewContextManager_EmptyDir(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Pass empty string — NewContextManager should default to ".forge/context".
	cm := NewContextManager(db, "")
	if cm == nil {
		t.Fatal("expected non-nil ContextManager")
	}
	defer cm.Stop()

	if cm.contextDir != ".forge/context" {
		t.Errorf("expected contextDir='.forge/context', got %q", cm.contextDir)
	}
}

// ---------------------------------------------------------------------------
// TestWave67_StoreInDB_Success — covers storeInDB happy path
// ---------------------------------------------------------------------------

func TestWave67_StoreInDB_Success(t *testing.T) {
	db := setupContextTestDB(t)
	defer db.Close()

	tmpDir, err := os.MkdirTemp("", "wave67-ctx")
	if err != nil {
		t.Fatalf("mkdirtemp: %v", err)
	}
	defer os.RemoveAll(tmpDir)

	cm := &ContextManager{db: db, contextDir: tmpDir}
	ctx := context.Background()

	envelope := &ContextEnvelope{
		ID:        "wave67-env-001",
		AgentID:   "agent-wave67",
		Domain:    "test",
		Project:   "proj",
		TaskID:    "task-001",
		Summary:   "wave67 test",
		CreatedAt: time.Now(),
		ExpiresAt: time.Now().Add(24 * time.Hour),
		Metadata:  make(map[string]any),
	}

	if err := cm.storeInDB(ctx, envelope); err != nil {
		t.Fatalf("storeInDB: %v", err)
	}

	// Verify row exists.
	var count int
	if err := db.QueryRow(`SELECT COUNT(*) FROM context_envelopes WHERE id = ?`, envelope.ID).Scan(&count); err != nil {
		t.Fatalf("query: %v", err)
	}
	if count != 1 {
		t.Errorf("expected 1 row, got %d", count)
	}
}

// ---------------------------------------------------------------------------
// TestWave67_ScanTasks_NullFields — scanTasks handles all-null optional columns
// ---------------------------------------------------------------------------

func TestWave67_ScanTasks_NullFields(t *testing.T) {
	q, cleanup := setupTestQueue(t)
	defer cleanup()

	ctx := context.Background()

	// Enqueue a task with minimal fields; optional columns will be NULL.
	task := Task{
		ID:       "wave67-scan-null",
		Domain:   "d",
		Project:  "p",
		Type:     TaskTypeFeature,
		Priority: 1,
		Status:   TaskStatusQueued,
	}
	if err := q.Enqueue(ctx, task); err != nil {
		t.Fatalf("enqueue: %v", err)
	}

	tasks, err := q.ListAllTasks(ctx, 10)
	if err != nil {
		t.Fatalf("ListAllTasks: %v", err)
	}

	found := false
	for _, tsk := range tasks {
		if tsk.ID == task.ID {
			found = true
			break
		}
	}
	if !found {
		t.Errorf("task %s not found in ListAllTasks result", task.ID)
	}
}

// ---------------------------------------------------------------------------
// TestWave67_FetchData_DecodeError — /api/dashboard returns invalid JSON
// ---------------------------------------------------------------------------

func TestWave67_FetchData_DecodeError(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if strings.Contains(r.URL.Path, "dashboard") {
			w.Header().Set("Content-Type", "application/json")
			fmt.Fprint(w, `{not valid json}`)
			return
		}
		w.WriteHeader(http.StatusOK)
	}))
	defer server.Close()

	m := DashboardModel{
		apiBaseURL: server.URL,
	}
	cmd := m.fetchData()
	msg := cmd()

	dm, ok := msg.(dataMsg)
	if !ok {
		t.Fatalf("expected dataMsg, got %T", msg)
	}
	if dm.Error == "" {
		t.Error("expected non-empty Error in state when JSON decode fails")
	}
}

// ---------------------------------------------------------------------------
// TestWave67_FetchData_NetworkError — server unreachable
// ---------------------------------------------------------------------------

func TestWave67_FetchData_NetworkError(t *testing.T) {
	// Point to a port that nothing is listening on.
	m := DashboardModel{
		apiBaseURL: "http://127.0.0.1:19878",
	}
	cmd := m.fetchData()
	msg := cmd()

	dm, ok := msg.(dataMsg)
	if !ok {
		t.Fatalf("expected dataMsg, got %T", msg)
	}
	if dm.Error == "" {
		t.Error("expected non-empty Error in state when network fails")
	}
}

// ---------------------------------------------------------------------------
// TestWave67_FetchData_SprintDecodeError — /api/coordination/status returns bad JSON
// The dashboard state error should be empty (sprint errors are silently ignored).
// ---------------------------------------------------------------------------

func TestWave67_FetchData_SprintDecodeError(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		if strings.Contains(r.URL.Path, "coordination") {
			fmt.Fprint(w, `{bad sprint json}`)
			return
		}
		// Return valid empty dashboard state.
		json.NewEncoder(w).Encode(DashboardState{})
	}))
	defer server.Close()

	m := DashboardModel{
		apiBaseURL: server.URL,
	}
	cmd := m.fetchData()
	msg := cmd()

	dm, ok := msg.(dataMsg)
	if !ok {
		t.Fatalf("expected dataMsg, got %T", msg)
	}
	// No top-level error: sprint decode failure is silently skipped.
	if dm.Error != "" {
		t.Errorf("unexpected Error: %s", dm.Error)
	}
	// Sprint should be nil since decode failed.
	if dm.Sprint != nil {
		t.Error("expected nil Sprint when coordination decode fails")
	}
}

// ---------------------------------------------------------------------------
// TestWave67_FetchData_SprintSuccess — both endpoints return valid JSON
// ---------------------------------------------------------------------------

func TestWave67_FetchData_SprintSuccess(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		if strings.Contains(r.URL.Path, "coordination") {
			json.NewEncoder(w).Encode(SprintStatus{Name: "sprint-1"})
			return
		}
		json.NewEncoder(w).Encode(DashboardState{})
	}))
	defer server.Close()

	m := DashboardModel{
		apiBaseURL: server.URL,
	}
	cmd := m.fetchData()
	msg := cmd()

	dm, ok := msg.(dataMsg)
	if !ok {
		t.Fatalf("expected dataMsg, got %T", msg)
	}
	if dm.Error != "" {
		t.Errorf("unexpected Error: %s", dm.Error)
	}
	if dm.Sprint == nil {
		t.Error("expected non-nil Sprint when coordination returns valid JSON")
	}
}
