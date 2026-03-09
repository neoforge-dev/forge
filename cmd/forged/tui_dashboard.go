package main

import (
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"time"

	"github.com/charmbracelet/bubbles/key"
	"github.com/charmbracelet/bubbles/list"
	"github.com/charmbracelet/bubbles/progress"
	"github.com/charmbracelet/bubbles/textinput"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
)

// View represents the current dashboard view
type View int

const (
	OverviewView View = iota
	AgentsView
	TasksView
	SprintsView
	LogsView
	AgentDetailView
)

// Panel represents a focused panel in the overview
type Panel int

const (
	StatusPanel Panel = iota
	TasksPanel
	AgentsPanel
	EventsPanel
)

// DashboardState holds all dashboard data
type DashboardState struct {
	Summary      DashboardSummary `json:"summary"`
	Agents       []AgentHealth    `json:"agents"`
	Nodes        []NodeInfo       `json:"nodes"`
	RecentEvents []DashboardEvent `json:"recent_events"`
	Alerts       []DashboardAlert `json:"alerts"`
	Sprint       *SprintStatus    `json:"sprint"`
	LastUpdated  time.Time
	Error        string
}

// DashboardModel is the BubbleTea model
type DashboardModel struct {
	state         DashboardState
	currentView   View
	previousView  View
	focusedPanel  Panel
	selectedAgent *AgentHealth
	width         int
	height        int
	apiBaseURL    string
	ticker        *time.Ticker
	keys          KeyMap
	agentList     list.Model
	taskList      list.Model
	logList       list.Model
	sprintProg    progress.Model
	quickTask     textinput.Model
	showQuick     bool
	isDark        bool
	history       struct {
		Throughput []float64
		ErrorRate  []float64
	}
}

type KeyMap struct {
	Up          key.Binding
	Down        key.Binding
	Left        key.Binding
	Right       key.Binding
	Tab         key.Binding
	Refresh     key.Binding
	Quit        key.Binding
	Enter       key.Binding
	Esc         key.Binding
	Dispatch    key.Binding
	ToggleTheme key.Binding
}

func DefaultKeyMap() KeyMap {
	return KeyMap{
		Up: key.NewBinding(
			key.WithKeys("up", "k"),
			key.WithHelp("↑/k", "up"),
		),
		Down: key.NewBinding(
			key.WithKeys("down", "j"),
			key.WithHelp("↓/j", "down"),
		),
		Left: key.NewBinding(
			key.WithKeys("left", "h"),
			key.WithHelp("←/h", "prev"),
		),
		Right: key.NewBinding(
			key.WithKeys("right", "l"),
			key.WithHelp("→/l", "next"),
		),
		Tab: key.NewBinding(
			key.WithKeys("tab"),
			key.WithHelp("tab", "switch view"),
		),
		Refresh: key.NewBinding(
			key.WithKeys("r"),
			key.WithHelp("r", "refresh"),
		),
		Quit: key.NewBinding(
			key.WithKeys("q", "ctrl+c"),
			key.WithHelp("q", "quit"),
		),
		Enter: key.NewBinding(
			key.WithKeys("enter"),
			key.WithHelp("enter", "select/details"),
		),
		Esc: key.NewBinding(
			key.WithKeys("esc"),
			key.WithHelp("esc", "back"),
		),
		Dispatch: key.NewBinding(
			key.WithKeys("d"),
			key.WithHelp("d", "dispatch task"),
		),
		ToggleTheme: key.NewBinding(
			key.WithKeys("t"),
			key.WithHelp("t", "toggle theme"),
		),
	}
}

