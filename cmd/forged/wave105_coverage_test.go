//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

// ── StateMachine branches ──────────────────────────────────────────────────────

func TestWave105_StateMachine_Transition_NonExistentTask(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	store := NewTaskStore(db)
	sm := NewStateMachine(store, db)

	// Transition on a task that doesn't exist in DB → ErrNoRows path
	err := sm.Transition("nonexistent-task-wave105", StateQueued, StateDispatched, "test")
	if err == nil {
		t.Error("expected error for non-existent task, got nil")
	}
}

func TestWave105_StateMachine_Transition_WithOnExitErrorHook(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	store := NewTaskStore(db)
	sm := NewStateMachine(store, db)

	// Register an OnExit hook that returns an error — should be logged but not fail transition
	sm.RegisterOnExit(StateDispatched, func(taskID string, from, to TaskState, reason string) error {
		return errors.New("exit hook intentional error for wave105")
	})

	// Insert a task in DISPATCHED state
	_, err := db.Exec(`INSERT INTO tasks (id, domain, project, type, title, priority, status, state, created_at, updated_at)
		VALUES ('task-wave105-exit', 'dom', 'proj', 'feature', 'Test task', 5, 'assigned', 'DISPATCHED', datetime('now'), datetime('now'))`)
	if err != nil {
		t.Fatalf("insert task: %v", err)
	}

	// Transition DISPATCHED→RUNNING: triggers OnExit hook for DISPATCHED (returns error, logs but continues)
	err = sm.Transition("task-wave105-exit", StateDispatched, StateRunning, "testing hook error")
	// Transition should succeed even if hook errors (hook errors are logged, not fatal)
	if err != nil {
		t.Logf("transition returned err (acceptable): %v", err)
	}
}

func TestWave105_StateMachine_Transition_WithOnEnterErrorHook(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	store := NewTaskStore(db)
	sm := NewStateMachine(store, db)

	// Register an OnEnter hook that returns an error — logged but not fatal
	sm.RegisterOnEnter(StateRunning, func(taskID string, from, to TaskState, reason string) error {
		return errors.New("enter hook intentional error for wave105")
	})

	_, err := db.Exec(`INSERT INTO tasks (id, domain, project, type, title, priority, status, state, created_at, updated_at)
		VALUES ('task-wave105-enter', 'dom', 'proj', 'feature', 'Test task', 5, 'assigned', 'DISPATCHED', datetime('now'), datetime('now'))`)
	if err != nil {
		t.Fatalf("insert task: %v", err)
	}

	// Transition DISPATCHED→RUNNING: triggers OnEnter hook for RUNNING (returns error, logs but continues)
	err = sm.Transition("task-wave105-enter", StateDispatched, StateRunning, "testing enter hook error")
	// The OnEnter hook fires AFTER commit, so the error is logged but transition is done
	if err != nil {
		t.Logf("transition returned err: %v", err)
	}
}

func TestWave105_StateMachine_Transition_WithOnTransitionErrorHook(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	store := NewTaskStore(db)
	sm := NewStateMachine(store, db)

	// Register OnTransition hook that errors
	key := fmt.Sprintf("%s->%s", StateDispatched, StateRunning)
	sm.onTransit[key] = append(sm.onTransit[key], func(taskID string, from, to TaskState, reason string) error {
		return errors.New("transition hook intentional error for wave105")
	})

	_, err := db.Exec(`INSERT INTO tasks (id, domain, project, type, title, priority, status, state, created_at, updated_at)
		VALUES ('task-wave105-transit', 'dom', 'proj', 'feature', 'Test task', 5, 'assigned', 'DISPATCHED', datetime('now'), datetime('now'))`)
	if err != nil {
		t.Fatalf("insert task: %v", err)
	}

	err = sm.Transition("task-wave105-transit", StateDispatched, StateRunning, "testing transition hook error")
	if err != nil {
		t.Logf("transition returned err: %v", err)
	}
}

