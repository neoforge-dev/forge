//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"testing"
)

// ---------------------------------------------------------------------------
// Wave 162: routing_config.go pure functions
// Targets:
//   inferWorkload          (routing_config.go:96)  — taskType → workload string
//   nodeAllowsWorkload     (routing_config.go:112) — forbidden list check
//   preferredNodesForWeight(routing_config.go:124) — heavy vs light
//   agentBestAtScore       (routing_config.go:133) — best_at substring match
//   stageToWorkload        (routing_config.go:145) — portfolio stage → workload hint
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// inferWorkload
// ---------------------------------------------------------------------------

func TestWave162_InferWorkload_CoverageTask(t *testing.T) {
	if got := inferWorkload("coverage-push"); got != "go-test" {
		t.Errorf("expected go-test, got %q", got)
	}
}

func TestWave162_InferWorkload_TestTask(t *testing.T) {
	if got := inferWorkload("write-tests"); got != "go-test" {
		t.Errorf("expected go-test, got %q", got)
	}
}

func TestWave162_InferWorkload_IOSTask(t *testing.T) {
	if got := inferWorkload("ios-build-fix"); got != "ios-builds" {
		t.Errorf("expected ios-builds, got %q", got)
	}
}

func TestWave162_InferWorkload_XcodeTask(t *testing.T) {
	if got := inferWorkload("xcode-signing"); got != "ios-builds" {
		t.Errorf("expected ios-builds, got %q", got)
	}
}

func TestWave162_InferWorkload_ResearchTask(t *testing.T) {
	if got := inferWorkload("market-research"); got != "research" {
		t.Errorf("expected research, got %q", got)
	}
}

func TestWave162_InferWorkload_AuditTask(t *testing.T) {
	if got := inferWorkload("security-audit"); got != "research" {
		t.Errorf("expected research, got %q", got)
	}
}

func TestWave162_InferWorkload_Default(t *testing.T) {
	if got := inferWorkload("refactor-handler"); got != "complex-edits" {
		t.Errorf("expected complex-edits, got %q", got)
	}
}

// ---------------------------------------------------------------------------
// nodeAllowsWorkload
// ---------------------------------------------------------------------------

func TestWave162_NodeAllowsWorkload_NotForbidden(t *testing.T) {
	node := RoutingNodeEntry{
		ForbiddenWorkloads: []string{"ios-builds"},
	}
	if !nodeAllowsWorkload(node, "go-test") {
		t.Error("expected go-test to be allowed")
	}
}

func TestWave162_NodeAllowsWorkload_Forbidden(t *testing.T) {
	node := RoutingNodeEntry{
		ForbiddenWorkloads: []string{"ios-builds", "xcode"},
	}
	if nodeAllowsWorkload(node, "ios-builds") {
		t.Error("expected ios-builds to be forbidden")
	}
}

func TestWave162_NodeAllowsWorkload_EmptyList(t *testing.T) {
	node := RoutingNodeEntry{}
	if !nodeAllowsWorkload(node, "anything") {
		t.Error("expected workload to be allowed with empty forbidden list")
	}
}

// ---------------------------------------------------------------------------
// preferredNodesForWeight
// ---------------------------------------------------------------------------

func TestWave162_PreferredNodesForWeight_Heavy(t *testing.T) {
	preferred, denied := preferredNodesForWeight("heavy")
	if len(preferred) == 0 {
		t.Error("expected preferred nodes for heavy weight")
	}
	if len(denied) == 0 {
		t.Error("expected denied nodes for heavy weight")
	}
	// sati and nova should be preferred
	found := false
	for _, n := range preferred {
		if n == "sati" {
			found = true
		}
	}
	if !found {
		t.Error("expected sati in preferred nodes for heavy")
	}
}

func TestWave162_PreferredNodesForWeight_Light(t *testing.T) {
	preferred, denied := preferredNodesForWeight("light")
	if len(preferred) != 0 {
		t.Errorf("expected no preferred nodes for light, got %v", preferred)
	}
	if len(denied) != 0 {
		t.Errorf("expected no denied nodes for light, got %v", denied)
	}
}

func TestWave162_PreferredNodesForWeight_Empty(t *testing.T) {
	preferred, denied := preferredNodesForWeight("")
	if len(preferred) != 0 || len(denied) != 0 {
		t.Error("expected no restrictions for empty weight")
	}
}

// ---------------------------------------------------------------------------
// agentBestAtScore
// ---------------------------------------------------------------------------

func TestWave162_AgentBestAtScore_Match(t *testing.T) {
	agent := RoutingAgentEntry{BestAt: []string{"go-test", "coverage"}}
	if got := agentBestAtScore(agent, "go-test"); got != 2 {
		t.Errorf("expected score 2, got %d", got)
	}
}

func TestWave162_AgentBestAtScore_SubstringMatch(t *testing.T) {
	agent := RoutingAgentEntry{BestAt: []string{"go-testing-coverage"}}
	// workload "go-test" is contained in best_at entry "go-testing-coverage"
	if got := agentBestAtScore(agent, "go-test"); got != 2 {
		t.Errorf("expected score 2 for substring match, got %d", got)
	}
}

func TestWave162_AgentBestAtScore_NoMatch(t *testing.T) {
	agent := RoutingAgentEntry{BestAt: []string{"ios-builds", "xcode"}}
	if got := agentBestAtScore(agent, "go-test"); got != 0 {
		t.Errorf("expected score 0, got %d", got)
	}
}

func TestWave162_AgentBestAtScore_EmptyBestAt(t *testing.T) {
	agent := RoutingAgentEntry{}
	if got := agentBestAtScore(agent, "go-test"); got != 0 {
		t.Errorf("expected score 0 for empty BestAt, got %d", got)
	}
}

// ---------------------------------------------------------------------------
// stageToWorkload
// ---------------------------------------------------------------------------

func TestWave162_StageToWorkload_Build(t *testing.T) {
	if got := stageToWorkload("build"); got != "complex-edits" {
		t.Errorf("expected complex-edits, got %q", got)
	}
}

func TestWave162_StageToWorkload_Deploy(t *testing.T) {
	if got := stageToWorkload("deploy"); got != "complex-reasoning" {
		t.Errorf("expected complex-reasoning, got %q", got)
	}
}

func TestWave162_StageToWorkload_Validate(t *testing.T) {
	if got := stageToWorkload("validate"); got != "research" {
		t.Errorf("expected research, got %q", got)
	}
}

func TestWave162_StageToWorkload_Idea(t *testing.T) {
	if got := stageToWorkload("idea"); got != "docs" {
		t.Errorf("expected docs, got %q", got)
	}
}

func TestWave162_StageToWorkload_Unknown(t *testing.T) {
	if got := stageToWorkload("unknown-stage"); got != "" {
		t.Errorf("expected empty for unknown stage, got %q", got)
	}
}

func TestWave162_StageToWorkload_CaseInsensitive(t *testing.T) {
	if got := stageToWorkload("BUILD"); got != "complex-edits" {
		t.Errorf("expected complex-edits for BUILD, got %q", got)
	}
}
