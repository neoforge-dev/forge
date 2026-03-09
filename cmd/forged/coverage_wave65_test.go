//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"testing"

	tea "github.com/charmbracelet/bubbletea"
)

// Wave 65: Push coverage from 74.6% to 75%+
// Targets:
//   - tui_dashboard.go: DashboardModel.Update (46.5% — tickMsg, dataMsg, WindowSizeMsg, key paths)
//   - tui_dashboard.go: fetchData (35.3% — execute cmd, get message)
//   - tui_dashboard.go: agentsView (66.7%) and logsView (66.7%)

// ─── DashboardModel.Update — tickMsg ─────────────────────────────────────────

func TestWave65_Update_TickMsg(t *testing.T) {
	m := NewDashboardModel("")
	_, cmd := m.Update(tickMsg{})
	if cmd == nil {
		t.Error("expected non-nil cmd from tickMsg (should batch fetchData + tickCmd)")
	}
}

// ─── DashboardModel.Update — dataMsg ─────────────────────────────────────────

func TestWave65_Update_DataMsg(t *testing.T) {
	m := NewDashboardModel("")
	state := DashboardState{
		Summary: DashboardSummary{TaskThroughput: 1.5, ErrorRate: 0.1},
	}
	model, cmd := m.Update(dataMsg(state))
	if cmd != nil {
		t.Logf("dataMsg returned cmd: %T", cmd)
	}
	dm := model.(DashboardModel)
	if dm.state.Summary.TaskThroughput != 1.5 {
		t.Errorf("expected throughput 1.5, got %.2f", dm.state.Summary.TaskThroughput)
	}
	if len(dm.history.Throughput) == 0 {
		t.Error("expected throughput history to be populated")
	}
}

func TestWave65_Update_DataMsg_WithAgentsAndEvents(t *testing.T) {
	m := NewDashboardModel("")
	m.width = 80
	m.height = 24

	task := "test-task"
	agents := []AgentHealth{
		{AgentID: "agent-w65-1", StatusText: "idle", Node: "sati", ContextPercent: 10.0, CurrentTask: &task},
		{AgentID: "agent-w65-2", StatusText: "busy", Node: "nova", ContextPercent: 50.0},
	}
	state := DashboardState{
		Summary: DashboardSummary{TaskThroughput: 2.0},
		Agents:  agents,
	}
	model, _ := m.Update(dataMsg(state))
	dm := model.(DashboardModel)
	if len(dm.history.Throughput) == 0 {
		t.Error("throughput history should be non-empty")
	}
}

// ─── DashboardModel.Update — WindowSizeMsg ───────────────────────────────────

func TestWave65_Update_WindowSizeMsg(t *testing.T) {
	m := NewDashboardModel("")
	model, _ := m.Update(tea.WindowSizeMsg{Width: 120, Height: 40})
	dm := model.(DashboardModel)
	if dm.width != 120 {
		t.Errorf("expected width 120, got %d", dm.width)
	}
	if dm.height != 40 {
		t.Errorf("expected height 40, got %d", dm.height)
	}
}

// ─── DashboardModel.Update — key: Quit ───────────────────────────────────────

func TestWave65_Update_KeyQuit(t *testing.T) {
	m := NewDashboardModel("")
	_, cmd := m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("q")})
	if cmd == nil {
		t.Error("expected non-nil cmd (tea.Quit) for 'q' key")
	}
}

// ─── DashboardModel.Update — key: Tab ────────────────────────────────────────

func TestWave65_Update_KeyTab(t *testing.T) {
	m := NewDashboardModel("")
	m.currentView = OverviewView
	model, _ := m.Update(tea.KeyMsg{Type: tea.KeyTab})
	dm := model.(DashboardModel)
	if dm.currentView == OverviewView {
		t.Error("expected view to advance past OverviewView after Tab")
	}
}

// ─── DashboardModel.Update — key: Right ──────────────────────────────────────

func TestWave65_Update_KeyRight_OverviewView(t *testing.T) {
	m := NewDashboardModel("")
	m.currentView = OverviewView
	m.focusedPanel = StatusPanel
	model, _ := m.Update(tea.KeyMsg{Type: tea.KeyRight})
	dm := model.(DashboardModel)
	if dm.focusedPanel == StatusPanel {
		t.Error("expected focusedPanel to advance after Right in OverviewView")
	}
}

func TestWave65_Update_KeyRight_TasksView(t *testing.T) {
	m := NewDashboardModel("")
	m.currentView = TasksView
	model, _ := m.Update(tea.KeyMsg{Type: tea.KeyRight})
	dm := model.(DashboardModel)
	if dm.currentView == TasksView {
		t.Error("expected currentView to change after Right in TasksView")
	}
}

// ─── DashboardModel.Update — key: Left ───────────────────────────────────────