// Styles
var (
	titleStyle = lipgloss.NewStyle().
		Bold(true).
		Foreground(lipgloss.Color("#7D56F4")).
		PaddingLeft(2)

	statusBarStyle = lipgloss.NewStyle().
		Foreground(lipgloss.Color("#FFF")).
		Background(lipgloss.Color("#333")).
		PaddingLeft(2).
		PaddingRight(2)

	activeTabStyle = lipgloss.NewStyle().
		Bold(true).
		Foreground(lipgloss.Color("#FFF")).
		Background(lipgloss.Color("#7D56F4")).
		Padding(0, 2)

	inactiveTabStyle = lipgloss.NewStyle().
		Foreground(lipgloss.Color("#999")).
		Padding(0, 2)

	boxStyle = lipgloss.NewStyle().
		Border(lipgloss.RoundedBorder()).
		BorderForeground(lipgloss.Color("#666")).
		Padding(1)

	headerStyle = lipgloss.NewStyle().
		Bold(true).
		Foreground(lipgloss.Color("#FFF"))

	successStyle = lipgloss.NewStyle().
		Foreground(lipgloss.Color("#00FF00"))

	warningStyle = lipgloss.NewStyle().
		Foreground(lipgloss.Color("#FFFF00"))

	errorStyle = lipgloss.NewStyle().
		Foreground(lipgloss.Color("#FF0000"))
)

// Messages
type tickMsg time.Time
type dataMsg DashboardState

type agentItem struct {
	agent AgentHealth
}

func (i agentItem) Title() string { return i.agent.AgentID }
func (i agentItem) Description() string {
	return fmt.Sprintf("%s | %s | %.1f%% context", i.agent.StatusText, i.agent.Node, i.agent.ContextPercent)
}
func (i agentItem) FilterValue() string { return i.agent.AgentID }

type eventItem struct {
	event DashboardEvent
}

func (i eventItem) Title() string       { return i.event.Timestamp.Format("15:04:05") + " - " + i.event.Type }
func (i eventItem) Description() string { return i.event.AgentID + ": " + i.event.Message }
func (i eventItem) FilterValue() string { return i.event.Message }

func NewDashboardModel(apiBaseURL string) DashboardModel {
	if apiBaseURL == "" {
		apiBaseURL = "http://localhost:8081"
	}

	ti := textinput.New()
	ti.Placeholder = "Enter task command..."
	ti.CharLimit = 156
	ti.Width = 40

	return DashboardModel{
		currentView: OverviewView,
		apiBaseURL:  apiBaseURL,
		keys:        DefaultKeyMap(),
		ticker:      time.NewTicker(2 * time.Second),
		sprintProg:  progress.New(progress.WithDefaultGradient()),
		quickTask:   ti,
		isDark:      true,
	}
}

func (m DashboardModel) Init() tea.Cmd {
	return tea.Batch(
		m.fetchData(),
		m.tickCmd(),
	)
}

func (m DashboardModel) tickCmd() tea.Cmd {
	return func() tea.Msg {
		return tickMsg(<-m.ticker.C)
	}
}

func (m DashboardModel) fetchData() tea.Cmd {
	return func() tea.Msg {
		state := DashboardState{
			LastUpdated: time.Now(),
		}

		resp, err := http.Get(m.apiBaseURL + "/api/dashboard")
		if err != nil {
			state.Error = err.Error()
			return dataMsg(state)
		}
		defer resp.Body.Close()
		if err := json.NewDecoder(resp.Body).Decode(&state); err != nil {
			state.Error = "Decode error: " + err.Error()
			return dataMsg(state)
		}

		resp, err = http.Get(m.apiBaseURL + "/api/coordination/status")
		if err == nil {
			var sprint SprintStatus
			if err := json.NewDecoder(resp.Body).Decode(&sprint); err == nil {
				state.Sprint = &sprint
			}
			resp.Body.Close()
		}

		return dataMsg(state)
	}
}

