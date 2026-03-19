// handlers_test.go - Tests for CLI handler functions (Coverage Wave 114c)
package main

import (
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

// ==================== handleUnknownCommand Tests ====================

func TestHandleUnknownCommand(t *testing.T) {
	tests := []struct {
		name     string
		args     []string
		wantExit bool
	}{
		{
			name:     "empty args",
			args:     []string{},
			wantExit: false,
		},
		{
			name:     "flag-like arg",
			args:     []string{"--help"},
			wantExit: false,
		},
		{
			name:     "short flag",
			args:     []string{"-h"},
			wantExit: false,
		},
		{
			name:     "unknown command that is not a plugin",
			args:     []string{"nonexistent-plugin-xyz"},
			wantExit: false, // returns without dispatching
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			// handleUnknownCommand doesn't return anything, just ensures no panic
			handleUnknownCommand(tt.args)
		})
	}
}

// ==================== Plugin Helper Functions Tests ====================

func TestAppendIfMissing(t *testing.T) {
	tests := []struct {
		name     string
		env      []string
		key      string
		value    string
		expected []string
	}{
		{
			name:     "add new key",
			env:      []string{"EXISTING=value"},
			key:      "NEW",
			value:    "newvalue",
			expected: []string{"EXISTING=value", "NEW=newvalue"},
		},
		{
			name:     "key already exists",
			env:      []string{"EXISTING=value", "KEY=old"},
			key:      "KEY",
			value:    "new",
			expected: []string{"EXISTING=value", "KEY=old"},
		},
		{
			name:     "empty env",
			env:      []string{},
			key:      "KEY",
			value:    "value",
			expected: []string{"KEY=value"},
		},
		{
			name:     "key prefix match but different key",
			env:      []string{"KEY_OTHER=value"},
			key:      "KEY",
			value:    "new",
			expected: []string{"KEY_OTHER=value", "KEY=new"},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			result := appendIfMissing(tt.env, tt.key, tt.value)
			if len(result) != len(tt.expected) {
				t.Errorf("appendIfMissing() = %v, want %v", result, tt.expected)
			}
			for i, v := range tt.expected {
				if result[i] != v {
					t.Errorf("appendIfMissing()[%d] = %v, want %v", i, result[i], v)
				}
			}
		})
	}
}

func TestIsExecutable(t *testing.T) {
	tmpDir := t.TempDir()

	// Create executable file
	execFile := filepath.Join(tmpDir, "executable")
	if err := os.WriteFile(execFile, []byte("#!/bin/sh\necho hello"), 0755); err != nil {
		t.Fatalf("failed to create executable: %v", err)
	}

	// Create non-executable file
	nonExecFile := filepath.Join(tmpDir, "nonexecutable")
	if err := os.WriteFile(nonExecFile, []byte("content"), 0644); err != nil {
		t.Fatalf("failed to create non-executable: %v", err)
	}

	// Create directory
	dirPath := filepath.Join(tmpDir, "directory")
	if err := os.Mkdir(dirPath, 0755); err != nil {
		t.Fatalf("failed to create directory: %v", err)
	}

	tests := []struct {
		name     string
		path     string
		expected bool
	}{
		{
			name:     "executable file",
			path:     execFile,
			expected: true,
		},
		{
			name:     "non-executable file",
			path:     nonExecFile,
			expected: false,
		},
		{
			name:     "directory",
			path:     dirPath,
			expected: false,
		},
		{
			name:     "non-existent file",
			path:     filepath.Join(tmpDir, "doesnotexist"),
			expected: false,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			result := isExecutable(tt.path)
			if result != tt.expected {
				t.Errorf("isExecutable(%q) = %v, want %v", tt.path, result, tt.expected)
			}
		})
	}
}

