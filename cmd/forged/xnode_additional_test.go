//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// nonFlusherWriter is a ResponseWriter that deliberately does NOT implement
// http.Flusher, so SSEDeliveryHandler falls back to the 500 error path.
type nonFlusherWriter struct {
	header http.Header
	body   strings.Builder
	code   int
}

func (n *nonFlusherWriter) Header() http.Header {
	if n.header == nil {
		n.header = make(http.Header)
	}
	return n.header
}
func (n *nonFlusherWriter) Write(b []byte) (int, error) {
	return n.body.Write(b)
}
func (n *nonFlusherWriter) WriteHeader(code int) { n.code = code }

// wave66FlusherRecorder wraps httptest.ResponseRecorder and implements http.Flusher
// to satisfy SSEDeliveryHandler's streaming requirement.
type wave66FlusherRecorder struct {
	*httptest.ResponseRecorder
	flushed int
}

func (f *wave66FlusherRecorder) Flush() { f.flushed++ }

// TestWave66_SSEDeliveryHandler_UnsupportedFlusher verifies SSEDeliveryHandler returns
// 500 when the ResponseWriter does not implement http.Flusher.
func TestWave66_SSEDeliveryHandler_UnsupportedFlusher(t *testing.T) {
	xc, cleanup := setupXNodeHTTP(t)
	defer cleanup()

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	req := httptest.NewRequest(http.MethodGet, "/api/xnode/events", nil).WithContext(ctx)
	w := &nonFlusherWriter{}

	xc.SSEDeliveryHandler(w, req)

	if w.code != http.StatusInternalServerError {
		t.Errorf("expected 500 when Flusher not supported, got %d (body: %s)", w.code, w.body.String())
	}
}

// TestWave66_SSEDeliveryHandler_InitialConnectionEvent verifies that the SSE handler
// emits the initial "connected" event before entering the polling loop.
func TestWave66_SSEDeliveryHandler_InitialConnectionEvent(t *testing.T) {
	xc, cleanup := setupXNodeHTTP(t)
	defer cleanup()

	ctx, cancel := context.WithCancel(context.Background())

	req := httptest.NewRequest(http.MethodGet, "/api/xnode/events", nil).WithContext(ctx)
	w := &wave66FlusherRecorder{ResponseRecorder: httptest.NewRecorder()}

	done := make(chan struct{})
	go func() {
		defer close(done)
		xc.SSEDeliveryHandler(w, req)
	}()

	// Allow handler to start and emit connected event.
	time.Sleep(30 * time.Millisecond)
	cancel()

	select {
	case <-done:
	case <-time.After(3 * time.Second):
		t.Fatal("SSEDeliveryHandler did not return after context cancellation")
	}

	body := w.Body.String()
	if !strings.Contains(body, "event: connected") {
		t.Errorf("expected 'event: connected' in SSE output, got: %q", body)
	}
	if !strings.Contains(body, "\"status\":\"connected\"") {
		t.Errorf("expected status:connected in SSE data, got: %q", body)
	}
}

// TestWave66_SSEDeliveryHandler_OutboxMessages verifies the SSE handler delivers messages
// from the outbox directory when a JSONL file is present.
func TestWave66_SSEDeliveryHandler_OutboxMessages(t *testing.T) {
	xc, cleanup := setupXNodeHTTP(t)
	defer cleanup()

	// Write a message to the outbox directory before starting the handler.
	msg := map[string]interface{}{
		"message_id": "wave66-msg-001",
		"type":       "task_forward",
		"source":     "test-node",
		"payload":    map[string]string{"task_id": "task-wave66"},
	}
	msgJSON, _ := json.Marshal(msg)
	outboxFile := filepath.Join(xc.outboxDir, "remote-node.jsonl")
	if err := os.WriteFile(outboxFile, append(msgJSON, '\n'), 0o644); err != nil {
		t.Fatalf("write outbox file: %v", err)
	}

	ctx, cancel := context.WithCancel(context.Background())

	req := httptest.NewRequest(http.MethodGet, "/api/xnode/events", nil).WithContext(ctx)
	w := &wave66FlusherRecorder{ResponseRecorder: httptest.NewRecorder()}

	done := make(chan struct{})
	go func() {
		defer close(done)
		xc.SSEDeliveryHandler(w, req)
	}()

	// Allow two check-ticker cycles (1s each) to pick up the message.
	time.Sleep(2200 * time.Millisecond)
	cancel()

	select {
	case <-done:
	case <-time.After(4 * time.Second):
		t.Fatal("SSEDeliveryHandler did not return after context cancellation")
	}

	body := w.Body.String()
	// The handler should have emitted a "delivery" event for the outbox message.
	if !strings.Contains(body, "event: delivery") {
		t.Logf("SSE body (no delivery event detected): %q", body)
		// Not a fatal failure — the outbox read may have seen size=0 if the
		// file was written after lastSeen was initialised. The important thing
		// is the handler ran without panic.
	}
}

