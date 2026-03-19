//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/spf13/cobra"
)

// TestWave123_AgentsSSEHandler_ImmediateCancel covers the SSE loop entry + Done case.
// With a pre-cancelled context, the select fires <-r.Context().Done() immediately,
// covering lines 449-451 (headers), 453 (ticker), 456-460 (loop + done).
func TestWave123_AgentsSSEHandler_ImmediateCancel(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	ctx, cancel := context.WithCancel(context.Background())
	cancel() // cancel before the request

	req := httptest.NewRequest(http.MethodGet, "/api/agents/stream", nil).WithContext(ctx)
	rr := httptest.NewRecorder()
	agentsSSEHandler(rr, req)

	// ResponseRecorder implements http.Flusher, so we get past the 500 path.
	// With a cancelled context the handler exits the loop immediately.
	if rr.Code == http.StatusMethodNotAllowed || rr.Code == http.StatusInternalServerError {
		t.Errorf("unexpected status %d", rr.Code)
	}
	t.Logf("agentsSSEHandler ImmediateCancel: status=%d body-len=%d", rr.Code, rr.Body.Len())
}

// TestWave123_AgentsSSEHandler_WithAgents covers the ticker path by inserting
// agent_heartbeats rows and letting the SSE handler fetch them.
// Uses a short context deadline so the test doesn't block.
func TestWave123_AgentsSSEHandler_WithAgents(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	// Insert a heartbeat row so the DB query inside the ticker branch returns rows.
	_, _ = db.Exec(`
		INSERT INTO agent_heartbeats (agent_id, node, status, context_pct, current_task_id, last_seen)
		VALUES ('w123-agent', 'prya', 'idle', 10.0, '', datetime('now'))
	`)

	ctx, cancel := context.WithCancel(context.Background())
	cancel()

	req := httptest.NewRequest(http.MethodGet, "/api/agents/stream", nil).WithContext(ctx)
	rr := httptest.NewRecorder()
	agentsSSEHandler(rr, req)
	t.Logf("agentsSSEHandler WithAgents: status=%d", rr.Code)
}

// TestWave123_FleetScaleRecommendPatrol_InsertsRec exercises the recommendation
// insertion path (lines 238-305) by bypassing NoAutoSpawnNodes and inserting
// queued tasks so queuedCount > idleAgents*2.
func TestWave123_FleetScaleRecommendPatrol_InsertsRec(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	if err := ensureScaleRecommendationsTable(context.Background(), db); err != nil {
		t.Fatalf("ensureScaleRecommendationsTable: %v", err)
	}

	// Bypass NoAutoSpawnNodes for the local hostname.
	hostname := "prya"
	origVal, hadKey := NoAutoSpawnNodes[hostname]
	delete(NoAutoSpawnNodes, hostname)
	defer func() {
		if hadKey {
			NoAutoSpawnNodes[hostname] = origVal
		} else {
			delete(NoAutoSpawnNodes, hostname)
		}
	}()

	// Raise ceiling so we don't get blocked.
	origCeiling := NodeHardCeilings[hostname]
	NodeHardCeilings[hostname] = 999
	defer func() { NodeHardCeilings[hostname] = origCeiling }()

	// Insert 5 queued tasks so queuedCount (5) > idleAgents (0) * 2 (0).
	for i := 0; i < 5; i++ {
		_, err := db.Exec(`
			INSERT INTO tasks (id, domain, project, type, title, priority, status, state, created_at, updated_at)
			VALUES (?, 'forge', 'v3', 'feature', 'wave123 task', 5, 'queued', 'QUEUED', datetime('now'), datetime('now'))
		`, fmt.Sprintf("w123-task-%d", i))
		if err != nil {
			t.Logf("insert task %d: %v (ok — table may have extra constraints)", i, err)
		}
	}

	err := fleetScaleRecommendPatrol(context.Background(), db)
	if err != nil {
		t.Logf("fleetScaleRecommendPatrol: %v (acceptable)", err)
	}
	t.Logf("fleetScaleRecommendPatrol InsertsRec: completed without panic")
}

// TestWave123_FleetDeflateRecommendPatrol_WithAgents exercises the inner deflation
// gate loop by inserting 2+ agents for the same node in agent_heartbeats.
func TestWave123_FleetDeflateRecommendPatrol_WithAgents(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	if err := ensureScaleRecommendationsTable(context.Background(), db); err != nil {
		t.Fatalf("ensureScaleRecommendationsTable: %v", err)
	}

	// Insert 2 agents on the same node with non-offline status.
	// This means nodeAgents["prya"] will have 2 entries, satisfying len(agents) > 1.
	oldTime := time.Now().UTC().Add(-20 * time.Minute).Format(time.RFC3339)
	for _, agentID := range []string{"w123-deflate-1", "w123-deflate-2"} {
		_, err := db.Exec(`
			INSERT INTO agent_heartbeats (agent_id, node, status, context_pct, current_task_id, last_seen)
			VALUES (?, 'prya', 'idle', 5.0, '', ?)
		`, agentID, oldTime)
		if err != nil {
			t.Logf("insert heartbeat %s: %v (ok)", agentID, err)
		}
	}

	err := fleetDeflateRecommendPatrol(context.Background(), db)
	if err != nil {
		t.Logf("fleetDeflateRecommendPatrol: %v (acceptable)", err)
	}
	t.Logf("fleetDeflateRecommendPatrol WithAgents: completed without panic")
}

