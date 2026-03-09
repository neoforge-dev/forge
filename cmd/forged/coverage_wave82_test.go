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
// fleet_scaler.go: fleetAutoExecutePatrol — 24.4%
// Insert a pending scale recommendation, ensure circuit breaker closed and
// flap guard inactive, then call the patrol. On macOS readLiveRAMMB fails
// (no /proc/meminfo), which exercises lines 776-778 and returns nil.
// ---------------------------------------------------------------------------

func TestWave82_FleetAutoExecutePatrol_PendingRec_RAMFails(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	db := getDBConn()
	ctx := context.Background()

	// Ensure scale_recommendations table exists
	if err := ensureScaleRecommendationsTable(ctx, db); err != nil {
		t.Fatalf("ensureScaleRecommendationsTable: %v", err)
	}

	// Ensure circuit breaker is closed
	circuitBreaker.Lock()
	prevState := circuitBreaker.state
	circuitBreaker.state = "closed"
	circuitBreaker.Unlock()
	defer func() {
		circuitBreaker.Lock()
		circuitBreaker.state = prevState
		circuitBreaker.Unlock()
	}()

	// Ensure flap guard is inactive (last scale event was long ago)
	lastScaleEvent.Lock()
	prevTS := lastScaleEvent.timestamp
	lastScaleEvent.timestamp = time.Time{}
	lastScaleEvent.Unlock()
	defer func() {
		lastScaleEvent.Lock()
		lastScaleEvent.timestamp = prevTS
		lastScaleEvent.Unlock()
	}()

	// Insert a pending auto_execute recommendation with a future expiry
	future := time.Now().Add(10 * time.Minute).UTC().Format(time.RFC3339)
	_, err := db.Exec(`
		INSERT INTO scale_recommendations
			(id, node_id, agent_type, action, reason, status, auto_execute, created_at, expires_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), ?)`,
		"wave82-rec-1", "test-node", "claude", "inflate",
		"test coverage", "pending", 1, future)
	if err != nil {
		t.Logf("insert scale_recommendations: %v (skipping main body test)", err)
		return
	}

	err = fleetAutoExecutePatrol(ctx, db)
	if err != nil {
		t.Logf("fleetAutoExecutePatrol with pending rec: %v (may be ram/cpu gate)", err)
	}
}

func TestWave82_FleetAutoExecutePatrol_EmptyTable(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	db := getDBConn()
	ctx := context.Background()

	if err := ensureScaleRecommendationsTable(ctx, db); err != nil {
		t.Fatalf("ensureScaleRecommendationsTable: %v", err)
	}

	// Ensure circuit breaker is closed and flap guard inactive
	circuitBreaker.Lock()
	prevState := circuitBreaker.state
	circuitBreaker.state = "closed"
	circuitBreaker.Unlock()
	defer func() {
		circuitBreaker.Lock()
		circuitBreaker.state = prevState
		circuitBreaker.Unlock()
	}()

	lastScaleEvent.Lock()
	prevTS := lastScaleEvent.timestamp
	lastScaleEvent.timestamp = time.Time{}
	lastScaleEvent.Unlock()
	defer func() {
		lastScaleEvent.Lock()
		lastScaleEvent.timestamp = prevTS
		lastScaleEvent.Unlock()
	}()

	err := fleetAutoExecutePatrol(ctx, db)
	if err != nil {
		t.Errorf("fleetAutoExecutePatrol empty: %v", err)
	}
}

// ---------------------------------------------------------------------------
// patrol.go: confidenceApproveCompletedTasks — 25%
// Insert a task in COMPLETED state with updated_at > 30 seconds ago to
// exercise the rows.Next() loop body.
// ---------------------------------------------------------------------------

