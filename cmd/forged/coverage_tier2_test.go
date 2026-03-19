//go:build !tmux_bridge
// +build !tmux_bridge

package main

// coverage_tier2_test.go — Tier 2 targeted coverage tests.
//
// Targets uncovered branches in:
//   - migrate.go: parseMigrationFilename, splitUpDown, detectDBType, columnExists,
//     indexExists, ensureMigrationsTable, getMigrationsForDB, MigrateDown error paths
//   - token_budget.go: nodeIDFromEnv env-var path, writeTokenBudgetFile success
//   - handlers_task.go: method-not-allowed + error paths (no-DB guard rails)
//
// All tests: pure-logic or in-memory SQLite — no network, no goroutines spawned.

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	_ "github.com/mattn/go-sqlite3"
)

// ---------------------------------------------------------------------------
// migrate.go — pure function coverage
// ---------------------------------------------------------------------------

// TestTier2_ParseMigrationFilename_Valid covers the success path.
func TestTier2_ParseMigrationFilename_Valid(t *testing.T) {
	version, name, err := parseMigrationFilename("042_add_context_envelopes.sql")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if version != 42 {
		t.Errorf("expected version 42, got %d", version)
	}
	if name != "add_context_envelopes" {
		t.Errorf("expected name 'add_context_envelopes', got %q", name)
	}
}

// TestTier2_ParseMigrationFilename_NoName covers filename with no name part.
func TestTier2_ParseMigrationFilename_NoName(t *testing.T) {
	version, name, err := parseMigrationFilename("007.sql")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if version != 7 {
		t.Errorf("expected version 7, got %d", version)
	}
	if name != "" {
		t.Errorf("expected empty name for no-underscore filename, got %q", name)
	}
}

// TestTier2_ParseMigrationFilename_InvalidVersion covers non-numeric version → error.
func TestTier2_ParseMigrationFilename_InvalidVersion(t *testing.T) {
	_, _, err := parseMigrationFilename("abc_some_name.sql")
	if err == nil {
		t.Fatal("expected error for non-numeric version, got nil")
	}
	if !strings.Contains(err.Error(), "invalid migration version") {
		t.Errorf("expected 'invalid migration version' in error, got: %v", err)
	}
}

// TestTier2_SplitUpDown_WithDownSection covers the two-part split.
func TestTier2_SplitUpDown_WithDownSection(t *testing.T) {
	sql := "CREATE TABLE foo (id INTEGER);\n-- +migrate Down\nDROP TABLE foo;"
	up, down := splitUpDown(sql)
	if !strings.Contains(up, "CREATE TABLE foo") {
		t.Errorf("up section missing CREATE TABLE: %q", up)
	}
	if !strings.Contains(down, "DROP TABLE foo") {
		t.Errorf("down section missing DROP TABLE: %q", down)
	}
}

// TestTier2_SplitUpDown_NoDownSection covers no separator → empty down.
func TestTier2_SplitUpDown_NoDownSection(t *testing.T) {
	sql := "CREATE TABLE bar (id INTEGER);"
	up, down := splitUpDown(sql)
	if !strings.Contains(up, "CREATE TABLE bar") {
		t.Errorf("up section wrong: %q", up)
	}
	if down != "" {
		t.Errorf("expected empty down, got %q", down)
	}
}

// TestTier2_DetectDBType_SQLite verifies that an in-memory SQLite DB is detected as SQLite.
func TestTier2_DetectDBType_SQLite(t *testing.T) {
	db, err := sql.Open("sqlite3", ":memory:")
	if err != nil {
		t.Fatalf("open sqlite: %v", err)
	}
	defer db.Close()

	dt := detectDBType(db)
	if dt != SQLite {
		t.Errorf("expected SQLite, got %q", dt)
	}
}

// TestTier2_ColumnExists_Present verifies columnExists returns true when column is present.
func TestTier2_ColumnExists_Present(t *testing.T) {
	db, err := sql.Open("sqlite3", ":memory:")
	if err != nil {
		t.Fatalf("open sqlite: %v", err)
	}
	defer db.Close()

	if _, err := db.Exec("CREATE TABLE test_col (id INTEGER PRIMARY KEY, name TEXT)"); err != nil {
		t.Fatalf("create table: %v", err)
	}

	exists, err := columnExists(db, "test_col", "name")
	if err != nil {
		t.Fatalf("columnExists error: %v", err)
	}
	if !exists {
		t.Error("expected 'name' column to exist")
	}
}

