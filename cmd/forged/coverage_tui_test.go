//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"strings"
	"testing"
	"time"

	tea "github.com/charmbracelet/bubbletea"
)

// newTestDashboard returns a DashboardModel with size set for rendering.
func newTuiTestDashboard() DashboardModel {
	m := NewDashboardModel("http://localhost:9999")
	m.width = 80
	m.height = 24
	return m
}

// ---------------------------------------------------------------------------
// View() — top-level rendering dispatch
// ---------------------------------------------------------------------------

func TestCoverDashboardView_Loading(t *testing.T) {
	m := NewDashboardModel("")
	result := m.View()
	if result != "Loading..." {
		t.Errorf("View() with width=0: want 'Loading...', got %q", result)
	}
}

func TestCoverDashboardView_Overview(t *testing.T) {
	m := newTuiTestDashboard()
	m.currentView = OverviewView
	result := m.View()
	if result == "" {
		t.Error("View() overview returned empty string")
	}
}

func TestCoverDashboardView_TasksView(t *testing.T) {
	m := newTuiTestDashboard()
	m.currentView = TasksView
	result := m.View()
	if result == "" {
		t.Error("View() tasks returned empty")
	}
}

func TestCoverDashboardView_SprintsView(t *testing.T) {
	m := newTuiTestDashboard()
	m.currentView = SprintsView
	result := m.View()
	if result == "" {
		t.Error("View() sprints returned empty")
	}
}

func TestCoverDashboardView_LogsView(t *testing.T) {
	m := newTuiTestDashboard()
	m.currentView = LogsView
	result := m.View()
	if result == "" {
		t.Error("View() logs returned empty")
	}
}

func TestCoverDashboardView_AgentDetailView_NoAgent(t *testing.T) {
	m := newTuiTestDashboard()
	m.currentView = AgentDetailView
	result := m.View()
	if result == "" {
		t.Error("View() agent detail returned empty")
	}
}

// ---------------------------------------------------------------------------
// agentsView()
// ---------------------------------------------------------------------------

func TestCoverAgentsView_Empty(t *testing.T) {
	m := newTuiTestDashboard()
	result := m.agentsView(16)
	if !strings.Contains(result, "No agents") {
		t.Errorf("agentsView with no agents: got %s", result)
	}
}

// ---------------------------------------------------------------------------
// sprintsView()
// ---------------------------------------------------------------------------

func TestCoverSprintsView_Nil(t *testing.T) {
	m := newTuiTestDashboard()
	result := m.sprintsView(16)
	if !strings.Contains(result, "No active sprints") {
		t.Errorf("sprintsView nil: got %s", result)
	}
}

func TestCoverSprintsView_WithSprint(t *testing.T) {
	m := newTuiTestDashboard()
	m.state.Sprint = &SprintStatus{
		Name:       "S82",
		StartedAt:  time.Now().Add(-24 * time.Hour),
		ETA:        "2026-03-15",
		Completion: 65.0,
		Agents: []AgentSprintStatus{
			{Name: "glm", Status: "working", CurrentTask: "task-1"},
			{Name: "kimi", Status: "blocked", CurrentTask: "task-2"},
		},
		Blockers: []Blocker{
			{Agent: "kimi", Description: "stuck on lint", Severity: "medium"},
		},
	}
	result := m.sprintsView(16)
	if !strings.Contains(result, "S82") {
		t.Errorf("sprintsView should contain sprint name, got: %s", result)
	}
}

func TestCoverSprintsView_NoBlockers(t *testing.T) {
	m := newTuiTestDashboard()
	m.state.Sprint = &SprintStatus{
		Name:       "S82",
		StartedAt:  time.Now(),
		Completion: 80.0,
		Blockers:   []Blocker{},
	}
	result := m.sprintsView(16)
	if !strings.Contains(result, "No active blockers") {
		t.Errorf("sprintsView with no blockers: got %s", result)
	}
}

// ---------------------------------------------------------------------------
// logsView()
// ---------------------------------------------------------------------------

func TestCoverLogsView_Empty(t *testing.T) {
	m := newTuiTestDashboard()
	result := m.logsView(16)
	if !strings.Contains(result, "No events") {
		t.Errorf("logsView empty: got %s", result)
	}
}

// ---------------------------------------------------------------------------
// agentDetailView()
// ---------------------------------------------------------------------------

func TestCoverAgentDetailView_NoSelection(t *testing.T) {
	m := newTuiTestDashboard()
	result := m.agentDetailView(16)
	if !strings.Contains(result, "No agent selected") {
		t.Errorf("agentDetailView no selection: got %q", result)
	}
}

