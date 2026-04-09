//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
	"time"
)

// ---------------------------------------------------------------------------
// Backward-compatibility aliases rescued from deleted wave/coverage test files.
// These helpers are referenced by handler_method_validation_test.go and other
// canonical test files that survived the wave/coverage cleanup.
// ---------------------------------------------------------------------------

// setupWave5 is an alias for setupHandlerTest kept for backward compatibility
// with handler_method_validation_test.go (wave5–wave14 era tests).
func setupWave5(t *testing.T) func() {
	t.Helper()
	return setupHandlerTest(t)
}

// newTestApprovalHandler creates an ApprovalHandler wired to a migrated test DB.
// Rescued from coverage_wave143_test.go.
func newTestApprovalHandler(t *testing.T) (*ApprovalHandler, func()) {
	t.Helper()
	db, cleanup := setupClaimTestDB(t)
	setDBConn(db)
	store := NewApprovalStore(db)
	service := NewApprovalService(store)
	return NewApprovalHandler(service), func() {
		setDBConn(nil)
		cleanup()
	}
}

// setupApprovalHandler creates an ApprovalHandler wired to a migrated test DB.
// Rescued from coverage_wave45_test.go.
func setupApprovalHandler(t *testing.T) (*ApprovalHandler, func()) {
	t.Helper()
	db, cleanup := setupClaimTestDB(t)
	store := NewApprovalStore(db)
	svc := NewApprovalService(store)
	h := NewApprovalHandler(svc)
	return h, cleanup
}

// newTestAuthManager creates a minimal AuthManager for use in handler tests.
// Rescued from coverage_wave144_test.go.
func newTestAuthManager() *AuthManager {
	return NewAuthManager("local")
}

// newTestAuthManagerWave144 is an alias for newTestAuthManager kept for
// backward compatibility with wave154-era tests.
// Rescued from coverage_wave154_test.go.
func newTestAuthManagerWave144() *AuthManager {
	return NewAuthManager("local")
}

// newTestDashboard creates a Dashboard wired to a migrated in-memory-like SQLite DB.
// Rescued from coverage_wave4_test.go.
func newTestDashboard(t *testing.T) (*Dashboard, func()) {
	t.Helper()
	cleanup := setupHandlerTest(t)
	db := getDBConn()
	d := NewDashboard(db, hub)
	return d, cleanup
}

// setupCoverageQueue creates a fully-wired test environment with DB, queue, hub, and state machine.
// Rescued from coverage_test.go.
func setupCoverageQueue(t *testing.T) func() {
	t.Helper()
	dbPath := fmt.Sprintf("/tmp/test_coverage_%d.db", time.Now().UnixNano())
	db, err := OpenDB(dbPath)
	if err != nil {
		t.Fatalf("setupCoverageQueue OpenDB: %v", err)
	}
	if err := MigrateUp(db); err != nil {
		db.Close()
		t.Fatalf("setupCoverageQueue MigrateUp: %v", err)
	}

	oldDB := getDBConn()
	setDBConn(db)

	oldQueue := taskQueue
	q, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("setupCoverageQueue NewTaskQueueFromDB: %v", err)
	}
	taskQueue = q

	oldHub := hub
	hub = NewWebSocketHub()

	oldSM := stateMachine
	stateMachine = NewStateMachine(NewTaskStore(db), db)

	return func() {
		db.Close()
		os.Remove(dbPath)
		setDBConn(oldDB)
		taskQueue = oldQueue
		hub = oldHub
		stateMachine = oldSM
	}
}

// setupHandoffTest creates a test DB with the handoffs table migrated.
// Rescued from coverage_wave15_test.go.
func setupHandoffTest(t *testing.T) (store HandoffStore, service *HandoffService, cleanup func()) {
	t.Helper()
	dbPath := "/tmp/test_handoffs_" + time.Now().Format("20060102150405.999999999") + ".db"

	db, err := OpenDB(dbPath)
	if err != nil {
		t.Fatalf("failed to open db: %v", err)
	}
	if err := MigrateUp(db); err != nil {
		db.Close()
		os.Remove(dbPath)
		t.Fatalf("failed to migrate: %v", err)
	}

	store = NewHandoffStore(db)
	service = NewHandoffService(store)
	cleanup = func() {
		db.Close()
		os.Remove(dbPath)
	}
	return store, service, cleanup
}

// ---------------------------------------------------------------------------
// Handler utility tests
// ---------------------------------------------------------------------------

// TestWriteJSON tests writeJSON sets correct Content-Type and encodes JSON.
func TestWriteJSON(t *testing.T) {
	w := httptest.NewRecorder()

	type TestData struct {
		Name  string `json:"name"`
		Value int    `json:"value"`
	}

	writeJSON(w, TestData{Name: "test", Value: 42})

	if w.Header().Get("Content-Type") != "application/json" {
		t.Errorf("Content-Type = %q, want %q", w.Header().Get("Content-Type"), "application/json")
	}

	body := w.Body.String()
	if !strings.Contains(body, `"name":"test"`) || !strings.Contains(body, `"value":42`) {
		t.Errorf("Body = %q, expected JSON with name and value", body)
	}
}

// TestRequireMethod tests requireMethod validates HTTP methods correctly.
func TestRequireMethod(t *testing.T) {
	// Test POST request with POST required - should pass
	req := httptest.NewRequest(http.MethodPost, "/test", nil)
	w := httptest.NewRecorder()

	result := requireMethod(w, req, http.MethodPost)
	if result != true {
		t.Error("requireMethod returned false for correct POST method")
	}

	// Test GET request with POST required - should fail with 405
	req = httptest.NewRequest(http.MethodGet, "/test", nil)
	w = httptest.NewRecorder()

	result = requireMethod(w, req, http.MethodPost)
	if result != false {
		t.Error("requireMethod returned true for incorrect GET method")
	}

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("StatusCode = %d, want %d", w.Code, http.StatusMethodNotAllowed)
	}
}

// TestWithDBNilSignature verifies withDB exists with the correct function signature.
func TestWithDBNilSignature(t *testing.T) {
	_ = withDB
}
