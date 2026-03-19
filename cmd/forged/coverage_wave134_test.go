//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// ---------------------------------------------------------------------------
// NewXNodeController tests
// ---------------------------------------------------------------------------

func TestWave134_NewXNodeController_NilDB(t *testing.T) {
	_, err := NewXNodeController(nil, "test-node")
	if err == nil {
		t.Error("expected error with nil DB")
	}
	t.Logf("NewXNodeController(nil): %v", err)
}

func TestWave134_NewXNodeController_EmptyNodeID(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctrl, err := NewXNodeController(db, "")
	if err != nil {
		t.Fatalf("NewXNodeController with empty nodeID: %v", err)
	}
	if ctrl == nil {
		t.Fatal("expected controller, got nil")
	}
	// Should use hostname
	t.Logf("NewXNodeController(''): nodeID=%s", ctrl.nodeID)
}

func TestWave134_NewXNodeController_Success(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctrl, err := NewXNodeController(db, "test-node-134")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}
	if ctrl.nodeID != "test-node-134" {
		t.Errorf("expected nodeID 'test-node-134', got %q", ctrl.nodeID)
	}
}

// ---------------------------------------------------------------------------
// RegisterNode tests (xnode.go:244)
// ---------------------------------------------------------------------------

func TestWave134_RegisterNode_Success(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctrl, err := NewXNodeController(db, "test-node")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}

	node := Node{
		ID:            "node-test-1",
		Hostname:      "test-host",
		Address:       "127.0.0.1:8081",
		Status:        "online",
		LastHeartbeat: time.Now(),
	}

	err = ctrl.RegisterNode(context.Background(), node)
	if err != nil {
		t.Errorf("RegisterNode: %v", err)
	}
}

func TestWave134_RegisterNode_UpdateExisting(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctrl, err := NewXNodeController(db, "test-node")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}

	// Register first time
	node := Node{
		ID:            "node-test-update",
		Hostname:      "test-host",
		Address:       "127.0.0.1:8081",
		Status:        "online",
		LastHeartbeat: time.Now(),
	}
	if err := ctrl.RegisterNode(context.Background(), node); err != nil {
		t.Fatalf("first RegisterNode: %v", err)
	}

	// Update same node
	node.Hostname = "updated-host"
	node.Address = "127.0.0.2:8081"
	err = ctrl.RegisterNode(context.Background(), node)
	if err != nil {
		t.Errorf("update RegisterNode: %v", err)
	}
}

func TestWave134_RegisterNode_DBError(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	cleanup() // close DB

	ctrl := &XNodeController{db: db}

	node := Node{
		ID:            "node-test",
		Hostname:      "test-host",
		Address:       "127.0.0.1:8081",
		Status:        "online",
		LastHeartbeat: time.Now(),
	}

	err := ctrl.RegisterNode(context.Background(), node)
	if err == nil {
		t.Error("expected error with closed DB")
	}
	t.Logf("RegisterNode with closed DB: %v", err)
}

// ---------------------------------------------------------------------------
// Heartbeat tests (xnode.go:261)
// ---------------------------------------------------------------------------

func TestWave134_Heartbeat_Success(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctrl, err := NewXNodeController(db, "test-node")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}

	// Register a node first
	node := Node{
		ID:            "heartbeat-node",
		Hostname:      "heartbeat-host",
		Address:       "127.0.0.1:8081",
		Status:        "online",
		LastHeartbeat: time.Now(),
	}
	if err := ctrl.RegisterNode(context.Background(), node); err != nil {
		t.Fatalf("RegisterNode: %v", err)
	}

	// Send heartbeat
	err = ctrl.Heartbeat(context.Background(), "heartbeat-node")
	if err != nil {
		t.Errorf("Heartbeat: %v", err)
	}
}

func TestWave134_Heartbeat_NewNode(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctrl, err := NewXNodeController(db, "test-node")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}

	// Heartbeat for non-existent node should insert it
	err = ctrl.Heartbeat(context.Background(), "new-heartbeat-node")
	if err != nil {
		t.Errorf("Heartbeat for new node: %v", err)
	}
}

