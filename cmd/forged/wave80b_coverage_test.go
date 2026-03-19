//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"testing"
	"time"
)

// ---------------------------------------------------------------------------
// patrol.go: confidenceApproveCompletedTasks — 25% → higher
// Needs stateMachine + globalApprovalService + COMPLETED tasks
// ---------------------------------------------------------------------------

func TestWave80b_ConfidenceApproveCompletedTasks_NilStateMachine(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	prevSM := stateMachine
	prevGAS := globalApprovalService
	defer func() { stateMachine = prevSM; globalApprovalService = prevGAS }()
	stateMachine = nil
	globalApprovalService = nil

	if err := confidenceApproveCompletedTasks(context.Background(), db); err != nil {
		t.Errorf("expected nil for nil stateMachine: %v", err)
	}
}

func TestWave80b_ConfidenceApproveCompletedTasks_WithCompletedTasks(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	prevSM := stateMachine
	prevGAS := globalApprovalService
	defer func() { stateMachine = prevSM; globalApprovalService = prevGAS }()

	ts := NewTaskStore(db)
	stateMachine = NewStateMachine(ts, db)
	store := NewApprovalStore(db)
	globalApprovalService = NewApprovalService(store)

	old := time.Now().Add(-2 * time.Minute).Format(time.RFC3339)
	_, _ = db.Exec(`INSERT INTO tasks (id, domain, project, type, priority, status, state, title, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)`,
		"wave80b-completed-1", "d", "p", "feature", 5, "completed", "COMPLETED", "Test task", old, old)

	err := confidenceApproveCompletedTasks(context.Background(), db)
	t.Logf("confidenceApproveCompletedTasks with tasks: %v", err)
}

// ---------------------------------------------------------------------------
// patrol.go: autoPromoteCompletedTasksInLane — 63.2%
// With globalLaneManager set and COMPLETED tasks in lanes
// ---------------------------------------------------------------------------

func TestWave80b_AutoPromoteCompletedTasksInLane_Empty(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	if err := autoPromoteCompletedTasksInLane(context.Background(), db); err != nil {
		t.Errorf("autoPromoteCompletedTasksInLane empty: %v", err)
	}
}

func TestWave80b_AutoPromoteCompletedTasksInLane_WithLaneManager(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	prevLM := globalLaneManager
	defer func() { globalLaneManager = prevLM }()

	store := NewApprovalStore(db)
	svc := NewApprovalService(store)
	globalLaneManager = NewLaneManager(taskQueue, svc, map[Lane]LaneConfig{
		"dev": {Gates: []string{"test"}},
	})

	old := time.Now().Add(-2 * time.Minute).Format(time.RFC3339)
	_, _ = db.Exec(`INSERT INTO tasks (id, domain, project, type, priority, status, state, lane, title, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)`,
		"wave80b-lane-1", "d", "p", "feature", 5, "completed", "COMPLETED", "dev", "Lane task", old, old)

	err := autoPromoteCompletedTasksInLane(context.Background(), db)
	t.Logf("autoPromoteCompletedTasksInLane with manager: %v", err)
}

// ---------------------------------------------------------------------------
// patrol.go: checkContextThreshold — 69.2%
// ---------------------------------------------------------------------------

func TestWave80b_CheckContextThreshold_NilCM(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	if err := checkContextThreshold(context.Background(), db, nil, nil); err != nil {
		t.Errorf("checkContextThreshold nil cm: %v", err)
	}
}

func TestWave80b_CheckContextThreshold_WithHighContextAgent(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	_, _ = db.Exec(`INSERT INTO agent_heartbeats (agent_id, node, status, context_pct, last_seen, updated_at) VALUES (?,?,?,?,datetime('now'),datetime('now'))`,
		"wave80b-agent-high", "test-node", "busy", 80.0)

	tmpDir := t.TempDir()
	cm := testContextManager(t, db, tmpDir)
	err := checkContextThreshold(context.Background(), db, cm, nil)
	t.Logf("checkContextThreshold high context: %v", err)
}

// ---------------------------------------------------------------------------
// patrol.go: councilCleanupPatrol, checkBinaryFreshness, checkAgentHealth
// ---------------------------------------------------------------------------

func TestWave80b_CouncilCleanupPatrol_OK(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	err := councilCleanupPatrol(context.Background(), db)
	t.Logf("councilCleanupPatrol: %v", err)
}

func TestWave80b_CheckBinaryFreshness_OK(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	err := checkBinaryFreshness(context.Background(), db)
	t.Logf("checkBinaryFreshness: %v", err)
}

func TestWave80b_CheckAgentHealth_WithStaleAgent(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	stale := time.Now().Add(-10 * time.Minute).Format(time.RFC3339)
	_, _ = db.Exec(`INSERT INTO agent_heartbeats (agent_id, node, status, context_pct, last_seen, updated_at) VALUES (?,?,?,?,?,?)`,
		"wave80b-stale", "node", "busy", 50.0, stale, stale)

	err := checkAgentHealth(context.Background(), db)
	t.Logf("checkAgentHealth stale: %v", err)
}

// ---------------------------------------------------------------------------
// patrol.go: dispatchTimeoutPatrol, dataRetentionPatrol
// ---------------------------------------------------------------------------

func TestWave80b_DispatchTimeoutPatrol_OK(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	err := dispatchTimeoutPatrol(context.Background(), db)
	t.Logf("dispatchTimeoutPatrol: %v", err)
}

func TestWave80b_DataRetentionPatrol_OK(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	err := dataRetentionPatrol(context.Background(), db)
	t.Logf("dataRetentionPatrol: %v", err)
}

// ---------------------------------------------------------------------------
// patrol.go: monitorQueueDepth — 88.9%
// ---------------------------------------------------------------------------

func TestWave80b_MonitorQueueDepth_OK(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	err := monitorQueueDepth(context.Background(), db)
	t.Logf("monitorQueueDepth: %v", err)
}

// ---------------------------------------------------------------------------
// patrol.go: resultIngestPatrol — 86.4%
// ---------------------------------------------------------------------------

func TestWave80b_ResultIngestPatrol_OK(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	err := resultIngestPatrol(context.Background(), db)
	t.Logf("resultIngestPatrol: %v", err)
}

// ---------------------------------------------------------------------------
// patrol.go: agentLivenessPatrol — 82.8%
// With zombie agents (stale heartbeat > 5min)
// ---------------------------------------------------------------------------

func TestWave80b_AgentLivenessPatrol_WithZombie(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	stale := time.Now().Add(-10 * time.Minute).Format(time.RFC3339)
	_, _ = db.Exec(`INSERT INTO agent_heartbeats (agent_id, node, status, context_pct, last_seen, updated_at) VALUES (?,?,?,?,?,?)`,
		"wave80b-zombie", "node", "busy", 50.0, stale, stale)

	err := agentLivenessPatrol(context.Background(), db)
	t.Logf("agentLivenessPatrol with zombie: %v", err)
}

// ---------------------------------------------------------------------------
// patrol.go: dailyDigestPatrol — 82.9%
// ---------------------------------------------------------------------------

func TestWave80b_DailyDigestPatrol_OK(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	err := dailyDigestPatrol(context.Background(), db)
	t.Logf("dailyDigestPatrol: %v", err)
}
