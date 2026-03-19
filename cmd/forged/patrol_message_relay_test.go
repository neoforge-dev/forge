//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	_ "github.com/mattn/go-sqlite3"
)

// ── Test DB setup ─────────────────────────────────────────────────────────────

func setupRelayTestDB(t *testing.T) *sql.DB {
	t.Helper()
	db, err := sql.Open("sqlite3", ":memory:")
	if err != nil {
		t.Fatalf("open in-memory db: %v", err)
	}

	_, err = db.Exec(`
		CREATE TABLE IF NOT EXISTS tasks (
			id TEXT PRIMARY KEY,
			domain TEXT NOT NULL DEFAULT '',
			project TEXT NOT NULL DEFAULT '',
			type TEXT NOT NULL DEFAULT 'research',
			title TEXT NOT NULL DEFAULT '',
			description TEXT DEFAULT '',
			priority INTEGER DEFAULT 5,
			status TEXT NOT NULL DEFAULT 'queued',
			state TEXT NOT NULL DEFAULT 'QUEUED',
			lane TEXT,
			assigned_to TEXT,
			plan_version INTEGER DEFAULT 0,
			plan_id TEXT,
			envelope_id TEXT,
			created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
			updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
		);
		CREATE TABLE IF NOT EXISTS task_events (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			task_id TEXT NOT NULL,
			domain TEXT DEFAULT '',
			project TEXT DEFAULT '',
			event_type TEXT NOT NULL,
			payload TEXT DEFAULT '{}',
			created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
		);
		CREATE TABLE IF NOT EXISTS nodes (
			id TEXT PRIMARY KEY,
			hostname TEXT NOT NULL DEFAULT '',
			address TEXT NOT NULL DEFAULT '',
			status TEXT NOT NULL DEFAULT 'offline',
			last_heartbeat TIMESTAMP DEFAULT CURRENT_TIMESTAMP
		);
	`)
	if err != nil {
		t.Fatalf("create tables: %v", err)
	}
	return db
}

// setupRelayTempDir creates a temp dir and sets FORGE_ROOT so all relay
// helpers write under it.  Returns the dir path and a cleanup func.
func setupRelayTempDir(t *testing.T) string {
	t.Helper()
	dir := t.TempDir()
	t.Setenv("FORGE_ROOT", dir)
	return dir
}

// ── SendMessage ───────────────────────────────────────────────────────────────

func TestSendMessageCreatesFile(t *testing.T) {
	dir := setupRelayTempDir(t)

	if err := SendMessage("sati", MsgTypeTask, "Test subject", "Test body"); err != nil {
		t.Fatalf("SendMessage: %v", err)
	}

	path := filepath.Join(dir, ".forge", "messages", "outbox", "sati.jsonl")
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read outbox file: %v", err)
	}

	var msg Message
	if err := json.Unmarshal([]byte(strings.TrimSpace(string(data))), &msg); err != nil {
		t.Fatalf("unmarshal message: %v", err)
	}

	if msg.To != "sati" {
		t.Errorf("expected To=sati, got %q", msg.To)
	}
	if msg.Type != MsgTypeTask {
		t.Errorf("expected Type=task, got %q", msg.Type)
	}
	if msg.Subject != "Test subject" {
		t.Errorf("expected Subject=%q, got %q", "Test subject", msg.Subject)
	}
	if msg.Body != "Test body" {
		t.Errorf("expected Body=%q, got %q", "Test body", msg.Body)
	}
	if msg.Ack {
		t.Error("expected Ack=false on a freshly sent message")
	}
	if msg.ID == "" {
		t.Error("expected non-empty ID")
	}
}

func TestSendMessageValidJSONL(t *testing.T) {
	setupRelayTempDir(t)

	for i := 0; i < 3; i++ {
		if err := SendMessage("nova", MsgTypeDirective, fmt.Sprintf("sub%d", i), "body"); err != nil {
			t.Fatalf("SendMessage %d: %v", i, err)
		}
	}

	msgs, err := readJSONLMessages(outboxPath("nova"))
	if err != nil {
		t.Fatalf("readJSONLMessages: %v", err)
	}
	if len(msgs) != 3 {
		t.Errorf("expected 3 messages, got %d", len(msgs))
	}
}

// ── ReadInbox ─────────────────────────────────────────────────────────────────

