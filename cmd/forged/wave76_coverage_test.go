//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"net/http"
	"net/http/httptest"
	"os"
	"testing"
	"time"
)

// ---------------------------------------------------------------------------
// approvals.go: ListByAgent, ListByTask — 77.8%
// ---------------------------------------------------------------------------

func TestWave76_ListByAgent_Empty(t *testing.T) {
	store, cleanup := setupApprovalsTestDB(t)
	defer cleanup()

	results, err := store.ListByAgent(context.Background(), "ghost-agent", 10)
	if err != nil {
		t.Fatalf("ListByAgent empty: %v", err)
	}
	if len(results) != 0 {
		t.Errorf("expected 0, got %d", len(results))
	}
}

func TestWave76_ListByAgent_WithData(t *testing.T) {
	store, cleanup := setupApprovalsTestDB(t)
	defer cleanup()

	approval := makeApproval("wave76-agent-appr-1")
	approval.AgentID = "agent-wave76"
	_ = store.Create(context.Background(), approval)

	results, err := store.ListByAgent(context.Background(), "agent-wave76", 10)
	if err != nil {
		t.Fatalf("ListByAgent with data: %v", err)
	}
	if len(results) != 1 {
		t.Errorf("expected 1, got %d", len(results))
	}
}

func TestWave76_ListByAgent_NegativeLimit(t *testing.T) {
	store, cleanup := setupApprovalsTestDB(t)
	defer cleanup()

	// Negative limit should use default (50)
	_, err := store.ListByAgent(context.Background(), "agent-wave76", -1)
	if err != nil {
		t.Fatalf("ListByAgent negative limit: %v", err)
	}
}

func TestWave76_ListByTask_Empty(t *testing.T) {
	store, cleanup := setupApprovalsTestDB(t)
	defer cleanup()

	results, err := store.ListByTask(context.Background(), "nonexistent-task", 10)
	if err != nil {
		t.Fatalf("ListByTask empty: %v", err)
	}
	if len(results) != 0 {
		t.Errorf("expected 0, got %d", len(results))
	}
}

func TestWave76_ListByTask_WithData(t *testing.T) {
	store, cleanup := setupApprovalsTestDB(t)
	defer cleanup()

	approval := makeApproval("wave76-task-appr-1")
	taskID := "TASK-WAVE76"
	approval.TaskID = &taskID
	_ = store.Create(context.Background(), approval)

	results, err := store.ListByTask(context.Background(), "TASK-WAVE76", 10)
	if err != nil {
		t.Fatalf("ListByTask with data: %v", err)
	}
	if len(results) != 1 {
		t.Errorf("expected 1, got %d", len(results))
	}
}

// ---------------------------------------------------------------------------
// approvals.go: Approve, Reject — 83.3%
// ---------------------------------------------------------------------------

func TestWave76_Approve_Success(t *testing.T) {
	store, cleanup := setupApprovalsTestDB(t)
	defer cleanup()

	approval := makeApproval("wave76-approve-1")
	_ = store.Create(context.Background(), approval)

	if err := store.Approve(context.Background(), "wave76-approve-1", "user-a"); err != nil {
		t.Fatalf("Approve: %v", err)
	}

	updated, _ := store.Get(context.Background(), "wave76-approve-1")
	if updated.Status != StatusApproved {
		t.Errorf("expected approved, got %s", updated.Status)
	}
}

func TestWave76_Approve_NotFound(t *testing.T) {
	store, cleanup := setupApprovalsTestDB(t)
	defer cleanup()

	if err := store.Approve(context.Background(), "nonexistent-wave76", "user-a"); err == nil {
		t.Error("expected error approving nonexistent approval")
	}
}

func TestWave76_Reject_Success(t *testing.T) {
	store, cleanup := setupApprovalsTestDB(t)
	defer cleanup()

	approval := makeApproval("wave76-reject-1")
	_ = store.Create(context.Background(), approval)

	if err := store.Reject(context.Background(), "wave76-reject-1", "user-b"); err != nil {
		t.Fatalf("Reject: %v", err)
	}

	updated, _ := store.Get(context.Background(), "wave76-reject-1")
	if updated.Status != StatusRejected {
		t.Errorf("expected rejected, got %s", updated.Status)
	}
}