func TestWave134_Heartbeat_DBError(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	cleanup() // close DB

	ctrl := &XNodeController{db: db}

	err := ctrl.Heartbeat(context.Background(), "test-node")
	if err == nil {
		t.Error("expected error with closed DB")
	}
	t.Logf("Heartbeat with closed DB: %v", err)
}

// ---------------------------------------------------------------------------
// ListAcksHandler tests (xnode.go:401)
// ---------------------------------------------------------------------------

func TestWave134_ListAcksHandler_Success(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctrl, err := NewXNodeController(db, "test-node")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}

	// Use temp directory for clean test
	tmpDir := t.TempDir()
	origAcksDir := XNodeAcksDir
	XNodeAcksDir = tmpDir
	defer func() { XNodeAcksDir = origAcksDir }()

	req := httptest.NewRequest(http.MethodGet, "/api/xnode/acks", nil)
	w := httptest.NewRecorder()
	ctrl.ListAcksHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected status 200, got %d", w.Code)
	}

	var resp map[string]interface{}
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Errorf("failed to decode response: %v", err)
	}
	t.Logf("ListAcksHandler: count=%v", resp["count"])
}

func TestWave134_ListAcksHandler_WithAcks(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctrl, err := NewXNodeController(db, "test-node")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}

	// Use temp directory for clean test
	tmpDir := t.TempDir()
	origAcksDir := XNodeAcksDir
	XNodeAcksDir = tmpDir
	defer func() { XNodeAcksDir = origAcksDir }()

	ack := map[string]interface{}{
		"message_id": "msg-1",
		"status":     "acked",
		"timestamp":  time.Now().Format(time.RFC3339),
	}
	ackData, _ := json.Marshal(ack)
	if err := os.WriteFile(filepath.Join(tmpDir, "ack-1.json"), ackData, 0644); err != nil {
		t.Fatalf("write ack: %v", err)
	}

	req := httptest.NewRequest(http.MethodGet, "/api/xnode/acks", nil)
	w := httptest.NewRecorder()
	ctrl.ListAcksHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected status 200, got %d: %s", w.Code, w.Body.String())
	}

	var resp map[string]interface{}
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Errorf("failed to decode response: %v", err)
	}

	acks := resp["acks"].([]interface{})
	if len(acks) != 1 {
		t.Errorf("expected 1 ack, got %d", len(acks))
	}
}

func TestWave134_ListAcksHandler_InvalidJSON(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctrl, err := NewXNodeController(db, "test-node")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}

	// Use temp directory for clean test
	tmpDir := t.TempDir()
	origAcksDir := XNodeAcksDir
	XNodeAcksDir = tmpDir
	defer func() { XNodeAcksDir = origAcksDir }()

	if err := os.WriteFile(filepath.Join(tmpDir, "bad-ack.json"), []byte("{invalid"), 0644); err != nil {
		t.Fatalf("write bad ack: %v", err)
	}

	req := httptest.NewRequest(http.MethodGet, "/api/xnode/acks", nil)
	w := httptest.NewRecorder()
	ctrl.ListAcksHandler(w, req)

	// Should still return 200, just skip invalid file
	if w.Code != http.StatusOK {
		t.Errorf("expected status 200, got %d", w.Code)
	}
}