func TestFindPlugin(t *testing.T) {
	// Skip on Windows as executable permissions work differently
	if runtime.GOOS == "windows" {
		t.Skip("Skipping plugin tests on Windows")
	}

	tmpDir := t.TempDir()

	// Create a fake plugin in temp plugins dir
	pluginsDir := filepath.Join(tmpDir, ".forge", "plugins")
	if err := os.MkdirAll(pluginsDir, 0755); err != nil {
		t.Fatalf("failed to create plugins dir: %v", err)
	}

	pluginFile := filepath.Join(pluginsDir, "forge-testplugin")
	if err := os.WriteFile(pluginFile, []byte("#!/bin/sh\necho test"), 0755); err != nil {
		t.Fatalf("failed to create plugin: %v", err)
	}

	// Temporarily override home directory
	oldHome := os.Getenv("HOME")
	os.Setenv("HOME", tmpDir)
	defer os.Setenv("HOME", oldHome)

	tests := []struct {
		name     string
		noun     string
		expected string
	}{
		{
			name:     "plugin exists in plugins dir",
			noun:     "testplugin",
			expected: pluginFile,
		},
		{
			name:     "plugin does not exist",
			noun:     "nonexistent",
			expected: "",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			result := findPlugin(tt.noun)
			if result != tt.expected {
				t.Errorf("findPlugin(%q) = %q, want %q", tt.noun, result, tt.expected)
			}
		})
	}
}

func TestDiscoverPluginsInPath(t *testing.T) {
	// Skip on Windows
	if runtime.GOOS == "windows" {
		t.Skip("Skipping plugin tests on Windows")
	}

	tmpDir := t.TempDir()

	// Create a fake plugins directory and add it to PATH
	binDir := filepath.Join(tmpDir, "bin")
	if err := os.MkdirAll(binDir, 0755); err != nil {
		t.Fatalf("failed to create bin dir: %v", err)
	}

	// Create forge-* executables
	for _, name := range []string{"forge-discovered1", "forge-discovered2"} {
		path := filepath.Join(binDir, name)
		if err := os.WriteFile(path, []byte("#!/bin/sh\necho test"), 0755); err != nil {
			t.Fatalf("failed to create plugin %s: %v", name, err)
		}
	}

	// Create non-forge executable
	normalBin := filepath.Join(binDir, "not-a-plugin")
	if err := os.WriteFile(normalBin, []byte("#!/bin/sh\necho test"), 0755); err != nil {
		t.Fatalf("failed to create normal bin: %v", err)
	}

	// Override PATH
	oldPath := os.Getenv("PATH")
	os.Setenv("PATH", binDir)
	defer os.Setenv("PATH", oldPath)

	// Also test plugins in home directory
	pluginsDir := filepath.Join(tmpDir, ".forge", "plugins")
	if err := os.MkdirAll(pluginsDir, 0755); err != nil {
		t.Fatalf("failed to create plugins dir: %v", err)
	}

	homePlugin := filepath.Join(pluginsDir, "forge-homeplugin")
	if err := os.WriteFile(homePlugin, []byte("#!/bin/sh\necho test"), 0755); err != nil {
		t.Fatalf("failed to create home plugin: %v", err)
	}

	oldHome := os.Getenv("HOME")
	os.Setenv("HOME", tmpDir)
	defer os.Setenv("HOME", oldHome)

	result := discoverPluginsInPath()

	// Should find discovered1, discovered2, and homeplugin
	found := make(map[string]bool)
	for _, name := range result {
		found[name] = true
	}

	if !found["forge-discovered1"] {
		t.Error("expected to find forge-discovered1")
	}
	if !found["forge-discovered2"] {
		t.Error("expected to find forge-discovered2")
	}
	if !found["forge-homeplugin"] {
		t.Error("expected to find forge-homeplugin")
	}
	if found["not-a-plugin"] {
		t.Error("should not find non-forge binaries")
	}
}

// ==================== Config Helper Functions Tests ====================

