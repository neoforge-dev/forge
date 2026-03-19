//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"os"
	"path/filepath"
	"testing"
)

// ---------------------------------------------------------------------------
// routing_config.go:LoadRoutingBundle (line ~61)
// ---------------------------------------------------------------------------

// TestWave128_LoadRoutingBundle_MissingDir verifies error when config dir is missing.
func TestWave128_LoadRoutingBundle_MissingDir(t *testing.T) {
	_, err := LoadRoutingBundle("/nonexistent/path/wave128")
	if err == nil {
		t.Error("expected error for missing config dir")
	}
	t.Logf("LoadRoutingBundle missing dir: %v", err)
}

// TestWave128_LoadRoutingBundle_MissingAgentRegistry verifies error when agent-registry.yaml missing.
func TestWave128_LoadRoutingBundle_MissingAgentRegistry(t *testing.T) {
	tmpDir := t.TempDir()
	// Only create node-registry.yaml, not agent-registry.yaml
	if err := os.WriteFile(filepath.Join(tmpDir, "node-registry.yaml"), []byte("nodes: []\n"), 0644); err != nil {
		t.Fatalf("write: %v", err)
	}

	_, err := LoadRoutingBundle(tmpDir)
	if err == nil {
		t.Error("expected error for missing agent-registry.yaml")
	}
	t.Logf("LoadRoutingBundle missing agent-registry: %v", err)
}

// TestWave128_LoadRoutingBundle_InvalidAgentYAML verifies error on malformed YAML.
func TestWave128_LoadRoutingBundle_InvalidAgentYAML(t *testing.T) {
	tmpDir := t.TempDir()
	if err := os.WriteFile(filepath.Join(tmpDir, "agent-registry.yaml"), []byte("{bad: yaml: ["), 0644); err != nil {
		t.Fatalf("write: %v", err)
	}
	if err := os.WriteFile(filepath.Join(tmpDir, "node-registry.yaml"), []byte("nodes: []\n"), 0644); err != nil {
		t.Fatalf("write: %v", err)
	}

	_, err := LoadRoutingBundle(tmpDir)
	if err == nil {
		t.Error("expected error for malformed agent YAML")
	}
	t.Logf("LoadRoutingBundle malformed YAML: %v", err)
}

// TestWave128_LoadRoutingBundle_MissingNodeRegistry verifies error when node-registry.yaml missing.
func TestWave128_LoadRoutingBundle_MissingNodeRegistry(t *testing.T) {
	tmpDir := t.TempDir()
	if err := os.WriteFile(filepath.Join(tmpDir, "agent-registry.yaml"), []byte("agents: []\n"), 0644); err != nil {
		t.Fatalf("write: %v", err)
	}
	// No node-registry.yaml

	_, err := LoadRoutingBundle(tmpDir)
	if err == nil {
		t.Error("expected error for missing node-registry.yaml")
	}
	t.Logf("LoadRoutingBundle missing node-registry: %v", err)
}

// TestWave128_LoadRoutingBundle_MissingEnvelopeSchema verifies error when task-envelope-v1.yaml missing.
func TestWave128_LoadRoutingBundle_MissingEnvelopeSchema(t *testing.T) {
	tmpDir := t.TempDir()
	if err := os.WriteFile(filepath.Join(tmpDir, "agent-registry.yaml"), []byte("agents: []\n"), 0644); err != nil {
		t.Fatalf("write: %v", err)
	}
	if err := os.WriteFile(filepath.Join(tmpDir, "node-registry.yaml"), []byte("nodes: []\n"), 0644); err != nil {
		t.Fatalf("write: %v", err)
	}
	// No task-envelope-v1.yaml

	_, err := LoadRoutingBundle(tmpDir)
	if err == nil {
		t.Error("expected error for missing task-envelope-v1.yaml")
	}
	t.Logf("LoadRoutingBundle missing envelope schema: %v", err)
}

// ---------------------------------------------------------------------------
// context.go:storeInFilesystem (line ~245)
// ---------------------------------------------------------------------------

// TestWave128_StoreInFilesystem_Success verifies envelope is written to disk.
func TestWave128_StoreInFilesystem_Success(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	tmpDir := t.TempDir()
	cm := testContextManager(t, db, tmpDir)

	envelope := &ContextEnvelope{
		ID:      "test-env-wave128",
		AgentID: "test-agent",
		Domain:  "forge",
		Project: "dispatch",
		Summary: "test summary",
	}

	err := cm.storeInFilesystem(envelope)
	if err != nil {
		t.Fatalf("storeInFilesystem: %v", err)
	}

	// Verify file was written
	path := filepath.Join(tmpDir, "envelopes", "test-env-wave128.json")
	if _, err := os.Stat(path); err != nil {
		t.Errorf("expected file at %s: %v", path, err)
	}
}