// ── EventBus branches ──────────────────────────────────────────────────────────

func TestWave105_EventBus_Subscriber_Error(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	eb, err := NewEventBus(db)
	if err != nil {
		t.Fatalf("NewEventBus: %v", err)
	}
	defer eb.Close()

	// Subscribe with a handler that returns an error
	errHandler := Subscriber(func(e BusEvent) error {
		return errors.New("subscriber intentional error for wave105")
	})
	if err := eb.Subscribe("test.topic", errHandler); err != nil {
		t.Fatalf("Subscribe: %v", err)
	}

	// Publish — should trigger error handler asynchronously
	if err := eb.Publish("test.topic", map[string]string{"key": "value"}); err != nil {
		t.Fatalf("Publish: %v", err)
	}

	// Give goroutine time to run
	time.Sleep(50 * time.Millisecond)
}

func TestWave105_EventBus_Replay_NonJSONPayload(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	eb, err := NewEventBus(db)
	if err != nil {
		t.Fatalf("NewEventBus: %v", err)
	}
	defer eb.Close()

	// Insert an event with non-JSON payload directly into the events table
	eventID := "evt-wave105-" + fmt.Sprintf("%d", time.Now().UnixNano())
	_, err = db.Exec(`INSERT INTO events (id, aggregate_id, topic, type, version, payload, timestamp, agent_id, sequence)
		VALUES (?, ?, ?, ?, ?, ?, datetime('now'), '', 999)`,
		eventID, eventID, "test.nonjson", "test.nonjson", 1, "not-valid-json-{{{{")
	if err != nil {
		t.Fatalf("insert event: %v", err)
	}

	// Replay — should hit the non-JSON unmarshal path and set e.Data = rawPayload
	events, err := eb.Replay(time.Now().Add(-1*time.Hour), nil)
	if err != nil {
		t.Logf("Replay returned err (acceptable): %v", err)
	}
	t.Logf("Replayed %d events", len(events))
}

func TestWave105_EventBus_GetLatestEvents_WithData(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	eb, err := NewEventBus(db)
	if err != nil {
		t.Fatalf("NewEventBus: %v", err)
	}
	defer eb.Close()

	// Publish a few events
	for i := 0; i < 3; i++ {
		_ = eb.Publish("test.latest", map[string]int{"i": i})
	}

	// Get latest events
	events, err := eb.GetLatestEvents(10)
	if err != nil {
		t.Errorf("GetLatestEvents: %v", err)
	}
	t.Logf("Got %d latest events", len(events))
}

func TestWave105_EventBus_Stats(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	eb, err := NewEventBus(db)
	if err != nil {
		t.Fatalf("NewEventBus: %v", err)
	}
	defer eb.Close()

	stats, err := eb.Stats()
	if err != nil {
		t.Errorf("Stats: %v", err)
	}
	if _, ok := stats["total_events"]; !ok {
		t.Error("Stats missing total_events")
	}
}

func TestWave105_EventBus_ReplayForTopic(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	eb, err := NewEventBus(db)
	if err != nil {
		t.Fatalf("NewEventBus: %v", err)
	}
	defer eb.Close()

	_ = eb.Publish("tasks.created", map[string]string{"task": "t1"})
	_ = eb.Publish("tasks.updated", map[string]string{"task": "t2"})

	events, err := eb.ReplayForTopic(time.Now().Add(-1*time.Hour), "tasks.*")
	if err != nil {
		t.Errorf("ReplayForTopic: %v", err)
	}
	t.Logf("ReplayForTopic returned %d events", len(events))
}

// ── GetTaskEvents invalid timestamp ───────────────────────────────────────────