func (m DashboardModel) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	var cmd tea.Cmd

	if m.showQuick {
		switch msg := msg.(type) {
		case tea.KeyMsg:
			switch msg.String() {
			case "enter":
				m.showQuick = false
				m.quickTask.Reset()
				return m, m.fetchData()
			case "esc":
				m.showQuick = false
				m.quickTask.Reset()
				return m, nil
			}
		}
		m.quickTask, cmd = m.quickTask.Update(msg)
		return m, cmd
	}

	switch msg := msg.(type) {
	case tea.WindowSizeMsg:
		m.width = msg.Width
		m.height = msg.Height
		m.sprintProg.Width = m.width - 10

	case tea.KeyMsg:
		switch {
		case key.Matches(msg, m.keys.Quit):
			return m, tea.Quit

		case key.Matches(msg, m.keys.Refresh):
			return m, m.fetchData()

		case key.Matches(msg, m.keys.Tab):
			m.currentView = (m.currentView + 1) % 5
			return m, nil

		case key.Matches(msg, m.keys.Right):
			if m.currentView == OverviewView {
				m.focusedPanel = Panel((int(m.focusedPanel) + 1) % 4)
			} else if m.currentView != AgentDetailView {
				m.currentView = (m.currentView + 1) % 5
			}
			return m, nil

		case key.Matches(msg, m.keys.Left):
			if m.currentView == OverviewView {
				m.focusedPanel = Panel((int(m.focusedPanel) + 3) % 4)
			} else if m.currentView != AgentDetailView {
				m.currentView = View((int(m.currentView) + 4) % 5)
			}
			return m, nil

		case key.Matches(msg, m.keys.Dispatch):
			m.showQuick = true
			m.quickTask.Focus()
			return m, textinput.Blink

		case key.Matches(msg, m.keys.ToggleTheme):
			m.isDark = !m.isDark
			return m, nil

		case key.Matches(msg, m.keys.Enter):
			if m.currentView == AgentsView {
				if item, ok := m.agentList.SelectedItem().(agentItem); ok {
					m.selectedAgent = &item.agent
					m.previousView = AgentsView
					m.currentView = AgentDetailView
				}
			}
			return m, nil

		case key.Matches(msg, m.keys.Esc):
			if m.currentView == AgentDetailView {
				m.currentView = m.previousView
			}
			return m, nil
		}

	case tickMsg:
		return m, tea.Batch(m.fetchData(), m.tickCmd())

	case dataMsg:
		m.state = DashboardState(msg)
		
		m.history.Throughput = append(m.history.Throughput, m.state.Summary.TaskThroughput)
		if len(m.history.Throughput) > 20 {
			m.history.Throughput = m.history.Throughput[1:]
		}
		m.history.ErrorRate = append(m.history.ErrorRate, m.state.Summary.ErrorRate)
		if len(m.history.ErrorRate) > 20 {
			m.history.ErrorRate = m.history.ErrorRate[1:]
		}

		var agentItems []list.Item
		for _, a := range m.state.Agents {
			agentItems = append(agentItems, agentItem{agent: a})
		}
		m.agentList = list.New(agentItems, list.NewDefaultDelegate(), m.width, m.height-10)
		m.agentList.Title = "Agents"

		var eventItems []list.Item
		for _, e := range m.state.RecentEvents {
			eventItems = append(eventItems, eventItem{event: e})
		}
		m.logList = list.New(eventItems, list.NewDefaultDelegate(), m.width, m.height-10)
		m.logList.Title = "Events"

		return m, nil
	}

	if m.currentView == AgentsView {
		m.agentList, cmd = m.agentList.Update(msg)
		return m, cmd
	} else if m.currentView == LogsView {
		m.logList, cmd = m.logList.Update(msg)
		return m, cmd
	}

	return m, nil
}

