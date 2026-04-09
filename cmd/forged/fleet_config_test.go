//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// writeTempDomainsYAML creates a temporary FORGE_ROOT with a config/domains.yaml
// containing the given YAML content. It sets FORGE_ROOT and resets the global cache.
func writeTempDomainsYAML(t *testing.T, yamlContent string) {
	t.Helper()
	tmpDir := t.TempDir()
	cfgDir := filepath.Join(tmpDir, "config")
	if err := os.MkdirAll(cfgDir, 0o755); err != nil {
		t.Fatalf("mkdir config: %v", err)
	}
	if err := os.WriteFile(filepath.Join(cfgDir, "domains.yaml"), []byte(yamlContent), 0o644); err != nil {
		t.Fatalf("write domains.yaml: %v", err)
	}
	t.Setenv("FORGE_ROOT", tmpDir)

	// Reset global cache so loadFleetConfig re-reads from the new path.
	globalFleetCache.mu.Lock()
	globalFleetCache.registry = nil
	globalFleetCache.path = ""
	globalFleetCache.mu.Unlock()
}

func TestIsAgentAutoAssignable(t *testing.T) {
	writeTempDomainsYAML(t, `
fleet:
  no_auto_assign_agents:
    - prya
    - sati
    - nova
domains:
  codeswiftr-com:
    display_name: CodeSwiftr
    owner: sati
    active: true
    frontend_tier: react
`)

	// Excluded agents should NOT be assignable.
	for _, agent := range []string{"prya", "sati", "nova"} {
		if IsAgentAutoAssignable(agent) {
			t.Errorf("expected %s to be excluded from auto-assign", agent)
		}
	}

	// Case-insensitive check.
	if IsAgentAutoAssignable("PRYA") {
		t.Error("expected PRYA (uppercase) to be excluded from auto-assign")
	}
	if IsAgentAutoAssignable("Sati") {
		t.Error("expected Sati (mixed case) to be excluded from auto-assign")
	}

	// Non-excluded agents should be assignable.
	for _, agent := range []string{"kimi", "pi", "gemini", "minimax", "glm"} {
		if !IsAgentAutoAssignable(agent) {
			t.Errorf("expected %s to be assignable", agent)
		}
	}
}

func TestIsAgentAutoAssignable_EmptyList(t *testing.T) {
	writeTempDomainsYAML(t, `
fleet:
  no_auto_assign_agents: []
domains: {}
`)

	// With an empty exclusion list, every agent should be assignable.
	for _, agent := range []string{"prya", "sati", "nova", "kimi", "pi"} {
		if !IsAgentAutoAssignable(agent) {
			t.Errorf("expected %s to be assignable when exclusion list is empty", agent)
		}
	}
}

func TestIsDomainActive(t *testing.T) {
	writeTempDomainsYAML(t, `
fleet:
  no_auto_assign_agents: []
domains:
  codeswiftr-com:
    display_name: CodeSwiftr
    owner: sati
    active: true
    frontend_tier: react
  thebrightharbor-com:
    display_name: The Bright Harbor
    owner: gaea
    active: false
    frontend_tier: react
`)

	if !IsDomainActive("codeswiftr-com") {
		t.Error("expected codeswiftr-com to be active")
	}

	if IsDomainActive("thebrightharbor-com") {
		t.Error("expected thebrightharbor-com to be inactive")
	}

	// Unknown domain should be treated as active.
	if !IsDomainActive("some-unknown-domain") {
		t.Error("expected unknown domain to be treated as active")
	}
}

func TestNoAutoAssignAgents_ConfigNotFound(t *testing.T) {
	tmpDir := t.TempDir()
	// Point FORGE_ROOT at a directory that does NOT contain config/domains.yaml.
	t.Setenv("FORGE_ROOT", tmpDir)

	// Reset global cache.
	globalFleetCache.mu.Lock()
	globalFleetCache.registry = nil
	globalFleetCache.path = ""
	globalFleetCache.mu.Unlock()

	// Should return empty list gracefully, not panic.
	agents := NoAutoAssignAgents()
	if len(agents) != 0 {
		t.Errorf("expected empty exclusion list when config missing, got %v", agents)
	}

	// All agents should be assignable when config is missing.
	if !IsAgentAutoAssignable("prya") {
		t.Error("expected prya to be assignable when config is missing")
	}
}

// ─── Real config/domains.yaml validation ─────────────────────────────────────

