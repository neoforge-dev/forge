//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"strings"
	"testing"
)

// TestHelpCoverage_ShowAgentHelp verifies agent help output.
func TestHelpCoverage_ShowAgentHelp(t *testing.T) {
	hs := NewHelpSystem()
	out := captureStdout(t, func() {
		hs.ShowAgentHelp()
	})
	if !strings.Contains(out, "forge agent") {
		t.Errorf("ShowAgentHelp missing 'forge agent', got:\n%s", out)
	}
	if !strings.Contains(out, "spawn") {
		t.Errorf("ShowAgentHelp missing 'spawn', got:\n%s", out)
	}
}

// TestHelpCoverage_ShowFleetHelp verifies fleet help output.
func TestHelpCoverage_ShowFleetHelp(t *testing.T) {
	hs := NewHelpSystem()
	out := captureStdout(t, func() {
		hs.ShowFleetHelp()
	})
	if !strings.Contains(out, "forge fleet") {
		t.Errorf("ShowFleetHelp missing 'forge fleet', got:\n%s", out)
	}
	if !strings.Contains(out, "topology") {
		t.Errorf("ShowFleetHelp missing 'topology', got:\n%s", out)
	}
}

// TestHelpCoverage_ShowQueueHelp verifies queue help output.
func TestHelpCoverage_ShowQueueHelp(t *testing.T) {
	hs := NewHelpSystem()
	out := captureStdout(t, func() {
		hs.ShowQueueHelp()
	})
	if !strings.Contains(out, "forge queue") {
		t.Errorf("ShowQueueHelp missing 'forge queue', got:\n%s", out)
	}
	if !strings.Contains(out, "depth") {
		t.Errorf("ShowQueueHelp missing 'depth', got:\n%s", out)
	}
}

// TestHelpCoverage_ShowGitHelp verifies git help output.
func TestHelpCoverage_ShowGitHelp(t *testing.T) {
	hs := NewHelpSystem()
	out := captureStdout(t, func() {
		hs.ShowGitHelp()
	})
	if !strings.Contains(out, "forge git") {
		t.Errorf("ShowGitHelp missing 'forge git', got:\n%s", out)
	}
	if !strings.Contains(out, "guard") {
		t.Errorf("ShowGitHelp missing 'guard', got:\n%s", out)
	}
}

// TestHelpCoverage_ShowSystemHelp verifies system help output.
func TestHelpCoverage_ShowSystemHelp(t *testing.T) {
	hs := NewHelpSystem()
	out := captureStdout(t, func() {
		hs.ShowSystemHelp()
	})
	if !strings.Contains(out, "forge system") {
		t.Errorf("ShowSystemHelp missing 'forge system', got:\n%s", out)
	}
	if !strings.Contains(out, "patrol") {
		t.Errorf("ShowSystemHelp missing 'patrol', got:\n%s", out)
	}
}

// TestHelpCoverage_ShowDocsHelp verifies docs help output.
func TestHelpCoverage_ShowDocsHelp(t *testing.T) {
	hs := NewHelpSystem()
	out := captureStdout(t, func() {
		hs.ShowDocsHelp()
	})
	if !strings.Contains(out, "forge docs") {
		t.Errorf("ShowDocsHelp missing 'forge docs', got:\n%s", out)
	}
	if !strings.Contains(out, "quickstart") {
		t.Errorf("ShowDocsHelp missing 'quickstart', got:\n%s", out)
	}
}

// TestHelpCoverage_ShowPatterns verifies patterns output contains workflow entries.
func TestHelpCoverage_ShowPatterns(t *testing.T) {
	hs := NewHelpSystem()
	out := captureStdout(t, func() {
		hs.ShowPatterns()
	})
	if !strings.Contains(out, "Hotfix Workflow") {
		t.Errorf("ShowPatterns missing 'Hotfix Workflow', got:\n%s", out)
	}
	if !strings.Contains(out, "Feature Development") {
		t.Errorf("ShowPatterns missing 'Feature Development', got:\n%s", out)
	}
	if !strings.Contains(out, "Emergency Stop") {
		t.Errorf("ShowPatterns missing 'Emergency Stop', got:\n%s", out)
	}
}

// TestHelpCoverage_ShowVerbHelp_TaskList exercises the "task list" verb branch.
func TestHelpCoverage_ShowVerbHelp_TaskList(t *testing.T) {
	hs := NewHelpSystem()
	out := captureStdout(t, func() {
		hs.ShowVerbHelp("task", "list")
	})
	if !strings.Contains(out, "forge task list") {
		t.Errorf("ShowVerbHelp(task,list) missing expected text, got:\n%s", out)
	}
}

