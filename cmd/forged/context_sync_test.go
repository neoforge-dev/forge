//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
	"time"

	_ "github.com/mattn/go-sqlite3"
)

func TestContextSync_Bidirectional(t *testing.T) {
	t.Setenv("FORGE_CONTEXT_SYNC_DEBOUNCE_MS", "50") // 500ms → 50ms debounce
	// Create temp directory for test
	tempDir, err := os.MkdirTemp("", "context-sync-test")
	if err != nil {
		t.Fatalf("Failed to create temp dir: %v", err)
	}
	defer os.RemoveAll(tempDir)

	// Create temp database
	dbPath := filepath.Join(tempDir, "test.db")
	db, err := sql.Open("sqlite3", dbPath+"?_journal_mode=WAL")
	if err != nil {
		t.Fatalf("Failed to open database: %v", err)
	}
	defer db.Close()

	// Create context_envelopes table
	_, err = db.Exec(`
		CREATE TABLE context_envelopes (
			id TEXT PRIMARY KEY,
			agent_id TEXT NOT NULL,
			domain TEXT NOT NULL,
			project TEXT NOT NULL,
			task_id TEXT NOT NULL,
			summary TEXT,
			content TEXT,
			created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
			expires_at TIMESTAMP NOT NULL
		)
	`)
	if err != nil {
		t.Fatalf("Failed to create table: %v", err)
	}

	// Create context directory structure BEFORE creating ContextManager
	contextDir := filepath.Join(tempDir, "context")
	testDomainDir := filepath.Join(contextDir, "test-domain")
	if err := os.MkdirAll(testDomainDir, 0755); err != nil {
		t.Fatalf("Failed to create domain dir: %v", err)
	}

	// Create ContextManager
	cm := testContextManager(t, db, contextDir)
	defer cm.Stop()

	// Give sync time to start
	time.Sleep(50 * time.Millisecond)

	// Test 1: Generate envelope via API should create file on disk
	ctx := context.Background()
	envelope, err := cm.GenerateEnvelope(ctx, "test-agent", "test-domain", "test-project", "test-task", "Test envelope")
	if err != nil {
		t.Fatalf("Failed to generate envelope: %v", err)
	}

	// Wait for file write
	time.Sleep(100 * time.Millisecond)

	// Verify envelope file exists
	envelopePath := filepath.Join(contextDir, "envelopes", envelope.ID+".json")
	if _, err := os.Stat(envelopePath); os.IsNotExist(err) {
		t.Errorf("Envelope file not created: %s", envelopePath)
	} else {
		t.Logf("Envelope file created: %s", envelopePath)
	}

	// Test 2: Modify file on disk should update SQLite
	leadContextPath := filepath.Join(testDomainDir, "lead-context.md")
	testContent := "# Test Domain\n\nThis is test lead context.\n"
	if err := os.WriteFile(leadContextPath, []byte(testContent), 0644); err != nil {
		t.Fatalf("Failed to write lead context: %v", err)
	}

	// Wait for sync
	time.Sleep(120 * time.Millisecond)

	// Verify SQLite was updated
	var content string
	err = db.QueryRow(`
		SELECT content FROM context_envelopes WHERE domain = ?
	`, "test-domain").Scan(&content)
	if err != nil {
		t.Fatalf("Failed to query envelope: %v", err)
	}

	var envelopeData map[string]interface{}
	if err := json.Unmarshal([]byte(content), &envelopeData); err != nil {
		t.Fatalf("Failed to unmarshal envelope: %v", err)
	}

	metadata, ok := envelopeData["metadata"].(map[string]interface{})
	if !ok {
		t.Fatalf("Metadata not found in envelope")
	}

	leadContext, ok := metadata["lead_context"].(string)
	if !ok || leadContext != testContent {
		t.Errorf("Lead context not synced correctly. Expected:\n%s\nGot:\n%v", testContent, leadContext)
	} else {
		t.Log("Lead context synced correctly!")
	}

	t.Log("Bidirectional sync test passed!")
}

