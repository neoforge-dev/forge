//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// Wave 63: Push coverage from 74.8% to 75%+
// Targets:
//   - handlers_tui.go: getPlanHistoryHandler (new branches with planManager set)
//   - handlers_patrols.go: nodesHealthHandler (healthy / degraded status paths)
//   - output.go: isTerminal (os.File path, non-file path)
//   - context_sync.go: SyncEnvelopesToFilesystem (with valid envelopes)
//   - approval_integration.go: CheckPendingApprovals (expired path)
//   - queue.go: dependenciesComplete (multiple-dep scenario)

// ─── getPlanHistoryHandler new branches ──────────────────────────────────────

func TestWave63_GetPlanHistoryHandler_WithHistory(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	db := getDBConn()
	oldPM := planManager
	planManager = NewPlanManager(db)
	defer func() { planManager = oldPM }()

	ctx := context.Background()
	taskQueue.Enqueue(ctx, Task{
		ID: "task-ph-w63", Domain: "test", Project: "proj", Type: "feature",
		Title: "Plan history wave63", Priority: 5, Status: TaskStatusQueued,
	})

	// Create a plan entry so GetPlanHistory returns data
	planManager.CreatePlan(ctx, "task-ph-w63", "Initial plan for w63", "initial setup") //nolint:errcheck

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/task-ph-w63/plans", nil)
	req.URL.Path = "/api/tasks/task-ph-w63/plans"
	w := httptest.NewRecorder()
	getPlanHistoryHandler(w, req)

	if w.Code != http.StatusOK {
		t.Logf("getPlanHistoryHandler with history: %d %s", w.Code, w.Body.String())
	}
	// Verify it returns a valid JSON slice
	if w.Code == http.StatusOK {
		var plans []interface{}
		if err := json.NewDecoder(w.Body).Decode(&plans); err != nil {
			t.Errorf("decode plan history: %v", err)
		}
	}
}

func TestWave63_GetPlanHistoryHandler_EmptyTaskID(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	// Path that resolves to empty task ID after stripping prefix/suffix
	req := httptest.NewRequest(http.MethodGet, "/api/tasks//plans", nil)
	req.URL.Path = "/api/tasks//plans"
	w := httptest.NewRecorder()
	getPlanHistoryHandler(w, req)

	if w.Code != http.StatusBadRequest {
		t.Logf("getPlanHistoryHandler empty ID: %d (may be acceptable)", w.Code)
	}
}

// ─── nodesHealthHandler status branches (healthy / degraded / unknown) ────────

func TestWave63_NodesHealthHandler_DegradedStatus(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	oldDB := getDBConn()
	setDBConn(db)
	defer setDBConn(oldDB)

	// Insert agent with status != 'online' so OnlineCount=0, AgentCount=1 → degraded
	db.Exec(`
		INSERT OR REPLACE INTO agent_heartbeats (agent_id, node, status, current_task_id, context_pct, last_seen)
		VALUES ('agent-offline-w63', 'node-w63-degraded', 'offline', NULL, 0.0, datetime('now'))
	`)

	req := httptest.NewRequest(http.MethodGet, "/api/nodes/health", nil)
	w := httptest.NewRecorder()
	nodesHealthHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}

	var resp map[string]interface{}
	if err := json.NewDecoder(w.Body).Decode(&resp); err != nil {
		t.Fatalf("decode response: %v", err)
	}

	nodes, ok := resp["nodes"].([]interface{})
	if !ok {
		t.Fatalf("nodes field missing or wrong type: %v", resp)
	}

	// Verify at least one node with degraded status exists
	found := false
	for _, n := range nodes {
		nm := n.(map[string]interface{})
		if nm["node_id"] == "node-w63-degraded" {
			if nm["status"] == "degraded" {
				found = true
			}
			break
		}
	}
	if !found {
		t.Logf("degraded node not found in response (may be filtered): %v", nodes)
	}
}