func TestGetControlPlaneURL(t *testing.T) {
	// Save and clear FORGE_API_URL to ensure clean test environment
	oldAPIURL := os.Getenv("FORGE_API_URL")
	os.Unsetenv("FORGE_API_URL")
	defer os.Setenv("FORGE_API_URL", oldAPIURL)

	tests := []struct {
		name     string
		env      map[string]string
		cfg      *Config
		expected string
	}{
		{
			name:     "default",
			env:      map[string]string{},
			cfg:      nil,
			expected: "http://localhost:8081",
		},
		{
			name: "from env FORGE_API_URL",
			env: map[string]string{
				"FORGE_API_URL": "http://custom:9090",
			},
			cfg:      nil,
			expected: "http://custom:9090",
		},
		{
			name: "from config",
			env:  map[string]string{},
			cfg: &Config{
				APIURL: "http://config:8080",
			},
			expected: "http://config:8080",
		},
		{
			name: "env overrides config",
			env: map[string]string{
				"FORGE_API_URL": "http://env:7070",
			},
			cfg: &Config{
				APIURL: "http://config:8080",
			},
			expected: "http://env:7070",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			// Clear FORGE_API_URL before each test
			os.Unsetenv("FORGE_API_URL")

			// Set env vars
			for k, v := range tt.env {
				os.Setenv(k, v)
				defer os.Unsetenv(k)
			}

			// Set config
			oldCfg := cfg
			cfg = tt.cfg
			defer func() { cfg = oldCfg }()

			result := getControlPlaneURL()
			if result != tt.expected {
				t.Errorf("getControlPlaneURL() = %q, want %q", result, tt.expected)
			}
		})
	}
}

func TestGetNodeID(t *testing.T) {
	hostname, _ := os.Hostname()

	tests := []struct {
		name     string
		env      map[string]string
		cfg      *Config
		expected string
	}{
		{
			name:     "default falls back to hostname",
			env:      map[string]string{},
			cfg:      nil,
			expected: hostname,
		},
		{
			name: "from env FORGE_NODE_ID",
			env: map[string]string{
				"FORGE_NODE_ID": "custom-node",
			},
			cfg:      nil,
			expected: "custom-node",
		},
		{
			name: "from config",
			env:  map[string]string{},
			cfg: &Config{
				NodeID: "config-node",
			},
			expected: "config-node",
		},
		{
			name: "env overrides config",
			env: map[string]string{
				"FORGE_NODE_ID": "env-node",
			},
			cfg: &Config{
				NodeID: "config-node",
			},
			expected: "env-node",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			// Set env vars
			for k, v := range tt.env {
				old := os.Getenv(k)
				os.Setenv(k, v)
				defer os.Setenv(k, old)
			}

			// Set config
			oldCfg := cfg
			cfg = tt.cfg
			defer func() { cfg = oldCfg }()

			result := getNodeID()
			if result != tt.expected {
				t.Errorf("getNodeID() = %q, want %q", result, tt.expected)
			}
		})
	}
}

func TestGetAuthToken(t *testing.T) {
	tests := []struct {
		name     string
		env      map[string]string
		expected string
	}{
		{
			name:     "default empty",
			env:      map[string]string{},
			expected: "",
		},
		{
			name: "from FORGE_TOKEN",
			env: map[string]string{
				"FORGE_TOKEN": "secret-token",
			},
			expected: "secret-token",
		},
		{
			name: "from FORGE_WEBHOOK_TOKEN",
			env: map[string]string{
				"FORGE_WEBHOOK_TOKEN": "webhook-token",
			},
			expected: "webhook-token",
		},
		{
			name: "FORGE_TOKEN takes precedence",
			env: map[string]string{
				"FORGE_TOKEN":          "primary-token",
				"FORGE_WEBHOOK_TOKEN":  "secondary-token",
			},
			expected: "primary-token",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			// Clear relevant env vars first
			oldToken := os.Getenv("FORGE_TOKEN")
			oldWebhook := os.Getenv("FORGE_WEBHOOK_TOKEN")
			os.Unsetenv("FORGE_TOKEN")
			os.Unsetenv("FORGE_WEBHOOK_TOKEN")
			defer func() {
				os.Setenv("FORGE_TOKEN", oldToken)
				os.Setenv("FORGE_WEBHOOK_TOKEN", oldWebhook)
			}()

			// Set env vars
			for k, v := range tt.env {
				os.Setenv(k, v)
			}

			result := getAuthToken()
			if result != tt.expected {
				t.Errorf("getAuthToken() = %q, want %q", result, tt.expected)
			}
		})
	}
}

// ==================== Plugin Registry Tests ====================