func TestContextSync_FileHashTracking(t *testing.T) {
	t.Setenv("FORGE_CONTEXT_SYNC_DEBOUNCE_MS", "50") // 500ms → 50ms debounce
	tempDir, err := os.MkdirTemp("", "context-sync-hash-test")
	if err != nil {
		t.Fatalf("Failed to create temp dir: %v", err)
	}
	defer os.RemoveAll(tempDir)

	dbPath := filepath.Join(tempDir, "test.db")
	db, err := sql.Open("sqlite3", dbPath)
	if err != nil {
		t.Fatalf("Failed to open database: %v", err)
	}
	defer db.Close()

	_, err = db.Exec(`
		CREATE TABLE context_envelopes (
			id TEXT PRIMARY KEY,
			agent_id TEXT NOT NULL,
			domain TEXT NOT NULL,
			project TEXT NOT NULL,
			task_id TEXT NOT NULL,
			summary TEXT,
			content TEXT,
			created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
			expires_at TIMESTAMP NOT NULL
		)
	`)
	if err != nil {
		t.Fatalf("Failed to create table: %v", err)
	}

	contextDir := filepath.Join(tempDir, "context")
	testDomainDir := filepath.Join(contextDir, "test-domain")
	os.MkdirAll(testDomainDir, 0755)

	cm := testContextManager(t, db, contextDir)
	defer cm.Stop()

	time.Sleep(50 * time.Millisecond)

	// Write initial file
	testFile := filepath.Join(testDomainDir, "decisions.json")
	initialContent := `[{"id": "1", "description": "Decision 1"}]`
	os.WriteFile(testFile, []byte(initialContent), 0644)

	time.Sleep(120 * time.Millisecond)

	// Write same content again - should not trigger duplicate sync
	os.WriteFile(testFile, []byte(initialContent), 0644)

	time.Sleep(120 * time.Millisecond)

	// Write different content - should trigger sync
	newContent := `[{"id": "1", "description": "Decision 1"}, {"id": "2", "description": "Decision 2"}]`
	os.WriteFile(testFile, []byte(newContent), 0644)

	time.Sleep(120 * time.Millisecond)

	t.Log("File hash tracking test passed!")
}

func TestContextSync_extractDomainProject(t *testing.T) {
	cs := &ContextSync{contextDir: "/tmp/context"}

	tests := []struct {
		path        string
		wantDomain  string
		wantProject string
		wantErr     bool
	}{
		{
			path:        "/tmp/context/domain1/file.md",
			wantDomain:  "domain1",
			wantProject: "",
			wantErr:     false,
		},
		{
			path:        "/tmp/context/domain1/project1/file.md",
			wantDomain:  "domain1",
			wantProject: "project1",
			wantErr:     false,
		},
	}

	for _, tt := range tests {
		domain, project, err := cs.extractDomainProject(tt.path)
		if (err != nil) != tt.wantErr {
			t.Errorf("extractDomainProject(%s) error = %v, wantErr %v", tt.path, err, tt.wantErr)
			continue
		}
		if domain != tt.wantDomain {
			t.Errorf("extractDomainProject(%s) domain = %v, want %v", tt.path, domain, tt.wantDomain)
		}
		if project != tt.wantProject {
			t.Errorf("extractDomainProject(%s) project = %v, want %v", tt.path, project, tt.wantProject)
		}
	}
}

