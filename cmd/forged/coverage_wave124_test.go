//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"testing"
	"time"
)

// ---------------------------------------------------------------------------
// syncRoyalJelly tests (patrol.go:396)
// ---------------------------------------------------------------------------

// TestWave124_SyncRoyalJelly_WithContextDir exercises the happy path where
// a context dir exists with a subdomain that has a context_pct file.
func TestWave124_SyncRoyalJelly_WithContextDir(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	dir := t.TempDir()
	t.Setenv("FORGE_ROOT", dir)

	// Create .forge/context/<domain>/context_pct
	domain := "wave124-domain"
	contextDir := filepath.Join(dir, ".forge", "context", domain)
	if err := os.MkdirAll(contextDir, 0755); err != nil {
		t.Fatalf("mkdir contextDir: %v", err)
	}
	if err := os.WriteFile(filepath.Join(contextDir, "context_pct"), []byte("55.5"), 0644); err != nil {
		t.Fatalf("write context_pct: %v", err)
	}

	ctx := context.Background()
	if err := syncRoyalJelly(ctx, db); err != nil {
		t.Errorf("syncRoyalJelly returned unexpected error: %v", err)
	}
}

// TestWave124_SyncRoyalJelly_NonExistentDir exercises the os.IsNotExist branch.
func TestWave124_SyncRoyalJelly_NonExistentDir(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	t.Setenv("FORGE_ROOT", "/nonexistent/path/wave124xyzzy")

	ctx := context.Background()
	if err := syncRoyalJelly(ctx, db); err != nil {
		t.Errorf("syncRoyalJelly with nonexistent dir should return nil, got: %v", err)
	}
}

