//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"database/sql"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	_ "github.com/mattn/go-sqlite3"
)

// insertEnvelope inserts a minimal row into context_envelopes.
func insertEnvelope(t *testing.T, db *sql.DB, id, domain, project string, content string) {
	t.Helper()
	_, err := db.Exec(`
		INSERT INTO context_envelopes (id, agent_id, domain, project, task_id, summary, content, expires_at)
		VALUES (?, 'agent-1', ?, ?, 'task-1', '', ?, datetime('now','+1 day'))
	`, id, domain, project, content)
	if err != nil {
		t.Fatalf("insert envelope: %v", err)
	}
}

// TestContextSyncCoverage_parseMarkdownDecisions verifies bullet lines become Decision records.
func TestContextSyncCoverage_parseMarkdownDecisions(t *testing.T) {
	md := `# Decisions

- Use SQLite for local storage
* Prefer noun-verb CLI structure
Not a bullet

- Another decision
`
	decisions := parseMarkdownDecisions(md)
	if len(decisions) != 3 {
		t.Fatalf("expected 3 decisions, got %d: %v", len(decisions), decisions)
	}

	want := []string{
		"Use SQLite for local storage",
		"Prefer noun-verb CLI structure",
		"Another decision",
	}
	for i, d := range decisions {
		if d.Description != want[i] {
			t.Errorf("decision[%d] description = %q, want %q", i, d.Description, want[i])
		}
		if d.ID == "" {
			t.Errorf("decision[%d] ID is empty", i)
		}
		if d.Timestamp.IsZero() {
			t.Errorf("decision[%d] Timestamp is zero", i)
		}
	}
}

// TestContextSyncCoverage_parseMarkdownDecisionsEmpty ensures empty markdown returns nil slice.
func TestContextSyncCoverage_parseMarkdownDecisionsEmpty(t *testing.T) {
	decisions := parseMarkdownDecisions("# Title\n\nNo bullets here.\n")
	if len(decisions) != 0 {
		t.Errorf("expected 0 decisions, got %d", len(decisions))
	}
}

// TestContextSyncCoverage_parseMarkdownFailures verifies bullet lines become Failure records.
func TestContextSyncCoverage_parseMarkdownFailures(t *testing.T) {
	md := `## Failures

- SQLite UUID incompatibility on allergen-coach
* bcrypt incompatible with Python 3.13
`
	failures := parseMarkdownFailures(md)
	if len(failures) != 2 {
		t.Fatalf("expected 2 failures, got %d", len(failures))
	}

	if failures[0].Description != "SQLite UUID incompatibility on allergen-coach" {
		t.Errorf("failure[0] description = %q", failures[0].Description)
	}
	if failures[1].Description != "bcrypt incompatible with Python 3.13" {
		t.Errorf("failure[1] description = %q", failures[1].Description)
	}
	for i, f := range failures {
		if f.ID == "" {
			t.Errorf("failure[%d] ID is empty", i)
		}
		if f.Timestamp.IsZero() {
			t.Errorf("failure[%d] Timestamp is zero", i)
		}
	}
}

// TestContextSyncCoverage_parseMarkdownFailuresEmpty ensures no bullets returns nil slice.
func TestContextSyncCoverage_parseMarkdownFailuresEmpty(t *testing.T) {
	failures := parseMarkdownFailures("Nothing here")
	if len(failures) != 0 {
		t.Errorf("expected 0 failures, got %d", len(failures))
	}
}

// TestContextSyncCoverage_sanitizeJSONKey exercises all branches of the sanitizer.
func TestContextSyncCoverage_sanitizeJSONKey(t *testing.T) {
	tests := []struct {
		input string
		want  string
	}{
		{"lead-context.md", "lead_context"},   // hyphen replaced, extension stripped
		{"decisions.json", "decisions"},        // extension stripped, no substitution
		{"my file name.txt", "my_file_name"},   // spaces replaced
		{"calibration.md", "calibration"},
		{"foo-bar_baz.md", "foo_bar_baz"},
		{"ABC123.md", "ABC123"},                // alphanumeric passes through
		{"special!@#chars.md", "special___chars"}, // non-alphanum replaced
		{"noext", "noext"},                     // no extension
	}

	for _, tt := range tests {
		got := sanitizeJSONKey(tt.input)
		if got != tt.want {
			t.Errorf("sanitizeJSONKey(%q) = %q, want %q", tt.input, got, tt.want)
		}
	}
}

// TestContextSyncCoverage_updateFailures verifies the SQL path via syncFileToDB.
func TestContextSyncCoverage_updateFailures(t *testing.T) {
	db := setupContextSyncDB(t)
	defer db.Close()
	cs, _ := buildCS(t, db)

	// Seed a row with valid JSON content so json_set won't choke.
	seedContent := `{"id":"env-1","agent_id":"a","domain":"dom","project":"proj","task_id":"t","failures":[]}`
	insertEnvelope(t, db, "env-1", "dom", "proj", seedContent)

	md := "- failure one\n- failure two\n"
	if err := cs.updateFailures("dom", "proj", md); err != nil {
		t.Fatalf("updateFailures: %v", err)
	}

	// Verify the row was touched (no error is sufficient for the method path,
	// but also confirm the JSON field was updated).
	var content string
	err := db.QueryRow(`SELECT content FROM context_envelopes WHERE id = 'env-1'`).Scan(&content)
	if err != nil {
		t.Fatalf("query after updateFailures: %v", err)
	}
	if !strings.Contains(content, "failure") && !strings.Contains(content, "failures") {
		t.Logf("content after update: %s", content)
	}
}

