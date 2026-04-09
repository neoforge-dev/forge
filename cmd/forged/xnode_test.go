//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
	"time"

	_ "github.com/mattn/go-sqlite3"
)

func setupXNodeTestDB(t *testing.T) *sql.DB {
	db, err := sql.Open("sqlite3", ":memory:")
	if err != nil {
		t.Fatalf("failed to open in-memory db: %v", err)
	}

	_, err = db.Exec(`
		CREATE TABLE nodes (
			id TEXT PRIMARY KEY,
			hostname TEXT NOT NULL,
			address TEXT NOT NULL,
			status TEXT NOT NULL,
			last_heartbeat TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
			version TEXT NOT NULL DEFAULT '',
			git_commit TEXT NOT NULL DEFAULT ''
		);
		CREATE TABLE xnode_tasks (
			id TEXT PRIMARY KEY,
			source_node TEXT NOT NULL,
			target_node TEXT NOT NULL,
			task_id TEXT NOT NULL,
			status TEXT NOT NULL,
			created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
			updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
		);
	`)
	if err != nil {
		t.Fatalf("failed to create tables: %v", err)
	}

	return db
}

func TestXNodeController(t *testing.T) {
	db := setupXNodeTestDB(t)
	defer db.Close()

	tmpDir, err := os.MkdirTemp("", "forge-xnode-test")
	if err != nil {
		t.Fatalf("failed to create tmp dir: %v", err)
	}
	defer os.RemoveAll(tmpDir)

	xc, err := NewXNodeController(db, "test-node")
	if err != nil {
		t.Fatalf("failed to create XNodeController: %v", err)
	}

	ctx := context.Background()

	// Test RegisterNode
	node := Node{
		ID:       "node-2",
		Hostname: "node-2-host",
		Address:  "10.0.0.2:8081",
		Status:   "online",
	}
	err = xc.RegisterNode(ctx, node)
	if err != nil {
		t.Fatalf("RegisterNode failed: %v", err)
	}

	// Verify in DB
	var count int
	db.QueryRow("SELECT COUNT(*) FROM nodes WHERE id = 'node-2'").Scan(&count)
	if count != 1 {
		t.Errorf("node not registered in DB")
	}

	// Test ForwardTask
	xc.outboxDir = tmpDir

	taskID := "task-123"
	err = xc.ForwardTask(ctx, "node-2", taskID)
	if err != nil {
		t.Fatalf("ForwardTask failed: %v", err)
	}

	// Verify xnode_task record
	var status string
	err = db.QueryRow("SELECT status FROM xnode_tasks WHERE target_node = 'node-2' AND task_id = ?", taskID).Scan(&status)
	if err != nil {
		t.Fatalf("xnode_task record not found: %v", err)
	}
	if status != "forwarded" {
		t.Errorf("wrong xnode_task status: %s", status)
	}

	// Verify outbox file
	outboxPath := filepath.Join(tmpDir, "node-2.jsonl")
	data, err := os.ReadFile(outboxPath)
	if err != nil {
		t.Fatalf("outbox file not found: %v", err)
	}

	var msg map[string]interface{}
	err = json.Unmarshal(data, &msg)
	if err != nil {
		t.Fatalf("failed to unmarshal outbox message: %v", err)
	}

	if msg["type"] != "task_forward" {
		t.Errorf("wrong message type: %s", msg["type"])
	}
	payload := msg["payload"].(map[string]interface{})
	if payload["task_id"] != taskID {
		t.Errorf("wrong task_id in payload: %s", payload["task_id"])
	}
}

// --- XNodeError constructors ---

func TestXNodeErrorConstructors(t *testing.T) {
	inner := errors.New("cause")
	cases := []struct {
		name string
		err  *XNodeError
	}{
		{"NodeNotFound", newXNodeNodeNotFoundError("n1", inner)},
		{"NodeOffline", newXNodeNodeOfflineError("n1", "maintenance")},
		{"ForwardFailed", newXNodeForwardFailedError("t1", inner)},
		{"OutboxWrite", newXNodeOutboxWriteError("n1", inner)},
		{"InvalidRequest", newXNodeInvalidRequestError("field", inner)},
		{"Database", newXNodeDatabaseError("select", inner)},
	}
	for _, c := range cases {
		if c.err == nil {
			t.Errorf("%s: expected non-nil", c.name)
		}
		if c.err.Code == "" {
			t.Errorf("%s: expected non-empty code", c.name)
		}
		if c.err.Error() == "" {
			t.Errorf("%s: expected non-empty message", c.name)
		}
	}
}