func TestWave134_ListAcksHandler_ReadDirError(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctrl, err := NewXNodeController(db, "test-node")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}

	// Save original and set invalid path
	origAcksDir := XNodeAcksDir
	XNodeAcksDir = "/nonexistent/path/to/acks"
	defer func() { XNodeAcksDir = origAcksDir }()

	req := httptest.NewRequest(http.MethodGet, "/api/xnode/acks", nil)
	w := httptest.NewRecorder()
	ctrl.ListAcksHandler(w, req)

	if w.Code != http.StatusInternalServerError {
		t.Errorf("expected status 500, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// ListNodesHandler tests (xnode.go:477)
// ---------------------------------------------------------------------------

func TestWave134_ListNodesHandler_Success(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctrl, err := NewXNodeController(db, "test-node")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}

	// Register some nodes
	for i := 0; i < 3; i++ {
		node := Node{
			ID:            fmt.Sprintf("node-%d", i),
			Hostname:      fmt.Sprintf("host-%d", i),
			Address:       fmt.Sprintf("127.0.0.%d:8081", i),
			Status:        "online",
			LastHeartbeat: time.Now(),
		}
		if err := ctrl.RegisterNode(context.Background(), node); err != nil {
			t.Fatalf("RegisterNode: %v", err)
		}
	}

	req := httptest.NewRequest(http.MethodGet, "/api/xnode/nodes", nil)
	w := httptest.NewRecorder()
	ctrl.ListNodesHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected status 200, got %d: %s", w.Code, w.Body.String())
	}

	var nodes []Node
	if err := json.Unmarshal(w.Body.Bytes(), &nodes); err != nil {
		t.Errorf("failed to decode response: %v", err)
	}
	if len(nodes) != 3 {
		t.Errorf("expected 3 nodes, got %d", len(nodes))
	}
}

func TestWave134_ListNodesHandler_DBError(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	cleanup() // close DB

	ctrl := &XNodeController{db: db}

	req := httptest.NewRequest(http.MethodGet, "/api/xnode/nodes", nil)
	w := httptest.NewRecorder()
	ctrl.ListNodesHandler(w, req)

	if w.Code != http.StatusInternalServerError {
		t.Errorf("expected status 500, got %d", w.Code)
	}
}

func TestWave134_ListNodesHandler_Empty(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctrl, err := NewXNodeController(db, "test-node")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}

	req := httptest.NewRequest(http.MethodGet, "/api/xnode/nodes", nil)
	w := httptest.NewRecorder()
	ctrl.ListNodesHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected status 200, got %d", w.Code)
	}

	var nodes []Node
	if err := json.Unmarshal(w.Body.Bytes(), &nodes); err != nil {
		t.Errorf("failed to decode response: %v", err)
	}
	if len(nodes) != 0 {
		t.Errorf("expected 0 nodes, got %d", len(nodes))
	}
}

// ---------------------------------------------------------------------------
// detectTailscaleIP tests (util_misc.go:13)
// ---------------------------------------------------------------------------

func TestWave134_DetectTailscaleIP_NoTailscale(t *testing.T) {
	ip := detectTailscaleIP()
	if ip == "" {
		t.Logf("detectTailscaleIP: no tailscale available (expected in test environment)")
	} else {
		t.Logf("detectTailscaleIP: %s", ip)
	}
}

// ---------------------------------------------------------------------------
// serializePendingMessages tests (xnode.go:938)
// ---------------------------------------------------------------------------

func TestWave134_SerializePendingMessages_EmptyDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctrl, err := NewXNodeController(db, "test-node")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}

	// Call with empty DB - should return without error
	ctx := context.Background()
	ctrl.serializePendingMessages(ctx, db)
	t.Log("serializePendingMessages with empty DB completed")
}

func TestWave134_SerializePendingMessages_WithMessages(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctrl, err := NewXNodeController(db, "test-node")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}

	// Create xnode_outbox table
	_, err = db.Exec(`
		CREATE TABLE IF NOT EXISTS xnode_outbox (
			id TEXT PRIMARY KEY,
			target_node TEXT NOT NULL,
			message_type TEXT NOT NULL,
			payload TEXT NOT NULL,
			idempotency_key TEXT,
			status TEXT DEFAULT 'pending',
			created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
		)
	`)
	if err != nil {
		t.Fatalf("create xnode_outbox: %v", err)
	}

	// Insert pending message
	_, err = db.Exec(`
		INSERT INTO xnode_outbox (id, target_node, message_type, payload, status)
		VALUES (?, ?, ?, ?, ?)
	`, "msg-1", "target-node-1", "task_forward", `{"task_id":"task-1"}`, "pending")
	if err != nil {
		t.Fatalf("insert message: %v", err)
	}

	ctx := context.Background()
	ctrl.serializePendingMessages(ctx, db)
	t.Log("serializePendingMessages with messages completed")
}