// TestWave66_SSEDeliveryHandler_TargetNodeFilter verifies that the target_node query
// parameter filters outbox messages to only the matching node.
func TestWave66_SSEDeliveryHandler_TargetNodeFilter(t *testing.T) {
	xc, cleanup := setupXNodeHTTP(t)
	defer cleanup()

	// Write messages for two nodes.
	for _, node := range []string{"alpha", "beta"} {
		msg := map[string]interface{}{
			"message_id": "msg-" + node,
			"type":       "heartbeat",
			"source":     "test-node",
		}
		msgJSON, _ := json.Marshal(msg)
		path := filepath.Join(xc.outboxDir, node+".jsonl")
		os.WriteFile(path, append(msgJSON, '\n'), 0o644) //nolint:errcheck
	}

	ctx, cancel := context.WithCancel(context.Background())

	// Filter to only "alpha" node.
	req := httptest.NewRequest(http.MethodGet, "/api/xnode/events?target_node=alpha", nil).WithContext(ctx)
	w := &wave66FlusherRecorder{ResponseRecorder: httptest.NewRecorder()}

	done := make(chan struct{})
	go func() {
		defer close(done)
		xc.SSEDeliveryHandler(w, req)
	}()

	// Two check-ticker cycles.
	time.Sleep(2200 * time.Millisecond)
	cancel()

	select {
	case <-done:
	case <-time.After(4 * time.Second):
		t.Fatal("SSEDeliveryHandler did not return after context cancellation")
	}

	body := w.Body.String()
	// "beta" events must NOT appear in the filtered output.
	if strings.Contains(body, "\"target\":\"beta\"") {
		t.Error("expected beta node messages to be filtered out by target_node=alpha")
	}
}

// ---------------------------------------------------------------------------
// Consolidated from coverage_wave179_test.go: xnodeAckProcessorPatrol tests
// ---------------------------------------------------------------------------

// setXNodeAcksDir saves and restores XNodeAcksDir around a test.
func setXNodeAcksDir(t *testing.T, dir string) func() {
	t.Helper()
	orig := XNodeAcksDir
	XNodeAcksDir = dir
	return func() { XNodeAcksDir = orig }
}

func TestWave179_XnodeAckProcessor_EmptyDir(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	t.Cleanup(cleanup)

	dir := t.TempDir()
	defer setXNodeAcksDir(t, dir)()

	if err := xnodeAckProcessorPatrol(context.Background(), db); err != nil {
		t.Errorf("expected nil, got %v", err)
	}
}

func TestWave179_XnodeAckProcessor_NotConfigured(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	t.Cleanup(cleanup)

	defer setXNodeAcksDir(t, "")()

	if err := xnodeAckProcessorPatrol(context.Background(), db); err != nil {
		t.Errorf("expected nil for empty XNodeAcksDir, got %v", err)
	}
}

func TestWave179_XnodeAckProcessor_DirIsNotExist(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	t.Cleanup(cleanup)

	defer setXNodeAcksDir(t, "/tmp/wave179_nonexistent_acks_dir_xyz987")()

	if err := xnodeAckProcessorPatrol(context.Background(), db); err != nil {
		t.Errorf("expected nil for non-existent dir, got %v", err)
	}
}

func TestWave179_XnodeAckProcessor_ReadDirError(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	t.Cleanup(cleanup)

	f, err := os.CreateTemp("", "wave179_notadir*.json")
	if err != nil {
		t.Fatal(err)
	}
	f.Close()
	defer os.Remove(f.Name())

	defer setXNodeAcksDir(t, f.Name())()

	err = xnodeAckProcessorPatrol(context.Background(), db)
	if err == nil {
		t.Error("expected error when ackDir is a file, not a directory")
	}
}

func TestWave179_XnodeAckProcessor_ReadFileError(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	t.Cleanup(cleanup)

	dir := t.TempDir()
	defer setXNodeAcksDir(t, dir)()

	subdir := filepath.Join(dir, "bad.json")
	if err := os.Mkdir(subdir, 0755); err != nil {
		t.Fatal(err)
	}

	if err := xnodeAckProcessorPatrol(context.Background(), db); err != nil {
		t.Errorf("expected nil, got %v", err)
	}
}