func TestXNodeError_Unwrap(t *testing.T) {
	inner := errors.New("inner")
	e := newXNodeForwardFailedError("task-1", inner)
	if e.Unwrap() != inner {
		t.Error("expected Unwrap to return wrapped error")
	}
	e2 := newXNodeNodeOfflineError("node-1", "offline")
	if e2.Unwrap() != nil {
		t.Error("expected Unwrap to return nil for no-wrap error")
	}
}

func TestXNodeNewController_NilDB(t *testing.T) {
	_, err := NewXNodeController(nil, "node")
	if err == nil {
		t.Error("expected error for nil DB")
	}
}

// setupXNodeHTTP creates a controller with in-memory DB and temp dirs for HTTP handler tests
func setupXNodeHTTP(t *testing.T) (*XNodeController, func()) {
	t.Helper()
	db := setupXNodeTestDB(t)

	tmpDir := t.TempDir()
	origInbox := XNodeInboxDir
	origAcks := XNodeAcksDir
	origOutbox := XNodeOutboxDir
	XNodeInboxDir = tmpDir + "/inbox"
	XNodeAcksDir = tmpDir + "/acks"
	XNodeOutboxDir = tmpDir + "/outbox"
	for _, d := range []string{XNodeInboxDir, XNodeAcksDir, XNodeOutboxDir} {
		if err := os.MkdirAll(d, 0755); err != nil {
			t.Fatalf("mkdir %s: %v", d, err)
		}
	}

	xc, err := NewXNodeController(db, "test-node")
	if err != nil {
		db.Close()
		t.Fatalf("NewXNodeController: %v", err)
	}

	return xc, func() {
		XNodeInboxDir = origInbox
		XNodeAcksDir = origAcks
		XNodeOutboxDir = origOutbox
		db.Close()
	}
}