func (m DashboardModel) View() string {
	if m.width == 0 {
		return "Loading..."
	}

	bg := lipgloss.Color("#0d1117")
	fg := lipgloss.Color("#c9d1d9")
	if !m.isDark {
		bg = lipgloss.Color("#ffffff")
		fg = lipgloss.Color("#000000")
	}

	mainStyle := lipgloss.NewStyle().Background(bg).Foreground(fg).Width(m.width).Height(m.height)

	header := m.drawHeader()
	tabBar := m.drawTabs()

	contentHeight := m.height - 8
	var content string

	switch m.currentView {
	case OverviewView:
		content = m.overviewView(contentHeight)
	case AgentsView:
		content = m.agentsView(contentHeight)
	case TasksView:
		content = m.tasksView(contentHeight)
	case SprintsView:
		content = m.sprintsView(contentHeight)
	case LogsView:
		content = m.logsView(contentHeight)
	case AgentDetailView:
		content = m.agentDetailView(contentHeight)
	}

	statusBar := m.drawStatusBar()

	if m.showQuick {
		overlay := lipgloss.Place(m.width, m.height, lipgloss.Center, lipgloss.Center,
			boxStyle.BorderForeground(lipgloss.Color("#58a6ff")).Render(
				lipgloss.JoinVertical(lipgloss.Left,
					headerStyle.Render("🚀 Dispatch Quick Task"),
					m.quickTask.View(),
					lipgloss.NewStyle().Foreground(lipgloss.Color("#666")).Render("Enter: dispatch | Esc: cancel"),
				),
			),
		)
		return overlay
	}

	return mainStyle.Render(
		lipgloss.JoinVertical(lipgloss.Left,
			header,
			tabBar,
			content,
			statusBar,
		),
	)
}

func (m DashboardModel) drawHeader() string {
	title := titleStyle.Render("🔥 FORGE v3")
	uptime := ""
	if m.state.Summary.Uptime != "" {
		uptime = fmt.Sprintf("Uptime: %s", m.state.Summary.Uptime)
	}
	healthColor := successStyle
	if m.state.Summary.SystemHealth != "healthy" {
		healthColor = warningStyle
	}
	health := healthColor.Render(strings.ToUpper(m.state.Summary.SystemHealth))
	
	return lipgloss.JoinHorizontal(lipgloss.Left, 
		title, 
		lipgloss.NewStyle().Width(m.width-lipgloss.Width(title)-len(uptime)-len(m.state.Summary.SystemHealth)-5).Render(""),
		health,
		"  ",
		uptime,
	)
}

func (m DashboardModel) drawTabs() string {
	if m.currentView == AgentDetailView {
		return activeTabStyle.Render("Agent Details")
	}
	tabs := []string{"Overview", "Agents", "Tasks", "Sprints", "Logs"}
	var tabViews []string
	for i, tab := range tabs {
		style := inactiveTabStyle
		if View(i) == m.currentView {
			style = activeTabStyle
		}
		tabViews = append(tabViews, style.Render(tab))
	}
	return lipgloss.JoinHorizontal(lipgloss.Left, tabViews...)
}

func (m DashboardModel) drawStatusBar() string {
	statusText := fmt.Sprintf(" Agents: %d/%d | Tasks: %d | Throughput: %.1f/h | Updated: %s ",
		m.state.Summary.OnlineAgents, m.state.Summary.TotalAgents,
		m.state.Summary.TotalTasks,
		m.state.Summary.TaskThroughput,
		m.state.LastUpdated.Format("15:04:05"))
	
	if m.state.Error != "" {
		statusText = " ERROR: " + m.state.Error
	}

	help := " q: quit | r: refresh | ←/→: navigate | d: quick task | t: theme"
	if m.currentView == AgentsView {
		help += " | enter: details"
	} else if m.currentView == AgentDetailView {
		help = " esc: back | q: quit"
	}
	
	return lipgloss.JoinVertical(lipgloss.Left,
		statusBarStyle.Width(m.width).Render(statusText),
		lipgloss.NewStyle().Foreground(lipgloss.Color("#666")).PaddingLeft(2).Render(help),
	)
}

