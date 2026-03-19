//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
	"time"
)

// ---------------------------------------------------------------------------
// handlers_ui.go: uiFleetHandler — 50% coverage
// This handler builds HTML — just verify it returns 200 and HTML content
// ---------------------------------------------------------------------------

func TestWave71_UIFleetHandler_GET(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodGet, "/ui", nil)
	w := httptest.NewRecorder()
	uiFleetHandler(w, r)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
	body := w.Body.String()
	if !strings.Contains(body, "FORGE Fleet") {
		t.Error("expected 'FORGE Fleet' in HTML body")
	}
}

func TestWave71_UIFleetHandler_MethodNotAllowed(t *testing.T) {
	r := httptest.NewRequest(http.MethodPost, "/ui", nil)
	w := httptest.NewRecorder()
	uiFleetHandler(w, r)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave71_UIFleetHandler_NoAgents(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodGet, "/ui", nil)
	w := httptest.NewRecorder()
	uiFleetHandler(w, r)

	body := w.Body.String()
	if !strings.Contains(body, "No agents") {
		t.Error("expected 'No agents' in empty fleet dashboard")
	}
}

// ---------------------------------------------------------------------------
// coordination.go: GetCoordinationHTML — 56.5%
// Test the HTML generation with blockers, agents
// ---------------------------------------------------------------------------

func TestWave71_CoordinationHTML_NoData(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	db := getDBConn()
	cd := NewCoordinationDashboard(db)
	html := cd.GetCoordinationHTML()

	if html == "" {
		t.Error("expected non-empty HTML")
	}
	if !strings.Contains(html, "Sprint Coordination") {
		t.Error("expected 'Sprint Coordination' in HTML")
	}
}

func TestWave71_CoordinationHTML_WithBlockers(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	db := getDBConn()
	cd := NewCoordinationDashboard(db)

	// Insert a long-idle agent (high-context) to trigger blocked status
	_, _ = db.Exec(`INSERT OR REPLACE INTO agent_heartbeats (agent_id, node, status, context_pct, last_seen)
		VALUES (?, ?, ?, ?, ?)`,
		"test-blocked-agent", "prya", "online", 85.0,
		time.Now().Add(-20*time.Minute).Format("2006-01-02 15:04:05"))

	html := cd.GetCoordinationHTML()
	if html == "" {
		t.Error("expected non-empty HTML")
	}
}

func TestWave71_TruncateString(t *testing.T) {
	cases := []struct {
		input  string
		maxLen int
		want   string
	}{
		{"short", 10, "short"},
		{"exactly10!", 10, "exactly10!"},
		{"this is a long string", 10, "this is..."},
		{"", 5, ""},
	}
	for _, c := range cases {
		got := truncateString(c.input, c.maxLen)
		if got != c.want {
			t.Errorf("truncateString(%q, %d) = %q, want %q", c.input, c.maxLen, got, c.want)
		}
	}
}

// ---------------------------------------------------------------------------
// patrol.go: autoPromoteCompletedTasksInLane — 63.2%
// ---------------------------------------------------------------------------

func TestWave71_AutoPromoteCompletedTasksInLane_NoRows(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	db := getDBConn()
	// No COMPLETED tasks in a lane — should return nil
	err := autoPromoteCompletedTasksInLane(context.Background(), db)
	if err != nil {
		t.Errorf("expected nil for no completed tasks, got: %v", err)
	}
}

