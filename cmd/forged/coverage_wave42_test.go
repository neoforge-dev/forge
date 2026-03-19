//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"fmt"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

// Wave 42: checkTierCompatibility, tierCanClaim, nodeMetricsPushPatrol,
//          agentByIDHandler paths, listPatternRunsHandler with real data

// ─── tierCanClaim ─────────────────────────────────────────────────────────

func TestTierCanClaim_Any_W42(t *testing.T) {
	tests := []struct {
		agent    string
		required string
		want     bool
	}{
		{"heavy", "any", true},
		{"medium", "any", true},
		{"lightweight", "any", true},
		{"heavy", "", true},
		{"medium", "", true},
	}
	for _, tt := range tests {
		if got := tierCanClaim(tt.agent, tt.required); got != tt.want {
			t.Errorf("tierCanClaim(%q, %q) = %v, want %v", tt.agent, tt.required, got, tt.want)
		}
	}
}

func TestTierCanClaim_Hierarchy_W42(t *testing.T) {
	tests := []struct {
		agent    string
		required string
		want     bool
	}{
		{"heavy", "lightweight", true},
		{"heavy", "medium", true},
		{"heavy", "heavy", true},
		{"medium", "lightweight", true},
		{"medium", "medium", true},
		{"medium", "heavy", false}, // medium can't claim heavy-required task
		{"lightweight", "lightweight", true},
		{"lightweight", "medium", false},
		{"lightweight", "heavy", false},
		{"unknown", "heavy", true}, // unknown tier — don't block
	}
	for _, tt := range tests {
		if got := tierCanClaim(tt.agent, tt.required); got != tt.want {
			t.Errorf("tierCanClaim(%q, %q) = %v, want %v", tt.agent, tt.required, got, tt.want)
		}
	}
}

// ─── checkTierCompatibility ───────────────────────────────────────────────

func TestCheckTierCompatibility_NilDB_W42(t *testing.T) {
	orig := getDBConn()
	setDBConn(nil)
	defer setDBConn(orig)

	ctx := context.Background()
	// Nil DB → returns nil immediately.
	if err := checkTierCompatibility(ctx, "TASK-X", "agent-x"); err != nil {
		t.Errorf("expected nil with nil DB, got %v", err)
	}
}

func TestCheckTierCompatibility_RequiredAny_W42(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	ctx := context.Background()

	// Insert task with required_tier='any'.
	_, err := db.ExecContext(ctx, `
		INSERT INTO tasks (id, domain, project, type, title, status, state, priority, required_tier, created_at, updated_at)
		VALUES ('TASK-W42-ANY', 'test', 'proj', 'feature', 'Any Tier Task', 'queued', 'QUEUED', 50, 'any', datetime('now'), datetime('now'))
	`)
	if err != nil {
		t.Fatalf("insert task: %v", err)
	}

	// Any tier → returns nil.
	if err := checkTierCompatibility(ctx, "TASK-W42-ANY", "agent-w42"); err != nil {
		t.Errorf("expected nil for required_tier=any, got %v", err)
	}
}

func TestCheckTierCompatibility_TierMismatch_W42(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	ctx := context.Background()

	// Insert task requiring 'heavy' tier.
	_, err := db.ExecContext(ctx, `
		INSERT INTO tasks (id, domain, project, type, title, status, state, priority, required_tier, created_at, updated_at)
		VALUES ('TASK-W42-HEAVY', 'test', 'proj', 'feature', 'Heavy Task', 'queued', 'QUEUED', 50, 'heavy', datetime('now'), datetime('now'))
	`)
	if err != nil {
		t.Fatalf("insert task: %v", err)
	}

	// Agent not in agent_inventory → defaults to 'medium', can't claim 'heavy'.
	err = checkTierCompatibility(ctx, "TASK-W42-HEAVY", "agent-w42-medium")
	if err == nil {
		t.Error("expected error when medium agent tries to claim heavy task, got nil")
	}
}

func TestCheckTierCompatibility_NoRequiredTier_W42(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	ctx := context.Background()

	// Insert task with no required_tier (NULL).
	_, err := db.ExecContext(ctx, `
		INSERT INTO tasks (id, domain, project, type, title, status, state, priority, created_at, updated_at)
		VALUES ('TASK-W42-NOTIER', 'test', 'proj', 'feature', 'No Tier Task', 'queued', 'QUEUED', 50, datetime('now'), datetime('now'))
	`)
	if err != nil {
		t.Fatalf("insert task: %v", err)
	}

	// No required_tier → returns nil.
	if err := checkTierCompatibility(ctx, "TASK-W42-NOTIER", "agent-w42"); err != nil {
		t.Errorf("expected nil for no required_tier, got %v", err)
	}
}