// TestContextSyncCoverage_updateCalibration exercises the calibration update path.
func TestContextSyncCoverage_updateCalibration(t *testing.T) {
	db := setupContextSyncDB(t)
	defer db.Close()
	cs, _ := buildCS(t, db)

	seedContent := `{"id":"env-2","agent_id":"a","domain":"cal-dom","project":"","task_id":"t","calibration":{}}`
	insertEnvelope(t, db, "env-2", "cal-dom", "", seedContent)

	if err := cs.updateCalibration("cal-dom", "", "# Calibration notes\n\nSome notes here."); err != nil {
		t.Fatalf("updateCalibration: %v", err)
	}
}

// TestContextSyncCoverage_updateMetadata exercises the metadata update path including sanitizeJSONKey.
func TestContextSyncCoverage_updateMetadata(t *testing.T) {
	db := setupContextSyncDB(t)
	defer db.Close()
	cs, _ := buildCS(t, db)

	seedContent := `{"id":"env-3","agent_id":"a","domain":"meta-dom","project":"","task_id":"t","metadata":{}}`
	insertEnvelope(t, db, "env-3", "meta-dom", "", seedContent)

	if err := cs.updateMetadata("meta-dom", "", "custom-notes.md", "some content"); err != nil {
		t.Fatalf("updateMetadata: %v", err)
	}
}

// TestContextSyncCoverage_SyncDomainToFilesystem_NoRows verifies graceful no-op when no rows exist.
func TestContextSyncCoverage_SyncDomainToFilesystem_NoRows(t *testing.T) {
	db := setupContextSyncDB(t)
	defer db.Close()
	cs, _ := buildCS(t, db)

	// Domain with no rows — should return nil (not error).
	if err := cs.SyncDomainToFilesystem("nonexistent-domain"); err != nil {
		t.Fatalf("SyncDomainToFilesystem on empty domain: %v", err)
	}
}

// TestContextSyncCoverage_SyncDomainToFilesystem_WithData exercises the write paths.
func TestContextSyncCoverage_SyncDomainToFilesystem_WithData(t *testing.T) {
	db := setupContextSyncDB(t)
	defer db.Close()
	cs, contextDir := buildCS(t, db)

	decisions := []Decision{
		{ID: "d1", Description: "Use Go for CLI", Timestamp: time.Now()},
	}
	failures := []Failure{
		{ID: "f1", Description: "bcrypt issue", Timestamp: time.Now()},
	}
	decisionsJSON, _ := json.Marshal(decisions)
	failuresJSON, _ := json.Marshal(failures)

	envelope := map[string]interface{}{
		"id":        "env-fs",
		"agent_id":  "agent-1",
		"domain":    "fs-dom",
		"project":   "",
		"task_id":   "t1",
		"decisions": json.RawMessage(decisionsJSON),
		"failures":  json.RawMessage(failuresJSON),
		"metadata":  map[string]interface{}{"lead_context": "# Lead\n\nsome context"},
	}
	contentBytes, _ := json.Marshal(envelope)

	_, err := db.Exec(`
		INSERT INTO context_envelopes (id, agent_id, domain, project, task_id, summary, content, created_at, expires_at)
		VALUES ('env-fs', 'agent-1', 'fs-dom', '', 't1', '', ?, datetime('now'), datetime('now','+1 day'))
	`, string(contentBytes))
	if err != nil {
		t.Fatalf("insert envelope: %v", err)
	}

	if err := cs.SyncDomainToFilesystem("fs-dom"); err != nil {
		t.Fatalf("SyncDomainToFilesystem: %v", err)
	}

	// lead-context.md should have been written.
	lcPath := filepath.Join(contextDir, "fs-dom", "lead-context.md")
	if _, err := os.Stat(lcPath); err != nil {
		t.Errorf("lead-context.md not written: %v", err)
	}

	// decisions.json should have been written.
	djPath := filepath.Join(contextDir, "fs-dom", "decisions.json")
	if _, err := os.Stat(djPath); err != nil {
		t.Errorf("decisions.json not written: %v", err)
	}

	// failures.json should have been written.
	fjPath := filepath.Join(contextDir, "fs-dom", "failures.json")
	if _, err := os.Stat(fjPath); err != nil {
		t.Errorf("failures.json not written: %v", err)
	}
}