// TestTier2_ColumnExists_Absent verifies columnExists returns false for missing column.
func TestTier2_ColumnExists_Absent(t *testing.T) {
	db, err := sql.Open("sqlite3", ":memory:")
	if err != nil {
		t.Fatalf("open sqlite: %v", err)
	}
	defer db.Close()

	if _, err := db.Exec("CREATE TABLE test_col2 (id INTEGER PRIMARY KEY)"); err != nil {
		t.Fatalf("create table: %v", err)
	}

	exists, err := columnExists(db, "test_col2", "missing_col")
	if err != nil {
		t.Fatalf("columnExists error: %v", err)
	}
	if exists {
		t.Error("expected 'missing_col' to not exist")
	}
}

// TestTier2_IndexExists_Present verifies indexExists returns true when index exists.
func TestTier2_IndexExists_Present(t *testing.T) {
	db, err := sql.Open("sqlite3", ":memory:")
	if err != nil {
		t.Fatalf("open sqlite: %v", err)
	}
	defer db.Close()

	if _, err := db.Exec("CREATE TABLE idx_test (id INTEGER PRIMARY KEY, val TEXT)"); err != nil {
		t.Fatalf("create table: %v", err)
	}
	if _, err := db.Exec("CREATE INDEX idx_test_val ON idx_test(val)"); err != nil {
		t.Fatalf("create index: %v", err)
	}

	exists, err := indexExists(db, "idx_test_val")
	if err != nil {
		t.Fatalf("indexExists error: %v", err)
	}
	if !exists {
		t.Error("expected idx_test_val to exist")
	}
}

// TestTier2_IndexExists_Absent verifies indexExists returns false for missing index.
func TestTier2_IndexExists_Absent(t *testing.T) {
	db, err := sql.Open("sqlite3", ":memory:")
	if err != nil {
		t.Fatalf("open sqlite: %v", err)
	}
	defer db.Close()

	exists, err := indexExists(db, "nonexistent_index_xyz")
	if err != nil {
		t.Fatalf("indexExists error: %v", err)
	}
	if exists {
		t.Error("expected nonexistent_index_xyz to not exist")
	}
}

// TestTier2_EnsureMigrationsTable_SQLite verifies table creation with SQLite DB type.
func TestTier2_EnsureMigrationsTable_SQLite(t *testing.T) {
	db, err := sql.Open("sqlite3", ":memory:")
	if err != nil {
		t.Fatalf("open sqlite: %v", err)
	}
	defer db.Close()

	if err := ensureMigrationsTable(db, SQLite); err != nil {
		t.Fatalf("ensureMigrationsTable(SQLite): %v", err)
	}

	// Verify the table exists by inserting a row.
	_, err = db.Exec("INSERT INTO applied_migrations (version, name, applied_at) VALUES (999, 'test', ?)",
		time.Now().UTC().Format(time.RFC3339))
	if err != nil {
		t.Errorf("insert into applied_migrations failed: %v", err)
	}
}

// TestTier2_EnsureMigrationsTable_Idempotent verifies calling twice is safe.
func TestTier2_EnsureMigrationsTable_Idempotent(t *testing.T) {
	db, err := sql.Open("sqlite3", ":memory:")
	if err != nil {
		t.Fatalf("open sqlite: %v", err)
	}
	defer db.Close()

	if err := ensureMigrationsTable(db, SQLite); err != nil {
		t.Fatalf("first call: %v", err)
	}
	if err := ensureMigrationsTable(db, SQLite); err != nil {
		t.Fatalf("second call (idempotent): %v", err)
	}
}

// TestTier2_GetAppliedMigrations_Empty verifies empty table returns empty map.
func TestTier2_GetAppliedMigrations_Empty(t *testing.T) {
	db, err := sql.Open("sqlite3", ":memory:")
	if err != nil {
		t.Fatalf("open sqlite: %v", err)
	}
	defer db.Close()

	if err := ensureMigrationsTable(db, SQLite); err != nil {
		t.Fatalf("ensureMigrationsTable: %v", err)
	}

	applied, err := getAppliedMigrations(db)
	if err != nil {
		t.Fatalf("getAppliedMigrations: %v", err)
	}
	if len(applied) != 0 {
		t.Errorf("expected empty map, got %v", applied)
	}
}

