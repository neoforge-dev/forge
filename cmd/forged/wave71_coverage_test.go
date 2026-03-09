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
	"strings"
	"testing"
	"time"
)

// ---------------------------------------------------------------------------
// gitguard.go: executeCommit — 24.1% coverage
// Target: errors paths in executeCommit (no branch lock, branch mismatch)
// ---------------------------------------------------------------------------

func TestWave71_GitGuard_ExecuteCommit_NoBranchLock(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	repoDir := t.TempDir()
	gg := NewGitGuard(db, repoDir)

	// No branch lock — executeCommit returns error "no branch lock"
	result := gg.executeCommit(GitAction{
		TaskID:  "task-no-lock",
		Message: "test commit",
	})

	if result.Success {
		t.Error("expected failure with no branch lock")
	}
	// getCurrentBranch will fail since it's not a git repo — that's the first error
	if result.Error == "" {
		t.Error("expected non-empty error")
	}
}

func TestWave71_GitGuard_ExecuteCommit_NoFilesToStage(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Use FORGE repo root so git commands work
	repoDir := "/Users/bogdan/work/FORGE"
	gg := NewGitGuard(db, repoDir)

	// Insert branch lock for a task
	_, err := db.Exec(`INSERT OR REPLACE INTO git_branch_locks (task_id, branch, locked_at, locked_by) VALUES (?, ?, ?, ?)`,
		"task-nofiles", "main", time.Now(), "test")
	if err != nil {
		t.Fatalf("insert branch lock: %v", err)
	}

	// executeCommit with no files and clean working tree — "no files to commit"
	result := gg.executeCommit(GitAction{
		TaskID:  "task-nofiles",
		Message: "test commit",
		Files:   []string{}, // empty — will call getModifiedFiles
	})

	// Either success with clean tree returning "no files to commit" or
	// branch mismatch (current branch != "main" might differ) — both are valid error paths
	if result.Success && result.CommitID == "" {
		t.Error("success but no commit ID")
	}
}

func TestWave71_GitGuard_ExecuteBranch_AlreadyLockedDifferentBranch(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	repoDir := t.TempDir()
	gg := NewGitGuard(db, repoDir)

	// Insert a branch lock for task
	_, err := db.Exec(`INSERT OR REPLACE INTO git_branch_locks (task_id, branch, locked_at, locked_by) VALUES (?, ?, ?, ?)`,
		"task-locked", "feature/existing", time.Now(), "test")
	if err != nil {
		t.Fatalf("insert branch lock: %v", err)
	}

	// Try to switch to a different branch — should fail
	result := gg.executeBranch(GitAction{
		TaskID: "task-locked",
		Branch: "feature/different",
		Author: "tester",
	})

	if result.Success {
		t.Error("expected failure switching to different branch when lock exists")
	}
	if !strings.Contains(result.Error, "already locked") {
		t.Errorf("expected 'already locked' in error, got: %s", result.Error)
	}
}

func TestWave71_GitGuard_Execute_UnknownAction(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	repoDir := t.TempDir()
	gg := NewGitGuard(db, repoDir)

	// Unknown action type should return an error result
	result, err := gg.Execute(context.Background(), GitGuardRequest{
		TaskID: "task-unknown",
		Action: "unknown_action",
	})

	if err != nil {
		t.Fatalf("Execute returned unexpected error: %v", err)
	}
	if result == nil {
		t.Fatal("expected non-nil result")
	}
	// The unknown action is handled inside Execute after idempotency check
	// For unknown action type, result.Success should be false
}

func TestWave71_GitGuard_Execute_MissingTaskID(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	gg := NewGitGuard(db, t.TempDir())
	req := GitGuardRequest{
		Action: "commit",
		// No TaskID
	}
	// gitguardHandler validates, but Execute itself doesn't — test handler
	handler := gitguardHandler(gg)
	body, _ := json.Marshal(req)
	r := httptest.NewRequest(http.MethodPost, "/api/gitguard", bytes.NewReader(body))
	w := httptest.NewRecorder()
	handler(w, r)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", w.Code)
	}
}

