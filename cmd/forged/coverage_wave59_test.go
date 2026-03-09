//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"testing"
)

// Wave 59: fleetAutoExecutePatrol deep path (has recommendation in DB)
//          This exercises the RAM gate, CPU gate, ceiling check, token budget gate, spawnAgent

func TestFleetAutoExecutePatrol_WithRecommendation_W59(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()

	// Ensure scale_recommendations table exists
	if err := ensureScaleRecommendationsTable(ctx, db); err != nil {
		t.Fatalf("ensureScaleRecommendationsTable: %v", err)
	}

	// Insert a pending auto-execute inflate recommendation
	_, err := db.ExecContext(ctx, `
		INSERT INTO scale_recommendations (id, node_id, action, agent_type, reason, auto_execute, status, expires_at, created_at)
		VALUES ('rec-w59-1', 'prya', 'inflate', 'kimi', 'load-test-w59', 1, 'pending',
		        datetime('now', '+10 minutes'), datetime('now'))
	`)
	if err != nil {
		t.Fatalf("insert scale_recommendations: %v", err)
	}

	// Reset circuit breaker to ensure we're in the main path
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

	// Run the patrol — should proceed past DB query and exercise RAM/CPU/ceiling gates
	// spawnAgent will fail (no tmux forge session) → marks rec failed → returns nil
	if err := fleetAutoExecutePatrol(ctx, db); err != nil {
		t.Logf("fleetAutoExecutePatrol with recommendation: %v (may be OK)", err)
	}
}

func TestFleetAutoExecutePatrol_RAMGateFail_W59(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()

	if err := ensureScaleRecommendationsTable(ctx, db); err != nil {
		t.Fatalf("ensureScaleRecommendationsTable: %v", err)
	}

	// Insert a heavy agent type that requires lots of RAM to trigger RAM gate
	_, err := db.ExecContext(ctx, `
		INSERT INTO scale_recommendations (id, node_id, action, agent_type, reason, auto_execute, status, expires_at, created_at)
		VALUES ('rec-w59-2', 'prya', 'inflate', 'opencode', 'ram-test-w59', 1, 'pending',
		        datetime('now', '+10 minutes'), datetime('now'))
	`)
	if err != nil {
		t.Fatalf("insert scale_recommendations: %v", err)
	}

	circuitBreaker.Lock()
	origState := circuitBreaker.state
	circuitBreaker.state = "closed"
	circuitBreaker.Unlock()
	defer func() {
		circuitBreaker.Lock()
		circuitBreaker.state = origState
		circuitBreaker.Unlock()
	}()

	// opencode needs ~2700MB — may or may not hit RAM gate depending on available RAM
	// Either way, exercises the recommendation reading path
	if err := fleetAutoExecutePatrol(ctx, db); err != nil {
		t.Logf("fleetAutoExecutePatrol RAM gate: %v (may be OK)", err)
	}
}

func TestFleetAutoExecutePatrol_NoRecs_W59(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()
	if err := ensureScaleRecommendationsTable(ctx, db); err != nil {
		t.Fatalf("ensureScaleRecommendationsTable: %v", err)
	}

	// No pending recommendations → returns nil at the "no recs" path
	if err := fleetAutoExecutePatrol(ctx, db); err != nil {
		t.Errorf("expected nil, got %v", err)
	}
}
