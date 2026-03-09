//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"database/sql"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	_ "github.com/mattn/go-sqlite3"
)

// wave70SyncDB creates a full context_envelopes table for sync tests.
func wave70SyncDB(t *testing.T) *sql.DB {
	t.Helper()
	db, err := sql.Open("sqlite3", ":memory:?_journal_mode=WAL")
	if err != nil {
		t.Fatalf("wave70SyncDB: %v", err)
	}
	_, err = db.Exec(`
		CREATE TABLE context_envelopes (
			id         TEXT PRIMARY KEY,
			agent_id   TEXT NOT NULL,
			domain     TEXT NOT NULL,
			project    TEXT NOT NULL DEFAULT '',
			task_id    TEXT NOT NULL DEFAULT '',
			summary    TEXT,
			content    TEXT,
			created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
			expires_at TIMESTAMP NOT NULL DEFAULT (datetime('now','+1 day'))
		)
	`)
	if err != nil {
		t.Fatalf("wave70SyncDB create table: %v", err)
	}
	t.Cleanup(func() { db.Close() })
	return db
}

// ---------------------------------------------------------------------------
// NewContextSync — 75.0%
// ---------------------------------------------------------------------------

// TestWave70_NewContextSync_Success verifies successful creation with a valid dir.
func TestWave70_NewContextSync_Success(t *testing.T) {
	db := wave70SyncDB(t)
	tmpDir := t.TempDir()
	contextDir := filepath.Join(tmpDir, "context")
	os.MkdirAll(contextDir, 0755)

	cm := &ContextManager{db: db, contextDir: contextDir}
	cs, err := NewContextSync(cm, db, contextDir)
	if err != nil {
		t.Fatalf("NewContextSync: %v", err)
	}
	defer cs.Stop()

	if cs.cm == nil {
		t.Error("cm should not be nil")
	}
	if cs.db == nil {
		t.Error("db should not be nil")
	}
	if cs.watcher == nil {
		t.Error("watcher should not be nil")
	}
	if cs.contextDir != contextDir {
		t.Errorf("contextDir mismatch: %s != %s", cs.contextDir, contextDir)
	}
	if cs.fileHashes == nil {
		t.Error("fileHashes should be initialized")
	}
	if cs.lastDBUpdate == nil {
		t.Error("lastDBUpdate should be initialized")
	}
	if cs.stopCh == nil {
		t.Error("stopCh should not be nil")
	}
}

// ---------------------------------------------------------------------------
// Start() — 63.2% — cover domain/project directory watching branches
// ---------------------------------------------------------------------------

// TestWave70_ContextSync_Start_EmptyDir covers Start() with no domain dirs.
func TestWave70_ContextSync_Start_EmptyDir(t *testing.T) {
	db := wave70SyncDB(t)
	tmpDir := t.TempDir()
	contextDir := filepath.Join(tmpDir, "context")
	os.MkdirAll(contextDir, 0755)

	cm := &ContextManager{db: db, contextDir: contextDir}
	cs, err := NewContextSync(cm, db, contextDir)
	if err != nil {
		t.Fatalf("NewContextSync: %v", err)
	}
	defer cs.Stop()

	if err := cs.Start(); err != nil {
		t.Fatalf("Start with empty contextDir: %v", err)
	}
}

// TestWave70_ContextSync_Start_WithDomains covers Start() watching domain dirs.
func TestWave70_ContextSync_Start_WithDomains(t *testing.T) {
	db := wave70SyncDB(t)
	tmpDir := t.TempDir()
	contextDir := filepath.Join(tmpDir, "context")

	// Create domain directories (non-envelope)
	domain1 := filepath.Join(contextDir, "domain-alpha")
	domain2 := filepath.Join(contextDir, "domain-beta")
	os.MkdirAll(domain1, 0755)
	os.MkdirAll(domain2, 0755)

	// Create project subdirectory inside domain1
	proj1 := filepath.Join(domain1, "project-one")
	os.MkdirAll(proj1, 0755)

	// Write a file in domain1
	os.WriteFile(filepath.Join(domain1, "lead-context.md"), []byte("# Alpha\n"), 0644)

	cm := &ContextManager{db: db, contextDir: contextDir}
	cs, err := NewContextSync(cm, db, contextDir)
	if err != nil {
		t.Fatalf("NewContextSync: %v", err)
	}
	defer cs.Stop()

	if err := cs.Start(); err != nil {
		t.Fatalf("Start with domains: %v", err)
	}
}

