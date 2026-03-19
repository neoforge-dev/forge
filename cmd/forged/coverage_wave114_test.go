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
// xnode.go — processInbox (line 762)
// ---------------------------------------------------------------------------

// TestWave114_ProcessInbox_NonexistentDir verifies that processInbox is silent
// when XNodeInboxDir does not exist (os.IsNotExist branch).
func TestWave114_ProcessInbox_NonexistentDir(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	xnc, _ := NewXNodeController(db, "test-node")

	origInbox := XNodeInboxDir
	XNodeInboxDir = "/tmp/no-such-xnode-inbox-wave114"
	defer func() { XNodeInboxDir = origInbox }()

	offsets := make(map[string]int64)
	// Must not panic; logs silently for os.IsNotExist.
	xnc.processInbox(offsets)
}

// TestWave114_ProcessInbox_EmptyDir verifies that processInbox is a no-op when
// the inbox directory exists but contains no .jsonl files.
func TestWave114_ProcessInbox_EmptyDir(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	inboxDir := t.TempDir()
	xnc, _ := NewXNodeController(db, "test-node")

	origInbox := XNodeInboxDir
	XNodeInboxDir = inboxDir
	defer func() { XNodeInboxDir = origInbox }()

	offsets := make(map[string]int64)
	xnc.processInbox(offsets) // Must not panic, no files touched.

	entries, _ := os.ReadDir(inboxDir)
	if len(entries) != 0 {
		t.Errorf("expected inbox dir to remain empty, got %d entries", len(entries))
	}
}

// TestWave114_ProcessInbox_NonJsonlFileIgnored verifies that non-.jsonl files in
// the inbox directory are skipped without error.
func TestWave114_ProcessInbox_NonJsonlFileIgnored(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	inboxDir := t.TempDir()
	xnc, _ := NewXNodeController(db, "test-node")

	origInbox := XNodeInboxDir
	XNodeInboxDir = inboxDir
	defer func() { XNodeInboxDir = origInbox }()

	// Write a non-.jsonl file — should be ignored.
	if err := os.WriteFile(filepath.Join(inboxDir, "README.txt"), []byte("ignore me"), 0644); err != nil {
		t.Fatalf("write non-jsonl file: %v", err)
	}

	offsets := make(map[string]int64)
	xnc.processInbox(offsets) // Must not panic.
}

// ---------------------------------------------------------------------------
// xnode.go — routeMessage unknown type (line 805)
// ---------------------------------------------------------------------------

// TestWave114_RouteMessage_UnknownType verifies that an unknown message type
// falls to the default branch (log + ack) and returns nil.
func TestWave114_RouteMessage_UnknownType(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	acksDir := t.TempDir()
	xnc, _ := NewXNodeController(db, "test-node")

	origAcks := XNodeAcksDir
	XNodeAcksDir = acksDir
	defer func() { XNodeAcksDir = origAcks }()

	msgID := fmt.Sprintf("unknown-type-%d", time.Now().UnixNano())
	msg := map[string]interface{}{
		"type":       "definitely_unknown_type",
		"message_id": msgID,
		"source":     "remote-node",
	}

	err := xnc.routeMessage(context.Background(), msg)
	if err != nil {
		t.Errorf("routeMessage with unknown type returned error: %v", err)
	}

	// Ack file must be written.
	ackPath := filepath.Join(acksDir, msgID+".json")
	data, err := os.ReadFile(ackPath)
	if err != nil {
		t.Fatalf("expected ack file %s to exist: %v", ackPath, err)
	}
	var ack map[string]interface{}
	if err := json.Unmarshal(data, &ack); err != nil {
		t.Fatalf("parse ack JSON: %v", err)
	}
	if ack["status"] != "acked" {
		t.Errorf("ack status: got %v, want \"acked\"", ack["status"])
	}
	if ack["message_id"] != msgID {
		t.Errorf("ack message_id: got %v, want %q", ack["message_id"], msgID)
	}
}

// TestWave114_RouteMessage_MissingMessageID verifies that routeMessage generates
// a ULID message_id when one is absent, and still writes an ack.
func TestWave114_RouteMessage_MissingMessageID(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	acksDir := t.TempDir()
	xnc, _ := NewXNodeController(db, "test-node")

	origAcks := XNodeAcksDir
	XNodeAcksDir = acksDir
	defer func() { XNodeAcksDir = origAcks }()

	msg := map[string]interface{}{
		"type": "some_unknown_event",
		// no "message_id" field
	}

	err := xnc.routeMessage(context.Background(), msg)
	if err != nil {
		t.Errorf("routeMessage without message_id returned error: %v", err)
	}

	// At least one ack file should exist.
	entries, _ := os.ReadDir(acksDir)
	if len(entries) == 0 {
		t.Error("expected at least one ack file written when message_id absent")
	}
}

// ---------------------------------------------------------------------------
// xnode.go — serializePendingMessages empty queue (line 938)
// ---------------------------------------------------------------------------

// TestWave114_SerializePendingMessages_EmptyQueue verifies that
// serializePendingMessages is a no-op (no files written) when xnode_outbox has
// no pending rows.
func TestWave114_SerializePendingMessages_EmptyQueue(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	outboxDir := t.TempDir()
	xnc := &XNodeController{
		db:        db,
		nodeID:    "test-node",
		outboxDir: outboxDir,
	}

	xnc.serializePendingMessages(context.Background(), db)

	entries, err := os.ReadDir(outboxDir)
	if err != nil {
		t.Fatalf("ReadDir: %v", err)
	}
	if len(entries) != 0 {
		t.Errorf("expected no files written for empty queue, got %d", len(entries))
	}
}