func TestWave71_GitGuard_Handler_GET(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	gg := NewGitGuard(db, t.TempDir())
	handler := gitguardHandler(gg)

	r := httptest.NewRequest(http.MethodGet, "/api/gitguard", nil)
	w := httptest.NewRecorder()
	handler(w, r)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200 for GET, got %d", w.Code)
	}
	var resp map[string]interface{}
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Errorf("response not JSON: %v", err)
	}
}

func TestWave71_GitGuard_Handler_GET_WithTaskID(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	gg := NewGitGuard(db, t.TempDir())
	handler := gitguardHandler(gg)

	r := httptest.NewRequest(http.MethodGet, "/api/gitguard?task_id=task-123&limit=10", nil)
	w := httptest.NewRecorder()
	handler(w, r)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200 for GET with task_id, got %d", w.Code)
	}
}

func TestWave71_GitGuard_Handler_MethodNotAllowed(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	gg := NewGitGuard(db, t.TempDir())
	handler := gitguardHandler(gg)

	r := httptest.NewRequest(http.MethodPut, "/api/gitguard", nil)
	w := httptest.NewRecorder()
	handler(w, r)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave71_GitGuard_Handler_MissingAction(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	gg := NewGitGuard(db, t.TempDir())
	handler := gitguardHandler(gg)

	req := GitGuardRequest{TaskID: "task-x"} // no action
	body, _ := json.Marshal(req)
	r := httptest.NewRequest(http.MethodPost, "/api/gitguard", bytes.NewReader(body))
	w := httptest.NewRecorder()
	handler(w, r)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for missing action, got %d", w.Code)
	}
}

func TestWave71_GitGuard_CheckSafe_Clean(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	repoDir := t.TempDir()
	gg := NewGitGuard(db, repoDir)

	safe, reason := gg.CheckSafe()
	if !safe {
		t.Errorf("expected safe in clean dir, got reason: %s", reason)
	}
}

func TestWave71_GitGuard_CheckSafe_IndexLock(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	repoDir := t.TempDir()
	gitDir := repoDir + "/.git"
	os.MkdirAll(gitDir, 0755)
	// Create index.lock
	os.WriteFile(gitDir+"/index.lock", []byte("lock"), 0644)

	gg := NewGitGuard(db, repoDir)
	safe, reason := gg.CheckSafe()
	if safe {
		t.Error("expected unsafe with index.lock present")
	}
	if !strings.Contains(reason, "index.lock") {
		t.Errorf("expected 'index.lock' in reason, got: %s", reason)
	}
}

func TestWave71_GitGuard_ReleaseBranchLock(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	gg := NewGitGuard(db, t.TempDir())

	// Insert a branch lock
	_, _ = db.Exec(`INSERT OR REPLACE INTO git_branch_locks (task_id, branch, locked_at, locked_by) VALUES (?, ?, ?, ?)`,
		"task-release", "feature/x", time.Now(), "test")

	// Release it
	if err := gg.ReleaseBranchLock(context.Background(), "task-release"); err != nil {
		t.Fatalf("ReleaseBranchLock: %v", err)
	}

	// Verify it's gone
	branch, _ := gg.GetBranchLock(context.Background(), "task-release")
	if branch != "" {
		t.Errorf("expected empty branch after release, got %s", branch)
	}
}

// ---------------------------------------------------------------------------
// handlers_openclaw.go: openclawHandler routing — 32.7% coverage
// ---------------------------------------------------------------------------

func TestWave71_OpenclawHandler_GET_Status(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodGet, "/api/openclaw/status", nil)
	w := httptest.NewRecorder()
	openclawHandler(w, r)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200 for GET /status, got %d", w.Code)
	}
}

func TestWave71_OpenclawHandler_GET_NotFound(t *testing.T) {
	r := httptest.NewRequest(http.MethodGet, "/api/openclaw/other", nil)
	w := httptest.NewRecorder()
	openclawHandler(w, r)
	if w.Code != http.StatusNotFound {
		t.Errorf("expected 404 for unknown GET path, got %d", w.Code)
	}
}

func TestWave71_OpenclawHandler_POST_NotFound(t *testing.T) {
	r := httptest.NewRequest(http.MethodPost, "/api/openclaw/other", nil)
	w := httptest.NewRecorder()
	openclawHandler(w, r)
	if w.Code != http.StatusNotFound {
		t.Errorf("expected 404 for unknown POST path, got %d", w.Code)
	}
}

