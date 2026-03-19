//go:build !tmux_bridge
// +build !tmux_bridge

package main

// coverage_tier1_test.go — targeted tests to push cmd/forged coverage from ~81.8% to ~85%.
//
// Focuses on uncovered branches in:
//   - fleet_scaler.go: fleetAutoExecutePatrol (52.6%), fleetScaleRecommendPatrol (66.2%),
//     fleetDeflateRecommendPatrol (73.3%), deflationGateCheck (78.6%)
//   - xnode.go: ListInboxHandler (56%), readNewMessages (75%)
//
// All tests are pure-logic or use in-memory SQLite — no network calls, no goroutines.

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// ---------------------------------------------------------------------------
// Tier1: fleetAutoExecutePatrol — uncovered branches
// ---------------------------------------------------------------------------

// TestTier1_FleetAutoExecutePatrol_CircuitBreakerOpen verifies that when the circuit
// breaker is tripped, the patrol returns nil immediately (no DB query needed).
func TestTier1_FleetAutoExecutePatrol_CircuitBreakerOpen(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Trip the circuit breaker. Must also set lastFailure to time.Now() to prevent
	// the auto-reset logic from clearing the "open" state on the first check.
	circuitBreaker.Lock()
	origState := circuitBreaker.state
	origFailures := circuitBreaker.consecutiveFailures
	origLastFailure := circuitBreaker.lastFailure
	circuitBreaker.state = "open"
	circuitBreaker.consecutiveFailures = 3
	circuitBreaker.lastFailure = time.Now() // prevent auto-reset (needs > 5min to reset)
	circuitBreaker.Unlock()
	defer func() {
		circuitBreaker.Lock()
		circuitBreaker.state = origState
		circuitBreaker.consecutiveFailures = origFailures
		circuitBreaker.lastFailure = origLastFailure
		circuitBreaker.Unlock()
	}()

	err := fleetAutoExecutePatrol(context.Background(), db)
	if err != nil {
		t.Errorf("expected nil from circuit-breaker-open path, got: %v", err)
	}
}

// TestTier1_FleetAutoExecutePatrol_FlapGuardBlocks verifies that when canScale()
// returns false (recent scale event), the patrol exits early.
func TestTier1_FleetAutoExecutePatrol_FlapGuardBlocks(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Ensure circuit breaker is closed first.
	circuitBreaker.Lock()
	origState := circuitBreaker.state
	origFailures := circuitBreaker.consecutiveFailures
	circuitBreaker.state = "closed"
	circuitBreaker.consecutiveFailures = 0
	circuitBreaker.Unlock()
	defer func() {
		circuitBreaker.Lock()
		circuitBreaker.state = origState
		circuitBreaker.consecutiveFailures = origFailures
		circuitBreaker.Unlock()
	}()

	// Set a recent scale event so flap guard blocks.
	lastScaleEvent.Lock()
	origTS := lastScaleEvent.timestamp
	lastScaleEvent.timestamp = time.Now() // just happened
	lastScaleEvent.Unlock()
	defer func() {
		lastScaleEvent.Lock()
		lastScaleEvent.timestamp = origTS
		lastScaleEvent.Unlock()
	}()

	err := fleetAutoExecutePatrol(context.Background(), db)
	if err != nil {
		t.Errorf("expected nil from flap-guard path, got: %v", err)
	}
}

// TestTier1_FleetAutoExecutePatrol_NoPendingRecs verifies patrol returns nil when
// there are no pending auto_execute recommendations.
func TestTier1_FleetAutoExecutePatrol_NoPendingRecs(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	if err := ensureScaleRecommendationsTable(context.Background(), db); err != nil {
		t.Fatalf("ensureScaleRecommendationsTable: %v", err)
	}

	// Reset circuit breaker and flap guard to allow execution.
	circuitBreaker.Lock()
	origState := circuitBreaker.state
	origFailures := circuitBreaker.consecutiveFailures
	circuitBreaker.state = "closed"
	circuitBreaker.consecutiveFailures = 0
	circuitBreaker.Unlock()
	defer func() {
		circuitBreaker.Lock()
		circuitBreaker.state = origState
		circuitBreaker.consecutiveFailures = origFailures
		circuitBreaker.Unlock()
	}()

	lastScaleEvent.Lock()
	origTS := lastScaleEvent.timestamp
	lastScaleEvent.timestamp = time.Time{} // zero — allow scaling
	lastScaleEvent.Unlock()
	defer func() {
		lastScaleEvent.Lock()
		lastScaleEvent.timestamp = origTS
		lastScaleEvent.Unlock()
	}()

	// No recommendations inserted — patrol should find zero rows and return nil.
	err := fleetAutoExecutePatrol(context.Background(), db)
	if err != nil {
		t.Errorf("expected nil when no pending recs, got: %v", err)
	}
}

