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

// RoutingAgentEntry represents a single agent in agent-registry.yaml.
type RoutingAgentEntry struct {
	HomeNode        string   `yaml:"home_node"`
	Provider        string   `yaml:"provider"`
	Model           string   `yaml:"model"`
	CostClass       string   `yaml:"cost_class"`
	RAMGB           float64  `yaml:"ram_gb"`
	Weight          string   `yaml:"weight"`
	ForbiddenNodes  []string `yaml:"forbidden_nodes"`
	BestAt          []string `yaml:"best_at"`
	AvoidFor        []string `yaml:"avoid_for"`
	Status          string   `yaml:"status"`
	Quirks          string   `yaml:"quirks"`
	StagePreference string   `yaml:"stage_preference"`
}

// RoutingAgentConfig is the top-level structure of agent-registry.yaml.
type RoutingAgentConfig struct {
	Agents map[string]RoutingAgentEntry `yaml:"agents"`
}

// RoutingNodeEntry represents a single node in node-registry.yaml.
type RoutingNodeEntry struct {
	Role               string   `yaml:"role"`
	RAMGB              float64  `yaml:"ram_gb"`
	CPUCores           int      `yaml:"cpu_cores"`
	Class              string   `yaml:"class"`
	AllowedWorkloads   []string `yaml:"allowed_workloads"`
	ForbiddenWorkloads []string `yaml:"forbidden_workloads"`
	MaxAgents          int      `yaml:"max_agents"`
	Notes              string   `yaml:"notes"`
}

// RoutingNodeConfig is the top-level structure of node-registry.yaml.
type RoutingNodeConfig struct {
	Nodes map[string]RoutingNodeEntry `yaml:"nodes"`
}

// RoutingBundle holds all parsed routing config files.
type RoutingBundle struct {
	Agents RoutingAgentConfig
	Nodes  RoutingNodeConfig
}

// LoadRoutingBundle reads the three YAML routing config files from configDir
// and returns a parsed RoutingBundle. configDir should point to config/routing/.
func LoadRoutingBundle(configDir string) (*RoutingBundle, error) {
	bundle := &RoutingBundle{}

	agentData, err := os.ReadFile(filepath.Join(configDir, "agent-registry.yaml"))
	if err != nil {
		return nil, fmt.Errorf("read agent-registry.yaml: %w", err)
	}
	if err := yaml.Unmarshal(agentData, &bundle.Agents); err != nil {
		return nil, fmt.Errorf("parse agent-registry.yaml: %w", err)
	}

	nodeData, err := os.ReadFile(filepath.Join(configDir, "node-registry.yaml"))
	if err != nil {
		return nil, fmt.Errorf("read node-registry.yaml: %w", err)
	}
	if err := yaml.Unmarshal(nodeData, &bundle.Nodes); err != nil {
		return nil, fmt.Errorf("parse node-registry.yaml: %w", err)
	}

	// task-envelope-v1.yaml is documentation/schema only; we read it to verify
	// it exists and is valid YAML before accepting the bundle as complete.
	envelopeData, err := os.ReadFile(filepath.Join(configDir, "task-envelope-v1.yaml"))
	if err != nil {
		return nil, fmt.Errorf("read task-envelope-v1.yaml: %w", err)
	}
	var envelopeCheck interface{}
	if err := yaml.Unmarshal(envelopeData, &envelopeCheck); err != nil {
		return nil, fmt.Errorf("parse task-envelope-v1.yaml: %w", err)
	}

	return bundle, nil
}

// inferWorkload maps a free-form task type string to a canonical workload
// category understood by the node registry.
func inferWorkload(taskType string) string {
	t := strings.ToLower(taskType)
	switch {
	case strings.Contains(t, "coverage") || strings.Contains(t, "test"):
		return "go-test"
	case strings.Contains(t, "ios") || strings.Contains(t, "xcode"):
		return "ios-builds"
	case strings.Contains(t, "research") || strings.Contains(t, "audit") || strings.Contains(t, "analysis") || strings.Contains(t, "plan"):
		return "research"
	case strings.Contains(t, "refactor") || strings.Contains(t, "feature") || strings.Contains(t, "implementation") || strings.Contains(t, "scaffold"):
		return "complex-edits"
	case strings.Contains(t, "docs") || strings.Contains(t, "content") || strings.Contains(t, "runbook"):
		return "docs"
	case strings.Contains(t, "triage") || strings.Contains(t, "quick") || strings.Contains(t, "edit") || strings.Contains(t, "format"):
		return "small-edits"
	default:
		return "small-edits"
	}
}

// nodeAllowsWorkload returns true when the node's forbidden_workloads list does
// not contain the given workload.
func nodeAllowsWorkload(node RoutingNodeEntry, workload string) bool {
	for _, fw := range node.ForbiddenWorkloads {
		if fw == workload {
			return false
		}
	}
	return true
}