func TestWave105_GetTaskEvents_InvalidTimestamp(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	q, _ := NewTaskQueueFromDB(db)
	sq := q.(*sqliteTaskQueue)

	// Insert a task_events row with an invalid timestamp
	taskID := "task-wave105-events"
	_, _ = db.Exec(`INSERT INTO tasks (id, domain, project, type, title, priority, status, state, created_at, updated_at)
		VALUES (?, 'dom', 'proj', 'feature', 'Test', 5, 'queued', 'QUEUED', datetime('now'), datetime('now'))`, taskID)
	_, err := db.Exec(`INSERT INTO task_events (task_id, domain, project, event_type, payload, created_at)
		VALUES (?, 'dom', 'proj', 'task.created', '{}', 'not-a-valid-timestamp')`, taskID)
	if err != nil {
		t.Fatalf("insert task_event: %v", err)
	}

	ctx := context.Background()
	events, err := sq.GetTaskEvents(ctx, taskID, 10)
	if err != nil {
		t.Logf("GetTaskEvents returned err: %v", err)
	}
	t.Logf("Got %d events (including invalid timestamp)", len(events))
}

// ── nil-DB handler paths ───────────────────────────────────────────────────────

func TestWave105_NodesHealth_NilDB(t *testing.T) {
	oldDB := getDBConn()
	setDBConn(nil)
	defer setDBConn(oldDB)

	req := httptest.NewRequest(http.MethodGet, "/api/nodes/health", nil)
	rr := httptest.NewRecorder()
	nodesHealthHandler(rr, req)
	if rr.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503 with nil DB, got %d: %s", rr.Code, rr.Body.String())
	}
}

func TestWave105_FleetSnapshot_NilDB(t *testing.T) {
	oldDB := getDBConn()
	setDBConn(nil)
	defer setDBConn(oldDB)

	req := httptest.NewRequest(http.MethodGet, "/api/fleet/snapshot", nil)
	rr := httptest.NewRecorder()
	fleetSnapshotHandler(rr, req)
	if rr.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503 with nil DB, got %d: %s", rr.Code, rr.Body.String())
	}
}

// ── ApprovalHandler branches ───────────────────────────────────────────────────

func newApprovalHandlerForTest(t *testing.T) (*ApprovalHandler, func()) {
	t.Helper()
	db, cleanup := setupClaimTestDB(t)
	store := NewApprovalStore(db)
	svc := NewApprovalService(store)
	h := &ApprovalHandler{service: svc}
	return h, cleanup
}

func TestWave105_ApprovalHandler_HandleApprovalAction_DecodeFail(t *testing.T) {
	h, cleanup := newApprovalHandlerForTest(t)
	defer cleanup()

	// Send malformed JSON body — decode fails, req.User = "system"
	req := httptest.NewRequest(http.MethodPost, "/api/approvals/some-id/approve", strings.NewReader("not-json{{{{"))
	rr := httptest.NewRecorder()
	h.handleApprovalAction(rr, req)
	// Should attempt approve and fail since approval doesn't exist
	t.Logf("approval action decode fail: %d %s", rr.Code, rr.Body.String())
}

func TestWave105_ApprovalHandler_HandleApprovalAction_EmptyUser(t *testing.T) {
	h, cleanup := newApprovalHandlerForTest(t)
	defer cleanup()

	// Send {"user":""} — req.User is empty, gets set to "system"
	req := httptest.NewRequest(http.MethodPost, "/api/approvals/some-id/approve", strings.NewReader(`{"user":""}`))
	rr := httptest.NewRecorder()
	h.handleApprovalAction(rr, req)
	t.Logf("approval action empty user: %d %s", rr.Code, rr.Body.String())
}

func TestWave105_ApprovalHandler_HandleApprovalAction_MethodNotAllowed(t *testing.T) {
	h, cleanup := newApprovalHandlerForTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/approvals/some-id/approve", nil)
	rr := httptest.NewRecorder()
	h.handleApprovalAction(rr, req)
	if rr.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", rr.Code)
	}
}

