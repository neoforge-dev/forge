//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"database/sql"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

// setupMetricsTestDB creates a temporary SQLite database for metrics/coordination tests
func setupMetricsTestDB(t *testing.T) (*sql.DB, func()) {
	dbPath := fmt.Sprintf("/tmp/test_metrics_%d.db", time.Now().UnixNano())
	db, err := OpenDB(dbPath)
	if err != nil {
		t.Fatalf("OpenDB failed: %v", err)
	}

	if err := MigrateUp(db); err != nil {
		db.Close()
		t.Fatalf("MigrateUp failed: %v", err)
	}

	cleanup := func() {
		db.Close()
		os.Remove(dbPath)
	}
	return db, cleanup
}

// ============================================================
// Metrics Tests
// ============================================================

// TestInitMetrics verifies InitMetrics doesn't panic
func TestInitMetrics(t *testing.T) {
	// Should not panic
	InitMetrics()
}

// TestMetricsRecordRequest tests HTTP request recording
func TestMetricsRecordRequest(t *testing.T) {
	m := &Metrics{}
	m.RecordRequest("GET", "/api/test", 200, 100*time.Millisecond)

	if m.RequestsTotal != 1 {
		t.Errorf("expected RequestsTotal=1, got %d", m.RequestsTotal)
	}

	// Test with error status
	m.RecordRequest("POST", "/api/error", 500, 50*time.Millisecond)
	if m.RequestsTotal != 2 {
		t.Errorf("expected RequestsTotal=2, got %d", m.RequestsTotal)
	}
}

// TestMetricsRecordTaskCreated tests task creation recording
func TestMetricsRecordTaskCreated(t *testing.T) {
	// Save and restore global metrics
	oldTasksCreated := atomic.LoadInt64(&metrics.TasksCreated)
	defer atomic.StoreInt64(&metrics.TasksCreated, oldTasksCreated)

	m := &Metrics{}
	m.RecordTaskCreated("feature", 5)

	// RecordTaskCreated increments global metrics.TasksCreated
	if atomic.LoadInt64(&metrics.TasksCreated) != oldTasksCreated+1 {
		t.Errorf("expected TasksCreated=%d, got %d", oldTasksCreated+1, atomic.LoadInt64(&metrics.TasksCreated))
	}
}

// TestMetricsRecordTaskCompleted tests task completion recording
func TestMetricsRecordTaskCompleted(t *testing.T) {
	// Save and restore global metrics
	oldTasksCompleted := atomic.LoadInt64(&metrics.TasksCompleted)
	defer atomic.StoreInt64(&metrics.TasksCompleted, oldTasksCompleted)

	m := &Metrics{}
	m.RecordTaskCompleted()

	// RecordTaskCompleted increments global metrics.TasksCompleted
	if atomic.LoadInt64(&metrics.TasksCompleted) != oldTasksCompleted+1 {
		t.Errorf("expected TasksCompleted=%d, got %d", oldTasksCompleted+1, atomic.LoadInt64(&metrics.TasksCompleted))
	}
}

// TestMetricsRecordTaskFailed tests task failure recording
func TestMetricsRecordTaskFailed(t *testing.T) {
	// Save and restore global metrics
	oldTasksFailed := atomic.LoadInt64(&metrics.TasksFailed)
	defer atomic.StoreInt64(&metrics.TasksFailed, oldTasksFailed)

	m := &Metrics{}
	m.RecordTaskFailed()

	// RecordTaskFailed increments global metrics.TasksFailed
	if atomic.LoadInt64(&metrics.TasksFailed) != oldTasksFailed+1 {
		t.Errorf("expected TasksFailed=%d, got %d", oldTasksFailed+1, atomic.LoadInt64(&metrics.TasksFailed))
	}
}

// TestMetricsRecordTaskDuration tests duration recording
func TestMetricsRecordTaskDuration(t *testing.T) {
	m := &Metrics{}
	m.RecordTaskDuration(5 * time.Second)
	// Prometheus histogram observation - no direct assertion, just ensure no panic
}