func TestReadInboxEmpty(t *testing.T) {
	setupRelayTempDir(t)

	msgs, err := ReadInbox()
	if err != nil {
		t.Fatalf("ReadInbox on empty dir: %v", err)
	}
	if len(msgs) != 0 {
		t.Errorf("expected 0 messages, got %d", len(msgs))
	}
}

func TestReadInboxReturnsUnacked(t *testing.T) {
	dir := setupRelayTempDir(t)

	inDir := filepath.Join(dir, ".forge", "messages", "inbox")
	if err := os.MkdirAll(inDir, 0755); err != nil {
		t.Fatal(err)
	}

	msgs := []Message{
		{ID: "id-1", From: "prya", To: "sati", Type: MsgTypeTask, Subject: "s1", Ack: false, Timestamp: time.Now()},
		{ID: "id-2", From: "prya", To: "sati", Type: MsgTypeStatus, Subject: "s2", Ack: true, Timestamp: time.Now()},
		{ID: "id-3", From: "nova", To: "sati", Type: MsgTypeResult, Subject: "s3", Ack: false, Timestamp: time.Now()},
	}

	path := filepath.Join(inDir, "prya.jsonl")
	f, _ := os.Create(path)
	for _, m := range msgs {
		line, _ := json.Marshal(m)
		fmt.Fprintf(f, "%s\n", line)
	}
	f.Close()

	got, err := ReadInbox()
	if err != nil {
		t.Fatalf("ReadInbox: %v", err)
	}

	// Only unacked (id-1 and id-3) should be returned.
	if len(got) != 2 {
		t.Errorf("expected 2 unacked messages, got %d", len(got))
	}
	for _, m := range got {
		if m.Ack {
			t.Errorf("ReadInbox returned acked message: %s", m.ID)
		}
	}
}

// ── AckMessage ────────────────────────────────────────────────────────────────

func TestAckMessageMarksAcked(t *testing.T) {
	dir := setupRelayTempDir(t)

	inDir := filepath.Join(dir, ".forge", "messages", "inbox")
	if err := os.MkdirAll(inDir, 0755); err != nil {
		t.Fatal(err)
	}

	msg := Message{ID: "ack-test-1", From: "prya", To: "sati", Type: MsgTypeTask, Subject: "hello", Ack: false, Timestamp: time.Now()}
	path := filepath.Join(inDir, "prya.jsonl")
	line, _ := json.Marshal(msg)
	if err := os.WriteFile(path, []byte(string(line)+"\n"), 0644); err != nil {
		t.Fatal(err)
	}

	if err := AckMessage("ack-test-1"); err != nil {
		t.Fatalf("AckMessage: %v", err)
	}

	updated, err := readJSONLMessages(path)
	if err != nil {
		t.Fatal(err)
	}
	if len(updated) != 1 {
		t.Fatalf("expected 1 message after ack, got %d", len(updated))
	}
	if !updated[0].Ack {
		t.Error("expected Ack=true after AckMessage")
	}
}

func TestAckMessageNotFound(t *testing.T) {
	dir := setupRelayTempDir(t)
	inDir := filepath.Join(dir, ".forge", "messages", "inbox")
	if err := os.MkdirAll(inDir, 0755); err != nil {
		t.Fatal(err)
	}

	err := AckMessage("nonexistent-id")
	if err == nil {
		t.Error("expected error for nonexistent message ID")
	}
}

// ── Duplicate detection ───────────────────────────────────────────────────────

func TestDuplicateMessageDetection(t *testing.T) {
	setupRelayTempDir(t)
	db := setupRelayTestDB(t)
	defer db.Close()

	ctx := context.Background()

	// Insert a seen message event manually.
	seenID := "dup-test-999"
	_, err := db.ExecContext(ctx, `
		INSERT INTO task_events (task_id, domain, project, event_type, payload, created_at)
		VALUES (?, 'infrastructure', 'cross-node', 'relay_message_task', '{}', CURRENT_TIMESTAMP)
	`, seenID)
	if err != nil {
		t.Fatalf("insert seed event: %v", err)
	}

	seen, err := loadSeenMessageIDs(ctx, db)
	if err != nil {
		t.Fatalf("loadSeenMessageIDs: %v", err)
	}
	if _, ok := seen[seenID]; !ok {
		t.Errorf("expected %q in seen IDs", seenID)
	}

	// A different ID must NOT be in seen.
	if _, ok := seen["other-id"]; ok {
		t.Errorf("unexpected ID 'other-id' in seen IDs")
	}
}