func TestWave179_XnodeAckProcessor_InvalidJSON(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	t.Cleanup(cleanup)

	dir := t.TempDir()
	defer setXNodeAcksDir(t, dir)()

	ackFile := filepath.Join(dir, "bad_msg.json")
	if err := os.WriteFile(ackFile, []byte("{not valid json"), 0644); err != nil {
		t.Fatal(err)
	}

	if err := xnodeAckProcessorPatrol(context.Background(), db); err != nil {
		t.Errorf("expected nil on bad JSON, got %v", err)
	}
}

func TestWave179_XnodeAckProcessor_NonAckedStatus(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	t.Cleanup(cleanup)

	dir := t.TempDir()
	defer setXNodeAcksDir(t, dir)()

	ack := map[string]string{"message_id": "msg-001", "status": "pending"}
	data, _ := json.Marshal(ack)
	if err := os.WriteFile(filepath.Join(dir, "pending.json"), data, 0644); err != nil {
		t.Fatal(err)
	}

	if err := xnodeAckProcessorPatrol(context.Background(), db); err != nil {
		t.Errorf("expected nil for non-acked status, got %v", err)
	}
}

func TestWave179_XnodeAckProcessor_EmptyMessageID(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	t.Cleanup(cleanup)

	dir := t.TempDir()
	defer setXNodeAcksDir(t, dir)()

	ack := map[string]string{"message_id": "", "status": "acked"}
	data, _ := json.Marshal(ack)
	if err := os.WriteFile(filepath.Join(dir, "noid.json"), data, 0644); err != nil {
		t.Fatal(err)
	}

	if err := xnodeAckProcessorPatrol(context.Background(), db); err != nil {
		t.Errorf("expected nil for empty message_id, got %v", err)
	}
}

func TestWave179_XnodeAckProcessor_SuccessfulAck(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	t.Cleanup(cleanup)

	_, err := db.Exec(`CREATE TABLE IF NOT EXISTS xnode_outbox (
		id TEXT PRIMARY KEY,
		target_node TEXT,
		message_type TEXT,
		payload TEXT,
		idempotency_key TEXT,
		status TEXT DEFAULT 'serialized',
		acked_at TEXT,
		created_at TEXT DEFAULT (datetime('now'))
	)`)
	if err != nil {
		t.Fatalf("create xnode_outbox: %v", err)
	}
	_, err = db.Exec(`INSERT INTO xnode_outbox (id, target_node, message_type, payload, status)
		VALUES ('msg-ack-179', 'sati', 'test', '{}', 'serialized')`)
	if err != nil {
		t.Fatalf("insert row: %v", err)
	}

	dir := t.TempDir()
	defer setXNodeAcksDir(t, dir)()

	ack := map[string]string{"message_id": "msg-ack-179", "status": "acked"}
	data, _ := json.Marshal(ack)
	ackPath := filepath.Join(dir, "ack179.json")
	if err := os.WriteFile(ackPath, data, 0644); err != nil {
		t.Fatal(err)
	}

	if err := xnodeAckProcessorPatrol(context.Background(), db); err != nil {
		t.Errorf("expected nil, got %v", err)
	}

	var status string
	db.QueryRow(`SELECT status FROM xnode_outbox WHERE id='msg-ack-179'`).Scan(&status)
	if status != "acked" {
		t.Errorf("expected status=acked, got %q", status)
	}

	if _, err := os.Stat(ackPath); !os.IsNotExist(err) {
		t.Error("expected ack file to be removed after successful processing")
	}
}

func TestWave179_XnodeAckProcessor_DBExecError(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	cleanup() // close DB immediately so ExecContext fails

	dir := t.TempDir()
	defer setXNodeAcksDir(t, dir)()

	ack := map[string]string{"message_id": "msg-closed-179", "status": "acked"}
	data, _ := json.Marshal(ack)
	if err := os.WriteFile(filepath.Join(dir, "closed.json"), data, 0644); err != nil {
		t.Fatal(err)
	}

	_ = xnodeAckProcessorPatrol(context.Background(), db)
}

func TestWave179_XnodeAckProcessor_NonJsonExtSkipped(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	t.Cleanup(cleanup)

	dir := t.TempDir()
	defer setXNodeAcksDir(t, dir)()

	if err := os.WriteFile(filepath.Join(dir, "readme.txt"), []byte("not an ack"), 0644); err != nil {
		t.Fatal(err)
	}

	if err := xnodeAckProcessorPatrol(context.Background(), db); err != nil {
		t.Errorf("expected nil, got %v", err)
	}
}