func TestXNodeListNodesHandler(t *testing.T) {
	xc, cleanup := setupXNodeHTTP(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/xnode/nodes", nil)
	w := httptest.NewRecorder()
	xc.ListNodesHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestXNodeRegisterHandler_Success(t *testing.T) {
	xc, cleanup := setupXNodeHTTP(t)
	defer cleanup()

	body, _ := json.Marshal(map[string]string{
		"id":       "node-test-reg",
		"hostname": "testhost",
		"address":  "10.0.0.1",
		"status":   "online",
	})
	req := httptest.NewRequest(http.MethodPost, "/api/xnode/nodes/register", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	xc.RegisterHandler(w, req)

	if w.Code != http.StatusCreated {
		t.Errorf("expected 201, got %d: %s", w.Code, w.Body.String())
	}
}

func TestXNodeRegisterHandler_InvalidMethod(t *testing.T) {
	xc, cleanup := setupXNodeHTTP(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/xnode/nodes/register", nil)
	w := httptest.NewRecorder()
	xc.RegisterHandler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestXNodeRegisterHandler_MissingID(t *testing.T) {
	xc, cleanup := setupXNodeHTTP(t)
	defer cleanup()

	body, _ := json.Marshal(map[string]string{"hostname": "h"})
	req := httptest.NewRequest(http.MethodPost, "/api/xnode/nodes/register", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	xc.RegisterHandler(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d: %s", w.Code, w.Body.String())
	}
}

func TestXNodeStatusHandler(t *testing.T) {
	xc, cleanup := setupXNodeHTTP(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/xnode/status", nil)
	w := httptest.NewRecorder()
	xc.StatusHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestXNodeForwardHandler_InvalidMethod(t *testing.T) {
	xc, cleanup := setupXNodeHTTP(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/xnode/forward", nil)
	w := httptest.NewRecorder()
	xc.ForwardHandler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestXNodeListInboxHandler(t *testing.T) {
	xc, cleanup := setupXNodeHTTP(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/xnode/inbox", nil)
	w := httptest.NewRecorder()
	xc.ListInboxHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestXNodeListAcksHandler(t *testing.T) {
	xc, cleanup := setupXNodeHTTP(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/xnode/acks", nil)
	w := httptest.NewRecorder()
	xc.ListAcksHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

// --- New tests for zero-coverage xnode.go functions ---

// TestXNodeRegisterRoutes verifies all expected paths are wired into the mux without panic.
func TestXNodeRegisterRoutes(t *testing.T) {
	xc, cleanup := setupXNodeHTTP(t)
	defer cleanup()

	mux := http.NewServeMux()
	// Must not panic.
	xc.RegisterRoutes(mux)

	// Spot-check that two key patterns are registered.
	paths := []string{
		"/api/xnode/nodes",
		"/api/xnode/status",
		"/api/xnode/forward",
		"/api/xnode/acks",
	}
	for _, p := range paths {
		req := httptest.NewRequest(http.MethodGet, p, nil)
		w := httptest.NewRecorder()
		mux.ServeHTTP(w, req)
		// We just need the handler to exist (not 404).
		if w.Code == http.StatusNotFound {
			t.Errorf("path %s not routed — got 404", p)
		}
	}
}

// TestXNodeStartHeartbeatMonitor verifies the heartbeat monitor goroutine starts without panicking.
func TestXNodeStartHeartbeatMonitor(t *testing.T) {
	xc, cleanup := setupXNodeHTTP(t)
	defer func() {
		StopXNode()
		cleanup()
	}()
	// Ensure xnodeCtx is initialized before starting the monitor.
	initXNodeContext()
	// Short interval so the goroutine fires quickly.
	xc.StartHeartbeatMonitor(30 * time.Millisecond)
	// Give it two ticks to run without panicking.
	time.Sleep(80 * time.Millisecond)
}

// closeNotifyRecorder wraps httptest.ResponseRecorder and implements http.Flusher so
// SSEDeliveryHandler does not fall back to the non-streaming error path.
type closeNotifyRecorder struct {
	*httptest.ResponseRecorder
}

func (c *closeNotifyRecorder) Flush() {}

// TestXNodeSSEDeliveryHandler_Disconnect verifies the SSE handler terminates cleanly when
// the request context is cancelled (simulating a client disconnect).
func TestXNodeSSEDeliveryHandler_Disconnect(t *testing.T) {
	xc, cleanup := setupXNodeHTTP(t)
	defer cleanup()

	ctx, cancel := context.WithCancel(context.Background())
	req := httptest.NewRequest(http.MethodGet, "/api/xnode/events", nil).WithContext(ctx)
	w := &closeNotifyRecorder{ResponseRecorder: httptest.NewRecorder()}

	done := make(chan struct{})
	go func() {
		defer close(done)
		xc.SSEDeliveryHandler(w, req)
	}()

	// Brief pause to let handler enter its select loop, then cancel.
	time.Sleep(20 * time.Millisecond)
	cancel()

	select {
	case <-done:
		// Good — handler returned after disconnect.
	case <-time.After(3 * time.Second):
		t.Error("SSEDeliveryHandler did not return after context cancellation")
	}
}

// TestXNodeStartInboxWorker verifies the inbox worker goroutine starts and can be stopped.
func TestXNodeStartInboxWorker(t *testing.T) {
	xc, cleanup := setupXNodeHTTP(t)
	defer cleanup()

	ctx, cancel := context.WithCancel(context.Background())
	xc.StartInboxWorker(ctx)
	// Let goroutine settle for one poll cycle.
	time.Sleep(20 * time.Millisecond)
	cancel()
}

// TestXNodeProcessInbox_WithMessages verifies processInbox reads JSONL and writes ack files.
func TestXNodeProcessInbox_WithMessages(t *testing.T) {
	t.Setenv("FORGE_TAILSCALE_IP", "") // skip exec call — heartbeat_update for new node triggers detectTailscaleIP
	xc, cleanup := setupXNodeHTTP(t)
	defer cleanup()

	// Write a heartbeat_update message to the inbox dir so processInbox has something to do.
	msg := map[string]interface{}{
		"message_id": "hb-pi-001",
		"type":       "heartbeat_update",
		"source":     "remote-pi-node",
	}
	data, _ := json.Marshal(msg)
	inboxFile := filepath.Join(XNodeInboxDir, "remote-pi-node.jsonl")
	if err := os.WriteFile(inboxFile, append(data, '\n'), 0644); err != nil {
		t.Fatalf("write inbox file: %v", err)
	}

	offsets := make(map[string]int64)
	xc.processInbox(offsets)

	// An ack file should have been created for the processed message.
	ackFile := filepath.Join(XNodeAcksDir, "hb-pi-001.json")
	if _, err := os.Stat(ackFile); os.IsNotExist(err) {
		t.Error("expected ack file to be written by processInbox")
	}
}

// TestXNodeProcessInbox_MissingDir verifies processInbox handles a non-existent inbox dir gracefully.
func TestXNodeProcessInbox_MissingDir(t *testing.T) {
	xc, cleanup := setupXNodeHTTP(t)
	defer cleanup()

	// Point the global inbox dir to a path that does not exist.
	oldInbox := XNodeInboxDir
	XNodeInboxDir = "/tmp/xnode-test-inbox-does-not-exist-" + t.Name()
	defer func() { XNodeInboxDir = oldInbox }()

	// Must not panic or return an error.
	offsets := make(map[string]int64)
	xc.processInbox(offsets)
}

// TestXNodeRespondWithXNodeError_WithStatus verifies the helper sets the provided HTTP status.
func TestXNodeRespondWithXNodeError_WithStatus(t *testing.T) {
	xerr := newXNodeNodeNotFoundError("missing-node", nil)
	w := httptest.NewRecorder()
	respondWithXNodeError(w, xerr, http.StatusNotFound)

	if w.Code != http.StatusNotFound {
		t.Errorf("expected 404, got %d", w.Code)
	}
	var body map[string]interface{}
	if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil {
		t.Fatalf("unmarshal response: %v", err)
	}
	if body["code"] != string(XNodeErrNodeNotFound) {
		t.Errorf("expected code %s, got %v", XNodeErrNodeNotFound, body["code"])
	}
}

// TestXNodeRespondWithXNodeError_DefaultStatus verifies the helper defaults to 500.
func TestXNodeRespondWithXNodeError_DefaultStatus(t *testing.T) {
	xerr := newXNodeDatabaseError("test-op", nil)
	w := httptest.NewRecorder()
	respondWithXNodeError(w, xerr) // no status argument → should default to 500

	if w.Code != http.StatusInternalServerError {
		t.Errorf("expected 500, got %d", w.Code)
	}
}

// TestXNodeIsXNodeError_True verifies the helper returns true for *XNodeError values.
func TestXNodeIsXNodeError_True(t *testing.T) {
	xerr := newXNodeOutboxWriteError("n1", nil)
	got, ok := isXNodeError(xerr)
	if !ok {
		t.Fatal("expected isXNodeError to return true for *XNodeError")
	}
	if got.Code != XNodeErrOutboxWrite {
		t.Errorf("wrong code: %v", got.Code)
	}
}

// TestXNodeIsXNodeError_False verifies the helper returns false for plain errors.
func TestXNodeIsXNodeError_False(t *testing.T) {
	plainErr := errors.New("not an xnode error")
	_, ok := isXNodeError(plainErr)
	if ok {
		t.Error("expected isXNodeError to return false for plain error")
	}
}

// TestXNodeStartSerializationWorker verifies the serialization worker starts and stops cleanly.
func TestXNodeStartSerializationWorker(t *testing.T) {
	xc, cleanup := setupXNodeHTTP(t)
	defer cleanup()

	db, dbCleanup := setupClaimTestDB(t)
	defer dbCleanup()

	ctx, cancel := context.WithCancel(context.Background())
	xc.StartSerializationWorker(ctx, db)
	// Give goroutine a moment to start.
	time.Sleep(20 * time.Millisecond)
	cancel()
}

// Wave 30b: StopXNode, initXNodeContext (0% coverage)

func TestInitXNodeContext_W30B(t *testing.T) {
	ctx := initXNodeContext()
	if ctx == nil {
		t.Error("expected non-nil context")
	}
	// Stop it to avoid goroutine leak
	StopXNode()
}

func TestStopXNode_NoCancel_W30B(t *testing.T) {
	// Call StopXNode when xnodeCancel is nil — should not panic
	orig := xnodeCancel
	xnodeCancel = nil
	defer func() { xnodeCancel = orig }()

	StopXNode() // should be a no-op
}

func TestStopXNode_WithCancel_W30B(t *testing.T) {
	// Initialize context and then stop
	initXNodeContext()
	StopXNode()
	// Verify xnodeCancel is nil after stop
	if xnodeCancel != nil {
		t.Error("expected xnodeCancel to be nil after StopXNode")
	}
}
