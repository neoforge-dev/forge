//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"net/http"
	"net/http/httptest"
	"os"
	"testing"

	"github.com/spf13/cobra"
)

// ---------------------------------------------------------------------------
// patrol.go: confidenceApproveCompletedTasks — 25%
// ---------------------------------------------------------------------------

func TestW74_ConfidenceApproveCompletedTasks_NilStateMachine(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	// Save and nil out stateMachine + globalApprovalService
	prevSM := stateMachine
	prevAS := globalApprovalService
	stateMachine = nil
	globalApprovalService = nil
	defer func() {
		stateMachine = prevSM
		globalApprovalService = prevAS
	}()

	// Should return nil immediately (early return path)
	err := confidenceApproveCompletedTasks(context.Background(), db)
	if err != nil {
		t.Errorf("expected nil error when stateMachine is nil, got: %v", err)
	}
}

func TestW74_ConfidenceApproveCompletedTasks_WithServices_NoTasks(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	// Initialize stateMachine and globalApprovalService
	store := NewTaskStore(db)
	prevSM := stateMachine
	prevAS := globalApprovalService
	stateMachine = NewStateMachine(store, db)
	approvalStore := NewApprovalStore(db)
	globalApprovalService = NewApprovalService(approvalStore)
	defer func() {
		stateMachine = prevSM
		globalApprovalService = prevAS
	}()

	// No COMPLETED tasks in DB — should return nil with zero work done
	err := confidenceApproveCompletedTasks(context.Background(), db)
	if err != nil {
		t.Errorf("expected nil error with no COMPLETED tasks, got: %v", err)
	}
}

func TestW74_ConfidenceApproveCompletedTasks_WithCompletedTask(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	store := NewTaskStore(db)
	prevSM := stateMachine
	prevAS := globalApprovalService
	stateMachine = NewStateMachine(store, db)
	approvalStore := NewApprovalStore(db)
	globalApprovalService = NewApprovalService(approvalStore)
	defer func() {
		stateMachine = prevSM
		globalApprovalService = prevAS
	}()

	// Insert a completed task with state=COMPLETED and old updated_at
	_, err := db.Exec(`
		INSERT INTO tasks (id, domain, project, title, type, priority, status, state, assigned_to, result, created_at, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now', '-2 minutes'), datetime('now', '-2 minutes'))
	`, "w74-conf-task", "test", "proj", "Test Task", "feature", 5, "completed", "COMPLETED", "agent-1", "ok")
	if err != nil {
		t.Fatalf("insert task: %v", err)
	}

	err = confidenceApproveCompletedTasks(context.Background(), db)
	if err != nil {
		t.Errorf("expected nil error, got: %v", err)
	}
}

// ---------------------------------------------------------------------------
// handlers_openclaw.go: openclawEventsHandler — 36.7%
// SSE handler with cancelled context (exits immediately)
// ---------------------------------------------------------------------------

func TestW74_OpenclawEventsHandler_CancelledContext(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	ctx, cancel := context.WithCancel(context.Background())
	cancel() // already cancelled

	req := httptest.NewRequest(http.MethodGet, "/api/openclaw/events", nil).WithContext(ctx)
	w := httptest.NewRecorder()
	openclawEventsHandler(w, req)

	// Should return after seeing cancelled context; 200 with SSE headers or streaming started
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// handlers_ui.go: uiFleetHandler — 50%
// ---------------------------------------------------------------------------

func TestW74_UIFleetHandler_GET(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/ui", nil)
	w := httptest.NewRecorder()
	uiFleetHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
	ct := w.Header().Get("Content-Type")
	if ct == "" {
		t.Error("expected Content-Type header")
	}
}

func TestW74_UIFleetHandler_NilDB(t *testing.T) {
	// Test with nil DB — handler should still return 200 (graceful degradation)
	setDBConn(nil)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/ui", nil)
	w := httptest.NewRecorder()
	uiFleetHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200 with nil DB, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// cli_commands.go: getAgentID — 50%
// ---------------------------------------------------------------------------

func TestW74_GetAgentID_FromFlag(t *testing.T) {
	cmd := &cobra.Command{}
	cmd.Flags().String("agent", "", "agent ID")
	if err := cmd.Flags().Set("agent", "test-agent-42"); err != nil {
		t.Fatalf("set flag: %v", err)
	}

	id, err := getAgentID(cmd)
	if err != nil {
		t.Errorf("expected no error, got: %v", err)
	}
	if id != "test-agent-42" {
		t.Errorf("expected test-agent-42, got %s", id)
	}
}

func TestW74_GetAgentID_FromEnv(t *testing.T) {
	cmd := &cobra.Command{}
	cmd.Flags().String("agent", "", "agent ID")

	prev := os.Getenv("FORGE_AGENT_ID")
	os.Setenv("FORGE_AGENT_ID", "env-agent-99")
	defer func() {
		if prev == "" {
			os.Unsetenv("FORGE_AGENT_ID")
		} else {
			os.Setenv("FORGE_AGENT_ID", prev)
		}
	}()

	id, err := getAgentID(cmd)
	if err != nil {
		t.Errorf("expected no error, got: %v", err)
	}
	if id != "env-agent-99" {
		t.Errorf("expected env-agent-99, got %s", id)
	}
}

func TestW74_GetAgentID_NoSource_ReturnsError(t *testing.T) {
	cmd := &cobra.Command{}
	cmd.Flags().String("agent", "", "agent ID")

	// Ensure no env var and no tmux
	prevAgent := os.Getenv("FORGE_AGENT_ID")
	prevTmux := os.Getenv("TMUX")
	os.Unsetenv("FORGE_AGENT_ID")
	os.Unsetenv("TMUX")
	defer func() {
		if prevAgent != "" {
			os.Setenv("FORGE_AGENT_ID", prevAgent)
		}
		if prevTmux != "" {
			os.Setenv("TMUX", prevTmux)
		}
	}()

	_, err := getAgentID(cmd)
	if err == nil {
		t.Error("expected error when no agent ID source available")
	}
}