func TestWave134_SerializePendingMessages_DBError(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	cleanup() // close DB

	ctrl, err := NewXNodeController(db, "test-node")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}

	ctx := context.Background()
	// Should log error but not panic
	ctrl.serializePendingMessages(ctx, db)
	t.Log("serializePendingMessages with closed DB completed (should have logged error)")
}

func TestWave134_SerializePendingMessages_MultipleTargets(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctrl, err := NewXNodeController(db, "test-node")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}

	// Create xnode_outbox table
	_, err = db.Exec(`
		CREATE TABLE IF NOT EXISTS xnode_outbox (
			id TEXT PRIMARY KEY,
			target_node TEXT NOT NULL,
			message_type TEXT NOT NULL,
			payload TEXT NOT NULL,
			idempotency_key TEXT,
			status TEXT DEFAULT 'pending',
			created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
		)
	`)
	if err != nil {
		t.Fatalf("create xnode_outbox: %v", err)
	}

	// Insert messages for different targets
	for i := 0; i < 3; i++ {
		_, err = db.Exec(`
			INSERT INTO xnode_outbox (id, target_node, message_type, payload, status)
			VALUES (?, ?, ?, ?, ?)
		`, fmt.Sprintf("msg-%d", i), fmt.Sprintf("target-node-%d", i%2), "task_forward", `{"task_id":"task-1"}`, "pending")
		if err != nil {
			t.Fatalf("insert message: %v", err)
		}
	}

	ctx := context.Background()
	ctrl.serializePendingMessages(ctx, db)
	t.Log("serializePendingMessages with multiple targets completed")
}

// ---------------------------------------------------------------------------
// RegisterHandler tests (bonus - uses RegisterNode)
// ---------------------------------------------------------------------------

func TestWave134_RegisterHandler_Success(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctrl, err := NewXNodeController(db, "test-node")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}

	node := Node{
		ID:       "handler-node-1",
		Hostname: "handler-host",
		Address:  "127.0.0.1:8081",
		Status:   "online",
	}
	body, _ := json.Marshal(node)

	req := httptest.NewRequest(http.MethodPost, "/api/xnode/nodes/register", bytes.NewReader(body))
	w := httptest.NewRecorder()
	ctrl.RegisterHandler(w, req)

	if w.Code != http.StatusCreated {
		t.Errorf("expected status 201, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave134_RegisterHandler_InvalidMethod(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctrl, err := NewXNodeController(db, "test-node")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}

	req := httptest.NewRequest(http.MethodGet, "/api/xnode/nodes/register", nil)
	w := httptest.NewRecorder()
	ctrl.RegisterHandler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected status 405, got %d", w.Code)
	}
}

func TestWave134_RegisterHandler_InvalidJSON(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctrl, err := NewXNodeController(db, "test-node")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}

	req := httptest.NewRequest(http.MethodPost, "/api/xnode/nodes/register", strings.NewReader("{invalid"))
	w := httptest.NewRecorder()
	ctrl.RegisterHandler(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected status 400, got %d", w.Code)
	}
}

func TestWave134_RegisterHandler_MissingID(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctrl, err := NewXNodeController(db, "test-node")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}

	node := Node{
		Hostname: "handler-host",
		Address:  "127.0.0.1:8081",
	}
	body, _ := json.Marshal(node)

	req := httptest.NewRequest(http.MethodPost, "/api/xnode/nodes/register", bytes.NewReader(body))
	w := httptest.NewRecorder()
	ctrl.RegisterHandler(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected status 400, got %d", w.Code)
	}
}