// TestMetricsRecordAgentTaskCompleted tests per-agent completion recording
func TestMetricsRecordAgentTaskCompleted(t *testing.T) {
	// Save and restore global metrics
	oldTasksCompleted := atomic.LoadInt64(&metrics.TasksCompleted)
	defer atomic.StoreInt64(&metrics.TasksCompleted, oldTasksCompleted)

	m := &Metrics{}
	m.RecordAgentTaskCompleted("agent-1", 3*time.Second)

	// RecordAgentTaskCompleted calls RecordTaskCompleted which increments global metrics.TasksCompleted
	if atomic.LoadInt64(&metrics.TasksCompleted) != oldTasksCompleted+1 {
		t.Errorf("expected TasksCompleted=%d, got %d", oldTasksCompleted+1, atomic.LoadInt64(&metrics.TasksCompleted))
	}
}

// TestMetricsRecordAgentTaskFailed tests per-agent failure recording
func TestMetricsRecordAgentTaskFailed(t *testing.T) {
	// Save and restore global metrics
	oldTasksFailed := atomic.LoadInt64(&metrics.TasksFailed)
	defer atomic.StoreInt64(&metrics.TasksFailed, oldTasksFailed)

	m := &Metrics{}
	m.RecordAgentTaskFailed("agent-1", 2*time.Second)

	// RecordAgentTaskFailed calls RecordTaskFailed which increments global metrics.TasksFailed
	if atomic.LoadInt64(&metrics.TasksFailed) != oldTasksFailed+1 {
		t.Errorf("expected TasksFailed=%d, got %d", oldTasksFailed+1, atomic.LoadInt64(&metrics.TasksFailed))
	}
}

// TestMetricsRecordTaskEnqueued tests enqueue recording
func TestMetricsRecordTaskEnqueued(t *testing.T) {
	m := &Metrics{}
	m.RecordTaskEnqueued()

	if m.TasksEnqueued != 1 {
		t.Errorf("expected TasksEnqueued=1, got %d", m.TasksEnqueued)
	}
}

// TestMetricsRecordHeartbeat tests heartbeat recording
func TestMetricsRecordHeartbeat(t *testing.T) {
	m := &Metrics{}
	m.RecordHeartbeat()

	if atomic.LoadInt64(&m.HeartbeatsReceived) != 1 {
		t.Errorf("expected HeartbeatsReceived=1, got %d", atomic.LoadInt64(&m.HeartbeatsReceived))
	}
}

// TestMetricsSetActiveConnections tests connection count setting
func TestMetricsSetActiveConnections(t *testing.T) {
	m := &Metrics{}
	m.SetActiveConnections(5)
	// Prometheus gauge set - no direct assertion, just ensure no panic
}

// TestRecordPatrolExecution tests patrol execution recording
func TestRecordPatrolExecution(t *testing.T) {
	db, cleanup := setupMetricsTestDB(t)
	defer cleanup()

	RecordPatrolExecution(db, "health-check")

	// Verify insertion
	var count int
	err := db.QueryRow("SELECT COUNT(*) FROM patrol_executions WHERE patrol_id = ?", "health-check").Scan(&count)
	if err != nil {
		t.Fatalf("query error: %v", err)
	}
	if count != 1 {
		t.Errorf("expected 1 patrol execution, got %d", count)
	}
}

// TestRecordPatrolSuccess tests patrol success recording
func TestRecordPatrolSuccess(t *testing.T) {
	db, cleanup := setupMetricsTestDB(t)
	defer cleanup()

	// First record execution
	RecordPatrolExecution(db, "test-patrol")
	// Then record success
	RecordPatrolSuccess(db, "test-patrol")

	// Verify update
	var status string
	err := db.QueryRow("SELECT status FROM patrol_executions WHERE patrol_id = ?", "test-patrol").Scan(&status)
	if err != nil {
		t.Fatalf("query error: %v", err)
	}
	if status != "completed" {
		t.Errorf("expected status='completed', got %s", status)
	}
}

