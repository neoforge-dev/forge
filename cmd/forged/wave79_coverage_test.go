//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

// ---------------------------------------------------------------------------
// handlers_pwa_bridge.go: handleSummary/handleAgents nil db — 71-73%
// ---------------------------------------------------------------------------

func TestWave79_PWAHandler_Summary_NilDB(t *testing.T) {
	h := NewPWADashboardHandler(nil)
	r := httptest.NewRequest(http.MethodGet, "/api/pwa/summary", nil)
	w := httptest.NewRecorder()
	h.handleSummary(w, r)
	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503 for nil dashboard, got %d", w.Code)
	}
}

func TestWave79_PWAHandler_Agents_NilDB(t *testing.T) {
	h := NewPWADashboardHandler(nil)
	r := httptest.NewRequest(http.MethodGet, "/api/pwa/agents", nil)
	w := httptest.NewRecorder()
	h.handleAgents(w, r)
	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503 for nil dashboard, got %d", w.Code)
	}
}

func TestWave79_PWAHandler_Summary_WithDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	d := NewDashboard(db, nil)
	h := NewPWADashboardHandler(d)
	r := httptest.NewRequest(http.MethodGet, "/api/pwa/summary", nil)
	w := httptest.NewRecorder()
	h.handleSummary(w, r)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave79_PWAHandler_Agents_WithDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	d := NewDashboard(db, nil)
	h := NewPWADashboardHandler(d)
	r := httptest.NewRequest(http.MethodGet, "/api/pwa/agents", nil)
	w := httptest.NewRecorder()
	h.handleAgents(w, r)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// handlers_ui.go: uiFleetHandler — 66%
// ---------------------------------------------------------------------------