// TestWave70_ContextSync_Start_WithEnvelopesDir verifies "envelopes" dir is skipped.
func TestWave70_ContextSync_Start_WithEnvelopesDir(t *testing.T) {
	db := wave70SyncDB(t)
	tmpDir := t.TempDir()
	contextDir := filepath.Join(tmpDir, "context")

	// Create "envelopes" dir — should be skipped by getDomainDirectories
	envelopesDir := filepath.Join(contextDir, "envelopes")
	domainDir := filepath.Join(contextDir, "real-domain")
	os.MkdirAll(envelopesDir, 0755)
	os.MkdirAll(domainDir, 0755)

	cm := &ContextManager{db: db, contextDir: contextDir}
	cs, err := NewContextSync(cm, db, contextDir)
	if err != nil {
		t.Fatalf("NewContextSync: %v", err)
	}
	defer cs.Stop()

	if err := cs.Start(); err != nil {
		t.Fatalf("Start with envelopes dir present: %v", err)
	}

	// Verify getDomainDirectories skips "envelopes"
	domains, err := cs.getDomainDirectories()
	if err != nil {
		t.Fatalf("getDomainDirectories: %v", err)
	}
	for _, d := range domains {
		if d == "envelopes" {
			t.Error("getDomainDirectories should skip 'envelopes' dir")
		}
	}
}

// TestWave70_ContextSync_Stop_Idempotent verifies Stop() doesn't panic.
func TestWave70_ContextSync_Stop_Idempotent(t *testing.T) {
	db := wave70SyncDB(t)
	tmpDir := t.TempDir()
	contextDir := filepath.Join(tmpDir, "context")
	os.MkdirAll(contextDir, 0755)

	cm := &ContextManager{db: db, contextDir: contextDir}
	cs, err := NewContextSync(cm, db, contextDir)
	if err != nil {
		t.Fatalf("NewContextSync: %v", err)
	}

	// Should not panic
	cs.Stop()
}

// ---------------------------------------------------------------------------
// fullSync() — 76.5% — cover more branches
// ---------------------------------------------------------------------------

// TestWave70_ContextSync_FullSync_MultiDomain exercises full sync with multiple domains.
func TestWave70_ContextSync_FullSync_MultiDomain(t *testing.T) {
	db := wave70SyncDB(t)
	tmpDir := t.TempDir()
	contextDir := filepath.Join(tmpDir, "context")

	// Two domain dirs with files
	for _, domain := range []string{"alpha-dom", "beta-dom"} {
		domDir := filepath.Join(contextDir, domain)
		os.MkdirAll(domDir, 0755)
		os.WriteFile(filepath.Join(domDir, "lead-context.md"), []byte("# "+domain+"\n"), 0644)
	}

	cm := &ContextManager{db: db, contextDir: contextDir}
	cs, err := NewContextSync(cm, db, contextDir)
	if err != nil {
		t.Fatalf("NewContextSync: %v", err)
	}
	defer cs.Stop()

	// fullSync should not error even without DB rows
	if err := cs.fullSync(); err != nil {
		t.Fatalf("fullSync: %v", err)
	}
}