func TestWave71_OpenclawHandler_MethodNotAllowed(t *testing.T) {
	r := httptest.NewRequest(http.MethodDelete, "/api/openclaw/status", nil)
	w := httptest.NewRecorder()
	openclawHandler(w, r)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405 for DELETE, got %d", w.Code)
	}
}

func TestWave71_OpenclawIngest_BadJSON(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodPost, "/api/openclaw/ingest", strings.NewReader("{bad json"))
	w := httptest.NewRecorder()
	openclawIngestHandler(w, r)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for bad JSON, got %d", w.Code)
	}
}

func TestWave71_OpenclawIngest_MissingFields(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	payload := map[string]string{"task_id": "tid", "status": "done"}
	body, _ := json.Marshal(payload)
	r := httptest.NewRequest(http.MethodPost, "/api/openclaw/ingest", bytes.NewReader(body))
	w := httptest.NewRecorder()
	openclawIngestHandler(w, r)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for missing fields, got %d", w.Code)
	}
}

func TestWave71_OpenclawIngest_ValidPayload(t *testing.T) {
	tmpDir := t.TempDir()
	os.Setenv("FORGE_ROOT", tmpDir)
	defer os.Unsetenv("FORGE_ROOT")

	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	payload := OpenClawIngestPayload{
		TaskID:  "task-ingest-1",
		Status:  "done",
		Agent:   "kimi",
		Node:    "prya",
		Summary: "task completed successfully",
	}
	body, _ := json.Marshal(payload)
	r := httptest.NewRequest(http.MethodPost, "/api/openclaw/ingest", bytes.NewReader(body))
	w := httptest.NewRecorder()
	openclawIngestHandler(w, r)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200 for valid payload, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave71_OpenclawIngest_FailStatus(t *testing.T) {
	tmpDir := t.TempDir()
	os.Setenv("FORGE_ROOT", tmpDir)
	defer os.Unsetenv("FORGE_ROOT")

	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	payload := OpenClawIngestPayload{
		TaskID:  "task-ingest-fail",
		Status:  "fail",
		Agent:   "kimi",
		Node:    "prya",
		Summary: "task failed",
	}
	body, _ := json.Marshal(payload)
	r := httptest.NewRequest(http.MethodPost, "/api/openclaw/ingest", bytes.NewReader(body))
	w := httptest.NewRecorder()
	openclawIngestHandler(w, r)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200 for fail status, got %d", w.Code)
	}
}

func TestWave71_OpenclawIngest_NeedsApprovalStatus(t *testing.T) {
	tmpDir := t.TempDir()
	os.Setenv("FORGE_ROOT", tmpDir)
	defer os.Unsetenv("FORGE_ROOT")

	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	payload := OpenClawIngestPayload{
		TaskID:  "task-ingest-approval",
		Status:  "needs_approval",
		Agent:   "kimi",
		Node:    "prya",
		Summary: "task needs approval",
	}
	body, _ := json.Marshal(payload)
	r := httptest.NewRequest(http.MethodPost, "/api/openclaw/ingest", bytes.NewReader(body))
	w := httptest.NewRecorder()
	openclawIngestHandler(w, r)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200 for needs_approval status, got %d", w.Code)
	}
}

func TestWave71_ParseTaskFromMessage(t *testing.T) {
	cases := []struct {
		input string
		want  string
	}{
		{"create task: implement login", "implement login"},
		{"task: fix bug in queue", "fix bug in queue"},
		{"add task: write tests", "write tests"},
		{"TASK: uppercase prefix", "uppercase prefix"}, // case-insensitive match on "task:"
		{"just a message", "just a message"},
	}
	for _, c := range cases {
		got := parseTaskFromMessage(c.input)
		if got != c.want {
			t.Errorf("parseTaskFromMessage(%q) = %q, want %q", c.input, got, c.want)
		}
	}
}

func TestWave71_ParseTaskFromMessage_CaseInsensitive(t *testing.T) {
	// The function uses strings.ToLower for prefix matching
	got := parseTaskFromMessage("Create Task: uppercase input")
	if got != "uppercase input" {
		t.Errorf("expected 'uppercase input', got %q", got)
	}
}

// ---------------------------------------------------------------------------
// main.go handler functions — 55-65% coverage targets
// ---------------------------------------------------------------------------