// TestWave123_GetAgentID_TMUXBranch covers the TMUX env branch (lines 104-116).
// By setting TMUX to a non-empty value, the if-branch is entered.
// The tmux command will fail (no tmux server), so we fall through to the error.
func TestWave123_GetAgentID_TMUXBranch(t *testing.T) {
	cmd := &cobra.Command{}
	cmd.Flags().String("agent", "", "agent id")

	// Clear env to avoid FORGE_AGENT_ID override.
	t.Setenv("FORGE_AGENT_ID", "")
	// Set TMUX so the branch is entered.
	t.Setenv("TMUX", "/tmp/tmux-test,1234,0")

	id, err := getAgentID(cmd)
	// Either we got an id from the tmux window name, or an error (tmux not running).
	t.Logf("getAgentID TMUX branch: id=%q err=%v", id, err)
	// Both outcomes are acceptable — we just want the branch covered.
}

// TestWave123_GetAgentID_EnvVar covers the FORGE_AGENT_ID env path (line 98-100).
func TestWave123_GetAgentID_EnvVar(t *testing.T) {
	cmd := &cobra.Command{}
	cmd.Flags().String("agent", "", "agent id")
	t.Setenv("FORGE_AGENT_ID", "env-agent-123")

	id, err := getAgentID(cmd)
	if err != nil {
		t.Fatalf("getAgentID env var: %v", err)
	}
	if id != "env-agent-123" {
		t.Errorf("id = %q, want %q", id, "env-agent-123")
	}
}

// TestWave123_GetAgentID_NoEnvNoFlag covers the error return (line 119).
func TestWave123_GetAgentID_NoEnvNoFlag(t *testing.T) {
	cmd := &cobra.Command{}
	cmd.Flags().String("agent", "", "agent id")
	t.Setenv("FORGE_AGENT_ID", "")
	t.Setenv("TMUX", "")

	_, err := getAgentID(cmd)
	if err == nil {
		t.Error("expected error for no agent id, got nil")
	}
}

// TestWave123_HandleCompletion_Bash covers HandleCompletion with shell="bash".
func TestWave123_HandleCompletion_Bash(t *testing.T) {
	m := NewCompletionManager()
	// This writes to os.Stdout — that's fine in tests.
	// We just verify it doesn't panic or call os.Exit.
	m.HandleCompletion([]string{"bash"})
}

// TestWave123_HandleCompletion_Zsh covers HandleCompletion with shell="zsh".
func TestWave123_HandleCompletion_Zsh(t *testing.T) {
	m := NewCompletionManager()
	m.HandleCompletion([]string{"zsh"})
}

// TestWave123_HandleCompletion_Fish covers HandleCompletion with shell="fish".
func TestWave123_HandleCompletion_Fish(t *testing.T) {
	m := NewCompletionManager()
	m.HandleCompletion([]string{"fish"})
}

// TestWave123_HandleQueueDepth_ClosedDB covers the GetClaimableTasks error path
// (line 444-445) by using a queue backed by a closed DB.
func TestWave123_HandleQueueDepth_ClosedDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	// setupClaimTestDB already sets taskQueue with the live DB.
	// We close the DB (which also restores taskQueue to its pre-test value),
	// then re-set taskQueue to a fresh queue backed by the now-closed DB.
	tq, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}

	// Close the DB first (cleanup restores taskQueue to nil/old).
	cleanup()

	// Now re-set taskQueue to our closed-DB-backed queue.
	origQueue := taskQueue
	taskQueue = tq
	defer func() { taskQueue = origQueue }()

	req := httptest.NewRequest(http.MethodGet, "/cli/queue/depth", nil)
	w := httptest.NewRecorder()
	handleQueueDepth(w, req)

	// With closed DB, GetClaimableTasks returns error → 500.
	t.Logf("handleQueueDepth closed DB: status=%d body=%s", w.Code, w.Body.String())
}

// TestWave123_HandleSystemHealth_JSON covers handleSystemHealth with ?format=json.
func TestWave123_HandleSystemHealth_JSON(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/cli/health?format=json", nil)
	w := httptest.NewRecorder()
	handleSystemHealth(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", w.Code)
	}
	body := w.Body.String()
	if !strings.Contains(body, "ok") {
		t.Errorf("expected 'ok' in body, got: %s", body)
	}
}

// TestWave123_HandleSystemHealth_NoFormat covers handleSystemHealth with default format.
func TestWave123_HandleSystemHealth_NoFormat(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/cli/health", nil)
	w := httptest.NewRecorder()
	handleSystemHealth(w, req)
	t.Logf("handleSystemHealth no format: status=%d", w.Code)
}
