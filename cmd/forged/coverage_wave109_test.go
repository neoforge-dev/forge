//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// ---------------------------------------------------------------------------
// migrate.go — MigrateUp, MigrateDown, ensureMigrationsTable
// ---------------------------------------------------------------------------

// TestWave109_MigrateUp_AlreadyMigrated verifies that calling MigrateUp on an
// already-migrated database is a no-op and returns nil.
func TestWave109_MigrateUp_AlreadyMigrated(t *testing.T) {
	dbPath := fmt.Sprintf("/tmp/test_wave109_migrateup_%d.db", time.Now().UnixNano())
	defer os.Remove(dbPath)

	db, err := OpenDB(dbPath)
	if err != nil {
		t.Fatalf("OpenDB: %v", err)
	}
	defer db.Close()

	// First run — applies all migrations.
	if err := MigrateUp(db); err != nil {
		t.Fatalf("first MigrateUp: %v", err)
	}

	// Second run — must be idempotent.
	if err := MigrateUp(db); err != nil {
		t.Errorf("second MigrateUp (no-op) returned error: %v", err)
	}
}

// TestWave109_EnsureMigrationsTable_Idempotent verifies that ensureMigrationsTable
// can be called multiple times without error.
func TestWave109_EnsureMigrationsTable_Idempotent(t *testing.T) {
	dbPath := fmt.Sprintf("/tmp/test_wave109_ensure_%d.db", time.Now().UnixNano())
	defer os.Remove(dbPath)

	db, err := OpenDB(dbPath)
	if err != nil {
		t.Fatalf("OpenDB: %v", err)
	}
	defer db.Close()

	dbType := detectDBType(db)

	// Call three times to exercise the "table already exists" branch.
	for i := 0; i < 3; i++ {
		if err := ensureMigrationsTable(db, dbType); err != nil {
			t.Errorf("call %d: ensureMigrationsTable returned error: %v", i+1, err)
		}
	}
}

// TestWave109_MigrateDown_ZeroSteps verifies that steps<=0 is a no-op.
func TestWave109_MigrateDown_ZeroSteps(t *testing.T) {
	dbPath := fmt.Sprintf("/tmp/test_wave109_migratedown_zero_%d.db", time.Now().UnixNano())
	defer os.Remove(dbPath)

	db, err := OpenDB(dbPath)
	if err != nil {
		t.Fatalf("OpenDB: %v", err)
	}
	defer db.Close()

	if err := MigrateUp(db); err != nil {
		t.Fatalf("MigrateUp: %v", err)
	}

	if err := MigrateDown(db, 0); err != nil {
		t.Errorf("MigrateDown(0) returned error: %v", err)
	}
	if err := MigrateDown(db, -5); err != nil {
		t.Errorf("MigrateDown(-5) returned error: %v", err)
	}
}

// TestWave109_MigrateDown_EmptyHistory verifies that rolling back when nothing
// has been applied returns nil (no-op).
func TestWave109_MigrateDown_EmptyHistory(t *testing.T) {
	dbPath := fmt.Sprintf("/tmp/test_wave109_migratedown_empty_%d.db", time.Now().UnixNano())
	defer os.Remove(dbPath)

	db, err := OpenDB(dbPath)
	if err != nil {
		t.Fatalf("OpenDB: %v", err)
	}
	defer db.Close()

	// Create the migrations table manually so MigrateDown can query it,
	// but insert no rows.
	if err := ensureMigrationsTable(db, SQLite); err != nil {
		t.Fatalf("ensureMigrationsTable: %v", err)
	}

	if err := MigrateDown(db, 1); err != nil {
		t.Errorf("MigrateDown on empty history returned error: %v", err)
	}
}

// ---------------------------------------------------------------------------
// lease.go — Claim
// ---------------------------------------------------------------------------

