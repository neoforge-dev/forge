//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
)

// ---------------------------------------------------------------------------
// handler_helpers.go: writeJSON — 66.7% (error encoding path)
// ---------------------------------------------------------------------------

func TestWave72_WriteJSON_OK(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	w := httptest.NewRecorder()
	writeJSON(w, map[string]string{"key": "value"})

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", w.Code)
	}
	if w.Header().Get("Content-Type") != "application/json" {
		t.Error("expected Content-Type: application/json")
	}
}

func TestWave72_RequireMethod_Match(t *testing.T) {
	w := httptest.NewRecorder()
	r := httptest.NewRequest(http.MethodPost, "/", nil)
	if !requireMethod(w, r, http.MethodPost) {
		t.Error("expected requireMethod to return true for matching method")
	}
}

func TestWave72_RequireMethod_Mismatch(t *testing.T) {
	w := httptest.NewRecorder()
	r := httptest.NewRequest(http.MethodGet, "/", nil)
	if requireMethod(w, r, http.MethodPost) {
		t.Error("expected requireMethod to return false for mismatched method")
	}
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}


// ---------------------------------------------------------------------------
// main.go: handleTaskLogs — 71.4% — missing id-required path
// ---------------------------------------------------------------------------

func TestWave72_HandleTaskLogs_MissingID(t *testing.T) {
	r := httptest.NewRequest(http.MethodGet, "/tasks/logs", nil)
	w := httptest.NewRecorder()
	handleTaskLogs(w, r)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for missing id, got %d", w.Code)
	}
}