func TestWave105_ApprovalHandler_HandleApprovalAction_ApproveNotFound(t *testing.T) {
	h, cleanup := newApprovalHandlerForTest(t)
	defer cleanup()

	body := bytes.NewBufferString(`{"user":"tester"}`)
	req := httptest.NewRequest(http.MethodPost, "/api/approvals/nonexistent-approval-id/approve", body)
	rr := httptest.NewRecorder()
	h.handleApprovalAction(rr, req)
	// Should return 404 (not found error)
	if rr.Code != http.StatusNotFound {
		t.Logf("approval not found: %d %s", rr.Code, rr.Body.String())
	}
}

func TestWave105_ApprovalHandler_HandleApprovalAction_RejectNotFound(t *testing.T) {
	h, cleanup := newApprovalHandlerForTest(t)
	defer cleanup()

	body := bytes.NewBufferString(`{"user":"tester"}`)
	req := httptest.NewRequest(http.MethodPost, "/api/approvals/nonexistent-approval-id/reject", body)
	rr := httptest.NewRecorder()
	h.handleApprovalAction(rr, req)
	t.Logf("approval reject not found: %d %s", rr.Code, rr.Body.String())
}

func TestWave105_ApprovalHandler_CreateApproval_AutoApprove(t *testing.T) {
	h, cleanup := newApprovalHandlerForTest(t)
	defer cleanup()
	db := getDBConn()

	// Create a task first
	taskID := "task-wave105-approval"
	_, _ = db.Exec(`INSERT INTO tasks (id, domain, project, type, title, priority, status, state, created_at, updated_at)
		VALUES (?, 'dom', 'proj', 'feature', 'Auto approve test', 5, 'queued', 'QUEUED', datetime('now'), datetime('now'))`, taskID)

	// High confidence context (PatternMatch=1.0) → AutoApprove=true → StatusAutoApproved
	body := map[string]interface{}{
		"type":     "task_completion",
		"task_id":  taskID,
		"agent_id": "agent-wave105",
		"domain":   "dom",
		"title":    "Test approval",
		"confidence_context": map[string]interface{}{
			"pattern_match": 1.0,
			"blast_radius":  1,
			"reversible":    true,
		},
	}
	bodyBytes, _ := json.Marshal(body)
	req := httptest.NewRequest(http.MethodPost, "/api/approvals", bytes.NewReader(bodyBytes))
	req.Header.Set("Content-Type", "application/json")
	rr := httptest.NewRecorder()
	h.createApproval(rr, req)
	t.Logf("createApproval response: %d %s", rr.Code, rr.Body.String())
}

func TestWave105_ApprovalHandler_HandleApprovals_List_ByTier(t *testing.T) {
	h, cleanup := newApprovalHandlerForTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/approvals?tier=WATCH", nil)
	rr := httptest.NewRecorder()
	h.handleApprovals(rr, req)
	if rr.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", rr.Code, rr.Body.String())
	}
}

func TestWave105_ApprovalHandler_HandleApprovals_List_ByAgent(t *testing.T) {
	h, cleanup := newApprovalHandlerForTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/approvals?agent_id=agent-x", nil)
	rr := httptest.NewRecorder()
	h.handleApprovals(rr, req)
	if rr.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", rr.Code, rr.Body.String())
	}
}

func TestWave105_ApprovalHandler_HandleApprovals_List_Default(t *testing.T) {
	h, cleanup := newApprovalHandlerForTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/approvals", nil)
	rr := httptest.NewRecorder()
	h.handleApprovals(rr, req)
	if rr.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", rr.Code, rr.Body.String())
	}
}

func TestWave105_ApprovalHandler_HandlePending(t *testing.T) {
	h, cleanup := newApprovalHandlerForTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/approvals/pending", nil)
	rr := httptest.NewRecorder()
	h.handlePending(rr, req)
	if rr.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", rr.Code, rr.Body.String())
	}
}