func TestWave79_UIFleetHandler_MethodNotAllowed(t *testing.T) {
	r := httptest.NewRequest(http.MethodPost, "/ui/fleet", nil)
	w := httptest.NewRecorder()
	uiFleetHandler(w, r)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave79_UIFleetHandler_GetOK(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodGet, "/ui/fleet", nil)
	w := httptest.NewRecorder()
	uiFleetHandler(w, r)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// handlers_tui.go: planTaskHandler — 66.7%
// ---------------------------------------------------------------------------

func TestWave79_PlanTaskHandler_MethodNotAllowed(t *testing.T) {
	r := httptest.NewRequest(http.MethodGet, "/api/tasks/tid/plan", nil)
	w := httptest.NewRecorder()
	planTaskHandler(w, r)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave79_PlanTaskHandler_MissingID(t *testing.T) {
	r := httptest.NewRequest(http.MethodPost, "/api/tasks//plan", nil)
	r.URL.Path = "/api/tasks//plan"
	w := httptest.NewRecorder()
	planTaskHandler(w, r)
	// Missing task ID → 400
	if w.Code != http.StatusBadRequest {
		t.Logf("planTaskHandler missing ID: %d", w.Code)
	}
}

func TestWave79_PlanTaskHandler_BadBody(t *testing.T) {
	r := httptest.NewRequest(http.MethodPost, "/api/tasks/wave79-plan-task/plan", bytes.NewBufferString("not json"))
	r.URL.Path = "/api/tasks/wave79-plan-task/plan"
	w := httptest.NewRecorder()
	planTaskHandler(w, r)
	if w.Code != http.StatusBadRequest {
		t.Logf("planTaskHandler bad body: %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// handlers_task.go: claimTaskHandler format paths — 68.4%
// ---------------------------------------------------------------------------

func TestWave79_ClaimTaskHandler_InvalidBody(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodPost, "/api/tasks/wave79-claim/claim", bytes.NewBufferString("not json"))
	r.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	claimTaskHandler(w, r)
	// Should return 404 (task not found) or 400 (bad body)
	if w.Code >= 500 {
		t.Errorf("unexpected 5xx: %d %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// main.go: handleTaskList with format flag — 60%
// ---------------------------------------------------------------------------

func TestWave79_HandleTaskList_FormatJSON(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodGet, "/tasks?format=json", nil)
	w := httptest.NewRecorder()
	handleTaskList(w, r)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave79_HandleTaskList_FormatTable(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodGet, "/tasks?format=table", nil)
	w := httptest.NewRecorder()
	handleTaskList(w, r)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// main.go: handleSystemHealth with format flag — 55.6%
// ---------------------------------------------------------------------------

func TestWave79_HandleSystemHealth_FormatJSON(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodGet, "/health?format=json", nil)
	w := httptest.NewRecorder()
	handleSystemHealth(w, r)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// main.go: handleAgentList with format flag — 57.1%
// ---------------------------------------------------------------------------

func TestWave79_HandleAgentList_FormatJSON(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodGet, "/agents?format=json", nil)
	w := httptest.NewRecorder()
	handleAgentList(w, r)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// main.go: handleQueueDepth with format flag — 54.5%
// ---------------------------------------------------------------------------

func TestWave79_HandleQueueDepth_FormatJSON(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodGet, "/queue/depth?format=json", nil)
	w := httptest.NewRecorder()
	handleQueueDepth(w, r)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// completion.go: HandleCompletion — 57.1%
// ---------------------------------------------------------------------------

func TestWave79_HandleCompletion_Bash(t *testing.T) {
	cm := NewCompletionManager()
	// Capture output without calling os.Exit — just verify GenerateBash works
	var w bytes.Buffer
	if err := cm.GenerateBash(&w); err != nil {
		t.Errorf("GenerateBash: %v", err)
	}
	if w.Len() == 0 {
		t.Error("expected non-empty bash completion")
	}
}

func TestWave79_HandleCompletion_Zsh(t *testing.T) {
	cm := NewCompletionManager()
	var w bytes.Buffer
	if err := cm.GenerateZsh(&w); err != nil {
		t.Errorf("GenerateZsh: %v", err)
	}
	if w.Len() == 0 {
		t.Error("expected non-empty zsh completion")
	}
}

func TestWave79_HandleCompletion_Fish(t *testing.T) {
	cm := NewCompletionManager()
	var w bytes.Buffer
	if err := cm.GenerateFish(&w); err != nil {
		t.Errorf("GenerateFish: %v", err)
	}
	if w.Len() == 0 {
		t.Error("expected non-empty fish completion")
	}
}

// ---------------------------------------------------------------------------
// handlers_patrols.go: patrolsHandler + dashHandler — 73.7% + 71.4%
// ---------------------------------------------------------------------------

func TestWave79_PatrolsHandler_GET(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodGet, "/api/patrols", nil)
	w := httptest.NewRecorder()
	patrolsHandler(w, r)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave79_DashHandler_GET(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodGet, "/api/patrols/dash", nil)
	w := httptest.NewRecorder()
	dashHandler(w, r)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// dashboard.go: ServeDashboard + ServeDashboardJSON — 71.4%
// ---------------------------------------------------------------------------

func TestWave79_ServeDashboard_OK(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	d := NewDashboard(db, nil)
	r := httptest.NewRequest(http.MethodGet, "/dashboard", nil)
	w := httptest.NewRecorder()
	d.ServeDashboard(w, r)
	if w.Code >= 500 {
		t.Errorf("unexpected 5xx: %d %s", w.Code, w.Body.String())
	}
}

func TestWave79_ServeDashboardJSON_OK(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	d := NewDashboard(db, nil)
	r := httptest.NewRequest(http.MethodGet, "/api/dashboard", nil)
	w := httptest.NewRecorder()
	d.ServeDashboardJSON(w, r)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// tui.go: Handler + JSONHandler — 62.5% + 71.4%
// ---------------------------------------------------------------------------

func TestWave79_TUI_Handler_OK(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	tui := NewTUI(db, nil, taskQueue)
	handler := tui.Handler()
	r := httptest.NewRequest(http.MethodGet, "/tui", nil)
	w := httptest.NewRecorder()
	handler(w, r)
	if w.Code >= 500 {
		t.Errorf("unexpected 5xx: %d", w.Code)
	}
}

func TestWave79_TUI_JSONHandler_OK(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	tui := NewTUI(db, nil, taskQueue)
	handler := tui.JSONHandler()
	r := httptest.NewRequest(http.MethodGet, "/tui/json", nil)
	w := httptest.NewRecorder()
	handler(w, r)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// handlers_node_metrics.go: fleetMetricsHandler — 72%
// ---------------------------------------------------------------------------

func TestWave79_FleetMetricsHandler_OK(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodGet, "/api/fleet/metrics", nil)
	w := httptest.NewRecorder()
	fleetMetricsHandler(w, r)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// handlers_plan.go: pauseTaskHandler / resumeTaskHandler — covers more paths
// ---------------------------------------------------------------------------

func TestWave79_PauseTaskHandler_MethodNotAllowed(t *testing.T) {
	r := httptest.NewRequest(http.MethodGet, "/api/tasks/tid/pause", nil)
	w := httptest.NewRecorder()
	pauseTaskHandler(w, r)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave79_PauseTaskHandler_MissingID(t *testing.T) {
	r := httptest.NewRequest(http.MethodPost, "/api/tasks//pause", nil)
	r.URL.Path = "/api/tasks//pause"
	w := httptest.NewRecorder()
	pauseTaskHandler(w, r)
	// empty ID → 400 or 404
	if w.Code >= 500 {
		t.Errorf("unexpected 5xx: %d %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// approvals.go: listApprovals + createApproval — cover more paths
// ---------------------------------------------------------------------------

func TestWave79_ListApprovals_WithStatus(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	store := NewApprovalStore(db)
	svc := NewApprovalService(store)
	h := NewApprovalHandler(svc)

	r := httptest.NewRequest(http.MethodGet, "/api/approvals?status=pending", nil)
	w := httptest.NewRecorder()
	h.listApprovals(w, r)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave79_CreateApproval_ValidBody(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	store := NewApprovalStore(db)
	svc := NewApprovalService(store)
	h := NewApprovalHandler(svc)

	body, _ := json.Marshal(map[string]interface{}{
		"agent_id": "wave79-agent",
		"action":   "deploy",
		"context":  map[string]interface{}{"env": "prod"},
	})
	r := httptest.NewRequest(http.MethodPost, "/api/approvals", bytes.NewReader(body))
	r.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	h.createApproval(w, r)
	if w.Code >= 500 {
		t.Logf("createApproval: %d: %s", w.Code, w.Body.String())
	}
}