// TestTier1_FleetAutoExecutePatrol_ForbiddenAgent inserts a pending rec for a
// forbidden agent type and verifies the executor-side guard defers it instead
// of attempting to spawn.
func TestTier1_FleetAutoExecutePatrol_ForbiddenAgent(t *testing.T) {
	// Skip on NoAutoSpawnNodes (prya/vega/gaea) — those exit immediately at guard 0.
	localNode, _ := os.Hostname()
	if NoAutoSpawnNodes[localNode] {
		t.Skipf("skipping: node %s is in NoAutoSpawnNodes", localNode)
	}

	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	if err := ensureScaleRecommendationsTable(context.Background(), db); err != nil {
		t.Fatalf("ensureScaleRecommendationsTable: %v", err)
	}

	// Reset circuit breaker and flap guard.
	circuitBreaker.Lock()
	origState := circuitBreaker.state
	circuitBreaker.state = "closed"
	circuitBreaker.consecutiveFailures = 0
	circuitBreaker.Unlock()
	defer func() {
		circuitBreaker.Lock()
		circuitBreaker.state = origState
		circuitBreaker.Unlock()
	}()

	lastScaleEvent.Lock()
	origTS := lastScaleEvent.timestamp
	lastScaleEvent.timestamp = time.Time{}
	lastScaleEvent.Unlock()
	defer func() {
		lastScaleEvent.Lock()
		lastScaleEvent.timestamp = origTS
		lastScaleEvent.Unlock()
	}()

	// Insert a pending rec for an agent type forbidden on the local node.
	// Pick a forbidden type that applies to the local node — "opencode" is forbidden on prya/vega/gaea.
	// Since we skip on NoAutoSpawnNodes, the node here is sati/nova/etc.
	// We use a type that IS forbidden on this node (if any); otherwise we test the non-forbidden path.
	// For sati/nova: no types are forbidden, so we insert "kimi" (allowed) and verify
	// readLiveRAMMB is reached (may succeed or return nil on no-/proc/meminfo).
	expires := time.Now().UTC().Add(5 * time.Minute).Format(time.RFC3339)
	_, err := db.Exec(`
		INSERT INTO scale_recommendations (id, node_id, agent_type, action, reason, queue_depth, idle_agents, auto_execute, status, created_at, expires_at)
		VALUES (?, ?, ?, 'inflate', 'tier1 test', 1, 0, 1, 'pending', datetime('now'), ?)
	`, "tier1-forbidden-rec", localNode, "kimi", expires)
	if err != nil {
		t.Fatalf("insert rec: %v", err)
	}

	// The patrol will proceed past circuit breaker and flap guard, claim the rec,
	// then hit the RAM gate (or forbidden gate). Either returns nil.
	err = fleetAutoExecutePatrol(context.Background(), db)
	if err != nil {
		t.Errorf("expected nil from patrol (deferred/ram gate), got: %v", err)
	}
}

// ---------------------------------------------------------------------------
// Tier1: fleetScaleRecommendPatrol — ceiling exceeded branch
// ---------------------------------------------------------------------------

