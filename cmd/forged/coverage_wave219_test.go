//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"bytes"
	"os"
	"path/filepath"
	"testing"
)

// ---------------------------------------------------------------------------
// Wave 219: config.go + completion.go pure functions
// Targets:
//   configPaths              (config.go:36)  — env override + home + cwd
//   loadForgeConfig          (config.go:52)  — no file → zero config
//   parseForgeTOML           (config.go:67)  — parses daemon + auth sections
//   NewCompletionManager     (completion.go:16) — non-nil
//   CompletionManager.GenerateBash (completion.go:21) — non-empty
//   CompletionManager.GenerateZsh  (completion.go:78) — non-empty
//   CompletionManager.GenerateFish (completion.go:179) — non-empty
//   CompletionManager.HandleCompletion (completion.go:226) — no panic
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// configPaths
// ---------------------------------------------------------------------------

func TestWave219_ConfigPaths_Default(t *testing.T) {
	paths := configPaths()
	if len(paths) == 0 {
		t.Error("expected at least one config path")
	}
}

func TestWave219_ConfigPaths_EnvOverride(t *testing.T) {
	t.Setenv("FORGE_CONFIG", "/tmp/test-forge.toml")
	paths := configPaths()
	if len(paths) == 0 || paths[0] != "/tmp/test-forge.toml" {
		t.Errorf("expected FORGE_CONFIG to be first path, got %v", paths)
	}
}

func TestWave219_ConfigPaths_NoEnv(t *testing.T) {
	t.Setenv("FORGE_CONFIG", "")
	paths := configPaths()
	// Should include home/.forge/forge.toml and cwd/.forge/forge.toml
	if len(paths) < 1 {
		t.Error("expected at least one path when no env var")
	}
}

// ---------------------------------------------------------------------------
// loadForgeConfig — no file → zero config
// ---------------------------------------------------------------------------

func TestWave219_LoadForgeConfig_NoFile(t *testing.T) {
	// Point FORGE_CONFIG to a nonexistent file
	t.Setenv("FORGE_CONFIG", "/tmp/nonexistent-forge-config-xyz.toml")
	cfg := loadForgeConfig()
	// Zero config — no panic
	_ = cfg
}

// ---------------------------------------------------------------------------
// parseForgeTOML
// ---------------------------------------------------------------------------

func TestWave219_ParseForgeTOML_DaemonSection(t *testing.T) {
	tmp := t.TempDir()
	toml := `[daemon]
port = 9090
ws_port = 9091
db_path = /tmp/test.db
node_id = testnode
forge_root = /tmp/forge
`
	path := filepath.Join(tmp, "forge.toml")
	if err := os.WriteFile(path, []byte(toml), 0644); err != nil {
		t.Fatal(err)
	}
	cfg, err := parseForgeTOML(path)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if cfg.Daemon.Port != 9090 {
		t.Errorf("expected port 9090, got %d", cfg.Daemon.Port)
	}
	if cfg.Daemon.WSPort != 9091 {
		t.Errorf("expected ws_port 9091, got %d", cfg.Daemon.WSPort)
	}
	if cfg.Daemon.DBPath != "/tmp/test.db" {
		t.Errorf("expected db_path /tmp/test.db, got %q", cfg.Daemon.DBPath)
	}
	if cfg.Daemon.NodeID != "testnode" {
		t.Errorf("expected node_id testnode, got %q", cfg.Daemon.NodeID)
	}
}

func TestWave219_ParseForgeTOML_AuthSection(t *testing.T) {
	tmp := t.TempDir()
	toml := `[auth]
mode = api
api_token = secret123
`
	path := filepath.Join(tmp, "forge.toml")
	if err := os.WriteFile(path, []byte(toml), 0644); err != nil {
		t.Fatal(err)
	}
	cfg, err := parseForgeTOML(path)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if cfg.Auth.Mode != "api" {
		t.Errorf("expected mode api, got %q", cfg.Auth.Mode)
	}
	if cfg.Auth.APIToken != "secret123" {
		t.Errorf("expected api_token secret123, got %q", cfg.Auth.APIToken)
	}
}

func TestWave219_ParseForgeTOML_CommentsAndBlanks(t *testing.T) {
	tmp := t.TempDir()
	toml := `# This is a comment

[daemon]
# Another comment
port = 8081

[auth]
mode = local
`
	path := filepath.Join(tmp, "forge.toml")
	if err := os.WriteFile(path, []byte(toml), 0644); err != nil {
		t.Fatal(err)
	}
	cfg, err := parseForgeTOML(path)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if cfg.Daemon.Port != 8081 {
		t.Errorf("expected port 8081, got %d", cfg.Daemon.Port)
	}
	if cfg.Auth.Mode != "local" {
		t.Errorf("expected mode local, got %q", cfg.Auth.Mode)
	}
}

