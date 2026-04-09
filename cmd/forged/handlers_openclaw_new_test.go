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
	"path/filepath"
	"testing"
)

// ---------------------------------------------------------------------------
// POST /api/openclaw/dispatch
// ---------------------------------------------------------------------------

// TestOpenClawDispatch_ValidRequest verifies that a well-formed dispatch
// request creates a queued task without claiming any specific agent.
func TestOpenClawDispatch_ValidRequest(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	body, _ := json.Marshal(OpenClawDispatchRequest{
		Message:       "fix the login bug",
		Priority:      7,
		PreferredNode: "sati",
		PreferredRole: "backend",
		SourceChannel: "telegram",
		ProductKey:    "interview-simulator",
	})
	req := httptest.NewRequest(http.MethodPost, "/api/openclaw/dispatch", bytes.NewReader(body))
	rr := httptest.NewRecorder()

	openclawHandler(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", rr.Code, rr.Body.String())
	}

	var resp OpenClawDispatchResponse
	if err := json.NewDecoder(rr.Body).Decode(&resp); err != nil {
		t.Fatalf("decode response: %v", err)
	}

	if resp.TaskID == "" {
		t.Error("expected non-empty task_id")
	}
	// OpenClaw is intake-only: status must be "queued", NOT "dispatched".
	if resp.Status != "queued" {
		t.Errorf("expected status 'queued', got '%s'", resp.Status)
	}

	// Verify the task exists and is NOT claimed by any agent.
	task, err := taskQueue.GetTask(context.Background(), resp.TaskID)
	if err != nil {
		t.Fatalf("GetTask: %v", err)
	}
	if task.AssignedTo != "" {
		t.Errorf("expected task not assigned (intake-only), got assigned_to=%q", task.AssignedTo)
	}
	if task.Status != TaskStatusQueued {
		t.Errorf("expected task status 'queued', got '%s'", task.Status)
	}
	if task.Priority != 7 {
		t.Errorf("expected priority 7, got %d", task.Priority)
	}

	// Verify origin metadata was persisted in task_events.
	db := getDBConn()
	if db == nil {
		t.Fatal("expected test DB connection")
	}
	var payload string
	err = db.QueryRow(
		`SELECT payload FROM task_events WHERE task_id = ? AND event_type = 'task.queued.openclaw'`,
		resp.TaskID,
	).Scan(&payload)
	if err != nil {
		t.Fatalf("query task_events for origin metadata: %v", err)
	}
	var meta map[string]interface{}
	if err := json.Unmarshal([]byte(payload), &meta); err != nil {
		t.Fatalf("unmarshal metadata payload: %v", err)
	}
	if meta["origin"] != "openclaw" {
		t.Errorf("expected origin=openclaw in metadata, got %v", meta["origin"])
	}
	if meta["preferred_node"] != "sati" {
		t.Errorf("expected preferred_node=sati, got %v", meta["preferred_node"])
	}
	if meta["preferred_role"] != "backend" {
		t.Errorf("expected preferred_role=backend, got %v", meta["preferred_role"])
	}
	if meta["source_channel"] != "telegram" {
		t.Errorf("expected source_channel=telegram, got %v", meta["source_channel"])
	}
	if meta["product_key"] != "interview-simulator" {
		t.Errorf("expected product_key=interview-simulator, got %v", meta["product_key"])
	}
}

// TestOpenClawDispatch_MinimalRequest verifies that only message is required
// and optional routing hints are accepted as empty values.
func TestOpenClawDispatch_MinimalRequest(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	body, _ := json.Marshal(map[string]string{
		"message": "update docs",
	})
	req := httptest.NewRequest(http.MethodPost, "/api/openclaw/dispatch", bytes.NewReader(body))
	rr := httptest.NewRecorder()

	openclawHandler(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", rr.Code, rr.Body.String())
	}

	var resp OpenClawDispatchResponse
	json.NewDecoder(rr.Body).Decode(&resp)

	if resp.Status != "queued" {
		t.Errorf("expected status 'queued', got '%s'", resp.Status)
	}

	task, err := taskQueue.GetTask(context.Background(), resp.TaskID)
	if err != nil {
		t.Fatalf("GetTask: %v", err)
	}
	// Priority omitted — should default to 5.
	if task.Priority != 5 {
		t.Errorf("expected default priority 5, got %d", task.Priority)
	}
	// Task must not be claimed.
	if task.AssignedTo != "" {
		t.Errorf("expected no assignment (intake-only), got %q", task.AssignedTo)
	}
}