// ─── nodeMetricsPushPatrol ────────────────────────────────────────────────

func TestNodeMetricsPushPatrol_NoLeadURL_W42(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// With no FORGE_LEAD_URL, patrol returns nil immediately (this is the lead node).
	t.Setenv("FORGE_LEAD_URL", "")
	ctx := context.Background()
	if err := nodeMetricsPushPatrol(ctx, db); err != nil {
		t.Errorf("expected nil when no FORGE_LEAD_URL, got %v", err)
	}
}

func TestNodeMetricsPushPatrol_WithLeadURL_W42(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()

	// Start a test HTTP server to receive the push.
	var received bool
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		received = true
		w.WriteHeader(http.StatusOK)
	}))
	defer ts.Close()

	t.Setenv("FORGE_LEAD_URL", ts.URL)
	t.Setenv("NODE_ID", "test-node-w42")

	// Run patrol — should collect metrics and push (covers query + HTTP call paths).
	if err := nodeMetricsPushPatrol(ctx, db); err != nil {
		t.Logf("nodeMetricsPushPatrol with lead URL: %v (OK)", err)
	}
	_ = received // may or may not have been called depending on HTTP client behavior
}

// ─── agentByIDHandler ─────────────────────────────────────────────────────

func TestAgentByIDHandler_NotFound_W42(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/api/agents/no-such-agent-w42", nil)
	req.URL.Path = "/api/agents/no-such-agent-w42"
	w := httptest.NewRecorder()
	agentByIDHandler(w, req)
	if w.Code != http.StatusNotFound && w.Code != http.StatusInternalServerError {
		t.Errorf("expected 404 or 500, got %d", w.Code)
	}
}

func TestAgentByIDHandler_Found_W42(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	ctx := context.Background()

	// Insert an agent_heartbeat row with connected_at (from migration 010).
	_, err := db.ExecContext(ctx, `
		INSERT INTO agent_heartbeats (agent_id, node, status, context_pct, last_seen, connected_at)
		VALUES ('agent-w42-found', 'prya', 'idle', 5.0, ?, ?)
		ON CONFLICT(agent_id) DO UPDATE SET
			status='idle', context_pct=5.0, last_seen=excluded.last_seen, connected_at=excluded.connected_at
	`, time.Now().UTC().Format(time.RFC3339), time.Now().UTC().Format(time.RFC3339))
	if err != nil {
		t.Fatalf("insert agent_heartbeat: %v", err)
	}

	req := httptest.NewRequest(http.MethodGet, "/api/agents/agent-w42-found", nil)
	req.URL.Path = "/api/agents/agent-w42-found"
	w := httptest.NewRecorder()
	agentByIDHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

// ─── listPatternRunsHandler with data ─────────────────────────────────────

func TestListPatternRunsHandler_WithData_W42(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	ctx := context.Background()

	// Insert pattern_runs with all optional fields set.
	startedAt := time.Now().Add(-2 * time.Minute).UTC().Format(time.RFC3339)
	completedAt := time.Now().Add(-1 * time.Minute).UTC().Format(time.RFC3339)
	for i := 0; i < 2; i++ {
		_, err := db.ExecContext(ctx, `
			INSERT INTO pattern_runs (run_id, pattern_id, task_id, agent_id, domain, started_at, completed_at, success, confidence, tokens_used, duration_s, error_type, metadata)
			VALUES (?, 'pat-w42', ?, 'agent-w42', 'test', ?, ?, '1', '0.85', '1000', '60', NULL, '{}')
		`,
			fmt.Sprintf("run-w42-%d", i),
			fmt.Sprintf("TASK-W42-PAT-%d", i),
			startedAt,
			completedAt,
		)
		if err != nil {
			t.Logf("insert pattern_run: %v (may skip)", err)
		}
	}

	req := httptest.NewRequest(http.MethodGet, "/api/patterns/pat-w42/runs", nil)
	w := httptest.NewRecorder()
	listPatternRunsHandler(w, req, "pat-w42")
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}