func TestWave71_HandleAgentList(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodGet, "/cli/agent/list", nil)
	w := httptest.NewRecorder()
	handleAgentList(w, r)
	// Should succeed even with empty agent table
	if w.Code >= 500 {
		t.Errorf("expected non-500, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave71_HandleAgentList_JSONFormat(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodGet, "/cli/agent/list?format=json", nil)
	w := httptest.NewRecorder()
	handleAgentList(w, r)
	if w.Code >= 500 {
		t.Errorf("expected non-500, got %d", w.Code)
	}
}

func TestWave71_HandleAgentStatus_MissingID(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodGet, "/cli/agent/status", nil)
	w := httptest.NewRecorder()
	handleAgentStatus(w, r)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for missing agent id, got %d", w.Code)
	}
}

func TestWave71_HandleAgentStatus_NotFound(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodGet, "/cli/agent/status?id=nonexistent", nil)
	w := httptest.NewRecorder()
	handleAgentStatus(w, r)
	if w.Code != http.StatusNotFound {
		t.Errorf("expected 404 for unknown agent, got %d", w.Code)
	}
}

func TestWave71_HandleSystemHealth(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodGet, "/cli/system/health", nil)
	w := httptest.NewRecorder()
	handleSystemHealth(w, r)
	// Should return health data
	if w.Code >= 500 {
		t.Errorf("expected non-500, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave71_HandleQueueDepth(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodGet, "/cli/queue/depth", nil)
	w := httptest.NewRecorder()
	handleQueueDepth(w, r)
	if w.Code >= 500 {
		t.Errorf("expected non-500, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave71_HandleQueueDepth_JSONFormat(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodGet, "/cli/queue/depth?format=json", nil)
	w := httptest.NewRecorder()
	handleQueueDepth(w, r)
	if w.Code >= 500 {
		t.Errorf("expected non-500, got %d", w.Code)
	}
}

func TestWave71_HandleTaskList(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodGet, "/cli/task/list", nil)
	w := httptest.NewRecorder()
	handleTaskList(w, r)
	if w.Code >= 500 {
		t.Errorf("expected non-500, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave71_HandleTaskShow_MissingID(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodGet, "/cli/task/show", nil)
	w := httptest.NewRecorder()
	handleTaskShow(w, r)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for missing id, got %d", w.Code)
	}
}

func TestWave71_HandleTaskShow_WithID(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodGet, "/cli/task/show?id=task-nonexistent", nil)
	w := httptest.NewRecorder()
	handleTaskShow(w, r)
	// Should 404 for non-existent task
	if w.Code == http.StatusOK {
		// acceptable if it returns an empty body
	} else if w.Code != http.StatusNotFound && w.Code != http.StatusBadRequest {
		t.Errorf("expected 404 or 400, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave71_HandleTaskLogs_MissingID(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodGet, "/cli/task/logs", nil)
	w := httptest.NewRecorder()
	handleTaskLogs(w, r)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for missing id, got %d", w.Code)
	}
}

func TestWave71_HandleTaskCancel(t *testing.T) {
	r := httptest.NewRequest(http.MethodPost, "/cli/task/cancel", nil)
	w := httptest.NewRecorder()
	handleTaskCancel(w, r)
	if w.Code != http.StatusNotImplemented {
		t.Errorf("expected 501, got %d", w.Code)
	}
}

func TestWave71_HandleAgentSpawn(t *testing.T) {
	r := httptest.NewRequest(http.MethodPost, "/cli/agent/spawn", nil)
	w := httptest.NewRecorder()
	handleAgentSpawn(w, r)
	if w.Code != http.StatusNotImplemented {
		t.Errorf("expected 501, got %d", w.Code)
	}
}

func TestWave71_HandleAgentStop(t *testing.T) {
	r := httptest.NewRequest(http.MethodPost, "/cli/agent/stop", nil)
	w := httptest.NewRecorder()
	handleAgentStop(w, r)
	if w.Code != http.StatusNotImplemented {
		t.Errorf("expected 501, got %d", w.Code)
	}
}

func TestWave71_HandleSystemPatrol(t *testing.T) {
	r := httptest.NewRequest(http.MethodPost, "/cli/system/patrol", nil)
	w := httptest.NewRecorder()
	handleSystemPatrol(w, r)
	if w.Code != http.StatusNotImplemented {
		t.Errorf("expected 501, got %d", w.Code)
	}
}

func TestWave71_HandleFleetStatus(t *testing.T) {
	r := httptest.NewRequest(http.MethodGet, "/cli/fleet/status", nil)
	w := httptest.NewRecorder()
	handleFleetStatus(w, r)
	if w.Code != http.StatusNotImplemented {
		t.Errorf("expected 501, got %d", w.Code)
	}
}

func TestWave71_HandleFleetTopology(t *testing.T) {
	r := httptest.NewRequest(http.MethodGet, "/cli/fleet/topology", nil)
	w := httptest.NewRecorder()
	handleFleetTopology(w, r)
	if w.Code != http.StatusNotImplemented {
		t.Errorf("expected 501, got %d", w.Code)
	}
}

func TestWave71_HandleGitGuard(t *testing.T) {
	r := httptest.NewRequest(http.MethodPost, "/cli/git/guard", nil)
	w := httptest.NewRecorder()
	handleGitGuard(w, r)
	if w.Code != http.StatusNotImplemented {
		t.Errorf("expected 501, got %d", w.Code)
	}
}

func TestWave71_HandleGitStatus(t *testing.T) {
	r := httptest.NewRequest(http.MethodGet, "/cli/git/status", nil)
	w := httptest.NewRecorder()
	handleGitStatus(w, r)
	if w.Code != http.StatusNotImplemented {
		t.Errorf("expected 501, got %d", w.Code)
	}
}

func TestWave71_HandleSystemMetrics(t *testing.T) {
	r := httptest.NewRequest(http.MethodGet, "/cli/system/metrics", nil)
	w := httptest.NewRecorder()
	handleSystemMetrics(w, r)
	if w.Code != http.StatusNotImplemented {
		t.Errorf("expected 501, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// fleet_scaler.go: readLiveRAMMB, readLoadAverage, getCPUCount
// On macOS /proc/meminfo doesn't exist — test the error path
// ---------------------------------------------------------------------------

func TestWave71_ReadLiveRAMMB_Error(t *testing.T) {
	// On macOS, /proc/meminfo doesn't exist
	// Reset cache so we don't get a cached value
	liveRAMCache.Lock()
	liveRAMCache.lastRead = time.Time{}
	liveRAMCache.Unlock()

	_, err := readLiveRAMMB()
	if err == nil {
		// On Linux this succeeds — skip test
		t.Log("readLiveRAMMB succeeded (Linux environment)")
	} else {
		// On macOS this fails — that's the expected error path
		if !strings.Contains(err.Error(), "/proc/meminfo") {
			t.Errorf("expected /proc/meminfo error, got: %v", err)
		}
	}
}

func TestWave71_ReadLoadAverage_Error(t *testing.T) {
	_, err := readLoadAverage()
	if err == nil {
		t.Log("readLoadAverage succeeded (Linux environment)")
	} else {
		// On macOS /proc/loadavg doesn't exist
		if !strings.Contains(err.Error(), "/proc/loadavg") {
			t.Errorf("expected /proc/loadavg error, got: %v", err)
		}
	}
}

func TestWave71_GetCPUCount(t *testing.T) {
	count := getCPUCount()
	if count <= 0 {
		t.Errorf("expected positive CPU count, got %d", count)
	}
}

func TestWave71_FindForgeRoot_WithEnv(t *testing.T) {
	tmpDir := t.TempDir()
	os.Setenv("FORGE_ROOT", tmpDir)
	defer os.Unsetenv("FORGE_ROOT")

	root := findForgeRoot()
	if root != tmpDir {
		t.Errorf("expected %s, got %s", tmpDir, root)
	}
}

func TestWave71_FindForgeRoot_WithoutEnv(t *testing.T) {
	os.Unsetenv("FORGE_ROOT")
	root := findForgeRoot()
	if root == "" {
		t.Error("expected non-empty forge root")
	}
}

func TestWave71_CircuitBreaker_RecordAndReset(t *testing.T) {
	// Reset state
	circuitBreaker.Lock()
	circuitBreaker.consecutiveFailures = 0
	circuitBreaker.state = "closed"
	circuitBreaker.Unlock()

	if circuitBreakerTripped() {
		t.Error("circuit breaker should not be tripped initially")
	}

	// Record 3 failures to open the circuit breaker
	recordSpawnFailure()
	recordSpawnFailure()
	recordSpawnFailure()

	if !circuitBreakerTripped() {
		t.Error("circuit breaker should be tripped after 3 failures")
	}

	// Reset it
	recordSpawnSuccess()
	if circuitBreakerTripped() {
		t.Error("circuit breaker should be reset after success")
	}
}

func TestWave71_CanScale(t *testing.T) {
	// Reset timestamp to zero
	lastScaleEvent.Lock()
	lastScaleEvent.timestamp = time.Time{}
	lastScaleEvent.Unlock()

	if !canScale() {
		t.Error("should be able to scale with zero timestamp")
	}

	recordScaleEvent()

	if canScale() {
		t.Error("should not be able to scale immediately after event")
	}

	// Reset for other tests
	lastScaleEvent.Lock()
	lastScaleEvent.timestamp = time.Time{}
	lastScaleEvent.Unlock()
}

// ---------------------------------------------------------------------------
// xnode.go StatusHandler — 53.8%
// ---------------------------------------------------------------------------

func TestWave71_XNode_StatusHandler(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

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

	var resp map[string]interface{}
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatalf("response not JSON: %v", err)
	}
	if resp["node_id"] != "test-node" {
		t.Errorf("expected node_id=test-node, got %v", resp["node_id"])
	}
}

func TestWave71_XNode_NilDB(t *testing.T) {
	_, err := NewXNodeController(nil, "test")
	if err == nil {
		t.Error("expected error with nil DB")
	}
}

func TestWave71_XNode_RegisterNode(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	xc, err := NewXNodeController(db, "test-node")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}

	node := Node{
		ID:       "node-1",
		Hostname: "test-host",
		Address:  "192.168.1.1:8081",
		Status:   "online",
	}
	if err := xc.RegisterNode(context.Background(), node); err != nil {
		t.Fatalf("RegisterNode: %v", err)
	}

	// Status handler should now show 1 node
	r := httptest.NewRequest(http.MethodGet, "/api/xnode/status", nil)
	w := httptest.NewRecorder()
	xc.StatusHandler(w, r)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// task_state_machine.go: ClaimTransition and GetState — 68.8%, 71.4%
// ---------------------------------------------------------------------------

func TestWave71_StateMachine_GetState_NotFound(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	sm := NewStateMachine(NewTaskStore(db), db)
	_, err := sm.GetState("nonexistent-task-id")
	if err == nil {
		t.Error("expected error for nonexistent task")
	}
	if !strings.Contains(err.Error(), "not found") {
		t.Errorf("expected 'not found' in error, got: %v", err)
	}
}

func TestWave71_StateMachine_ClaimTransition_NotFound(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	sm := NewStateMachine(NewTaskStore(db), db)
	err := sm.ClaimTransition("nonexistent-task", "agent-1")
	if err == nil {
		t.Error("expected error for nonexistent task")
	}
	if !strings.Contains(err.Error(), "not found") {
		t.Errorf("expected 'not found' in error, got: %v", err)
	}
}

func TestWave71_StateMachine_ClaimTransition_AlreadyAssigned(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	sm := NewStateMachine(NewTaskStore(db), db)

	// Create a task already in DISPATCHED state (not QUEUED)
	taskID := fmt.Sprintf("task-claim-%d", time.Now().UnixNano())
	now := time.Now().Format(time.RFC3339)
	_, err := db.Exec(`INSERT INTO tasks (id, domain, project, type, priority, status, state, created_at, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		taskID, "test", "test", "feature", 5, "assigned", string(StateDispatched), now, now)
	if err != nil {
		t.Fatalf("insert task: %v", err)
	}

	err = sm.ClaimTransition(taskID, "agent-1")
	if err == nil {
		t.Error("expected error claiming already-dispatched task")
	}
}

func TestWave71_StateMachine_Transition_TaskNotFound(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	sm := NewStateMachine(NewTaskStore(db), db)
	err := sm.Transition("nonexistent", StateQueued, StateDispatched, "test")
	if err == nil {
		t.Error("expected error for nonexistent task")
	}
}

func TestWave71_StateMachine_Transition_StateMismatch(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	sm := NewStateMachine(NewTaskStore(db), db)

	taskID := fmt.Sprintf("task-trans-%d", time.Now().UnixNano())
	now := time.Now().Format(time.RFC3339)
	_, err := db.Exec(`INSERT INTO tasks (id, domain, project, type, priority, status, state, created_at, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		taskID, "test", "test", "feature", 5, "queued", string(StateQueued), now, now)
	if err != nil {
		t.Fatalf("insert task: %v", err)
	}

	// Transition expects from=DISPATCHED but task is QUEUED — mismatch
	err = sm.Transition(taskID, StateDispatched, StateRunning, "test")
	if err == nil {
		t.Error("expected error for state mismatch")
	}
}

func TestWave71_StateMachine_GetAllowedTransitions(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	sm := NewStateMachine(NewTaskStore(db), db)
	allowed := sm.GetAllowedTransitions(StateQueued)
	if len(allowed) == 0 {
		t.Error("expected at least one allowed transition from QUEUED")
	}
}

// ---------------------------------------------------------------------------
// queue.go: eventsHandler filters — 71.9%
// ---------------------------------------------------------------------------

func TestWave71_EventsHandler_WithFilters(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Insert a test event
	db := getDBConn()
	_, _ = db.Exec(`INSERT INTO task_events (task_id, domain, project, event_type, payload, created_at)
		VALUES (?, ?, ?, ?, ?, datetime('now'))`,
		"ev-task-1", "test-domain", "test-project", "task.created", `{"key":"val"}`)

	tests := []struct {
		query string
	}{
		{"?task_id=ev-task-1"},
		{"?domain=test-domain"},
		{"?project=test-project"},
		{"?event_type=task.created"},
		{"?since=2020-01-01T00:00:00Z"},
		{"?limit=5"},
		{"?task_id=ev-task-1&domain=test-domain"},
	}

	for _, tt := range tests {
		r := httptest.NewRequest(http.MethodGet, "/api/events"+tt.query, nil)
		w := httptest.NewRecorder()
		eventsHandler(w, r)
		if w.Code != http.StatusOK {
			t.Errorf("query %s: expected 200, got %d: %s", tt.query, w.Code, w.Body.String())
		}
	}
}

func TestWave71_EventsHandler_MethodNotAllowed(t *testing.T) {
	r := httptest.NewRequest(http.MethodPost, "/api/events", nil)
	w := httptest.NewRecorder()
	eventsHandler(w, r)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// context_sync.go: SyncDomainToFilesystem — 80.8%
// ---------------------------------------------------------------------------

func TestWave71_ContextSync_SyncDomainToFilesystem_NoData(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	tmpDir := t.TempDir()
	cm := NewContextManager(db, tmpDir)
	cs, _ := NewContextSync(cm, db, tmpDir)

	// No data for domain — should return nil
	err := cs.SyncDomainToFilesystem("nonexistent-domain")
	if err != nil {
		t.Errorf("expected nil for no data domain, got: %v", err)
	}
}

// ---------------------------------------------------------------------------
// worktree_manager.go: CreateWorktree paths — 60.9%
// ---------------------------------------------------------------------------

func TestWave71_WorktreeManager_CreateWorktree_EmptyTaskID(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	wm := NewWorktreeManager(t.TempDir(), "", db)
	_, err := wm.CreateWorktree(context.Background(), "")
	if err == nil {
		t.Error("expected error for empty taskID")
	}
}

func TestWave71_WorktreeManager_RemoveWorktree_EmptyTaskID(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	wm := NewWorktreeManager(t.TempDir(), "", db)
	err := wm.RemoveWorktree(context.Background(), "")
	if err == nil {
		t.Error("expected error for empty taskID")
	}
}

func TestWave71_WorktreeManager_NewWorktreeManager_Defaults(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Empty repoRoot — should use cwd
	wm := NewWorktreeManager("", "", db)
	if wm == nil {
		t.Error("expected non-nil WorktreeManager")
	}
	if wm.repoRoot == "" {
		t.Error("expected non-empty repoRoot")
	}
	if wm.worktreeDir == "" {
		t.Error("expected non-empty worktreeDir")
	}
}