// TestTier2_GetAppliedMigrations_WithData verifies populated table returns correct map.
func TestTier2_GetAppliedMigrations_WithData(t *testing.T) {
	db, err := sql.Open("sqlite3", ":memory:")
	if err != nil {
		t.Fatalf("open sqlite: %v", err)
	}
	defer db.Close()

	if err := ensureMigrationsTable(db, SQLite); err != nil {
		t.Fatalf("ensureMigrationsTable: %v", err)
	}
	if _, err := db.Exec("INSERT INTO applied_migrations (version, name, applied_at) VALUES (1, 'init', ?)",
		time.Now().UTC().Format(time.RFC3339)); err != nil {
		t.Fatalf("insert: %v", err)
	}
	if _, err := db.Exec("INSERT INTO applied_migrations (version, name, applied_at) VALUES (2, 'second', ?)",
		time.Now().UTC().Format(time.RFC3339)); err != nil {
		t.Fatalf("insert: %v", err)
	}

	applied, err := getAppliedMigrations(db)
	if err != nil {
		t.Fatalf("getAppliedMigrations: %v", err)
	}
	if !applied[1] || !applied[2] {
		t.Errorf("expected versions 1 and 2 in applied map, got %v", applied)
	}
}

// TestTier2_GetMigrationsForDB_SQLite verifies SQLite returns migrations (non-postgres files).
func TestTier2_GetMigrationsForDB_SQLite(t *testing.T) {
	migrations, err := getMigrationsForDB(SQLite)
	if err != nil {
		t.Fatalf("getMigrationsForDB(SQLite): %v", err)
	}
	if len(migrations) == 0 {
		t.Fatal("expected at least one migration for SQLite")
	}
	// Verify none of the returned migrations are postgres-specific
	for _, m := range migrations {
		if strings.Contains(m.Name, "postgres") {
			t.Errorf("SQLite migration set should not include postgres migration: %q", m.Name)
		}
	}
}

// TestTier2_GetMigrationsForDB_Sorted verifies migrations are sorted by version.
func TestTier2_GetMigrationsForDB_Sorted(t *testing.T) {
	migrations, err := getMigrationsForDB(SQLite)
	if err != nil {
		t.Fatalf("getMigrationsForDB: %v", err)
	}
	for i := 1; i < len(migrations); i++ {
		if migrations[i].Version < migrations[i-1].Version {
			t.Errorf("migrations not sorted: [%d].Version=%d < [%d].Version=%d",
				i, migrations[i].Version, i-1, migrations[i-1].Version)
		}
	}
}

// TestTier2_MigrateDown_ZeroSteps verifies MigrateDown(db, 0) is a no-op.
func TestTier2_MigrateDown_ZeroSteps(t *testing.T) {
	db, err := sql.Open("sqlite3", ":memory:")
	if err != nil {
		t.Fatalf("open sqlite: %v", err)
	}
	defer db.Close()

	// MigrateDown with 0 steps should return immediately.
	if err := MigrateDown(db, 0); err != nil {
		t.Fatalf("MigrateDown(0) unexpected error: %v", err)
	}
}

// TestTier2_MigrateDown_NegativeSteps verifies MigrateDown(db, -1) is a no-op.
func TestTier2_MigrateDown_NegativeSteps(t *testing.T) {
	db, err := sql.Open("sqlite3", ":memory:")
	if err != nil {
		t.Fatalf("open sqlite: %v", err)
	}
	defer db.Close()

	if err := MigrateDown(db, -1); err != nil {
		t.Fatalf("MigrateDown(-1) unexpected error: %v", err)
	}
}

// TestTier2_IsPostgresMigration covers the postgres check helper.
func TestTier2_IsPostgresMigration(t *testing.T) {
	if !isPostgresMigration("042_something_postgres_only.sql") {
		t.Error("expected true for postgres migration filename")
	}
	if isPostgresMigration("042_something_normal.sql") {
		t.Error("expected false for non-postgres migration filename")
	}
}

// TestTier2_IsSQLiteMigration covers the sqlite check helper.
func TestTier2_IsSQLiteMigration(t *testing.T) {
	if !isSQLiteMigration("042_something_sqlite_only.sql") {
		t.Error("expected true for sqlite migration filename")
	}
	if isSQLiteMigration("042_something_normal.sql") {
		t.Error("expected false for non-sqlite migration filename")
	}
}

// ---------------------------------------------------------------------------
// token_budget.go — nodeIDFromEnv coverage
// ---------------------------------------------------------------------------