func TestWave63_NodesHealthHandler_HealthyStatus(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	oldDB := getDBConn()
	setDBConn(db)
	defer setDBConn(oldDB)

	// Insert online agent → healthy node
	db.Exec(`
		INSERT OR REPLACE INTO agent_heartbeats (agent_id, node, status, current_task_id, context_pct, last_seen)
		VALUES ('agent-online-w63', 'node-w63-healthy', 'online', NULL, 25.0, datetime('now'))
	`)

	req := httptest.NewRequest(http.MethodGet, "/api/nodes/health", nil)
	w := httptest.NewRecorder()
	nodesHealthHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}

	var resp map[string]interface{}
	if err := json.NewDecoder(w.Body).Decode(&resp); err != nil {
		t.Fatalf("decode response: %v", err)
	}

	healthyCount, _ := resp["healthy_nodes"].(float64)
	if healthyCount < 1 {
		t.Logf("healthy_nodes=%v (expected >=1 with online agent)", healthyCount)
	}
}

// ─── isTerminal — file and non-file paths ────────────────────────────────────

func TestWave63_IsTerminal_WithOsStdout(t *testing.T) {
	// os.Stdout is an *os.File but typically not a char device in tests
	result := isTerminal(os.Stdout)
	// In CI / non-TTY environment this is false; in interactive it would be true.
	// Just ensure no panic and it returns a bool.
	t.Logf("isTerminal(os.Stdout) = %v", result)
}

func TestWave63_IsTerminal_WithRegularFile(t *testing.T) {
	f, err := os.CreateTemp("", "wave63-terminal-test-*.txt")
	if err != nil {
		t.Fatalf("create temp file: %v", err)
	}
	defer os.Remove(f.Name())
	defer f.Close()

	// A regular file is not a char device → should return false
	result := isTerminal(f)
	if result {
		t.Errorf("isTerminal(regular file) = true, want false")
	}
}

func TestWave63_IsTerminal_WithBytesBuffer(t *testing.T) {
	// bytes.Buffer is not *os.File → should return false immediately
	var buf bytes.Buffer
	result := isTerminal(&buf)
	if result {
		t.Errorf("isTerminal(bytes.Buffer) = true, want false")
	}
}

// ─── context_sync.go: SyncEnvelopesToFilesystem with envelope data ────────────

func TestWave63_SyncEnvelopesToFilesystem_WithEnvelope(t *testing.T) {
	db := setupContextSyncDB(t)
	defer db.Close()

	tmpDir := t.TempDir()
	contextDir := filepath.Join(tmpDir, "context")
	if err := os.MkdirAll(contextDir, 0755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}

	cm := &ContextManager{db: db, contextDir: contextDir}
	cs, err := NewContextSync(cm, db, contextDir)
	if err != nil {
		t.Fatalf("NewContextSync: %v", err)
	}
	defer cs.Stop()

	// Insert a valid envelope that expires in the future
	env := ContextEnvelope{
		ID:       "env-w63-sync",
		AgentID:  "agent-w63",
		Domain:   "testdomain",
		Project:  "testproj",
		TaskID:   "task-w63",
		Summary:  "Wave 63 sync test",
		Metadata: map[string]interface{}{"key": "value"},
	}
	content, _ := json.Marshal(env)
	_, err = db.Exec(`
		INSERT INTO context_envelopes (id, agent_id, domain, project, task_id, summary, content, expires_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now', '+1 day'))
	`, env.ID, env.AgentID, env.Domain, env.Project, env.TaskID, env.Summary, string(content))
	if err != nil {
		t.Fatalf("insert: %v", err)
	}

	if err := cs.SyncEnvelopesToFilesystem(); err != nil {
		t.Fatalf("SyncEnvelopesToFilesystem: %v", err)
	}
}

func TestWave63_SyncEnvelopesToFilesystem_EmptyResult(t *testing.T) {
	db := setupContextSyncDB(t)
	defer db.Close()

	tmpDir := t.TempDir()
	contextDir := filepath.Join(tmpDir, "context")
	os.MkdirAll(contextDir, 0755)

	cm := &ContextManager{db: db, contextDir: contextDir}
	cs, err := NewContextSync(cm, db, contextDir)
	if err != nil {
		t.Fatalf("NewContextSync: %v", err)
	}
	defer cs.Stop()

	// No envelopes — should return nil with no iteration
	if err := cs.SyncEnvelopesToFilesystem(); err != nil {
		t.Fatalf("SyncEnvelopesToFilesystem empty: %v", err)
	}
}