// TestOpenClawDispatch_DefaultPriority verifies that omitting priority defaults to 5.
func TestOpenClawDispatch_DefaultPriority(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	body, _ := json.Marshal(map[string]string{
		"message": "update docs",
	})
	req := httptest.NewRequest(http.MethodPost, "/api/openclaw/dispatch", bytes.NewReader(body))
	rr := httptest.NewRecorder()

	openclawHandler(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", rr.Code, rr.Body.String())
	}

	var resp OpenClawDispatchResponse
	json.NewDecoder(rr.Body).Decode(&resp)

	task, err := taskQueue.GetTask(context.Background(), resp.TaskID)
	if err != nil {
		t.Fatalf("GetTask: %v", err)
	}
	if task.Priority != 5 {
		t.Errorf("expected default priority 5, got %d", task.Priority)
	}
}

// TestOpenClawDispatch_MissingMessage verifies that an empty message returns 400.
func TestOpenClawDispatch_MissingMessage(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	body, _ := json.Marshal(map[string]string{
		"preferred_node": "sati",
	})
	req := httptest.NewRequest(http.MethodPost, "/api/openclaw/dispatch", bytes.NewReader(body))
	rr := httptest.NewRecorder()

	openclawHandler(rr, req)

	if rr.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", rr.Code)
	}
}

func TestOpenClawDispatch_InvalidJSON(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/openclaw/dispatch", bytes.NewReader([]byte("not-json")))
	rr := httptest.NewRecorder()

	openclawHandler(rr, req)

	if rr.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", rr.Code)
	}
}

// ---------------------------------------------------------------------------
// POST /api/openclaw/notify
// ---------------------------------------------------------------------------

func TestOpenClawNotify_LogChannel(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	body, _ := json.Marshal(OpenClawNotifyRequest{
		Channel: "log",
		Message: "deployment started",
		Level:   "info",
	})
	req := httptest.NewRequest(http.MethodPost, "/api/openclaw/notify", bytes.NewReader(body))
	rr := httptest.NewRecorder()

	openclawHandler(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", rr.Code, rr.Body.String())
	}

	var resp OpenClawNotifyResponse
	if err := json.NewDecoder(rr.Body).Decode(&resp); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if !resp.OK {
		t.Error("expected ok=true")
	}
	if resp.Channel != "log" {
		t.Errorf("expected channel 'log', got '%s'", resp.Channel)
	}

	// Verify event was persisted.
	db := getDBConn()
	if db == nil {
		t.Fatal("expected test DB connection")
	}
	var count int
	if err := db.QueryRow(`SELECT COUNT(*) FROM task_events WHERE event_type = 'openclaw.notify'`).Scan(&count); err != nil {
		t.Fatalf("count events: %v", err)
	}
	if count < 1 {
		t.Errorf("expected at least 1 notify event, got %d", count)
	}
}

func TestOpenClawNotify_DefaultChannel(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Channel omitted — should default to "log".
	body, _ := json.Marshal(map[string]string{
		"message": "hello",
	})
	req := httptest.NewRequest(http.MethodPost, "/api/openclaw/notify", bytes.NewReader(body))
	rr := httptest.NewRecorder()

	openclawHandler(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", rr.Code, rr.Body.String())
	}

	var resp OpenClawNotifyResponse
	json.NewDecoder(rr.Body).Decode(&resp)

	if resp.Channel != "log" {
		t.Errorf("expected default channel 'log', got '%s'", resp.Channel)
	}
}

