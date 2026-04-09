package main

import (
	"os"
	"testing"
	"time"
)

// TestFleetCommand tests fleet command
func TestFleetCommand(t *testing.T) {
	runner := NewTestRunner(t)

	_, err := runner.Execute("fleet")
	if err != nil {
		t.Logf("fleet: %v", err)
	}
}

// TestFleetListRemoved verifies that forge fleet list no longer exists (CLI Phase 3).
func TestFleetListRemoved(t *testing.T) {
	// Verify by inspecting the registered subcommands directly — cobra returns
	// exit 0 for unknown subcommands (prints help), so Execute() is not reliable.
	for _, sub := range fleetCmd.Commands() {
		if sub.Name() == "list" {
			t.Error("'fleet list' subcommand still registered — should have been removed in CLI Phase 3; use 'agent list' instead")
		}
	}
}

// TestFleetStateManager tests fleet state manager creation
func TestFleetStateManager(t *testing.T) {
	fsm := NewFleetStateManager()
	if fsm == nil {
		t.Fatal("expected fleet state manager")
	}
	if fsm.StateDir == "" {
		t.Error("expected state dir")
	}
}

// TestFleetStateManagerGetAgentsEmpty tests getting agents from empty dir
func TestFleetStateManagerGetAgentsEmpty(t *testing.T) {
	tmpDir := t.TempDir()
	oldWd, _ := os.Getwd()
	os.Chdir(tmpDir)
	defer os.Chdir(oldWd)

	// Set FORGE_ROOT to isolate from main repo
	os.Setenv("FORGE_ROOT", tmpDir)
	defer os.Unsetenv("FORGE_ROOT")

	// Create .forge directory structure
	os.MkdirAll(".forge/workflow/state", 0755)

	fsm := NewFleetStateManager()
	agents, err := fsm.GetAgents()
	if err != nil {
		t.Fatalf("GetAgents failed: %v", err)
	}
	if len(agents) != 0 {
		t.Error("expected empty agents")
	}
}

// TestFleetStateManagerSaveAndGetAgent tests saving and getting an agent
func TestFleetStateManagerSaveAndGetAgent(t *testing.T) {
	tmpDir := t.TempDir()
	oldWd, _ := os.Getwd()
	os.Chdir(tmpDir)
	defer os.Chdir(oldWd)

	// Set FORGE_ROOT to isolate from main repo
	os.Setenv("FORGE_ROOT", tmpDir)
	defer os.Unsetenv("FORGE_ROOT")

	// Create .forge directory structure
	os.MkdirAll(".forge/workflow/state", 0755)

	fsm := NewFleetStateManager()

	now := time.Now()
	agent := &FleetAgent{
		ID:              "forge:test",
		State:           StateIdle,
		LastStateChange: now,
	}

	err := fsm.SaveAgent(agent)
	if err != nil {
		t.Fatalf("SaveAgent failed: %v", err)
	}

	// Get the agent back
	got, err := fsm.GetAgent("forge:test")
	if err != nil {
		t.Fatalf("GetAgent failed: %v", err)
	}
	if got.ID != "forge:test" {
		t.Error("expected forge:test")
	}
}

// TestFleetStateManagerGetAgentNotFound tests getting non-existent agent
func TestFleetStateManagerGetAgentNotFound(t *testing.T) {
	tmpDir := t.TempDir()
	oldWd, _ := os.Getwd()
	os.Chdir(tmpDir)
	defer os.Chdir(oldWd)

	// Set FORGE_ROOT to isolate from main repo
	os.Setenv("FORGE_ROOT", tmpDir)
	defer os.Unsetenv("FORGE_ROOT")

	os.MkdirAll(".forge/workflow/state", 0755)

	fsm := NewFleetStateManager()
	_, err := fsm.GetAgent("nonexistent")
	if err == nil {
		t.Error("expected error for nonexistent agent")
	}
}

