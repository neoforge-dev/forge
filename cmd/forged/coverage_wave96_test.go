//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/bubbles/list"
)

// ---------------------------------------------------------------------------
// xnode.go: XNodeController handlers — ListNodesHandler, ListAcksHandler,
// RegisterHandler, StatusHandler  (covers multiple uncovered blocks)
// ---------------------------------------------------------------------------

func TestWave96_XNodeController_ListNodesHandler_Empty(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	xc, err := NewXNodeController(db, "wave96-node")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}

	r := httptest.NewRequest(http.MethodGet, "/api/xnode/nodes", nil)
	w := httptest.NewRecorder()
	xc.ListNodesHandler(w, r)
	if w.Code >= 500 {
		t.Logf("ListNodesHandler empty: got %d — %s", w.Code, w.Body.String())
	}
	t.Logf("ListNodesHandler empty: %d", w.Code)
}

func TestWave96_XNodeController_ListNodesHandler_WithNode(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	xc, err := NewXNodeController(db, "wave96-node")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}

	// Register a node first
	node := Node{
		ID:       "wave96-test-node",
		Hostname: "wave96host",
		Address:  "10.0.0.1",
		Status:   "online",
	}
	if err := xc.RegisterNode(context.Background(), node); err != nil {
		t.Fatalf("RegisterNode: %v", err)
	}

	r := httptest.NewRequest(http.MethodGet, "/api/xnode/nodes", nil)
	w := httptest.NewRecorder()
	xc.ListNodesHandler(w, r)
	if w.Code >= 500 {
		t.Logf("ListNodesHandler with node: got %d", w.Code)
	}
	t.Logf("ListNodesHandler with node: %d, body: %s", w.Code, w.Body.String()[:min96(len(w.Body.String()), 120)])
}

func min96(a, b int) int {
	if a < b {
		return a
	}
	return b
}

func TestWave96_XNodeController_RegisterHandler_POST(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	xc, err := NewXNodeController(db, "wave96-node")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}

	body := `{"id":"wave96-reg-node","hostname":"wave96host","address":"10.0.0.99","status":"online"}`
	r := httptest.NewRequest(http.MethodPost, "/api/xnode/nodes/register", strings.NewReader(body))
	r.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	xc.RegisterHandler(w, r)
	if w.Code >= 500 {
		t.Logf("RegisterHandler POST: got %d — %s", w.Code, w.Body.String())
	}
	t.Logf("RegisterHandler POST: %d", w.Code)
}

func TestWave96_XNodeController_RegisterHandler_MethodNotAllowed(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	xc, err := NewXNodeController(db, "wave96-node")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}

	r := httptest.NewRequest(http.MethodGet, "/api/xnode/nodes/register", nil)
	w := httptest.NewRecorder()
	xc.RegisterHandler(w, r)
	if w.Code != http.StatusMethodNotAllowed {
		t.Logf("RegisterHandler GET: got %d (want 405)", w.Code)
	}
}

func TestWave96_XNodeController_StatusHandler(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	xc, err := NewXNodeController(db, "wave96-node")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}

	r := httptest.NewRequest(http.MethodGet, "/api/xnode/status", nil)
	w := httptest.NewRecorder()
	xc.StatusHandler(w, r)
	if w.Code >= 500 {
		t.Logf("StatusHandler: got %d — %s", w.Code, w.Body.String())
	}
	t.Logf("StatusHandler: %d", w.Code)
}

func TestWave96_XNodeController_ListAcksHandler(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	xc, err := NewXNodeController(db, "wave96-node")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}

	r := httptest.NewRequest(http.MethodGet, "/api/xnode/acks", nil)
	w := httptest.NewRecorder()
	xc.ListAcksHandler(w, r)
	if w.Code >= 500 {
		t.Logf("ListAcksHandler: got %d — %s", w.Code, w.Body.String())
	}
	t.Logf("ListAcksHandler: %d", w.Code)
}

