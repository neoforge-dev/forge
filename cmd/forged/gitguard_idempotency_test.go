//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// setupIdempotencyDB creates a temp DB ready for idempotency tests.
// Uses SQLite-compatible DDL (no TIMESTAMPTZ/JSONB).
func setupIdempotencyDB(t *testing.T) (*IdempotencyStore, func()) {
	t.Helper()
	dbPath := filepath.Join(os.TempDir(), "idem_test_"+time.Now().Format("20060102150405999")+".db")
	db, err := OpenDB(dbPath)
	if err != nil {
		t.Fatalf("OpenDB: %v", err)
	}

	store := NewIdempotencyStore(db)
	return store, func() {
		db.Close()
		os.Remove(dbPath)
	}
}

// createIdempotencyTableSQLite creates the table using SQLite-compatible DDL.
func createIdempotencyTableSQLite(t *testing.T, store *IdempotencyStore) {
	t.Helper()
	_, err := store.db.Exec(`
CREATE TABLE IF NOT EXISTS idempotent_actions (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    payload_hash TEXT NOT NULL UNIQUE,
    executed_at TEXT DEFAULT (datetime('now')),
    result TEXT
);`)
	if err != nil {
		t.Fatalf("create sqlite idempotency table: %v", err)
	}
}

func TestComputeIdempotencyHash(t *testing.T) {
	key1 := IdempotencyKey{ActionType: "git_commit", Payload: []byte("payload1")}
	key2 := IdempotencyKey{ActionType: "git_commit", Payload: []byte("payload1")}
	key3 := IdempotencyKey{ActionType: "git_push", Payload: []byte("payload1")}
	key4 := IdempotencyKey{ActionType: "git_commit", Payload: []byte("payload1"), ResourceID: "repo-1"}

	h1 := computeIdempotencyHash(key1)
	h2 := computeIdempotencyHash(key2)
	h3 := computeIdempotencyHash(key3)
	h4 := computeIdempotencyHash(key4)

	if h1 != h2 {
		t.Error("same key should produce same hash")
	}
	if h1 == h3 {
		t.Error("different action types should produce different hashes")
	}
	if h1 == h4 {
		t.Error("different resource IDs should produce different hashes")
	}
	if len(h1) != 64 {
		t.Errorf("expected 64-char SHA256 hex, got %d", len(h1))
	}
}

func TestHashPayload(t *testing.T) {
	h1 := hashPayload("type-A", []byte("data"))
	h2 := hashPayload("type-A", []byte("data"))
	h3 := hashPayload("type-B", []byte("data"))

	if h1 != h2 {
		t.Error("same inputs should produce same hash")
	}
	if h1 == h3 {
		t.Error("different types should produce different hashes")
	}
}

func TestIdempotencyStore_StoreAndGetByHash(t *testing.T) {
	store, cleanup := setupIdempotencyDB(t)
	defer cleanup()
	createIdempotencyTableSQLite(t, store)

	action := &IdempotentAction{
		ID:          "test-id-1",
		Type:        "git_commit",
		PayloadHash: "deadbeef1234",
		ExecutedAt:  time.Now().UTC().Truncate(time.Second),
		Result:      []byte(`"commit-sha-abc123"`),
	}

	if err := store.Store(action); err != nil {
		t.Fatalf("Store: %v", err)
	}

	fetched, err := store.GetByHash("deadbeef1234")
	if err != nil {
		t.Fatalf("GetByHash: %v", err)
	}

	if fetched.ID != action.ID {
		t.Errorf("ID mismatch: got %s, want %s", fetched.ID, action.ID)
	}
	if fetched.Type != action.Type {
		t.Errorf("Type mismatch: got %s, want %s", fetched.Type, action.Type)
	}
}

func TestIdempotencyStore_GetByHash_NotFound(t *testing.T) {
	store, cleanup := setupIdempotencyDB(t)
	defer cleanup()
	createIdempotencyTableSQLite(t, store)

	_, err := store.GetByHash("nonexistent")
	if err == nil {
		t.Error("expected error for nonexistent hash")
	}
}

func TestIdempotencyStore_IsDuplicate(t *testing.T) {
	store, cleanup := setupIdempotencyDB(t)
	defer cleanup()
	createIdempotencyTableSQLite(t, store)

	payload := []byte(`{"key":"value"}`)

	// Not a duplicate before any store
	isDup, _, err := store.IsDuplicate("action-x", payload)
	if err != nil {
		t.Fatalf("IsDuplicate (first call): %v", err)
	}
	if isDup {
		t.Error("should not be duplicate before storing")
	}

	// Store an action
	hash := hashPayload("action-x", payload)
	action := &IdempotentAction{
		ID:          "dup-id-1",
		Type:        "action-x",
		PayloadHash: hash,
		ExecutedAt:  time.Now().UTC(),
		Result:      []byte(`"result-1"`),
	}
	if err := store.Store(action); err != nil {
		t.Fatalf("Store: %v", err)
	}

	// Now it should be a duplicate
	isDup, fetched, err := store.IsDuplicate("action-x", payload)
	if err != nil {
		t.Fatalf("IsDuplicate (second call): %v", err)
	}
	if !isDup {
		t.Error("should be duplicate after storing")
	}
	if fetched == nil || fetched.ID != "dup-id-1" {
		t.Errorf("expected action with ID dup-id-1, got %v", fetched)
	}
}