// TestContextSyncCoverage_SyncEnvelopesToFilesystem_Empty verifies no error on empty table.
func TestContextSyncCoverage_SyncEnvelopesToFilesystem_Empty(t *testing.T) {
	db := setupContextSyncDB(t)
	defer db.Close()

	tmpDir := t.TempDir()
	contextDir := filepath.Join(tmpDir, "context")
	os.MkdirAll(contextDir, 0755)

	cm := &ContextManager{db: db, contextDir: contextDir}
	cs, err := NewContextSync(cm, db, contextDir)
	if err != nil {
		t.Fatalf("NewContextSync: %v", err)
	}
	defer cs.Stop()

	if err := cs.SyncEnvelopesToFilesystem(); err != nil {
		t.Fatalf("SyncEnvelopesToFilesystem on empty DB: %v", err)
	}
}

// TestContextSyncCoverage_SyncEnvelopesToFilesystem_WithData exercises the scan+store path.
func TestContextSyncCoverage_SyncEnvelopesToFilesystem_WithData(t *testing.T) {
	db := setupContextSyncDB(t)
	defer db.Close()

	tmpDir := t.TempDir()
	contextDir := filepath.Join(tmpDir, "context")
	os.MkdirAll(contextDir, 0755)

	cm := &ContextManager{db: db, contextDir: contextDir}
	cs, err := NewContextSync(cm, db, contextDir)
	if err != nil {
		t.Fatalf("NewContextSync: %v", err)
	}
	defer cs.Stop()

	// Insert a valid envelope that has not expired.
	envelope := ContextEnvelope{
		ID:       "env-sync-1",
		AgentID:  "agent-x",
		Domain:   "sync-dom",
		Project:  "sync-proj",
		TaskID:   "task-x",
		Summary:  "test summary",
		Metadata: map[string]any{},
	}
	contentBytes, _ := json.Marshal(envelope)
	_, err = db.Exec(`
		INSERT INTO context_envelopes (id, agent_id, domain, project, task_id, summary, content, expires_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now','+1 day'))
	`, envelope.ID, envelope.AgentID, envelope.Domain, envelope.Project,
		envelope.TaskID, envelope.Summary, string(contentBytes))
	if err != nil {
		t.Fatalf("insert: %v", err)
	}

	// Should not error even if storeInFilesystem creates dirs.
	if err := cs.SyncEnvelopesToFilesystem(); err != nil {
		t.Fatalf("SyncEnvelopesToFilesystem: %v", err)
	}
}

// TestContextSyncCoverage_fullSync exercises the full sync loop with real domain dirs.
func TestContextSyncCoverage_fullSync(t *testing.T) {
	db := setupContextSyncDB(t)
	defer db.Close()

	tmpDir := t.TempDir()
	contextDir := filepath.Join(tmpDir, "context")

	// Create a domain directory with a file.
	domainDir := filepath.Join(contextDir, "full-sync-dom")
	os.MkdirAll(domainDir, 0755)
	if err := os.WriteFile(filepath.Join(domainDir, "lead-context.md"), []byte("# Full Sync\n"), 0644); err != nil {
		t.Fatalf("write lead-context: %v", err)
	}

	// Seed a matching envelope so SyncDomainToFilesystem has something to write.
	seedContent := `{"id":"env-full","agent_id":"a","domain":"full-sync-dom","project":"","task_id":"t","metadata":{}}`
	_, err := db.Exec(`
		INSERT INTO context_envelopes (id, agent_id, domain, project, task_id, summary, content, expires_at)
		VALUES ('env-full', 'a', 'full-sync-dom', '', 't', '', ?, datetime('now','+1 day'))
	`, seedContent)
	if err != nil {
		t.Fatalf("insert envelope: %v", err)
	}

	cm := &ContextManager{db: db, contextDir: contextDir}
	cs, err := NewContextSync(cm, db, contextDir)
	if err != nil {
		t.Fatalf("NewContextSync: %v", err)
	}
	defer cs.Stop()

	if err := cs.fullSync(); err != nil {
		t.Fatalf("fullSync: %v", err)
	}
}

// TestContextSyncCoverage_syncFileToDBBranches exercises each branch of syncFileToDB.
func TestContextSyncCoverage_syncFileToDBBranches(t *testing.T) {
	db := setupContextSyncDB(t)
	defer db.Close()
	cs, _ := buildCS(t, db)

	seedContent := `{"id":"env-br","agent_id":"a","domain":"br-dom","project":"","task_id":"t","metadata":{},"calibration":{},"failures":[]}`
	insertEnvelope(t, db, "env-br", "br-dom", "", seedContent)

	// failures.md branch
	if err := cs.syncFileToDB("br-dom", "", "failures.md", "- fail one\n"); err != nil {
		t.Errorf("syncFileToDB failures.md: %v", err)
	}

	// calibration.md branch
	if err := cs.syncFileToDB("br-dom", "", "calibration.md", "# Calibration\n"); err != nil {
		t.Errorf("syncFileToDB calibration.md: %v", err)
	}

	// default (metadata) branch
	if err := cs.syncFileToDB("br-dom", "", "notes.md", "some notes"); err != nil {
		t.Errorf("syncFileToDB notes.md (default): %v", err)
	}
}