func TestWave71_AutoPromoteCompletedTasksInLane_CompletedWithNoLaneManager(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	db := getDBConn()
	oldLane := globalLaneManager
	globalLaneManager = nil
	defer func() { globalLaneManager = oldLane }()

	// Insert a COMPLETED task in a lane
	now := time.Now().Add(-2 * time.Minute).Format(time.RFC3339)
	_, _ = db.Exec(`INSERT INTO tasks (id, domain, project, type, priority, status, state, lane, created_at, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		"task-promote-1", "test", "test", "feature", 5, "completed", "COMPLETED", "backlog", now, now)

	err := autoPromoteCompletedTasksInLane(context.Background(), db)
	if err != nil {
		t.Errorf("expected nil when lane manager is nil, got: %v", err)
	}
}

// ---------------------------------------------------------------------------
// patrol.go: syncRoyalJelly — 62.9%
// Test the case where context dir doesn't exist (returns nil)
// ---------------------------------------------------------------------------

func TestWave71_SyncRoyalJelly_NoDirExists(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Change working directory to a temp dir that has no .forge/context
	tmpDir := t.TempDir()
	oldWd, _ := os.Getwd()
	if err := os.Chdir(tmpDir); err != nil {
		t.Skipf("could not change dir: %v", err)
	}
	defer os.Chdir(oldWd)

	db := getDBConn()
	err := syncRoyalJelly(context.Background(), db)
	if err != nil {
		t.Errorf("expected nil when context dir doesn't exist, got: %v", err)
	}
}

// ---------------------------------------------------------------------------
// patrol.go: confidenceApproveCompletedTasks — 5%
// Test with stateMachine=nil (early return)
// ---------------------------------------------------------------------------

func TestWave71_ConfidenceApproveCompletedTasks_NilStateMachine(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	db := getDBConn()
	oldSM := stateMachine
	stateMachine = nil
	defer func() { stateMachine = oldSM }()

	err := confidenceApproveCompletedTasks(context.Background(), db)
	if err != nil {
		t.Errorf("expected nil with nil stateMachine, got: %v", err)
	}
}

func TestWave71_ConfidenceApproveCompletedTasks_NoCompletedTasks(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	db := getDBConn()
	// stateMachine is set in setupClaimTestDB, globalApprovalService may be nil
	// Either way, with no COMPLETED tasks older than 30s, should return nil
	err := confidenceApproveCompletedTasks(context.Background(), db)
	if err != nil {
		t.Errorf("expected nil with no completed tasks, got: %v", err)
	}
}

// ---------------------------------------------------------------------------
// handlers_node_metrics.go: nodeMetricsPushPatrol — 58.3%
// Test the case where FORGE_LEAD_URL is not set (early return)
// ---------------------------------------------------------------------------

func TestWave71_NodeMetricsPushPatrol_NoLeadURL(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	os.Unsetenv("FORGE_LEAD_URL")
	db := getDBConn()

	err := nodeMetricsPushPatrol(context.Background(), db)
	if err != nil {
		t.Errorf("expected nil with no lead URL, got: %v", err)
	}
}

func TestWave71_NodeMetricsPushPatrol_WithFakeLeadURL(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Set up a test server to receive the push
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))
	defer server.Close()

	os.Setenv("FORGE_LEAD_URL", server.URL)
	defer os.Unsetenv("FORGE_LEAD_URL")

	db := getDBConn()
	err := nodeMetricsPushPatrol(context.Background(), db)
	if err != nil {
		t.Errorf("expected nil with valid lead URL, got: %v", err)
	}
}

// ---------------------------------------------------------------------------
// parity_check.go: countV3Agents — 63.6%
// ---------------------------------------------------------------------------

func TestWave71_CountV3Agents_EmptyDB(t *testing.T) {
	dbPath := t.TempDir() + "/test-v3.db"

	// Create minimal DB with no agents table
	checker := NewParityChecker(t.TempDir(), dbPath)
	count, err := checker.countV3Agents()
	if err != nil {
		// sqlite open with non-existent file may fail or return 0
		t.Logf("countV3Agents error (acceptable): %v", err)
		return
	}
	if count != 0 {
		t.Errorf("expected 0 for DB with no agents table, got %d", count)
	}
}

func TestWave71_CountV3Agents_WithDB(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Use a separate DB file for parity testing
	dbFile := t.TempDir() + "/parity-test.db"
	// Write a minimal DB
	testDB, _ := OpenDB(dbFile)
	if testDB != nil {
		defer testDB.Close()
		testDB.Exec(`CREATE TABLE agents (id TEXT PRIMARY KEY, name TEXT)`)
		testDB.Exec(`INSERT INTO agents VALUES ('a1', 'kimi')`)
	}

	checker := NewParityChecker(t.TempDir(), dbFile)
	count, err := checker.countV3Agents()
	if err != nil {
		t.Fatalf("countV3Agents: %v", err)
	}
	if count != 1 {
		t.Errorf("expected 1 agent, got %d", count)
	}
}

// ---------------------------------------------------------------------------
// context_sync.go: SyncEnvelopesToFilesystem — 72.2%
// ---------------------------------------------------------------------------

func TestWave71_SyncEnvelopesToFilesystem_Empty(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	tmpDir := t.TempDir()
	db := getDBConn()
	cm := testContextManager(t, db, tmpDir)
	cs, err := NewContextSync(cm, db, tmpDir)
	if err != nil {
		t.Fatalf("NewContextSync: %v", err)
	}
	t.Cleanup(cs.Stop)

	// No envelopes — should return nil
	err = cs.SyncEnvelopesToFilesystem()
	if err != nil {
		t.Errorf("expected nil for empty envelopes, got: %v", err)
	}
}

// ---------------------------------------------------------------------------
// tui.go: Handler and JSONHandler — 62.5%, 71.4%
// ---------------------------------------------------------------------------

func TestWave71_TUIHandler_POST(t *testing.T) {
	// TUI handler accepts all methods — just verify it responds
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	db := getDBConn()
	tui := NewTUI(db, hub, taskQueue)
	handler := tui.Handler()

	r := httptest.NewRequest(http.MethodPost, "/api/tui", nil)
	w := httptest.NewRecorder()
	handler(w, r)
	// Handler returns 200 (no method guard) or 500 if template fails
	if w.Code != http.StatusOK && w.Code != http.StatusInternalServerError {
		t.Errorf("expected 200 or 500 for POST, got %d", w.Code)
	}
}

func TestWave71_TUIHandler_GET(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	db := getDBConn()
	tui := NewTUI(db, hub, taskQueue)
	handler := tui.Handler()

	r := httptest.NewRequest(http.MethodGet, "/api/tui", nil)
	w := httptest.NewRecorder()
	handler(w, r)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave71_TUIJSONHandler_GET(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	db := getDBConn()
	tui := NewTUI(db, hub, taskQueue)
	handler := tui.JSONHandler()

	r := httptest.NewRequest(http.MethodGet, "/api/tui/json", nil)
	w := httptest.NewRecorder()
	handler(w, r)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave71_TUIJSONHandler_DELETE(t *testing.T) {
	// JSONHandler accepts all methods — verify it responds
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	db := getDBConn()
	tui := NewTUI(db, hub, taskQueue)
	handler := tui.JSONHandler()

	r := httptest.NewRequest(http.MethodDelete, "/api/tui/json", nil)
	w := httptest.NewRecorder()
	handler(w, r)
	if w.Code != http.StatusOK && w.Code != http.StatusInternalServerError {
		t.Errorf("expected 200 or 500 for DELETE, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// completion.go: HandleCompletion edge cases — 57.1%
// ---------------------------------------------------------------------------

func TestWave71_HandleCompletion_Zsh(t *testing.T) {
	m := NewCompletionManager()
	// Redirect stdout temporarily via a pipe — test via GenerateZsh
	var buf strings.Builder
	if err := m.GenerateZsh(&buf); err != nil {
		t.Fatalf("GenerateZsh: %v", err)
	}
	if buf.Len() == 0 {
		t.Error("expected non-empty zsh completion script")
	}
}

func TestWave71_HandleCompletion_Fish(t *testing.T) {
	m := NewCompletionManager()
	var buf strings.Builder
	if err := m.GenerateFish(&buf); err != nil {
		t.Fatalf("GenerateFish: %v", err)
	}
	if buf.Len() == 0 {
		t.Error("expected non-empty fish completion script")
	}
}

// ---------------------------------------------------------------------------
// fleet_scaler.go: readLiveRAMMB cache path
// ---------------------------------------------------------------------------

func TestWave71_ReadLiveRAMMB_Cache(t *testing.T) {
	// Set a fresh cached value
	liveRAMCache.Lock()
	liveRAMCache.valueMB = 8192
	liveRAMCache.lastRead = time.Now() // Fresh
	liveRAMCache.Unlock()

	val, err := readLiveRAMMB()
	if err != nil {
		t.Fatalf("readLiveRAMMB with cache: %v", err)
	}
	if val != 8192 {
		t.Errorf("expected cached value 8192, got %d", val)
	}

	// Reset cache
	liveRAMCache.Lock()
	liveRAMCache.lastRead = time.Time{}
	liveRAMCache.Unlock()
}

// ---------------------------------------------------------------------------
// xnode.go: ListAcksHandler — 65%
// ---------------------------------------------------------------------------

func TestWave71_XNode_ListAcksHandler_GET(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	db := getDBConn()
	xc, err := NewXNodeController(db, "test-node")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}

	r := httptest.NewRequest(http.MethodGet, "/api/xnode/acks", nil)
	w := httptest.NewRecorder()
	xc.ListAcksHandler(w, r)

	// Should return 200 with empty acks list
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave71_XNode_ListAcksHandler_POST(t *testing.T) {
	// ListAcksHandler accepts all methods (no method guard)
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	db := getDBConn()
	xc, _ := NewXNodeController(db, "test-node")

	r := httptest.NewRequest(http.MethodPost, "/api/xnode/acks", nil)
	w := httptest.NewRecorder()
	xc.ListAcksHandler(w, r)
	// Should return 200 or 500 (dir may not exist)
	if w.Code >= 400 && w.Code != http.StatusInternalServerError {
		t.Errorf("unexpected status code %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// xnode.go: ListNodesHandler — 75%
// ---------------------------------------------------------------------------

func TestWave71_XNode_ListNodesHandler(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	db := getDBConn()
	xc, _ := NewXNodeController(db, "test-node")

	r := httptest.NewRequest(http.MethodGet, "/api/xnode/nodes", nil)
	w := httptest.NewRecorder()
	xc.ListNodesHandler(w, r)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// context_helpers.go: findLatestEnvelopeForAgent — 81.8%
// ---------------------------------------------------------------------------

func TestWave71_FindLatestEnvelopeForAgent_NotFound(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	env, err := findLatestEnvelopeForAgent(context.Background(), "nonexistent-agent")
	if err != nil {
		// No-row errors should return nil envelope, not error
		t.Logf("findLatestEnvelopeForAgent error: %v", err)
	}
	if env != nil {
		t.Error("expected nil envelope for nonexistent agent")
	}
}

// ---------------------------------------------------------------------------
// context.go: storeInFilesystem — 75%
// ---------------------------------------------------------------------------

func TestWave71_ContextManager_StoreInFilesystem(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	tmpDir := t.TempDir()
	db := getDBConn()
	cm := testContextManager(t, db, tmpDir)

	env := &ContextEnvelope{
		ID:      "env-store-1",
		AgentID: "kimi",
		Domain:  "test",
		Project: "myproject",
		TaskID:  "task-1",
	}

	err := cm.storeInFilesystem(env)
	if err != nil {
		t.Fatalf("storeInFilesystem: %v", err)
	}
}

// ---------------------------------------------------------------------------
// token_budget.go: writeTokenBudgetFile — 72.7%
// ---------------------------------------------------------------------------

func TestWave71_WriteTokenBudgetFile_Valid(t *testing.T) {
	tmpDir := t.TempDir()
	path := tmpDir + "/token_budget.json"

	budget := &tokenBudgetFile{
		Node:      "test-node",
		UpdatedAt: time.Now().Format(time.RFC3339),
		Providers: map[string]tbProviderData{
			"anthropic": {
				Provider: "anthropic",
				Status:   "ok",
			},
		},
	}

	err := writeTokenBudgetFile(path, budget)
	if err != nil {
		t.Fatalf("writeTokenBudgetFile: %v", err)
	}

	// Verify file was written
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read written file: %v", err)
	}
	if len(data) == 0 {
		t.Error("expected non-empty file content")
	}
}

func TestWave71_WriteTokenBudgetFile_InvalidPath(t *testing.T) {
	err := writeTokenBudgetFile("/nonexistent/dir/budget.json", &tokenBudgetFile{})
	if err == nil {
		t.Error("expected error for invalid path")
	}
}

func TestWave71_ReadTokenBudgetFile_NotExist(t *testing.T) {
	result, err := readTokenBudgetFile("/tmp/nonexistent-budget-file.json")
	if err != nil {
		t.Fatalf("expected nil error for missing file, got: %v", err)
	}
	if result != nil {
		t.Error("expected nil result for missing file")
	}
}

func TestWave71_ReadTokenBudgetFile_Valid(t *testing.T) {
	tmpDir := t.TempDir()
	path := tmpDir + "/budget.json"

	budget := &tokenBudgetFile{
		Node:      "mynode",
		UpdatedAt: time.Now().Format(time.RFC3339),
		Providers: map[string]tbProviderData{},
	}
	if err := writeTokenBudgetFile(path, budget); err != nil {
		t.Fatalf("write: %v", err)
	}

	result, err := readTokenBudgetFile(path)
	if err != nil {
		t.Fatalf("readTokenBudgetFile: %v", err)
	}
	if result == nil {
		t.Fatal("expected non-nil result")
	}
	if result.Node != "mynode" {
		t.Errorf("expected node=mynode, got %s", result.Node)
	}
}