func TestWave76_Reject_NotFound(t *testing.T) {
	store, cleanup := setupApprovalsTestDB(t)
	defer cleanup()

	if err := store.Reject(context.Background(), "nonexistent-wave76-rej", "user-b"); err == nil {
		t.Error("expected error rejecting nonexistent approval")
	}
}

// ---------------------------------------------------------------------------
// approvals.go: ExpireOldApprovals — 83.3%
// ---------------------------------------------------------------------------

func TestWave76_ExpireOldApprovals_None(t *testing.T) {
	store, cleanup := setupApprovalsTestDB(t)
	defer cleanup()

	n, err := store.ExpireOldApprovals(context.Background(), time.Now().Add(-time.Hour))
	if err != nil {
		t.Fatalf("ExpireOldApprovals none: %v", err)
	}
	if n != 0 {
		t.Errorf("expected 0 expired, got %d", n)
	}
}

func TestWave76_ExpireOldApprovals_WithOldRecords(t *testing.T) {
	store, cleanup := setupApprovalsTestDB(t)
	defer cleanup()

	approval := makeApproval("wave76-expire-1")
	_ = store.Create(context.Background(), approval)

	// Expire everything (future cutoff)
	n, err := store.ExpireOldApprovals(context.Background(), time.Now().Add(time.Hour))
	if err != nil {
		t.Fatalf("ExpireOldApprovals with records: %v", err)
	}
	t.Logf("expired %d approvals", n)
}

// ---------------------------------------------------------------------------
// context.go: GenerateEnvelope — 71.4%
// ---------------------------------------------------------------------------

func TestWave76_GenerateEnvelope_Success(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	tmpDir := t.TempDir()
	cm := testContextManager(t, db, tmpDir)

	envelope, err := cm.GenerateEnvelope(context.Background(), "agent-76", "test-domain", "test-project", "TASK-76", "wave76 test")
	if err != nil {
		t.Fatalf("GenerateEnvelope: %v", err)
	}
	if envelope == nil {
		t.Error("expected non-nil envelope")
	}
	if envelope.AgentID != "agent-76" {
		t.Errorf("AgentID = %q, want %q", envelope.AgentID, "agent-76")
	}
}

func TestWave76_GenerateEnvelope_WithContextFiles(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	tmpDir := t.TempDir()
	domainDir := tmpDir + "/test-dom"
	os.MkdirAll(domainDir, 0755)
	os.WriteFile(domainDir+"/lead-context.md", []byte("# Test Domain\n\nContext here."), 0644)

	cm := testContextManager(t, db, tmpDir)
	envelope, err := cm.GenerateEnvelope(context.Background(), "agent-76b", "test-dom", "project-x", "TASK-76b", "with files")
	if err != nil {
		t.Fatalf("GenerateEnvelope with files: %v", err)
	}
	if envelope == nil {
		t.Error("expected non-nil envelope")
	}
}

// ---------------------------------------------------------------------------
// xnode.go: ListNodesHandler — 75.0%
// ---------------------------------------------------------------------------

func TestWave76_ListNodesHandler_Empty(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	xc, _ := NewXNodeController(db, "wave76-node")
	req := httptest.NewRequest(http.MethodGet, "/api/xnode/nodes", nil)
	rr := httptest.NewRecorder()
	xc.ListNodesHandler(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("ListNodesHandler empty: status=%d body=%s", rr.Code, rr.Body.String())
	}
}

func TestWave76_ListNodesHandler_WithNodes(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	_, _ = db.Exec(`INSERT INTO nodes (id, hostname, address, status, last_heartbeat)
		VALUES ('n-wave76', 'prya', '10.0.0.1', 'online', ?)`, time.Now().Format(time.RFC3339))

	xc, _ := NewXNodeController(db, "wave76-node")
	req := httptest.NewRequest(http.MethodGet, "/api/xnode/nodes", nil)
	rr := httptest.NewRecorder()
	xc.ListNodesHandler(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("ListNodesHandler with nodes: status=%d", rr.Code)
	}
}