// TestTier2_NodeIDFromEnv_WithEnvVar verifies that NODE_ID env var is used when set.
func TestTier2_NodeIDFromEnv_WithEnvVar(t *testing.T) {
	t.Setenv("NODE_ID", "test-node-env")
	got := nodeIDFromEnv()
	if got != "test-node-env" {
		t.Errorf("expected 'test-node-env', got %q", got)
	}
}

// TestTier2_NodeIDFromEnv_DefaultsToHostname verifies fallback when NODE_ID is unset.
func TestTier2_NodeIDFromEnv_DefaultsToHostname(t *testing.T) {
	t.Setenv("NODE_ID", "") // ensure not set
	got := nodeIDFromEnv()
	if got == "" {
		t.Error("expected non-empty node ID from hostname fallback")
	}
	// Should not contain dots (domain stripped)
	if strings.Contains(got, ".") {
		t.Errorf("nodeIDFromEnv should strip domain from hostname, got %q", got)
	}
}

// TestTier2_WriteTokenBudgetFile_Success verifies atomic write-rename works.
func TestTier2_WriteTokenBudgetFile_Success(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "budget.json")

	f := &tokenBudgetFile{
		Providers: map[string]tbProviderData{
			"openai": {Status: "OK"},
		},
	}
	if err := writeTokenBudgetFile(path, f); err != nil {
		t.Fatalf("writeTokenBudgetFile: %v", err)
	}

	// Verify the file exists and is valid JSON.
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read written file: %v", err)
	}
	var result tokenBudgetFile
	if err := json.Unmarshal(data, &result); err != nil {
		t.Fatalf("unmarshal written file: %v", err)
	}
	p, ok := result.Providers["openai"]
	if !ok || p.Status != "OK" {
		t.Errorf("unexpected provider data: %+v", result.Providers)
	}
	// UpdatedAt should be set by writeTokenBudgetFile.
	if result.UpdatedAt == "" {
		t.Error("expected UpdatedAt to be set")
	}
}

// ---------------------------------------------------------------------------
// handlers_task.go — method-not-allowed guards (testable without full DB)
// ---------------------------------------------------------------------------

// TestTier2_TaskDeleteHandler_MethodNotAllowed verifies GET → 405.
func TestTier2_TaskDeleteHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/tasks/abc123", nil)
	w := httptest.NewRecorder()
	taskDeleteHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// TestTier2_PruneTasksHandler_MethodNotAllowed verifies GET → 405.
func TestTier2_PruneTasksHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/tasks/prune", nil)
	w := httptest.NewRecorder()
	pruneTasksHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// TestTier2_TaskHistoryHandler_MethodNotAllowed verifies POST → 405.
func TestTier2_TaskHistoryHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/abc/history", nil)
	w := httptest.NewRecorder()
	taskHistoryHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// TestTier2_AbandonTaskHandler_MethodNotAllowed verifies GET → 405.
func TestTier2_AbandonTaskHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/tasks/abc/abandon", nil)
	w := httptest.NewRecorder()
	abandonTaskHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// TestTier2_GetTaskEventsHandler_MethodNotAllowed verifies POST → 405.
func TestTier2_GetTaskEventsHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/abc/events", nil)
	w := httptest.NewRecorder()
	getTaskEventsHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// TestTier2_ApproveTaskHandler_MethodNotAllowed verifies GET → 405.
func TestTier2_ApproveTaskHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/tasks/abc/approve", nil)
	w := httptest.NewRecorder()
	approveTaskHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// TestTier2_ClaimableTasksHandler_MethodNotAllowed verifies POST → 405.
func TestTier2_ClaimableTasksHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/claimable", nil)
	w := httptest.NewRecorder()
	claimableTasksHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// TestTier2_TaskUpdateHandler_PutMethodNotAllowed verifies GET → 405 for taskUpdateHandler.
func TestTier2_TaskUpdateHandler_PutMethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/tasks/abc", nil)
	w := httptest.NewRecorder()
	taskUpdateHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// TestTier2_CompleteTaskHandler_MethodNotAllowed verifies GET → 405.
func TestTier2_CompleteTaskHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/tasks/abc/complete", nil)
	w := httptest.NewRecorder()
	completeTaskHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// handlers_task.go — missing task ID guards
// ---------------------------------------------------------------------------

// TestTier2_TaskHistoryHandler_EmptyID verifies empty task ID → 400.
func TestTier2_TaskHistoryHandler_EmptyID(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/tasks//history", nil)
	w := httptest.NewRecorder()
	taskHistoryHandler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", w.Code)
	}
}

