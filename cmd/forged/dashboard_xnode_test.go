//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// TestDash_NewDashboard verifies dashboard initialization and template loading.
func TestDash_NewDashboard(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	h := NewHub()
	d := NewDashboard(db, h)
	if d == nil {
		t.Fatal("NewDashboard returned nil")
	}
	if d.db != db {
		t.Error("Dashboard db not set correctly")
	}
}

// TestDash_ServeDashboard verifies the dashboard HTML HTTP handler.
func TestDash_ServeDashboard(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	h := NewHub()
	d := NewDashboard(db, h)

	req := httptest.NewRequest(http.MethodGet, "/dashboard", nil)
	rr := httptest.NewRecorder()

	d.ServeDashboard(rr, req)

	if rr.Code != http.StatusOK {
		t.Errorf("expected 200 OK, got %d", rr.Code)
	}
	if !strings.Contains(rr.Body.String(), "FORGE Agent Health Dashboard") {
		t.Error("Dashboard HTML missing title")
	}
}

// TestDash_ServeDashboardJSON verifies the dashboard data JSON API.
func TestDash_ServeDashboardJSON(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	h := NewHub()
	d := NewDashboard(db, h)

	// Seed some agent data
	_, err := db.Exec(`
		INSERT INTO agent_heartbeats (agent_id, node, status, context_pct, last_seen, connected_at)
		VALUES ('agent-001', 'node-1', 'idle', 10.0, datetime('now'), datetime('now'))
	`)
	if err != nil {
		t.Fatalf("failed to seed agent: %v", err)
	}

	req := httptest.NewRequest(http.MethodGet, "/api/dashboard", nil)
	rr := httptest.NewRecorder()

	d.ServeDashboardJSON(rr, req)

	if rr.Code != http.StatusOK {
		t.Errorf("expected 200 OK, got %d", rr.Code)
	}

	var data DashboardData
	if err := json.Unmarshal(rr.Body.Bytes(), &data); err != nil {
		t.Fatalf("failed to unmarshal dashboard data: %v", err)
	}

	if len(data.Agents) == 0 {
		t.Error("expected at least one agent in dashboard data")
	}
	if data.Agents[0].AgentID != "agent-001" {
		t.Errorf("expected agent-001, got %s", data.Agents[0].AgentID)
	}
}

// TestDash_GetDashboardData verifies data aggregation logic.
func TestDash_GetDashboardData(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	h := NewHub()
	d := NewDashboard(db, h)

	// Seed agents, tasks, nodes
	db.Exec(`INSERT INTO nodes (id, hostname, address, status, last_heartbeat) VALUES ('node-1', 'host-1', '1.1.1.1', 'online', datetime('now'))`)
	db.Exec(`INSERT INTO agent_heartbeats (agent_id, node, status, context_pct, last_seen, connected_at) VALUES ('agent-1', 'node-1', 'working', 50.0, datetime('now'), datetime('now'))`)
	db.Exec(`INSERT INTO tasks (id, status, state, domain, project, type, priority, created_at, updated_at) VALUES ('task-1', 'executing', 'RUNNING', 'test', 'test', 'feature', 5, datetime('now'), datetime('now'))`)

	data, err := d.GetDashboardData(context.Background())
	if err != nil {
		t.Fatalf("GetDashboardData failed: %v", err)
	}

	if data.Summary.TotalAgents != 1 {
		t.Errorf("expected 1 agent, got %d", data.Summary.TotalAgents)
	}
	if len(data.Nodes) != 1 {
		t.Errorf("expected 1 node, got %d", len(data.Nodes))
	}
}

// TestXNode_RegisterHandler verifies node registration via HTTP.
func TestXNode_RegisterHandler(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	xc, _ := NewXNodeController(db, "test-node")
	
	node := Node{
		ID:       "remote-node",
		Hostname: "remote-host",
		Address:  "10.0.0.2",
	}
	body, _ := json.Marshal(node)
	req := httptest.NewRequest(http.MethodPost, "/api/xnode/register", bytes.NewReader(body))
	rr := httptest.NewRecorder()

	xc.RegisterHandler(rr, req)

	if rr.Code != http.StatusCreated {
		t.Errorf("expected 201 Created, got %d: %s", rr.Code, rr.Body.String())
	}

	var registered Node
	json.Unmarshal(rr.Body.Bytes(), &registered)
	if registered.ID != "remote-node" {
		t.Errorf("expected remote-node, got %s", registered.ID)
	}
}

// TestXNode_StatusHandler verifies the status report.
func TestXNode_StatusHandler(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	xc, _ := NewXNodeController(db, "local-node")
	
	req := httptest.NewRequest(http.MethodGet, "/api/xnode/status", nil)
	rr := httptest.NewRecorder()

	xc.StatusHandler(rr, req)

	if rr.Code != http.StatusOK {
		t.Errorf("expected 200 OK, got %d", rr.Code)
	}

	var status map[string]interface{}
	json.Unmarshal(rr.Body.Bytes(), &status)
	if status["node_id"] != "local-node" {
		t.Errorf("expected local-node, got %v", status["node_id"])
	}
}