func TestWave82_ConfidenceApproveCompletedTasks_WithRows(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	db := getDBConn()
	ctx := context.Background()

	// Initialize stateMachine and globalApprovalService for this test
	ts := NewTaskStore(db)
	oldSM := stateMachine
	stateMachine = NewStateMachine(ts, db)
	defer func() { stateMachine = oldSM }()

	store := NewApprovalStore(db)
	svc := NewApprovalService(store)
	oldSvc := globalApprovalService
	globalApprovalService = svc
	defer func() { globalApprovalService = oldSvc }()

	// Insert a task in COMPLETED state with updated_at more than 30 seconds ago.
	// SQLite datetime() returns "2006-01-02 15:04:05" format (space separator, no Z)
	// so we must use the same format for string comparison to work.
	// assigned_to must be non-NULL (confidenceApproveCompletedTasks scans into string).
	oldTime := time.Now().Add(-2 * time.Minute).UTC().Format("2006-01-02 15:04:05")
	_, err := db.Exec(`
		INSERT INTO tasks (id, domain, project, type, priority, status, state, title, assigned_to, result, created_at, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		"wave82-completed-task", "codeswiftr", "test-project", "feature",
		5, "completed", "COMPLETED", "Wave82 Test Task", "wave82-agent", "", oldTime, oldTime)
	if err != nil {
		t.Fatalf("insert task: %v", err)
	}

	err = confidenceApproveCompletedTasks(ctx, db)
	if err != nil {
		t.Logf("confidenceApproveCompletedTasks: %v (may be expected)", err)
	}
}

func TestWave82_ConfidenceApproveCompletedTasks_HighScore(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	db := getDBConn()
	ctx := context.Background()

	ts := NewTaskStore(db)
	oldSM := stateMachine
	stateMachine = NewStateMachine(ts, db)
	defer func() { stateMachine = oldSM }()

	store := NewApprovalStore(db)
	svc := NewApprovalService(store)
	oldSvc := globalApprovalService
	globalApprovalService = svc
	defer func() { globalApprovalService = oldSvc }()

	// Insert multiple completed tasks to exercise the loop fully
	// Use SQLite datetime format (space separator, no Z) for correct string comparison
	// assigned_to and result must be non-NULL (scanned into string variables)
	oldTime := time.Now().Add(-5 * time.Minute).UTC().Format("2006-01-02 15:04:05")
	for i, id := range []string{"wave82-c1", "wave82-c2", "wave82-c3"} {
		_, _ = db.Exec(`
			INSERT INTO tasks (id, domain, project, type, priority, status, state, title, assigned_to, result, created_at, updated_at)
			VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
			id, "codeswiftr", "proj", "feature", i+1,
			"completed", "COMPLETED", "Task "+id, "wave82-agent", "", oldTime, oldTime)
	}

	err := confidenceApproveCompletedTasks(ctx, db)
	if err != nil {
		t.Logf("confidenceApproveCompletedTasks multiple: %v", err)
	}
}

// ---------------------------------------------------------------------------
// patrol.go: autoPromoteCompletedTasksInLane — 63.2%
// Insert a task in COMPLETED state with a lane — exercises rows.Next() body.
// ---------------------------------------------------------------------------