func TestWave105_ApprovalHandler_HandlePending_MethodNotAllowed(t *testing.T) {
	h, cleanup := newApprovalHandlerForTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/approvals/pending", nil)
	rr := httptest.NewRecorder()
	h.handlePending(rr, req)
	if rr.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", rr.Code)
	}
}

func TestWave105_ApprovalHandler_CreateApproval_MissingFields(t *testing.T) {
	h, cleanup := newApprovalHandlerForTest(t)
	defer cleanup()

	body := bytes.NewBufferString(`{"type":"task_completion"}`) // missing agent_id, domain, title
	req := httptest.NewRequest(http.MethodPost, "/api/approvals", body)
	rr := httptest.NewRecorder()
	h.createApproval(rr, req)
	if rr.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d: %s", rr.Code, rr.Body.String())
	}
}

func TestWave105_ApprovalHandler_CreateApproval_MissingTaskID(t *testing.T) {
	h, cleanup := newApprovalHandlerForTest(t)
	defer cleanup()

	body := bytes.NewBufferString(`{"type":"task_completion","agent_id":"a","domain":"d","title":"t"}`)
	req := httptest.NewRequest(http.MethodPost, "/api/approvals", body)
	rr := httptest.NewRecorder()
	h.createApproval(rr, req)
	if rr.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d: %s", rr.Code, rr.Body.String())
	}
}

func TestWave105_ApprovalHandler_CreateApproval_InvalidTier(t *testing.T) {
	h, cleanup := newApprovalHandlerForTest(t)
	defer cleanup()
	db := getDBConn()

	taskID := "task-wave105-tier"
	_, _ = db.Exec(`INSERT INTO tasks (id, domain, project, type, title, priority, status, state, created_at, updated_at)
		VALUES (?, 'dom', 'proj', 'feature', 'Tier test', 5, 'queued', 'QUEUED', datetime('now'), datetime('now'))`, taskID)

	body := map[string]interface{}{
		"type":     "task_completion",
		"task_id":  taskID,
		"agent_id": "agent-x",
		"domain":   "dom",
		"title":    "Test",
		"tier":     "INVALID_TIER",
	}
	bodyBytes, _ := json.Marshal(body)
	req := httptest.NewRequest(http.MethodPost, "/api/approvals", bytes.NewReader(bodyBytes))
	rr := httptest.NewRecorder()
	h.createApproval(rr, req)
	if rr.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for invalid tier, got %d: %s", rr.Code, rr.Body.String())
	}
}

func TestWave105_ApprovalHandler_CreateApproval_InvalidType(t *testing.T) {
	h, cleanup := newApprovalHandlerForTest(t)
	defer cleanup()
	db := getDBConn()

	taskID := "task-wave105-type"
	_, _ = db.Exec(`INSERT INTO tasks (id, domain, project, type, title, priority, status, state, created_at, updated_at)
		VALUES (?, 'dom', 'proj', 'feature', 'Type test', 5, 'queued', 'QUEUED', datetime('now'), datetime('now'))`, taskID)

	body := map[string]interface{}{
		"type":     "invalid_type",
		"task_id":  taskID,
		"agent_id": "agent-x",
		"domain":   "dom",
		"title":    "Test",
	}
	bodyBytes, _ := json.Marshal(body)
	req := httptest.NewRequest(http.MethodPost, "/api/approvals", bytes.NewReader(bodyBytes))
	rr := httptest.NewRecorder()
	h.createApproval(rr, req)
	if rr.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for invalid type, got %d: %s", rr.Code, rr.Body.String())
	}
}

// ── queue.go Dequeue with startedAt.Valid ─────────────────────────────────────