// ── Relay patrol creates task from task-type message ──────────────────────────

func TestRelayPatrolCreatesTask(t *testing.T) {
	dir := setupRelayTempDir(t)
	db := setupRelayTestDB(t)
	defer db.Close()

	// Place a task-type message in the inbox.
	inDir := filepath.Join(dir, ".forge", "messages", "inbox")
	if err := os.MkdirAll(inDir, 0755); err != nil {
		t.Fatal(err)
	}

	msg := Message{
		ID:        "relay-task-001",
		From:      "sati",
		To:        "prya",
		Timestamp: time.Now(),
		Type:      MsgTypeTask,
		Subject:   "Run coverage wave",
		Body:      `{"domain":"infra","project":"forged","type":"research","priority":7}`,
		Ack:       false,
	}
	path := filepath.Join(inDir, "sati.jsonl")
	line, _ := json.Marshal(msg)
	if err := os.WriteFile(path, []byte(string(line)+"\n"), 0644); err != nil {
		t.Fatal(err)
	}

	ctx := context.Background()
	if err := messageRelayPatrol(ctx, db); err != nil {
		t.Fatalf("messageRelayPatrol: %v", err)
	}

	// Verify a task was created.
	var count int
	row := db.QueryRow("SELECT COUNT(*) FROM tasks")
	if err := row.Scan(&count); err != nil {
		t.Fatalf("count tasks: %v", err)
	}
	if count == 0 {
		t.Error("expected at least one task to be created from relay message")
	}
}

// ── Relay patrol handles result message ──────────────────────────────────────

func TestRelayPatrolHandlesResult(t *testing.T) {
	dir := setupRelayTempDir(t)
	db := setupRelayTestDB(t)
	defer db.Close()

	inDir := filepath.Join(dir, ".forge", "messages", "inbox")
	if err := os.MkdirAll(inDir, 0755); err != nil {
		t.Fatal(err)
	}

	msg := Message{
		ID:        "result-msg-001",
		From:      "nova",
		To:        "prya",
		Timestamp: time.Now(),
		Type:      MsgTypeResult,
		Subject:   "Coverage wave 267 complete",
		Body:      "All tests pass. Coverage 84.2%.",
		Ack:       false,
	}
	path := filepath.Join(inDir, "nova.jsonl")
	line, _ := json.Marshal(msg)
	if err := os.WriteFile(path, []byte(string(line)+"\n"), 0644); err != nil {
		t.Fatal(err)
	}

	ctx := context.Background()
	if err := messageRelayPatrol(ctx, db); err != nil {
		t.Fatalf("messageRelayPatrol: %v", err)
	}

	// Verify the result file was written.
	resultsDir := filepath.Join(dir, ".forge", "heartbeat", "results")
	entries, err := os.ReadDir(resultsDir)
	if err != nil {
		t.Fatalf("read results dir: %v", err)
	}
	if len(entries) == 0 {
		t.Error("expected result file to be written for result-type message")
	}
}

// ── SendMessage rotation ──────────────────────────────────────────────────────

// TestSendMessageRotatesOutbox verifies that when the outbox file contains
// maxMessagesPerFile entries, SendMessage renames it before appending a new one.
func TestSendMessageRotatesOutbox(t *testing.T) {
	dir := setupRelayTempDir(t)

	outDir := filepath.Join(dir, ".forge", "messages", "outbox")
	if err := os.MkdirAll(outDir, 0755); err != nil {
		t.Fatal(err)
	}

	// Pre-populate the file with exactly maxMessagesPerFile messages so the
	// next SendMessage triggers rotation.
	path := outboxPath("rotate-target")
	f, err := os.Create(path)
	if err != nil {
		t.Fatal(err)
	}
	stub := Message{ID: "stub", From: "prya", To: "rotate-target", Type: MsgTypeStatus, Subject: "s", Timestamp: time.Now()}
	line, _ := json.Marshal(stub)
	for i := 0; i < maxMessagesPerFile; i++ {
		fmt.Fprintf(f, "%s\n", line)
	}
	f.Close()

	// SendMessage should rotate the old file and create a fresh one.
	if err := SendMessage("rotate-target", MsgTypeTask, "after-rotate", "body"); err != nil {
		t.Fatalf("SendMessage after filling outbox: %v", err)
	}

	// The original path must now contain exactly 1 line (the new message).
	msgs, err := readJSONLMessages(path)
	if err != nil {
		t.Fatalf("readJSONLMessages: %v", err)
	}
	if len(msgs) != 1 {
		t.Errorf("expected 1 message in rotated outbox, got %d", len(msgs))
	}

	// At least one rotated file must exist alongside the fresh outbox.
	entries, err := os.ReadDir(outDir)
	if err != nil {
		t.Fatalf("ReadDir: %v", err)
	}
	rotatedCount := 0
	for _, e := range entries {
		if strings.HasPrefix(e.Name(), "rotate-target.jsonl.") {
			rotatedCount++
		}
	}
	if rotatedCount == 0 {
		t.Error("expected at least one rotated outbox file")
	}
}

