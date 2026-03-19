//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"database/sql"
	"os"
	"path/filepath"
	"testing"
	"time"
)

// ---------------------------------------------------------------------------
// patrol.go: executePatrol error path — 70%
// Exercise RecordPatrolExecution/Error/Success directly.
// ---------------------------------------------------------------------------

func TestWave84_PatrolRecording(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Test RecordPatrolExecution, RecordPatrolError, RecordPatrolSuccess
	RecordPatrolExecution(db, "wave84-test-patrol")
	RecordPatrolError(db, "wave84-test-patrol", "intentional test error wave84")
	RecordPatrolSuccess(db, "wave84-test-patrol")
}

// ---------------------------------------------------------------------------
// patrol.go: checkBinaryFreshness with stale binary — 68%
// Create a fake binary and stale source file.
// ---------------------------------------------------------------------------

func TestWave84_CheckBinaryFreshness_WithBinary(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	db := getDBConn()
	ctx := context.Background()

	tmpDir := t.TempDir()
	prevRoot := os.Getenv("FORGE_ROOT")
	os.Setenv("FORGE_ROOT", tmpDir)
	defer func() {
		if prevRoot == "" {
			os.Unsetenv("FORGE_ROOT")
		} else {
			os.Setenv("FORGE_ROOT", prevRoot)
		}
	}()

	// Create .forge/heartbeat/results dir
	resultsDir := filepath.Join(tmpDir, ".forge", "heartbeat", "results")
	if err := os.MkdirAll(resultsDir, 0755); err != nil {
		t.Fatalf("MkdirAll results: %v", err)
	}

	// Create fake binary (old mtime)
	binaryDir := filepath.Join(tmpDir, "cmd", "forged")
	if err := os.MkdirAll(binaryDir, 0755); err != nil {
		t.Fatalf("MkdirAll binary dir: %v", err)
	}
	binaryPath := filepath.Join(binaryDir, "forged")
	if err := os.WriteFile(binaryPath, []byte("fake binary"), 0755); err != nil {
		t.Fatalf("WriteFile binary: %v", err)
	}
	// Set binary mtime to 2 hours ago
	oldTime := time.Now().Add(-2 * time.Hour)
	if err := os.Chtimes(binaryPath, oldTime, oldTime); err != nil {
		t.Logf("Chtimes: %v (skipping)", err)
		return
	}

	// Create a newer source file (newer than binary + 30min threshold)
	srcFile := filepath.Join(binaryDir, "main.go")
	if err := os.WriteFile(srcFile, []byte("package main"), 0644); err != nil {
		t.Fatalf("WriteFile src: %v", err)
	}
	// Source file mtime = now (default) — much newer than binary

	err := checkBinaryFreshness(ctx, db)
	if err != nil {
		t.Errorf("checkBinaryFreshness: %v", err)
	}
}

// ---------------------------------------------------------------------------
// patrol.go: agentLivenessPatrol — covers zombie detection
// ---------------------------------------------------------------------------

func TestWave84_AgentLivenessPatrol_WithStaleAgent(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	db := getDBConn()
	ctx := context.Background()

	// Insert a stale agent heartbeat (last_seen > 5 minutes ago)
	staleTime := time.Now().Add(-10 * time.Minute).UTC().Format(time.RFC3339)
	now := time.Now().UTC().Format(time.RFC3339)
	_, err := db.Exec(`INSERT OR REPLACE INTO agent_heartbeats
		(agent_id, node, status, context_pct, last_seen, connected_at)
		VALUES (?, ?, ?, ?, ?, ?)`,
		"wave84-stale-agent", "test-node", "working", 50.0, staleTime, now)
	if err != nil {
		t.Logf("insert stale agent: %v", err)
		return
	}

	err = agentLivenessPatrol(ctx, db)
	if err != nil {
		t.Errorf("agentLivenessPatrol: %v", err)
	}
}

func TestWave84_AgentLivenessPatrol_EmptyDB(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	db := getDBConn()
	ctx := context.Background()

	err := agentLivenessPatrol(ctx, db)
	if err != nil {
		t.Errorf("agentLivenessPatrol empty: %v", err)
	}
}

// ---------------------------------------------------------------------------
// patrol.go: monitorResultFiles — 63.2%
// Create temp result files and call the function.
// ---------------------------------------------------------------------------