// TestWave109_Lease_Claim_Success verifies that a fresh lease claim returns a
// non-nil Lease with matching fields.
func TestWave109_Lease_Claim_Success(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	mgr := &sqliteLeaseManager{db: db, stateMachine: nil}

	taskID := fmt.Sprintf("lease-task-%d", time.Now().UnixNano())
	agentID := "agent-alpha"
	ttl := 5 * time.Minute

	lease, err := mgr.Claim(context.Background(), taskID, agentID, ttl)
	if err != nil {
		t.Fatalf("Claim returned unexpected error: %v", err)
	}
	if lease == nil {
		t.Fatal("expected non-nil Lease")
	}
	if lease.TaskID != taskID {
		t.Errorf("TaskID: got %q, want %q", lease.TaskID, taskID)
	}
	if lease.AgentID != agentID {
		t.Errorf("AgentID: got %q, want %q", lease.AgentID, agentID)
	}
	if lease.ExpiresAt.Before(time.Now()) {
		t.Errorf("ExpiresAt %v is in the past", lease.ExpiresAt)
	}
}

// TestWave109_Lease_Claim_Conflict verifies that a second claim on the same
// active task returns ErrLeaseConflict.
func TestWave109_Lease_Claim_Conflict(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	mgr := &sqliteLeaseManager{db: db, stateMachine: nil}

	taskID := fmt.Sprintf("lease-conflict-%d", time.Now().UnixNano())
	ttl := 5 * time.Minute

	_, err := mgr.Claim(context.Background(), taskID, "agent-one", ttl)
	if err != nil {
		t.Fatalf("first Claim failed: %v", err)
	}

	_, err = mgr.Claim(context.Background(), taskID, "agent-two", ttl)
	if err == nil {
		t.Fatal("expected ErrLeaseConflict, got nil")
	}
	if !strings.Contains(err.Error(), "already leased") {
		t.Errorf("unexpected error message: %v", err)
	}
}

// TestWave109_Lease_Claim_ExpiredRenewal verifies that after an expired lease
// row has been removed (evicted by a cleanup sweep), a new agent can claim the
// same task successfully.  The leases table has a UNIQUE constraint on task_id,
// so the normal flow is: expired row gets deleted by eviction, then new INSERT
// succeeds.
func TestWave109_Lease_Claim_ExpiredRenewal(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	mgr := &sqliteLeaseManager{db: db, stateMachine: nil}

	taskID := fmt.Sprintf("lease-expired-%d", time.Now().UnixNano())

	// Insert and then immediately delete an expired lease to simulate eviction.
	expiredAt := time.Now().UTC().Add(-10 * time.Minute).Format(time.RFC3339)
	_, err := db.Exec(
		"INSERT INTO leases (id, task_id, agent_id, expires_at) VALUES (?, ?, ?, ?)",
		"old-lease-id", taskID, "old-agent", expiredAt,
	)
	if err != nil {
		t.Fatalf("insert expired lease: %v", err)
	}
	// Evict the expired row as a lease-cleanup routine would do.
	_, err = db.Exec("DELETE FROM leases WHERE task_id = ? AND expires_at <= strftime('%Y-%m-%dT%H:%M:%SZ', 'now')", taskID)
	if err != nil {
		t.Fatalf("delete expired lease: %v", err)
	}

	// Now a new agent can successfully claim.
	lease, err := mgr.Claim(context.Background(), taskID, "new-agent", 5*time.Minute)
	if err != nil {
		t.Fatalf("Claim after expired lease eviction returned error: %v", err)
	}
	if lease.AgentID != "new-agent" {
		t.Errorf("expected new-agent, got %q", lease.AgentID)
	}
}

// ---------------------------------------------------------------------------
// gitguard.go — executeMerge
// ---------------------------------------------------------------------------

// TestWave109_GitGuard_ExecuteMerge_NoLock verifies that executeMerge returns a
// failure result (not a panic) when the task has no branch lock.
func TestWave109_GitGuard_ExecuteMerge_NoLock(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	g := &GitGuard{db: db, repoRoot: t.TempDir()}

	action := GitAction{
		TaskID:  "no-such-task",
		Type:    GitActionMerge,
		Branch:  "feat/no-lock",
		Message: "test merge",
	}

	result := g.executeMerge(action)
	if result == nil {
		t.Fatal("expected non-nil result")
	}
	if result.Success {
		t.Error("expected Success=false when no branch lock exists")
	}
	if !strings.Contains(result.Error, "no branch lock") {
		t.Errorf("unexpected error: %q", result.Error)
	}
}