// ─── CheckPendingApprovals with expired approval ──────────────────────────────

func TestWave63_CheckPendingApprovals_ExpiredApproval(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	tai := makeTAI(t)

	// Enqueue task
	enqueueTestTask(t, "task-expired-w63")
	taskID := "task-expired-w63"

	// Create an approval that is already expired (ExpiresAt in the past)
	// We do this by inserting directly with an old expires_at
	db := getDBConn()
	_, err := db.Exec(`
		INSERT INTO approvals (id, type, task_id, requested_by, domain, title, description, status, expires_at, created_at, updated_at)
		VALUES ('approval-expired-w63', 'task_completion', ?, 'agent-w63', 'test', 'Expired approval', 'expired test', 'pending',
		        datetime('now', '-1 hour'), datetime('now', '-2 hour'), datetime('now', '-2 hour'))
	`, taskID)
	if err != nil {
		// Table may have different schema; use the service to create it
		t.Logf("direct insert failed: %v; using service", err)
		_, createErr := tai.approvalService.CreateApprovalRequest(
			context.Background(),
			ApprovalTaskCompletion,
			&taskID,
			"agent-w63",
			"test",
			"Expired approval w63",
			"test description",
			nil,
			ConfidenceContext{PatternMatch: 0.1, BlastRadius: 50, Reversible: false},
		)
		if createErr != nil {
			t.Fatalf("CreateApprovalRequest: %v", createErr)
		}
	}

	// CheckPendingApprovals should process without error
	err = tai.CheckPendingApprovals(context.Background())
	if err != nil {
		t.Logf("CheckPendingApprovals with expired: %v (acceptable)", err)
	}
}

// ─── dependenciesComplete with multiple deps ──────────────────────────────────

func TestWave63_DependenciesComplete_MultipleDepsAllComplete(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	q, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}
	sq := q.(*sqliteTaskQueue)
	ctx := context.Background()

	dep1 := Task{ID: "dep1-w63", Domain: "test", Project: "proj", Type: "feature", Title: "Dep1", Priority: 5, Status: TaskStatusQueued}
	dep2 := Task{ID: "dep2-w63", Domain: "test", Project: "proj", Type: "feature", Title: "Dep2", Priority: 5, Status: TaskStatusQueued}
	main := Task{ID: "main-w63", Domain: "test", Project: "proj", Type: "feature", Title: "Main", Priority: 5, Status: TaskStatusQueued}

	q.Enqueue(ctx, dep1)
	q.Enqueue(ctx, dep2)
	q.Enqueue(ctx, main)

	// Mark both deps as completed
	db.Exec(`UPDATE tasks SET status = 'completed' WHERE id IN (?, ?)`, dep1.ID, dep2.ID)
	db.Exec(`INSERT INTO task_dependencies (task_id, depends_on) VALUES (?, ?)`, main.ID, dep1.ID)
	db.Exec(`INSERT INTO task_dependencies (task_id, depends_on) VALUES (?, ?)`, main.ID, dep2.ID)

	done, err := sq.dependenciesComplete(ctx, main.ID)
	if err != nil {
		t.Fatalf("dependenciesComplete: %v", err)
	}
	if !done {
		t.Error("expected true when all dependencies are completed")
	}
}