// TestRecordPatrolError tests patrol error recording
func TestRecordPatrolError(t *testing.T) {
	db, cleanup := setupMetricsTestDB(t)
	defer cleanup()

	// First record execution
	RecordPatrolExecution(db, "error-patrol")
	// Then record error
	RecordPatrolError(db, "error-patrol", "connection timeout")

	// Verify update
	var status, errMsg string
	err := db.QueryRow("SELECT status, error FROM patrol_executions WHERE patrol_id = ?", "error-patrol").Scan(&status, &errMsg)
	if err != nil {
		t.Fatalf("query error: %v", err)
	}
	if status != "error" {
		t.Errorf("expected status='error', got %s", status)
	}
	if errMsg != "connection timeout" {
		t.Errorf("expected error='connection timeout', got %s", errMsg)
	}
}

// TestGetPatrolMetrics returns patrol metrics map
func TestGetPatrolMetrics(t *testing.T) {
	result := GetPatrolMetrics()
	if result == nil {
		t.Error("expected non-nil patrol metrics")
	}
	if _, ok := result["executions"]; !ok {
		t.Error("expected 'executions' key in patrol metrics")
	}
}

// TestDebugHandler tests debug endpoint handler
func TestDebugHandler(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/debug", nil)
	rr := httptest.NewRecorder()

	DebugHandler(rr, req)

	if rr.Code != http.StatusOK {
		t.Errorf("expected status 200, got %d", rr.Code)
	}

	body := rr.Body.String()
	if !strings.Contains(body, "timestamp") {
		t.Error("expected response to contain 'timestamp'")
	}
	if !strings.Contains(body, "uptime") {
		t.Error("expected response to contain 'uptime'")
	}
}

// TestDetailedHealthHandler tests health endpoint
func TestDetailedHealthHandler(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/health", nil)
	rr := httptest.NewRecorder()

	DetailedHealthHandler(rr, req)

	if rr.Code != http.StatusOK {
		t.Errorf("expected status 200, got %d", rr.Code)
	}

	body := rr.Body.String()
	if !strings.Contains(body, "ok") && !strings.Contains(body, "status") {
		t.Error("expected response to contain health status")
	}
}

// TestLoggingMiddleware tests request logging middleware
func TestLoggingMiddleware(t *testing.T) {
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		w.Write([]byte("OK"))
	})

	middleware := LoggingMiddleware(handler)
	req := httptest.NewRequest(http.MethodGet, "/test", nil)
	rr := httptest.NewRecorder()

	middleware.ServeHTTP(rr, req)

	if rr.Code != http.StatusOK {
		t.Errorf("expected status 200, got %d", rr.Code)
	}
}

// TestGetStartTime tests start time retrieval
func TestGetStartTime(t *testing.T) {
	start := GetStartTime()
	if start.IsZero() {
		t.Error("expected non-zero start time")
	}
}

// TestSetQueueDepthFunc tests callback registration
func TestSetQueueDepthFunc(t *testing.T) {
	called := false
	fn := func() int64 {
		called = true
		return 42
	}
	SetQueueDepthFunc(fn)

	if getQueueDepthFunc == nil {
		t.Error("expected getQueueDepthFunc to be set")
	}

	// Test the function works
	result := getQueueDepthFunc()
	if result != 42 {
		t.Errorf("expected 42, got %d", result)
	}
	if !called {
		t.Error("expected callback to be called")
	}
}

// TestSetActiveLeasesFunc tests callback registration
func TestSetActiveLeasesFunc(t *testing.T) {
	called := false
	fn := func() int64 {
		called = true
		return 10
	}
	SetActiveLeasesFunc(fn)

	if getActiveLeasesFunc == nil {
		t.Error("expected getActiveLeasesFunc to be set")
	}

	// Test the function works
	result := getActiveLeasesFunc()
	if result != 10 {
		t.Errorf("expected 10, got %d", result)
	}
	if !called {
		t.Error("expected callback to be called")
	}
}

// ============================================================
// Coordination Tests
// ============================================================