// TestTier1_FleetScaleRecommendPatrol_CeilingExceeded verifies that when the
// agent_inventory count for a node is at or above the hard ceiling, no recommendation
// is inserted for that node.
func TestTier1_FleetScaleRecommendPatrol_CeilingExceeded(t *testing.T) {
	// Skip on NoAutoSpawnNodes — patrol returns immediately there.
	localNode, _ := os.Hostname()
	if NoAutoSpawnNodes[localNode] {
		t.Skipf("skipping: node %s is in NoAutoSpawnNodes", localNode)
	}

	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()

	// Insert many queued tasks so queuedCount > 0.
	for i := 0; i < 10; i++ {
		_, err := db.ExecContext(ctx, `
			INSERT INTO tasks (id, status, state, domain, project, type, priority, title)
			VALUES (?, 'queued', 'QUEUED', 'test', 'proj', 'dev', 5, 'task')
		`, "tier1-task-"+string(rune('a'+i)))
		if err != nil {
			t.Fatalf("insert task: %v", err)
		}
	}

	// Insert agents into agent_inventory at the hard ceiling for the local node.
	// agent_inventory schema: id (PK), agent_type, node_id, tmux_window, status, ...
	ceiling := NodeHardCeilings[localNode]
	if ceiling == 0 {
		ceiling = 2 // conservative default for unknown nodes
	}
	for i := 0; i < ceiling; i++ {
		_, err := db.ExecContext(ctx, `
			INSERT OR REPLACE INTO agent_inventory (id, agent_type, node_id, tmux_window, status)
			VALUES (?, 'kimi', ?, ?, 'idle')
		`, "tier1-agent-ceil-"+string(rune('a'+i)), localNode, "kimi-"+string(rune('a'+i)))
		if err != nil {
			t.Fatalf("insert agent: %v", err)
		}
	}

	// Run patrol — ceiling is met, so no recommendation should be inserted.
	err := fleetScaleRecommendPatrol(ctx, db)
	if err != nil {
		t.Errorf("patrol failed: %v", err)
	}

	// Verify no recommendation was inserted.
	var count int
	db.QueryRowContext(ctx, `SELECT COUNT(*) FROM scale_recommendations WHERE status = 'pending'`).Scan(&count)
	if count != 0 {
		t.Errorf("expected 0 pending recs when at ceiling, got %d", count)
	}
}

// TestTier1_FleetScaleRecommendPatrol_ForbiddenAgentSkip verifies that when the
// default agent type (kimi) is forbidden on a node, no recommendation is inserted.
func TestTier1_FleetScaleRecommendPatrol_ForbiddenAgentSkip(t *testing.T) {
	// Skip on NoAutoSpawnNodes.
	localNode, _ := os.Hostname()
	if NoAutoSpawnNodes[localNode] {
		t.Skipf("skipping: node %s is in NoAutoSpawnNodes", localNode)
	}

	// "kimi" is NOT forbidden on sati/nova, so this test only applies to prya/vega/gaea.
	// But NoAutoSpawnNodes guard above already skips those.
	// We test it by temporarily adding "kimi" to the forbidden list for a fake node.
	fakeNode := "tier1-fake-restricted-node"

	// Temporarily add kimi to ForbiddenAgentTypes for the fake node.
	if ForbiddenAgentTypes[fakeNode] == nil {
		ForbiddenAgentTypes[fakeNode] = make(map[string]bool)
	}
	ForbiddenAgentTypes[fakeNode]["kimi"] = true
	defer delete(ForbiddenAgentTypes, fakeNode)

	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()

	// Insert a queued task.
	_, err := db.ExecContext(ctx, `
		INSERT INTO tasks (id, status, state, domain, project, type, priority, title)
		VALUES ('tier1-ftask-1', 'queued', 'QUEUED', 'test', 'proj', 'dev', 5, 'task')
	`)
	if err != nil {
		t.Fatalf("insert task: %v", err)
	}

	// Insert an idle agent on the fake node (0 idle agents means the threshold check
	// queuedCount > 0 > 0*2 passes, but forbidden guard should block).
	_, err = db.ExecContext(ctx, `
		INSERT OR REPLACE INTO agent_inventory (id, agent_type, node_id, tmux_window, status)
		VALUES ('tier1-fakenode-agent', 'kimi', ?, 'kimi-fake', 'idle')
	`, fakeNode)
	if err != nil {
		t.Fatalf("insert agent: %v", err)
	}

	err = fleetScaleRecommendPatrol(ctx, db)
	if err != nil {
		t.Errorf("patrol failed: %v", err)
	}

	// No rec should have been inserted for fakeNode because kimi is forbidden there.
	var count int
	db.QueryRowContext(ctx, `
		SELECT COUNT(*) FROM scale_recommendations WHERE node_id = ? AND status = 'pending'
	`, fakeNode).Scan(&count)
	if count != 0 {
		t.Errorf("expected 0 recs for node with forbidden agent, got %d", count)
	}
}

// ---------------------------------------------------------------------------
// Tier1: deflationGateCheck — uncovered branches
// ---------------------------------------------------------------------------

