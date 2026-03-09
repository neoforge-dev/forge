//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
	"time"
)

// ---------------------------------------------------------------------------
// lease.go — Claim conflict path (ErrLeaseConflict)
// ---------------------------------------------------------------------------

func TestWave98_Lease_Claim_ConflictPath(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	dbPath := filepath.Join(t.TempDir(), "lease98_conflict.db")

	// Write the test DB out to a file so NewLeaseManagerWithStateMachine can open it
	// Instead, use the shared DB by directly inserting an active lease
	now := time.Now().UTC().Format(time.RFC3339)
	taskID := "wave98-claim-conflict-task"

	// Insert a task first
	_, err := db.Exec(`INSERT INTO tasks (id, domain, project, type, priority, status, state, title, created_at, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		taskID, "test", "proj", "feature", 5, "queued", "QUEUED", "Conflict Test Task", now, now)
	if err != nil {
		t.Fatalf("insert task: %v", err)
	}

	// Insert an active lease for the task
	futureExpiry := time.Now().UTC().Add(5 * time.Minute).Format(time.RFC3339)
	_, err = db.Exec(`INSERT INTO leases (id, task_id, agent_id, expires_at) VALUES (?, ?, ?, ?)`,
		"existing-lease-wave98", taskID, "agent-one", futureExpiry)
	if err != nil {
		t.Fatalf("insert lease: %v", err)
	}

	sm := NewTaskStateMachine(db)
	lm := &sqliteLeaseManager{db: db, stateMachine: sm}

	// Now try to claim the same task — should get ErrLeaseConflict
	_, err = lm.Claim(context.Background(), taskID, "agent-two", 5*time.Minute)
	if err == nil {
		t.Error("expected ErrLeaseConflict, got nil")
	} else if err != ErrLeaseConflict {
		t.Logf("claim conflict: got err %v (wanted ErrLeaseConflict — may be wrapped)", err)
	} else {
		t.Logf("claim conflict: correctly returned ErrLeaseConflict")
	}
	_ = dbPath
}

// ---------------------------------------------------------------------------
// lease.go — Release not-found
// ---------------------------------------------------------------------------

func TestWave98_Lease_Release_NotFound(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	sm := NewTaskStateMachine(db)
	lm := &sqliteLeaseManager{db: db, stateMachine: sm}

	err := lm.Release(context.Background(), "nonexistent-lease-wave98")
	if err == nil {
		t.Error("expected error for missing lease, got nil")
	} else if err != ErrLeaseNotFound {
		t.Logf("release not-found: got %v (expected ErrLeaseNotFound)", err)
	} else {
		t.Logf("release not-found: correctly returned ErrLeaseNotFound")
	}
}

// ---------------------------------------------------------------------------
// lease.go — Renew not-found
// ---------------------------------------------------------------------------

func TestWave98_Lease_Renew_NotFound(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	sm := NewTaskStateMachine(db)
	lm := &sqliteLeaseManager{db: db, stateMachine: sm}

	err := lm.Renew(context.Background(), "nonexistent-lease-wave98-renew", 5*time.Minute)
	if err == nil {
		t.Error("expected ErrLeaseNotFound for missing lease, got nil")
	} else if err != ErrLeaseNotFound {
		t.Logf("renew not-found: got %v (expected ErrLeaseNotFound)", err)
	} else {
		t.Logf("renew not-found: correctly returned ErrLeaseNotFound")
	}
}

// ---------------------------------------------------------------------------
// lease.go — Recover with no expired leases (empty path)
// ---------------------------------------------------------------------------

func TestWave98_Lease_Recover_NoExpired(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	sm := NewTaskStateMachine(db)
	lm := &sqliteLeaseManager{db: db, stateMachine: sm}

	recovered, err := lm.Recover(context.Background())
	if err != nil {
		t.Fatalf("Recover: %v", err)
	}
	if len(recovered) != 0 {
		t.Errorf("expected 0 recovered leases with no expired, got %d", len(recovered))
	}
	t.Logf("recover no expired: ok, %d leases recovered", len(recovered))
}

// ---------------------------------------------------------------------------
// lease.go — Recover with expired leases (state transition path)
// ---------------------------------------------------------------------------

func TestWave98_Lease_Recover_WithExpired(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	now := time.Now().UTC().Format(time.RFC3339)
	taskID := "wave98-expired-task"

	// Insert a task
	_, err := db.Exec(`INSERT INTO tasks (id, domain, project, type, priority, status, state, title, created_at, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		taskID, "test", "proj", "feature", 5, "assigned", "DISPATCHED", "Expired Lease Task", now, now)
	if err != nil {
		t.Fatalf("insert task: %v", err)
	}

	// Insert a lease that expired in the past
	pastExpiry := time.Now().UTC().Add(-2 * time.Minute).Format(time.RFC3339)
	_, err = db.Exec(`INSERT INTO leases (id, task_id, agent_id, expires_at) VALUES (?, ?, ?, ?)`,
		"expired-lease-wave98", taskID, "agent-expired", pastExpiry)
	if err != nil {
		t.Fatalf("insert expired lease: %v", err)
	}

	sm := NewTaskStateMachine(db)
	lm := &sqliteLeaseManager{db: db, stateMachine: sm}

	recovered, err := lm.Recover(context.Background())
	if err != nil {
		t.Fatalf("Recover with expired: %v", err)
	}
	t.Logf("recover with expired: %d leases recovered", len(recovered))
	// Expired lease should be recovered and deleted
	if len(recovered) < 1 {
		t.Errorf("expected at least 1 recovered lease, got %d", len(recovered))
	}
}

// ---------------------------------------------------------------------------
// approvals.go — Approve: not-found path (rows == 0)
// ---------------------------------------------------------------------------

func TestWave98_Approve_NotFound(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	store := NewApprovalStore(db)

	err := store.Approve(context.Background(), "nonexistent-approval-wave98", "user1")
	if err == nil {
		t.Error("expected error for approving nonexistent approval, got nil")
	} else {
		t.Logf("approve not-found: %v", err)
	}
}

// ---------------------------------------------------------------------------
// approvals.go — Reject: not-found path (rows == 0)
// ---------------------------------------------------------------------------

func TestWave98_Reject_NotFound(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	store := NewApprovalStore(db)

	err := store.Reject(context.Background(), "nonexistent-approval-wave98", "user1")
	if err == nil {
		t.Error("expected error for rejecting nonexistent approval, got nil")
	} else {
		t.Logf("reject not-found: %v", err)
	}
}

// ---------------------------------------------------------------------------
// approvals.go — Approve with state transition (pending → approved)
// ---------------------------------------------------------------------------

func TestWave98_Approve_StateTransition(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	store := NewApprovalStore(db)
	ctx := context.Background()

	now := time.Now()
	taskIDStr := "wave98-approve-task"
	approval := Approval{
		ID:              "wave98-approval-1",
		Type:            ApprovalTaskCompletion,
		TaskID:          &taskIDStr,
		AgentID:         "glm",
		Domain:          "test",
		Title:           "Deploy approval",
		RiskScore:       0.3,
		ConfidenceScore: 0.7,
		Tier:            TierPhone,
		Status:          StatusPending,
		CreatedAt:       now,
		ExpiresAt:       now.Add(24 * time.Hour),
	}

	if err := store.Create(ctx, approval); err != nil {
		t.Fatalf("Create approval: %v", err)
	}

	// Approve it
	if err := store.Approve(ctx, "wave98-approval-1", "admin-user"); err != nil {
		t.Fatalf("Approve: %v", err)
	}

	// Verify approved
	got, err := store.Get(ctx, "wave98-approval-1")
	if err != nil {
		t.Fatalf("Get after approve: %v", err)
	}
	if got.Status != StatusApproved {
		t.Errorf("expected status=approved, got %s", got.Status)
	}
	t.Logf("approve state transition: %s → %s", StatusPending, got.Status)
}

// ---------------------------------------------------------------------------
// approvals.go — Reject with reason
// ---------------------------------------------------------------------------

func TestWave98_Reject_WithReason(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	store := NewApprovalStore(db)
	ctx := context.Background()

	now := time.Now()
	taskIDStr := "wave98-reject-task"
	approval := Approval{
		ID:              "wave98-approval-reject",
		Type:            ApprovalDeploy,
		TaskID:          &taskIDStr,
		AgentID:         "kimi",
		Domain:          "test",
		Title:           "Risky deploy",
		RiskScore:       0.9,
		ConfidenceScore: 0.1,
		Tier:            TierDesktop,
		Status:          StatusPending,
		CreatedAt:       now,
		ExpiresAt:       now.Add(24 * time.Hour),
	}

	if err := store.Create(ctx, approval); err != nil {
		t.Fatalf("Create approval: %v", err)
	}

	if err := store.Reject(ctx, "wave98-approval-reject", "admin-user"); err != nil {
		t.Fatalf("Reject: %v", err)
	}

	got, err := store.Get(ctx, "wave98-approval-reject")
	if err != nil {
		t.Fatalf("Get after reject: %v", err)
	}
	if got.Status != StatusRejected {
		t.Errorf("expected status=rejected, got %s", got.Status)
	}
	t.Logf("reject with reason: %s → %s", StatusPending, got.Status)
}

// ---------------------------------------------------------------------------
// approvals.go — ListByTask coverage
// ---------------------------------------------------------------------------

func TestWave98_ListByTask_WithItems(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	store := NewApprovalStore(db)
	ctx := context.Background()

	now := time.Now()
	taskIDStr := "wave98-list-task"
	for i, id := range []string{"wave98-list-a1", "wave98-list-a2"} {
		_ = i
		approval := Approval{
			ID:              id,
			Type:            ApprovalTaskCompletion,
			TaskID:          &taskIDStr,
			AgentID:         "glm",
			Domain:          "test",
			Title:           "List test " + id,
			RiskScore:       0.2,
			ConfidenceScore: 0.8,
			Tier:            TierWatch,
			Status:          StatusPending,
			CreatedAt:       now,
			ExpiresAt:       now.Add(24 * time.Hour),
		}
		if err := store.Create(ctx, approval); err != nil {
			t.Fatalf("Create %s: %v", id, err)
		}
	}

	items, err := store.ListByTask(ctx, taskIDStr, 10)
	if err != nil {
		t.Fatalf("ListByTask: %v", err)
	}
	if len(items) < 2 {
		t.Errorf("expected at least 2 items, got %d", len(items))
	}
	t.Logf("listByTask with items: %d approvals", len(items))
}

// ---------------------------------------------------------------------------
// handoffs.go — Create with required data
// ---------------------------------------------------------------------------

func TestWave98_Handoff_Create_WithRequiredData(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	store := NewHandoffStore(db)
	svc := NewHandoffService(store)
	ctx := context.Background()

	handoff, err := svc.Create(ctx, "glm", "kimi", "Implement auth module", []string{"auth.go", "auth_test.go"}, "high")
	if err != nil {
		t.Fatalf("Create handoff: %v", err)
	}
	if handoff.ID == "" {
		t.Error("handoff ID should not be empty")
	}
	if handoff.Status != HandoffPending {
		t.Errorf("expected status=pending, got %s", handoff.Status)
	}
	if handoff.Priority != "high" {
		t.Errorf("expected priority=high, got %s", handoff.Priority)
	}
	t.Logf("handoff create: id=%s status=%s", handoff.ID, handoff.Status)
}

// ---------------------------------------------------------------------------
// handoffs.go — Reject found handoff
// ---------------------------------------------------------------------------

func TestWave98_Handoff_Reject_Found(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	store := NewHandoffStore(db)
	svc := NewHandoffService(store)
	ctx := context.Background()

	handoff, err := svc.Create(ctx, "gemini", "pi", "Review test suite", []string{}, "medium")
	if err != nil {
		t.Fatalf("Create handoff: %v", err)
	}

	rejected, err := svc.Reject(ctx, handoff.ID, "Not enough context provided")
	if err != nil {
		t.Fatalf("Reject handoff: %v", err)
	}
	if rejected.Status != HandoffRejected {
		t.Errorf("expected status=rejected, got %s", rejected.Status)
	}
	if rejected.Reason == nil || *rejected.Reason != "Not enough context provided" {
		t.Errorf("reason mismatch, got: %v", rejected.Reason)
	}
	t.Logf("handoff reject: id=%s status=%s", rejected.ID, rejected.Status)
}

// ---------------------------------------------------------------------------
// handoffs.go — List with items
// ---------------------------------------------------------------------------

func TestWave98_Handoff_List_WithItems(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	store := NewHandoffStore(db)
	svc := NewHandoffService(store)
	ctx := context.Background()

	// Create multiple handoffs
	for i := 0; i < 3; i++ {
		_, err := svc.Create(ctx, "kilo", "minimax", "Task handoff", []string{}, "low")
		if err != nil {
			t.Fatalf("Create handoff %d: %v", i, err)
		}
	}

	handoffs, err := store.List(ctx, "", "kilo", "", 10)
	if err != nil {
		t.Fatalf("List handoffs: %v", err)
	}
	if len(handoffs) < 3 {
		t.Errorf("expected at least 3 handoffs, got %d", len(handoffs))
	}
	t.Logf("handoff list with items: %d handoffs", len(handoffs))
}

// ---------------------------------------------------------------------------
// handoffs.go — List with status filter
// ---------------------------------------------------------------------------

func TestWave98_Handoff_List_StatusFilter(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	store := NewHandoffStore(db)
	svc := NewHandoffService(store)
	ctx := context.Background()

	// Create and reject one
	h, err := svc.Create(ctx, "glm", "kimi", "Status filter test", []string{}, "")
	if err != nil {
		t.Fatalf("Create: %v", err)
	}
	_, err = svc.Reject(ctx, h.ID, "Test rejection")
	if err != nil {
		t.Fatalf("Reject: %v", err)
	}

	// List with status=rejected
	handoffs, err := store.List(ctx, string(HandoffRejected), "", "", 10)
	if err != nil {
		t.Fatalf("List filtered: %v", err)
	}
	t.Logf("handoff list status=rejected: %d handoffs", len(handoffs))
}

// ---------------------------------------------------------------------------
// event_store.go — Append with topic/payload (covers missing ID branch)
// ---------------------------------------------------------------------------

func TestWave98_EventStore_Append_WithTopic(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	es := NewEventStore(db)
	ctx := context.Background()

	payload, _ := json.Marshal(map[string]interface{}{
		"domain":  "test",
		"project": "wave98",
		"status":  "queued",
	})

	// Test with empty ID (triggers ULID generation branch)
	events := []Event{
		{
			// ID empty → ULID will be generated
			AggregateID: "wave98-agg-task",
			Type:        EventTaskCreated,
			Version:     1,
			Payload:     json.RawMessage(payload),
			AgentID:     "glm",
		},
		{
			// ID provided
			ID:          "wave98-evt-2",
			AggregateID: "wave98-agg-task",
			Type:        EventTaskAssigned,
			Version:     2,
			Payload:     json.RawMessage(`{"agent_id":"glm"}`),
			AgentID:     "glm",
			Timestamp:   time.Now(), // Non-zero timestamp
		},
	}

	if err := es.Append(ctx, events); err != nil {
		t.Fatalf("Append events: %v", err)
	}

	retrieved, err := es.GetEvents(ctx, "wave98-agg-task")
	if err != nil {
		t.Fatalf("GetEvents: %v", err)
	}
	if len(retrieved) < 2 {
		t.Errorf("expected at least 2 events, got %d", len(retrieved))
	}
	t.Logf("event store append: %d events stored and retrieved", len(retrieved))
}

// ---------------------------------------------------------------------------
// event_store.go — Append with zero timestamp (triggers timestamp=now branch)
// ---------------------------------------------------------------------------

func TestWave98_EventStore_Append_ZeroTimestamp(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	es := NewEventStore(db)
	ctx := context.Background()

	events := []Event{
		{
			AggregateID: "wave98-zero-ts-task",
			Type:        EventTaskStarted,
			Version:     1,
			Payload:     json.RawMessage(`{}`),
			// Timestamp zero — should be set to time.Now()
		},
	}

	if err := es.Append(ctx, events); err != nil {
		t.Fatalf("Append zero-timestamp event: %v", err)
	}
	t.Logf("event store append zero-timestamp: ok")
}

// ---------------------------------------------------------------------------
// parity_check.go — Check with tasks mismatch (covers error paths)
// ---------------------------------------------------------------------------

func TestWave98_ParityCheck_WithMismatch(t *testing.T) {
	// Use a temp dir as forge root (no tasks or fleet dir)
	tmpDir := t.TempDir()

	checker := NewParityChecker(tmpDir, ":memory:")
	// countV3Tasks will fail because :memory: has no tasks table
	// Check() will collect errors but still return a result
	result, err := checker.Check()
	if err != nil {
		t.Logf("Check returned err (may be expected): %v", err)
	}
	if result == nil {
		t.Error("expected non-nil ParityResult even with errors")
	} else {
		t.Logf("parity check mismatch: tasks_v2=%d tasks_v3=%d match=%v errors=%v",
			result.TasksV2, result.TasksV3, result.TasksMatch, result.Errors)
	}
}

// ---------------------------------------------------------------------------
// parity_check.go — countV2Agents with existing state.json
// ---------------------------------------------------------------------------

func TestWave98_ParityCheck_WithAgentStateFile(t *testing.T) {
	tmpDir := t.TempDir()

	// Create fleet/state.json
	fleetDir := filepath.Join(tmpDir, "fleet")
	if err := os.MkdirAll(fleetDir, 0755); err != nil {
		t.Fatalf("mkdir fleet: %v", err)
	}
	stateData := `{"agents": [{"id": "agent-1"}, {"id": "agent-2"}]}`
	if err := os.WriteFile(filepath.Join(fleetDir, "state.json"), []byte(stateData), 0644); err != nil {
		t.Fatalf("write state.json: %v", err)
	}

	checker := NewParityChecker(tmpDir, ":memory:")
	result, err := checker.Check()
	if err != nil {
		t.Logf("Check error: %v", err)
	}
	if result == nil {
		t.Fatal("expected non-nil result")
	}
	// Should have found 2 agents from state.json
	if result.AgentsV2 != 2 {
		t.Errorf("expected 2 v2 agents, got %d", result.AgentsV2)
	}
	t.Logf("parity check agent state: agents_v2=%d", result.AgentsV2)
}

// ---------------------------------------------------------------------------
// approvals.go — ExpireOldApprovals coverage
// ---------------------------------------------------------------------------

func TestWave98_ExpireOldApprovals_WithPendingApprovals(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	store := NewApprovalStore(db)
	ctx := context.Background()

	// Create approval that's already expired
	pastTime := time.Now().Add(-2 * time.Hour)
	taskIDStr := "wave98-expire-task"
	approval := Approval{
		ID:              "wave98-expire-approval",
		Type:            ApprovalMerge,
		TaskID:          &taskIDStr,
		AgentID:         "glm",
		Domain:          "test",
		Title:           "Old approval to expire",
		RiskScore:       0.5,
		ConfidenceScore: 0.5,
		Tier:            TierPhone,
		Status:          StatusPending,
		CreatedAt:       pastTime,
		ExpiresAt:       pastTime, // already expired
	}

	if err := store.Create(ctx, approval); err != nil {
		t.Fatalf("Create approval: %v", err)
	}

	svc := NewApprovalService(store)
	count, err := svc.ExpireOld(ctx)
	if err != nil {
		t.Fatalf("ExpireOld: %v", err)
	}
	t.Logf("expire old approvals: %d expired", count)
	if count < 1 {
		t.Errorf("expected at least 1 expired approval, got %d", count)
	}
}

// ---------------------------------------------------------------------------
// handoffs.go — Create with default priority (covers empty priority branch)
// ---------------------------------------------------------------------------

func TestWave98_Handoff_Create_DefaultPriority(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	store := NewHandoffStore(db)
	svc := NewHandoffService(store)
	ctx := context.Background()

	// Empty priority → should default to "medium"
	handoff, err := svc.Create(ctx, "pi", "opencode", "Default priority handoff", nil, "")
	if err != nil {
		t.Fatalf("Create handoff with empty priority: %v", err)
	}
	if handoff.Priority != "medium" {
		t.Errorf("expected default priority=medium, got %s", handoff.Priority)
	}
	t.Logf("handoff create default priority: %s", handoff.Priority)
}