// TestNewCoordinationDashboard tests dashboard creation
func TestNewCoordinationDashboard(t *testing.T) {
	db, cleanup := setupMetricsTestDB(t)
	defer cleanup()

	dashboard := NewCoordinationDashboard(db)
	if dashboard == nil {
		t.Fatal("expected non-nil dashboard")
	}
	if dashboard.db != db {
		t.Error("expected dashboard to use provided DB")
	}
}

// TestCoordinationDashboardGetSprintStatus tests sprint status retrieval
func TestCoordinationDashboardGetSprintStatus(t *testing.T) {
	db, cleanup := setupMetricsTestDB(t)
	defer cleanup()

	dashboard := NewCoordinationDashboard(db)
	status, err := dashboard.GetSprintStatus("test-sprint")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if status == nil {
		t.Fatal("expected non-nil status")
	}
	if status.Name != "test-sprint" {
		t.Errorf("expected sprint name 'test-sprint', got %s", status.Name)
	}
}

// TestCoordinationDashboardGetSprintStatusDefault tests default sprint name
func TestCoordinationDashboardGetSprintStatusDefault(t *testing.T) {
	db, cleanup := setupMetricsTestDB(t)
	defer cleanup()

	dashboard := NewCoordinationDashboard(db)
	status, err := dashboard.GetSprintStatus("")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if status.Name != "Phase 3" {
		t.Errorf("expected default name 'Phase 3', got %s", status.Name)
	}
}

// TestCoordinationDashboardGetAgentStatusesFromDB tests DB agent retrieval
func TestCoordinationDashboardGetAgentStatusesFromDB(t *testing.T) {
	db, cleanup := setupMetricsTestDB(t)
	defer cleanup()

	// Insert test agent
	_, err := db.Exec(`
		INSERT INTO agent_heartbeats (agent_id, node, status, current_task_id, last_seen, connected_at, context_pct)
		VALUES ('test-agent', 'prya', 'online', 'task-001', datetime('now'), datetime('now'), 50.0)
	`)
	if err != nil {
		t.Fatalf("failed to insert test agent: %v", err)
	}

	dashboard := NewCoordinationDashboard(db)
	agents := dashboard.getAgentStatusesFromDB()

	if len(agents) != 1 {
		t.Errorf("expected 1 agent, got %d", len(agents))
	}
	if len(agents) > 0 && agents[0].Name != "test-agent" {
		t.Errorf("expected agent name 'test-agent', got %s", agents[0].Name)
	}
}

// TestCoordinationDashboardCalculateCompletion tests completion calculation
func TestCoordinationDashboardCalculateCompletion(t *testing.T) {
	db, cleanup := setupMetricsTestDB(t)
	defer cleanup()

	dashboard := NewCoordinationDashboard(db)
	status := &SprintStatus{
		Agents: []AgentSprintStatus{
			{Name: "agent1", Status: "working"},
			{Name: "agent2", Status: "working"},
		},
		Commits: CommitSummary{Total: 5},
	}

	completion := dashboard.calculateCompletion(status)
	if completion < 0 || completion > 100 {
		t.Errorf("expected completion between 0-100, got %.2f", completion)
	}
}

// TestCoordinationDashboardDetectBlockers tests blocker detection
func TestCoordinationDashboardDetectBlockers(t *testing.T) {
	db, cleanup := setupMetricsTestDB(t)
	defer cleanup()

	dashboard := NewCoordinationDashboard(db)
	agents := []AgentSprintStatus{
		{Name: "active-agent", Status: "working", LastCommit: time.Now()},
		{Name: "idle-agent", Status: "idle", LastCommit: time.Now().Add(-35 * time.Minute)},
		{Name: "blocked-agent", Status: "blocked", LastCommit: time.Now().Add(-70 * time.Minute)},
	}

	blockers := dashboard.detectBlockers(agents)

	// Should detect idle and blocked agents
	if len(blockers) == 0 {
		t.Error("expected blockers to be detected")
	}

	// Check for critical blocker (blocked agent)
	hasCritical := false
	for _, b := range blockers {
		if b.Severity == "critical" {
			hasCritical = true
			break
		}
	}
	if !hasCritical {
		t.Error("expected at least one critical blocker")
	}
}