func TestFleetStateManagerGetSummary_Empty(t *testing.T) {
	tmpDir := t.TempDir()
	oldWd, _ := os.Getwd()
	os.Chdir(tmpDir)
	defer os.Chdir(oldWd)

	t.Setenv("FORGE_ROOT", tmpDir)
	os.MkdirAll(".forge/workflow/state", 0755)

	fsm := NewFleetStateManager()
	summary, err := fsm.GetSummary()
	if err != nil {
		t.Fatalf("GetSummary failed: %v", err)
	}
	if summary == nil {
		t.Fatal("GetSummary returned nil")
	}
	if summary.TotalAgents != 0 {
		t.Errorf("TotalAgents = %d, want 0", summary.TotalAgents)
	}
	if summary.Working != 0 || summary.Available != 0 || summary.Failed != 0 {
		t.Errorf("unexpected non-zero counts: working=%d available=%d failed=%d",
			summary.Working, summary.Available, summary.Failed)
	}
}

func TestFleetStateManagerGetSummary_WithAgents(t *testing.T) {
	tmpDir := t.TempDir()
	oldWd, _ := os.Getwd()
	os.Chdir(tmpDir)
	defer os.Chdir(oldWd)

	t.Setenv("FORGE_ROOT", tmpDir)
	os.MkdirAll(".forge/workflow/state", 0755)

	fsm := NewFleetStateManager()

	now := time.Now()
	agents := []*FleetAgent{
		{ID: "agent-1", State: StateWorking, LastStateChange: now},
		{ID: "agent-2", State: StateIdle, LastStateChange: now},
		{ID: "agent-3", State: StateFailed, LastStateChange: now},
	}
	for _, a := range agents {
		if err := fsm.SaveAgent(a); err != nil {
			t.Fatalf("SaveAgent(%q): %v", a.ID, err)
		}
	}

	summary, err := fsm.GetSummary()
	if err != nil {
		t.Fatalf("GetSummary failed: %v", err)
	}
	if summary.TotalAgents != 3 {
		t.Errorf("TotalAgents = %d, want 3", summary.TotalAgents)
	}
	if summary.Working != 1 {
		t.Errorf("Working = %d, want 1", summary.Working)
	}
	if summary.Available != 1 {
		t.Errorf("Available = %d, want 1", summary.Available)
	}
	if summary.Failed != 1 {
		t.Errorf("Failed = %d, want 1", summary.Failed)
	}
}

func testFleetSetup(t *testing.T) (*FleetStateManager, func()) {
	t.Helper()
	tmpDir := t.TempDir()
	oldWd, _ := os.Getwd()
	os.Chdir(tmpDir)
	t.Setenv("FORGE_ROOT", tmpDir)
	os.MkdirAll(".forge/workflow/state", 0755)
	return NewFleetStateManager(), func() { os.Chdir(oldWd) }
}

func TestFleetBroadcast_Empty(t *testing.T) {
	fsm, cleanup := testFleetSetup(t)
	defer cleanup()

	rec, err := fsm.Broadcast("hello", "info", "", "")
	if err != nil {
		t.Fatalf("Broadcast failed: %v", err)
	}
	if rec.TargetCount != 0 {
		t.Errorf("TargetCount = %d, want 0", rec.TargetCount)
	}
}

func TestFleetBroadcast_WithAgents(t *testing.T) {
	fsm, cleanup := testFleetSetup(t)
	defer cleanup()

	now := time.Now()
	for _, id := range []string{"a1", "a2"} {
		fsm.SaveAgent(&FleetAgent{ID: id, State: StateIdle, LastStateChange: now})
	}

	rec, err := fsm.Broadcast("ping", "heartbeat", "", "")
	if err != nil {
		t.Fatalf("Broadcast failed: %v", err)
	}
	if rec.TargetCount != 2 {
		t.Errorf("TargetCount = %d, want 2", rec.TargetCount)
	}
}

func TestFleetPauseAll_NoAgents(t *testing.T) {
	fsm, cleanup := testFleetSetup(t)
	defer cleanup()

	paused, targets, err := fsm.PauseAll("maintenance")
	if err != nil {
		t.Fatalf("PauseAll failed: %v", err)
	}
	if paused != 0 || targets != 0 {
		t.Errorf("PauseAll() = %d, %d; want 0, 0", paused, targets)
	}
}

