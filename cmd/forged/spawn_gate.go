//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"fmt"
	"log"
	"os"

	"gopkg.in/yaml.v3"
)

// NodeConfig holds resource budget for a single node.
type NodeConfig struct {
	RAMGB        int      `yaml:"ram_gb"`
	MaxAgents    int      `yaml:"max_agents"`
	DeniedModels []string `yaml:"denied_models"`
}

// NodesConfig holds all node budgets loaded from config/nodes.yaml.
type NodesConfig struct {
	Nodes map[string]NodeConfig `yaml:"nodes"`
}

// SpawnGate enforces node resource budgets before agent spawning.
// It checks static config only — live RAM checks are handled by fleet_scaler.go.
type SpawnGate struct {
	config NodesConfig
}

// NewSpawnGate creates a SpawnGate by loading config/nodes.yaml from configPath.
// If the file is missing or unreadable, a permissive gate (no restrictions) is
// returned so that the daemon can still start on unconfigured nodes.
func NewSpawnGate(configPath string) *SpawnGate {
	data, err := os.ReadFile(configPath)
	if err != nil {
		log.Printf("[spawn_gate] WARNING: cannot read %s: %v — using permissive gate (no restrictions)", configPath, err)
		return &SpawnGate{config: NodesConfig{Nodes: map[string]NodeConfig{}}}
	}

	var cfg NodesConfig
	if err := yaml.Unmarshal(data, &cfg); err != nil {
		log.Printf("[spawn_gate] WARNING: cannot parse %s: %v — using permissive gate (no restrictions)", configPath, err)
		return &SpawnGate{config: NodesConfig{Nodes: map[string]NodeConfig{}}}
	}

	if cfg.Nodes == nil {
		cfg.Nodes = map[string]NodeConfig{}
	}

	return &SpawnGate{config: cfg}
}

// CanSpawn checks whether an agent of the given type can be spawned on nodeID
// given the current number of active agents on that node.
//
// Checks (in order):
//  1. Node exists in config — if not, allows with a warning log.
//  2. activeAgentCount < config.MaxAgents — HARD block if at or over limit.
//  3. agentType NOT IN config.DeniedModels — HARD block if denied.
//
// Returns (true, "") if allowed, or (false, reason) if denied.
func (sg *SpawnGate) CanSpawn(agentType, nodeID string, activeAgentCount int) (bool, string) {
	nodeCfg, ok := sg.config.Nodes[nodeID]
	if !ok {
		log.Printf("[spawn_gate] WARNING: node %q not found in config — allowing spawn of %q (permissive fallback)", nodeID, agentType)
		return true, ""
	}

	if nodeCfg.MaxAgents > 0 && activeAgentCount >= nodeCfg.MaxAgents {
		reason := fmt.Sprintf("node %q is at capacity: %d/%d agents active (max_agents=%d)",
			nodeID, activeAgentCount, nodeCfg.MaxAgents, nodeCfg.MaxAgents)
		return false, reason
	}

	for _, denied := range nodeCfg.DeniedModels {
		if denied == agentType {
			reason := fmt.Sprintf("agent type %q is denied on node %q (denied_models policy)", agentType, nodeID)
			return false, reason
		}
	}

	return true, ""
}