// TestWave70_ContextSync_FullSync_WithSubdir exercises domains that have subdirectories
// (files in subdir are directories, skipped by fullSync).
func TestWave70_ContextSync_FullSync_WithSubdir(t *testing.T) {
	db := wave70SyncDB(t)
	tmpDir := t.TempDir()
	contextDir := filepath.Join(tmpDir, "context")

	domainDir := filepath.Join(contextDir, "subdir-dom")
	subDir := filepath.Join(domainDir, "project-sub")
	os.MkdirAll(subDir, 0755)

	// File in domain dir
	os.WriteFile(filepath.Join(domainDir, "decisions.json"), []byte("[]"), 0644)
	// Dir in domain dir (should be skipped)
	os.MkdirAll(filepath.Join(domainDir, "project-nested"), 0755)

	cm := &ContextManager{db: db, contextDir: contextDir}
	cs, err := NewContextSync(cm, db, contextDir)
	if err != nil {
		t.Fatalf("NewContextSync: %v", err)
	}
	defer cs.Stop()

	if err := cs.fullSync(); err != nil {
		t.Fatalf("fullSync with subdir: %v", err)
	}
}

// ---------------------------------------------------------------------------
// handleFileChange — extra branches (deleted file, directory, hash match)
// ---------------------------------------------------------------------------

// TestWave70_HandleFileChange_DeletedFile exercises the os.IsNotExist path.
func TestWave70_HandleFileChange_DeletedFile(t *testing.T) {
	db := wave70SyncDB(t)
	tmpDir := t.TempDir()
	contextDir := filepath.Join(tmpDir, "context")
	os.MkdirAll(contextDir, 0755)

	cm := &ContextManager{db: db, contextDir: contextDir}
	cs, err := NewContextSync(cm, db, contextDir)
	if err != nil {
		t.Fatalf("NewContextSync: %v", err)
	}
	defer cs.Stop()

	// handleFileChange for a non-existent file should return nil
	err = cs.handleFileChange("/nonexistent/path/to/file.md")
	if err != nil {
		t.Fatalf("handleFileChange for deleted file should return nil, got %v", err)
	}
}

// TestWave70_HandleFileChange_Directory exercises the IsDir() skip.
func TestWave70_HandleFileChange_Directory(t *testing.T) {
	db := wave70SyncDB(t)
	tmpDir := t.TempDir()
	contextDir := filepath.Join(tmpDir, "context")
	domainDir := filepath.Join(contextDir, "hfc-domain")
	os.MkdirAll(domainDir, 0755)

	cm := &ContextManager{db: db, contextDir: contextDir}
	cs, err := NewContextSync(cm, db, contextDir)
	if err != nil {
		t.Fatalf("NewContextSync: %v", err)
	}
	defer cs.Stop()

	// Passing a directory path should return nil (skip)
	err = cs.handleFileChange(domainDir)
	if err != nil {
		t.Fatalf("handleFileChange for dir should return nil, got %v", err)
	}
}

// TestWave70_HandleFileChange_SameHash exercises the hash deduplication path.
func TestWave70_HandleFileChange_SameHash(t *testing.T) {
	db := wave70SyncDB(t)
	tmpDir := t.TempDir()
	contextDir := filepath.Join(tmpDir, "context")
	domainDir := filepath.Join(contextDir, "hash-domain")
	os.MkdirAll(domainDir, 0755)

	filePath := filepath.Join(domainDir, "lead-context.md")
	os.WriteFile(filePath, []byte("# Hash Test\n"), 0644)

	cm := &ContextManager{db: db, contextDir: contextDir}
	cs, err := NewContextSync(cm, db, contextDir)
	if err != nil {
		t.Fatalf("NewContextSync: %v", err)
	}
	defer cs.Stop()

	// First call computes and stores hash
	err = cs.handleFileChange(filePath)
	if err != nil {
		t.Fatalf("first handleFileChange: %v", err)
	}

	// Second call with same content hits the "no change" branch
	err = cs.handleFileChange(filePath)
	if err != nil {
		t.Fatalf("second handleFileChange (same hash): %v", err)
	}
}