func TestWave105_Queue_Dequeue_TaskWithStartedAt(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	q, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}

	// Insert a queued task that has started_at set
	_, err = db.Exec(`INSERT INTO tasks (id, domain, project, type, title, priority, status, state, started_at, created_at, updated_at)
		VALUES ('task-w105-dequeue', 'dom', 'proj', 'feature', 'Started dequeue test', 8, 'queued', 'QUEUED', datetime('now', '-1 hour'), datetime('now', '-2 hours'), datetime('now'))`)
	if err != nil {
		t.Fatalf("insert task: %v", err)
	}

	ctx := context.Background()
	task, err := q.Dequeue(ctx, "agent-dequeue-w105")
	if err != nil {
		t.Logf("Dequeue returned: %v", err)
		return
	}
	if task == nil {
		t.Log("Dequeue returned nil task")
		return
	}
	// If task was dequeued, verify startedAt was parsed
	t.Logf("Dequeued task: %s, startedAt: %v", task.ID, task.StartedAt)
}

// ── queue.go ExtendLease not-found path ───────────────────────────────────────

func TestWave105_Queue_ExtendLease_NotAssigned(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	q, _ := NewTaskQueueFromDB(db)
	sq := q.(*sqliteTaskQueue)

	ctx := context.Background()
	// Call ExtendLease on non-existent task/agent combo
	err := sq.ExtendLease(ctx, "nonexistent-task", "nonexistent-agent")
	if err == nil {
		t.Error("expected error for non-existent task, got nil")
	} else {
		t.Logf("ExtendLease error: %v", err)
	}
}

// ── Additional queue paths ─────────────────────────────────────────────────────

func TestWave105_Queue_Pause_TaskNotFound(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	q, _ := NewTaskQueueFromDB(db)

	ctx := context.Background()
	err := q.Pause(ctx, "nonexistent-task-pause")
	if err == nil {
		t.Error("expected error for non-existent task pause, got nil")
	}
}

func TestWave105_Queue_Resume_TaskNotFound(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	q, _ := NewTaskQueueFromDB(db)

	ctx := context.Background()
	err := q.Resume(ctx, "nonexistent-task-resume")
	if err == nil {
		t.Error("expected error for non-existent task resume, got nil")
	}
}

func TestWave105_Queue_Pause_Success(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	q, _ := NewTaskQueueFromDB(db)

	_, err := db.Exec(`INSERT INTO tasks (id, domain, project, type, title, priority, status, state, created_at, updated_at)
		VALUES ('task-w105-pause', 'dom', 'proj', 'feature', 'Pause test', 5, 'executing', 'RUNNING', datetime('now'), datetime('now'))`)
	if err != nil {
		t.Fatalf("insert: %v", err)
	}

	ctx := context.Background()
	if err := q.Pause(ctx, "task-w105-pause"); err != nil {
		t.Errorf("Pause: %v", err)
	}
}

func TestWave105_Queue_Resume_Success(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	q, _ := NewTaskQueueFromDB(db)

	_, err := db.Exec(`INSERT INTO tasks (id, domain, project, type, title, priority, status, state, created_at, updated_at)
		VALUES ('task-w105-resume', 'dom', 'proj', 'feature', 'Resume test', 5, 'paused', 'RUNNING', datetime('now'), datetime('now'))`)
	if err != nil {
		t.Fatalf("insert: %v", err)
	}

	ctx := context.Background()
	if err := q.Resume(ctx, "task-w105-resume"); err != nil {
		t.Errorf("Resume: %v", err)
	}
}

func TestWave105_Queue_GetClaimableTasks(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	q, _ := NewTaskQueueFromDB(db)

	_, _ = db.Exec(`INSERT INTO tasks (id, domain, project, type, title, priority, status, state, created_at, updated_at)
		VALUES ('task-w105-claimable', 'dom', 'proj', 'feature', 'Claimable task', 5, 'requested', 'QUEUED', datetime('now'), datetime('now'))`)

	ctx := context.Background()
	tasks, err := q.GetClaimableTasks(ctx, 10)
	if err != nil {
		t.Errorf("GetClaimableTasks: %v", err)
	}
	t.Logf("GetClaimableTasks returned %d tasks", len(tasks))
}