// preferredNodesForWeight returns (preferred, denied) node lists based on weight.
// "heavy" tasks prefer sati then nova and deny prya/vega/gaea.
// "light" tasks have no preference and no denials.
func preferredNodesForWeight(weight string) (preferred []string, denied []string) {
	if weight == "heavy" {
		return []string{"sati", "nova"}, []string{"prya", "vega", "gaea"}
	}
	return nil, nil
}

// agentBestAtScore returns a positive score when the agent's best_at list
// contains a substring match for workload, 0 otherwise.
func agentBestAtScore(agent RoutingAgentEntry, workload string) int {
	wl := strings.ToLower(workload)
	for _, ba := range agent.BestAt {
		if strings.Contains(strings.ToLower(ba), wl) || strings.Contains(wl, strings.ToLower(ba)) {
			return 2
		}
	}
	return 0
}

// stageToWorkload maps a portfolio stage to a canonical workload hint used
// for agent scoring. Returns "" when the stage has no workload preference.
func stageToWorkload(stage string) string {
	switch strings.ToLower(stage) {
	case "build":
		return "complex-edits"
	case "deploy":
		return "complex-reasoning" // route to safest/most careful agents
	case "validate", "measure":
		return "research"
	case "idea":
		return "docs"
	default:
		return "" // no stage preference
	}
}

// ResolveAgent picks the best agent and its home node given routing constraints.
// portfolioStage is an optional portfolio stage hint (e.g. "build", "deploy").
// Pass "" to disable stage-based scoring.
// Returns (agentName, nodeName, reason, error).
func ResolveAgent(bundle *RoutingBundle, taskType string, weight string, forbiddenNodes []string, portfolioStage string) (string, string, string, error) {
	workload := inferWorkload(taskType)

	preferredNodes, heavyDenied := preferredNodesForWeight(weight)

	// Combine caller-supplied forbidden nodes with weight-derived denied nodes.
	forbidden := make(map[string]bool)
	for _, n := range forbiddenNodes {
		forbidden[n] = true
	}
	for _, n := range heavyDenied {
		forbidden[n] = true
	}

	type candidate struct {
		name  string
		entry RoutingAgentEntry
		score int
	}
	var eligible []candidate

	for name, agent := range bundle.Agents.Agents {
		// Skip non-OK agents.
		if agent.Status != "OK" {
			continue
		}

		node := agent.HomeNode
		if node == "" {
			continue
		}

		// Skip if the agent's own forbidden_nodes list covers its home node
		// (e.g. opencode/kilo explicitly forbid prya).
		agentForbidsOwnNode := false
		for _, fn := range agent.ForbiddenNodes {
			if fn == node {
				agentForbidsOwnNode = true
				break
			}
		}
		if agentForbidsOwnNode {
			continue
		}

		// Skip if the node is in the combined forbidden set.
		if forbidden[node] {
			continue
		}

		// Skip if the node's workload policy forbids this workload.
		if nodeEntry, known := bundle.Nodes.Nodes[node]; known {
			if !nodeAllowsWorkload(nodeEntry, workload) {
				continue
			}
		}

		score := agentBestAtScore(agent, workload)

		// Stage-based scoring: add workload hint from portfolio stage (weight 1 — hint, not override).
		if portfolioStage != "" {
			stageWL := stageToWorkload(portfolioStage)
			if stageWL != "" {
				score += agentBestAtScore(agent, stageWL)
			}
			// Direct stage preference match: boost by +1 on top of workload score.
			if strings.EqualFold(agent.StagePreference, portfolioStage) {
				score++
			}
		}

		// Boost score for preferred nodes (heavy routing).
		for i, pn := range preferredNodes {
			if node == pn {
				score += (len(preferredNodes) - i) * 10
				break
			}
		}

		eligible = append(eligible, candidate{name: name, entry: agent, score: score})
	}

	if len(eligible) == 0 {
		return "", "", "", fmt.Errorf("no eligible agent found")
	}

	// Pick the highest-scoring candidate; tie-break alphabetically for
	// deterministic output.
	best := eligible[0]
	for _, c := range eligible[1:] {
		if c.score > best.score || (c.score == best.score && c.name < best.name) {
			best = c
		}
	}

	reason := "fallback: first OK agent"
	if best.score >= 10 {
		reason = fmt.Sprintf("best_at match: %s (node preference)", workload)
	} else if best.score > 0 {
		reason = fmt.Sprintf("best_at match: %s", workload)
	}
	if portfolioStage != "" && strings.EqualFold(best.entry.StagePreference, portfolioStage) {
		reason += fmt.Sprintf("; stage preference: %s", portfolioStage)
	}

	return best.name, best.entry.HomeNode, reason, nil
}

// routingConfigDir returns the path to config/routing/ relative to FORGE_ROOT,
// falling back to the current working directory if FORGE_ROOT is unset.
func routingConfigDir() string {
	root := os.Getenv("FORGE_ROOT")
	if root == "" {
		root = "."
	}
	return filepath.Join(root, "config", "routing")
}