// TestNodeID_FromEnv verifies that nodeID() returns NODE_ID when set.
func TestNodeID_FromEnv(t *testing.T) {
	t.Setenv("NODE_ID", "test-node-env")
	id := nodeID()
	if id != "test-node-env" {
		t.Errorf("expected 'test-node-env', got %q", id)
	}
}

// TestNodeID_FallbackToHostname verifies that nodeID() returns os.Hostname()
// when NODE_ID is not set.
func TestNodeID_FallbackToHostname(t *testing.T) {
	t.Setenv("NODE_ID", "")

	expected, err := os.Hostname()
	if err != nil {
		t.Skip("os.Hostname() failed, skipping")
	}

	id := nodeID()
	if id != expected {
		t.Errorf("expected hostname %q, got %q", expected, id)
	}
}

// TestInboxPath verifies inboxPath returns the correct path under messagesDir.
func TestInboxPath(t *testing.T) {
	dir := setupRelayTempDir(t)
	got := inboxPath("prya")
	expected := filepath.Join(dir, ".forge", "messages", "inbox", "prya.jsonl")
	if got != expected {
		t.Errorf("expected %q, got %q", expected, got)
	}
}

// ── Relay patrol with nil DB ──────────────────────────────────────────────────

func TestRelayPatrolNilDB(t *testing.T) {
	dir := setupRelayTempDir(t)

	inDir := filepath.Join(dir, ".forge", "messages", "inbox")
	if err := os.MkdirAll(inDir, 0755); err != nil {
		t.Fatal(err)
	}

	// With nil DB, only result messages should succeed (they don't need DB).
	msg := Message{
		ID:        "nil-db-test",
		From:      "nova",
		To:        "prya",
		Timestamp: time.Now(),
		Type:      MsgTypeResult,
		Subject:   "Result without DB",
		Body:      "body",
		Ack:       false,
	}
	path := filepath.Join(inDir, "nova.jsonl")
	line, _ := json.Marshal(msg)
	if err := os.WriteFile(path, []byte(string(line)+"\n"), 0644); err != nil {
		t.Fatal(err)
	}

	ctx := context.Background()
	// Should not panic with nil DB.
	_ = messageRelayPatrol(ctx, nil)
}

// ── nodeID ─────────────────────────────────────────────────────────────────

func TestNodeIDFromEnv(t *testing.T) {
	t.Setenv("NODE_ID", "test-node-abc")
	id := nodeID()
	if id != "test-node-abc" {
		t.Errorf("expected 'test-node-abc', got %q", id)
	}
}

func TestNodeIDFallsBackToHostname(t *testing.T) {
	t.Setenv("NODE_ID", "")
	id := nodeID()
	if id == "" {
		t.Error("expected non-empty node ID from hostname fallback")
	}
}

// ── ReadInbox skips non-jsonl ───────────────────────────────────────────────

