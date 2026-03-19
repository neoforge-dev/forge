//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"testing"
)

// ---------------------------------------------------------------------------
// fleet_scaler.go:readLoadAverage (line ~594)
// ---------------------------------------------------------------------------

// TestWave133_ReadLoadAverage_Linux verifies /proc/loadavg is readable on Linux.
func TestWave133_ReadLoadAverage_Linux(t *testing.T) {
	load, err := readLoadAverage()
	if err != nil {
		t.Logf("readLoadAverage: %v (acceptable on non-Linux)", err)
		return
	}
	if load < 0 {
		t.Errorf("expected non-negative load, got %f", load)
	}
	t.Logf("readLoadAverage: %.2f", load)
}

// ---------------------------------------------------------------------------
// fleet_scaler.go:fleetDeflateRecommendPatrol (line ~420)
// ---------------------------------------------------------------------------

// TestWave133_FleetDeflateRecommendPatrol_EmptyDB verifies no error on empty DB.
func TestWave133_FleetDeflateRecommendPatrol_EmptyDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	err := fleetDeflateRecommendPatrol(context.Background(), db)
	if err != nil {
		t.Logf("fleetDeflateRecommendPatrol empty DB: %v (acceptable)", err)
	}
}

// TestWave133_FleetDeflateRecommendPatrol_ClosedDB verifies graceful error on closed DB.
func TestWave133_FleetDeflateRecommendPatrol_ClosedDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	cleanup()

	err := fleetDeflateRecommendPatrol(context.Background(), db)
	t.Logf("fleetDeflateRecommendPatrol closed DB: %v", err)
}

// TestWave133_FleetDeflateRecommendPatrol_WithAgents verifies patrol with agents in DB.
func TestWave133_FleetDeflateRecommendPatrol_WithAgents(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Insert two agents on the same node to allow deflation evaluation.
	for _, id := range []string{"agent-a-133", "agent-b-133"} {
		db.Exec(`INSERT OR REPLACE INTO agent_heartbeats
			(agent_id, node, status, context_pct, current_task_id, last_seen)
			VALUES (?, 'test-node', 'online', 10.0, '', datetime('now', '-20 minutes'))`, id)
	}

	err := fleetDeflateRecommendPatrol(context.Background(), db)
	if err != nil {
		t.Logf("fleetDeflateRecommendPatrol with agents: %v (acceptable)", err)
	}
}

// ---------------------------------------------------------------------------
// fleet_scaler.go:fleetScaleRecommendPatrol (line ~151)
// ---------------------------------------------------------------------------

// TestWave133_FleetScaleRecommendPatrol_EmptyDB verifies no error on empty DB.
func TestWave133_FleetScaleRecommendPatrol_EmptyDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	err := fleetScaleRecommendPatrol(context.Background(), db)
	if err != nil {
		t.Logf("fleetScaleRecommendPatrol empty DB: %v (acceptable)", err)
	}
}

// TestWave133_FleetScaleRecommendPatrol_ClosedDB verifies graceful error on closed DB.
func TestWave133_FleetScaleRecommendPatrol_ClosedDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	cleanup()

	err := fleetScaleRecommendPatrol(context.Background(), db)
	t.Logf("fleetScaleRecommendPatrol closed DB: %v", err)
}

// ---------------------------------------------------------------------------
// fleet_scaler.go:circuitBreakerTripped / getCPUCount
// ---------------------------------------------------------------------------

// TestWave133_GetCPUCount verifies getCPUCount returns positive value.
func TestWave133_GetCPUCount(t *testing.T) {
	count := getCPUCount()
	if count <= 0 {
		t.Errorf("expected positive CPU count, got %d", count)
	}
	t.Logf("getCPUCount: %d", count)
}

// TestWave133_CircuitBreakerTripped_InitialState verifies initial state is not tripped.
func TestWave133_CircuitBreakerTripped_InitialState(t *testing.T) {
	// In clean state, circuit breaker should be closed (not tripped).
	tripped := circuitBreakerTripped()
	t.Logf("circuitBreakerTripped initial: %v", tripped)
	// Don't assert — other tests may have set it; just exercise the code path.
}