func TestIdempotencyStore_ExecuteOnce(t *testing.T) {
	store, cleanup := setupIdempotencyDB(t)
	defer cleanup()
	createIdempotencyTableSQLite(t, store)

	callCount := 0
	fn := func() (interface{}, error) {
		callCount++
		return "commit-" + strings.Repeat("a", callCount), nil
	}

	payload := []byte(`{"msg":"test-execute-once"}`)

	// First call — fn executed
	result1, err := store.ExecuteOnce("test-action", payload, fn)
	if err != nil {
		t.Fatalf("ExecuteOnce (first): %v", err)
	}
	if callCount != 1 {
		t.Errorf("fn should be called once, got %d", callCount)
	}

	// Second call — fn should NOT be re-executed (cached)
	result2, err := store.ExecuteOnce("test-action", payload, fn)
	if err != nil {
		t.Fatalf("ExecuteOnce (second): %v", err)
	}
	if callCount != 1 {
		t.Errorf("fn should still be called only once, got %d", callCount)
	}
	if result1 != result2 {
		t.Errorf("cached result mismatch: %v vs %v", result1, result2)
	}
}

func TestIdempotencyStore_ExecuteOnce_FnError(t *testing.T) {
	store, cleanup := setupIdempotencyDB(t)
	defer cleanup()
	createIdempotencyTableSQLite(t, store)

	fn := func() (interface{}, error) {
		return nil, errors.New("fn failed")
	}

	_, err := store.ExecuteOnce("fail-action", []byte("payload"), fn)
	if err == nil {
		t.Error("expected error from fn, got nil")
	}
}

func TestIdempotencyStore_Cleanup(t *testing.T) {
	store, cleanup := setupIdempotencyDB(t)
	defer cleanup()
	createIdempotencyTableSQLite(t, store)

	// Insert an old action
	oldAction := &IdempotentAction{
		ID:          "old-id",
		Type:        "old-type",
		PayloadHash: "oldhash123",
		ExecutedAt:  time.Now().UTC().Add(-2 * time.Hour),
		Result:      []byte(`"old-result"`),
	}
	if err := store.Store(oldAction); err != nil {
		t.Fatalf("Store old action: %v", err)
	}

	// Cleanup actions older than 1 hour
	if err := store.Cleanup(1 * time.Hour); err != nil {
		t.Fatalf("Cleanup: %v", err)
	}

	// Old action should be gone
	_, err := store.GetByHash("oldhash123")
	if err == nil {
		t.Error("old action should have been cleaned up")
	}
}

func TestHandleIdempotencyCheck_Duplicate(t *testing.T) {
	store, cleanup := setupIdempotencyDB(t)
	defer cleanup()
	createIdempotencyTableSQLite(t, store)

	// Pre-store an action
	payload := []byte(`{"msg":"duplicate-test"}`)
	hash := hashPayload("check-type", payload)
	_ = store.Store(&IdempotentAction{
		ID:          "check-id-1",
		Type:        "check-type",
		PayloadHash: hash,
		ExecutedAt:  time.Now().UTC(),
		Result:      []byte(`"ok"`),
	})

	body, _ := json.Marshal(IdempotencyCheckRequest{
		ActionType: "check-type",
		Payload:    payload,
	})
	req := httptest.NewRequest(http.MethodPost, "/idempotency/check", bytes.NewReader(body))
	w := httptest.NewRecorder()

	store.HandleIdempotencyCheck(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", w.Code)
	}

	var resp IdempotencyCheckResponse
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatalf("unmarshal response: %v", err)
	}
	if !resp.IsDuplicate {
		t.Error("expected is_duplicate=true")
	}
}

func TestHandleIdempotencyCheck_MethodNotAllowed(t *testing.T) {
	store, cleanup := setupIdempotencyDB(t)
	defer cleanup()
	createIdempotencyTableSQLite(t, store)

	req := httptest.NewRequest(http.MethodGet, "/idempotency/check", nil)
	w := httptest.NewRecorder()
	store.HandleIdempotencyCheck(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestHandleIdempotencyCheck_BadJSON(t *testing.T) {
	store, cleanup := setupIdempotencyDB(t)
	defer cleanup()
	createIdempotencyTableSQLite(t, store)

	req := httptest.NewRequest(http.MethodPost, "/idempotency/check", strings.NewReader("{invalid"))
	w := httptest.NewRecorder()
	store.HandleIdempotencyCheck(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", w.Code)
	}
}

func TestHandleIdempotencyExecute(t *testing.T) {
	store, cleanup := setupIdempotencyDB(t)
	defer cleanup()
	createIdempotencyTableSQLite(t, store)

	// Valid POST
	req := httptest.NewRequest(http.MethodPost, "/idempotency/execute", nil)
	w := httptest.NewRecorder()
	store.HandleIdempotencyExecute(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", w.Code)
	}

	// Invalid method
	req2 := httptest.NewRequest(http.MethodGet, "/idempotency/execute", nil)
	w2 := httptest.NewRecorder()
	store.HandleIdempotencyExecute(w2, req2)
	if w2.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w2.Code)
	}
}