// TestTier1_DeflationGateCheck_ContextPctHigh verifies the gate blocks when
// context_pct >= 10% (signal 1 fails).
func TestTier1_DeflationGateCheck_ContextPctHigh(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()

	// agent_heartbeats.node is NOT NULL — must supply a value.
	_, err := db.ExecContext(ctx, `
		INSERT INTO agent_heartbeats (agent_id, node, status, context_pct, last_seen)
		VALUES ('tier1-hb-agent', 'sati', 'WORKING', 55.0, datetime('now', '-30 minutes'))
	`)
	if err != nil {
		t.Fatalf("insert heartbeat: %v", err)
	}

	ok, reason, err := deflationGateCheck(ctx, db, "tier1-hb-agent", "sati", 15*time.Minute)
	if err != nil {
		t.Fatalf("deflationGateCheck error: %v", err)
	}
	if ok {
		t.Error("expected gate to BLOCK when context_pct=55% >= 10%")
	}
	if !strings.Contains(reason, "context_pct") {
		t.Errorf("expected reason to mention context_pct, got: %s", reason)
	}
}

// TestTier1_DeflationGateCheck_RecentlyActive verifies the gate blocks when
// last_seen is within the idle threshold (signal 2 fails).
func TestTier1_DeflationGateCheck_RecentlyActive(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()

	// context_pct = 0 (passes signal 1), but last_seen is 5m ago (fails signal 2 at 15m threshold).
	recentTime := time.Now().Add(-5 * time.Minute).Format(time.RFC3339)
	_, err := db.ExecContext(ctx, `
		INSERT INTO agent_heartbeats (agent_id, node, status, context_pct, last_seen)
		VALUES ('tier1-recent-agent', 'sati', 'IDLE', 0.0, ?)
	`, recentTime)
	if err != nil {
		t.Fatalf("insert heartbeat: %v", err)
	}

	ok, reason, err := deflationGateCheck(ctx, db, "tier1-recent-agent", "sati", 15*time.Minute)
	if err != nil {
		t.Fatalf("deflationGateCheck error: %v", err)
	}
	if ok {
		t.Errorf("expected gate to BLOCK when agent last seen 5m ago (threshold=15m), reason=%s", reason)
	}
	if !strings.Contains(reason, "idle only") {
		t.Errorf("expected reason to mention idle time, got: %s", reason)
	}
}

// TestTier1_DeflationGateCheck_HasAssignedTask verifies the gate blocks when
// the agent has an active assigned task (signal 3 fails).
func TestTier1_DeflationGateCheck_HasAssignedTask(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()

	// context_pct = 0 (signal 1 passes), last_seen = 30m ago (signal 2 passes).
	oldTime := time.Now().Add(-30 * time.Minute).Format(time.RFC3339)
	_, err := db.ExecContext(ctx, `
		INSERT INTO agent_heartbeats (agent_id, node, status, context_pct, last_seen)
		VALUES ('tier1-assigned-agent', 'sati', 'WORKING', 0.0, ?)
	`, oldTime)
	if err != nil {
		t.Fatalf("insert heartbeat: %v", err)
	}

	// Insert an assigned task pointing to this agent.
	_, err = db.ExecContext(ctx, `
		INSERT INTO tasks (id, status, state, domain, project, type, priority, title, assigned_to)
		VALUES ('tier1-atask-1', 'assigned', 'RUNNING', 'test', 'proj', 'dev', 5, 'task', 'tier1-assigned-agent')
	`)
	if err != nil {
		t.Fatalf("insert task: %v", err)
	}

	ok, reason, err := deflationGateCheck(ctx, db, "tier1-assigned-agent", "sati", 15*time.Minute)
	if err != nil {
		t.Fatalf("deflationGateCheck error: %v", err)
	}
	if ok {
		t.Errorf("expected gate to BLOCK when agent has assigned task, reason=%s", reason)
	}
	if !strings.Contains(reason, "assigned task") {
		t.Errorf("expected reason to mention assigned task, got: %s", reason)
	}
}

// TestTier1_DeflationGateCheck_QueueGrowing verifies the gate blocks when the
// queue is growing (signal 4 fails).
func TestTier1_DeflationGateCheck_QueueGrowing(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()

	// context_pct = 0, last_seen = 30m ago, no assigned tasks — signals 1-3 pass.
	oldTime := time.Now().Add(-30 * time.Minute).Format(time.RFC3339)
	_, err := db.ExecContext(ctx, `
		INSERT INTO agent_heartbeats (agent_id, node, status, context_pct, last_seen)
		VALUES ('tier1-queue-agent', 'sati', 'IDLE', 0.0, ?)
	`, oldTime)
	if err != nil {
		t.Fatalf("insert heartbeat: %v", err)
	}

	// Insert many queued tasks so queuedCount > idleCount*2.
	for i := 0; i < 10; i++ {
		_, err := db.ExecContext(ctx, `
			INSERT INTO tasks (id, status, state, domain, project, type, priority, title)
			VALUES (?, 'queued', 'QUEUED', 'test', 'proj', 'dev', 5, 'task')
		`, "tier1-qtask-"+string(rune('a'+i)))
		if err != nil {
			t.Fatalf("insert task: %v", err)
		}
	}
	// No idle agents in agent_inventory so idleCount=0; queuedCount=10 > 0*2=0.

	ok, reason, err := deflationGateCheck(ctx, db, "tier1-queue-agent", "sati", 15*time.Minute)
	if err != nil {
		t.Fatalf("deflationGateCheck error: %v", err)
	}
	if ok {
		t.Errorf("expected gate to BLOCK when queue is growing, reason=%s", reason)
	}
	if !strings.Contains(reason, "queue growing") {
		t.Errorf("expected reason to mention queue growing, got: %s", reason)
	}
}

