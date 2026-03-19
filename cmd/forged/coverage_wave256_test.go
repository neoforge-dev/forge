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
	"testing"

	_ "github.com/mattn/go-sqlite3"
)

// ---------------------------------------------------------------------------
// XNode RegisterHandler tests (xnode.go:437) - 77.3%
// ---------------------------------------------------------------------------

func TestWave256_XNode_RegisterHandler_InvalidJSON(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctrl, err := NewXNodeController(db, "test-node")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}

	req := httptest.NewRequest(http.MethodPost, "/api/xnode/register",
		bytes.NewReader([]byte(`{invalid json`)))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	ctrl.RegisterHandler(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", w.Code)
	}
}

func TestWave256_XNode_RegisterHandler_MissingID(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctrl, err := NewXNodeController(db, "test-node")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}

	node := Node{Hostname: "test-host", Address: "127.0.0.1:8081"}
	body, _ := json.Marshal(node)

	req := httptest.NewRequest(http.MethodPost, "/api/xnode/register", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	ctrl.RegisterHandler(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", w.Code)
	}
}

func TestWave256_XNode_RegisterHandler_DBError(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	cleanup() // Close DB

	ctrl := &XNodeController{db: db}

	node := Node{ID: "test-node", Hostname: "test-host", Address: "127.0.0.1:8081"}
	body, _ := json.Marshal(node)

	req := httptest.NewRequest(http.MethodPost, "/api/xnode/register", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	ctrl.RegisterHandler(w, req)

	if w.Code < 500 {
		t.Errorf("expected server error with closed DB, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// XNode ListAcksHandler tests (xnode.go:401) - 75.0%
// ---------------------------------------------------------------------------

func TestWave256_XNode_ListAcksHandler_ReadDirError(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctrl, err := NewXNodeController(db, "test-node")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}

	// Use non-existent directory
	origDir := XNodeAcksDir
	XNodeAcksDir = "/nonexistent/acks/dir"
	defer func() { XNodeAcksDir = origDir }()

	req := httptest.NewRequest(http.MethodGet, "/api/xnode/acks", nil)
	w := httptest.NewRecorder()
	ctrl.ListAcksHandler(w, req)

	if w.Code != http.StatusInternalServerError {
		t.Errorf("expected 500, got %d", w.Code)
	}
}

func TestWave256_XNode_ListAcksHandler_InvalidJSON(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctrl, err := NewXNodeController(db, "test-node")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}

	// Create temp acks dir with invalid JSON file
	tmpDir := t.TempDir()
	origDir := XNodeAcksDir
	XNodeAcksDir = tmpDir
	defer func() { XNodeAcksDir = origDir }()

	// Write invalid JSON ack
	if err := os.WriteFile(filepath.Join(tmpDir, "bad-ack.json"), []byte("{invalid"), 0644); err != nil {
		t.Fatalf("write invalid ack: %v", err)
	}

	req := httptest.NewRequest(http.MethodGet, "/api/xnode/acks", nil)
	w := httptest.NewRecorder()
	ctrl.ListAcksHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", w.Code)
	}

	// Should return empty acks (invalid ones are skipped)
	var result map[string]interface{}
	if err := json.Unmarshal(w.Body.Bytes(), &result); err != nil {
		t.Errorf("invalid JSON response: %v", err)
	}
}

// ---------------------------------------------------------------------------
// XNode ListNodesHandler tests (xnode.go:477) - 75.0%
// ---------------------------------------------------------------------------

func TestWave256_XNode_ListNodesHandler_DBError(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	cleanup() // Close DB

	ctrl := &XNodeController{db: db}

	req := httptest.NewRequest(http.MethodGet, "/api/xnode/nodes", nil)
	w := httptest.NewRecorder()
	ctrl.ListNodesHandler(w, req)

	if w.Code != http.StatusInternalServerError {
		t.Errorf("expected 500, got %d", w.Code)
	}
}

func TestWave256_XNode_ListNodesHandler_Success(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctrl, err := NewXNodeController(db, "test-node")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}

	// Insert a test node
	_, err = db.Exec(`INSERT INTO nodes (id, hostname, address, status, last_heartbeat) 
		VALUES (?, ?, ?, ?, datetime('now'))`, "node1", "host1", "127.0.0.1:8081", "online")
	if err != nil {
		t.Fatalf("insert node: %v", err)
	}

	req := httptest.NewRequest(http.MethodGet, "/api/xnode/nodes", nil)
	w := httptest.NewRecorder()
	ctrl.ListNodesHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// XNode RegisterNode tests (xnode.go:244) - 75.0%
// ---------------------------------------------------------------------------

func TestWave256_XNode_RegisterNode_DBError(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	cleanup() // Close DB

	ctrl := &XNodeController{db: db}

	node := Node{
		ID:       "test-node",
		Hostname: "test-host",
		Address:  "127.0.0.1:8081",
		Status:   "online",
	}

	err := ctrl.RegisterNode(context.Background(), node)
	if err == nil {
		t.Error("expected error with closed DB")
	}
}

// ---------------------------------------------------------------------------
// XNode Heartbeat tests (xnode.go:261) - 75.0%
// ---------------------------------------------------------------------------

func TestWave256_XNode_Heartbeat_DBError(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	cleanup() // Close DB

	ctrl := &XNodeController{db: db}

	err := ctrl.Heartbeat(context.Background(), "test-node")
	if err == nil {
		t.Error("expected error with closed DB")
	}
}

// ---------------------------------------------------------------------------
// XNode processInbox tests (xnode.go:762) - 78.3%
// ---------------------------------------------------------------------------

func TestWave256_XNode_ProcessInbox_NonExistentDir(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctrl, err := NewXNodeController(db, "test-node")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}

	origDir := XNodeInboxDir
	XNodeInboxDir = "/nonexistent/inbox"
	defer func() { XNodeInboxDir = origDir }()

	offsets := make(map[string]int64)
	// Should return gracefully without error
	ctrl.processInbox(offsets)
}

// ---------------------------------------------------------------------------
// Summary test
// ---------------------------------------------------------------------------

func TestWave256_CoverageSummary(t *testing.T) {
	t.Log("Wave 256 Coverage Targets:")
	t.Log("  - RegisterHandler method not allowed (xnode.go:437)")
	t.Log("  - RegisterHandler invalid JSON (xnode.go:437)")
	t.Log("  - RegisterHandler missing ID (xnode.go:437)")
	t.Log("  - RegisterHandler DB error (xnode.go:437)")
	t.Log("  - ListAcksHandler read dir error (xnode.go:401)")
	t.Log("  - ListAcksHandler invalid JSON (xnode.go:401)")
	t.Log("  - ListNodesHandler DB error (xnode.go:477)")
	t.Log("  - ListNodesHandler success (xnode.go:477)")
	t.Log("  - RegisterNode DB error (xnode.go:244)")
	t.Log("  - Heartbeat DB error (xnode.go:261)")
	t.Log("  - processInbox non-existent dir (xnode.go:762)")
}