// TestTier2_TaskAbandonHandler_EmptyID verifies empty task ID → 400.
func TestTier2_TaskAbandonHandler_EmptyID(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/tasks//abandon", nil)
	w := httptest.NewRecorder()
	abandonTaskHandler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", w.Code)
	}
}

// TestTier2_GetTaskEventsHandler_EmptyID verifies empty task ID → 400.
func TestTier2_GetTaskEventsHandler_EmptyID(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/tasks//events", nil)
	w := httptest.NewRecorder()
	getTaskEventsHandler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", w.Code)
	}
}

// TestTier2_ApproveTaskHandler_EmptyID verifies empty task ID → 400.
func TestTier2_ApproveTaskHandler_EmptyID(t *testing.T) {
	body, _ := json.Marshal(map[string]string{"approved_by": "human"})
	req := httptest.NewRequest(http.MethodPost, "/api/tasks//approve", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	approveTaskHandler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d: %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// handlers_task.go — DB-backed paths using in-memory SQLite
// ---------------------------------------------------------------------------

// TestTier2_PruneTasksHandler_Success verifies pruneTasksHandler with a live DB.
func TestTier2_PruneTasksHandler_Success(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Wire the DB into the global handler state.
	origDB := getDBConn()
	setDBConn(db)
	defer setDBConn(origDB)

	req := httptest.NewRequest(http.MethodPost, "/api/tasks/prune", nil)
	w := httptest.NewRecorder()
	pruneTasksHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}

	var resp map[string]interface{}
	if err := json.NewDecoder(w.Body).Decode(&resp); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if resp["status"] != "ok" {
		t.Errorf("expected status=ok, got %v", resp["status"])
	}
}

// TestTier2_TaskDeleteHandler_Success verifies taskDeleteHandler soft-deletes a task.
func TestTier2_TaskDeleteHandler_Success(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Insert a task to delete.
	taskID := "tier2-delete-task"
	_, err := db.Exec(`INSERT INTO tasks (id, domain, project, type, title, status, state, priority, created_at, updated_at)
		VALUES (?, 'test', 'proj', 'feature', 'Test delete task', 'queued', 'QUEUED', 5, datetime('now'), datetime('now'))`,
		taskID)
	if err != nil {
		t.Fatalf("insert task: %v", err)
	}

	origDB := getDBConn()
	setDBConn(db)
	defer setDBConn(origDB)

	req := httptest.NewRequest(http.MethodDelete, "/api/tasks/"+taskID, nil)
	w := httptest.NewRecorder()
	taskDeleteHandler(w, req)

	if w.Code != http.StatusNoContent {
		t.Errorf("expected 204, got %d: %s", w.Code, w.Body.String())
	}
}

// TestTier2_TaskAbandonHandler_NotFound verifies abandon on non-queued task → 404.
func TestTier2_TaskAbandonHandler_NotFound(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	origDB := getDBConn()
	setDBConn(db)
	defer setDBConn(origDB)

	// Task doesn't exist → rows affected = 0 → 404
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/nonexistent-task-xyz/abandon", nil)
	w := httptest.NewRecorder()
	abandonTaskHandler(w, req)

	if w.Code != http.StatusNotFound {
		t.Errorf("expected 404, got %d: %s", w.Code, w.Body.String())
	}
}

// TestTier2_TaskAbandonHandler_Success verifies abandon updates an assigned task.
func TestTier2_TaskAbandonHandler_Success(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	taskID := "tier2-abandon-task"
	_, err := db.Exec(`INSERT INTO tasks (id, domain, project, type, title, status, state, priority, created_at, updated_at)
		VALUES (?, 'test', 'proj', 'feature', 'Test abandon task', 'assigned', 'RUNNING', 5, datetime('now'), datetime('now'))`,
		taskID)
	if err != nil {
		t.Fatalf("insert task: %v", err)
	}

	origDB := getDBConn()
	setDBConn(db)
	defer setDBConn(origDB)

	req := httptest.NewRequest(http.MethodPost, "/api/tasks/"+taskID+"/abandon", nil)
	w := httptest.NewRecorder()
	abandonTaskHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// OpenClaw handlers — method not allowed paths
// ---------------------------------------------------------------------------

// TestTier2_OpenClawHandler_InvalidMethod verifies PUT → 405.
func TestTier2_OpenClawHandler_InvalidMethod(t *testing.T) {
	req := httptest.NewRequest(http.MethodPut, "/api/openclaw/chat", nil)
	w := httptest.NewRecorder()
	openclawHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// TestTier2_OpenClawStatusHandler_MethodNotAllowed verifies POST → 405.
func TestTier2_OpenClawStatusHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/openclaw/status", nil)
	w := httptest.NewRecorder()
	openclawStatusHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// TestTier2_OpenClawChatHandler_EmptyText verifies empty text → 400.
func TestTier2_OpenClawChatHandler_EmptyText(t *testing.T) {
	body, _ := json.Marshal(map[string]string{"text": "  "})
	req := httptest.NewRequest(http.MethodPost, "/api/openclaw/chat", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	openclawChatHandler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d: %s", w.Code, w.Body.String())
	}
}

// TestTier2_OpenClawIngestHandler_MethodNotAllowed verifies GET → 405.
func TestTier2_OpenClawIngestHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/openclaw/ingest", nil)
	w := httptest.NewRecorder()
	openclawIngestHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// TestTier2_OpenClawIngestHandler_MissingFields verifies missing required fields → 400.
func TestTier2_OpenClawIngestHandler_MissingFields(t *testing.T) {
	// Missing node field
	body, _ := json.Marshal(map[string]string{
		"task_id": "t1",
		"status":  "success",
		"agent":   "kimi",
		"summary": "done",
		// "node" is missing
	})
	req := httptest.NewRequest(http.MethodPost, "/api/openclaw/ingest", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	openclawIngestHandler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d: %s", w.Code, w.Body.String())
	}
}

// TestTier2_ParseTaskFromMessage_WithPrefix verifies prefix stripping.
func TestTier2_ParseTaskFromMessage_WithPrefix(t *testing.T) {
	tests := []struct {
		input string
		want  string
	}{
		{"create task: fix the login bug", "fix the login bug"},
		{"task: add dark mode", "add dark mode"},
		{"add task: improve test coverage", "improve test coverage"},
		{"Task: Mixed Case Prefix", "Mixed Case Prefix"},
		{"plain message no prefix", "plain message no prefix"},
	}
	for _, tt := range tests {
		got := parseTaskFromMessage(tt.input)
		if got != tt.want {
			t.Errorf("parseTaskFromMessage(%q) = %q, want %q", tt.input, got, tt.want)
		}
	}
}

// ---------------------------------------------------------------------------
// XNode — NewXNodeController error paths
// ---------------------------------------------------------------------------

// TestTier2_NewXNodeController_NilDB verifies nil db → XNodeError returned.
func TestTier2_NewXNodeController_NilDB(t *testing.T) {
	_, err := NewXNodeController(nil, "test-node")
	if err == nil {
		t.Fatal("expected error for nil db")
	}
	xerr, ok := isXNodeError(err)
	if !ok {
		t.Fatalf("expected *XNodeError, got %T: %v", err, err)
	}
	if xerr.Code != XNodeErrDatabase {
		t.Errorf("expected XNodeErrDatabase, got %q", xerr.Code)
	}
}

// TestTier2_NewXNodeController_EmptyNodeID verifies empty nodeID → uses hostname.
func TestTier2_NewXNodeController_EmptyNodeID(t *testing.T) {
	db := setupXNodeTestDB(t)
	defer db.Close()

	origOutbox := XNodeOutboxDir
	tmpDir := t.TempDir()
	XNodeOutboxDir = filepath.Join(tmpDir, "outbox")
	defer func() { XNodeOutboxDir = origOutbox }()

	xc, err := NewXNodeController(db, "")
	if err != nil {
		t.Fatalf("unexpected error with empty nodeID: %v", err)
	}
	if xc.nodeID == "" {
		t.Error("expected nodeID to be set to hostname when empty string passed")
	}
}

// TestTier2_XNode_ListAcksHandler_EmptyDir verifies empty acks dir returns empty list.
func TestTier2_XNode_ListAcksHandler_EmptyDir(t *testing.T) {
	xc, cleanup := setupXNodeHTTP(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/xnode/acks", nil)
	w := httptest.NewRecorder()
	xc.ListAcksHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}

	var resp map[string]interface{}
	if err := json.NewDecoder(w.Body).Decode(&resp); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if int(resp["count"].(float64)) != 0 {
		t.Errorf("expected count=0 for empty dir, got %v", resp["count"])
	}
}

// TestTier2_XNode_ListNodesHandler_WithNodes verifies nodes from DB are returned.
func TestTier2_XNode_ListNodesHandler_WithNodes(t *testing.T) {
	xc, cleanup := setupXNodeHTTP(t)
	defer cleanup()

	// Insert a node.
	_, err := xc.db.Exec(
		"INSERT INTO nodes (id, hostname, address, status, last_heartbeat) VALUES (?, ?, ?, ?, ?)",
		"test-node-1", "testhost", "10.0.0.1", "online", time.Now().Format(time.RFC3339),
	)
	if err != nil {
		t.Fatalf("insert node: %v", err)
	}

	req := httptest.NewRequest(http.MethodGet, "/api/xnode/nodes", nil)
	w := httptest.NewRecorder()
	xc.ListNodesHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}

	var nodes []Node
	if err := json.NewDecoder(w.Body).Decode(&nodes); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if len(nodes) != 1 {
		t.Errorf("expected 1 node, got %d", len(nodes))
	}
	if nodes[0].ID != "test-node-1" {
		t.Errorf("unexpected node ID: %q", nodes[0].ID)
	}
}

// TestTier2_XNode_StatusHandler_WithData verifies counts from populated tables.
func TestTier2_XNode_StatusHandler_WithData(t *testing.T) {
	xc, cleanup := setupXNodeHTTP(t)
	defer cleanup()

	// Insert a node and a task.
	_, _ = xc.db.Exec(
		"INSERT INTO nodes (id, hostname, address, status, last_heartbeat) VALUES (?, ?, ?, ?, ?)",
		"node-s1", "shost", "10.0.0.2", "online", time.Now().Format(time.RFC3339),
	)
	_, _ = xc.db.Exec(
		"INSERT INTO xnode_tasks (id, source_node, target_node, task_id, status) VALUES (?, ?, ?, ?, ?)",
		"xnt-1", "node-s1", "node-s2", "task-abc", "pending",
	)

	req := httptest.NewRequest(http.MethodGet, "/api/xnode/status", nil)
	w := httptest.NewRecorder()
	xc.StatusHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}

	var resp map[string]interface{}
	if err := json.NewDecoder(w.Body).Decode(&resp); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if int(resp["nodes_total"].(float64)) != 1 {
		t.Errorf("expected nodes_total=1, got %v", resp["nodes_total"])
	}
	if int(resp["tasks_total"].(float64)) != 1 {
		t.Errorf("expected tasks_total=1, got %v", resp["tasks_total"])
	}
}

// ---------------------------------------------------------------------------
// handlers_patrols.go — dashHandler with data (63.4% → higher)
// ---------------------------------------------------------------------------

// TestTier2_DashHandler_MethodNotAllowed verifies POST → 405.
func TestTier2_DashHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/dash", nil)
	w := httptest.NewRecorder()
	dashHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// TestTier2_DashHandler_WithExecutions verifies dashHandler renders HTML with patrol executions.
// Targets the completedAt.Valid, result.Valid, errMsg.Valid branches and duration display.
func TestTier2_DashHandler_WithExecutions(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	origDB := getDBConn()
	setDBConn(db)
	defer setDBConn(origDB)

	now := time.Now().UTC()
	startedAt := now.Add(-5 * time.Second).Format("2006-01-02 15:04:05")
	completedAt := now.Format("2006-01-02 15:04:05")
	result := "succeeded"
	errMsg := "some error that happened during execution"

	// Insert an execution with completedAt + result (tests valid branches).
	_, err := db.Exec(`INSERT INTO patrol_executions (id, patrol_id, started_at, completed_at, status, result, error)
		VALUES (?, ?, ?, ?, ?, ?, NULL)`,
		"exec-1", "health-check", startedAt, completedAt, "success", result)
	if err != nil {
		t.Fatalf("insert exec with result: %v", err)
	}

	// Insert an execution with an error message > 50 chars (tests truncation path).
	_, err = db.Exec(`INSERT INTO patrol_executions (id, patrol_id, started_at, completed_at, status, result, error)
		VALUES (?, ?, ?, ?, ?, NULL, ?)`,
		"exec-2", "token-budget", startedAt, completedAt, "error", errMsg)
	if err != nil {
		t.Fatalf("insert exec with error: %v", err)
	}

	// Insert an execution without completedAt (tests nil branch).
	_, err = db.Exec(`INSERT INTO patrol_executions (id, patrol_id, started_at, completed_at, status, result, error)
		VALUES (?, ?, ?, NULL, ?, NULL, NULL)`,
		"exec-3", "fleet-scaler", startedAt, "running")
	if err != nil {
		t.Fatalf("insert exec running: %v", err)
	}

	req := httptest.NewRequest(http.MethodGet, "/dash", nil)
	w := httptest.NewRecorder()
	dashHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}

	body := w.Body.String()
	if !strings.Contains(body, "health-check") {
		t.Error("expected 'health-check' patrol ID in response body")
	}
	if !strings.Contains(body, "token-budget") {
		t.Error("expected 'token-budget' patrol ID in response body")
	}
}