func TestCoverAgentDetailView_WithAgent(t *testing.T) {
	m := newTuiTestDashboard()
	task := "test-task-123"
	agent := AgentHealth{
		AgentID:        "agent-x",
		Node:           "prya",
		StatusText:     "busy",
		ContextPercent: 45.5,
		SuccessRate:    92.3,
		TaskCount:      17,
		LastHeartbeat:  time.Now(),
		CurrentTask:    &task,
		Capabilities:   []string{"go", "python"},
	}
	m.selectedAgent = &agent
	result := m.agentDetailView(16)
	if !strings.Contains(result, "agent-x") {
		t.Errorf("agentDetailView should contain agent ID, got: %s", result)
	}
}

// ---------------------------------------------------------------------------
// drawHeader()
// ---------------------------------------------------------------------------

func TestCoverDrawHeader_Healthy(t *testing.T) {
	m := newTuiTestDashboard()
	m.state.Summary.SystemHealth = "healthy"
	result := m.drawHeader()
	if result == "" {
		t.Error("drawHeader returned empty")
	}
}

func TestCoverDrawHeader_WithUptime(t *testing.T) {
	m := newTuiTestDashboard()
	m.state.Summary.SystemHealth = "degraded"
	m.state.Summary.Uptime = "2h30m"
	result := m.drawHeader()
	if !strings.Contains(result, "2h30m") {
		t.Errorf("drawHeader with uptime: got %s", result)
	}
}

// ---------------------------------------------------------------------------
// drawTabs()
// ---------------------------------------------------------------------------

func TestCoverDrawTabs_Overview(t *testing.T) {
	m := newTuiTestDashboard()
	m.currentView = OverviewView
	result := m.drawTabs()
	if !strings.Contains(result, "Overview") {
		t.Errorf("drawTabs OverviewView: got %s", result)
	}
}

func TestCoverDrawTabs_AgentDetail(t *testing.T) {
	m := newTuiTestDashboard()
	m.currentView = AgentDetailView
	result := m.drawTabs()
	if !strings.Contains(result, "Agent Details") {
		t.Errorf("drawTabs AgentDetailView: got %q", result)
	}
}

func TestCoverDrawTabs_AllViews(t *testing.T) {
	for _, view := range []View{AgentsView, TasksView, SprintsView, LogsView} {
		m := newTuiTestDashboard()
		m.currentView = view
		result := m.drawTabs()
		if result == "" {
			t.Errorf("drawTabs for view %d returned empty", view)
		}
	}
}

// ---------------------------------------------------------------------------
// drawStatusBar()
// ---------------------------------------------------------------------------

func TestCoverDrawStatusBar_NoError(t *testing.T) {
	m := newTuiTestDashboard()
	m.state.Summary.OnlineAgents = 3
	m.state.Summary.TotalAgents = 5
	result := m.drawStatusBar()
	if result == "" {
		t.Error("drawStatusBar returned empty")
	}
}

func TestCoverDrawStatusBar_WithError(t *testing.T) {
	m := newTuiTestDashboard()
	m.state.Error = "connection refused"
	result := m.drawStatusBar()
	if !strings.Contains(result, "ERROR") {
		t.Errorf("drawStatusBar with error: got %s", result)
	}
}

// ---------------------------------------------------------------------------
// panelStyle()
// ---------------------------------------------------------------------------

func TestCoverPanelStyle_Focused(t *testing.T) {
	m := newTuiTestDashboard()
	m.currentView = OverviewView
	m.focusedPanel = StatusPanel
	_ = m.panelStyle(StatusPanel)
}

func TestCoverPanelStyle_Unfocused(t *testing.T) {
	m := newTuiTestDashboard()
	m.currentView = OverviewView
	m.focusedPanel = TasksPanel
	_ = m.panelStyle(StatusPanel)
}

// ---------------------------------------------------------------------------
// overviewView()
// ---------------------------------------------------------------------------

func TestCoverOverviewView_Empty(t *testing.T) {
	m := newTuiTestDashboard()
	result := m.overviewView(16)
	if result == "" {
		t.Error("overviewView returned empty string")
	}
}

func TestCoverOverviewView_WithData(t *testing.T) {
	m := newTuiTestDashboard()
	task := "some-task"
	m.state.Agents = []AgentHealth{
		{AgentID: "glm", Node: "prya", StatusText: "busy", CurrentTask: &task},
		{AgentID: "kimi", Node: "prya", StatusText: "idle"},
		{AgentID: "gemini", Node: "prya", StatusText: "error"},
		{AgentID: "offline-agent", Node: "sati", StatusText: "offline"},
	}
	m.state.Nodes = []NodeInfo{
		{Name: "prya", Status: "online", AgentCount: 2},
		{Name: "sati", Status: "offline", AgentCount: 0},
	}
	m.history.Throughput = []float64{1.0, 2.5, 3.0}
	m.history.ErrorRate = []float64{0.0, 0.1, 0.2}
	result := m.overviewView(16)
	if !strings.Contains(result, "glm") {
		t.Errorf("overviewView should contain agent 'glm', got: %s", result)
	}
}

