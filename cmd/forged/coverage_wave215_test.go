//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"testing"
)

// ---------------------------------------------------------------------------
// Wave 215: routing_config.go pure functions
// Targets:
//   inferWorkload           (routing_config.go:96)  — all branches
//   nodeAllowsWorkload      (routing_config.go:112) — allowed/denied
//   preferredNodesForWeight (routing_config.go:124) — heavy/light
//   agentBestAtScore        (routing_config.go:133) — match/no-match
//   stageToWorkload         (routing_config.go:145) — all stages + default
//   routingConfigDir        (routing_config.go:274)
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// inferWorkload
// ---------------------------------------------------------------------------

func TestWave215_InferWorkload_Coverage(t *testing.T) {
	if got := inferWorkload("coverage-wave"); got != "go-test" {
		t.Errorf("expected 'go-test', got %q", got)
	}
}

func TestWave215_InferWorkload_Test(t *testing.T) {
	if got := inferWorkload("test-task"); got != "go-test" {
		t.Errorf("expected 'go-test' for test, got %q", got)
	}
}

func TestWave215_InferWorkload_IOS(t *testing.T) {
	if got := inferWorkload("ios-build"); got != "ios-builds" {
		t.Errorf("expected 'ios-builds', got %q", got)
	}
}

func TestWave215_InferWorkload_Research(t *testing.T) {
	if got := inferWorkload("research-task"); got != "research" {
		t.Errorf("expected 'research', got %q", got)
	}
}

func TestWave215_InferWorkload_Default(t *testing.T) {
	if got := inferWorkload("unknown-type"); got != "small-edits" {
		t.Errorf("expected 'small-edits' default, got %q", got)
	}
}

// ---------------------------------------------------------------------------
// nodeAllowsWorkload
// ---------------------------------------------------------------------------

func TestWave215_NodeAllowsWorkload_Allowed(t *testing.T) {
	node := RoutingNodeEntry{ForbiddenWorkloads: []string{"ios-builds"}}
	if !nodeAllowsWorkload(node, "go-test") {
		t.Error("expected go-test to be allowed")
	}
}

func TestWave215_NodeAllowsWorkload_Denied(t *testing.T) {
	node := RoutingNodeEntry{ForbiddenWorkloads: []string{"ios-builds", "go-test"}}
	if nodeAllowsWorkload(node, "ios-builds") {
		t.Error("expected ios-builds to be denied")
	}
}

func TestWave215_NodeAllowsWorkload_EmptyForbidden(t *testing.T) {
	node := RoutingNodeEntry{ForbiddenWorkloads: nil}
	if !nodeAllowsWorkload(node, "any-workload") {
		t.Error("expected all workloads allowed when no forbidden list")
	}
}

// ---------------------------------------------------------------------------
// preferredNodesForWeight
// ---------------------------------------------------------------------------

func TestWave215_PreferredNodesForWeight_Heavy(t *testing.T) {
	pref, denied := preferredNodesForWeight("heavy")
	if len(pref) == 0 {
		t.Error("expected preferred nodes for heavy weight")
	}
	if len(denied) == 0 {
		t.Error("expected denied nodes for heavy weight")
	}
}

func TestWave215_PreferredNodesForWeight_Light(t *testing.T) {
	pref, denied := preferredNodesForWeight("light")
	if len(pref) != 0 || len(denied) != 0 {
		t.Error("expected no preferred/denied nodes for light weight")
	}
}

func TestWave215_PreferredNodesForWeight_Empty(t *testing.T) {
	pref, denied := preferredNodesForWeight("")
	if len(pref) != 0 || len(denied) != 0 {
		t.Error("expected no preferred/denied nodes for empty weight")
	}
}

// ---------------------------------------------------------------------------
// agentBestAtScore
// ---------------------------------------------------------------------------

func TestWave215_AgentBestAtScore_Match(t *testing.T) {
	agent := RoutingAgentEntry{BestAt: []string{"go-test", "coverage"}}
	score := agentBestAtScore(agent, "go-test")
	if score <= 0 {
		t.Errorf("expected positive score for matching workload, got %d", score)
	}
}

func TestWave215_AgentBestAtScore_NoMatch(t *testing.T) {
	agent := RoutingAgentEntry{BestAt: []string{"ios-builds"}}
	score := agentBestAtScore(agent, "go-test")
	if score != 0 {
		t.Errorf("expected 0 for non-matching workload, got %d", score)
	}
}

func TestWave215_AgentBestAtScore_EmptyBestAt(t *testing.T) {
	agent := RoutingAgentEntry{BestAt: nil}
	score := agentBestAtScore(agent, "go-test")
	if score != 0 {
		t.Errorf("expected 0 for empty BestAt, got %d", score)
	}
}

// ---------------------------------------------------------------------------
// stageToWorkload
// ---------------------------------------------------------------------------

func TestWave215_StageToWorkload_Build(t *testing.T) {
	if got := stageToWorkload("build"); got != "complex-edits" {
		t.Errorf("expected 'complex-edits', got %q", got)
	}
}

func TestWave215_StageToWorkload_Deploy(t *testing.T) {
	if got := stageToWorkload("deploy"); got != "complex-reasoning" {
		t.Errorf("expected 'complex-reasoning', got %q", got)
	}
}

func TestWave215_StageToWorkload_Validate(t *testing.T) {
	if got := stageToWorkload("validate"); got != "research" {
		t.Errorf("expected 'research', got %q", got)
	}
}

func TestWave215_StageToWorkload_Idea(t *testing.T) {
	if got := stageToWorkload("idea"); got != "docs" {
		t.Errorf("expected 'docs', got %q", got)
	}
}

func TestWave215_StageToWorkload_Default(t *testing.T) {
	if got := stageToWorkload("unknown-stage"); got != "" {
		t.Errorf("expected empty string for unknown stage, got %q", got)
	}
}

// ---------------------------------------------------------------------------
// routingConfigDir
// ---------------------------------------------------------------------------

func TestWave215_RoutingConfigDir_NonEmpty(t *testing.T) {
	dir := routingConfigDir()
	if dir == "" {
		t.Error("expected non-empty routing config dir")
	}
}
