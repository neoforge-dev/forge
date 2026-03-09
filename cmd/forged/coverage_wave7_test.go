//go:build !tmux_bridge
// +build !tmux_bridge

// coverage_wave7_test.go — Coverage wave 7: output, patrol setters, migrate, handler_helpers, gitguard
// Target: +2pp from ~56% to ~58%
package main

import (
	"bytes"
	"context"
	"database/sql"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// --- output.go: Formatter Name() methods ---

func TestOutputFormatters_Name(t *testing.T) {
	jf := JSONFormatter{}
	if jf.Name() != OutputFormatJSON {
		t.Error("JSONFormatter.Name() mismatch")
	}
	yf := YAMLFormatter{}
	if yf.Name() != OutputFormatYAML {
		t.Error("YAMLFormatter.Name() mismatch")
	}
	tf := TableFormatter{}
	if tf.Name() != OutputFormatTable {
		t.Error("TableFormatter.Name() mismatch")
	}
	pf := PlainFormatter{}
	if pf.Name() != OutputFormatPlain {
		t.Error("PlainFormatter.Name() mismatch")
	}
}

func TestWriteOutput_JSON(t *testing.T) {
	var buf bytes.Buffer
	data := map[string]string{"key": "value"}
	if err := WriteOutput(&buf, OutputFormatJSON, data); err != nil {
		t.Fatalf("WriteOutput JSON: %v", err)
	}
	if !strings.Contains(buf.String(), "\"key\"") {
		t.Errorf("expected JSON with key, got: %s", buf.String())
	}
}

func TestWriteOutput_YAML(t *testing.T) {
	var buf bytes.Buffer
	data := map[string]string{"foo": "bar"}
	if err := WriteOutput(&buf, OutputFormatYAML, data); err != nil {
		t.Fatalf("WriteOutput YAML: %v", err)
	}
	if !strings.Contains(buf.String(), "foo") {
		t.Errorf("expected YAML with foo, got: %s", buf.String())
	}
}

func TestWriteOutput_Plain_String(t *testing.T) {
	var buf bytes.Buffer
	if err := WriteOutput(&buf, OutputFormatPlain, "hello world"); err != nil {
		t.Fatalf("WriteOutput plain string: %v", err)
	}
	if !strings.Contains(buf.String(), "hello world") {
		t.Errorf("expected 'hello world', got: %s", buf.String())
	}
}

func TestWriteOutput_Plain_Nil(t *testing.T) {
	var buf bytes.Buffer
	if err := WriteOutput(&buf, OutputFormatPlain, nil); err != nil {
		t.Fatalf("WriteOutput plain nil: %v", err)
	}
	if !strings.Contains(buf.String(), "(no data)") {
		t.Errorf("expected '(no data)', got: %s", buf.String())
	}
}

func TestWriteOutput_Plain_Bytes(t *testing.T) {
	var buf bytes.Buffer
	if err := WriteOutput(&buf, OutputFormatPlain, []byte("raw bytes")); err != nil {
		t.Fatalf("WriteOutput plain bytes: %v", err)
	}
	if !strings.Contains(buf.String(), "raw bytes") {
		t.Errorf("expected 'raw bytes', got: %s", buf.String())
	}
}

func TestWriteOutputWithFlag(t *testing.T) {
	var buf bytes.Buffer
	data := map[string]int{"count": 3}
	if err := WriteOutputWithFlag(&buf, "json", data); err != nil {
		t.Fatalf("WriteOutputWithFlag: %v", err)
	}
	if !strings.Contains(buf.String(), "count") {
		t.Errorf("expected JSON output, got: %s", buf.String())
	}
}

// --- migrate.go: convertToPostgresSQL ---

func TestConvertToPostgresSQL_Empty(t *testing.T) {
	result := convertToPostgresSQL("")
	if result != "" {
		t.Errorf("expected empty string, got %q", result)
	}
}

func TestConvertToPostgresSQL_DatetimeNow(t *testing.T) {
	sql := "INSERT INTO tasks (created_at) VALUES (datetime('now'))"
	result := convertToPostgresSQL(sql)
	if strings.Contains(result, "datetime('now')") {
		t.Error("datetime('now') should be replaced with NOW()")
	}
	if !strings.Contains(result, "NOW()") {
		t.Errorf("expected NOW() in result, got: %s", result)
	}
}

func TestConvertToPostgresSQL_Autoincrement(t *testing.T) {
	sql := "CREATE TABLE t (id INTEGER PRIMARY KEY AUTOINCREMENT)"
	result := convertToPostgresSQL(sql)
	if strings.Contains(result, "AUTOINCREMENT") {
		t.Error("AUTOINCREMENT should be replaced")
	}
	if !strings.Contains(result, "BIGSERIAL") {
		t.Errorf("expected BIGSERIAL, got: %s", result)
	}
}

func TestConvertToPostgresSQL_CurrentTimestamp(t *testing.T) {
	sql := "SELECT CURRENT_TIMESTAMP"
	result := convertToPostgresSQL(sql)
	if strings.Contains(result, "CURRENT_TIMESTAMP") {
		t.Error("CURRENT_TIMESTAMP should be replaced")
	}
}

// --- handler_helpers.go: withDB, requireMethod, writeJSON ---

func TestWithDB_NilDB_Returns503(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	oldDB := getDBConn()
	setDBConn(nil)
	defer setDBConn(oldDB)

	w := httptest.NewRecorder()
	called := false
	withDB(w, func(db *sql.DB) {
		called = true
	})
	if called {
		t.Error("fn should not be called when DB is nil")
	}
	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503 when DB nil, got %d", w.Code)
	}
}