func (m DashboardModel) overviewView(height int) string {
	metricsHeight := 6
	
	var nodesView string
	for _, n := range m.state.Nodes {
		dot := successStyle.Render("●")
		if n.Status != "online" {
			dot = errorStyle.Render("●")
		}
		nodesView += fmt.Sprintf("%s %-8s [%d]\n", dot, n.Name, n.AgentCount)
	}
	
	nodeBox := m.panelStyle(StatusPanel).Width(m.width/4 - 2).Height(metricsHeight).Render(
		headerStyle.Render("Nodes") + "\n" + nodesView,
	)

	tpSpark := drawSparkline(m.history.Throughput, m.width/4-6)
	tpBox := m.panelStyle(TasksPanel).Width(m.width/4 - 2).Height(metricsHeight).Render(
		headerStyle.Render("Throughput") + "\n\n" + tpSpark + "\n" + fmt.Sprintf("%.1f tasks/h", m.state.Summary.TaskThroughput),
	)

	errSpark := drawSparkline(m.history.ErrorRate, m.width/4-6)
	errBox := m.panelStyle(AgentsPanel).Width(m.width/4 - 2).Height(metricsHeight).Render(
		headerStyle.Render("Error Rate") + "\n\n" + errSpark + "\n" + fmt.Sprintf("%.1f%%", m.state.Summary.ErrorRate),
	)

	summaryBox := m.panelStyle(EventsPanel).Width(m.width/4 - 2).Height(metricsHeight).Render(
		headerStyle.Render("Agents") + "\n" +
			fmt.Sprintf("Idle: %d\nBusy: %d\nErr:  %d", m.state.Summary.IdleAgents, m.state.Summary.BusyAgents, m.state.Summary.ErrorAgents),
	)

	topRow := lipgloss.JoinHorizontal(lipgloss.Top, nodeBox, tpBox, errBox, summaryBox)

	contentBoxHeight := height - metricsHeight - 2
	
	var agentsList string
	for i, a := range m.state.Agents {
		if i >= contentBoxHeight-4 { break }
		statusColor := successStyle
		if a.StatusText == "busy" { statusColor = warningStyle }
		if a.StatusText == "error" || a.StatusText == "offline" { statusColor = errorStyle }
		
		task := "-"
		if a.CurrentTask != nil { task = *a.CurrentTask }
		
		agentsList += fmt.Sprintf("%-12s %-10s %-8s %s\n", 
			a.AgentID, 
			statusColor.Render(a.StatusText),
			a.Node,
			truncateStr(task, 30))
	}
	
	mainBox := boxStyle.Width(m.width - 2).Height(contentBoxHeight).Render(
		headerStyle.Render("Active Agents Activity") + "\n" +
		fmt.Sprintf("%-12s %-10s %-8s %-30s\n", "AGENT", "STATUS", "NODE", "TASK") +
		strings.Repeat("─", m.width-6) + "\n" +
		agentsList,
	)

	return lipgloss.JoinVertical(lipgloss.Left, topRow, mainBox)
}

func (m DashboardModel) panelStyle(p Panel) lipgloss.Style {
	style := boxStyle
	if m.currentView == OverviewView && m.focusedPanel == p {
		style = style.BorderForeground(lipgloss.Color("#7D56F4")).Border(lipgloss.ThickBorder())
	}
	return style
}

func (m DashboardModel) agentsView(height int) string {
	if len(m.state.Agents) == 0 {
		return boxStyle.Width(m.width).Height(height).Render("No agents connected")
	}
	return m.agentList.View()
}

func (m DashboardModel) tasksView(height int) string {
	var view string
	view += headerStyle.Render(fmt.Sprintf("%-20s %-12s %-40s %s\n", "TASK ID", "STATUS", "PROGRESS", "ASSIGNED"))
	view += strings.Repeat("─", m.width-4) + "\n"
	
	for _, a := range m.state.Agents {
		if a.CurrentTask != nil {
			prog := progress.New(progress.WithDefaultGradient())
			prog.Width = 38
			p := 0.45 
			view += fmt.Sprintf("%-20s %-12s %-40s %s\n", 
				*a.CurrentTask, 
				warningStyle.Render("executing"),
				prog.ViewAs(p),
				a.AgentID)
		}
	}
	
	return boxStyle.Width(m.width - 2).Height(height).Render(view)
}