func TestWave65_Update_KeyLeft_OverviewView(t *testing.T) {
	m := NewDashboardModel("")
	m.currentView = OverviewView
	m.focusedPanel = TasksPanel
	model, _ := m.Update(tea.KeyMsg{Type: tea.KeyLeft})
	dm := model.(DashboardModel)
	if dm.focusedPanel == TasksPanel {
		t.Error("expected focusedPanel to change after Left in OverviewView")
	}
}

// ─── DashboardModel.Update — showQuick=true paths ────────────────────────────

func TestWave65_Update_ShowQuick_Enter(t *testing.T) {
	m := NewDashboardModel("")
	m.showQuick = true
	model, cmd := m.Update(tea.KeyMsg{Type: tea.KeyEnter})
	dm := model.(DashboardModel)
	if dm.showQuick {
		t.Error("expected showQuick to be false after Enter")
	}
	if cmd == nil {
		t.Error("expected non-nil cmd (fetchData) after Enter with showQuick")
	}
}

func TestWave65_Update_ShowQuick_Esc(t *testing.T) {
	m := NewDashboardModel("")
	m.showQuick = true
	model, cmd := m.Update(tea.KeyMsg{Type: tea.KeyEsc})
	dm := model.(DashboardModel)
	if dm.showQuick {
		t.Error("expected showQuick to be false after Esc")
	}
	if cmd != nil {
		t.Logf("Esc with showQuick returned cmd: %T (expected nil)", cmd)
	}
}

// ─── DashboardModel.Update — AgentsView Enter ────────────────────────────────

func TestWave65_Update_AgentsView_Enter(t *testing.T) {
	m := NewDashboardModel("")
	m.currentView = AgentsView
	// agentList is empty by default — Enter should be a no-op (no SelectedItem)
	model, _ := m.Update(tea.KeyMsg{Type: tea.KeyEnter})
	dm := model.(DashboardModel)
	// View should stay AgentsView since no agent is selected in the empty list
	if dm.currentView == AgentDetailView {
		t.Error("did not expect AgentDetailView when agent list is empty")
	}
}

// ─── DashboardModel.Update — AgentDetailView Esc ────────────────────────────

func TestWave65_Update_AgentDetailView_Esc(t *testing.T) {
	m := NewDashboardModel("")
	m.currentView = AgentDetailView
	m.previousView = AgentsView
	model, _ := m.Update(tea.KeyMsg{Type: tea.KeyEsc})
	dm := model.(DashboardModel)
	if dm.currentView != AgentsView {
		t.Errorf("expected currentView=AgentsView after Esc from AgentDetailView, got %v", dm.currentView)
	}
}

// ─── fetchData ────────────────────────────────────────────────────────────────

func TestWave65_FetchData_ReturnsCmd(t *testing.T) {
	m := NewDashboardModel("")
	cmd := m.fetchData()
	if cmd == nil {
		t.Fatal("expected non-nil cmd from fetchData")
	}
	// Execute the cmd — it will fail to connect to localhost:8081 but should
	// return a dataMsg with an Error field (not panic).
	msg := cmd()
	dm, ok := msg.(dataMsg)
	if !ok {
		t.Fatalf("expected dataMsg from fetchData cmd, got %T", msg)
	}
	if dm.Error == "" {
		t.Log("fetchData succeeded (daemon is running locally)")
	} else {
		t.Logf("fetchData returned expected error: %s", dm.Error)
	}
}

// ─── agentsView ───────────────────────────────────────────────────────────────

func TestWave65_AgentsView_Empty(t *testing.T) {
	m := NewDashboardModel("")
	m.width = 80
	m.height = 24
	result := m.agentsView(16)
	if result == "" {
		t.Error("agentsView should return non-empty string even with no agents")
	}
}

func TestWave65_AgentsView_WithAgents(t *testing.T) {
	m := NewDashboardModel("")
	m.width = 80
	m.height = 24
	// Populate via a dataMsg Update so agentList is properly initialized
	state := DashboardState{
		Agents: []AgentHealth{
			{AgentID: "agent-w65-view", StatusText: "idle", Node: "sati"},
		},
	}
	model, _ := m.Update(dataMsg(state))
	dm := model.(DashboardModel)
	result := dm.agentsView(16)
	if result == "" {
		t.Error("agentsView with agents should return non-empty string")
	}
}

// ─── logsView ────────────────────────────────────────────────────────────────

func TestWave65_LogsView_Empty(t *testing.T) {
	m := NewDashboardModel("")
	m.width = 80
	m.height = 24
	result := m.logsView(16)
	if result == "" {
		t.Error("logsView should return non-empty string even with no events")
	}
}

func TestWave65_LogsView_WithEvents(t *testing.T) {
	m := NewDashboardModel("")
	m.width = 80
	m.height = 24
	ev := DashboardEvent{Type: "task_completed", AgentID: "agent-w65", Message: "done"}
	state := DashboardState{
		RecentEvents: []DashboardEvent{ev},
	}
	model, _ := m.Update(dataMsg(state))
	dm := model.(DashboardModel)
	result := dm.logsView(16)
	if result == "" {
		t.Error("logsView with events should return non-empty string")
	}
}