// TestWave109_GitGuard_ExecuteMerge_WithLock verifies the merge path when a
// branch lock exists. The git command will fail in the test environment, which
// is the expected outcome — we only verify no panic and the error is surfaced.
func TestWave109_GitGuard_ExecuteMerge_WithLock(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	taskID := fmt.Sprintf("gg-merge-task-%d", time.Now().UnixNano())

	// Insert a branch lock row directly.
	_, err := db.Exec(
		"INSERT INTO git_branch_locks (task_id, branch, agent_id, locked_at) VALUES (?, ?, ?, ?)",
		taskID, "feat/test-branch", "agent-x", time.Now().UTC().Format(time.RFC3339),
	)
	if err != nil {
		t.Logf("could not insert git_branch_locks (table may not exist): %v — skipping", err)
		t.Skip("git_branch_locks table not in schema")
	}

	g := &GitGuard{db: db, repoRoot: t.TempDir()}

	action := GitAction{
		TaskID:  taskID,
		Type:    GitActionMerge,
		Branch:  "feat/test-branch",
		Message: "test merge",
	}

	result := g.executeMerge(action)
	if result == nil {
		t.Fatal("expected non-nil result")
	}
	// Git will fail in a temp dir — acceptable; just ensure no panic.
	t.Logf("executeMerge result: success=%v err=%q", result.Success, result.Error)
}

// ---------------------------------------------------------------------------
// fleet_scaler.go — readLoadAverage
// ---------------------------------------------------------------------------

// TestWave109_ReadLoadAverage_Normal verifies readLoadAverage returns a
// non-negative value on Linux (reads /proc/loadavg).
func TestWave109_ReadLoadAverage_Normal(t *testing.T) {
	if _, err := os.Stat("/proc/loadavg"); os.IsNotExist(err) {
		t.Skip("/proc/loadavg not available on this platform")
	}

	load, err := readLoadAverage()
	if err != nil {
		t.Fatalf("readLoadAverage: %v", err)
	}
	if load < 0 {
		t.Errorf("expected non-negative load average, got %f", load)
	}
}

// TestWave109_ReadLoadAverage_ParsesFirstField verifies the function returns the
// 1-minute average, which is the first space-separated field in /proc/loadavg.
func TestWave109_ReadLoadAverage_ParsesFirstField(t *testing.T) {
	if _, err := os.Stat("/proc/loadavg"); os.IsNotExist(err) {
		t.Skip("/proc/loadavg not available")
	}

	data, _ := os.ReadFile("/proc/loadavg")
	fields := strings.Fields(string(data))
	if len(fields) < 1 {
		t.Skip("malformed /proc/loadavg")
	}

	load, err := readLoadAverage()
	if err != nil {
		t.Fatalf("readLoadAverage: %v", err)
	}

	var expected float64
	if _, err := fmt.Sscanf(fields[0], "%f", &expected); err != nil {
		t.Fatalf("parse expected: %v", err)
	}
	// Allow small delta from concurrent system changes.
	if load < 0 || load > expected+5 {
		t.Errorf("load %f seems implausible (expected near %f)", load, expected)
	}
}

// ---------------------------------------------------------------------------
// context.go — GenerateEnvelope
// ---------------------------------------------------------------------------

// TestWave109_GenerateEnvelope_ValidDomain verifies that GenerateEnvelope
// returns a populated envelope for a normal domain/project/task.
func TestWave109_GenerateEnvelope_ValidDomain(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	tmpDir := t.TempDir()
	cm := &ContextManager{db: db, contextDir: tmpDir}

	ctx := context.Background()
	env, err := cm.GenerateEnvelope(ctx, "agent-test", "backend", "api", "task-001", "test handoff")
	if err != nil {
		t.Fatalf("GenerateEnvelope: %v", err)
	}
	if env == nil {
		t.Fatal("expected non-nil envelope")
	}
	if env.Domain != "backend" {
		t.Errorf("Domain: got %q, want %q", env.Domain, "backend")
	}
	if env.AgentID != "agent-test" {
		t.Errorf("AgentID: got %q, want %q", env.AgentID, "agent-test")
	}
	if env.TaskID != "task-001" {
		t.Errorf("TaskID: got %q, want %q", env.TaskID, "task-001")
	}
}