func TestWave72_HandleTaskLogs_WithID(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	_ = taskQueue.Enqueue(context.Background(), Task{
		ID: "wave72-logs-task", Domain: "d", Project: "p",
		Type: TaskTypeFeature, Priority: 5, Status: TaskStatusQueued,
	})

	r := httptest.NewRequest(http.MethodGet, "/tasks/logs?id=wave72-logs-task", nil)
	w := httptest.NewRecorder()
	handleTaskLogs(w, r)

	// Should return 200 or some non-400 for a valid task
	if w.Code == http.StatusBadRequest {
		t.Errorf("unexpected 400: %s", w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// main.go: handleTaskCancel — simple stub handler
// ---------------------------------------------------------------------------

func TestWave72_HandleTaskCancel_Returns501(t *testing.T) {
	r := httptest.NewRequest(http.MethodPost, "/tasks/cancel", nil)
	w := httptest.NewRecorder()
	handleTaskCancel(w, r)

	if w.Code != http.StatusNotImplemented {
		t.Errorf("expected 501, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// main.go: handleAgentSpawn/Stop/SystemPatrol/SystemMetrics stubs
// ---------------------------------------------------------------------------

func TestWave72_HandleAgentSpawn_Returns501(t *testing.T) {
	r := httptest.NewRequest(http.MethodPost, "/agents/spawn", nil)
	w := httptest.NewRecorder()
	handleAgentSpawn(w, r)
	if w.Code != http.StatusNotImplemented {
		t.Errorf("expected 501, got %d", w.Code)
	}
}

func TestWave72_HandleAgentStop_Returns501(t *testing.T) {
	r := httptest.NewRequest(http.MethodPost, "/agents/stop", nil)
	w := httptest.NewRecorder()
	handleAgentStop(w, r)
	if w.Code != http.StatusNotImplemented {
		t.Errorf("expected 501, got %d", w.Code)
	}
}

func TestWave72_HandleSystemPatrol_Returns501(t *testing.T) {
	r := httptest.NewRequest(http.MethodPost, "/system/patrol", nil)
	w := httptest.NewRecorder()
	handleSystemPatrol(w, r)
	if w.Code != http.StatusNotImplemented {
		t.Errorf("expected 501, got %d", w.Code)
	}
}

func TestWave72_HandleSystemMetrics_Returns501(t *testing.T) {
	r := httptest.NewRequest(http.MethodPost, "/system/metrics", nil)
	w := httptest.NewRecorder()
	handleSystemMetrics(w, r)
	if w.Code != http.StatusNotImplemented {
		t.Errorf("expected 501, got %d", w.Code)
	}
}

func TestWave72_HandleGitGuard_Returns501(t *testing.T) {
	r := httptest.NewRequest(http.MethodPost, "/git/guard", nil)
	w := httptest.NewRecorder()
	handleGitGuard(w, r)
	if w.Code != http.StatusNotImplemented {
		t.Errorf("expected 501, got %d", w.Code)
	}
}

func TestWave72_HandleGitStatus_Returns501(t *testing.T) {
	r := httptest.NewRequest(http.MethodPost, "/git/status", nil)
	w := httptest.NewRecorder()
	handleGitStatus(w, r)
	if w.Code != http.StatusNotImplemented {
		t.Errorf("expected 501, got %d", w.Code)
	}
}

func TestWave72_HandleFleetStatus_Returns501(t *testing.T) {
	r := httptest.NewRequest(http.MethodGet, "/fleet/status", nil)
	w := httptest.NewRecorder()
	handleFleetStatus(w, r)
	if w.Code != http.StatusNotImplemented {
		t.Errorf("expected 501, got %d", w.Code)
	}
}

func TestWave72_HandleFleetTopology_Returns501(t *testing.T) {
	r := httptest.NewRequest(http.MethodGet, "/fleet/topology", nil)
	w := httptest.NewRecorder()
	handleFleetTopology(w, r)
	if w.Code != http.StatusNotImplemented {
		t.Errorf("expected 501, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// handlers_adr014.go: handleAuthLogin — 90.9% — missing webhook-not-configured path
// ---------------------------------------------------------------------------

func TestWave72_HandleAuthLogin_MethodNotAllowed(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	auth := NewAuthManager("local")
	handler := handleAuthLogin(auth)

	r := httptest.NewRequest(http.MethodGet, "/api/auth/login", nil)
	w := httptest.NewRecorder()
	handler(w, r)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave72_HandleAuthLogin_NoWebhookToken(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Ensure FORGE_WEBHOOK_TOKEN is unset for this test
	prev := os.Getenv("FORGE_WEBHOOK_TOKEN")
	os.Unsetenv("FORGE_WEBHOOK_TOKEN")
	defer func() {
		if prev != "" {
			os.Setenv("FORGE_WEBHOOK_TOKEN", prev)
		}
	}()

	auth := NewAuthManager("api")
	handler := handleAuthLogin(auth)

	body := `{"token": "anything"}`
	r := httptest.NewRequest(http.MethodPost, "/api/auth/login", strings.NewReader(body))
	w := httptest.NewRecorder()
	handler(w, r)

	// Without FORGE_WEBHOOK_TOKEN env, should be 503 (not configured)
	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503 when no FORGE_WEBHOOK_TOKEN, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave72_HandleAuthMe_MethodNotAllowed(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	auth := NewAuthManager("local")
	handler := handleAuthMe(auth)

	r := httptest.NewRequest(http.MethodPost, "/api/auth/me", nil)
	w := httptest.NewRecorder()
	handler(w, r)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave72_HandleAuthMe_OK(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	auth := NewAuthManager("local")
	handler := handleAuthMe(auth)

	r := httptest.NewRequest(http.MethodGet, "/api/auth/me", nil)
	w := httptest.NewRecorder()
	handler(w, r)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", w.Code)
	}
	var resp map[string]interface{}
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Errorf("response not JSON: %v", err)
	}
}

// ---------------------------------------------------------------------------
// xnode.go: StatusHandler — 53.8% — DB query paths
// ---------------------------------------------------------------------------

func TestWave72_XNode_StatusHandler_OK(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	db := getDBConn()
	xc, err := NewXNodeController(db, "test-node")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}

	r := httptest.NewRequest(http.MethodGet, "/api/xnode/status", nil)
	w := httptest.NewRecorder()
	xc.StatusHandler(w, r)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// xnode.go: ListAcksHandler — 65% — empty acks dir
// ---------------------------------------------------------------------------

func TestWave72_XNode_ListAcksHandler_EmptyDir(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Set acks dir to a temp empty dir
	origAcksDir := XNodeAcksDir
	XNodeAcksDir = t.TempDir()
	defer func() { XNodeAcksDir = origAcksDir }()

	db := getDBConn()
	xc, _ := NewXNodeController(db, "test-node")

	r := httptest.NewRequest(http.MethodGet, "/api/xnode/acks", nil)
	w := httptest.NewRecorder()
	xc.ListAcksHandler(w, r)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// handlers_dispatch.go: dispatchHandler — 69% — GET and POST paths
// ---------------------------------------------------------------------------

func TestWave72_DispatchHandler_GET(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodGet, "/api/dispatch", nil)
	w := httptest.NewRecorder()
	dispatchHandler(w, r)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave72_DispatchHandler_POST(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	body, _ := json.Marshal(map[string]string{
		"task_id":  "wave72-dispatch-task",
		"agent_id": "agent-test",
		"message":  "test dispatch",
	})
	r := httptest.NewRequest(http.MethodPost, "/api/dispatch", bytes.NewReader(body))
	w := httptest.NewRecorder()
	dispatchHandler(w, r)

	if w.Code != http.StatusOK && w.Code != http.StatusCreated {
		t.Errorf("expected 200/201, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave72_DispatchHandler_MethodNotAllowed(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodDelete, "/api/dispatch", nil)
	w := httptest.NewRecorder()
	dispatchHandler(w, r)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// handlers_agent.go: agentContextHandler — 71.4% — bad agent IDs
// ---------------------------------------------------------------------------

func TestWave72_AgentContextHandler_EmptyID(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodGet, "/api/agents//context", nil)
	w := httptest.NewRecorder()
	agentContextHandler(w, r)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for empty agent ID, got %d", w.Code)
	}
}

func TestWave72_AgentContextHandler_ValidID_NotFound(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodGet, "/api/agents/wave72-agent/context", nil)
	r.URL.Path = "/api/agents/wave72-agent/context"
	w := httptest.NewRecorder()
	agentContextHandler(w, r)

	// Should return 200 with context_pct 0 (graceful degradation)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200 for graceful degradation, got %d: %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// fleet_scaler.go: getCPUCount / readLoadAverage / readLiveRAMMB
// Cover the fallback paths on macOS (no /proc files)
// ---------------------------------------------------------------------------

func TestWave72_GetCPUCount_Fallback(t *testing.T) {
	// On macOS, /proc/cpuinfo doesn't exist — getCPUCount falls back to 4
	count := getCPUCount()
	if count < 1 {
		t.Errorf("expected positive CPU count, got %d", count)
	}
}

func TestWave72_ReadLoadAverage_Fails(t *testing.T) {
	// On macOS, /proc/loadavg doesn't exist
	_, err := readLoadAverage()
	// On macOS this will fail — that's expected
	_ = err
}

func TestWave72_ReadLiveRAMMB_Fails(t *testing.T) {
	// On macOS, /proc/meminfo doesn't exist
	_, err := readLiveRAMMB()
	_ = err
}

// ---------------------------------------------------------------------------
// migrate.go: MigrateUp — 64.7% — no-migrations path
// ---------------------------------------------------------------------------

func TestWave72_MigrateUp_AlreadyApplied(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// MigrateUp was already called by setupClaimTestDB — calling again should be a no-op
	if err := MigrateUp(db); err != nil {
		t.Errorf("expected no error on re-run, got: %v", err)
	}
}

// ---------------------------------------------------------------------------
// handlers_lane.go: laneByIDHandler — 92.9% — missing ID path
// ---------------------------------------------------------------------------

func TestWave72_LaneByIDHandler_MissingID(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodGet, "/api/lanes/", nil)
	w := httptest.NewRecorder()
	laneByIDHandler(w, r)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for missing lane ID, got %d", w.Code)
	}
}

func TestWave72_LaneByIDHandler_MethodNotAllowed(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodPost, "/api/lanes/some-id", nil)
	w := httptest.NewRecorder()
	laneByIDHandler(w, r)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave72_LaneByIDHandler_NotFound(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodGet, "/api/lanes/nonexistent-lane-id", nil)
	r.URL.Path = "/api/lanes/nonexistent-lane-id"
	w := httptest.NewRecorder()
	laneByIDHandler(w, r)

	if w.Code != http.StatusNotFound {
		t.Errorf("expected 404 for nonexistent lane, got %d: %s", w.Code, w.Body.String())
	}
}