func TestReadInboxSkipsNonJSONL(t *testing.T) {
	dir := setupRelayTempDir(t)

	inDir := filepath.Join(dir, ".forge", "messages", "inbox")
	if err := os.MkdirAll(inDir, 0755); err != nil {
		t.Fatal(err)
	}

	// Write a non-jsonl file and a directory — both must be skipped.
	if err := os.WriteFile(filepath.Join(inDir, "README.txt"), []byte("ignore me"), 0644); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(filepath.Join(inDir, "subdir"), 0755); err != nil {
		t.Fatal(err)
	}

	// Write one valid jsonl file with a single unacked message.
	msg := Message{ID: "skip-test-1", From: "prya", To: "sati", Type: MsgTypeTask, Subject: "s", Ack: false, Timestamp: time.Now()}
	line, _ := json.Marshal(msg)
	if err := os.WriteFile(filepath.Join(inDir, "prya.jsonl"), []byte(string(line)+"\n"), 0644); err != nil {
		t.Fatal(err)
	}

	got, err := ReadInbox()
	if err != nil {
		t.Fatalf("ReadInbox: %v", err)
	}
	if len(got) != 1 {
		t.Errorf("expected 1 message (non-jsonl files skipped), got %d", len(got))
	}
}

// ── rewriteJSONL ─────────────────────────────────────────────────────────────

func TestRewriteJSONLSuccess(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "messages.jsonl")

	msgs := []Message{
		{ID: "rw-1", From: "prya", To: "sati", Type: MsgTypeTask, Subject: "first", Ack: false, Timestamp: time.Now()},
		{ID: "rw-2", From: "prya", To: "sati", Type: MsgTypeStatus, Subject: "second", Ack: true, Timestamp: time.Now()},
	}

	if err := rewriteJSONL(path, msgs); err != nil {
		t.Fatalf("rewriteJSONL: %v", err)
	}

	got, err := readJSONLMessages(path)
	if err != nil {
		t.Fatalf("readJSONLMessages after rewrite: %v", err)
	}
	if len(got) != 2 {
		t.Fatalf("expected 2 messages, got %d", len(got))
	}
	if got[0].ID != "rw-1" || got[1].ID != "rw-2" {
		t.Errorf("unexpected message IDs: %v, %v", got[0].ID, got[1].ID)
	}
	if !got[1].Ack {
		t.Error("expected second message to have Ack=true")
	}
}

func TestRewriteJSONLEmpty(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "empty.jsonl")

	// Rewriting an empty slice should produce a valid (empty) file.
	if err := rewriteJSONL(path, []Message{}); err != nil {
		t.Fatalf("rewriteJSONL with empty slice: %v", err)
	}

	got, err := readJSONLMessages(path)
	if err != nil {
		t.Fatalf("readJSONLMessages: %v", err)
	}
	if len(got) != 0 {
		t.Errorf("expected 0 messages, got %d", len(got))
	}
}

// ── SendMessage rotation ─────────────────────────────────────────────────────

func TestSendMessageRotatesAtLimit(t *testing.T) {
	dir := setupRelayTempDir(t)

	outDir := filepath.Join(dir, ".forge", "messages", "outbox")
	if err := os.MkdirAll(outDir, 0755); err != nil {
		t.Fatal(err)
	}

	// Manually write maxMessagesPerFile messages to the target file.
	path := filepath.Join(outDir, "nova.jsonl")
	var buf []byte
	for i := 0; i < maxMessagesPerFile; i++ {
		m := Message{ID: fmt.Sprintf("seed-%d", i), From: "prya", To: "nova", Type: MsgTypeTask, Subject: "s", Timestamp: time.Now()}
		line, _ := json.Marshal(m)
		buf = append(buf, line...)
		buf = append(buf, '\n')
	}
	if err := os.WriteFile(path, buf, 0644); err != nil {
		t.Fatal(err)
	}

	// Sending one more message should rotate the file.
	if err := SendMessage("nova", MsgTypeDirective, "rotate-trigger", "body"); err != nil {
		t.Fatalf("SendMessage: %v", err)
	}

	// The original file should now contain only the new message.
	msgs, err := readJSONLMessages(path)
	if err != nil {
		t.Fatalf("readJSONLMessages after rotation: %v", err)
	}
	if len(msgs) != 1 {
		t.Errorf("expected 1 message in rotated file, got %d", len(msgs))
	}

	// A rotated backup file must exist.
	entries, _ := os.ReadDir(outDir)
	rotatedCount := 0
	for _, e := range entries {
		if strings.HasPrefix(e.Name(), "nova.jsonl.") {
			rotatedCount++
		}
	}
	if rotatedCount != 1 {
		t.Errorf("expected 1 rotated file, got %d", rotatedCount)
	}
}

// ── relayHandleTask ──────────────────────────────────────────────────────────

