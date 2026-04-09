//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// writeNodesYAML writes a nodes.yaml fixture to a temp dir and returns the path.
func writeNodesYAML(t *testing.T, content string) string {
	t.Helper()
	dir := t.TempDir()
	path := filepath.Join(dir, "nodes.yaml")
	require.NoError(t, os.WriteFile(path, []byte(content), 0o644))
	return path
}

const testNodesYAML = `
nodes:
  prya:
    ram_gb: 16
    max_agents: 2
    denied_models: [opencode, kilo, amp]
  sati:
    ram_gb: 64
    max_agents: 6
    denied_models: []
  nova:
    ram_gb: 48
    max_agents: 4
    denied_models: []
`

// TestCanSpawn_AllowedAgent verifies a normal spawn is permitted when the node
// has capacity and the agent type is not denied.
func TestCanSpawn_AllowedAgent(t *testing.T) {
	path := writeNodesYAML(t, testNodesYAML)
	gate := NewSpawnGate(path)

	allowed, reason := gate.CanSpawn("kimi", "sati", 3)
	assert.True(t, allowed, "expected kimi on sati with 3/6 active to be allowed")
	assert.Empty(t, reason)
}

// TestCanSpawn_DeniedModel verifies that a denied model type is blocked.
func TestCanSpawn_DeniedModel(t *testing.T) {
	path := writeNodesYAML(t, testNodesYAML)
	gate := NewSpawnGate(path)

	allowed, reason := gate.CanSpawn("opencode", "prya", 0)
	assert.False(t, allowed, "expected opencode on prya to be denied")
	assert.Contains(t, reason, "opencode")
	assert.Contains(t, reason, "prya")
}

// TestCanSpawn_MaxAgentsReached verifies that spawning is blocked when the node
// is at its agent capacity.
func TestCanSpawn_MaxAgentsReached(t *testing.T) {
	path := writeNodesYAML(t, testNodesYAML)
	gate := NewSpawnGate(path)

	// prya max_agents=2; passing activeAgentCount=2 should be denied.
	allowed, reason := gate.CanSpawn("kimi", "prya", 2)
	assert.False(t, allowed, "expected spawn to be denied when at max capacity")
	assert.Contains(t, reason, "capacity")
	assert.Contains(t, reason, "prya")
}

// TestCanSpawn_UnknownNode verifies that an unknown node is allowed with a log
// warning (permissive fallback).
func TestCanSpawn_UnknownNode(t *testing.T) {
	path := writeNodesYAML(t, testNodesYAML)
	gate := NewSpawnGate(path)

	allowed, reason := gate.CanSpawn("kimi", "unknown-node-xyz", 0)
	assert.True(t, allowed, "expected unknown node to be allowed (permissive fallback)")
	assert.Empty(t, reason)
}

// TestCanSpawn_EmptyConfig verifies that a gate with no config is fully permissive.
func TestCanSpawn_EmptyConfig(t *testing.T) {
	path := writeNodesYAML(t, "nodes: {}")
	gate := NewSpawnGate(path)

	allowed, reason := gate.CanSpawn("opencode", "prya", 99)
	assert.True(t, allowed, "expected empty config to be fully permissive")
	assert.Empty(t, reason)
}

// TestNewSpawnGate_LoadsConfig verifies that nodes.yaml is parsed correctly and
// the resulting gate reflects the configured budgets.
func TestNewSpawnGate_LoadsConfig(t *testing.T) {
	path := writeNodesYAML(t, testNodesYAML)
	gate := NewSpawnGate(path)

	require.NotNil(t, gate)
	require.NotNil(t, gate.config.Nodes)

	prya, ok := gate.config.Nodes["prya"]
	require.True(t, ok, "prya node must be present")
	assert.Equal(t, 16, prya.RAMGB)
	assert.Equal(t, 2, prya.MaxAgents)
	assert.ElementsMatch(t, []string{"opencode", "kilo", "amp"}, prya.DeniedModels)

	sati, ok := gate.config.Nodes["sati"]
	require.True(t, ok, "sati node must be present")
	assert.Equal(t, 64, sati.RAMGB)
	assert.Equal(t, 6, sati.MaxAgents)
	assert.Empty(t, sati.DeniedModels)
}

// TestNewSpawnGate_MissingFile verifies that a missing config file produces a
// permissive gate (no restrictions) rather than a panic or error.
func TestNewSpawnGate_MissingFile(t *testing.T) {
	gate := NewSpawnGate("/nonexistent/path/nodes.yaml")
	require.NotNil(t, gate)

	// Permissive: any agent on any node with any count should be allowed.
	allowed, reason := gate.CanSpawn("opencode", "prya", 100)
	assert.True(t, allowed, "missing config must produce permissive gate")
	assert.Empty(t, reason)
}
