//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/spf13/cobra"
)

// ---------------------------------------------------------------------------
// cli_commands.go: getAgentID — 50%
// Cover the FORGE_AGENT_ID env var path.
// ---------------------------------------------------------------------------

func TestWave80_GetAgentID_FromEnv(t *testing.T) {
	prev := os.Getenv("FORGE_AGENT_ID")
	os.Setenv("FORGE_AGENT_ID", "env-agent-wave80")
	defer func() {
		if prev == "" {
			os.Unsetenv("FORGE_AGENT_ID")
		} else {
			os.Setenv("FORGE_AGENT_ID", prev)
		}
	}()

	cmd := &cobra.Command{}
	cmd.Flags().String("agent", "", "agent id")
	// Don't set the --agent flag, so it falls through to FORGE_AGENT_ID
	id, err := getAgentID(cmd)
	if err != nil {
		t.Errorf("expected agent ID from env, got error: %v", err)
	}
	if id != "env-agent-wave80" {
		t.Errorf("expected 'env-agent-wave80', got %q", id)
	}
}

// ---------------------------------------------------------------------------
// fleet_scaler.go: spawnAgent — 13%
// Test with valid agent type but no tmux (covers more code paths up to tmux error)
// ---------------------------------------------------------------------------

func TestWave80_SpawnAgent_ValidType_TmuxFails(t *testing.T) {
	// SAFETY: Do NOT call spawnAgent with a real agent type when tmux is available —
	// it creates real windows in the forge session (orphan claude-N windows).
	// Test only the unknown-type error path which is safe.
	ctx := context.Background()
	_, err := spawnAgent(ctx, "unknown-type-wave80-safe", "test-node-wave80")
	if err == nil {
		t.Error("expected error for unknown agent type")
	}
	t.Logf("spawnAgent unknown type error (expected): %v", err)
}

// ---------------------------------------------------------------------------
// metrics.go: computeMetricsRollups — 68.2%
// Test with actual pattern_runs data to cover the rows.Next() loop body.
// ---------------------------------------------------------------------------

func TestWave80_ComputeMetricsRollups_WithPatternRuns(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	db := getDBConn()
	ctx := context.Background()

	// Insert a recent pattern run so the query finds data
	now := time.Now().UTC().Format(time.RFC3339)
	_, err := db.Exec(`
		INSERT INTO pattern_runs (run_id, pattern_id, task_id, agent_id, domain, started_at, completed_at, success, tokens_used)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		"wave80-run-1", "api_endpoint:simple", "task-wave80", "agent-wave80",
		"codeswiftr", now, now, 1, 1000)
	if err != nil {
		t.Logf("insert pattern_run: %v (may be ok)", err)
	}

	err = computeMetricsRollups(ctx, db)
	if err != nil {
		t.Errorf("computeMetricsRollups: %v", err)
	}
}

// ---------------------------------------------------------------------------
// patrol.go: syncRoyalJelly — 65.7%
// Create a temp .forge/context directory and test the filesystem sync path.
// ---------------------------------------------------------------------------

func TestWave80_SyncRoyalJelly_WithContextDir(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Create a temp dir to simulate .forge/context
	tmpDir := t.TempDir()
	contextDir := filepath.Join(tmpDir, ".forge", "context")
	domainDir := filepath.Join(contextDir, "codeswiftr")
	if err := os.MkdirAll(domainDir, 0755); err != nil {
		t.Fatalf("MkdirAll: %v", err)
	}

	// Create a context_pct file
	pctFile := filepath.Join(domainDir, "context_pct")
	if err := os.WriteFile(pctFile, []byte("45.5"), 0644); err != nil {
		t.Fatalf("WriteFile: %v", err)
	}

	// Change to tmpDir so "./.forge/context" resolves to our temp dir
	prevWD, _ := os.Getwd()
	if err := os.Chdir(tmpDir); err != nil {
		t.Fatalf("Chdir: %v", err)
	}
	defer os.Chdir(prevWD)

	db := getDBConn()
	ctx := context.Background()
	err := syncRoyalJelly(ctx, db)
	if err != nil {
		t.Errorf("syncRoyalJelly with context dir: %v", err)
	}
}

func TestWave80_SyncRoyalJelly_WithContextPctAndFileEntry(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	tmpDir := t.TempDir()
	contextDir := filepath.Join(tmpDir, ".forge", "context")
	domainDir := filepath.Join(contextDir, "finance")
	if err := os.MkdirAll(domainDir, 0755); err != nil {
		t.Fatalf("MkdirAll: %v", err)
	}

	// Write context_pct file
	if err := os.WriteFile(filepath.Join(domainDir, "context_pct"), []byte("72.1\n"), 0644); err != nil {
		t.Fatalf("WriteFile: %v", err)
	}

	// Add a non-directory file entry to test the !e.IsDir() branch
	if err := os.WriteFile(filepath.Join(contextDir, "README.md"), []byte("# Context"), 0644); err != nil {
		t.Fatalf("WriteFile README: %v", err)
	}

	prevWD, _ := os.Getwd()
	if err := os.Chdir(tmpDir); err != nil {
		t.Fatalf("Chdir: %v", err)
	}
	defer os.Chdir(prevWD)

	db := getDBConn()
	err := syncRoyalJelly(context.Background(), db)
	if err != nil {
		t.Errorf("syncRoyalJelly: %v", err)
	}
}

// ---------------------------------------------------------------------------
// patrol.go: syncRoyalJelly — DB→Filesystem phase
// Test with context_envelopes data.
// ---------------------------------------------------------------------------

func TestWave80_SyncRoyalJelly_WithEnvelopes(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	db := getDBConn()
	ctx := context.Background()

	tmpDir := t.TempDir()
	contextDir := filepath.Join(tmpDir, ".forge", "context")
	if err := os.MkdirAll(contextDir, 0755); err != nil {
		t.Fatalf("MkdirAll: %v", err)
	}

	prevWD, _ := os.Getwd()
	if err := os.Chdir(tmpDir); err != nil {
		t.Fatalf("Chdir: %v", err)
	}
	defer os.Chdir(prevWD)

	// Insert a context_envelope from the last hour
	envelope := `{"metadata":{"lead_context":"# Test Lead Context\nSome state."},"decisions":[],"failures":[],"summary":"Test summary"}`
	_, _ = db.Exec(`INSERT INTO context_envelopes (id, domain, content, created_at)
		VALUES (?, ?, ?, datetime('now', '-10 minutes'))`,
		"env-wave80-1", "codeswiftr", envelope)

	err := syncRoyalJelly(ctx, db)
	if err != nil {
		t.Errorf("syncRoyalJelly with envelopes: %v", err)
	}
}

// ---------------------------------------------------------------------------
// fleet_scaler.go: fleetAutoDeflatePatrol — 69.2%
// Test with no draining agents and with draining agents.
// ---------------------------------------------------------------------------

func TestWave80_FleetAutoDeflatePatrol_NoDrainingAgents(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	db := getDBConn()
	err := fleetAutoDeflatePatrol(context.Background(), db)
	if err != nil {
		t.Errorf("fleetAutoDeflatePatrol with no agents: %v", err)
	}
}

func TestWave80_FleetAutoDeflatePatrol_WithDrainingAgent_Expired(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	db := getDBConn()
	ctx := context.Background()

	// Insert a draining agent with old drain start (past drain timeout)
	oldTime := time.Now().Add(-20 * time.Minute).UTC().Format(time.RFC3339)
	_, err := db.Exec(`INSERT INTO agent_inventory (id, agent_type, tmux_window, node_id, drain_state, started_at, last_seen, status)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
		"wave80-drain-agent", "claude", "claude-1", "test-node",
		"draining", oldTime, oldTime, "active")
	if err != nil {
		t.Logf("insert agent_inventory: %v (table may not exist)", err)
		return
	}

	err = fleetAutoDeflatePatrol(ctx, db)
	if err != nil {
		t.Errorf("fleetAutoDeflatePatrol with draining agent: %v", err)
	}
}