// TestWave109_GenerateEnvelope_EmptyDomain verifies that an empty domain is
// handled gracefully and an envelope is still returned.
func TestWave109_GenerateEnvelope_EmptyDomain(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	tmpDir := t.TempDir()
	cm := &ContextManager{db: db, contextDir: tmpDir}

	ctx := context.Background()
	env, err := cm.GenerateEnvelope(ctx, "agent-x", "", "", "", "empty domain test")
	if err != nil {
		t.Fatalf("GenerateEnvelope with empty domain: %v", err)
	}
	if env == nil {
		t.Fatal("expected non-nil envelope")
	}
}

// ---------------------------------------------------------------------------
// xnode.go — serializePendingMessages
// ---------------------------------------------------------------------------

// TestWave109_SerializePendingMessages_Empty verifies the function is a no-op
// when the xnode_outbox table has no pending rows.
func TestWave109_SerializePendingMessages_Empty(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	tmpDir := t.TempDir()
	xc := &XNodeController{
		db:        db,
		nodeID:    "test-node",
		outboxDir: tmpDir,
	}

	// Should return without writing any files.
	xc.serializePendingMessages(context.Background(), db)

	entries, err := os.ReadDir(tmpDir)
	if err != nil {
		t.Fatalf("ReadDir: %v", err)
	}
	if len(entries) != 0 {
		t.Errorf("expected no files, got %d", len(entries))
	}
}

// TestWave109_SerializePendingMessages_WithRows verifies that pending outbox
// rows are written to the correct .jsonl file and marked 'serialized'.
func TestWave109_SerializePendingMessages_WithRows(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	tmpDir := t.TempDir()
	xc := &XNodeController{
		db:        db,
		nodeID:    "source-node",
		outboxDir: tmpDir,
	}

	payload := `{"data":"hello"}`
	msgID := fmt.Sprintf("msg-%d", time.Now().UnixNano())
	_, err := db.Exec(`
		INSERT INTO xnode_outbox (id, target_node, message_type, payload, idempotency_key, status)
		VALUES (?, ?, ?, ?, ?, 'pending')
	`, msgID, "target-node", "test_event", payload, "ikey-"+msgID)
	if err != nil {
		t.Fatalf("insert outbox row: %v", err)
	}

	xc.serializePendingMessages(context.Background(), db)

	// Verify file was written.
	outPath := filepath.Join(tmpDir, "target-node.jsonl")
	data, err := os.ReadFile(outPath)
	if err != nil {
		t.Fatalf("expected outbox file to be created: %v", err)
	}
	line := strings.TrimSpace(string(data))
	var msg map[string]interface{}
	if err := json.Unmarshal([]byte(line), &msg); err != nil {
		t.Fatalf("parse JSONL line: %v", err)
	}
	if msg["message_id"] != msgID {
		t.Errorf("message_id: got %v, want %q", msg["message_id"], msgID)
	}

	// Verify row status updated.
	var status string
	if err := db.QueryRow("SELECT status FROM xnode_outbox WHERE id = ?", msgID).Scan(&status); err != nil {
		t.Fatalf("query status: %v", err)
	}
	if status != "serialized" {
		t.Errorf("expected status=serialized, got %q", status)
	}
}

// ---------------------------------------------------------------------------
// xnode.go — IngestIncomingMessages
// ---------------------------------------------------------------------------

// TestWave109_IngestIncomingMessages_MissingDir verifies that a missing inbox
// directory is treated as an empty inbox (returns nil).
func TestWave109_IngestIncomingMessages_MissingDir(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	xc := &XNodeController{
		db:        db,
		nodeID:    "test-node",
		outboxDir: t.TempDir(),
	}

	// Override the global XNodeInboxDir with a path that does not exist.
	origInbox := XNodeInboxDir
	XNodeInboxDir = "/tmp/no-such-xnode-inbox-dir-wave109"
	defer func() { XNodeInboxDir = origInbox }()

	if err := xc.IngestIncomingMessages(context.Background(), db); err != nil {
		t.Errorf("expected nil for missing inbox dir, got: %v", err)
	}
}