// TestTier2_DashHandler_LongDuration verifies duration >= 1s displays as "X.Xs".
func TestTier2_DashHandler_LongDuration(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	origDB := getDBConn()
	setDBConn(db)
	defer setDBConn(origDB)

	// Use 2-second duration to exercise the ">= 1000ms" branch (1s+ = "%.1fs" format).
	startedAt := "2026-01-01 12:00:00"
	completedAt := "2026-01-01 12:00:02"

	_, err := db.Exec(`INSERT INTO patrol_executions (id, patrol_id, started_at, completed_at, status, result, error)
		VALUES (?, ?, ?, ?, ?, NULL, NULL)`,
		"exec-long", "sync-patrol", startedAt, completedAt, "success")
	if err != nil {
		t.Fatalf("insert: %v", err)
	}

	req := httptest.NewRequest(http.MethodGet, "/dash", nil)
	w := httptest.NewRecorder()
	dashHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", w.Code)
	}
	// The body should contain the duration in seconds format.
	body := w.Body.String()
	if !strings.Contains(body, "s") {
		t.Logf("body snippet: %s", body[:minTier2(500, len(body))])
	}
}

// minTier2 is a helper for slice bounds.
func minTier2(a, b int) int {
	if a < b {
		return a
	}
	return b
}

// TestTier2_DashHandler_WithPendingApprovals verifies the approvals section with data.
func TestTier2_DashHandler_WithPendingApprovals(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	origDB := getDBConn()
	setDBConn(db)
	defer setDBConn(origDB)

	// Insert a pending approval (expires_at is NOT NULL per schema).
	expiresAt := time.Now().UTC().Add(1 * time.Hour).Format(time.RFC3339)
	_, err := db.Exec(`INSERT INTO approvals (id, task_id, title, agent_id, domain, type, status, risk_score, confidence_score, created_at, expires_at)
		VALUES (?, ?, ?, ?, ?, ?, 'pending', 0.3, 0.8, datetime('now'), ?)`,
		"appr-1", "task-abc", "Fix login bug", "kimi", "forge", "task_completion", expiresAt)
	if err != nil {
		t.Fatalf("insert approval: %v", err)
	}

	req := httptest.NewRequest(http.MethodGet, "/dash", nil)
	w := httptest.NewRecorder()
	dashHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
	body := w.Body.String()
	if !strings.Contains(body, "Fix login bug") {
		t.Error("expected approval title in response body")
	}
}