func TestOpenClawNotify_MissingMessage(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	body, _ := json.Marshal(map[string]string{
		"channel": "log",
	})
	req := httptest.NewRequest(http.MethodPost, "/api/openclaw/notify", bytes.NewReader(body))
	rr := httptest.NewRecorder()

	openclawHandler(rr, req)

	if rr.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", rr.Code)
	}
}

func TestOpenClawNotify_InvalidJSON(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/openclaw/notify", bytes.NewReader([]byte("bad")))
	rr := httptest.NewRecorder()

	openclawHandler(rr, req)

	if rr.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", rr.Code)
	}
}

// ---------------------------------------------------------------------------
// GET /api/openclaw/portfolio
// ---------------------------------------------------------------------------

// writeTestPortfolioYAML writes a minimal portfolio-state.yaml to a temp dir
// and sets FORGE_ROOT to point at it.  Returns the temp dir path.
func writeTestPortfolioYAML(t *testing.T) string {
	t.Helper()
	root := t.TempDir()
	dir := filepath.Join(root, "config", "portfolio")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	content := `version: "1"
updated: "2026-03-16"
north_star: "Test north star"
products:
  - key: "product-a"
    name: "Product A"
    domain: "example-com"
    repo_path: "example-com/product-a"
    stage: "measure"
    status: "active"
    owner: "prya-lead"
    icp: "developers"
    current_mrr: 1000
    target_mrr: 5000
    deploy_ready: true
    analytics_ready: true
    billing_ready: true
    next_gate: "content marketing"
    next_action: "publish posts"
    primary_metric: "weekly active users"
    primary_risk: "low traffic"
  - key: "product-b"
    name: "Product B"
    domain: "example-com"
    repo_path: "example-com/product-b"
    stage: "build"
    status: "active"
    owner: "sati"
    icp: "founders"
    current_mrr: 200
    target_mrr: 3000
    deploy_ready: false
    analytics_ready: false
    billing_ready: false
    next_gate: ""
    next_action: "ship MVP"
    primary_metric: "sign-ups"
    primary_risk: "scope creep"
`
	if err := os.WriteFile(filepath.Join(dir, "portfolio-state.yaml"), []byte(content), 0o644); err != nil {
		t.Fatalf("write yaml: %v", err)
	}
	return root
}

func TestOpenClawPortfolio_ReturnsValidJSON(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	root := writeTestPortfolioYAML(t)
	t.Setenv("FORGE_ROOT", root)

	req := httptest.NewRequest(http.MethodGet, "/api/openclaw/portfolio", nil)
	rr := httptest.NewRecorder()

	openclawHandler(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", rr.Code, rr.Body.String())
	}

	var result map[string]interface{}
	if err := json.NewDecoder(rr.Body).Decode(&result); err != nil {
		t.Fatalf("decode response: %v", err)
	}

	// Verify top-level fields are present.
	for _, field := range []string{"version", "updated", "north_star", "products", "total_mrr", "blocked_count"} {
		if _, ok := result[field]; !ok {
			t.Errorf("expected field '%s' in response", field)
		}
	}

	// Verify products array.
	products, ok := result["products"].([]interface{})
	if !ok {
		t.Fatalf("expected 'products' to be an array")
	}
	if len(products) != 2 {
		t.Errorf("expected 2 products, got %d", len(products))
	}
}

func TestOpenClawPortfolio_ComputedTotalMRR(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	root := writeTestPortfolioYAML(t)
	t.Setenv("FORGE_ROOT", root)

	req := httptest.NewRequest(http.MethodGet, "/api/openclaw/portfolio", nil)
	rr := httptest.NewRecorder()

	openclawHandler(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", rr.Code)
	}

	var result map[string]interface{}
	json.NewDecoder(rr.Body).Decode(&result)

	// product-a: 1000 + product-b: 200 = 1200
	totalMRR, ok := result["total_mrr"].(float64)
	if !ok {
		t.Fatalf("expected total_mrr to be a number, got %T", result["total_mrr"])
	}
	if totalMRR != 1200 {
		t.Errorf("expected total_mrr=1200, got %v", totalMRR)
	}
}