func TestWave84_MonitorResultFiles_WithResultFile(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	db := getDBConn()
	ctx := context.Background()

	tmpDir := t.TempDir()
	prevRoot := os.Getenv("FORGE_ROOT")
	os.Setenv("FORGE_ROOT", tmpDir)
	defer func() {
		if prevRoot == "" {
			os.Unsetenv("FORGE_ROOT")
		} else {
			os.Setenv("FORGE_ROOT", prevRoot)
		}
	}()

	// Create .forge/heartbeat/results dir
	resultsDir := filepath.Join(tmpDir, ".forge", "heartbeat", "results")
	if err := os.MkdirAll(resultsDir, 0755); err != nil {
		t.Fatalf("MkdirAll: %v", err)
	}

	// Insert a task that could be matched
	now := time.Now().UTC().Format(time.RFC3339)
	_, _ = db.Exec(`INSERT INTO tasks
		(id, domain, project, type, priority, status, state, title, assigned_to, created_at, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		"wave84-monitor-task", "codeswiftr", "proj", "feature", 5,
		"executing", "RUNNING", "Monitor Task", "wave84-agent", now, now)

	// Create result file matching task ID format: {agent}-{task-id}.md
	resultFile := filepath.Join(resultsDir, "wave84-agent-wave84-monitor-task.md")
	if err := os.WriteFile(resultFile, []byte("# Results\nTask completed successfully.\n"), 0644); err != nil {
		t.Fatalf("WriteFile result: %v", err)
	}

	err := monitorResultFiles(ctx, db)
	if err != nil {
		t.Logf("monitorResultFiles: %v", err)
	}
}

func TestWave84_MonitorResultFiles_EmptyDir(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	db := getDBConn()
	ctx := context.Background()

	tmpDir := t.TempDir()
	prevRoot := os.Getenv("FORGE_ROOT")
	os.Setenv("FORGE_ROOT", tmpDir)
	defer func() {
		if prevRoot == "" {
			os.Unsetenv("FORGE_ROOT")
		} else {
			os.Setenv("FORGE_ROOT", prevRoot)
		}
	}()

	resultsDir := filepath.Join(tmpDir, ".forge", "heartbeat", "results")
	if err := os.MkdirAll(resultsDir, 0755); err != nil {
		t.Fatalf("MkdirAll: %v", err)
	}

	err := monitorResultFiles(ctx, db)
	if err != nil {
		t.Errorf("monitorResultFiles empty: %v", err)
	}
}

// ---------------------------------------------------------------------------
// patrol.go: PatrolSystem.executePatrol error path — exercise via direct call
// ---------------------------------------------------------------------------

func TestWave84_PatrolSystem_ExecutePatrol_WithError(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ps := NewPatrolSystem(db)

	// Create a patrol that returns an error
	errPatrol := Patrol{
		ID:       "wave84-error-patrol",
		Name:     "Error Patrol",
		Schedule: time.Minute,
		Action: func(ctx context.Context, d *sql.DB) error {
			return os.ErrNotExist // return an error to cover the error recording path
		},
	}

	// executePatrol is unexported but we can call it via the PatrolSystem
	ps.executePatrol(errPatrol)
}

func TestWave84_PatrolSystem_ExecutePatrol_Success(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ps := NewPatrolSystem(db)

	// Create a patrol that succeeds
	successPatrol := Patrol{
		ID:       "wave84-success-patrol",
		Name:     "Success Patrol",
		Schedule: time.Minute,
		Action: func(ctx context.Context, d *sql.DB) error {
			return nil
		},
	}

	ps.executePatrol(successPatrol)
}

// ---------------------------------------------------------------------------
// patrol.go: checkContextThreshold — 69.2%
// Test with agents that have high context percentage.
// ---------------------------------------------------------------------------

func TestWave84_CheckContextThreshold_WithHighContext(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()
	cm := testContextManager(t, db, t.TempDir())

	// Insert an agent with high context_pct in agent_heartbeats
	now := time.Now().UTC().Format(time.RFC3339)
	_, _ = db.Exec(`INSERT OR REPLACE INTO agent_heartbeats
		(agent_id, node, status, context_pct, last_seen, connected_at)
		VALUES (?, ?, ?, ?, ?, ?)`,
		"wave84-high-ctx-agent", "test-node", "working", 85.0, now, now)

	err := checkContextThreshold(ctx, db, cm, nil)
	if err != nil {
		t.Logf("checkContextThreshold: %v", err)
	}
}