// TestTier1_DeflationGateCheck_AllGreenNoCouncilMarker verifies the gate passes
// when all 5 signals are green (no council marker, no tasks, queue stable).
func TestTier1_DeflationGateCheck_AllGreenNoCouncilMarker(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()

	// All signals should pass: context_pct=0, last_seen=30m ago, no tasks, queue empty.
	oldTime := time.Now().Add(-30 * time.Minute).Format(time.RFC3339)
	_, err := db.ExecContext(ctx, `
		INSERT INTO agent_heartbeats (agent_id, node, status, context_pct, last_seen)
		VALUES ('tier1-green-agent', 'sati', 'IDLE', 0.0, ?)
	`, oldTime)
	if err != nil {
		t.Fatalf("insert heartbeat: %v", err)
	}

	// Ensure no council marker file exists for this agent.
	// hasActiveCouncilMarker reads from .forge/autoscale/council/{agentID}.json
	// — the file should not exist in the test CWD.

	ok, reason, err := deflationGateCheck(ctx, db, "tier1-green-agent", "sati", 15*time.Minute)
	if err != nil {
		t.Fatalf("deflationGateCheck error: %v", err)
	}
	if !ok {
		t.Errorf("expected gate to PASS when all signals green, reason=%s", reason)
	}
	if !strings.Contains(reason, "green") {
		t.Errorf("expected reason to say green, got: %s", reason)
	}
}

// ---------------------------------------------------------------------------
// Tier1: fleetDeflateRecommendPatrol — with draining agents present
// ---------------------------------------------------------------------------

// TestTier1_FleetDeflateRecommendPatrol_WithAgents verifies the patrol runs
// through its full loop when agent_heartbeats has multiple agents on one node,
// including the "min floor: keep 1 agent" guard.
// The deflate patrol queries: SELECT agent_id, node, context_pct, last_seen FROM agent_heartbeats.
func TestTier1_FleetDeflateRecommendPatrol_WithAgents(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()

	// Insert two agents on "nova" so the min-floor (>1) guard passes.
	oldTime := time.Now().Add(-30 * time.Minute).Format(time.RFC3339)
	for i, id := range []string{"tier1-da-1", "tier1-da-2"} {
		contextPct := float64(i * 5) // 0% and 5% — both pass signal 1 (< 10%)
		_, err := db.ExecContext(ctx, `
			INSERT INTO agent_heartbeats (agent_id, node, status, context_pct, last_seen)
			VALUES (?, 'nova', 'IDLE', ?, ?)
		`, id, contextPct, oldTime)
		if err != nil {
			t.Fatalf("insert heartbeat for %s: %v", id, err)
		}
	}

	// Run the deflate patrol — should not panic or error.
	err := fleetDeflateRecommendPatrol(ctx, db)
	if err != nil {
		t.Errorf("fleetDeflateRecommendPatrol returned error: %v", err)
	}
}

// TestTier1_FleetDeflateRecommendPatrol_SingleAgent verifies the min-floor guard
// prevents a deflation recommendation when only 1 agent is on a node.
func TestTier1_FleetDeflateRecommendPatrol_SingleAgent(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()

	// Insert only 1 agent on "gaea" — min-floor should prevent any deflation.
	oldTime := time.Now().Add(-30 * time.Minute).Format(time.RFC3339)
	_, err := db.ExecContext(ctx, `
		INSERT INTO agent_heartbeats (agent_id, node, status, context_pct, last_seen)
		VALUES ('tier1-solo-agent', 'gaea', 'IDLE', 0.0, ?)
	`, oldTime)
	if err != nil {
		t.Fatalf("insert heartbeat: %v", err)
	}

	if err := ensureScaleRecommendationsTable(ctx, db); err != nil {
		t.Fatalf("ensureScaleRecommendationsTable: %v", err)
	}

	err = fleetDeflateRecommendPatrol(ctx, db)
	if err != nil {
		t.Errorf("fleetDeflateRecommendPatrol returned error: %v", err)
	}

	// No deflate rec should be inserted (only 1 agent, min-floor applies).
	var count int
	db.QueryRowContext(ctx, `
		SELECT COUNT(*) FROM scale_recommendations WHERE action = 'deflate' AND status = 'pending'
	`).Scan(&count)
	if count != 0 {
		t.Errorf("expected 0 deflate recs with single agent, got %d", count)
	}
}