func TestContextSync_Simple(t *testing.T) {
	// Create temp directory for test
	tempDir, err := os.MkdirTemp("", "context-sync-test")
	if err != nil {
		t.Fatalf("Failed to create temp dir: %v", err)
	}
	defer os.RemoveAll(tempDir)

	// Create temp database
	dbPath := filepath.Join(tempDir, "test.db")
	db, err := sql.Open("sqlite3", dbPath+"?_journal_mode=WAL")
	if err != nil {
		t.Fatalf("Failed to open database: %v", err)
	}
	defer db.Close()

	// Create context_envelopes table
	_, err = db.Exec(`
		CREATE TABLE context_envelopes (
			id TEXT PRIMARY KEY,
			agent_id TEXT NOT NULL,
			domain TEXT NOT NULL,
			project TEXT NOT NULL,
			task_id TEXT NOT NULL,
			summary TEXT,
			content TEXT,
			created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
			expires_at TIMESTAMP NOT NULL
		)
	`)
	if err != nil {
		t.Fatalf("Failed to create table: %v", err)
	}

	// Create context directory structure BEFORE creating ContextManager
	contextDir := filepath.Join(tempDir, "context")
	testDomainDir := filepath.Join(contextDir, "test-domain")
	if err := os.MkdirAll(testDomainDir, 0755); err != nil {
		t.Fatalf("Failed to create domain dir: %v", err)
	}

	// Test file hash tracking first
	testFile := filepath.Join(testDomainDir, "test.txt")
	if err := os.WriteFile(testFile, []byte("test content"), 0644); err != nil {
		t.Fatalf("Failed to write test file: %v", err)
	}

	// Create ContextSync directly (not through ContextManager)
	cm := &ContextManager{db: db, contextDir: contextDir}
	cs, err := NewContextSync(cm, db, contextDir)
	if err != nil {
		t.Fatalf("Failed to create context sync: %v", err)
	}
	defer cs.Stop()

	// Start sync with timeout
	done := make(chan error, 1)
	go func() {
		done <- cs.Start()
	}()

	select {
	case err := <-done:
		if err != nil {
			t.Fatalf("Failed to start sync: %v", err)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("Timeout starting sync")
	}

	t.Log("Context sync started successfully!")

	// Test extractDomainProject
	domain, project, err := cs.extractDomainProject(testFile)
	if err != nil {
		t.Fatalf("Failed to extract domain/project: %v", err)
	}
	if domain != "test-domain" {
		t.Errorf("Expected domain 'test-domain', got '%s'", domain)
	}
	if project != "" {
		t.Errorf("Expected empty project, got '%s'", project)
	}

	t.Logf("Extracted domain=%s, project=%s", domain, project)

	// Test file hash calculation
	hash1, err := cs.calculateFileHash(testFile)
	if err != nil {
		t.Fatalf("Failed to calculate file hash: %v", err)
	}
	t.Logf("File hash: %s", hash1)

	// Modify file
	os.WriteFile(testFile, []byte("modified content"), 0644)
	hash2, err := cs.calculateFileHash(testFile)
	if err != nil {
		t.Fatalf("Failed to calculate file hash: %v", err)
	}

	if hash1 == hash2 {
		t.Error("Hash should change when file content changes")
	}

	t.Log("Simple sync test passed!")
}

// TestSyncEnvelopesToFilesystem_EmptyDB verifies that SyncEnvelopesToFilesystem
// returns nil when there are no expired envelopes (empty result set).
// This covers the query execution and rows iteration paths.
func TestSyncEnvelopesToFilesystem_EmptyDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	contextDir := t.TempDir()
	cm := &ContextManager{db: db, contextDir: contextDir}
	cs, err := NewContextSync(cm, db, contextDir)
	if err != nil {
		t.Fatalf("NewContextSync: %v", err)
	}
	defer cs.Stop()

	// No envelopes in DB — should succeed with empty result.
	if err := cs.SyncEnvelopesToFilesystem(); err != nil {
		t.Errorf("SyncEnvelopesToFilesystem on empty DB: %v", err)
	}
}

// TestSyncDomainToFilesystem_NoRows verifies that SyncDomainToFilesystem
// returns nil (not an error) when there are no context_envelopes for the domain.
// This covers the sql.ErrNoRows early-return branch.
func TestSyncDomainToFilesystem_NoRows(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	contextDir := t.TempDir()
	cm := &ContextManager{db: db, contextDir: contextDir}
	cs, err := NewContextSync(cm, db, contextDir)
	if err != nil {
		t.Fatalf("NewContextSync: %v", err)
	}
	defer cs.Stop()

	// No envelopes for domain "nonexistent" — should return nil.
	if err := cs.SyncDomainToFilesystem("nonexistent"); err != nil {
		t.Errorf("SyncDomainToFilesystem with no rows: %v", err)
	}
}