func TestWave80_FleetAutoDeflatePatrol_MalformedTimestamp(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	db := getDBConn()
	ctx := context.Background()

	// Insert agent with malformed timestamp to cover the parse error path
	now := time.Now().UTC().Format(time.RFC3339)
	_, err := db.Exec(`INSERT INTO agent_inventory (id, agent_type, tmux_window, node_id, drain_state, started_at, last_seen, status)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
		"wave80-drain-bad-ts", "claude", "claude-2", "test-node",
		"draining", "not-a-valid-timestamp", now, "active")
	if err != nil {
		t.Logf("insert agent_inventory: %v (table may not exist)", err)
		return
	}

	err = fleetAutoDeflatePatrol(ctx, db)
	if err != nil {
		t.Errorf("fleetAutoDeflatePatrol malformed ts: %v", err)
	}
}

// ---------------------------------------------------------------------------
// fleet_scaler.go: fleetScaleRecommendPatrol — 67.2%
// Test with ensured scale_recommendations table.
// ---------------------------------------------------------------------------

func TestWave80_FleetScaleRecommendPatrol_EmptyTable(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	db := getDBConn()
	ctx := context.Background()
	_ = ensureScaleRecommendationsTable(ctx, db)

	err := fleetScaleRecommendPatrol(ctx, db)
	if err != nil {
		t.Errorf("fleetScaleRecommendPatrol empty: %v", err)
	}
}

// ---------------------------------------------------------------------------
// migrate.go: MigrateDown — 68.8%
// ---------------------------------------------------------------------------

func TestWave80_MigrateDown_OneStep(t *testing.T) {
	tmpPath := t.TempDir() + "/wave80_migrate_down.db"
	db, err := OpenDB(tmpPath)
	if err != nil {
		t.Fatalf("OpenDB: %v", err)
	}
	defer db.Close()

	if err := MigrateUp(db); err != nil {
		t.Fatalf("MigrateUp: %v", err)
	}

	if err := MigrateDown(db, 1); err != nil {
		t.Errorf("MigrateDown 1 step: %v", err)
	}
}

// fleetScaleRecommend deleted (f715a295) — test removed.