func TestWave219_ParseForgeTOML_QuotedValues(t *testing.T) {
	tmp := t.TempDir()
	toml := `[daemon]
db_path = "/tmp/quoted.db"
node_id = 'singlequote'
`
	path := filepath.Join(tmp, "forge.toml")
	if err := os.WriteFile(path, []byte(toml), 0644); err != nil {
		t.Fatal(err)
	}
	cfg, err := parseForgeTOML(path)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if cfg.Daemon.DBPath != "/tmp/quoted.db" {
		t.Errorf("expected unquoted path, got %q", cfg.Daemon.DBPath)
	}
	if cfg.Daemon.NodeID != "singlequote" {
		t.Errorf("expected unquoted node_id, got %q", cfg.Daemon.NodeID)
	}
}

func TestWave219_ParseForgeTOML_NotFound(t *testing.T) {
	_, err := parseForgeTOML("/nonexistent/path/forge.toml")
	if err == nil {
		t.Error("expected error for nonexistent file")
	}
}

func TestWave219_ParseForgeTOML_InvalidPortIgnored(t *testing.T) {
	tmp := t.TempDir()
	toml := `[daemon]
port = not-a-number
ws_port = 8082
`
	path := filepath.Join(tmp, "forge.toml")
	if err := os.WriteFile(path, []byte(toml), 0644); err != nil {
		t.Fatal(err)
	}
	cfg, err := parseForgeTOML(path)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	// Invalid port stays zero
	if cfg.Daemon.Port != 0 {
		t.Errorf("expected 0 for invalid port, got %d", cfg.Daemon.Port)
	}
	if cfg.Daemon.WSPort != 8082 {
		t.Errorf("expected ws_port 8082, got %d", cfg.Daemon.WSPort)
	}
}

// ---------------------------------------------------------------------------
// NewCompletionManager
// ---------------------------------------------------------------------------

func TestWave219_NewCompletionManager_NonNil(t *testing.T) {
	m := NewCompletionManager()
	if m == nil {
		t.Error("expected non-nil CompletionManager")
	}
}

// ---------------------------------------------------------------------------
// GenerateBash
// ---------------------------------------------------------------------------

func TestWave219_GenerateBash_NonEmpty(t *testing.T) {
	m := NewCompletionManager()
	var buf bytes.Buffer
	if err := m.GenerateBash(&buf); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if buf.Len() == 0 {
		t.Error("expected non-empty bash completion script")
	}
}

// ---------------------------------------------------------------------------
// GenerateZsh
// ---------------------------------------------------------------------------

func TestWave219_GenerateZsh_NonEmpty(t *testing.T) {
	m := NewCompletionManager()
	var buf bytes.Buffer
	if err := m.GenerateZsh(&buf); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if buf.Len() == 0 {
		t.Error("expected non-empty zsh completion script")
	}
}

// ---------------------------------------------------------------------------
// GenerateFish
// ---------------------------------------------------------------------------

func TestWave219_GenerateFish_NonEmpty(t *testing.T) {
	m := NewCompletionManager()
	var buf bytes.Buffer
	if err := m.GenerateFish(&buf); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if buf.Len() == 0 {
		t.Error("expected non-empty fish completion script")
	}
}

// ---------------------------------------------------------------------------
// HandleCompletion — only test paths that don't call os.Exit
// (empty args and unknown shell both call os.Exit(1) — skip those paths)
// ---------------------------------------------------------------------------

func TestWave219_HandleCompletion_Bash_NoPanic(t *testing.T) {
	old := os.Stdout
	defer func() { os.Stdout = old }()
	f, _ := os.OpenFile(os.DevNull, os.O_WRONLY, 0)
	os.Stdout = f
	defer f.Close()

	m := NewCompletionManager()
	m.HandleCompletion([]string{"bash"})
}

func TestWave219_HandleCompletion_Zsh_NoPanic(t *testing.T) {
	old := os.Stdout
	defer func() { os.Stdout = old }()
	f, _ := os.OpenFile(os.DevNull, os.O_WRONLY, 0)
	os.Stdout = f
	defer f.Close()

	m := NewCompletionManager()
	m.HandleCompletion([]string{"zsh"})
}

func TestWave219_HandleCompletion_Fish_NoPanic(t *testing.T) {
	old := os.Stdout
	defer func() { os.Stdout = old }()
	f, _ := os.OpenFile(os.DevNull, os.O_WRONLY, 0)
	os.Stdout = f
	defer f.Close()

	m := NewCompletionManager()
	m.HandleCompletion([]string{"fish"})
}
