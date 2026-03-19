//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"gopkg.in/yaml.v3"
)

// AgentRole defines a factory role with preferred agents and stage affinity.
type AgentRole struct {
	Description     string   `yaml:"description"`
	BestForStages   []string `yaml:"best_for_stages"`
	TaskTypes       []string `yaml:"task_types"`
	PreferredAgents []string `yaml:"preferred_agents"`
}

// AgentRoleConfig is the top-level config file parsed from agent-roles.yaml.
type AgentRoleConfig struct {
	Roles map[string]AgentRole `yaml:"roles"`
}

// LoadAgentRoles reads the agent-roles.yaml file at configPath and returns
// a parsed AgentRoleConfig. configPath must point directly to the YAML file.
func LoadAgentRoles(configPath string) (*AgentRoleConfig, error) {
	data, err := os.ReadFile(configPath)
	if err != nil {
		return nil, fmt.Errorf("read agent-roles.yaml: %w", err)
	}

	var cfg AgentRoleConfig
	if err := yaml.Unmarshal(data, &cfg); err != nil {
		return nil, fmt.Errorf("parse agent-roles.yaml: %w", err)
	}

	if cfg.Roles == nil {
		cfg.Roles = make(map[string]AgentRole)
	}

	return &cfg, nil
}

// ResolveRoleForTask determines which role should handle a task based on the
// product's portfolio stage and the task type.
//
// Selection algorithm:
//  1. Find roles where stage is in best_for_stages.
//  2. Among those, find roles where taskType is in task_types.
//  3. If multiple match, pick the most specific (fewest stages).
//  4. If no match by both stage+type, fall back to stage-only match.
//  5. If still no match, return "builder" as the safe default.
func ResolveRoleForTask(roles *AgentRoleConfig, stage string, taskType string) string {
	if roles == nil || len(roles.Roles) == 0 {
		return "builder"
	}

	stageLower := strings.ToLower(strings.TrimSpace(stage))
	taskLower := strings.ToLower(strings.TrimSpace(taskType))

	// Pass 1: roles matching both stage AND task type.
	var fullNames []string
	var fullCounts []int
	for name, role := range roles.Roles {
		if !roleMatchesStage(role, stageLower) {
			continue
		}
		if !roleMatchesTaskType(role, taskLower) {
			continue
		}
		fullNames = append(fullNames, name)
		fullCounts = append(fullCounts, len(role.BestForStages))
	}

	if len(fullNames) > 0 {
		return pickMostSpecificByName(fullNames, fullCounts)
	}

	// Pass 2: roles matching stage only (task type broadening fallback).
	var stageNames []string
	var stageCounts []int
	for name, role := range roles.Roles {
		if !roleMatchesStage(role, stageLower) {
			continue
		}
		stageNames = append(stageNames, name)
		stageCounts = append(stageCounts, len(role.BestForStages))
	}

	if len(stageNames) > 0 {
		return pickMostSpecificByName(stageNames, stageCounts)
	}

	return "builder"
}

// roleMatchesStage returns true when stage appears (case-insensitive) in
// role.BestForStages, or when BestForStages is empty (matches all stages).
func roleMatchesStage(role AgentRole, stage string) bool {
	if len(role.BestForStages) == 0 {
		return true
	}
	for _, s := range role.BestForStages {
		if strings.EqualFold(s, stage) {
			return true
		}
	}
	return false
}

// roleMatchesTaskType returns true when taskType appears (case-insensitive)
// in role.TaskTypes, or when TaskTypes is empty (matches all task types).
func roleMatchesTaskType(role AgentRole, taskType string) bool {
	if len(role.TaskTypes) == 0 {
		return true
	}
	for _, tt := range role.TaskTypes {
		if strings.EqualFold(tt, taskType) {
			return true
		}
	}
	return false
}

// pickMostSpecificByName selects the role name with the lowest stageCount
// (most targeted scope). Ties are broken alphabetically for determinism.
// names and counts must be the same length and non-empty.
func pickMostSpecificByName(names []string, counts []int) string {
	bestName := names[0]
	bestCount := counts[0]
	for i := 1; i < len(names); i++ {
		if counts[i] < bestCount ||
			(counts[i] == bestCount && names[i] < bestName) {
			bestName = names[i]
			bestCount = counts[i]
		}
	}
	return bestName
}

// PreferredAgentsForRole returns the list of preferred agents for the named
// role. Returns nil when the role does not exist.
func PreferredAgentsForRole(roles *AgentRoleConfig, roleName string) []string {
	if roles == nil {
		return nil
	}
	role, ok := roles.Roles[roleName]
	if !ok {
		return nil
	}
	return role.PreferredAgents
}

// agentRolesConfigPath returns the canonical path to agent-roles.yaml,
// respecting FORGE_ROOT if set.
func agentRolesConfigPath() string {
	root := os.Getenv("FORGE_ROOT")
	if root == "" {
		root = "."
	}
	return filepath.Join(root, "config", "routing", "agent-roles.yaml")
}