// ---------------------------------------------------------------------------
// xnode.go — RegisterNode happy path
// ---------------------------------------------------------------------------

// TestWave114_RegisterNode_HappyPath verifies that RegisterNode inserts a new
// node and can be queried back by ID.
func TestWave114_RegisterNode_HappyPath(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	xnc, err := NewXNodeController(db, "test-node")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}

	node := Node{
		ID:            fmt.Sprintf("node-reg-%d", time.Now().UnixNano()),
		Hostname:      "test-host",
		Address:       "100.1.2.3:8081",
		Status:        "online",
		LastHeartbeat: time.Now().UTC(),
	}

	if err := xnc.RegisterNode(context.Background(), node); err != nil {
		t.Fatalf("RegisterNode: %v", err)
	}

	var gotID, gotStatus string
	row := db.QueryRow("SELECT id, status FROM nodes WHERE id = ?", node.ID)
	if err := row.Scan(&gotID, &gotStatus); err != nil {
		t.Fatalf("query inserted node: %v", err)
	}
	if gotID != node.ID {
		t.Errorf("id: got %q, want %q", gotID, node.ID)
	}
	if gotStatus != "online" {
		t.Errorf("status: got %q, want \"online\"", gotStatus)
	}
}

// TestWave114_RegisterNode_Upsert verifies that calling RegisterNode a second
// time with the same ID updates the existing record (ON CONFLICT DO UPDATE).
func TestWave114_RegisterNode_Upsert(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	xnc, _ := NewXNodeController(db, "test-node")

	nodeID := fmt.Sprintf("node-upsert-%d", time.Now().UnixNano())

	first := Node{
		ID:            nodeID,
		Hostname:      "host-v1",
		Address:       "10.0.0.1:8081",
		Status:        "online",
		LastHeartbeat: time.Now().UTC(),
	}
	if err := xnc.RegisterNode(context.Background(), first); err != nil {
		t.Fatalf("first RegisterNode: %v", err)
	}

	second := Node{
		ID:            nodeID,
		Hostname:      "host-v2",
		Address:       "10.0.0.2:8081",
		Status:        "online",
		LastHeartbeat: time.Now().UTC(),
	}
	if err := xnc.RegisterNode(context.Background(), second); err != nil {
		t.Fatalf("second RegisterNode (upsert): %v", err)
	}

	var hostname string
	if err := db.QueryRow("SELECT hostname FROM nodes WHERE id = ?", nodeID).Scan(&hostname); err != nil {
		t.Fatalf("query hostname: %v", err)
	}
	if hostname != "host-v2" {
		t.Errorf("hostname after upsert: got %q, want \"host-v2\"", hostname)
	}
}

// ---------------------------------------------------------------------------
// xnode.go — Heartbeat happy paths
// ---------------------------------------------------------------------------

// TestWave114_Heartbeat_ExistingNode verifies that Heartbeat updates
// last_heartbeat and sets status='online' for an already-registered node.
func TestWave114_Heartbeat_ExistingNode(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	xnc, _ := NewXNodeController(db, "test-node")

	nodeID := fmt.Sprintf("node-hb-existing-%d", time.Now().UnixNano())

	// Pre-insert with offline status and old heartbeat.
	oldTime := time.Now().UTC().Add(-10 * time.Minute).Format(time.RFC3339)
	_, err := db.Exec(
		"INSERT INTO nodes (id, hostname, address, status, last_heartbeat) VALUES (?, ?, ?, 'offline', ?)",
		nodeID, nodeID, nodeID+":8081", oldTime,
	)
	if err != nil {
		t.Fatalf("pre-insert node: %v", err)
	}

	if err := xnc.Heartbeat(context.Background(), nodeID); err != nil {
		t.Fatalf("Heartbeat: %v", err)
	}

	var status string
	if err := db.QueryRow("SELECT status FROM nodes WHERE id = ?", nodeID).Scan(&status); err != nil {
		t.Fatalf("query status: %v", err)
	}
	if status != "online" {
		t.Errorf("status after Heartbeat: got %q, want \"online\"", status)
	}
}

// TestWave114_Heartbeat_NewNode verifies that Heartbeat inserts a new node
// record (rows==0 branch) when the node is not registered yet.
func TestWave114_Heartbeat_NewNode(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	xnc, _ := NewXNodeController(db, "test-node")

	nodeID := fmt.Sprintf("node-hb-new-%d", time.Now().UnixNano())

	if err := xnc.Heartbeat(context.Background(), nodeID); err != nil {
		t.Fatalf("Heartbeat for new node: %v", err)
	}

	var count int
	if err := db.QueryRow("SELECT COUNT(*) FROM nodes WHERE id = ?", nodeID).Scan(&count); err != nil {
		t.Fatalf("query count: %v", err)
	}
	if count != 1 {
		t.Errorf("expected 1 row inserted for new node, got %d", count)
	}

	var status string
	if err := db.QueryRow("SELECT status FROM nodes WHERE id = ?", nodeID).Scan(&status); err != nil {
		t.Fatalf("query status: %v", err)
	}
	if status != "online" {
		t.Errorf("status for new node: got %q, want \"online\"", status)
	}
}