func TestRelayHandleTask_NilDB(t *testing.T) {
	msg := Message{ID: "rht-nil-db", From: "sati", Type: MsgTypeTask, Subject: "test", Ack: false, Timestamp: time.Now()}
	err := relayHandleTask(context.Background(), nil, msg)
	if err == nil {
		t.Error("expected error when db is nil")
	}
}

func TestRelayHandleTask_DefaultMeta(t *testing.T) {
	setupRelayTempDir(t)
	db := setupRelayTestDB(t)
	defer db.Close()

	// Body is empty — all defaults should be applied.
	msg := Message{
		ID:        "rht-defaults",
		From:      "sati",
		To:        "prya",
		Type:      MsgTypeTask,
		Subject:   "fallback defaults test",
		Body:      "",
		Timestamp: time.Now(),
	}

	err := relayHandleTask(context.Background(), db, msg)
	if err != nil {
		t.Fatalf("relayHandleTask: %v", err)
	}

	// Verify the task was inserted with default values.
	var domain, project, taskType string
	var priority int
	row := db.QueryRow(`SELECT domain, project, type, priority FROM tasks ORDER BY created_at DESC LIMIT 1`)
	if err := row.Scan(&domain, &project, &taskType, &priority); err != nil {
		t.Fatalf("scan: %v", err)
	}
	if domain != "infrastructure" {
		t.Errorf("expected domain 'infrastructure', got %q", domain)
	}
	if project != "cross-node" {
		t.Errorf("expected project 'cross-node', got %q", project)
	}
	if taskType != "research" {
		t.Errorf("expected type 'research', got %q", taskType)
	}
	if priority != 5 {
		t.Errorf("expected priority 5, got %d", priority)
	}
}

func TestRelayHandleTask_ZeroJSONMeta(t *testing.T) {
	setupRelayTempDir(t)
	db := setupRelayTestDB(t)
	defer db.Close()

	// JSON body with zero-value fields — defaults should fill them in.
	msg := Message{
		ID:        "rht-zero-meta",
		From:      "sati",
		To:        "prya",
		Type:      MsgTypeTask,
		Subject:   "zero meta test",
		Body:      `{"domain":"","project":"","type":"","priority":0}`,
		Timestamp: time.Now(),
	}

	if err := relayHandleTask(context.Background(), db, msg); err != nil {
		t.Fatalf("relayHandleTask: %v", err)
	}

	var domain, project, taskType string
	var priority int
	row := db.QueryRow(`SELECT domain, project, type, priority FROM tasks ORDER BY created_at DESC LIMIT 1`)
	if err := row.Scan(&domain, &project, &taskType, &priority); err != nil {
		t.Fatalf("scan: %v", err)
	}
	if domain != "infrastructure" {
		t.Errorf("expected default domain, got %q", domain)
	}
	if priority != 5 {
		t.Errorf("expected default priority 5, got %d", priority)
	}
}

// ── relayHandleStatus ────────────────────────────────────────────────────────

func TestRelayHandleStatus_NilDB(t *testing.T) {
	setupRelayTempDir(t)
	msg := Message{ID: "rhs-nil", From: "sati", Type: MsgTypeStatus, Subject: "status", Timestamp: time.Now()}
	// With nil DB, relayHandleStatus should return nil (no-op).
	if err := relayHandleStatus(context.Background(), nil, msg); err != nil {
		t.Errorf("expected nil error for nil DB, got %v", err)
	}
}

func TestRelayHandleStatus_UpdatesNode(t *testing.T) {
	setupRelayTempDir(t)
	db := setupRelayTestDB(t)
	defer db.Close()

	// Pre-insert the node so the UPDATE has a row to target.
	_, err := db.Exec(`INSERT INTO nodes (id, hostname, address, status) VALUES ('sati', 'sati', '10.0.0.2', 'offline')`)
	if err != nil {
		t.Fatalf("insert node: %v", err)
	}

	msg := Message{ID: "rhs-ok", From: "sati", Type: MsgTypeStatus, Subject: "heartbeat", Timestamp: time.Now()}
	if err := relayHandleStatus(context.Background(), db, msg); err != nil {
		t.Fatalf("relayHandleStatus: %v", err)
	}

	var status string
	if err := db.QueryRow(`SELECT status FROM nodes WHERE id = 'sati'`).Scan(&status); err != nil {
		t.Fatalf("scan: %v", err)
	}
	if status != "online" {
		t.Errorf("expected status 'online', got %q", status)
	}
}