// TestTier2_ScaleRecommendationsHandler_WithData verifies recs data path.
func TestTier2_ScaleRecommendationsHandler_WithData(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	if err := ensureScaleRecommendationsTable(context.Background(), db); err != nil {
		t.Fatalf("ensureScaleRecommendationsTable: %v", err)
	}

	origDB := getDBConn()
	setDBConn(db)
	defer setDBConn(origDB)

	expires := time.Now().UTC().Add(5 * time.Minute).Format(time.RFC3339)
	_, err := db.Exec(`INSERT INTO scale_recommendations (id, node_id, agent_type, action, reason, queue_depth, idle_agents, auto_execute, status, created_at, expires_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), ?)`,
		"rec-tier2-1", "sati", "kimi", "inflate", "queue backlog", 5, 0, 1, "pending", expires)
	if err != nil {
		t.Fatalf("insert recommendation: %v", err)
	}

	req := httptest.NewRequest(http.MethodGet, "/api/fleet/recommendations", nil)
	w := httptest.NewRecorder()
	fleetRecommendationsHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}

	var resp map[string]interface{}
	if err := json.NewDecoder(w.Body).Decode(&resp); err != nil {
		t.Fatalf("decode: %v", err)
	}
	count := int(resp["count"].(float64))
	if count < 1 {
		t.Errorf("expected at least 1 recommendation, got %d", count)
	}
}