func TestPluginsRegistryPath(t *testing.T) {
	tmpDir := t.TempDir()
	oldHome := os.Getenv("HOME")
	os.Setenv("HOME", tmpDir)
	defer os.Setenv("HOME", oldHome)

	path, err := pluginsRegistryPath()
	if err != nil {
		t.Fatalf("pluginsRegistryPath() error = %v", err)
	}

	expectedSuffix := filepath.Join(".forge", "plugins", "registry.toml")
	if !strings.HasSuffix(path, expectedSuffix) {
		t.Errorf("pluginsRegistryPath() = %q, should end with %q", path, expectedSuffix)
	}
}

func TestEnsurePluginsDir(t *testing.T) {
	tmpDir := t.TempDir()
	oldHome := os.Getenv("HOME")
	os.Setenv("HOME", tmpDir)
	defer os.Setenv("HOME", oldHome)

	path, err := ensurePluginsDir()
	if err != nil {
		t.Fatalf("ensurePluginsDir() error = %v", err)
	}

	expectedSuffix := filepath.Join(".forge", "plugins")
	if !strings.HasSuffix(path, expectedSuffix) {
		t.Errorf("ensurePluginsDir() = %q, should end with %q", path, expectedSuffix)
	}

	// Verify directory was created
	if _, err := os.Stat(path); os.IsNotExist(err) {
		t.Error("ensurePluginsDir() did not create the directory")
	}

	// Call again - should not fail if directory exists
	path2, err := ensurePluginsDir()
	if err != nil {
		t.Fatalf("ensurePluginsDir() second call error = %v", err)
	}
	if path != path2 {
		t.Error("ensurePluginsDir() returned different paths on second call")
	}
}

func TestLoadPluginRegistry(t *testing.T) {
	tmpDir := t.TempDir()
	oldHome := os.Getenv("HOME")
	os.Setenv("HOME", tmpDir)
	defer os.Setenv("HOME", oldHome)

	// Test with non-existent registry
	plugins, err := loadPluginRegistry()
	if err != nil {
		t.Fatalf("loadPluginRegistry() error = %v", err)
	}
	if plugins != nil {
		t.Errorf("loadPluginRegistry() = %v, want nil for non-existent registry", plugins)
	}

	// Create registry directory and file
	pluginsDir := filepath.Join(tmpDir, ".forge", "plugins")
	if err := os.MkdirAll(pluginsDir, 0755); err != nil {
		t.Fatalf("failed to create plugins dir: %v", err)
	}

	registryContent := `[[plugin]]
name = "test-plugin"
binary = "forge-test"
version = "1.0.0"
source = "./test"
`
	registryPath := filepath.Join(pluginsDir, "registry.toml")
	if err := os.WriteFile(registryPath, []byte(registryContent), 0644); err != nil {
		t.Fatalf("failed to write registry: %v", err)
	}

	// Test with existing registry
	plugins, err = loadPluginRegistry()
	if err != nil {
		t.Fatalf("loadPluginRegistry() error = %v", err)
	}
	if len(plugins) != 1 {
		t.Errorf("loadPluginRegistry() returned %d plugins, want 1", len(plugins))
	}
	if len(plugins) > 0 && plugins[0].Name != "test-plugin" {
		t.Errorf("loadPluginRegistry() plugin name = %q, want %q", plugins[0].Name, "test-plugin")
	}
}