func TestWithDB_ValidDB(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	w := httptest.NewRecorder()
	called := false
	withDB(w, func(db *sql.DB) {
		called = true
	})
	if !called {
		t.Error("fn should be called when DB is available")
	}
}

func TestRequireMethod_Matches(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/", nil)
	w := httptest.NewRecorder()
	result := requireMethod(w, req, http.MethodGet)
	if !result {
		t.Error("requireMethod should return true for matching method")
	}
	if w.Code != http.StatusOK {
		t.Errorf("expected no error response, got %d", w.Code)
	}
}

func TestRequireMethod_Mismatch(t *testing.T) {
	req := httptest.NewRequest(http.MethodDelete, "/", nil)
	w := httptest.NewRecorder()
	result := requireMethod(w, req, http.MethodGet)
	if result {
		t.Error("requireMethod should return false for method mismatch")
	}
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWriteJSON_Valid(t *testing.T) {
	w := httptest.NewRecorder()
	writeJSON(w, map[string]string{"status": "ok"})
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", w.Code)
	}
	if ct := w.Header().Get("Content-Type"); !strings.Contains(ct, "application/json") {
		t.Errorf("expected JSON content type, got %s", ct)
	}
	if !strings.Contains(w.Body.String(), "ok") {
		t.Errorf("expected 'ok' in body, got %s", w.Body.String())
	}
}

// --- patrol.go: PatrolSystem setters ---

func TestPatrolSystem_Setters(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	db := getDBConn()
	ps := NewPatrolSystem(db)

	// SetContextManager
	cm := &ContextManager{}
	ps.SetContextManager(cm)

	// SetRoyalJelly — pass nil (zero value)
	ps.SetRoyalJelly(nil)

	// AddContextPatrol — add a no-op ContextAwarePatrol
	noop := ContextAwarePatrol{
		Patrol: Patrol{
			ID:       "noop",
			Name:     "No-Op Test Patrol",
			Schedule: 0,
			Action: func(ctx context.Context, db *sql.DB) error {
				return nil
			},
		},
	}
	ps.AddContextPatrol(noop)
}

// --- gitguard.go: ReleaseBranchLock, GetBranchLock ---

func TestGitGuard_BranchLock(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	db := getDBConn()
	gg := &GitGuard{db: db}

	ctx := context.Background()

	// GetBranchLock on non-existent task should return "" with no error
	branch, err := gg.GetBranchLock(ctx, "NONEXISTENT-TASK")
	if err != nil {
		t.Errorf("GetBranchLock on missing task: %v", err)
	}
	if branch != "" {
		t.Errorf("expected empty branch, got %q", branch)
	}

	// ReleaseBranchLock on non-existent task should not error
	if err := gg.ReleaseBranchLock(ctx, "NONEXISTENT-TASK"); err != nil {
		t.Errorf("ReleaseBranchLock on missing task: %v", err)
	}
}