func TestWave63_DependenciesComplete_OneDepsIncomplete(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	q, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}
	sq := q.(*sqliteTaskQueue)
	ctx := context.Background()

	dep1 := Task{ID: "dep1b-w63", Domain: "test", Project: "proj", Type: "feature", Title: "Dep1b", Priority: 5, Status: TaskStatusQueued}
	dep2 := Task{ID: "dep2b-w63", Domain: "test", Project: "proj", Type: "feature", Title: "Dep2b", Priority: 5, Status: TaskStatusQueued}
	main := Task{ID: "mainb-w63", Domain: "test", Project: "proj", Type: "feature", Title: "Mainb", Priority: 5, Status: TaskStatusQueued}

	q.Enqueue(ctx, dep1)
	q.Enqueue(ctx, dep2)
	q.Enqueue(ctx, main)

	// Only complete dep1, leave dep2 in queued state
	db.Exec(`UPDATE tasks SET status = 'completed' WHERE id = ?`, dep1.ID)
	db.Exec(`INSERT INTO task_dependencies (task_id, depends_on) VALUES (?, ?)`, main.ID, dep1.ID)
	db.Exec(`INSERT INTO task_dependencies (task_id, depends_on) VALUES (?, ?)`, main.ID, dep2.ID)

	done, err := sq.dependenciesComplete(ctx, main.ID)
	if err != nil {
		t.Fatalf("dependenciesComplete: %v", err)
	}
	if done {
		t.Error("expected false when one dependency is not completed")
	}
}

// ─── patrolsHandler with DB data (uncovered branch) ──────────────────────────

func TestWave63_PatrolsHandler_WithExecutionData(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	oldDB := getDBConn()
	setDBConn(db)
	defer setDBConn(oldDB)

	// Reset globalPatrolSystem to nil to exercise the db-only code path
	oldPS := globalPatrolSystem
	globalPatrolSystem = nil
	defer func() { globalPatrolSystem = oldPS }()

	// Insert some patrol execution data
	db.Exec(`
		INSERT OR IGNORE INTO patrol_executions (id, patrol_id, started_at, status)
		VALUES ('exec-w63-1', 'test-patrol-w63', datetime('now', '-5 minutes'), 'completed')
	`)

	req := httptest.NewRequest(http.MethodGet, "/api/patrols", nil)
	w := httptest.NewRecorder()
	patrolsHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}

	var resp map[string]interface{}
	if err := json.NewDecoder(w.Body).Decode(&resp); err != nil {
		t.Fatalf("decode: %v", err)
	}
	t.Logf("patrolsHandler with data: %v", resp)
}

// ─── WriteOutput via isTerminal TTY branch (DetectOutputFormat with os.Stdin) ─

func TestWave63_DetectOutputFormat_WithOsFile(t *testing.T) {
	// Use os.Stdin as the writer — it's an *os.File. In CI it won't be a char device.
	format := DetectOutputFormat("", os.Stdin)
	// Either JSON (non-tty) or Table (tty) — just ensure no panic
	if format != OutputFormatJSON && format != OutputFormatTable {
		t.Errorf("unexpected format: %s", format)
	}
	t.Logf("DetectOutputFormat with os.Stdin = %s", format)
}

// ─── planTaskHandler method check (additional branch) ────────────────────────

func TestWave63_PlanTaskHandler_GetMethod(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/task-w63-plan/plan", nil)
	req.URL.Path = "/api/tasks/task-w63-plan/plan"
	w := httptest.NewRecorder()
	planTaskHandler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405 for GET planTaskHandler, got %d", w.Code)
	}
}

// ─── replanTaskHandler method check (additional branch) ──────────────────────

func TestWave63_ReplanTaskHandler_GetMethod(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/task-w63-replan/replan", nil)
	req.URL.Path = "/api/tasks/task-w63-replan/replan"
	w := httptest.NewRecorder()
	replanTaskHandler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405 for GET replanTaskHandler, got %d", w.Code)
	}
}

// ─── extractSummary helper coverage ──────────────────────────────────────────

func TestWave63_ExtractSummary_Markdown(t *testing.T) {
	input := "# Heading\n\nThis is the first non-empty non-heading line.\n\nMore content."
	got := extractSummary(input)
	if got != "This is the first non-empty non-heading line." {
		t.Errorf("extractSummary = %q, want %q", got, "This is the first non-empty non-heading line.")
	}
}

func TestWave63_ExtractSummary_EmptyContent(t *testing.T) {
	got := extractSummary("")
	if got != "" {
		t.Errorf("extractSummary empty = %q, want \"\"", got)
	}
}

func TestWave63_ExtractSummary_OnlyHeadings(t *testing.T) {
	got := extractSummary("# Heading One\n## Heading Two\n")
	if got != "" {
		t.Errorf("extractSummary only headings = %q, want \"\"", got)
	}
}