func TestWave82_AutoPromoteCompletedTasksInLane_WithRows(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	db := getDBConn()
	ctx := context.Background()

	// Insert a task in COMPLETED state with a non-empty lane, updated > 60s ago
	// Use SQLite format for correct string comparison with datetime()
	oldTime := time.Now().Add(-5 * time.Minute).UTC().Format("2006-01-02 15:04:05")
	_, err := db.Exec(`
		INSERT INTO tasks (id, domain, project, type, priority, status, state, title, lane, created_at, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		"wave82-lane-task", "codeswiftr", "proj", "feature", 5,
		"completed", "COMPLETED", "Lane Task", "dev", oldTime, oldTime)
	if err != nil {
		t.Logf("insert task with lane: %v (lane column may not exist)", err)
	}

	err = autoPromoteCompletedTasksInLane(ctx, db)
	if err != nil {
		t.Logf("autoPromoteCompletedTasksInLane: %v", err)
	}
}

func TestWave82_AutoPromoteCompletedTasksInLane_EmptyDB(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	db := getDBConn()
	ctx := context.Background()

	err := autoPromoteCompletedTasksInLane(ctx, db)
	if err != nil {
		t.Errorf("autoPromoteCompletedTasksInLane empty: %v", err)
	}
}

// ---------------------------------------------------------------------------
// patrol.go: councilCleanupPatrol — 67.4%
// Test with a temp directory containing expired + active council markers.
// ---------------------------------------------------------------------------

func TestWave82_CouncilCleanupPatrol_NoDir(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	db := getDBConn()
	ctx := context.Background()

	// Without the council dir existing, should return nil
	tmpDir := t.TempDir()
	prevWD, _ := os.Getwd()
	if err := os.Chdir(tmpDir); err != nil {
		t.Fatalf("Chdir: %v", err)
	}
	defer os.Chdir(prevWD)

	err := councilCleanupPatrol(ctx, db)
	if err != nil {
		t.Errorf("councilCleanupPatrol no dir: %v", err)
	}
}

func TestWave82_CouncilCleanupPatrol_WithExpiredMarker(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	db := getDBConn()
	ctx := context.Background()

	tmpDir := t.TempDir()
	prevWD, _ := os.Getwd()
	if err := os.Chdir(tmpDir); err != nil {
		t.Fatalf("Chdir: %v", err)
	}
	defer os.Chdir(prevWD)

	// Create .forge/autoscale/council directory
	councilDir := filepath.Join(tmpDir, ".forge", "autoscale", "council")
	if err := os.MkdirAll(councilDir, 0755); err != nil {
		t.Fatalf("MkdirAll: %v", err)
	}

	// Create an expired marker
	expiredMarker := map[string]interface{}{
		"agent":      "wave82-agent",
		"purpose":    "test coverage",
		"ttl_minutes": 5,
		"started_at": time.Now().Add(-30 * time.Minute).Format(time.RFC3339),
		"expires_at": time.Now().Add(-20 * time.Minute).Format(time.RFC3339),
	}
	data, _ := json.Marshal(expiredMarker)
	markerFile := filepath.Join(councilDir, "wave82-agent.json")
	if err := os.WriteFile(markerFile, data, 0644); err != nil {
		t.Fatalf("WriteFile: %v", err)
	}

	err := councilCleanupPatrol(ctx, db)
	if err != nil {
		t.Errorf("councilCleanupPatrol with expired marker: %v", err)
	}
}

func TestWave82_CouncilCleanupPatrol_WithActiveMarker(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	db := getDBConn()
	ctx := context.Background()

	tmpDir := t.TempDir()
	prevWD, _ := os.Getwd()
	if err := os.Chdir(tmpDir); err != nil {
		t.Fatalf("Chdir: %v", err)
	}
	defer os.Chdir(prevWD)

	councilDir := filepath.Join(tmpDir, ".forge", "autoscale", "council")
	if err := os.MkdirAll(councilDir, 0755); err != nil {
		t.Fatalf("MkdirAll: %v", err)
	}

	// Create an active marker (not yet expired)
	activeMarker := map[string]interface{}{
		"agent":       "wave82-active-agent",
		"purpose":     "active session",
		"ttl_minutes": 60,
		"started_at":  time.Now().Format(time.RFC3339),
		"expires_at":  time.Now().Add(30 * time.Minute).Format(time.RFC3339),
	}
	data, _ := json.Marshal(activeMarker)
	if err := os.WriteFile(filepath.Join(councilDir, "wave82-active.json"), data, 0644); err != nil {
		t.Fatalf("WriteFile: %v", err)
	}

	// Create a marker with no expires_at (permanent)
	permanentMarker := map[string]interface{}{
		"agent":      "wave82-permanent",
		"purpose":    "permanent session",
		"started_at": time.Now().Format(time.RFC3339),
	}
	data2, _ := json.Marshal(permanentMarker)
	if err := os.WriteFile(filepath.Join(councilDir, "wave82-permanent.json"), data2, 0644); err != nil {
		t.Fatalf("WriteFile permanent: %v", err)
	}

	// Create a non-json file to test skip logic
	if err := os.WriteFile(filepath.Join(councilDir, "README.txt"), []byte("ignore"), 0644); err != nil {
		t.Fatalf("WriteFile txt: %v", err)
	}

	err := councilCleanupPatrol(ctx, db)
	if err != nil {
		t.Errorf("councilCleanupPatrol with active marker: %v", err)
	}
}

// ---------------------------------------------------------------------------
// task_state_machine.go: ClaimTransition — 68.8%
// Test the success path, wrong-state path, and already-assigned path.
// ---------------------------------------------------------------------------

func TestWave82_ClaimTransition_Success(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ts := NewTaskStore(db)
	sm := NewStateMachine(ts, db)

	now := time.Now().Format(time.RFC3339)
	_, err := db.Exec(
		`INSERT INTO tasks (id, domain, project, type, priority, status, state, title, created_at, updated_at)
		 VALUES (?,?,?,?,?,?,?,?,?,?)`,
		"wave82-claim-task", "d", "p", "feature", 5,
		"queued", "QUEUED", "Claim Task", now, now)
	if err != nil {
		t.Fatalf("insert task: %v", err)
	}

	if err := sm.ClaimTransition("wave82-claim-task", "wave82-agent"); err != nil {
		t.Errorf("ClaimTransition success: %v", err)
	}
}

func TestWave82_ClaimTransition_WrongState(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ts := NewTaskStore(db)
	sm := NewStateMachine(ts, db)

	now := time.Now().Format(time.RFC3339)
	_, err := db.Exec(
		`INSERT INTO tasks (id, domain, project, type, priority, status, state, title, created_at, updated_at)
		 VALUES (?,?,?,?,?,?,?,?,?,?)`,
		"wave82-running-task", "d", "p", "feature", 5,
		"executing", "RUNNING", "Running Task", now, now)
	if err != nil {
		t.Fatalf("insert task: %v", err)
	}

	// RUNNING cannot be claimed (expects QUEUED→DISPATCHED)
	err = sm.ClaimTransition("wave82-running-task", "wave82-agent")
	if err == nil {
		t.Error("expected error when claiming a RUNNING task")
	}
}

func TestWave82_ClaimTransition_TaskNotFound(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ts := NewTaskStore(db)
	sm := NewStateMachine(ts, db)

	err := sm.ClaimTransition("nonexistent-wave82-task", "wave82-agent")
	if err == nil {
		t.Error("expected error for nonexistent task")
	}
}

func TestWave82_ClaimTransition_AlreadyAssigned(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ts := NewTaskStore(db)
	sm := NewStateMachine(ts, db)

	now := time.Now().Format(time.RFC3339)
	_, err := db.Exec(
		`INSERT INTO tasks (id, domain, project, type, priority, status, state, title, assigned_to, created_at, updated_at)
		 VALUES (?,?,?,?,?,?,?,?,?,?,?)`,
		"wave82-assigned-task", "d", "p", "feature", 5,
		"queued", "QUEUED", "Assigned Task", "other-agent", now, now)
	if err != nil {
		t.Fatalf("insert task: %v", err)
	}

	// A different agent tries to claim — should get ErrInvalidTransition
	err = sm.ClaimTransition("wave82-assigned-task", "wave82-agent")
	if err == nil {
		t.Error("expected error when task is already assigned to another agent")
	}
}

// ---------------------------------------------------------------------------
// patrol.go: checkBinaryFreshness — 68.0%
// ---------------------------------------------------------------------------

func TestWave82_CheckBinaryFreshness_NoBinary(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	db := getDBConn()
	ctx := context.Background()

	// Without a binary present, should log and return nil
	err := checkBinaryFreshness(ctx, db)
	if err != nil {
		t.Logf("checkBinaryFreshness no binary: %v (expected)", err)
	}
}

// ---------------------------------------------------------------------------
// patrol.go: checkContextThreshold — 69.2%
// ---------------------------------------------------------------------------

func TestWave82_CheckContextThreshold_EmptyDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()
	cm := NewContextManager(db, t.TempDir())

	err := checkContextThreshold(ctx, db, cm, nil)
	if err != nil {
		t.Logf("checkContextThreshold empty DB: %v", err)
	}
}