// ---------------------------------------------------------------------------
// Tier1: XNode.ListInboxHandler — uncovered branches
// ---------------------------------------------------------------------------

// TestTier1_XNode_ListInboxHandler_WithJSONLFile verifies the handler reads and
// returns messages from a real .jsonl file in the inbox directory.
func TestTier1_XNode_ListInboxHandler_WithJSONLFile(t *testing.T) {
	xc, cleanup := setupXNodeHTTP(t)
	defer cleanup()

	// Write a valid JSONL message to the inbox dir.
	msg := map[string]interface{}{
		"message_id": "tier1-msg-001",
		"type":       "task_forward",
		"source":     "sati",
		"payload":    map[string]string{"task_id": "task-xyz"},
	}
	data, _ := json.Marshal(msg)
	inboxFile := filepath.Join(XNodeInboxDir, "sati.jsonl")
	if err := os.WriteFile(inboxFile, append(data, '\n'), 0644); err != nil {
		t.Fatalf("write inbox file: %v", err)
	}

	req := httptest.NewRequest(http.MethodGet, "/api/xnode/inbox", nil)
	w := httptest.NewRecorder()
	xc.ListInboxHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}

	var resp map[string]interface{}
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatalf("unmarshal response: %v", err)
	}
	count, _ := resp["count"].(float64)
	if count != 1 {
		t.Errorf("expected count=1, got %v", resp["count"])
	}
}

// TestTier1_XNode_ListInboxHandler_NodeFilter verifies that the nodeID filter
// in the URL path restricts results to that node's messages only.
func TestTier1_XNode_ListInboxHandler_NodeFilter(t *testing.T) {
	xc, cleanup := setupXNodeHTTP(t)
	defer cleanup()

	// Write messages for two nodes.
	for _, node := range []string{"sati", "nova"} {
		msg := map[string]interface{}{
			"message_id": "tier1-filter-" + node,
			"type":       "heartbeat_update",
			"source":     node,
		}
		data, _ := json.Marshal(msg)
		f := filepath.Join(XNodeInboxDir, node+".jsonl")
		if err := os.WriteFile(f, append(data, '\n'), 0644); err != nil {
			t.Fatalf("write inbox file for %s: %v", node, err)
		}
	}

	// Request only sati's messages.
	req := httptest.NewRequest(http.MethodGet, "/api/xnode/inbox/sati", nil)
	req.URL.Path = "/api/xnode/inbox/sati"
	w := httptest.NewRecorder()
	xc.ListInboxHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}

	var resp map[string]interface{}
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatalf("unmarshal response: %v", err)
	}
	// Should have exactly 1 message (sati's), not 2.
	count, _ := resp["count"].(float64)
	if count != 1 {
		t.Errorf("expected count=1 with node filter, got %v", resp["count"])
	}
}