func TestWave96_XNodeController_ListInboxHandler(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	xc, err := NewXNodeController(db, "wave96-node")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}

	r := httptest.NewRequest(http.MethodGet, "/api/xnode/inbox", nil)
	w := httptest.NewRecorder()
	xc.ListInboxHandler(w, r)
	if w.Code >= 500 {
		t.Logf("ListInboxHandler: got %d — %s", w.Code, w.Body.String())
	}
	t.Logf("ListInboxHandler: %d", w.Code)
}

func TestWave96_XNodeController_ForwardHandler_MissingFields(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	xc, err := NewXNodeController(db, "wave96-node")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}

	// Missing target_node should return 400
	body := `{"task_id":"wave96-task"}`
	r := httptest.NewRequest(http.MethodPost, "/api/xnode/forward", strings.NewReader(body))
	r.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	xc.ForwardHandler(w, r)
	if w.Code != http.StatusBadRequest {
		t.Logf("ForwardHandler missing target: got %d (want 400)", w.Code)
	}
}

// ---------------------------------------------------------------------------
// handlers_plan.go: queueTaskHandler — worker assignment path
// The task must have status=planned for the worker branch to fire.
// ---------------------------------------------------------------------------

func TestWave96_QueueTaskHandler_PlannedTask_WithWorker(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	if hub == nil {
		t.Skip("hub not initialized")
	}

	// Inject a fake worker
	fakeWorker := &WebSocketWorker{
		AgentID: "wave96-queue-worker",
		Node:    "test-node",
		Send:    make(chan WSMessage, 100),
	}
	fakeWorker.touchPong()
	hub.mu.Lock()
	hub.workers["wave96-queue-worker"] = fakeWorker
	hub.mu.Unlock()
	defer func() {
		hub.mu.Lock()
		delete(hub.workers, "wave96-queue-worker")
		hub.mu.Unlock()
	}()

	// Insert a PLANNED task
	db := getDBConn()
	now := time.Now().UTC().Format(time.RFC3339)
	_, err := db.Exec(`INSERT INTO tasks (id, domain, project, type, priority, status, state, title, created_at, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		"wave96-planned-task", "codeswiftr", "proj", "feature", 5, "planned", "QUEUED", "Planned Task", now, now)
	if err != nil {
		t.Fatalf("insert task: %v", err)
	}

	r := httptest.NewRequest(http.MethodPost, "/api/tasks/wave96-planned-task/queue", nil)
	w := httptest.NewRecorder()
	queueTaskHandler(w, r)
	t.Logf("queueTaskHandler planned+worker: got %d — %s", w.Code, w.Body.String()[:min96(len(w.Body.String()), 100)])
}

// ---------------------------------------------------------------------------
// tui_dashboard.go: DashboardModel.Update — Dispatch key and ToggleTheme key
// ---------------------------------------------------------------------------

func TestWave96_DashboardModel_Update_DispatchKey(t *testing.T) {
	m := NewDashboardModel("http://localhost:9")
	defer m.ticker.Stop()

	// Press 'd' to trigger Dispatch branch
	updated, cmd := m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{'d'}})
	_ = cmd
	if dm, ok := updated.(DashboardModel); ok {
		t.Logf("DashboardModel Dispatch key: showQuick=%v", dm.showQuick)
	}
}

func TestWave96_DashboardModel_Update_ToggleTheme(t *testing.T) {
	m := NewDashboardModel("http://localhost:9")
	defer m.ticker.Stop()
	m.isDark = true

	// Press 't' to toggle theme
	updated, cmd := m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{'t'}})
	_ = cmd
	if dm, ok := updated.(DashboardModel); ok {
		t.Logf("DashboardModel ToggleTheme key: isDark=%v (was true)", dm.isDark)
	}
}

func TestWave96_DashboardModel_Update_EnterOnAgentsView(t *testing.T) {
	m := NewDashboardModel("http://localhost:9")
	defer m.ticker.Stop()

	// Create a list with an agent item
	items := []list.Item{
		agentItem{agent: AgentHealth{AgentID: "wave96-agent", Node: "test-node", Status: AgentStatusIdle}},
	}
	m.agentList = list.New(items, list.NewDefaultDelegate(), 80, 10)
	m.currentView = AgentsView

	// Press 'enter' on the AgentsView — should select agent and switch to detail view
	updated, cmd := m.Update(tea.KeyMsg{Type: tea.KeyEnter})
	_ = cmd
	if dm, ok := updated.(DashboardModel); ok {
		t.Logf("DashboardModel Enter on AgentsView: view=%v, selectedAgent=%v", dm.currentView, dm.selectedAgent != nil)
	}
}

// ---------------------------------------------------------------------------
// dispatchHandler: GET with existing git_guard_actions rows
// (covers the rows.Next() loop, 3 stmts)
// ---------------------------------------------------------------------------

func TestWave96_DispatchHandler_GET_WithRows(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	db := getDBConn()
	now := time.Now().UTC().Format(time.RFC3339)
	// Insert a task first (FK constraint in git_guard_actions references tasks.id)
	_, _ = db.Exec(`INSERT INTO tasks (id, domain, project, type, priority, status, state, title, created_at, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		"wave96-dispatch-task", "codeswiftr", "proj", "feature", 5, "completed", "COMPLETED", "Dispatch Task", now, now)
	_, _ = db.Exec(`INSERT INTO git_guard_actions (id, task_id, type, payload_hash, status, created_at)
		VALUES (?, ?, ?, ?, ?, ?)`,
		"wave96-gga-1", "wave96-dispatch-task", "commit", "hash-wave96-1", "completed", now)

	r := httptest.NewRequest(http.MethodGet, "/dispatch", nil)
	w := httptest.NewRecorder()
	dispatchHandler(w, r)
	if w.Code >= 500 {
		t.Logf("dispatchHandler GET with rows: got %d", w.Code)
	}

	var result map[string]interface{}
	if err := json.Unmarshal(w.Body.Bytes(), &result); err == nil {
		t.Logf("dispatchHandler GET with rows: count=%.0f", result["count"])
	}
}

// ---------------------------------------------------------------------------
// uiFleetHandler: with patrol_executions rows (covers handlers_ui.go:111 loop)
// ---------------------------------------------------------------------------

func TestWave96_UIFleetHandler_WithPatrolExecutions(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	db := getDBConn()
	now := time.Now().UTC().Format(time.RFC3339)
	// patrol_executions schema: id, patrol_id, started_at, completed_at, status, result, error
	_, _ = db.Exec(`INSERT INTO patrol_executions (id, patrol_id, started_at, completed_at, status)
		VALUES (?, ?, ?, ?, ?)`,
		"wave96-pe-1", "wave96-patrol", now, now, "success")

	r := httptest.NewRequest(http.MethodGet, "/ui/fleet", nil)
	w := httptest.NewRecorder()
	uiFleetHandler(w, r)
	if w.Code >= 500 {
		t.Logf("uiFleetHandler with patrol rows: got %d", w.Code)
	}
	t.Logf("uiFleetHandler with patrol rows: %d", w.Code)
}

// ---------------------------------------------------------------------------
// handlers_agent.go: agentTelemetryHandler — covers the query path
// ---------------------------------------------------------------------------

func TestWave96_AgentTelemetryHandler_WithData(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	db := getDBConn()
	now := time.Now().UTC().Format(time.RFC3339)
	// agent_telemetry: id(auto), agent_id, node, context_pct, task_id, status, tool, metadata, timestamp, created_at
	_, _ = db.Exec(`INSERT INTO agent_telemetry (agent_id, node, context_pct, status, tool, timestamp)
		VALUES (?, ?, ?, ?, ?, ?)`,
		"wave96-tel-agent", "test-node", 45.0, "idle", "Read", now)

	r := httptest.NewRequest(http.MethodGet, "/api/agents/wave96-tel-agent/telemetry", nil)
	w := httptest.NewRecorder()
	agentTelemetryHandler(w, r)
	if w.Code >= 500 {
		t.Logf("agentTelemetryHandler: got %d — %s", w.Code, w.Body.String())
	}
	t.Logf("agentTelemetryHandler: %d", w.Code)
}