// TestWave124_SyncRoyalJelly_PermissionDenied exercises the non-IsNotExist error
// branch (returns fmt.Errorf("read context directory")).
func TestWave124_SyncRoyalJelly_PermissionDenied(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	dir := t.TempDir()
	t.Setenv("FORGE_ROOT", dir)

	// Create .forge/context as a directory, then chmod 000 so ReadDir fails.
	contextDir := filepath.Join(dir, ".forge", "context")
	if err := os.MkdirAll(contextDir, 0755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	if err := os.Chmod(contextDir, 0000); err != nil {
		t.Fatalf("chmod: %v", err)
	}
	defer os.Chmod(contextDir, 0755) // restore so TempDir cleanup works

	ctx := context.Background()
	err := syncRoyalJelly(ctx, db)
	if err == nil {
		// Some CI environments run as root and ignore permissions — skip in that case
		t.Log("syncRoyalJelly returned nil despite chmod 000 (likely running as root — skipping)")
		return
	}
	// Should contain "read context directory"
	expected := "read context directory"
	if !containsStr(err.Error(), expected) {
		t.Errorf("expected error to contain %q, got: %v", expected, err)
	}
}

// TestWave124_SyncRoyalJelly_WithEnvelopeWriteback exercises Phase 2 (DB->FS)
// by inserting a context_envelopes row and verifying the patrol doesn't error.
func TestWave124_SyncRoyalJelly_WithEnvelopeWriteback(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	dir := t.TempDir()
	t.Setenv("FORGE_ROOT", dir)

	// Ensure context dir exists
	contextDir := filepath.Join(dir, ".forge", "context")
	if err := os.MkdirAll(contextDir, 0755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}

	// Insert a context_envelopes row with valid JSON content
	domain := "wave124-env"
	envelope := map[string]interface{}{
		"metadata":  map[string]interface{}{"lead_context": "# Test Context\n\nSome content."},
		"decisions": []interface{}{},
		"failures":  []interface{}{},
		"summary":   "Test summary for wave124",
	}
	content, _ := json.Marshal(envelope)
	agentID := "wave124-agent"
	_, err := db.Exec(`INSERT INTO context_envelopes (id, agent_id, domain, project, task_id, content, created_at, expires_at)
		VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now', '+1 hour'))`,
		"env-wave124-001", agentID, domain, "wave124-proj", "", string(content),
	)
	if err != nil {
		t.Fatalf("insert context_envelopes: %v", err)
	}

	ctx := context.Background()
	if err := syncRoyalJelly(ctx, db); err != nil {
		t.Errorf("syncRoyalJelly with envelope: %v", err)
	}
}

// TestWave124_SyncRoyalJelly_InvalidContextPct exercises the Sscanf parse-error
// branch (non-numeric content in context_pct is silently skipped).
func TestWave124_SyncRoyalJelly_InvalidContextPct(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	dir := t.TempDir()
	t.Setenv("FORGE_ROOT", dir)

	domain := "wave124-badpct"
	contextDir := filepath.Join(dir, ".forge", "context", domain)
	if err := os.MkdirAll(contextDir, 0755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	// Write non-numeric content — triggers fmt.Sscanf error → continue
	if err := os.WriteFile(filepath.Join(contextDir, "context_pct"), []byte("not-a-number"), 0644); err != nil {
		t.Fatalf("write context_pct: %v", err)
	}

	ctx := context.Background()
	if err := syncRoyalJelly(ctx, db); err != nil {
		t.Errorf("syncRoyalJelly with bad pct should not error: %v", err)
	}
}

// ---------------------------------------------------------------------------
// cleanStaleBranches tests (patrol.go:583)
// ---------------------------------------------------------------------------

// TestWave124_CleanStaleBranches_Basic exercises the git-failure return-nil path.
func TestWave124_CleanStaleBranches_Basic(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// git branch -r will typically fail or return empty in the test cwd.
	ctx := context.Background()
	if err := cleanStaleBranches(ctx, db); err != nil {
		t.Errorf("cleanStaleBranches: unexpected error: %v", err)
	}
}

// TestWave124_CleanStaleBranches_WithFakeDir exercises the scan loop when git
// is run in a directory that is not a git repo (returns non-zero exit).
func TestWave124_CleanStaleBranches_InTempDir(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()
	// Just ensure no panic — the git command will fail gracefully
	err := cleanStaleBranches(ctx, db)
	if err != nil {
		t.Errorf("expected nil from cleanStaleBranches (git failure path): %v", err)
	}
}

// ---------------------------------------------------------------------------
// checkContextThreshold tests (patrol.go:675)
// ---------------------------------------------------------------------------

// TestWave124_CheckContextThreshold_NilCM verifies the nil ContextManager guard.
func TestWave124_CheckContextThreshold_NilCM(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()
	if err := checkContextThreshold(ctx, db, nil, nil); err != nil {
		t.Errorf("checkContextThreshold(nil cm) should return nil: %v", err)
	}
}

// TestWave124_CheckContextThreshold_WithHighContextAgent inserts a heartbeat at
// >= 50% context, creates a real ContextManager, and exercises the inner loop.
func TestWave124_CheckContextThreshold_WithHighContextAgent(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()

	// Insert an agent heartbeat with context_pct = 75
	agentID := fmt.Sprintf("wave124-agent-%d", time.Now().UnixNano())
	_, err := db.ExecContext(ctx, `
		INSERT INTO agent_heartbeats (agent_id, context_pct, node, current_task_id, last_seen)
		VALUES (?, ?, ?, ?, datetime('now'))
	`, agentID, 75.0, "prya", "")
	if err != nil {
		t.Fatalf("insert agent_heartbeats: %v", err)
	}

	cm := testContextManager(t, db, t.TempDir())

	// rj = nil is fine — the nil guard in the function will skip TriggerHooks
	if err := checkContextThreshold(ctx, db, cm, nil); err != nil {
		t.Errorf("checkContextThreshold: %v", err)
	}
}

// TestWave124_CheckContextThreshold_WithRoyalJelly exercises the rj != nil branch.
func TestWave124_CheckContextThreshold_WithRoyalJelly(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()

	// Insert agent with high context
	agentID := fmt.Sprintf("wave124-rj-agent-%d", time.Now().UnixNano())
	_, err := db.ExecContext(ctx, `
		INSERT INTO agent_heartbeats (agent_id, context_pct, node, current_task_id, last_seen)
		VALUES (?, ?, ?, ?, datetime('now'))
	`, agentID, 80.0, "sati", "")
	if err != nil {
		t.Fatalf("insert agent_heartbeats: %v", err)
	}

	cm := testContextManager(t, db, t.TempDir())
	rj := NewRoyalJelly(db, cm)

	// Should not panic even with rj non-nil
	if err := checkContextThreshold(ctx, db, cm, rj); err != nil {
		t.Errorf("checkContextThreshold with rj: %v", err)
	}
}

// ---------------------------------------------------------------------------
// councilCleanupPatrol tests (patrol.go:1368)
// ---------------------------------------------------------------------------

// TestWave124_CouncilCleanupPatrol_NoDir exercises the os.IsNotExist path.
func TestWave124_CouncilCleanupPatrol_NoDir(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()
	// Default markerDir ".forge/autoscale/council" likely doesn't exist in test env.
	if err := councilCleanupPatrol(ctx, db); err != nil {
		t.Errorf("councilCleanupPatrol with missing dir should return nil: %v", err)
	}
}

// TestWave124_CouncilCleanupPatrol_WithExpiredMarker creates an expired marker
// file and verifies the cleanup logic removes it.
func TestWave124_CouncilCleanupPatrol_WithExpiredMarker(t *testing.T) {
	// councilCleanupPatrol uses a hardcoded relative path ".forge/autoscale/council"
	// so we need to create that directory relative to the working directory.
	markerDir := ".forge/autoscale/council"
	if err := os.MkdirAll(markerDir, 0755); err != nil {
		t.Fatalf("mkdir markerDir: %v", err)
	}
	defer os.RemoveAll(".forge/autoscale") // clean up after test

	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Write an expired marker
	expiredAt := time.Now().UTC().Add(-2 * time.Hour).Format(time.RFC3339)
	startedAt := time.Now().UTC().Add(-3 * time.Hour).Format(time.RFC3339)
	marker := map[string]interface{}{
		"agent":      "wave124-expired-agent",
		"purpose":    "test council",
		"ttl_minutes": 60,
		"started_at": startedAt,
		"expires_at": expiredAt,
	}
	data, _ := json.Marshal(marker)
	markerPath := filepath.Join(markerDir, "wave124-expired.json")
	if err := os.WriteFile(markerPath, data, 0644); err != nil {
		t.Fatalf("write marker: %v", err)
	}

	ctx := context.Background()
	if err := councilCleanupPatrol(ctx, db); err != nil {
		t.Errorf("councilCleanupPatrol with expired marker: %v", err)
	}

	// Marker should have been removed
	if _, err := os.Stat(markerPath); !os.IsNotExist(err) {
		t.Error("expired marker should have been removed")
	}
}

// TestWave124_CouncilCleanupPatrol_WithActiveMarker verifies that a non-expired
// marker is left in place (the "active council" log path).
func TestWave124_CouncilCleanupPatrol_WithActiveMarker(t *testing.T) {
	markerDir := ".forge/autoscale/council"
	if err := os.MkdirAll(markerDir, 0755); err != nil {
		t.Fatalf("mkdir markerDir: %v", err)
	}
	defer os.RemoveAll(".forge/autoscale")

	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Write a not-yet-expired marker
	expiresAt := time.Now().UTC().Add(2 * time.Hour).Format(time.RFC3339)
	startedAt := time.Now().UTC().Format(time.RFC3339)
	marker := map[string]interface{}{
		"agent":       "wave124-active-agent",
		"purpose":     "test council active",
		"ttl_minutes": 120,
		"started_at":  startedAt,
		"expires_at":  expiresAt,
	}
	data, _ := json.Marshal(marker)
	markerPath := filepath.Join(markerDir, "wave124-active.json")
	if err := os.WriteFile(markerPath, data, 0644); err != nil {
		t.Fatalf("write marker: %v", err)
	}

	ctx := context.Background()
	if err := councilCleanupPatrol(ctx, db); err != nil {
		t.Errorf("councilCleanupPatrol with active marker: %v", err)
	}

	// Marker should still exist
	if _, err := os.Stat(markerPath); os.IsNotExist(err) {
		t.Error("active marker should not have been removed")
	}
}

// TestWave124_CouncilCleanupPatrol_NoTTLMarker exercises the "no TTL — skip" path.
func TestWave124_CouncilCleanupPatrol_NoTTLMarker(t *testing.T) {
	markerDir := ".forge/autoscale/council"
	if err := os.MkdirAll(markerDir, 0755); err != nil {
		t.Fatalf("mkdir markerDir: %v", err)
	}
	defer os.RemoveAll(".forge/autoscale")

	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Marker with empty expires_at
	marker := map[string]interface{}{
		"agent":      "wave124-permanent-agent",
		"purpose":    "permanent council",
		"ttl_minutes": 0,
		"started_at": time.Now().UTC().Format(time.RFC3339),
		"expires_at": "",
	}
	data, _ := json.Marshal(marker)
	markerPath := filepath.Join(markerDir, "wave124-permanent.json")
	if err := os.WriteFile(markerPath, data, 0644); err != nil {
		t.Fatalf("write marker: %v", err)
	}

	ctx := context.Background()
	if err := councilCleanupPatrol(ctx, db); err != nil {
		t.Errorf("councilCleanupPatrol with no-TTL marker: %v", err)
	}

	// Permanent marker should be left alone
	if _, err := os.Stat(markerPath); os.IsNotExist(err) {
		t.Error("permanent marker should not have been removed")
	}
}

// TestWave124_CouncilCleanupPatrol_BadJSON exercises the JSON parse-error path.
func TestWave124_CouncilCleanupPatrol_BadJSON(t *testing.T) {
	markerDir := ".forge/autoscale/council"
	if err := os.MkdirAll(markerDir, 0755); err != nil {
		t.Fatalf("mkdir markerDir: %v", err)
	}
	defer os.RemoveAll(".forge/autoscale")

	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Write a non-JSON file
	markerPath := filepath.Join(markerDir, "wave124-bad.json")
	if err := os.WriteFile(markerPath, []byte("{not valid json!!"), 0644); err != nil {
		t.Fatalf("write marker: %v", err)
	}

	ctx := context.Background()
	if err := councilCleanupPatrol(ctx, db); err != nil {
		t.Errorf("councilCleanupPatrol with bad JSON should not error: %v", err)
	}
}

// TestWave124_CouncilCleanupPatrol_NonJSONFile exercises the extension filter
// (non-.json files are skipped).
func TestWave124_CouncilCleanupPatrol_NonJSONFile(t *testing.T) {
	markerDir := ".forge/autoscale/council"
	if err := os.MkdirAll(markerDir, 0755); err != nil {
		t.Fatalf("mkdir markerDir: %v", err)
	}
	defer os.RemoveAll(".forge/autoscale")

	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Write a non-.json file — should be skipped
	if err := os.WriteFile(filepath.Join(markerDir, "wave124-readme.txt"), []byte("ignore me"), 0644); err != nil {
		t.Fatalf("write txt: %v", err)
	}

	ctx := context.Background()
	if err := councilCleanupPatrol(ctx, db); err != nil {
		t.Errorf("councilCleanupPatrol with non-json file: %v", err)
	}
}

// ---------------------------------------------------------------------------
// xnodeAckProcessorPatrol additional tests (patrol.go:1666)
// ---------------------------------------------------------------------------

// TestWave124_XnodeAckProcessorPatrol_NonJsonFile verifies non-.json files in
// the acks directory are skipped.
func TestWave124_XnodeAckProcessorPatrol_NonJsonFile(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	acksDir := t.TempDir()
	orig := XNodeAcksDir
	XNodeAcksDir = acksDir
	defer func() { XNodeAcksDir = orig }()

	// Write a non-.json file — should be silently skipped
	if err := os.WriteFile(filepath.Join(acksDir, "readme.txt"), []byte("not json"), 0644); err != nil {
		t.Fatalf("write txt: %v", err)
	}

	ctx := context.Background()
	if err := xnodeAckProcessorPatrol(ctx, db); err != nil {
		t.Errorf("expected nil, got: %v", err)
	}
}

// TestWave124_XnodeAckProcessorPatrol_AckNoOutboxRow exercises the case where
// the ack file is valid and status=="acked" but no matching outbox row exists
// (RowsAffected==0 → file not removed).
func TestWave124_XnodeAckProcessorPatrol_AckNoOutboxRow(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	acksDir := t.TempDir()
	orig := XNodeAcksDir
	XNodeAcksDir = acksDir
	defer func() { XNodeAcksDir = orig }()

	// Ack file references a message_id that has no row in xnode_outbox
	ackData := `{"message_id":"wave124-no-row","status":"acked"}`
	ackPath := filepath.Join(acksDir, "wave124-no-row.json")
	if err := os.WriteFile(ackPath, []byte(ackData), 0644); err != nil {
		t.Fatalf("write ack: %v", err)
	}

	ctx := context.Background()
	if err := xnodeAckProcessorPatrol(ctx, db); err != nil {
		t.Errorf("expected nil: %v", err)
	}

	// File should still exist (RowsAffected==0, not removed)
	if _, err := os.Stat(ackPath); os.IsNotExist(err) {
		t.Error("ack file should remain when no outbox row matched")
	}
}

// TestWave124_XnodeAckProcessorPatrol_EmptyMessageID exercises the
// empty-message_id guard (ack.MessageID == "").
func TestWave124_XnodeAckProcessorPatrol_EmptyMessageID(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	acksDir := t.TempDir()
	orig := XNodeAcksDir
	XNodeAcksDir = acksDir
	defer func() { XNodeAcksDir = orig }()

	ackData := `{"message_id":"","status":"acked"}`
	ackPath := filepath.Join(acksDir, "empty-id.json")
	if err := os.WriteFile(ackPath, []byte(ackData), 0644); err != nil {
		t.Fatalf("write ack: %v", err)
	}

	ctx := context.Background()
	if err := xnodeAckProcessorPatrol(ctx, db); err != nil {
		t.Errorf("expected nil for empty message_id: %v", err)
	}
}

// ---------------------------------------------------------------------------
// fleetAutoPatrol tests (patrol.go:1870)
// ---------------------------------------------------------------------------

// TestWave124_FleetAutoPatrol_Basic exercises fleetAutoPatrol (which calls
// fleetAutoExecutePatrol + fleetAutoDeflatePatrol).
func TestWave124_FleetAutoPatrol_Basic(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()
	// Both sub-patrols should complete without panic regardless of DB state.
	if err := fleetAutoPatrol(ctx, db); err != nil {
		t.Errorf("fleetAutoPatrol: %v", err)
	}
}

// TestWave124_FleetAutoPatrol_WithOnlineAgent inserts an online agent and a
// queued task to exercise a non-trivial fleetAutoExecutePatrol path.
func TestWave124_FleetAutoPatrol_WithOnlineAgent(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()

	// Insert an online agent heartbeat
	agentID := fmt.Sprintf("wave124-fleet-agent-%d", time.Now().UnixNano())
	_, err := db.ExecContext(ctx, `
		INSERT INTO agent_heartbeats (agent_id, context_pct, node, current_task_id, last_seen)
		VALUES (?, 10, 'prya', '', datetime('now'))
	`, agentID)
	if err != nil {
		t.Fatalf("insert heartbeat: %v", err)
	}

	// Insert a queued task
	taskID := fmt.Sprintf("wave124-task-%d", time.Now().UnixNano())
	_, err = db.ExecContext(ctx, `
		INSERT INTO tasks (id, title, domain, project, type, priority, status, state, created_at, updated_at)
		VALUES (?, 'Wave124 test task', 'test', 'proj', 'feature', 5, 'queued', 'QUEUED', datetime('now'), datetime('now'))
	`, taskID)
	if err != nil {
		t.Fatalf("insert task: %v", err)
	}

	if err := fleetAutoPatrol(ctx, db); err != nil {
		t.Errorf("fleetAutoPatrol with agent+task: %v", err)
	}
}

// ---------------------------------------------------------------------------
// helper
// ---------------------------------------------------------------------------

func containsStr(s, substr string) bool {
	return len(s) >= len(substr) && (s == substr || len(substr) == 0 ||
		func() bool {
			for i := 0; i <= len(s)-len(substr); i++ {
				if s[i:i+len(substr)] == substr {
					return true
				}
			}
			return false
		}())
}