// ---------------------------------------------------------------------------
// tasksView()
// ---------------------------------------------------------------------------

func TestCoverTasksView_NoTasks(t *testing.T) {
	m := newTuiTestDashboard()
	result := m.tasksView(16)
	if result == "" {
		t.Error("tasksView returned empty")
	}
}

func TestCoverTasksView_WithTask(t *testing.T) {
	m := newTuiTestDashboard()
	task := "TASK-999"
	m.state.Agents = []AgentHealth{
		{AgentID: "glm", CurrentTask: &task},
	}
	result := m.tasksView(16)
	if !strings.Contains(result, "TASK-999") {
		t.Errorf("tasksView should contain task ID, got: %s", result)
	}
}

// ---------------------------------------------------------------------------
// drawSparkline() — pure function, exported test
// ---------------------------------------------------------------------------

func TestCoverDrawSparkline_Empty(t *testing.T) {
	result := drawSparkline([]float64{}, 10)
	if result == "" {
		t.Error("drawSparkline empty returned empty string")
	}
}

func TestCoverDrawSparkline_Values(t *testing.T) {
	result := drawSparkline([]float64{0, 1, 2, 3, 4, 5}, 10)
	if result == "" {
		t.Error("drawSparkline returned empty")
	}
}

func TestCoverDrawSparkline_AllZero(t *testing.T) {
	result := drawSparkline([]float64{0, 0, 0}, 10)
	if result == "" {
		t.Error("drawSparkline all zeros returned empty")
	}
}

// ---------------------------------------------------------------------------
// agentItem / eventItem — list.Item implementations
// ---------------------------------------------------------------------------

func TestCoverAgentItem(t *testing.T) {
	task := "task-abc"
	item := agentItem{agent: AgentHealth{
		AgentID:        "glm",
		Node:           "prya",
		StatusText:     "busy",
		ContextPercent: 55.0,
		CurrentTask:    &task,
	}}
	if item.Title() != "glm" {
		t.Errorf("Title = %s", item.Title())
	}
	if item.FilterValue() != "glm" {
		t.Errorf("FilterValue = %s", item.FilterValue())
	}
	desc := item.Description()
	if !strings.Contains(desc, "prya") {
		t.Errorf("Description should contain node, got %s", desc)
	}
}

func TestCoverEventItem(t *testing.T) {
	item := eventItem{event: DashboardEvent{
		Type:      "task_completed",
		AgentID:   "kimi",
		Message:   "finished TASK-X",
		Timestamp: time.Now(),
	}}
	if item.FilterValue() != "finished TASK-X" {
		t.Errorf("FilterValue = %s", item.FilterValue())
	}
	if !strings.Contains(item.Title(), "task_completed") {
		t.Errorf("Title should contain event type, got: %s", item.Title())
	}
	if !strings.Contains(item.Description(), "kimi") {
		t.Errorf("Description should contain agent, got: %s", item.Description())
	}
}

// ---------------------------------------------------------------------------
// Update() — message handling
// ---------------------------------------------------------------------------

func TestCoverDashboardUpdate_WindowSize(t *testing.T) {
	m := NewDashboardModel("")
	newModel, _ := m.Update(tea.WindowSizeMsg{Width: 120, Height: 40})
	dm := newModel.(DashboardModel)
	if dm.width != 120 {
		t.Errorf("width = %d, want 120", dm.width)
	}
	if dm.height != 40 {
		t.Errorf("height = %d, want 40", dm.height)
	}
}

func TestCoverDashboardUpdate_DataMsg(t *testing.T) {
	m := NewDashboardModel("")
	state := DashboardState{
		Summary: DashboardSummary{TotalAgents: 5, OnlineAgents: 3},
	}
	newModel, _ := m.Update(dataMsg(state))
	dm := newModel.(DashboardModel)
	if dm.state.Summary.TotalAgents != 5 {
		t.Errorf("TotalAgents = %d, want 5", dm.state.Summary.TotalAgents)
	}
}

func TestCoverDashboardUpdate_DataMsg_History(t *testing.T) {
	m := NewDashboardModel("")
	// First update
	state1 := DashboardState{Summary: DashboardSummary{TaskThroughput: 1.5, ErrorRate: 0.1}}
	m.Update(dataMsg(state1))
	// Second update — history appended
	state2 := DashboardState{Summary: DashboardSummary{TaskThroughput: 2.5, ErrorRate: 0.2}}
	newModel, _ := m.Update(dataMsg(state2))
	dm := newModel.(DashboardModel)
	if len(dm.history.Throughput) == 0 {
		t.Error("history.Throughput should be non-empty after dataMsg")
	}
}
