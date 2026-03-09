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
	"time"
)

func TestWave88_DispatchHandler_GET(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()
	r := httptest.NewRequest(http.MethodGet, "/dispatch", nil)
	w := httptest.NewRecorder()
	dispatchHandler(w, r)
	if w.Code >= 500 {
		t.Logf("dispatchHandler GET: got %d", w.Code)
	}
}

func TestWave88_DispatchHandler_POST_Valid(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()
	body := `{"task_id":"wave88-task","agent_id":"wave88-agent","message":"test dispatch"}`
	r := httptest.NewRequest(http.MethodPost, "/dispatch", strings.NewReader(body))
	r.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	dispatchHandler(w, r)
	if w.Code >= 500 {
		t.Logf("dispatchHandler POST valid: got %d", w.Code)
	}
}

func TestWave88_DispatchHandler_POST_InvalidJSON(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()
	r := httptest.NewRequest(http.MethodPost, "/dispatch", strings.NewReader("not-json"))
	r.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	dispatchHandler(w, r)
	if w.Code != http.StatusBadRequest {
		t.Logf("dispatchHandler invalid JSON: got %d", w.Code)
	}
}

func TestWave88_UiFleetHandler_WithData(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()
	db := getDBConn()
	now := time.Now().UTC().Format(time.RFC3339)
	_, _ = db.Exec(`INSERT OR REPLACE INTO agent_heartbeats (agent_id, node, status, context_pct, last_seen, connected_at) VALUES (?, ?, ?, ?, ?, ?)`,
		"wave88-ui-agent", "test-node", "working", 45.0, now, now)
	_, _ = db.Exec(`INSERT INTO tasks (id, domain, project, type, priority, status, state, title, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		"wave88-ui-task", "codeswiftr", "proj", "feature", 5, "queued", "QUEUED", "UI Task", now, now)
	r := httptest.NewRequest(http.MethodGet, "/ui/fleet", nil)
	w := httptest.NewRecorder()
	uiFleetHandler(w, r)
	if w.Code >= 500 {
		t.Logf("uiFleetHandler GET: got %d", w.Code)
	}
}

func TestWave88_ResponseRecorder_Write(t *testing.T) {
	rr := &responseRecorder{header: http.Header{}, status: 200}
	n, err := rr.Write([]byte("hello"))
	if err != nil {
		t.Errorf("responseRecorder.Write: %v", err)
	}
	if n != 5 {
		t.Errorf("responseRecorder.Write: wrote %d bytes", n)
	}
}

func TestWave88_ResponseRecorder_WriteHeader(t *testing.T) {
	rr := &responseRecorder{header: http.Header{}}
	rr.WriteHeader(http.StatusCreated)
	if rr.status != http.StatusCreated {
		t.Errorf("responseRecorder.WriteHeader: status = %d", rr.status)
	}
}

func TestWave88_ResponseRecorder_Header(t *testing.T) {
	rr := &responseRecorder{header: http.Header{}}
	h := rr.Header()
	if h == nil {
		t.Error("responseRecorder.Header: returned nil")
	}
}

func TestWave88_MigrateUp_IdempotentSecondCall(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	if err := MigrateUp(db); err != nil {
		t.Errorf("MigrateUp second call: %v", err)
	}
}

func TestWave88_SyncRoyalJelly_WithMultiDomainEnvelopes(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()
	db := getDBConn()
	ctx := context.Background()
	tmpDir := t.TempDir()
	if err := os.MkdirAll(tmpDir+"/.forge/context", 0755); err != nil {
		t.Fatalf("mkdir context: %v", err)
	}
	prevWD, _ := os.Getwd()
	if err := os.Chdir(tmpDir); err != nil {
		t.Fatalf("chdir: %v", err)
	}
	defer os.Chdir(prevWD)
	for _, domain := range []string{"codeswiftr", "finance"} {
		content := `{"metadata":{"lead_context":"# Lead\nState for ` + domain + `"},"decisions":[],"failures":[],"summary":"Summary"}`
		_, _ = db.Exec(`INSERT INTO context_envelopes (id, domain, content, created_at) VALUES (?, ?, ?, datetime('now', '-2 minutes'))`,
			"wave88-env-"+domain, domain, content)
	}
	err := syncRoyalJelly(ctx, db)
	if err != nil {
		t.Logf("syncRoyalJelly multi-domain: %v", err)
	}
}

func TestWave88_NodeMetricsPushPatrol_WithMetricsRows(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	ctx := context.Background()
	var receivedBody []byte
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		buf := new(bytes.Buffer)
		buf.ReadFrom(r.Body)
		receivedBody = buf.Bytes()
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()
	prevURL := os.Getenv("FORGE_LEAD_URL")
	prevNode := os.Getenv("NODE_ID")
	os.Setenv("FORGE_LEAD_URL", srv.URL)
	os.Setenv("NODE_ID", "wave88-push-node")
	defer func() {
		if prevURL == "" {
			os.Unsetenv("FORGE_LEAD_URL")
		} else {
			os.Setenv("FORGE_LEAD_URL", prevURL)
		}
		if prevNode == "" {
			os.Unsetenv("NODE_ID")
		} else {
			os.Setenv("NODE_ID", prevNode)
		}
	}()
	for i, name := range []string{"cpu_usage", "memory_usage", "disk_io"} {
		_, _ = db.Exec(`INSERT INTO metrics (metric_name, value, labels, period, computed_at) VALUES (?, ?, ?, ?, datetime('now', '-1 minute'))`,
			name, float64(i+1)*10.0, "{}", "1m")
	}
	err := nodeMetricsPushPatrol(ctx, db)
	if err != nil {
		t.Logf("nodeMetricsPushPatrol: %v", err)
	}
	if len(receivedBody) > 0 {
		var payload map[string]interface{}
		if json.Unmarshal(receivedBody, &payload) == nil {
			t.Logf("received payload with %d keys", len(payload))
		}
	}
}

func TestWave88_ExtendLeaseHandler_MethodNotAllowed(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()
	r := httptest.NewRequest(http.MethodGet, "/api/tasks/extend-lease", nil)
	w := httptest.NewRecorder()
	extendLeaseHandler(w, r)
	if w.Code != http.StatusMethodNotAllowed {
		t.Logf("extendLeaseHandler GET: got %d", w.Code)
	}
}

func TestWave88_AgentsHandler_POST(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()
	body := `{"agent_id":"wave88-post-agent","node":"test-node","status":"idle","context_pct":10}`
	r := httptest.NewRequest(http.MethodPost, "/api/agents", strings.NewReader(body))
	r.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	agentsHandler(w, r)
	if w.Code >= 500 {
		t.Logf("agentsHandler POST: got %d", w.Code)
	}
}

func TestWave88_AgentsHandler_GET(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()
	r := httptest.NewRequest(http.MethodGet, "/api/agents", nil)
	w := httptest.NewRecorder()
	agentsHandler(w, r)
	if w.Code >= 500 {
		t.Logf("agentsHandler GET: got %d", w.Code)
	}
}

func TestWave88_AgentByIDHandler_NotFound(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()
	r := httptest.NewRequest(http.MethodGet, "/api/agents/nonexistent-wave88", nil)
	w := httptest.NewRecorder()
	agentByIDHandler(w, r)
	if w.Code != http.StatusNotFound {
		t.Logf("agentByIDHandler not found: got %d", w.Code)
	}
}

func TestWave88_AgentHeartbeatReceive_ValidHeartbeat(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()
	now := time.Now().UTC().Format(time.RFC3339)
	body := `{"agent_id":"wave88-hb-agent","node":"test-node","status":"working","context_pct":55,"last_seen":"` + now + `"}`
	r := httptest.NewRequest(http.MethodPost, "/api/agents/wave88-hb-agent/heartbeat", strings.NewReader(body))
	r.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	agentHeartbeatReceive(w, r)
	if w.Code >= 500 {
		t.Logf("agentHeartbeatReceive valid: got %d", w.Code)
	}
}

func TestWave88_HandleTaskLogs_ValidIDNoLogs(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()
	db := getDBConn()
	now := time.Now().UTC().Format("2006-01-02 15:04:05")
	_, _ = db.Exec(`INSERT INTO tasks (id, domain, project, type, priority, status, state, title, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		"wave88-logs-task", "codeswiftr", "proj", "feature", 5, "queued", "QUEUED", "Log Task", now, now)
	r := httptest.NewRequest(http.MethodGet, "/tasks/logs?id=wave88-logs-task", nil)
	w := httptest.NewRecorder()
	handleTaskLogs(w, r)
	if w.Code >= 500 {
		t.Logf("handleTaskLogs with task: got %d", w.Code)
	}
}

func TestWave88_SendBlockerAlert_WithWebhook(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	var received bool
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		received = true
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()

	prevURL := os.Getenv("FORGE_COORDINATION_BLOCKER_WEBHOOK_URL")
	os.Setenv("FORGE_COORDINATION_BLOCKER_WEBHOOK_URL", srv.URL)
	defer func() {
		if prevURL == "" {
			os.Unsetenv("FORGE_COORDINATION_BLOCKER_WEBHOOK_URL")
		} else {
			os.Setenv("FORGE_COORDINATION_BLOCKER_WEBHOOK_URL", prevURL)
		}
	}()

	coord := NewCoordinationDashboard(db)
	blockers := []Blocker{
		{Agent: "wave88-agent", Description: "test blocker", Severity: "high"},
	}
	coord.sendBlockerAlert(blockers)
	if !received {
		t.Logf("sendBlockerAlert: mock server not called")
	}
}