// TestWave70_HandleFileChange_LeadContext exercises the lead-context.md sync path.
func TestWave70_HandleFileChange_LeadContext(t *testing.T) {
	db := wave70SyncDB(t)
	tmpDir := t.TempDir()
	contextDir := filepath.Join(tmpDir, "context")
	domainDir := filepath.Join(contextDir, "lc-domain")
	os.MkdirAll(domainDir, 0755)

	// Insert a row so updateLeadContext has something to update
	db.Exec(`INSERT INTO context_envelopes (id,agent_id,domain,project,task_id,content,expires_at) VALUES ('env-lc','a','lc-domain','','t','{"id":"env-lc"}',datetime('now','+1 day'))`)

	filePath := filepath.Join(domainDir, "lead-context.md")
	os.WriteFile(filePath, []byte("# Lead context for test\n"), 0644)

	cm := &ContextManager{db: db, contextDir: contextDir}
	cs, err := NewContextSync(cm, db, contextDir)
	if err != nil {
		t.Fatalf("NewContextSync: %v", err)
	}
	defer cs.Stop()

	err = cs.handleFileChange(filePath)
	if err != nil {
		t.Fatalf("handleFileChange lead-context.md: %v", err)
	}
}

// ---------------------------------------------------------------------------
// SyncEnvelopesToFilesystem — 72.2% — cover the scan error/unmarshal error branches
// ---------------------------------------------------------------------------

// TestWave70_SyncEnvelopesToFilesystem_BadJSON exercises the json.Unmarshal error branch (logged, skipped).
func TestWave70_SyncEnvelopesToFilesystem_BadJSON(t *testing.T) {
	db := wave70SyncDB(t)
	tmpDir := t.TempDir()
	contextDir := filepath.Join(tmpDir, "context")
	os.MkdirAll(contextDir, 0755)

	// Insert row with invalid JSON content — should be logged and skipped
	db.Exec(`INSERT INTO context_envelopes (id,agent_id,domain,project,task_id,content,expires_at) VALUES ('env-bad','a','bad-dom','','t','not-valid-json',datetime('now','+1 day'))`)

	cm := &ContextManager{db: db, contextDir: contextDir}
	cs, err := NewContextSync(cm, db, contextDir)
	if err != nil {
		t.Fatalf("NewContextSync: %v", err)
	}
	defer cs.Stop()

	// Should not return error — just log and continue
	if err := cs.SyncEnvelopesToFilesystem(); err != nil {
		t.Fatalf("SyncEnvelopesToFilesystem with bad JSON: %v", err)
	}
}

// TestWave70_SyncEnvelopesToFilesystem_MultipleRows exercises scanning multiple rows.
func TestWave70_SyncEnvelopesToFilesystem_MultipleRows(t *testing.T) {
	db := wave70SyncDB(t)
	tmpDir := t.TempDir()
	contextDir := filepath.Join(tmpDir, "context")
	os.MkdirAll(contextDir, 0755)

	envIDs := []string{"multi-env-a", "multi-env-b", "multi-env-c"}
	for _, id := range envIDs {
		env := ContextEnvelope{
			ID: id, AgentID: "ag", Domain: "multi-dom", Project: "p",
			TaskID: "t", Metadata: map[string]any{},
		}
		import_json_bytes, _ := json.Marshal(env)
		db.Exec(`INSERT INTO context_envelopes (id,agent_id,domain,project,task_id,content,expires_at) VALUES (?,?,?,?,?,?,datetime('now','+1 day'))`,
			env.ID, env.AgentID, env.Domain, env.Project, env.TaskID, string(import_json_bytes))
	}

	cm := &ContextManager{db: db, contextDir: contextDir}
	cs, err := NewContextSync(cm, db, contextDir)
	if err != nil {
		t.Fatalf("NewContextSync: %v", err)
	}
	defer cs.Stop()

	if err := cs.SyncEnvelopesToFilesystem(); err != nil {
		t.Fatalf("SyncEnvelopesToFilesystem multi-row: %v", err)
	}
}