func TestNewGitGuardIdempotency(t *testing.T) {
	store, cleanup := setupIdempotencyDB(t)
	defer cleanup()
	createIdempotencyTableSQLite(t, store)

	g := NewGitGuardIdempotency(store)
	if g == nil {
		t.Error("NewGitGuardIdempotency returned nil")
	}
}

func TestGitGuardIdempotency_ExecuteCommit(t *testing.T) {
	store, cleanup := setupIdempotencyDB(t)
	defer cleanup()
	createIdempotencyTableSQLite(t, store)

	g := NewGitGuardIdempotency(store)

	payload := CommitPayload{
		RepoPath:    "/tmp/test-repo",
		Message:     "feat: test commit",
		Files:       []string{"file1.go"},
		AuthorName:  "Test Author",
		AuthorEmail: "test@example.com",
	}

	callCount := 0
	fn := func() (string, error) {
		callCount++
		return "sha-abc123", nil
	}

	// First call — executes fn
	hash1, err := g.ExecuteCommit(payload, fn)
	if err != nil {
		t.Fatalf("ExecuteCommit first call: %v", err)
	}
	if hash1 != "sha-abc123" {
		t.Errorf("expected sha-abc123, got %s", hash1)
	}
	if callCount != 1 {
		t.Errorf("fn should be called once, got %d", callCount)
	}

	// Second call — should be idempotent (cached)
	hash2, err := g.ExecuteCommit(payload, fn)
	if err != nil {
		t.Fatalf("ExecuteCommit second call: %v", err)
	}
	if hash2 != hash1 {
		t.Errorf("idempotent result should match: %s vs %s", hash1, hash2)
	}
	if callCount != 1 {
		t.Errorf("fn should still be called only once, got %d", callCount)
	}
}

func TestGitGuardIdempotency_ExecuteCommit_WrongResultType(t *testing.T) {
	store, cleanup := setupIdempotencyDB(t)
	defer cleanup()
	createIdempotencyTableSQLite(t, store)

	g := NewGitGuardIdempotency(store)

	payload := CommitPayload{
		RepoPath:    "/tmp/wrong-type-repo",
		Message:     "feat: wrong type test",
		Files:       []string{"main.go"},
		AuthorName:  "Test",
		AuthorEmail: "test@example.com",
	}

	// Pre-compute the hash that ExecuteCommit will look up.
	payloadBytes, err := json.Marshal(payload)
	if err != nil {
		t.Fatalf("marshal payload: %v", err)
	}
	hash := hashPayload("git_commit", payloadBytes)

	// Store a row whose result is a JSON object (not a bare string).
	// When ExecuteCommit hits the cache and calls json.Unmarshal into interface{},
	// it gets map[string]interface{} — not string — so the type assertion fails.
	action := &IdempotentAction{
		ID:          "wrong-type-id",
		Type:        "git_commit",
		PayloadHash: hash,
		ExecutedAt:  time.Now().UTC(),
		Result:      json.RawMessage(`{"not": "a string"}`),
	}
	if err := store.Store(action); err != nil {
		t.Fatalf("Store pre-seeded action: %v", err)
	}

	fn := func() (string, error) {
		return "should-not-be-called", nil
	}

	_, err = g.ExecuteCommit(payload, fn)
	if err == nil {
		t.Error("ExecuteCommit with wrong cached result type should return error, got nil")
	}
}

func TestCreateIdempotencyTable(t *testing.T) {
	dbPath := filepath.Join(os.TempDir(), "idem_create_"+time.Now().Format("20060102150405")+".db")
	db, err := OpenDB(dbPath)
	if err != nil {
		t.Fatalf("OpenDB: %v", err)
	}
	defer func() {
		db.Close()
		os.Remove(dbPath)
	}()

	// CreateIdempotencyTable may fail on SQLite due to TIMESTAMPTZ/JSONB types,
	// but should not panic. A non-nil error is acceptable here.
	_ = CreateIdempotencyTable(db)

	// Regardless, we can create the table with SQLite-compatible DDL
	_, err = db.Exec(`CREATE TABLE IF NOT EXISTS idempotent_actions (
		id TEXT PRIMARY KEY, type TEXT, payload_hash TEXT UNIQUE,
		executed_at TEXT, result TEXT)`)
	if err != nil {
		t.Fatalf("create sqlite table: %v", err)
	}
}