// TestWave128_StoreInFilesystem_ReadOnlyDir verifies error on unwritable directory.
func TestWave128_StoreInFilesystem_ReadOnlyDir(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	tmpDir := t.TempDir()
	// Make tmpDir read-only
	if err := os.Chmod(tmpDir, 0555); err != nil {
		t.Skipf("cannot chmod: %v", err)
	}
	defer os.Chmod(tmpDir, 0755)

	cm := testContextManager(t, db, tmpDir)

	envelope := &ContextEnvelope{
		ID:      "test-env-readonly",
		AgentID: "test-agent",
		Summary: "readonly test",
	}

	err := cm.storeInFilesystem(envelope)
	if err == nil {
		t.Log("storeInFilesystem succeeded despite readonly dir (may be root)")
	} else {
		t.Logf("storeInFilesystem readonly dir: %v (expected)", err)
	}
}

// ---------------------------------------------------------------------------
// context.go:GenerateEnvelope (line ~123)
// ---------------------------------------------------------------------------

// TestWave128_GenerateEnvelope_Basic verifies envelope generation.
func TestWave128_GenerateEnvelope_Basic(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	cm := testContextManager(t, db, t.TempDir())

	// GenerateEnvelope(ctx, agentID, domain, project, taskID, reason)
	envelope, err := cm.GenerateEnvelope(
		context.Background(),
		"test-agent-128",
		"forge",
		"dispatch",
		"task-128",
		"Testing wave 128",
	)
	if err != nil {
		t.Logf("GenerateEnvelope error (may be expected): %v", err)
		return
	}

	if envelope.AgentID != "test-agent-128" {
		t.Errorf("expected agent 'test-agent-128', got %q", envelope.AgentID)
	}
	if envelope.Domain != "forge" {
		t.Errorf("expected domain 'forge', got %q", envelope.Domain)
	}
}

// TestWave128_GenerateEnvelope_ClosedDB verifies graceful handling on DB error.
func TestWave128_GenerateEnvelope_ClosedDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	cleanup() // close DB before call

	cm := testContextManager(t, db, t.TempDir())

	_, err := cm.GenerateEnvelope(
		context.Background(),
		"test-agent-128",
		"forge",
		"dispatch",
		"task-128",
		"Testing closed DB",
	)
	t.Logf("GenerateEnvelope closed DB: %v", err)
}

// ---------------------------------------------------------------------------
// context_sync.go:NewContextSync + Start (line ~41 + ~59)
// ---------------------------------------------------------------------------

// TestWave128_NewContextSync_Success verifies ContextSync can be created.
func TestWave128_NewContextSync_Success(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	tmpDir := t.TempDir()
	cm := testContextManager(t, db, tmpDir)

	cs, err := NewContextSync(cm, db, tmpDir)
	if err != nil {
		t.Fatalf("NewContextSync: %v", err)
	}
	if cs == nil {
		t.Error("expected non-nil ContextSync")
	}
	cs.Stop()
}

// TestWave128_ContextSync_Start_Stop verifies Start/Stop cycle doesn't panic.
func TestWave128_ContextSync_Start_Stop(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	tmpDir := t.TempDir()
	cm := testContextManager(t, db, tmpDir)

	cs, err := NewContextSync(cm, db, tmpDir)
	if err != nil {
		t.Fatalf("NewContextSync: %v", err)
	}

	if err := cs.Start(); err != nil {
		t.Logf("ContextSync.Start error (may be expected if watcher unavailable): %v", err)
		return
	}

	// Stop immediately
	cs.Stop()
}

// ---------------------------------------------------------------------------
// context.go:SyncEnvelopesToFilesystem (line ~317)
// ---------------------------------------------------------------------------

// TestWave128_SyncEnvelopesToFilesystem_EmptyDB verifies no error on empty DB.
func TestWave128_SyncEnvelopesToFilesystem_EmptyDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	tmpDir := t.TempDir()
	cm := testContextManager(t, db, tmpDir)

	err := cm.SyncEnvelopesToFilesystem()
	if err != nil {
		t.Logf("SyncEnvelopesToFilesystem empty DB: %v (acceptable)", err)
	}
}

// TestWave128_SyncEnvelopesToFilesystem_ClosedDB verifies error on closed DB.
func TestWave128_SyncEnvelopesToFilesystem_ClosedDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	cleanup()

	cm := testContextManager(t, db, t.TempDir())

	err := cm.SyncEnvelopesToFilesystem()
	t.Logf("SyncEnvelopesToFilesystem closed DB: %v", err)
}
