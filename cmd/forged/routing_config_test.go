//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"testing"
)

func TestInferWorkload(t *testing.T) {
	tests := []struct {
		input string
		want  string
	}{
		{"coverage", "go-test"},
		{"test", "go-test"},
		{"unit-test", "go-test"},
		{"research", "research"},
		{"audit", "research"},
		{"analysis", "research"},
		{"plan", "research"},
		{"ios", "ios-builds"},
		{"xcode", "ios-builds"},
		{"feature", "complex-edits"},
		{"refactor", "complex-edits"},
		{"implementation", "complex-edits"},
		{"scaffold", "complex-edits"},
		{"docs", "docs"},
		{"content", "docs"},
		{"runbook", "docs"},
		{"triage", "small-edits"},
		{"edit", "small-edits"},
		{"quick", "small-edits"},
		{"format", "small-edits"},
		{"", "small-edits"},
	}
	for _, tt := range tests {
		got := inferWorkload(tt.input)
		if got != tt.want {
			t.Errorf("inferWorkload(%q) = %q, want %q", tt.input, got, tt.want)
		}
	}
}

func TestPreferredNodesForWeight(t *testing.T) {
	preferred, denied := preferredNodesForWeight("heavy")
	if len(preferred) == 0 {
		t.Error("heavy should have preferred nodes")
	}
	if len(denied) == 0 {
		t.Error("heavy should have denied nodes")
	}
	// prya must be denied for heavy workloads
	foundPrya := false
	for _, n := range denied {
		if n == "prya" {
			foundPrya = true
		}
	}
	if !foundPrya {
		t.Error("prya must be denied for heavy workloads")
	}

	preferred, denied = preferredNodesForWeight("light")
	if len(preferred) != 0 || len(denied) != 0 {
		t.Error("light should have no preferred or denied nodes")
	}
}

func TestResolveAgent_NoBundle(t *testing.T) {
	// Empty bundle should return no eligible agent
	bundle := &RoutingBundle{}
	_, _, _, err := ResolveAgent(bundle, "coverage", "light", nil, "")
	if err == nil {
		t.Error("expected error for empty bundle, got nil")
	}
}

func TestResolveAgent_OfflineSkipped(t *testing.T) {
	bundle := &RoutingBundle{
		Agents: RoutingAgentConfig{
			Agents: map[string]RoutingAgentEntry{
				"opencode": {
					HomeNode: "sati",
					Status:   "OFFLINE",
					BestAt:   []string{"complex-reasoning"},
					Weight:   "heavy",
				},
			},
		},
	}
	_, _, _, err := ResolveAgent(bundle, "coverage", "light", nil, "")
	if err == nil {
		t.Error("expected error: OFFLINE agent should be skipped")
	}
}

func TestResolveAgent_BestAtMatch(t *testing.T) {
	bundle := &RoutingBundle{
		Agents: RoutingAgentConfig{
			Agents: map[string]RoutingAgentEntry{
				"kimi": {
					HomeNode: "sati",
					Status:   "OK",
					BestAt:   []string{"complex-edits", "multi-file-refactors", "research"},
					Weight:   "light",
				},
				"gemini": {
					HomeNode: "nova",
					Status:   "OK",
					BestAt:   []string{"research", "audits", "code-review"},
					Weight:   "light",
				},
			},
		},
	}
	// "research" should match gemini or kimi — both have it in best_at
	agent, node, _, err := ResolveAgent(bundle, "research", "light", nil, "")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if agent == "" || node == "" {
		t.Error("expected non-empty agent and node")
	}
}

func TestResolveAgent_HeavyDeniedPrya(t *testing.T) {
	bundle := &RoutingBundle{
		Agents: RoutingAgentConfig{
			Agents: map[string]RoutingAgentEntry{
				"minimax": {
					HomeNode: "prya",
					Status:   "OK",
					BestAt:   []string{"docs"},
					Weight:   "light",
				},
				"kimi": {
					HomeNode: "sati",
					Status:   "OK",
					BestAt:   []string{"complex-edits"},
					Weight:   "light",
				},
			},
		},
	}
	// heavy task should not go to prya (minimax's home node)
	agent, _, _, err := ResolveAgent(bundle, "coverage", "heavy", nil, "")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if agent == "minimax" {
		t.Error("minimax on prya should be denied for heavy workloads")
	}
	if agent != "kimi" {
		t.Errorf("expected kimi on sati for heavy task, got %q", agent)
	}
}