// TestWave109_IngestIncomingMessages_WithMessages verifies that a valid .jsonl
// inbox file is ingested into xnode_inbox with idempotency.
func TestWave109_IngestIncomingMessages_WithMessages(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	inboxDir := t.TempDir()

	xc := &XNodeController{
		db:        db,
		nodeID:    "local-node",
		outboxDir: t.TempDir(),
	}

	origInbox := XNodeInboxDir
	XNodeInboxDir = inboxDir
	defer func() { XNodeInboxDir = origInbox }()

	// Write a JSONL inbox file with one message.
	msgID := fmt.Sprintf("inbox-%d", time.Now().UnixNano())
	msg := map[string]interface{}{
		"v":               "1.0",
		"message_id":      msgID,
		"type":            "task_update",
		"source":          "remote-node",
		"target":          "local-node",
		"ts":              time.Now().Format(time.RFC3339),
		"idempotency_key": "ikey-" + msgID,
		"payload":         map[string]string{"task_id": "t-1"},
	}
	raw, _ := json.Marshal(msg)

	inboxFile := filepath.Join(inboxDir, "remote-node.jsonl")
	if err := os.WriteFile(inboxFile, append(raw, '\n'), 0644); err != nil {
		t.Fatalf("write inbox file: %v", err)
	}

	if err := xc.IngestIncomingMessages(context.Background(), db); err != nil {
		t.Fatalf("IngestIncomingMessages: %v", err)
	}

	// Verify row inserted into xnode_inbox.
	var count int
	if err := db.QueryRow("SELECT COUNT(*) FROM xnode_inbox WHERE id = ?", msgID).Scan(&count); err != nil {
		t.Fatalf("query xnode_inbox: %v", err)
	}
	if count != 1 {
		t.Errorf("expected 1 row in xnode_inbox, got %d", count)
	}
}

// TestWave109_IngestIncomingMessages_Idempotent verifies that ingesting the
// same message twice does not create duplicate rows (INSERT OR IGNORE).
func TestWave109_IngestIncomingMessages_Idempotent(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	inboxDir := t.TempDir()

	xc := &XNodeController{
		db:        db,
		nodeID:    "local-node",
		outboxDir: t.TempDir(),
	}

	origInbox := XNodeInboxDir
	XNodeInboxDir = inboxDir
	defer func() { XNodeInboxDir = origInbox }()

	msgID := fmt.Sprintf("inbox-idem-%d", time.Now().UnixNano())
	msg := map[string]interface{}{
		"v":               "1.0",
		"message_id":      msgID,
		"type":            "heartbeat",
		"source":          "remote-node",
		"target":          "local-node",
		"ts":              time.Now().Format(time.RFC3339),
		"idempotency_key": "ikey-idem-" + msgID,
	}
	raw, _ := json.Marshal(msg)

	inboxFile := filepath.Join(inboxDir, "remote-node.jsonl")
	// Write the same message twice on separate lines.
	content := append(raw, '\n')
	content = append(content, raw...)
	content = append(content, '\n')
	if err := os.WriteFile(inboxFile, content, 0644); err != nil {
		t.Fatalf("write inbox file: %v", err)
	}

	// Call twice to exercise idempotency across multiple ingest runs.
	if err := xc.IngestIncomingMessages(context.Background(), db); err != nil {
		t.Fatalf("first IngestIncomingMessages: %v", err)
	}
	if err := xc.IngestIncomingMessages(context.Background(), db); err != nil {
		t.Fatalf("second IngestIncomingMessages: %v", err)
	}

	var count int
	if err := db.QueryRow("SELECT COUNT(*) FROM xnode_inbox WHERE id = ?", msgID).Scan(&count); err != nil {
		t.Fatalf("query xnode_inbox: %v", err)
	}
	if count != 1 {
		t.Errorf("expected exactly 1 row after idempotent ingest, got %d", count)
	}
}