// TestRealDomainsYAML_ParsesWithoutError verifies that the real config/domains.yaml
// file (relative to the repo root) loads and parses without error.
func TestRealDomainsYAML_ParsesWithoutError(t *testing.T) {
	// Navigate from cmd/forged up two levels to the repo root.
	repoRoot := "../../"
	t.Setenv("FORGE_ROOT", repoRoot)

	// Reset cache so loadFleetConfig reads from the real path.
	globalFleetCache.mu.Lock()
	globalFleetCache.registry = nil
	globalFleetCache.path = ""
	globalFleetCache.mu.Unlock()

	reg := loadFleetConfig()
	if reg == nil {
		t.Fatal("loadFleetConfig returned nil for real domains.yaml")
	}
	if len(reg.Domains) == 0 {
		t.Fatal("expected at least one domain in real domains.yaml, got 0")
	}
}

// TestRealDomainsYAML_AllDomainsHaveDisplayName verifies that every domain entry
// in the real config has a non-empty display_name field.
func TestRealDomainsYAML_AllDomainsHaveDisplayName(t *testing.T) {
	repoRoot := "../../"
	t.Setenv("FORGE_ROOT", repoRoot)

	globalFleetCache.mu.Lock()
	globalFleetCache.registry = nil
	globalFleetCache.path = ""
	globalFleetCache.mu.Unlock()

	reg := loadFleetConfig()
	if reg == nil {
		t.Fatal("loadFleetConfig returned nil")
	}

	for key, domain := range reg.Domains {
		if domain.DisplayName == "" {
			t.Errorf("domain %q has empty display_name", key)
		}
	}
}

// TestRealDomainsYAML_NoDuplicateDomainKeys verifies that the domain map keys
// are all unique (YAML parsers enforce this at parse time, but this test ensures
// the Go struct representation has no silent overwrites).
func TestRealDomainsYAML_NoDuplicateDomainKeys(t *testing.T) {
	repoRoot := "../../"
	t.Setenv("FORGE_ROOT", repoRoot)

	globalFleetCache.mu.Lock()
	globalFleetCache.registry = nil
	globalFleetCache.path = ""
	globalFleetCache.mu.Unlock()

	reg := loadFleetConfig()
	if reg == nil {
		t.Fatal("loadFleetConfig returned nil")
	}

	// Build a set of all keys — map iteration in Go is itself unique per key,
	// so if any YAML duplicate silently overwrote an entry, the count will be
	// lower than expected. We cross-check by re-reading the raw YAML and
	// counting top-level domain keys manually.
	configPath := fleetConfigPath()
	data, err := os.ReadFile(configPath)
	if err != nil {
		t.Skipf("cannot open real domains.yaml at %s: %v", configPath, err)
	}

	// Count occurrences of domain keys at the "  key:" indentation level.
	lines := strings.Split(string(data), "\n")
	seen := map[string]int{}
	inDomains := false
	for _, line := range lines {
		trimmed := strings.TrimSpace(line)
		if trimmed == "domains:" {
			inDomains = true
			continue
		}
		if inDomains {
			// A new top-level section ends domains.
			if len(line) > 0 && line[0] != ' ' && !strings.HasPrefix(line, "#") {
				inDomains = false
				continue
			}
			// Domain keys are indented by exactly 2 spaces and end with ":".
			if strings.HasPrefix(line, "  ") && !strings.HasPrefix(line, "   ") {
				if idx := strings.Index(trimmed, ":"); idx > 0 {
					key := trimmed[:idx]
					seen[key]++
				}
			}
		}
	}
	for key, count := range seen {
		if count > 1 {
			t.Errorf("duplicate domain key in domains.yaml: %q appears %d times", key, count)
		}
	}
}

// TestRealDomainsYAML_NoAutoAssignAgentsAreKnownNodes verifies that every agent in
// the no_auto_assign_agents list matches one of the known FORGE node names.
func TestRealDomainsYAML_NoAutoAssignAgentsAreKnownNodes(t *testing.T) {
	repoRoot := "../../"
	t.Setenv("FORGE_ROOT", repoRoot)

	globalFleetCache.mu.Lock()
	globalFleetCache.registry = nil
	globalFleetCache.path = ""
	globalFleetCache.mu.Unlock()

	reg := loadFleetConfig()
	if reg == nil {
		t.Fatal("loadFleetConfig returned nil")
	}

	// Known FORGE node names (from docs/NODE_SETUP.md and domains.yaml preamble).
	knownNodes := map[string]bool{
		"prya": true, "sati": true, "nova": true, "vega": true, "gaea": true,
	}

	for _, agent := range reg.Fleet.NoAutoAssignAgents {
		if !knownNodes[strings.ToLower(agent)] {
			t.Errorf("no_auto_assign_agents contains %q which is not a known FORGE node", agent)
		}
	}
}
