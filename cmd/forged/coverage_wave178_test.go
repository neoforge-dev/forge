//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"os"
	"path/filepath"
	"testing"
)

// ---------------------------------------------------------------------------
// Wave 171: config.go — TOML config loading
// Targets:
//   configPaths     (config.go:36) — env var / home / cwd priority
//   loadForgeConfig (config.go:52) — no file → zero config, finds file
//   parseForgeTOML  (config.go:67) — daemon section, auth section, port, node_id
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// configPaths
// ---------------------------------------------------------------------------

func TestWave178_ConfigPaths_WithEnvVar(t *testing.T) {
	t.Setenv("FORGE_CONFIG", "/tmp/custom-forge.toml")
	paths := configPaths()
	if len(paths) == 0 || paths[0] != "/tmp/custom-forge.toml" {
		t.Errorf("expected env var path first, got %v", paths)
	}
}

func TestWave178_ConfigPaths_WithoutEnvVar(t *testing.T) {
	t.Setenv("FORGE_CONFIG", "")
	paths := configPaths()
	if len(paths) == 0 {
		t.Error("expected at least 1 config path without env var")
	}
}

// ---------------------------------------------------------------------------
// loadForgeConfig
// ---------------------------------------------------------------------------

func TestWave178_LoadForgeConfig_NoFile(t *testing.T) {
	// Point FORGE_CONFIG to a non-existent file
	t.Setenv("FORGE_CONFIG", "/tmp/nonexistent-forge-wave171.toml")
	cfg := loadForgeConfig()
	// Zero config returned — no panic
	if cfg.Daemon.Port != 0 {
		t.Errorf("expected zero port, got %d", cfg.Daemon.Port)
	}
}

func TestWave178_LoadForgeConfig_WithFile(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "forge.toml")
	content := `
[daemon]
port = 9090
ws_port = 9091
node_id = "test-wave171"
forge_root = "/tmp/forge"

[auth]
mode = "local"
api_token = "secret-token"
`
	if err := os.WriteFile(path, []byte(content), 0644); err != nil {
		t.Fatalf("write config: %v", err)
	}
	t.Setenv("FORGE_CONFIG", path)

	cfg := loadForgeConfig()
	if cfg.Daemon.Port != 9090 {
		t.Errorf("expected port 9090, got %d", cfg.Daemon.Port)
	}
	if cfg.Daemon.WSPort != 9091 {
		t.Errorf("expected ws_port 9091, got %d", cfg.Daemon.WSPort)
	}
	if cfg.Daemon.NodeID != "test-wave171" {
		t.Errorf("expected node_id 'test-wave171', got %q", cfg.Daemon.NodeID)
	}
	if cfg.Auth.Mode != "local" {
		t.Errorf("expected auth.mode 'local', got %q", cfg.Auth.Mode)
	}
}

// ---------------------------------------------------------------------------
// parseForgeTOML
// ---------------------------------------------------------------------------

func TestWave178_ParseForgeTOML_ValidFile(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "forge.toml")
	content := `
# FORGE config
[daemon]
port = 8081
db_path = "/data/forge.db"

[auth]
mode = "api"
`
	os.WriteFile(path, []byte(content), 0644)

	cfg, err := parseForgeTOML(path)
	if err != nil {
		t.Fatalf("parseForgeTOML: %v", err)
	}
	if cfg.Daemon.Port != 8081 {
		t.Errorf("expected port 8081, got %d", cfg.Daemon.Port)
	}
	if cfg.Daemon.DBPath != "/data/forge.db" {
		t.Errorf("expected db_path, got %q", cfg.Daemon.DBPath)
	}
	if cfg.Auth.Mode != "api" {
		t.Errorf("expected auth mode 'api', got %q", cfg.Auth.Mode)
	}
}

func TestWave178_ParseForgeTOML_NotFound(t *testing.T) {
	_, err := parseForgeTOML("/tmp/nonexistent-wave171-config.toml")
	if err == nil {
		t.Error("expected error for non-existent file")
	}
}

func TestWave178_ParseForgeTOML_EmptyFile(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "empty.toml")
	os.WriteFile(path, []byte(""), 0644)

	cfg, err := parseForgeTOML(path)
	if err != nil {
		t.Fatalf("parseForgeTOML empty: %v", err)
	}
	// Zero config for empty file
	if cfg.Daemon.Port != 0 {
		t.Errorf("expected port 0 for empty file, got %d", cfg.Daemon.Port)
	}
}

func TestWave178_ParseForgeTOML_QuotedValues(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "quoted.toml")
	content := `
[auth]
api_token = "my-secret-token"
`
	os.WriteFile(path, []byte(content), 0644)

	cfg, err := parseForgeTOML(path)
	if err != nil {
		t.Fatalf("parseForgeTOML: %v", err)
	}
	if cfg.Auth.APIToken != "my-secret-token" {
		t.Errorf("expected 'my-secret-token', got %q", cfg.Auth.APIToken)
	}
}

func TestWave178_ParseForgeTOML_CommentsAndBlanks(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "comments.toml")
	content := `
# This is a comment
# Another comment

[daemon]
# port comment
port = 7777
`
	os.WriteFile(path, []byte(content), 0644)

	cfg, err := parseForgeTOML(path)
	if err != nil {
		t.Fatalf("parseForgeTOML: %v", err)
	}
	if cfg.Daemon.Port != 7777 {
		t.Errorf("expected port 7777, got %d", cfg.Daemon.Port)
	}
}