// ---------------------------------------------------------------------------
// xnode.go: RegisterNode, Heartbeat — 75.0%
// ---------------------------------------------------------------------------

func TestWave76_RegisterNode_Success(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	xc, _ := NewXNodeController(db, "wave76-reg-node")
	ctx := context.Background()
	if err := xc.RegisterNode(ctx, Node{ID: "wave76-new-node", Hostname: "sati", Address: "10.0.0.2:8081", Status: "online"}); err != nil {
		t.Fatalf("RegisterNode: %v", err)
	}
}

func TestWave76_RegisterNode_Update(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	xc, _ := NewXNodeController(db, "wave76-node")
	ctx := context.Background()
	_ = xc.RegisterNode(ctx, Node{ID: "update-node", Hostname: "old-host", Address: "10.0.0.3:8081", Status: "online"})
	if err := xc.RegisterNode(ctx, Node{ID: "update-node", Hostname: "new-host", Address: "10.0.0.4:8081", Status: "online"}); err != nil {
		t.Fatalf("RegisterNode update: %v", err)
	}
}

func TestWave76_Heartbeat_Success(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	xc, _ := NewXNodeController(db, "wave76-node")
	ctx := context.Background()
	_ = xc.RegisterNode(ctx, Node{ID: "hb-node-76", Hostname: "prya", Address: "10.0.0.5:8081", Status: "online"})

	if err := xc.Heartbeat(ctx, "hb-node-76"); err != nil {
		t.Fatalf("Heartbeat: %v", err)
	}
}

func TestWave76_Heartbeat_NotFound(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	xc, _ := NewXNodeController(db, "wave76-node")
	ctx := context.Background()
	// Heartbeat on nonexistent node — may or may not error depending on implementation
	err := xc.Heartbeat(ctx, "nonexistent-node-76")
	t.Logf("Heartbeat nonexistent node: %v", err)
}

// ---------------------------------------------------------------------------
// approval_integration.go: CompleteTaskWithApproval — 81.8%
// ---------------------------------------------------------------------------

func TestWave76_CompleteTaskWithApproval_HighConfidence(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	_ = taskQueue.Enqueue(context.Background(), Task{
		ID:       "wave76-complete-task",
		Domain:   "test",
		Project:  "p",
		Type:     TaskTypeFeature,
		Priority: 5,
		Status:   TaskStatusQueued,
		State:    StateQueued,
	})
	_ = taskQueue.ClaimTask(context.Background(), "wave76-complete-task", "agent-76")

	store := &sqliteApprovalStore{db: db}
	svc := NewApprovalService(store)
	tai := NewTaskApprovalIntegration(taskQueue, svc)

	// High pattern match → high confidence → complete directly
	ctx := ConfidenceContext{
		PatternMatch: 0.95,
		BlastRadius:  1,
		Reversible:   true,
	}
	err := tai.CompleteTaskWithApproval(context.Background(), "wave76-complete-task", "agent-76", "done", ctx)
	if err != nil {
		t.Errorf("CompleteTaskWithApproval high confidence: %v", err)
	}
}

func TestWave76_CompleteTaskWithApproval_LowConfidence(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	_ = taskQueue.Enqueue(context.Background(), Task{
		ID:       "wave76-low-conf-task",
		Domain:   "test",
		Project:  "p",
		Type:     TaskTypeFeature,
		Priority: 5,
		Status:   TaskStatusQueued,
		State:    StateQueued,
	})
	_ = taskQueue.ClaimTask(context.Background(), "wave76-low-conf-task", "agent-76")

	store := &sqliteApprovalStore{db: db}
	svc := NewApprovalService(store)
	tai := NewTaskApprovalIntegration(taskQueue, svc)

	// Low confidence → approval request
	ctx := ConfidenceContext{
		PatternMatch: 0.1,
		BlastRadius:  50,
		Reversible:   false,
	}
	_ = tai.CompleteTaskWithApproval(context.Background(), "wave76-low-conf-task", "agent-76", "done", ctx)
}