func TestOpenClawPortfolio_ComputedBlockedCount(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	root := writeTestPortfolioYAML(t)
	t.Setenv("FORGE_ROOT", root)

	req := httptest.NewRequest(http.MethodGet, "/api/openclaw/portfolio", nil)
	rr := httptest.NewRecorder()

	openclawHandler(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", rr.Code)
	}

	var result map[string]interface{}
	json.NewDecoder(rr.Body).Decode(&result)

	// Only product-a has a non-empty next_gate; product-b has "".
	blockedCount, ok := result["blocked_count"].(float64)
	if !ok {
		t.Fatalf("expected blocked_count to be a number, got %T", result["blocked_count"])
	}
	if blockedCount != 1 {
		t.Errorf("expected blocked_count=1, got %v", blockedCount)
	}
}

func TestOpenClawPortfolio_MissingFile(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Point FORGE_ROOT to an empty dir with no portfolio-state.yaml.
	t.Setenv("FORGE_ROOT", t.TempDir())

	req := httptest.NewRequest(http.MethodGet, "/api/openclaw/portfolio", nil)
	rr := httptest.NewRecorder()

	openclawHandler(rr, req)

	if rr.Code != http.StatusInternalServerError {
		t.Errorf("expected 500, got %d", rr.Code)
	}
}

func TestOpenClawPortfolio_WrongMethod(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/openclaw/portfolio", nil)
	rr := httptest.NewRecorder()

	openclawHandler(rr, req)

	// POST /portfolio: method not allowed at the portfolio handler level.
	if rr.Code != http.StatusNotFound {
		t.Errorf("expected 404 (POST not routed to portfolio), got %d", rr.Code)
	}
}

// TestOpenClawPortfolio_DirectMethodNotAllowed calls openclawPortfolioHandler
// directly with a non-GET method to cover its method-check branch.
func TestOpenClawPortfolio_DirectMethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/openclaw/portfolio", nil)
	rr := httptest.NewRecorder()
	openclawPortfolioHandler(rr, req)
	if rr.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405 for non-GET, got %d", rr.Code)
	}
}

// TestOpenClawDispatch_NilTaskQueue verifies that openclawDispatchHandler returns
// 500 when the global taskQueue is nil (covers the nil-guard branch).
func TestOpenClawDispatch_NilTaskQueue(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Temporarily nil the task queue to exercise the nil-guard branch.
	orig := taskQueue
	taskQueue = nil
	defer func() { taskQueue = orig }()

	body, _ := json.Marshal(OpenClawDispatchRequest{
		Message: "test message",
	})
	req := httptest.NewRequest(http.MethodPost, "/api/openclaw/dispatch", bytes.NewReader(body))
	rr := httptest.NewRecorder()
	openclawDispatchHandler(rr, req)

	if rr.Code != http.StatusInternalServerError {
		t.Errorf("expected 500 with nil taskQueue, got %d", rr.Code)
	}
}

// TestOpenClawPortfolio_InvalidYAML verifies that the handler returns 500 when
// the portfolio-state.yaml file exists but contains malformed YAML.
func TestOpenClawPortfolio_InvalidYAML(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	root := t.TempDir()
	cfgDir := filepath.Join(root, "config", "portfolio")
	if err := os.MkdirAll(cfgDir, 0755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	// Write deliberately invalid YAML (unclosed bracket).
	if err := os.WriteFile(filepath.Join(cfgDir, "portfolio-state.yaml"), []byte(":\ninvalid: [unclosed"), 0644); err != nil {
		t.Fatalf("write yaml: %v", err)
	}
	t.Setenv("FORGE_ROOT", root)

	req := httptest.NewRequest(http.MethodGet, "/api/openclaw/portfolio", nil)
	rr := httptest.NewRecorder()
	openclawPortfolioHandler(rr, req)

	if rr.Code != http.StatusInternalServerError {
		t.Errorf("expected 500 for invalid YAML, got %d", rr.Code)
	}
}

// TestOpenClawNotify_MethodNotAllowed covers the method-check branch inside
// openclawNotifyHandler (GET to /notify should return 405).
func TestOpenClawNotify_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/openclaw/notify", nil)
	rr := httptest.NewRecorder()
	openclawNotifyHandler(rr, req)
	if rr.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405 for GET /notify, got %d", rr.Code)
	}
}

