//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"strings"
	"testing"
	"time"
)

// ---------------------------------------------------------------------------
// Helpers shared across Wave 3 tests
// ---------------------------------------------------------------------------

// makeApprovalService creates a real ApprovalService wired to the test DB.
func makeApprovalService(t *testing.T) *ApprovalService {
	t.Helper()
	db := getDBConn()
	if db == nil {
		t.Fatal("makeApprovalService: no DB conn – call setupHandlerTest first")
	}
	store := NewApprovalStore(db)
	return NewApprovalService(store)
}

// makeTAI creates a TaskApprovalIntegration wired to the test globals.
func makeTAI(t *testing.T) *TaskApprovalIntegration {
	t.Helper()
	svc := makeApprovalService(t)
	return NewTaskApprovalIntegration(taskQueue, svc)
}

// insertApproval directly inserts a pre-built Approval into the DB for tests
// that need to drive ProcessApprovedTask / ProcessRejectedTask.
func insertApproval(t *testing.T, a Approval) {
	t.Helper()
	db := getDBConn()
	_, err := db.ExecContext(context.Background(), `
		INSERT INTO approvals (
			id, type, task_id, agent_id, domain, title, description, diff_summary,
			risk_score, confidence_score, recommendation, tier, status,
			created_at, expires_at, resolved_at, resolved_by
		) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		a.ID, a.Type, a.TaskID, a.AgentID, a.Domain,
		a.Title, a.Description, a.DiffSummary,
		a.RiskScore, a.ConfidenceScore, a.Recommendation,
		a.Tier, a.Status, a.CreatedAt, a.ExpiresAt,
		a.ResolvedAt, a.ResolvedBy,
	)
	if err != nil {
		t.Fatalf("insertApproval: %v", err)
	}
}

// ---------------------------------------------------------------------------
// approval_integration.go tests
// ---------------------------------------------------------------------------

func TestNewTaskApprovalIntegration_Constructor(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	tai := makeTAI(t)
	if tai == nil {
		t.Fatal("expected non-nil TaskApprovalIntegration")
	}
	if tai.queue == nil {
		t.Error("expected queue to be set")
	}
	if tai.approvalService == nil {
		t.Error("expected approvalService to be set")
	}
}

func TestCompleteTaskWithApproval_HighConfidence(t *testing.T) {
	// High confidence → task completes immediately without creating an approval.
	cleanup := setupHandlerTest(t)
	defer cleanup()

	enqueueTestTask(t, "tai-high-001")
	tai := makeTAI(t)

	// Assign the task so it can be completed.
	if err := taskQueue.AssignTask(context.Background(), "tai-high-001", "agent-x"); err != nil {
		t.Fatalf("AssignTask: %v", err)
	}

	ctx := ConfidenceContext{
		TestResults: &TestResult{
			Passed:   50,
			Failed:   0,
			Skipped:  0,
			Coverage: 90.0,
		},
		PatternMatch: 0.95,
		BlastRadius:  1,
		Reversible:   true,
	}

	err := tai.CompleteTaskWithApproval(context.Background(), "tai-high-001", "agent-x", "done", ctx)
	if err != nil {
		t.Errorf("expected nil error for high-confidence completion, got: %v", err)
	}

	task, err := taskQueue.GetTask(context.Background(), "tai-high-001")
	if err != nil {
		t.Fatalf("GetTask: %v", err)
	}
	if task.Status != TaskStatusCompleted {
		t.Errorf("expected status completed, got %s", task.Status)
	}
}

func TestCompleteTaskWithApproval_LowConfidence(t *testing.T) {
	// Low confidence → approval request created, task moved to pending_approval.
	cleanup := setupHandlerTest(t)
	defer cleanup()

	enqueueTestTask(t, "tai-low-001")
	tai := makeTAI(t)

	if err := taskQueue.AssignTask(context.Background(), "tai-low-001", "agent-y"); err != nil {
		t.Fatalf("AssignTask: %v", err)
	}

	ctx := ConfidenceContext{
		TestResults: &TestResult{
			Passed:  2,
			Failed:  10,
			Skipped: 0,
			Coverage: 10.0,
		},
		PatternMatch: 0.1,
		BlastRadius:  100,
		Reversible:   false,
	}

	err := tai.CompleteTaskWithApproval(context.Background(), "tai-low-001", "agent-y", "attempted", ctx)
	// Expect an error because low confidence triggers approval workflow.
	if err == nil {
		t.Error("expected an error (approval required) for low-confidence task, got nil")
	}
	if err != nil && !strings.Contains(err.Error(), "approval") && !strings.Contains(err.Error(), "pending_approval") {
		t.Errorf("unexpected error message: %v", err)
	}
}

func TestProcessApprovedTask_Success(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	// Create a task and move it to pending_approval state.
	enqueueTestTask(t, "tai-appr-001")
	if err := taskQueue.AssignTask(context.Background(), "tai-appr-001", "agent-z"); err != nil {
		t.Fatalf("AssignTask: %v", err)
	}
	if err := taskQueue.UpdateTaskStatus("tai-appr-001", "pending_approval", ""); err != nil {
		t.Fatalf("UpdateTaskStatus: %v", err)
	}

	tai := makeTAI(t)

	// Insert a fully-approved approval record linked to the task.
	taskID := "tai-appr-001"
	resolvedBy := "human-reviewer"
	now := time.Now()
	a := Approval{
		ID:              "appr-approved-001",
		Type:            ApprovalTaskCompletion,
		TaskID:          &taskID,
		AgentID:         "agent-z",
		Domain:          "test",
		Title:           "test approval",
		RiskScore:       0.3,
		ConfidenceScore: 0.7,
		Tier:            TierPhone,
		Status:          StatusApproved,
		CreatedAt:       now,
		ExpiresAt:       now.Add(24 * time.Hour),
		ResolvedAt:      &now,
		ResolvedBy:      &resolvedBy,
	}
	insertApproval(t, a)

	err := tai.ProcessApprovedTask(context.Background(), "appr-approved-001")
	if err != nil {
		t.Errorf("ProcessApprovedTask: %v", err)
	}

	task, err := taskQueue.GetTask(context.Background(), "tai-appr-001")
	if err != nil {
		t.Fatalf("GetTask: %v", err)
	}
	if task.Status != TaskStatusCompleted {
		t.Errorf("expected status completed after approval, got %s", task.Status)
	}
}

func TestProcessApprovedTask_NotApproved(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	enqueueTestTask(t, "tai-appr-002")
	tai := makeTAI(t)

	taskID := "tai-appr-002"
	now := time.Now()
	a := Approval{
		ID:              "appr-pending-001",
		Type:            ApprovalTaskCompletion,
		TaskID:          &taskID,
		AgentID:         "agent-q",
		Domain:          "test",
		Title:           "still pending",
		RiskScore:       0.5,
		ConfidenceScore: 0.5,
		Tier:            TierDesktop,
		Status:          StatusPending, // Not approved yet
		CreatedAt:       now,
		ExpiresAt:       now.Add(24 * time.Hour),
	}
	insertApproval(t, a)

	err := tai.ProcessApprovedTask(context.Background(), "appr-pending-001")
	if err == nil {
		t.Error("expected error for non-approved approval")
	}
	if !strings.Contains(err.Error(), "not approved") {
		t.Errorf("expected 'not approved' in error, got: %v", err)
	}
}

func TestProcessRejectedTask_Success(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	enqueueTestTask(t, "tai-rej-001")
	if err := taskQueue.AssignTask(context.Background(), "tai-rej-001", "agent-r"); err != nil {
		t.Fatalf("AssignTask: %v", err)
	}

	tai := makeTAI(t)

	taskID := "tai-rej-001"
	resolvedBy := "human-rejector"
	now := time.Now()
	a := Approval{
		ID:              "appr-rejected-001",
		Type:            ApprovalTaskCompletion,
		TaskID:          &taskID,
		AgentID:         "agent-r",
		Domain:          "test",
		Title:           "rejected approval",
		RiskScore:       0.8,
		ConfidenceScore: 0.2,
		Tier:            TierDesktop,
		Status:          StatusRejected,
		CreatedAt:       now,
		ExpiresAt:       now.Add(24 * time.Hour),
		ResolvedAt:      &now,
		ResolvedBy:      &resolvedBy,
	}
	insertApproval(t, a)

	err := tai.ProcessRejectedTask(context.Background(), "appr-rejected-001")
	if err != nil {
		t.Errorf("ProcessRejectedTask: %v", err)
	}

	task, err := taskQueue.GetTask(context.Background(), "tai-rej-001")
	if err != nil {
		t.Fatalf("GetTask: %v", err)
	}
	if task.Status != TaskStatusFailed {
		t.Errorf("expected status failed after rejection, got %s", task.Status)
	}
}

func TestProcessRejectedTask_NotRejected(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	enqueueTestTask(t, "tai-rej-002")
	tai := makeTAI(t)

	taskID := "tai-rej-002"
	now := time.Now()
	a := Approval{
		ID:              "appr-notrej-001",
		Type:            ApprovalTaskCompletion,
		TaskID:          &taskID,
		AgentID:         "agent-s",
		Domain:          "test",
		Title:           "pending approval",
		RiskScore:       0.5,
		ConfidenceScore: 0.5,
		Tier:            TierDesktop,
		Status:          StatusPending,
		CreatedAt:       now,
		ExpiresAt:       now.Add(24 * time.Hour),
	}
	insertApproval(t, a)

	err := tai.ProcessRejectedTask(context.Background(), "appr-notrej-001")
	if err == nil {
		t.Error("expected error for non-rejected approval")
	}
	if !strings.Contains(err.Error(), "not rejected") {
		t.Errorf("expected 'not rejected' in error, got: %v", err)
	}
}

func TestCheckPendingApprovals_Empty(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	tai := makeTAI(t)
	err := tai.CheckPendingApprovals(context.Background())
	if err != nil {
		t.Errorf("CheckPendingApprovals on empty DB: %v", err)
	}
}

func TestCheckPendingApprovals_WithExpired(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	enqueueTestTask(t, "tai-chk-001")
	tai := makeTAI(t)

	taskID := "tai-chk-001"
	now := time.Now()
	past := now.Add(-2 * time.Hour) // already expired
	a := Approval{
		ID:              "appr-expired-chk",
		Type:            ApprovalTaskCompletion,
		TaskID:          &taskID,
		AgentID:         "agent-t",
		Domain:          "test",
		Title:           "expired approval",
		RiskScore:       0.5,
		ConfidenceScore: 0.5,
		Tier:            TierDesktop,
		Status:          StatusPending,
		CreatedAt:       now.Add(-3 * time.Hour),
		ExpiresAt:       past,
	}
	insertApproval(t, a)

	// Should return nil even if expiration handling runs.
	err := tai.CheckPendingApprovals(context.Background())
	if err != nil {
		t.Errorf("CheckPendingApprovals: %v", err)
	}
}

func TestHandleTaskCompletion_Completed(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	enqueueTestTask(t, "tai-htc-001")
	if err := taskQueue.AssignTask(context.Background(), "tai-htc-001", "agent-u"); err != nil {
		t.Fatalf("AssignTask: %v", err)
	}

	tai := makeTAI(t)

	req := TaskCompletionRequest{
		TaskID:  "tai-htc-001",
		AgentID: "agent-u",
		Result:  "work done",
		Confidence: ConfidenceFactorsInput{
			TestsPassed:  30,
			TestsFailed:  0,
			TestsSkipped: 0,
			TestCoverage: 80.0,
			PatternMatch: 0.9,
			BlastRadius:  2,
			Reversible:   true,
		},
	}

	resp, err := tai.HandleTaskCompletion(context.Background(), req)
	if err != nil {
		t.Fatalf("HandleTaskCompletion: %v", err)
	}
	if resp == nil {
		t.Fatal("expected non-nil response")
	}
	// High confidence → should complete immediately.
	if resp.Status != "completed" {
		t.Errorf("expected status 'completed', got %q", resp.Status)
	}
	if resp.TaskID != "tai-htc-001" {
		t.Errorf("expected task_id tai-htc-001, got %q", resp.TaskID)
	}
}

func TestHandleTaskCompletion_PendingApproval(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	enqueueTestTask(t, "tai-htc-002")
	if err := taskQueue.AssignTask(context.Background(), "tai-htc-002", "agent-v"); err != nil {
		t.Fatalf("AssignTask: %v", err)
	}

	tai := makeTAI(t)

	req := TaskCompletionRequest{
		TaskID:  "tai-htc-002",
		AgentID: "agent-v",
		Result:  "low confidence work",
		Confidence: ConfidenceFactorsInput{
			TestsPassed:  1,
			TestsFailed:  15,
			TestsSkipped: 0,
			TestCoverage: 5.0,
			PatternMatch: 0.05,
			BlastRadius:  200,
			Reversible:   false,
		},
	}

	resp, err := tai.HandleTaskCompletion(context.Background(), req)
	// Low confidence → either returns pending_approval response or an error.
	// Both are acceptable as long as the flow ran.
	if err != nil && resp != nil {
		t.Error("expected either err or resp, not both")
	}
	if resp != nil && resp.Status == "" {
		t.Error("expected non-empty status in response")
	}
}

// ---------------------------------------------------------------------------
// event_store.go tests
// ---------------------------------------------------------------------------

func TestNewEventStore_Constructor(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	db := getDBConn()
	es := NewEventStore(db)
	if es == nil {
		t.Fatal("expected non-nil EventStore")
	}
}

func TestEventStore_AppendAndGetEvents(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	db := getDBConn()
	es := NewEventStore(db)
	ctx := context.Background()

	payload, _ := json.Marshal(map[string]string{"title": "test task"})
	events := []Event{
		{
			AggregateID: "task-evt-001",
			Type:        EventTaskCreated,
			Version:     1,
			Payload:     payload,
			AgentID:     "agent-es",
		},
	}

	if err := es.Append(ctx, events); err != nil {
		t.Fatalf("Append: %v", err)
	}

	got, err := es.GetEvents(ctx, "task-evt-001")
	if err != nil {
		t.Fatalf("GetEvents: %v", err)
	}
	if len(got) != 1 {
		t.Fatalf("expected 1 event, got %d", len(got))
	}
	if got[0].Type != EventTaskCreated {
		t.Errorf("expected type %s, got %s", EventTaskCreated, got[0].Type)
	}
	if got[0].AggregateID != "task-evt-001" {
		t.Errorf("expected aggregate_id task-evt-001, got %s", got[0].AggregateID)
	}
}

func TestEventStore_GetEventsByType(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	db := getDBConn()
	es := NewEventStore(db)
	ctx := context.Background()

	for i := 0; i < 3; i++ {
		payload, _ := json.Marshal(map[string]string{"result": fmt.Sprintf("r%d", i)})
		_ = es.Append(ctx, []Event{
			{
				AggregateID: fmt.Sprintf("task-bytype-%d", i),
				Type:        EventTaskCompleted,
				Version:     1,
				Payload:     payload,
			},
		})
	}
	// One extra event of a different type.
	_ = es.Append(ctx, []Event{
		{
			AggregateID: "task-bytype-other",
			Type:        EventTaskFailed,
			Version:     1,
			Payload:     []byte(`{"error":"oops"}`),
		},
	})

	got, err := es.GetEventsByType(ctx, EventTaskCompleted)
	if err != nil {
		t.Fatalf("GetEventsByType: %v", err)
	}
	if len(got) < 3 {
		t.Errorf("expected at least 3 TaskCompleted events, got %d", len(got))
	}
	for _, e := range got {
		if e.Type != EventTaskCompleted {
			t.Errorf("unexpected event type %s", e.Type)
		}
	}
}

func TestEventStore_GetEventsSince(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	db := getDBConn()
	es := NewEventStore(db)
	ctx := context.Background()

	// Use a timestamp 2 seconds in the past as the mark so that the event we
	// append (whose timestamp is stored with RFC3339 second precision) will
	// definitely appear after it.
	before := time.Now().Add(-2 * time.Second)

	payload, _ := json.Marshal(map[string]string{"agent_id": "agent-since"})
	if err := es.Append(ctx, []Event{
		{
			AggregateID: "task-since-001",
			Type:        EventTaskAssigned,
			Version:     1,
			Payload:     payload,
			Timestamp:   time.Now(), // explicit – current time, after 'before'
		},
	}); err != nil {
		t.Fatalf("Append: %v", err)
	}

	got, err := es.GetEventsSince(ctx, before)
	if err != nil {
		t.Fatalf("GetEventsSince: %v", err)
	}
	if len(got) == 0 {
		t.Error("expected at least one event since the mark")
	}
}

func TestEventStore_GetAllEvents(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	db := getDBConn()
	es := NewEventStore(db)
	ctx := context.Background()

	for i := 0; i < 5; i++ {
		_ = es.Append(ctx, []Event{
			{
				AggregateID: fmt.Sprintf("task-all-%d", i),
				Type:        EventTaskCreated,
				Version:     1,
				Payload:     []byte(`{}`),
			},
		})
	}

	// Default limit (0 → 100)
	got, err := es.GetAllEvents(ctx, 0)
	if err != nil {
		t.Fatalf("GetAllEvents(0): %v", err)
	}
	if len(got) < 5 {
		t.Errorf("expected at least 5 events, got %d", len(got))
	}

	// Explicit small limit
	got2, err := es.GetAllEvents(ctx, 2)
	if err != nil {
		t.Fatalf("GetAllEvents(2): %v", err)
	}
	if len(got2) > 2 {
		t.Errorf("expected at most 2 events with limit=2, got %d", len(got2))
	}
}

func TestEventStore_GetCurrentVersion(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	db := getDBConn()
	es := NewEventStore(db)
	ctx := context.Background()

	// No events yet → version 0
	v, err := es.GetCurrentVersion(ctx, "task-ver-000")
	if err != nil {
		t.Fatalf("GetCurrentVersion (empty): %v", err)
	}
	if v != 0 {
		t.Errorf("expected version 0 for unknown task, got %d", v)
	}

	// Append two events.
	for i := 1; i <= 2; i++ {
		_ = es.Append(ctx, []Event{
			{
				AggregateID: "task-ver-001",
				Type:        EventTaskStarted,
				Version:     i,
				Payload:     []byte(`{}`),
			},
		})
	}

	v2, err := es.GetCurrentVersion(ctx, "task-ver-001")
	if err != nil {
		t.Fatalf("GetCurrentVersion: %v", err)
	}
	if v2 != 2 {
		t.Errorf("expected version 2, got %d", v2)
	}
}

func TestTask_Apply_AllEventTypes(t *testing.T) {
	tests := []struct {
		name      string
		eventType string
		payload   interface{}
		checkFn   func(t *testing.T, task *Task)
	}{
		{
			name:      "TaskCreated",
			eventType: EventTaskCreated,
			payload: Task{
				ID: "apply-001", Domain: "d", Project: "p",
				Type: TaskTypeFeature, Priority: 5, Status: TaskStatusQueued,
			},
			checkFn: func(t *testing.T, task *Task) {
				if task.ID != "apply-001" {
					t.Errorf("ID: got %s", task.ID)
				}
			},
		},
		{
			name:      "TaskAssigned",
			eventType: EventTaskAssigned,
			payload:   map[string]string{"agent_id": "agent-apply"},
			checkFn: func(t *testing.T, task *Task) {
				if task.AssignedTo != "agent-apply" {
					t.Errorf("AssignedTo: got %s", task.AssignedTo)
				}
			},
		},
		{
			name:      "TaskStarted",
			eventType: EventTaskStarted,
			payload:   map[string]string{},
			checkFn: func(t *testing.T, task *Task) {
				if task.Status != TaskStatusExecuting {
					t.Errorf("Status: got %s", task.Status)
				}
				if task.StartedAt == nil {
					t.Error("expected StartedAt to be set")
				}
			},
		},
		{
			name:      "TaskCompleted",
			eventType: EventTaskCompleted,
			payload:   map[string]string{"result": "all good"},
			checkFn: func(t *testing.T, task *Task) {
				if task.Status != TaskStatusCompleted {
					t.Errorf("Status: got %s", task.Status)
				}
				if task.Result != "all good" {
					t.Errorf("Result: got %s", task.Result)
				}
			},
		},
		{
			name:      "TaskFailed",
			eventType: EventTaskFailed,
			payload:   map[string]string{"error": "something broke"},
			checkFn: func(t *testing.T, task *Task) {
				if task.Status != TaskStatusFailed {
					t.Errorf("Status: got %s", task.Status)
				}
				if task.Error != "something broke" {
					t.Errorf("Error: got %s", task.Error)
				}
			},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			payload, _ := json.Marshal(tt.payload)
			e := Event{
				ID:          "evt-" + tt.name,
				AggregateID: "task-apply",
				Type:        tt.eventType,
				Version:     1,
				Payload:     payload,
				Timestamp:   time.Now(),
			}
			task := &Task{}
			if err := task.Apply(e); err != nil {
				t.Fatalf("Apply(%s): %v", tt.eventType, err)
			}
			tt.checkFn(t, task)
		})
	}
}

// ---------------------------------------------------------------------------
// config.go tests
// ---------------------------------------------------------------------------

func TestConfigPaths_ContainsHomeAndCWD(t *testing.T) {
	// Unset FORGE_CONFIG so we only get the default paths.
	orig := os.Getenv("FORGE_CONFIG")
	os.Unsetenv("FORGE_CONFIG")
	defer func() {
		if orig != "" {
			os.Setenv("FORGE_CONFIG", orig)
		}
	}()

	paths := configPaths()
	if len(paths) < 2 {
		t.Fatalf("expected at least 2 paths, got %d: %v", len(paths), paths)
	}

	// Every path should end with forge.toml.
	for _, p := range paths {
		if !strings.HasSuffix(p, "forge.toml") {
			t.Errorf("path %q does not end with forge.toml", p)
		}
	}
}

func TestConfigPaths_EnvOverride(t *testing.T) {
	orig := os.Getenv("FORGE_CONFIG")
	os.Setenv("FORGE_CONFIG", "/custom/path/forge.toml")
	defer func() {
		os.Unsetenv("FORGE_CONFIG")
		if orig != "" {
			os.Setenv("FORGE_CONFIG", orig)
		}
	}()

	paths := configPaths()
	if len(paths) == 0 {
		t.Fatal("expected at least 1 path")
	}
	if paths[0] != "/custom/path/forge.toml" {
		t.Errorf("expected custom path first, got %q", paths[0])
	}
}

func TestLoadForgeConfig_NoFile(t *testing.T) {
	// Unset env so no file is found → returns zero ForgeConfig.
	orig := os.Getenv("FORGE_CONFIG")
	os.Setenv("FORGE_CONFIG", "/nonexistent/path/forge.toml")
	defer func() {
		os.Unsetenv("FORGE_CONFIG")
		if orig != "" {
			os.Setenv("FORGE_CONFIG", orig)
		}
	}()

	cfg := loadForgeConfig()
	// Zero config: port should be 0 (unset).
	if cfg.Daemon.Port != 0 {
		t.Errorf("expected zero Port, got %d", cfg.Daemon.Port)
	}
}

func TestParseForgeTOML_FullConfig(t *testing.T) {
	// Write a temporary TOML file and parse it.
	content := `
[daemon]
port = 9090
ws_port = 9091
db_path = "/tmp/test.db"
node_id = "test-node"
forge_root = "/tmp/forge"

[auth]
mode = "api"
api_token = "secret-token"
`
	f, err := os.CreateTemp("", "forge-test-*.toml")
	if err != nil {
		t.Fatalf("create temp file: %v", err)
	}
	defer os.Remove(f.Name())

	if _, err := f.WriteString(content); err != nil {
		t.Fatalf("write temp file: %v", err)
	}
	f.Close()

	cfg, err := parseForgeTOML(f.Name())
	if err != nil {
		t.Fatalf("parseForgeTOML: %v", err)
	}

	if cfg.Daemon.Port != 9090 {
		t.Errorf("Port: got %d, want 9090", cfg.Daemon.Port)
	}
	if cfg.Daemon.WSPort != 9091 {
		t.Errorf("WSPort: got %d, want 9091", cfg.Daemon.WSPort)
	}
	if cfg.Daemon.DBPath != "/tmp/test.db" {
		t.Errorf("DBPath: got %q, want /tmp/test.db", cfg.Daemon.DBPath)
	}
	if cfg.Daemon.NodeID != "test-node" {
		t.Errorf("NodeID: got %q, want test-node", cfg.Daemon.NodeID)
	}
	if cfg.Daemon.ForgeRoot != "/tmp/forge" {
		t.Errorf("ForgeRoot: got %q, want /tmp/forge", cfg.Daemon.ForgeRoot)
	}
	if cfg.Auth.Mode != "api" {
		t.Errorf("Auth.Mode: got %q, want api", cfg.Auth.Mode)
	}
	if cfg.Auth.APIToken != "secret-token" {
		t.Errorf("Auth.APIToken: got %q, want secret-token", cfg.Auth.APIToken)
	}
}

func TestParseForgeTOML_CommentsAndBlanks(t *testing.T) {
	content := `
# This is a comment
[daemon]
# another comment
port = 8081

db_path = "/var/db/forge.db"
`
	f, err := os.CreateTemp("", "forge-comments-*.toml")
	if err != nil {
		t.Fatalf("create temp file: %v", err)
	}
	defer os.Remove(f.Name())
	f.WriteString(content)
	f.Close()

	cfg, err := parseForgeTOML(f.Name())
	if err != nil {
		t.Fatalf("parseForgeTOML: %v", err)
	}
	if cfg.Daemon.Port != 8081 {
		t.Errorf("Port: got %d, want 8081", cfg.Daemon.Port)
	}
	if cfg.Daemon.DBPath != "/var/db/forge.db" {
		t.Errorf("DBPath: got %q", cfg.Daemon.DBPath)
	}
}

func TestParseForgeTOML_NonExistentFile(t *testing.T) {
	_, err := parseForgeTOML("/nonexistent/path/forge.toml")
	if err == nil {
		t.Error("expected error for non-existent file, got nil")
	}
}

func TestParseForgeTOML_QuotedValues(t *testing.T) {
	content := `
[auth]
mode = 'local'
api_token = "tok-abc-123"
`
	f, err := os.CreateTemp("", "forge-quoted-*.toml")
	if err != nil {
		t.Fatalf("create temp file: %v", err)
	}
	defer os.Remove(f.Name())
	f.WriteString(content)
	f.Close()

	cfg, err := parseForgeTOML(f.Name())
	if err != nil {
		t.Fatalf("parseForgeTOML: %v", err)
	}
	if cfg.Auth.Mode != "local" {
		t.Errorf("Mode: got %q, want local", cfg.Auth.Mode)
	}
	if cfg.Auth.APIToken != "tok-abc-123" {
		t.Errorf("APIToken: got %q, want tok-abc-123", cfg.Auth.APIToken)
	}
}

func TestLoadForgeConfig_WithFile(t *testing.T) {
	content := `
[daemon]
port = 7777
`
	f, err := os.CreateTemp("", "forge-load-*.toml")
	if err != nil {
		t.Fatalf("create temp file: %v", err)
	}
	defer os.Remove(f.Name())
	f.WriteString(content)
	f.Close()

	orig := os.Getenv("FORGE_CONFIG")
	os.Setenv("FORGE_CONFIG", f.Name())
	defer func() {
		os.Unsetenv("FORGE_CONFIG")
		if orig != "" {
			os.Setenv("FORGE_CONFIG", orig)
		}
	}()

	cfg := loadForgeConfig()
	if cfg.Daemon.Port != 7777 {
		t.Errorf("Port: got %d, want 7777", cfg.Daemon.Port)
	}
}

// ---------------------------------------------------------------------------
// context_helpers.go tests
// ---------------------------------------------------------------------------

func TestGetEnvelopeByID_NotFound(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	_, err := getEnvelopeByID(context.Background(), "nonexistent-envelope-id")
	if err == nil {
		t.Error("expected error for missing envelope, got nil")
	}
}

func TestGetEnvelopeByID_Found(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	db := getDBConn()
	ctx := context.Background()

	// Build and insert a real context envelope.
	envelope := ContextEnvelope{
		ID:        "env-test-001",
		AgentID:   "agent-ctx",
		Domain:    "test-domain",
		Project:   "test-project",
		TaskID:    "task-ctx-001",
		Summary:   "test summary",
		CreatedAt: time.Now(),
		ExpiresAt: time.Now().Add(24 * time.Hour),
	}
	content, _ := json.Marshal(envelope)

	_, err := db.ExecContext(ctx, `
		INSERT INTO context_envelopes (id, agent_id, domain, project, task_id, summary, content, created_at, expires_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		envelope.ID, envelope.AgentID, envelope.Domain, envelope.Project,
		envelope.TaskID, envelope.Summary, string(content),
		envelope.CreatedAt.Format(time.RFC3339), envelope.ExpiresAt.Format(time.RFC3339),
	)
	if err != nil {
		t.Fatalf("insert context envelope: %v", err)
	}

	got, err := getEnvelopeByID(ctx, "env-test-001")
	if err != nil {
		t.Fatalf("getEnvelopeByID: %v", err)
	}
	if got == nil {
		t.Fatal("expected non-nil envelope")
	}
	if got.ID != "env-test-001" {
		t.Errorf("ID: got %q, want env-test-001", got.ID)
	}
	if got.AgentID != "agent-ctx" {
		t.Errorf("AgentID: got %q, want agent-ctx", got.AgentID)
	}
	if got.Domain != "test-domain" {
		t.Errorf("Domain: got %q, want test-domain", got.Domain)
	}
}

func TestGetEnvelopeByID_InvalidJSON(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	db := getDBConn()
	ctx := context.Background()

	// Insert a row with invalid JSON content.
	_, err := db.ExecContext(ctx, `
		INSERT INTO context_envelopes (id, agent_id, domain, project, task_id, summary, content, created_at, expires_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		"env-badjson-001", "agent-bad", "d", "p", "t-bad", "s",
		"{not valid json",
		time.Now().Format(time.RFC3339),
		time.Now().Add(time.Hour).Format(time.RFC3339),
	)
	if err != nil {
		t.Fatalf("insert bad JSON envelope: %v", err)
	}

	_, err = getEnvelopeByID(ctx, "env-badjson-001")
	if err == nil {
		t.Error("expected JSON unmarshal error, got nil")
	}
}

func TestFindLatestEnvelopeForAgent_NotFound(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	_, err := findLatestEnvelopeForAgent(context.Background(), "no-such-agent")
	if err == nil {
		t.Error("expected error for missing agent envelope, got nil")
	}
}

func TestFindLatestEnvelopeForAgent_Found(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	db := getDBConn()
	ctx := context.Background()

	envelope := ContextEnvelope{
		ID:        "env-agent-001",
		AgentID:   "agent-latest",
		Domain:    "d",
		Project:   "p",
		TaskID:    "t-latest",
		Summary:   "latest context",
		CreatedAt: time.Now(),
		ExpiresAt: time.Now().Add(24 * time.Hour),
	}
	content, _ := json.Marshal(envelope)

	_, err := db.ExecContext(ctx, `
		INSERT INTO context_envelopes (id, agent_id, domain, project, task_id, summary, content, created_at, expires_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		envelope.ID, envelope.AgentID, envelope.Domain, envelope.Project,
		envelope.TaskID, envelope.Summary, string(content),
		envelope.CreatedAt.Format(time.RFC3339), envelope.ExpiresAt.Format(time.RFC3339),
	)
	if err != nil {
		t.Fatalf("insert context envelope: %v", err)
	}

	got, err := findLatestEnvelopeForAgent(ctx, "agent-latest")
	if err != nil {
		t.Fatalf("findLatestEnvelopeForAgent: %v", err)
	}
	if got == nil {
		t.Fatal("expected non-nil envelope")
	}
	if got.AgentID != "agent-latest" {
		t.Errorf("AgentID: got %q, want agent-latest", got.AgentID)
	}
}