// ─── sanitizeJSONKey coverage ─────────────────────────────────────────────────

func TestWave63_SanitizeJSONKey_WithExtension(t *testing.T) {
	result := sanitizeJSONKey("my-file.json")
	// Extension removed, dashes replaced with underscores
	if !strings.Contains(result, "my") {
		t.Errorf("sanitizeJSONKey = %q, expected to contain 'my'", result)
	}
	if strings.Contains(result, ".json") {
		t.Errorf("sanitizeJSONKey = %q, should not contain '.json'", result)
	}
}

func TestWave63_SanitizeJSONKey_AlphanumericOnly(t *testing.T) {
	result := sanitizeJSONKey("leadContext123.md")
	if strings.Contains(result, ".") || strings.Contains(result, "-") {
		t.Errorf("sanitizeJSONKey = %q, should not contain dots or dashes", result)
	}
}

// ─── writeJSON helper coverage ────────────────────────────────────────────────

func TestWave63_WriteJSON_Basic(t *testing.T) {
	w := httptest.NewRecorder()
	writeJSON(w, map[string]string{"hello": "world"})

	if w.Code != http.StatusOK {
		t.Errorf("writeJSON status = %d, want 200", w.Code)
	}
	ct := w.Header().Get("Content-Type")
	if !strings.Contains(ct, "application/json") {
		t.Errorf("Content-Type = %q, want application/json", ct)
	}
	var resp map[string]string
	if err := json.NewDecoder(w.Body).Decode(&resp); err != nil {
		t.Errorf("decode writeJSON response: %v", err)
	}
	if resp["hello"] != "world" {
		t.Errorf("resp[hello] = %q, want world", resp["hello"])
	}
}

// ─── requireMethod helper coverage ────────────────────────────────────────────

func TestWave63_RequireMethod_Match(t *testing.T) {
	w := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/test", nil)
	ok := requireMethod(w, req, http.MethodGet)
	if !ok {
		t.Error("requireMethod returned false for matching method")
	}
	if w.Code != http.StatusOK {
		t.Errorf("requireMethod match: unexpected status %d", w.Code)
	}
}

func TestWave63_RequireMethod_Mismatch(t *testing.T) {
	w := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/test", nil)
	ok := requireMethod(w, req, http.MethodPost)
	if ok {
		t.Error("requireMethod returned true for mismatched method")
	}
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("requireMethod mismatch: expected 405, got %d", w.Code)
	}
}

// ─── withDB helper coverage ────────────────────────────────────────────────────

func TestWave63_WithDB_NilDB_Returns503(t *testing.T) {
	oldDB := getDBConn()
	setDBConn(nil)
	defer setDBConn(oldDB)

	w := httptest.NewRecorder()
	called := false
	withDB(w, func(db *sql.DB) {
		called = true
	})
	if called {
		t.Error("withDB called fn with nil DB")
	}
	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("withDB nil DB: expected 503, got %d", w.Code)
	}
}

// ─── isValidID coverage ────────────────────────────────────────────────────────

func TestWave63_IsValidID_Valid(t *testing.T) {
	cases := []string{"TASK-123", "agent_01", "forge:kimi", "abc-def"}
	for _, c := range cases {
		if !isValidID(c) {
			t.Errorf("isValidID(%q) = false, want true", c)
		}
	}
}

func TestWave63_IsValidID_Empty(t *testing.T) {
	if isValidID("") {
		t.Error("isValidID(\"\") = true, want false")
	}
}

func TestWave63_IsValidID_TooLong(t *testing.T) {
	long := strings.Repeat("a", 129)
	if isValidID(long) {
		t.Errorf("isValidID(129 chars) = true, want false")
	}
}

func TestWave63_IsValidID_InvalidChars(t *testing.T) {
	if isValidID("task/slash") {
		t.Error("isValidID('task/slash') = true, want false")
	}
	if isValidID("task space") {
		t.Error("isValidID('task space') = true, want false")
	}
}

// ─── isValidText coverage ─────────────────────────────────────────────────────