// TestOpenClawNotify_NilDB covers the nil-DB branch inside openclawNotifyHandler
// where db is nil so the persistence block is skipped but handler returns 200.
func TestOpenClawNotify_NilDB(t *testing.T) {
	origDB := getDBConn()
	setDBConn(nil)
	defer setDBConn(origDB)

	body, _ := json.Marshal(OpenClawNotifyRequest{
		Channel: "test",
		Message: "hello",
		Level:   "info",
	})
	req := httptest.NewRequest(http.MethodPost, "/api/openclaw/notify", bytes.NewReader(body))
	rr := httptest.NewRecorder()
	openclawNotifyHandler(rr, req)

	if rr.Code != http.StatusOK {
		t.Errorf("expected 200 with nil DB, got %d", rr.Code)
	}
}

// TestOpenClawDispatch_ShortMessage verifies that a message under 5 chars returns 400.
func TestOpenClawDispatch_ShortMessage(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	body, _ := json.Marshal(map[string]string{
		"message": "test", // exactly 4 chars — should fail
	})
	req := httptest.NewRequest(http.MethodPost, "/api/openclaw/dispatch", bytes.NewReader(body))
	rr := httptest.NewRecorder()

	openclawHandler(rr, req)

	if rr.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for 4-char message, got %d: %s", rr.Code, rr.Body.String())
	}
}

// TestOpenClawChat_ShortMessage verifies that a chat text under 5 chars returns 400.
func TestOpenClawChat_ShortMessage(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	body, _ := json.Marshal(map[string]string{
		"from": "telegram:user",
		"text": "hi", // 2 chars — should fail
	})
	req := httptest.NewRequest(http.MethodPost, "/api/openclaw/chat", bytes.NewReader(body))
	rr := httptest.NewRecorder()

	openclawHandler(rr, req)

	if rr.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for short chat text, got %d: %s", rr.Code, rr.Body.String())
	}
}

// TestOpenClawDispatch_ValidMessage verifies that a message of exactly 5 chars
// is accepted and returns a queued task (boundary at new minimum).
func TestOpenClawDispatch_ValidMessage(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	body, _ := json.Marshal(map[string]string{
		"message": "12345", // exactly 5 chars — should pass
	})
	req := httptest.NewRequest(http.MethodPost, "/api/openclaw/dispatch", bytes.NewReader(body))
	rr := httptest.NewRecorder()

	openclawHandler(rr, req)

	if rr.Code != http.StatusOK {
		t.Errorf("expected 200 for 5-char message, got %d: %s", rr.Code, rr.Body.String())
	}

	var resp OpenClawDispatchResponse
	if err := json.NewDecoder(rr.Body).Decode(&resp); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if resp.TaskID == "" {
		t.Error("expected non-empty task_id")
	}
	if resp.Status != "queued" {
		t.Errorf("expected status 'queued', got '%s'", resp.Status)
	}
}

// TestOpenClawDispatchHandler_EnqueueFails exercises the enqueue error path
// (handlers_openclaw_new.go:130.70,134.3) when the DB is closed so Enqueue fails.
func TestOpenClawDispatchHandler_EnqueueFails(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Close DB after setup so Enqueue fails.
	db := getDBConn()
	db.Close()

	body, _ := json.Marshal(OpenClawDispatchRequest{
		Message:  "fix the login bug",
		Priority: 5,
	})
	req := httptest.NewRequest(http.MethodPost, "/api/openclaw/dispatch", bytes.NewReader(body))
	rr := httptest.NewRecorder()
	openclawDispatchHandler(rr, req)

	if rr.Code != http.StatusInternalServerError {
		t.Errorf("expected 500 for enqueue failure in openclawDispatchHandler, got %d: %s", rr.Code, rr.Body.String())
	}
}