// TestHelpCoverage_ShowVerbHelp_TaskShow exercises the "task show" verb branch.
func TestHelpCoverage_ShowVerbHelp_TaskShow(t *testing.T) {
	hs := NewHelpSystem()
	out := captureStdout(t, func() {
		hs.ShowVerbHelp("task", "show")
	})
	if !strings.Contains(out, "forge task show") {
		t.Errorf("ShowVerbHelp(task,show) missing expected text, got:\n%s", out)
	}
}

// TestHelpCoverage_ShowVerbHelp_AgentSpawn exercises the "agent spawn" verb branch.
func TestHelpCoverage_ShowVerbHelp_AgentSpawn(t *testing.T) {
	hs := NewHelpSystem()
	out := captureStdout(t, func() {
		hs.ShowVerbHelp("agent", "spawn")
	})
	if !strings.Contains(out, "forge agent spawn") {
		t.Errorf("ShowVerbHelp(agent,spawn) missing expected text, got:\n%s", out)
	}
}

// TestHelpCoverage_ShowVerbHelp_Unknown exercises the default/unknown branch.
func TestHelpCoverage_ShowVerbHelp_Unknown(t *testing.T) {
	hs := NewHelpSystem()
	out := captureStdout(t, func() {
		hs.ShowVerbHelp("unknown", "verb")
	})
	if !strings.Contains(out, "No specific help") {
		t.Errorf("ShowVerbHelp(unknown,verb) missing fallback message, got:\n%s", out)
	}
}

// TestHelpCoverage_RunHelp_AllTopics exercises every topic switch case in RunHelp.
func TestHelpCoverage_RunHelp_AllTopics(t *testing.T) {
	topics := []struct {
		args        []string
		wantContain string
	}{
		{[]string{"agent"}, "forge agent"},
		{[]string{"fleet"}, "forge fleet"},
		{[]string{"queue"}, "forge queue"},
		{[]string{"git"}, "forge git"},
		{[]string{"system"}, "forge system"},
		{[]string{"docs"}, "forge docs"},
		{[]string{"patterns"}, "Hotfix Workflow"},
		{[]string{"unknown-topic"}, "Unknown help topic"},
	}

	for _, tt := range topics {
		t.Run(tt.args[0], func(t *testing.T) {
			out := captureStdout(t, func() {
				RunHelp(tt.args)
			})
			if !strings.Contains(out, tt.wantContain) {
				t.Errorf("RunHelp(%v) missing %q, got:\n%s", tt.args, tt.wantContain, out)
			}
		})
	}
}

// TestHelpCoverage_RunHelp_VerbCombo exercises the two-argument path in RunHelp.
func TestHelpCoverage_RunHelp_VerbCombo(t *testing.T) {
	out := captureStdout(t, func() {
		RunHelp([]string{"task", "create"})
	})
	if !strings.Contains(out, "Create a new task") {
		t.Errorf("RunHelp(task,create) missing expected text, got:\n%s", out)
	}
}

// TestHelpCoverage_levenshteinDistance exercises edit-distance edge cases.
func TestHelpCoverage_levenshteinDistance(t *testing.T) {
	tests := []struct {
		a, b string
		want int
	}{
		{"", "abc", 3},
		{"abc", "", 3},
		{"", "", 0},
		{"abc", "abc", 0},
		{"kitten", "sitting", 3},
		{"fleet", "fleat", 1},
		{"task", "tas", 1},
	}
	for _, tt := range tests {
		got := levenshteinDistance(tt.a, tt.b)
		if got != tt.want {
			t.Errorf("levenshteinDistance(%q, %q) = %d, want %d", tt.a, tt.b, got, tt.want)
		}
	}
}

// TestHelpCoverage_DidYouMean_NoMatch verifies empty return when nothing is close enough.
func TestHelpCoverage_DidYouMean_NoMatch(t *testing.T) {
	got := DidYouMean("zzzzzzzzz")
	if got != "" {
		t.Errorf("DidYouMean(zzzzzzzzz) expected empty, got %q", got)
	}
}

// TestHelpCoverage_DidYouMean_ContainsKey verifies substring matching path.
func TestHelpCoverage_DidYouMean_ContainsKey(t *testing.T) {
	got := DidYouMean("my task command")
	if !strings.Contains(got, "task") {
		t.Errorf("DidYouMean(my task command) expected 'task' suggestion, got %q", got)
	}
}