// TestXNode_SerializationWorker verifies that pending messages are written to JSONL.
func TestXNode_SerializationWorker(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	tmpDir, _ := os.MkdirTemp("", "xnode-outbox-*")
	defer os.RemoveAll(tmpDir)

	xc, _ := NewXNodeController(db, "source-node")
	xc.outboxDir = tmpDir

	// Seed a pending message in the outbox table
	_, err := db.Exec(`
		INSERT INTO xnode_outbox (id, target_node, message_type, payload, status, created_at)
		VALUES ('msg-001', 'target-node', 'test_type', '{"foo":"bar"}', 'pending', datetime('now'))
	`)
	if err != nil {
		t.Fatalf("failed to seed outbox: %v", err)
	}

	// Run serialization logic
	xc.serializePendingMessages(context.Background(), db)

	// Verify file was created
	filePath := filepath.Join(tmpDir, "target-node.jsonl")
	if _, err := os.Stat(filePath); os.IsNotExist(err) {
		t.Fatal("outbox JSONL file was not created")
	}

	// Verify message status updated
	var status string
	db.QueryRow("SELECT status FROM xnode_outbox WHERE id = 'msg-001'").Scan(&status)
	if status != "serialized" {
		t.Errorf("expected status 'serialized', got %s", status)
	}
}

// TestXNode_IngestIncomingMessages verifies ingestion from inbox JSONL to DB.
func TestXNode_IngestIncomingMessages(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	tmpDir, _ := os.MkdirTemp("", "xnode-inbox-*")
	defer os.RemoveAll(tmpDir)

	// Override global XNodeInboxDir for the test
	oldInboxDir := XNodeInboxDir
	XNodeInboxDir = tmpDir
	defer func() { XNodeInboxDir = oldInboxDir }()

	xc, _ := NewXNodeController(db, "target-node")

	// Create a dummy inbox file
	msg := map[string]interface{}{
		"message_id": "in-001",
		"type":       "task_forward",
		"source":     "remote-node",
		"payload":    map[string]interface{}{"task_id": "task-abc"},
	}
	data, _ := json.Marshal(msg)
	os.WriteFile(filepath.Join(tmpDir, "remote-node.jsonl"), append(data, '\n'), 0644)

	err := xc.IngestIncomingMessages(context.Background(), db)
	if err != nil {
		t.Fatalf("IngestIncomingMessages failed: %v", err)
	}

	// Verify message in xnode_inbox table
	var count int
	db.QueryRow("SELECT COUNT(*) FROM xnode_inbox WHERE id = 'in-001'").Scan(&count)
	if count != 1 {
		t.Error("message was not ingested into xnode_inbox table")
	}
}

// TestXNode_RouteMessage verifies routing of different message types.
func TestXNode_RouteMessage(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	tmpAcks, _ := os.MkdirTemp("", "xnode-acks-*")
	defer os.RemoveAll(tmpAcks)

	// Override global XNodeAcksDir
	oldAcksDir := XNodeAcksDir
	XNodeAcksDir = tmpAcks
	defer func() { XNodeAcksDir = oldAcksDir }()

	xc, _ := NewXNodeController(db, "local-node")

	// 1. Test heartbeat_update
	msgHB := map[string]interface{}{
		"message_id": "hb-001",
		"type":       "heartbeat_update",
		"source":     "peer-node",
	}
	if err := xc.routeMessage(context.Background(), msgHB); err != nil {
		t.Errorf("routeMessage heartbeat failed: %v", err)
	}

	// Verify node was updated/inserted
	var status string
	err := db.QueryRow("SELECT status FROM nodes WHERE id = 'peer-node'").Scan(&status)
	if err != nil {
		t.Errorf("node 'peer-node' not found: %v", err)
	}

	// 2. Test task_forward
	msgTF := map[string]interface{}{
		"message_id": "tf-001",
		"type":       "task_forward",
		"payload":    map[string]interface{}{"task_id": "remote-task-123"},
	}
	if err := xc.routeMessage(context.Background(), msgTF); err != nil {
		t.Errorf("routeMessage task_forward failed: %v", err)
	}

	// Verify task was inserted
	var taskID string
	err = db.QueryRow("SELECT id FROM tasks WHERE domain = 'xnode' AND type = 'xnode_forward'").Scan(&taskID)
	if err != nil {
		t.Errorf("forwarded task not found in DB: %v", err)
	}

	// Verify ack file exists
	if _, err := os.Stat(filepath.Join(tmpAcks, "tf-001.json")); os.IsNotExist(err) {
		t.Error("ack file was not created for task_forward")
	}
}