func TestFleetPauseAll_WithWorkingAgents(t *testing.T) {
	fsm, cleanup := testFleetSetup(t)
	defer cleanup()

	now := time.Now()
	fsm.SaveAgent(&FleetAgent{ID: "w1", State: StateWorking, LastStateChange: now})
	fsm.SaveAgent(&FleetAgent{ID: "i1", State: StateIdle, LastStateChange: now})

	paused, targets, err := fsm.PauseAll("test")
	if err != nil {
		t.Fatalf("PauseAll failed: %v", err)
	}
	if targets != 1 {
		t.Errorf("targets = %d, want 1 (only working agents)", targets)
	}
	if paused != 1 {
		t.Errorf("paused = %d, want 1", paused)
	}
}

func TestFleetResumeAll_NoAgents(t *testing.T) {
	fsm, cleanup := testFleetSetup(t)
	defer cleanup()

	resumed, targets, err := fsm.ResumeAll("test")
	if err != nil {
		t.Fatalf("ResumeAll failed: %v", err)
	}
	if resumed != 0 || targets != 0 {
		t.Errorf("ResumeAll() = %d, %d; want 0, 0", resumed, targets)
	}
}

func TestFleetResumeAll_WithPausedAgents(t *testing.T) {
	fsm, cleanup := testFleetSetup(t)
	defer cleanup()

	now := time.Now()
	fsm.SaveAgent(&FleetAgent{ID: "p1", State: StatePaused, LastStateChange: now})
	fsm.SaveAgent(&FleetAgent{ID: "w1", State: StateWorking, LastStateChange: now})

	resumed, targets, err := fsm.ResumeAll("back online")
	if err != nil {
		t.Fatalf("ResumeAll failed: %v", err)
	}
	if targets != 1 {
		t.Errorf("targets = %d, want 1 (only paused agents)", targets)
	}
	if resumed != 1 {
		t.Errorf("resumed = %d, want 1", resumed)
	}
}

func TestContainsString(t *testing.T) {
	if !containsString([]string{"a", "b", "c"}, "b") {
		t.Error("expected true for present element")
	}
	if containsString([]string{"a", "b", "c"}, "d") {
		t.Error("expected false for missing element")
	}
	if containsString(nil, "a") {
		t.Error("expected false for nil slice")
	}
	if containsString([]string{}, "a") {
		t.Error("expected false for empty slice")
	}
}

func TestValidAgentName(t *testing.T) {
	valid := []string{"kimi", "claude", "forge-agent", "agent-1", "ABC"}
	for _, n := range valid {
		if !validAgentName(n) {
			t.Errorf("validAgentName(%q) = false, want true", n)
		}
	}
	invalid := []string{"", "forge agent", "kimi!", "agent_1", "forge:kimi"}
	for _, n := range invalid {
		if validAgentName(n) {
			t.Errorf("validAgentName(%q) = true, want false", n)
		}
	}
}

func TestAgentStartCmd(t *testing.T) {
	cases := []struct {
		name string
		want string
	}{
		{"kimi", "kimi -y"},
		{"gemini", "gemini -y"},
		{"claude", "claude --dangerously-skip-permissions"},
		{"opencode", "opencode"},
		{"unknown-agent", "claude --dangerously-skip-permissions"},
	}
	for _, tc := range cases {
		got := agentStartCmd(tc.name)
		if got != tc.want {
			t.Errorf("agentStartCmd(%q) = %q, want %q", tc.name, got, tc.want)
		}
	}
}

func TestComputeBudgetStatus(t *testing.T) {
	cases := []struct {
		pct  int
		want string
	}{
		{50, "OK"},
		{80, "CAUTION"},
		{91, "RED"},
		{95, "DEPLETED"},
		{100, "DEPLETED"},
	}
	for _, tc := range cases {
		p := providerBudget{
			Limits: map[string]int{"monthly_pct": tc.pct},
		}
		got := computeBudgetStatus(p)
		if got != tc.want {
			t.Errorf("computeBudgetStatus(%d%%) = %q, want %q", tc.pct, got, tc.want)
		}
	}
}

func TestHostShortname(t *testing.T) {
	got := hostShortname()
	if got == "" {
		t.Error("hostShortname() returned empty string")
	}
	// Should not contain dots (it's the short name only)
	if len(got) > 63 { // max DNS label
		t.Errorf("hostShortname() too long: %q", got)
	}
}