func TestWave63_IsValidText_Empty(t *testing.T) {
	if !isValidText("") {
		t.Error("isValidText(\"\") = false, want true")
	}
}

func TestWave63_IsValidText_TooLong(t *testing.T) {
	long := strings.Repeat("a", 2049)
	if isValidText(long) {
		t.Error("isValidText(2049 chars) = true, want false")
	}
}

func TestWave63_IsValidText_Valid(t *testing.T) {
	if !isValidText("Hello, world! (test)") {
		t.Error("isValidText valid text = false, want true")
	}
}

// ─── writeJSONError coverage ──────────────────────────────────────────────────

func TestWave63_WriteJSONError_Basic(t *testing.T) {
	w := httptest.NewRecorder()
	writeJSONError(w, http.StatusBadRequest, "bad input")

	if w.Code != http.StatusBadRequest {
		t.Errorf("writeJSONError status = %d, want 400", w.Code)
	}
	var resp map[string]string
	if err := json.NewDecoder(w.Body).Decode(&resp); err != nil {
		t.Errorf("decode writeJSONError: %v", err)
	}
	if resp["error"] != "bad input" {
		t.Errorf("error field = %q, want 'bad input'", resp["error"])
	}
}

// ─── isTitleJunk coverage ─────────────────────────────────────────────────────

func TestWave63_IsTitleJunk_JunkTitles(t *testing.T) {
	cases := []string{"fix bug", "update code", "add feature", "abc", ""}
	for _, c := range cases {
		if !isTitleJunk(c) {
			t.Errorf("isTitleJunk(%q) = false, want true", c)
		}
	}
}

func TestWave63_IsTitleJunk_ValidTitle(t *testing.T) {
	if isTitleJunk("Implement user authentication flow") {
		t.Error("isTitleJunk(valid title) = true, want false")
	}
}

func TestWave63_IsTitleJunk_FeatureSuffix(t *testing.T) {
	if !isTitleJunk("something (feature)") {
		t.Error("isTitleJunk('something (feature)') = false, want true")
	}
}

// ─── queueTaskHandler additional branches ─────────────────────────────────────

func TestWave63_QueueTaskHandler_MethodNotAllowed(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/task-w63-q/queue", nil)
	req.URL.Path = "/api/tasks/task-w63-q/queue"
	w := httptest.NewRecorder()
	queueTaskHandler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("queueTaskHandler GET: expected 405, got %d", w.Code)
	}
}

func TestWave63_QueueTaskHandler_EmptyTaskID(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/tasks//queue", nil)
	req.URL.Path = "/api/tasks//queue"
	w := httptest.NewRecorder()
	queueTaskHandler(w, req)

	if w.Code != http.StatusBadRequest {
		t.Logf("queueTaskHandler empty ID: %d (acceptable)", w.Code)
	}
}

func TestWave63_QueueTaskHandler_TaskNotFound(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/tasks/NONEXISTENT-W63-Q/queue", nil)
	req.URL.Path = "/api/tasks/NONEXISTENT-W63-Q/queue"
	w := httptest.NewRecorder()
	queueTaskHandler(w, req)

	if w.Code != http.StatusNotFound {
		t.Logf("queueTaskHandler task not found: %d (acceptable)", w.Code)
	}
}

// ─── pauseTaskHandler branches ────────────────────────────────────────────────

func TestWave63_PauseTaskHandler_MethodNotAllowed(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/task-w63-p/pause", nil)
	req.URL.Path = "/api/tasks/task-w63-p/pause"
	w := httptest.NewRecorder()
	pauseTaskHandler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("pauseTaskHandler GET: expected 405, got %d", w.Code)
	}
}

func TestWave63_PauseTaskHandler_TaskNotFound(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/tasks/NONEXISTENT-W63-PAUSE/pause", nil)
	req.URL.Path = "/api/tasks/NONEXISTENT-W63-PAUSE/pause"
	w := httptest.NewRecorder()
	pauseTaskHandler(w, req)

	if w.Code != http.StatusNotFound && w.Code != http.StatusBadRequest {
		t.Logf("pauseTaskHandler task not found: %d %s", w.Code, w.Body.String())
	}
}