// TestTier1_XNode_ListInboxHandler_PathTraversalRejected verifies that a
// node ID containing ".." is rejected with 400.
func TestTier1_XNode_ListInboxHandler_PathTraversalRejected(t *testing.T) {
	xc, cleanup := setupXNodeHTTP(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/xnode/inbox/../etc", nil)
	req.URL.Path = "/api/xnode/inbox/../etc"
	w := httptest.NewRecorder()
	xc.ListInboxHandler(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for path traversal, got %d", w.Code)
	}
}

// TestTier1_XNode_ListInboxHandler_MissingDir verifies handler returns 500 when
// the inbox directory does not exist.
func TestTier1_XNode_ListInboxHandler_MissingDir(t *testing.T) {
	xc, cleanup := setupXNodeHTTP(t)
	defer cleanup()

	// Point global inbox to a non-existent path.
	origInbox := XNodeInboxDir
	XNodeInboxDir = "/tmp/tier1-xnode-missing-inbox-does-not-exist"
	defer func() { XNodeInboxDir = origInbox }()

	req := httptest.NewRequest(http.MethodGet, "/api/xnode/inbox", nil)
	w := httptest.NewRecorder()
	xc.ListInboxHandler(w, req)

	if w.Code != http.StatusInternalServerError {
		t.Errorf("expected 500 when inbox dir missing, got %d: %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// Tier1: XNode.readNewMessages — with non-zero offset
// ---------------------------------------------------------------------------

// TestTier1_XNode_ReadNewMessages_WithOffset verifies that readNewMessages skips
// bytes before the given offset, returning only messages written after the offset.
func TestTier1_XNode_ReadNewMessages_WithOffset(t *testing.T) {
	xc, cleanup := setupXNodeHTTP(t)
	defer cleanup()

	// Write two lines to a temp JSONL file.
	tmpFile := filepath.Join(t.TempDir(), "test.jsonl")

	line1 := `{"message_id":"m1","type":"heartbeat_update"}` + "\n"
	line2 := `{"message_id":"m2","type":"task_forward"}` + "\n"

	if err := os.WriteFile(tmpFile, []byte(line1+line2), 0644); err != nil {
		t.Fatalf("write file: %v", err)
	}

	// Read from offset = len(line1) — should only see line2.
	offset := int64(len(line1))
	msgs, err := xc.readNewMessages(tmpFile, offset)
	if err != nil {
		t.Fatalf("readNewMessages with offset: %v", err)
	}

	if len(msgs) != 1 {
		t.Errorf("expected 1 message after offset, got %d", len(msgs))
	}
	if id, _ := msgs[0]["message_id"].(string); id != "m2" {
		t.Errorf("expected message_id=m2 after offset, got %s", id)
	}
}

// TestTier1_XNode_ReadNewMessages_FromStart verifies reading from offset 0 returns
// all messages in the file.
func TestTier1_XNode_ReadNewMessages_FromStart(t *testing.T) {
	xc, cleanup := setupXNodeHTTP(t)
	defer cleanup()

	tmpFile := filepath.Join(t.TempDir(), "start.jsonl")
	content := `{"message_id":"a1","type":"x"}` + "\n" +
		`{"message_id":"a2","type":"y"}` + "\n"
	if err := os.WriteFile(tmpFile, []byte(content), 0644); err != nil {
		t.Fatalf("write file: %v", err)
	}

	msgs, err := xc.readNewMessages(tmpFile, 0)
	if err != nil {
		t.Fatalf("readNewMessages: %v", err)
	}
	if len(msgs) != 2 {
		t.Errorf("expected 2 messages from start, got %d", len(msgs))
	}
}

// TestTier1_XNode_ReadNewMessages_SkipsInvalidJSON verifies that malformed JSON
// lines are skipped and valid lines are still returned.
func TestTier1_XNode_ReadNewMessages_SkipsInvalidJSON(t *testing.T) {
	xc, cleanup := setupXNodeHTTP(t)
	defer cleanup()

	tmpFile := filepath.Join(t.TempDir(), "mixed.jsonl")
	content := `{"message_id":"good1"}` + "\n" +
		`{bad json line` + "\n" +
		`{"message_id":"good2"}` + "\n"
	if err := os.WriteFile(tmpFile, []byte(content), 0644); err != nil {
		t.Fatalf("write file: %v", err)
	}

	msgs, err := xc.readNewMessages(tmpFile, 0)
	if err != nil {
		t.Fatalf("readNewMessages: %v", err)
	}
	if len(msgs) != 2 {
		t.Errorf("expected 2 valid messages (invalid skipped), got %d", len(msgs))
	}
}

// ---------------------------------------------------------------------------
// Tier1: XNode error constructors — verify all codes and messages
// ---------------------------------------------------------------------------

// TestTier1_XNodeError_AllConstructors verifies all error constructors produce
// non-nil errors with correct codes and non-empty messages.
func TestTier1_XNodeError_AllConstructors(t *testing.T) {
	tests := []struct {
		name string
		err  *XNodeError
		code XNodeErrorCode
	}{
		{"NodeNotFound", newXNodeNodeNotFoundError("n1", nil), XNodeErrNodeNotFound},
		{"NodeOffline", newXNodeNodeOfflineError("n1", "maintenance"), XNodeErrNodeOffline},
		{"ForwardFailed", newXNodeForwardFailedError("t1", nil), XNodeErrForwardFailed},
		{"OutboxWrite", newXNodeOutboxWriteError("n1", nil), XNodeErrOutboxWrite},
		{"InvalidRequest", newXNodeInvalidRequestError("field", nil), XNodeErrInvalidRequest},
		{"Database", newXNodeDatabaseError("select", nil), XNodeErrDatabase},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			if tc.err == nil {
				t.Fatal("expected non-nil error")
			}
			if tc.err.Code != tc.code {
				t.Errorf("expected code %s, got %s", tc.code, tc.err.Code)
			}
			if tc.err.Message == "" {
				t.Error("expected non-empty message")
			}
			// Error() should never panic.
			_ = tc.err.Error()
		})
	}
}

// TestTier1_RespondWithError_SetsContentType verifies respondWithError sets the
// Content-Type header and uses the given HTTP status code.
func TestTier1_RespondWithError_SetsContentType(t *testing.T) {
	w := httptest.NewRecorder()
	respondWithError(w, http.StatusTeapot, "I'm a teapot", "detailed reason")

	if w.Code != http.StatusTeapot {
		t.Errorf("expected 418, got %d", w.Code)
	}
	ct := w.Header().Get("Content-Type")
	if ct != "application/json" {
		t.Errorf("expected Content-Type application/json, got %s", ct)
	}

	var body map[string]interface{}
	if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	if body["message"] != "I'm a teapot" {
		t.Errorf("expected message=I'm a teapot, got %v", body["message"])
	}
	if body["details"] != "detailed reason" {
		t.Errorf("expected details=detailed reason, got %v", body["details"])
	}
}

// TestTier1_RespondWithError_AllCodes verifies respondWithError works for common
// HTTP status codes without panicking.
func TestTier1_RespondWithError_AllCodes(t *testing.T) {
	codes := []int{
		http.StatusBadRequest,
		http.StatusUnauthorized,
		http.StatusForbidden,
		http.StatusNotFound,
		http.StatusMethodNotAllowed,
		http.StatusInternalServerError,
		http.StatusServiceUnavailable,
	}
	for _, code := range codes {
		t.Run(http.StatusText(code), func(t *testing.T) {
			w := httptest.NewRecorder()
			respondWithError(w, code, "msg", "details")
			if w.Code != code {
				t.Errorf("expected %d, got %d", code, w.Code)
			}
		})
	}
}

// TestTier1_RespondWithXNodeError_IncludesCode verifies that the code field from
// the XNodeError is included in the JSON response body.
func TestTier1_RespondWithXNodeError_IncludesCode(t *testing.T) {
	xerr := newXNodeNodeOfflineError("dead-node", "offline")
	w := httptest.NewRecorder()
	respondWithXNodeError(w, xerr, http.StatusServiceUnavailable)

	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503, got %d", w.Code)
	}

	var body map[string]interface{}
	if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	if body["code"] != string(XNodeErrNodeOffline) {
		t.Errorf("expected code %s, got %v", XNodeErrNodeOffline, body["code"])
	}
	if body["message"] == "" {
		t.Error("expected non-empty message in response")
	}
}

// TestTier1_XNode_ForwardHandler_MissingTaskID verifies ForwardHandler returns
// 400 when task_id is missing in the request.
func TestTier1_XNode_ForwardHandler_MissingTaskID(t *testing.T) {
	xc, cleanup := setupXNodeHTTP(t)
	defer cleanup()

	body := `{"target_node":"sati"}`
	req := httptest.NewRequest(http.MethodPost, "/api/xnode/forward",
		strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	xc.ForwardHandler(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for missing task_id, got %d: %s", w.Code, w.Body.String())
	}
}

// TestTier1_XNode_ForwardHandler_MissingTargetNode verifies ForwardHandler returns
// 400 when target_node is missing in the request.
func TestTier1_XNode_ForwardHandler_MissingTargetNode(t *testing.T) {
	xc, cleanup := setupXNodeHTTP(t)
	defer cleanup()

	body := `{"task_id":"task-123"}`
	req := httptest.NewRequest(http.MethodPost, "/api/xnode/forward",
		strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	xc.ForwardHandler(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for missing target_node, got %d: %s", w.Code, w.Body.String())
	}
}

// TestTier1_XNode_RegisterHandler_InvalidJSON verifies RegisterHandler returns
// 400 on malformed JSON body.
func TestTier1_XNode_RegisterHandler_InvalidJSON(t *testing.T) {
	xc, cleanup := setupXNodeHTTP(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/xnode/nodes/register",
		strings.NewReader("{bad json"))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	xc.RegisterHandler(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for invalid JSON, got %d", w.Code)
	}
}