func TestLoadRoutingBundle_Integration(t *testing.T) {
	// Requires config/routing/ directory to exist relative to repo root.
	// Skip gracefully if running from a context where it's unavailable.
	bundle, err := LoadRoutingBundle("../../config/routing")
	if err != nil {
		t.Skipf("config/routing/ not available: %v", err)
	}
	if len(bundle.Agents.Agents) == 0 {
		t.Error("expected at least one agent in registry")
	}
	if len(bundle.Nodes.Nodes) == 0 {
		t.Error("expected at least one node in registry")
	}
	// Verify kimi is in the registry
	if _, ok := bundle.Agents.Agents["kimi"]; !ok {
		t.Error("expected kimi agent in registry")
	}
	// Verify claude was added
	if _, ok := bundle.Agents.Agents["claude"]; !ok {
		t.Error("expected claude agent in registry")
	}
	// Verify codex was removed
	if _, ok := bundle.Agents.Agents["codex"]; ok {
		t.Error("codex should have been removed from registry")
	}
}

func TestResolveAgent_StageBoost(t *testing.T) {
	bundle := &RoutingBundle{
		Agents: RoutingAgentConfig{
			Agents: map[string]RoutingAgentEntry{
				"minimax": {HomeNode: "prya", Status: "OK", BestAt: []string{"docs"}, Weight: "light", StagePreference: "idea"},
				"kimi":    {HomeNode: "sati", Status: "OK", BestAt: []string{"complex-edits"}, Weight: "light", StagePreference: "build"},
			},
		},
	}
	// build stage should prefer kimi: best_at "complex-edits" matches stageToWorkload("build")="complex-edits"
	agent, _, _, err := ResolveAgent(bundle, "feature", "light", nil, "build")
	if err != nil {
		t.Fatal(err)
	}
	if agent != "kimi" {
		t.Errorf("expected kimi for build stage, got %s", agent)
	}

	// idea stage should prefer minimax: best_at "docs" matches stageToWorkload("idea")="docs"
	agent, _, _, err = ResolveAgent(bundle, "feature", "light", nil, "idea")
	if err != nil {
		t.Fatal(err)
	}
	if agent != "minimax" {
		t.Errorf("expected minimax for idea stage, got %s", agent)
	}
}

func TestResolveAgent_NoStage_Backward_Compat(t *testing.T) {
	// Passing empty portfolioStage must not change behavior for existing callers.
	bundle := &RoutingBundle{
		Agents: RoutingAgentConfig{
			Agents: map[string]RoutingAgentEntry{
				"kimi": {HomeNode: "sati", Status: "OK", BestAt: []string{"complex-edits"}, Weight: "light"},
			},
		},
	}
	agent, node, _, err := ResolveAgent(bundle, "feature", "light", nil, "")
	if err != nil {
		t.Fatal(err)
	}
	if agent != "kimi" || node != "sati" {
		t.Errorf("expected kimi/sati, got %s/%s", agent, node)
	}
}

func TestStageToWorkload(t *testing.T) {
	tests := []struct {
		stage string
		want  string
	}{
		{"build", "complex-edits"},
		{"deploy", "complex-reasoning"},
		{"validate", "research"},
		{"measure", "research"},
		{"idea", "docs"},
		{"scale", ""},
		{"kill", ""},
		{"monetize", ""},
		{"", ""},
	}
	for _, tt := range tests {
		got := stageToWorkload(tt.stage)
		if got != tt.want {
			t.Errorf("stageToWorkload(%q) = %q, want %q", tt.stage, got, tt.want)
		}
	}
}

func TestGetApprovalTierForStage(t *testing.T) {
	tier, threshold := GetApprovalTierForStage("deploy")
	if tier != TierDesktop {
		t.Errorf("deploy should be desktop, got %s", tier)
	}
	if threshold < 0.9 {
		t.Errorf("deploy threshold should be >= 0.9, got %f", threshold)
	}

	tier, _ = GetApprovalTierForStage("kill")
	if tier != TierWatch {
		t.Errorf("kill should be watch, got %s", tier)
	}

	tier, _ = GetApprovalTierForStage("")
	if tier != "" {
		t.Errorf("empty stage should return empty tier, got %q", tier)
	}

	tier, threshold = GetApprovalTierForStage("idea")
	if tier != TierWatch {
		t.Errorf("idea should be watch, got %s", tier)
	}
	if threshold != 1.0 {
		t.Errorf("idea threshold should be 1.0, got %f", threshold)
	}

	tier, threshold = GetApprovalTierForStage("monetize")
	if tier != TierDesktop {
		t.Errorf("monetize should be desktop, got %s", tier)
	}
	if threshold != 0.0 {
		t.Errorf("monetize threshold should be 0.0, got %f", threshold)
	}

	// Unknown stage should default to safest
	tier, threshold = GetApprovalTierForStage("unknown-stage")
	if tier != TierDesktop {
		t.Errorf("unknown stage should be desktop (safest), got %s", tier)
	}
	if threshold != 0.0 {
		t.Errorf("unknown stage threshold should be 0.0 (safest), got %f", threshold)
	}
}