func TestRegisterAndUnregisterPlugin(t *testing.T) {
	tmpDir := t.TempDir()
	oldHome := os.Getenv("HOME")
	os.Setenv("HOME", tmpDir)
	defer os.Setenv("HOME", oldHome)

	// Ensure plugins directory exists (registerPlugin doesn't create it)
	pluginsDir := filepath.Join(tmpDir, ".forge", "plugins")
	if err := os.MkdirAll(pluginsDir, 0755); err != nil {
		t.Fatalf("failed to create plugins dir: %v", err)
	}

	// Register a plugin
	entry := PluginEntry{
		Name:    "my-plugin",
		Binary:  "forge-my-plugin",
		Version: "1.0.0",
		Source:  "./my-plugin",
	}

	if err := registerPlugin(entry); err != nil {
		t.Fatalf("registerPlugin() error = %v", err)
	}

	// Verify it was registered
	plugins, err := loadPluginRegistry()
	if err != nil {
		t.Fatalf("loadPluginRegistry() error = %v", err)
	}
	if len(plugins) != 1 {
		t.Errorf("expected 1 plugin, got %d", len(plugins))
	}

	// Register another plugin
	entry2 := PluginEntry{
		Name:    "another-plugin",
		Binary:  "forge-another",
		Version: "2.0.0",
		Source:  "./another",
	}

	if err := registerPlugin(entry2); err != nil {
		t.Fatalf("registerPlugin() error = %v", err)
	}

	plugins, _ = loadPluginRegistry()
	if len(plugins) != 2 {
		t.Errorf("expected 2 plugins, got %d", len(plugins))
	}

	// Update existing plugin
	entryUpdated := PluginEntry{
		Name:    "my-plugin",
		Binary:  "forge-my-plugin",
		Version: "1.1.0",
		Source:  "./my-plugin-v2",
	}

	if err := registerPlugin(entryUpdated); err != nil {
		t.Fatalf("registerPlugin() update error = %v", err)
	}

	plugins, _ = loadPluginRegistry()
	if len(plugins) != 2 {
		t.Errorf("expected 2 plugins after update, got %d", len(plugins))
	}

	// Find updated plugin
	var found bool
	for _, p := range plugins {
		if p.Name == "my-plugin" && p.Version == "1.1.0" {
			found = true
			break
		}
	}
	if !found {
		t.Error("plugin was not properly updated")
	}

	// Unregister a plugin
	if err := unregisterPlugin("my-plugin"); err != nil {
		t.Fatalf("unregisterPlugin() error = %v", err)
	}

	plugins, _ = loadPluginRegistry()
	if len(plugins) != 1 {
		t.Errorf("expected 1 plugin after unregister, got %d", len(plugins))
	}
	if len(plugins) > 0 && plugins[0].Name != "another-plugin" {
		t.Errorf("wrong plugin remaining: %q", plugins[0].Name)
	}
}

func TestGetPluginInfo(t *testing.T) {
	// Skip on Windows
	if runtime.GOOS == "windows" {
		t.Skip("Skipping plugin tests on Windows")
	}

	tmpDir := t.TempDir()

	// Create a mock plugin that responds to --forge-plugin-info
	pluginPath := filepath.Join(tmpDir, "forge-mock")
	pluginScript := `#!/bin/sh
if [ "$1" = "--forge-plugin-info" ]; then
	echo '{"name": "mock-plugin", "version": "1.0.0", "description": "A mock plugin"}'
else
	echo "Usage: $0 --forge-plugin-info"
	exit 1
fi
`
	if err := os.WriteFile(pluginPath, []byte(pluginScript), 0755); err != nil {
		t.Fatalf("failed to create mock plugin: %v", err)
	}

	info, err := getPluginInfo(pluginPath)
	if err != nil {
		t.Fatalf("getPluginInfo() error = %v", err)
	}

	if info["name"] != "mock-plugin" {
		t.Errorf("getPluginInfo() name = %v, want %v", info["name"], "mock-plugin")
	}
	if info["version"] != "1.0.0" {
		t.Errorf("getPluginInfo() version = %v, want %v", info["version"], "1.0.0")
	}

	// Test with non-existent plugin
	_, err = getPluginInfo(filepath.Join(tmpDir, "nonexistent"))
	if err == nil {
		t.Error("getPluginInfo() should error for non-existent plugin")
	}

	// Test with plugin that doesn't support --forge-plugin-info
	badPlugin := filepath.Join(tmpDir, "forge-bad")
	if err := os.WriteFile(badPlugin, []byte("#!/bin/sh\necho error >&2\nexit 1"), 0755); err != nil {
		t.Fatalf("failed to create bad plugin: %v", err)
	}

	_, err = getPluginInfo(badPlugin)
	if err == nil {
		t.Error("getPluginInfo() should error for plugin without --forge-plugin-info support")
	}
}