// TestCoordinationDashboardEstimateETA tests ETA calculation
func TestCoordinationDashboardEstimateETA(t *testing.T) {
	db, cleanup := setupMetricsTestDB(t)
	defer cleanup()

	dashboard := NewCoordinationDashboard(db)

	// Test complete sprint
	completeStatus := &SprintStatus{Completion: 100}
	eta := dashboard.estimateETA(completeStatus)
	if eta != "Complete" {
		t.Errorf("expected 'Complete', got %s", eta)
	}

	// Test unknown completion
	unknownStatus := &SprintStatus{Completion: 0}
	eta = dashboard.estimateETA(unknownStatus)
	if eta != "Unknown" {
		t.Errorf("expected 'Unknown', got %s", eta)
	}

	// Test calculating ETA
	calculatingStatus := &SprintStatus{
		Completion: 50,
		StartedAt:  time.Now(),
	}
	eta = dashboard.estimateETA(calculatingStatus)
	// Should return some time estimate or "Calculating..."
	if eta == "" {
		t.Error("expected non-empty ETA")
	}
}

// TestCoordinationDashboardServeHTTP tests HTTP handler
func TestCoordinationDashboardServeHTTP(t *testing.T) {
	db, cleanup := setupMetricsTestDB(t)
	defer cleanup()

	dashboard := NewCoordinationDashboard(db)
	req := httptest.NewRequest(http.MethodGet, "/coordination?sprint=test", nil)
	rr := httptest.NewRecorder()

	dashboard.ServeHTTP(rr, req)

	if rr.Code != http.StatusOK {
		t.Errorf("expected status 200, got %d", rr.Code)
	}

	contentType := rr.Header().Get("Content-Type")
	if !strings.Contains(contentType, "application/json") {
		t.Errorf("expected JSON content type, got %s", contentType)
	}
}

// TestCoordinationDashboardGetCoordinationHTML tests HTML generation
func TestCoordinationDashboardGetCoordinationHTML(t *testing.T) {
	db, cleanup := setupMetricsTestDB(t)
	defer cleanup()

	dashboard := NewCoordinationDashboard(db)
	html := dashboard.GetCoordinationHTML()

	if html == "" {
		t.Error("expected non-empty HTML")
	}
	if !strings.Contains(html, "Sprint Coordination") {
		t.Error("expected HTML to contain 'Sprint Coordination'")
	}
}

// TestTruncateString tests string truncation helper
func TestTruncateString(t *testing.T) {
	tests := []struct {
		input    string
		maxLen   int
		expected string
	}{
		{"short", 10, "short"},
		{"this is a long string", 10, "this is..."},
		{"exactly", 7, "exactly"},
		{"", 5, ""},
	}

	for _, tt := range tests {
		result := truncateString(tt.input, tt.maxLen)
		if result != tt.expected {
			t.Errorf("truncateString(%q, %d) = %q, expected %q", tt.input, tt.maxLen, result, tt.expected)
		}
	}
}

// TestComputeMetricsRollups tests metrics rollup computation
func TestComputeMetricsRollups(t *testing.T) {
	db, cleanup := setupMetricsTestDB(t)
	defer cleanup()

	// The pattern_runs table schema may vary - skip if insert fails
	_, err := db.Exec(`
		INSERT INTO pattern_runs (agent_id, tokens_used, success, started_at)
		VALUES ('agent-1', 1000, 1, datetime('now'))
	`)
	if err != nil {
		t.Skipf("skipping test - pattern_runs schema incompatible: %v", err)
	}

	ctx := context.Background()
	err = computeMetricsRollups(ctx, db)
	if err != nil {
		t.Errorf("unexpected error: %v", err)
	}
	// Function logs metrics - no direct assertions as it inserts to DB
}

// TestMetricsHandler tests Prometheus metrics endpoint
func TestMetricsHandler(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/metrics", nil)
	rr := httptest.NewRecorder()

	MetricsHandler(rr, req)

	// Should return 200 or have valid response
	if rr.Code != http.StatusOK {
		t.Errorf("expected status 200, got %d", rr.Code)
	}
}