func (m DashboardModel) sprintsView(height int) string {
	if m.state.Sprint == nil {
		return boxStyle.Width(m.width).Height(height).Render("No active sprints found")
	}
	
	s := m.state.Sprint
	title := headerStyle.Render("🎯 Sprint: " + s.Name)
	stats := fmt.Sprintf("Started: %s | ETA: %s | Active Agents: %d", 
		s.StartedAt.Format("2006-01-02 15:04"), s.ETA, len(s.Agents))
	
	m.sprintProg.Width = m.width - 10
	progBar := m.sprintProg.ViewAs(s.Completion / 100.0)
	
	var blockers string
	if len(s.Blockers) > 0 {
		blockers = "\n" + errorStyle.Render("⚠️  BLOCKERS:") + "\n"
		for _, b := range s.Blockers {
			blockers += fmt.Sprintf("- %s: %s (%s)\n", b.Agent, b.Description, b.Severity)
		}
	} else {
		blockers = "\n" + successStyle.Render("✅ No active blockers") + "\n"
	}
	
	var agents string
	agents = "\n" + headerStyle.Render("AGENT ASSIGNMENTS:") + "\n"
	for _, a := range s.Agents {
		status := successStyle.Render(a.Status)
		if a.Status != "working" { status = warningStyle.Render(a.Status) }
		agents += fmt.Sprintf("%-12s %-10s %-40s\n", a.Name, status, truncateStr(a.CurrentTask, 40))
	}

	content := lipgloss.JoinVertical(lipgloss.Left, title, stats, "\nProgress: "+fmt.Sprintf("%.1f%%", s.Completion), progBar, blockers, agents)
	return boxStyle.Width(m.width - 2).Height(height).Render(content)
}

func (m DashboardModel) agentDetailView(height int) string {
	if m.selectedAgent == nil {
		return "No agent selected"
	}
	a := m.selectedAgent
	
	content := lipgloss.JoinVertical(lipgloss.Left,
		headerStyle.Render("👤 Agent: "+a.AgentID),
		fmt.Sprintf("Node:       %s", a.Node),
		fmt.Sprintf("Status:     %s", a.StatusText),
		fmt.Sprintf("Context:    %.1f%%", a.ContextPercent),
		fmt.Sprintf("Success:    %.1f%%", a.SuccessRate),
		fmt.Sprintf("Tasks:      %d", a.TaskCount),
		fmt.Sprintf("Last Seen:  %s", a.LastHeartbeat.Format("15:04:05")),
		"\nCapabilities: "+strings.Join(a.Capabilities, ", "),
	)
	
	return boxStyle.Width(m.width - 2).Height(height).Render(content)
}

func (m DashboardModel) logsView(height int) string {
	if len(m.state.RecentEvents) == 0 {
		return boxStyle.Width(m.width).Height(height).Render("No events recorded")
	}
	return m.logList.View()
}

func drawSparkline(values []float64, width int) string {
	if len(values) == 0 { return strings.Repeat(" ", width) }
	chars := []rune(" ▂▃▄▅▆▇█")
	var maxVal float64
	for _, v := range values { if v > maxVal { maxVal = v } }
	if maxVal == 0 { maxVal = 1 }
	result := ""
	for i := 0; i < width-len(values); i++ { result += " " }
	for _, v := range values {
		idx := int((v / maxVal) * float64(len(chars)-1))
		if idx < 0 { idx = 0 }
		if idx >= len(chars) { idx = len(chars)-1 }
		result += string(chars[idx])
	}
	return lipgloss.NewStyle().Foreground(lipgloss.Color("#58a6ff")).Render(result)
}

func truncateStr(s string, maxLen int) string {
	if len(s) <= maxLen { return s }
	return s[:maxLen-3] + "..."
}

func RunDashboard(apiBaseURL string) error {
	model := NewDashboardModel(apiBaseURL)
	p := tea.NewProgram(model, tea.WithAltScreen())
	finalModel, err := p.Run()
	// Clean up ticker to prevent goroutine leak
	if fm, ok := finalModel.(DashboardModel); ok && fm.ticker != nil {
		fm.ticker.Stop()
	}
	return err
}
